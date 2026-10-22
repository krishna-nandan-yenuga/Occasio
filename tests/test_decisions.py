"""Decision rules: aggregation, severity order and cross-cutting checks."""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from app.core.budget import allocate, rebalance
from app.core.decisions import SEVERITY_ORDER, advise
from app.core.models import Advisory, BudgetLine, EventContext
from app.core.timeline import schedule
from tests.factories import TODAY, make_context


def run(
    context: EventContext, today: date = TODAY, locked: dict[str, Decimal] | None = None
) -> list[Advisory]:
    lines: list[BudgetLine] = allocate(context)
    if locked:
        lines = rebalance(lines, locked)
    return advise(context, lines, schedule(context, today), today)


def codes(context: EventContext, **kwargs: Any) -> dict[str, str]:
    return {a.code: a.severity for a in run(context, **kwargs)}


def test_comfortable_plan_has_no_advisories() -> None:
    assert run(make_context()) == []


def test_aggregates_budget_and_timeline_advisories() -> None:
    context = make_context(guest_count=200, total_budget=Decimal("100000"))
    result = codes(context, today=context.event_date - timedelta(days=10))
    assert {"BUDGET_TIGHT", "SHORT_RUNWAY", "MISSED_MILESTONE"} <= set(result)


def test_advisories_are_sorted_most_severe_first() -> None:
    context = make_context(event_type="wedding", guest_count=300, total_budget=Decimal("100000"))
    severities = [a.severity for a in run(context, today=context.event_date - timedelta(days=20))]
    assert "critical" in severities and "warn" in severities
    assert severities == sorted(severities, key=SEVERITY_ORDER.__getitem__)


def test_venue_budget_too_small_for_guest_count() -> None:
    result = run(make_context(), locked={"venue": Decimal("5000")})
    assert [a.code for a in result] == ["VENUE_TOO_SMALL"]
    assert "about 31 guests" in result[0].message


def test_catering_too_thin_per_guest() -> None:
    assert codes(make_context(), locked={"catering": Decimal("5000")}) == {"CATERING_TIGHT": "warn"}


def test_small_category_cut_within_tolerance_is_fine() -> None:
    assert codes(make_context(), locked={"venue": Decimal("25000")}) == {}


def test_category_checks_are_skipped_when_whole_budget_is_tight() -> None:
    context = make_context(guest_count=200, total_budget=Decimal("100000"))
    assert codes(context, locked={"venue": Decimal("1000")}) == {"BUDGET_TIGHT": "warn"}


def test_category_checks_need_inr() -> None:
    context = make_context(currency="USD")
    assert codes(context, locked={"venue": Decimal("10")}) == {"SPEND_NOT_BENCHMARKED": "info"}


def test_alcohol_at_kids_party_is_flagged() -> None:
    context = make_context(extras={"honoree_age": 6})
    assert codes(context) == {}
    assert codes(context, locked={"bar": Decimal("3000")}) == {"ALCOHOL_AT_KIDS_PARTY": "warn"}


def test_community_event_must_fund_permits_and_accessibility() -> None:
    context = make_context(event_type="community", guest_count=150, event_date=date(2027, 6, 1))
    assert codes(context) == {}
    result = codes(context, locked={"permits_insurance": Decimal(0), "accessibility": Decimal(0)})
    assert result == {
        "PERMITS_INSURANCE_UNFUNDED": "critical",
        "ACCESSIBILITY_UNFUNDED": "critical",
    }


def test_advise_is_deterministic() -> None:
    context = make_context(event_type="wedding", guest_count=500, total_budget=Decimal("900000"))
    today = context.event_date - timedelta(days=40)
    assert run(context, today=today) == run(context, today=today)
