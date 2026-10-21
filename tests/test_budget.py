from datetime import date
from decimal import Decimal
from typing import Any, get_args

import pytest

from app.core.budget import BudgetError, allocate, feasibility, rebalance
from app.core.models import BudgetLine, EventContext, EventType, Formality, LocationTier


def make_context(**overrides: Any) -> EventContext:
    fields: dict[str, Any] = {
        "event_type": "birthday",
        "event_date": date(2026, 12, 5),
        "guest_count": 40,
        "total_budget": Decimal("150000"),
        "location_tier": "city",
        "formality": "semi",
    }
    return EventContext(**(fields | overrides))


def by_category(lines: list[BudgetLine]) -> dict[str, BudgetLine]:
    return {line.category: line for line in lines}


def share(lines: list[BudgetLine], category: str) -> Decimal:
    total = sum(line.amount for line in lines)
    return by_category(lines)[category].amount / total


def codes(context: EventContext, lines: list[BudgetLine]) -> dict[str, str]:
    return {a.code: a.severity for a in feasibility(context, lines)}


# allocate


@pytest.mark.parametrize("event_type", get_args(EventType))
@pytest.mark.parametrize("formality", get_args(Formality))
@pytest.mark.parametrize("tier", get_args(LocationTier))
@pytest.mark.parametrize("total", ["150000", "99999.99", "1234567.89", "0.07", "0"])
@pytest.mark.parametrize("guests", [1, 40, 333, 5000])
def test_allocation_sums_exactly_to_total(
    event_type: EventType, formality: Formality, tier: LocationTier, total: str, guests: int
) -> None:
    context = make_context(
        event_type=event_type,
        formality=formality,
        location_tier=tier,
        total_budget=Decimal(total),
        guest_count=guests,
    )
    lines = allocate(context)
    assert sum(line.amount for line in lines) == context.total_budget
    assert all(line.amount >= 0 for line in lines)


@pytest.mark.parametrize("event_type", get_args(EventType))
def test_allocation_reserves_5_to_10_percent_contingency(event_type: EventType) -> None:
    lines = allocate(make_context(event_type=event_type, guest_count=900, location_tier="metro"))
    assert Decimal("0.05") <= share(lines, "contingency") <= Decimal("0.10")


def test_rounding_drift_lands_on_contingency() -> None:
    lines = allocate(make_context(total_budget=Decimal("99999.99")))
    others = [line for line in lines if line.category != "contingency"]
    for line in others:
        assert line.amount == line.amount.quantize(Decimal("0.01"))
    assert by_category(lines)["contingency"].amount == Decimal("99999.99") - sum(
        line.amount for line in others
    )


def test_allocation_is_deterministic() -> None:
    context = make_context(event_type="wedding", guest_count=250, location_tier="metro")
    assert allocate(context) == allocate(context)


@pytest.mark.parametrize("extras", [{"honoree_age": 7}, {"kids_party": True}])
def test_kids_birthday_has_no_alcohol(extras: dict[str, Any]) -> None:
    kids = allocate(make_context(extras=extras))
    adults = allocate(make_context(extras={"honoree_age": 30}))
    assert by_category(kids)["bar"].amount == 0
    assert by_category(adults)["bar"].amount > 0
    assert share(kids, "entertainment") > share(adults, "entertainment")
    assert sum(line.amount for line in kids) == Decimal("150000")


@pytest.mark.parametrize("event_type", get_args(EventType))
def test_location_tier_changes_venue_share(event_type: EventType) -> None:
    venue = {
        tier: share(allocate(make_context(event_type=event_type, location_tier=tier)), "venue")
        for tier in get_args(LocationTier)
    }
    assert venue["metro"] > venue["city"] > venue["town"]


def test_more_guests_raise_catering_share() -> None:
    small = allocate(make_context(guest_count=15))
    large = allocate(make_context(guest_count=150))
    assert share(large, "catering") > share(small, "catering")


def test_ticketed_community_event_raises_contingency_and_permits() -> None:
    base = make_context(event_type="community", guest_count=150)
    ticketed = allocate(base.model_copy(update={"extras": {"ticketed": True}}))
    free = allocate(base)
    assert by_category(ticketed)["contingency"].percent == Decimal("10.00")
    assert share(ticketed, "permits_insurance") > share(free, "permits_insurance")


# rebalance


def test_locked_line_is_preserved_and_total_kept() -> None:
    lines = allocate(make_context())
    result = rebalance(lines, {"venue": Decimal("50000")})
    venue = by_category(result)["venue"]
    assert venue.amount == Decimal("50000")
    assert venue.is_locked
    assert sum(line.amount for line in result) == Decimal("150000")


def test_rebalance_keeps_unlocked_lines_proportional() -> None:
    lines = allocate(make_context())
    result = by_category(rebalance(lines, {"venue": Decimal("50000")}))
    before = by_category(lines)
    ratio_before = before["catering"].amount / before["decor"].amount
    ratio_after = result["catering"].amount / result["decor"].amount
    assert abs(ratio_after - ratio_before) < Decimal("0.001")


def test_previously_locked_lines_survive_later_rebalance() -> None:
    lines = rebalance(allocate(make_context()), {"venue": Decimal("50000")})
    result = by_category(rebalance(lines, {"catering": Decimal("40000")}))
    assert result["venue"].amount == Decimal("50000")
    assert result["catering"].amount == Decimal("40000")
    assert sum(line.amount for line in result.values()) == Decimal("150000")


def test_drift_goes_to_largest_unlocked_line_when_contingency_locked() -> None:
    lines = allocate(make_context(total_budget=Decimal("100000")))
    result = rebalance(lines, {"contingency": Decimal("3333.33"), "venue": Decimal("11111.11")})
    assert sum(line.amount for line in result) == Decimal("100000")


@pytest.mark.parametrize(
    ("locked", "message"),
    [
        ({"venue": Decimal("200000")}, "exceed"),
        ({"fireworks": Decimal("1")}, "Unknown"),
    ],
)
def test_rebalance_rejects_impossible_locks(locked: dict[str, Decimal], message: str) -> None:
    with pytest.raises(BudgetError, match=message):
        rebalance(allocate(make_context()), locked)


def test_rebalance_with_everything_locked_must_balance() -> None:
    lines = allocate(make_context())
    everything = {line.category: line.amount for line in lines}
    assert rebalance(lines, everything) == [
        line.model_copy(update={"is_locked": True}) for line in lines
    ]
    with pytest.raises(BudgetError, match="Every line is locked"):
        rebalance(lines, everything | {"venue": Decimal(0)})


# feasibility


def test_tight_budget_advisory_fires_for_low_per_guest_spend() -> None:
    context = make_context(guest_count=200, total_budget=Decimal("100000"))  # 500 INR/guest
    assert codes(context, allocate(context)) == {"BUDGET_TIGHT": "warn"}


def test_very_tight_budget_is_critical() -> None:
    context = make_context(guest_count=200, total_budget=Decimal("20000"))  # 100 INR/guest
    assert codes(context, allocate(context)) == {"BUDGET_TIGHT": "critical"}


def test_generous_budget_is_informational() -> None:
    context = make_context(guest_count=10, total_budget=Decimal("500000"))
    assert codes(context, allocate(context)) == {"BUDGET_GENEROUS": "info"}


def test_comfortable_budget_has_no_advisories() -> None:
    context = make_context()  # 3,750 INR per guest, semi-formal birthday in a city
    assert feasibility(context, allocate(context)) == []


def test_location_raises_tight_threshold() -> None:
    city = make_context(guest_count=100, total_budget=Decimal("90000"))  # 900 INR/guest
    metro = city.model_copy(update={"location_tier": "metro"})
    assert "BUDGET_TIGHT" not in codes(city, allocate(city))
    assert "BUDGET_TIGHT" in codes(metro, allocate(metro))


def test_lines_not_matching_total_are_critical() -> None:
    context = make_context()
    lines = allocate(context)[1:]
    assert codes(context, lines)["BUDGET_MISMATCH"] == "critical"


def test_low_contingency_warns() -> None:
    context = make_context()
    lines = rebalance(allocate(context), {"contingency": Decimal("1000")})
    assert codes(context, lines) == {"LOW_CONTINGENCY": "warn"}


def test_non_inr_budget_skips_per_guest_benchmark() -> None:
    context = make_context(currency="USD", total_budget=Decimal("100"), guest_count=50)
    assert codes(context, allocate(context)) == {"SPEND_NOT_BENCHMARKED": "info"}
