import math
from datetime import date, timedelta
from typing import get_args

import pytest

from app.core.models import EventContext, EventType, Formality, Milestone
from app.core.timeline import feasibility, horizon_days, schedule, templates
from tests.factories import TODAY, make_context

EVENT_DATE = date(2027, 6, 1)


def days_before(days: int) -> date:
    return EVENT_DATE - timedelta(days=days)


def codes(milestones: list[Milestone], today: date, **context: object) -> dict[str, str]:
    advisories = feasibility(make_context(event_date=EVENT_DATE, **context), milestones, today)
    return {a.code: a.severity for a in advisories}


def missed_message(context: EventContext, milestones: list[Milestone], today: date) -> str:
    advisories = feasibility(context, milestones, today)
    return next(a.message for a in advisories if a.code == "MISSED_MILESTONE")


def test_no_compression_with_enough_runway() -> None:
    context = make_context(event_date=EVENT_DATE)
    milestones = schedule(context, TODAY)
    expected = {t.title: days_before(t.offset_days_before_event) for t in templates(context)}
    assert {m.title: m.due_date for m in milestones} == expected
    assert all(m.status == "todo" for m in milestones)
    assert feasibility(context, milestones, TODAY) == []


@pytest.mark.parametrize(("runway", "severity"), [(21, "info"), (14, "warn")])
def test_short_runway_compresses_proportionally(runway: int, severity: str) -> None:
    context = make_context(event_date=EVENT_DATE)
    today = days_before(runway)
    horizon = horizon_days(context)
    milestones = schedule(context, today)
    expected = {
        t.title: days_before(math.ceil(t.offset_days_before_event * runway / horizon))
        for t in templates(context)
    }
    assert {m.title: m.due_date for m in milestones} == expected
    assert all(m.due_date >= today and m.status == "todo" for m in milestones)
    assert codes(milestones, today) == {"SHORT_RUNWAY": severity}


@pytest.mark.parametrize("event_type", get_args(EventType))
@pytest.mark.parametrize("runway", [0, 3, 20, 45, 100, 200, 400])
def test_compression_keeps_dependencies_first(event_type: EventType, runway: int) -> None:
    milestones = schedule(
        make_context(event_type=event_type, event_date=EVENT_DATE), days_before(runway)
    )
    due = {m.title: m.due_date for m in milestones}
    assert [m.due_date for m in milestones] == sorted(due.values())
    for milestone in milestones:
        assert all(due[dep] <= milestone.due_date for dep in milestone.depends_on)


def test_milestones_that_cannot_fit_are_late() -> None:
    context = make_context(event_type="wedding", event_date=EVENT_DATE)
    today = days_before(30)  # a 300-day plan compresses at most 4x, to 75 days
    milestones = schedule(context, today)
    late = {m.title for m in milestones if m.status == "late"}
    assert late == {
        t.title for t in templates(context) if math.ceil(t.offset_days_before_event / 4) > 30
    }
    assert "Book venue" in late
    assert all(m.due_date < today for m in milestones if m.status == "late")
    assert all(m.due_date >= today for m in milestones if m.status == "todo")

    result = codes(milestones, today, event_type="wedding")
    assert result == {"SHORT_RUNWAY": "warn", "MISSED_MILESTONE": "warn"}


def test_event_in_the_past() -> None:
    context = make_context(event_date=EVENT_DATE)
    milestones = schedule(context, EVENT_DATE + timedelta(days=1))
    assert all(m.status == "late" for m in milestones)
    assert codes(milestones, EVENT_DATE + timedelta(days=1)) == {"EVENT_IN_PAST": "critical"}


def test_milestones_become_overdue_as_time_passes() -> None:
    context = make_context(event_date=EVENT_DATE)
    first, *rest = schedule(context, days_before(60))
    later = days_before(10)
    assert first.title in missed_message(context, [first, *rest], later)

    done = first.model_copy(update={"status": "done"})
    assert first.title not in missed_message(context, [done, *rest], later)


@pytest.mark.parametrize("formality", get_args(Formality))
@pytest.mark.parametrize("runway", [365, 60, 5])
@pytest.mark.parametrize("ticketed", [True, False])
def test_community_events_always_get_permits_and_accessibility(
    formality: Formality, runway: int, ticketed: bool
) -> None:
    context = make_context(
        event_type="community",
        formality=formality,
        event_date=EVENT_DATE,
        extras={"ticketed": ticketed},
    )
    categories = {m.category for m in schedule(context, days_before(runway))}
    assert {"permits_insurance", "accessibility"} <= categories


def test_accessibility_needs_add_a_milestone() -> None:
    needs = ["wheelchair access"]
    wedding = schedule(make_context(event_type="wedding", accessibility_needs=needs), TODAY)
    community = schedule(make_context(event_type="community", accessibility_needs=needs), TODAY)
    assert "Confirm accessibility arrangements" in {m.title for m in wedding}
    assert sum(m.category == "accessibility" for m in community) == 1


@pytest.mark.parametrize("event_type", get_args(EventType))
def test_schedule_is_deterministic(event_type: EventType) -> None:
    context = make_context(event_type=event_type, event_date=EVENT_DATE)
    today = days_before(50)
    assert schedule(context, today) == schedule(context, today)
    milestones = schedule(context, today)
    assert feasibility(context, milestones, today) == feasibility(context, milestones, today)
