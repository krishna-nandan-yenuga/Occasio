"""Domain models and planning profiles: validation and table invariants."""

from datetime import datetime
from decimal import Decimal
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from app.core.models import BudgetLine, EventType, Formality, Plan
from app.core.profiles import PROFILES, get_profile
from tests.factories import make_context

ALL_TABLES = [
    (event_type, formality)
    for event_type in get_args(EventType)
    for formality in get_args(Formality)
]


# Profiles


def test_every_event_type_has_a_profile() -> None:
    assert set(PROFILES) == set(get_args(EventType))


@pytest.mark.parametrize(("event_type", "formality"), ALL_TABLES)
def test_allocation_table_sums_to_100(event_type: EventType, formality: Formality) -> None:
    table = get_profile(event_type).allocations[formality]
    assert sum(table.values()) == Decimal(100)


@pytest.mark.parametrize(("event_type", "formality"), ALL_TABLES)
def test_allocation_table_reserves_contingency(event_type: EventType, formality: Formality) -> None:
    table = get_profile(event_type).allocations[formality]
    assert Decimal(5) <= table["contingency"] <= Decimal(10)
    assert all(percent > 0 for percent in table.values())


@pytest.mark.parametrize("event_type", get_args(EventType))
def test_milestone_offsets_are_positive(event_type: EventType) -> None:
    for milestone in get_profile(event_type).milestones:
        assert milestone.offset_days_before_event > 0, milestone.title


@pytest.mark.parametrize("event_type", get_args(EventType))
def test_milestone_dependencies_exist_and_come_earlier(event_type: EventType) -> None:
    milestones = {m.title: m for m in get_profile(event_type).milestones}
    assert len(milestones) == len(get_profile(event_type).milestones), "duplicate titles"
    for milestone in milestones.values():
        for dependency in milestone.depends_on:
            assert dependency in milestones, f"{milestone.title} -> {dependency}"
            earlier = milestones[dependency].offset_days_before_event
            assert earlier > milestone.offset_days_before_event


@pytest.mark.parametrize("event_type", get_args(EventType))
def test_vendor_categories_are_budgeted(event_type: EventType) -> None:
    profile = get_profile(event_type)
    for formality in get_args(Formality):
        budgeted = set(profile.allocations[formality])
        for vendor in profile.vendors:
            assert vendor.category in budgeted
            assert vendor.suggested_vendor_types


@pytest.mark.parametrize("event_type", get_args(EventType))
def test_every_vendor_has_a_booking_milestone(event_type: EventType) -> None:
    profile = get_profile(event_type)
    milestone_categories = {m.category for m in profile.milestones}
    for vendor in profile.vendors:
        assert vendor.category in milestone_categories


def test_community_profile_has_permits_and_accessibility() -> None:
    categories = {m.category for m in get_profile("community").milestones}
    assert {"permits_insurance", "accessibility"} <= categories


# Models


def test_event_context_defaults() -> None:
    context = make_context()
    assert context.currency == "INR"
    assert context.extras == {}
    assert context.preferences == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"guest_count": 0},
        {"total_budget": Decimal("-1")},
        {"total_budget": Decimal("10.005")},
        {"currency": "inr"},
        {"event_type": "conference"},
        {"formality": "black-tie"},
        {"preferences": ["x" * 201]},
        {"extras": {"note": "x" * 201}},
        {"unknown_field": True},
    ],
)
def test_event_context_rejects_invalid_input(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        make_context(**overrides)


def test_money_parses_to_decimal() -> None:
    line = BudgetLine(category="venue", amount="1234.50", percent="22")  # type: ignore[arg-type]
    assert line.amount == Decimal("1234.50")
    assert isinstance(line.amount, Decimal)


def test_plan_round_trips_through_json() -> None:
    now = datetime(2026, 10, 21, 12, 0)
    plan = Plan(
        id="p1",
        context=make_context(extras={"honoree_age": 30, "kids": False}),
        budget=[BudgetLine(category="venue", amount=Decimal("30000.00"), percent=Decimal(20))],
        created_at=now,
        updated_at=now,
    )
    assert Plan.model_validate_json(plan.model_dump_json()) == plan
