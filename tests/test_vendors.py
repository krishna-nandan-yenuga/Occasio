"""Vendor checklist: booking dates and context-driven questions."""

from datetime import date, timedelta
from typing import get_args

import pytest

from app.core.models import EventType
from app.core.profiles import get_profile
from app.core.timeline import schedule
from app.core.vendors import BASE_QUESTIONS, checklist
from tests.factories import TODAY, make_context

EVENT_DATE = date(2027, 6, 1)


@pytest.mark.parametrize("event_type", get_args(EventType))
@pytest.mark.parametrize("runway", [400, 60, 10])
def test_one_item_per_vendor_booked_by_first_milestone(event_type: EventType, runway: int) -> None:
    context = make_context(event_type=event_type, event_date=EVENT_DATE)
    milestones = schedule(context, EVENT_DATE - timedelta(days=runway))
    items = checklist(context, milestones)

    assert [i.category for i in items] == sorted(
        (v.category for v in get_profile(event_type).vendors),
        key=lambda c: next(i.booked_by for i in items if i.category == c),
    )
    for item in items:
        first_due = min(m.due_date for m in milestones if m.category == item.category)
        assert item.booked_by == first_due
        assert item.status == "todo"
        assert item.suggested_vendor_types


def test_venue_is_booked_by_the_book_venue_milestone() -> None:
    context = make_context(event_type="wedding")
    milestones = schedule(context, TODAY)
    book_venue = next(m for m in milestones if m.title == "Book venue")
    venue = next(i for i in checklist(context, milestones) if i.category == "venue")
    assert venue.booked_by == book_venue.due_date


def test_missing_milestone_falls_back_to_event_date() -> None:
    context = make_context()
    assert all(item.booked_by == context.event_date for item in checklist(context, []))


def test_questions_reflect_guests_diet_and_access() -> None:
    context = make_context(
        guest_count=75, dietary=["jain", "vegan"], accessibility_needs=["wheelchair access"]
    )
    items = {i.category: i for i in checklist(context, schedule(context, TODAY))}
    venue, catering = items["venue"].questions_to_ask, items["catering"].questions_to_ask
    assert any("75 guests" in q for q in venue)
    assert any("wheelchair access" in q for q in venue)
    assert any("jain, vegan" in q for q in catering)
    assert items["cake"].questions_to_ask == list(BASE_QUESTIONS)


def test_long_question_is_shortened() -> None:
    context = make_context(dietary=[f"allergy-{n}-" + "x" * 40 for n in range(10)])
    catering = next(i for i in checklist(context, []) if i.category == "catering")
    assert all(len(q) <= 200 for q in catering.questions_to_ask)
    assert catering.questions_to_ask[-1].endswith("…")


@pytest.mark.parametrize("event_type", get_args(EventType))
def test_checklist_is_deterministic(event_type: EventType) -> None:
    context = make_context(event_type=event_type, dietary=["jain"])
    milestones = schedule(context, TODAY)
    assert checklist(context, milestones) == checklist(context, milestones)
