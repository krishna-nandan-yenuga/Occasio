"""Mock provider: rule-based extraction, canned content and the provider factory."""

from datetime import date, timedelta
from decimal import Decimal
from typing import get_args

import pytest

from app.config import Settings
from app.core.models import EventContext, EventType
from app.llm.anthropic_provider import AnthropicProvider
from app.llm.mock_provider import MockProvider
from app.llm.prompts import AnswerOutput, IdeasOutput, VendorQuestionsOutput
from app.llm.provider import get_provider
from tests.factories import DEMO_TEXT, TODAY, make_context, make_plan

provider = MockProvider()


async def test_extracts_the_demo_description() -> None:
    result = await provider.extract_context(DEMO_TEXT, TODAY)
    assert result.fields == {
        "event_type": "birthday",
        "event_date": TODAY + timedelta(weeks=6),
        "guest_count": 40,
        "total_budget": Decimal("150000.00"),
        "currency": "INR",
        "location_tier": "metro",
        "extras": {"honoree_age": 30},
        "preferences": [],
        "accessibility_needs": [],
        "dietary": [],
    }
    assert result.missing_fields == ["formality"]
    # With the missing field filled in, the extraction is a valid EventContext.
    EventContext.model_validate(result.fields | {"formality": "semi"})


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("A formal wedding reception for 300 guests in Mumbai", {"event_type": "wedding"}),
        ("Community cleanup drive for 120 attendees", {"event_type": "community"}),
        ("Casual birthday, Jain and vegan food please", {"formality": "casual"}),
        ("budget rs 80k for the party", {"total_budget": Decimal("80000.00")}),
    ],
)
async def test_extracts_common_phrasings(text: str, expected: dict[str, object]) -> None:
    fields = (await provider.extract_context(text, TODAY)).fields
    assert expected.items() <= fields.items()


async def test_extracts_the_plan_demo_description() -> None:
    text = (
        "Planning a community Diwali mela for ~300 people on 8 Nov, budget ₹2,00,000, "
        "need wheelchair access and veg-only food."
    )
    result = await provider.extract_context(text, TODAY)
    assert {
        "event_type": "community",
        "event_date": date(2026, 11, 8),
        "guest_count": 300,
        "total_budget": Decimal("200000.00"),
        "accessibility_needs": ["wheelchair access"],
        "dietary": ["vegetarian"],
    }.items() <= result.fields.items()
    # Not stated, and it changes the budget, so intake asks (PLAN.md demo step 2).
    assert result.missing_fields == ["location_tier", "formality", "ticketed"]


@pytest.mark.parametrize(
    ("text", "ticketed", "asked"),
    [
        ("community fair, ticketed", True, False),
        ("community fair with an entry fee", True, False),
        ("community fair, free entry, no tickets", False, False),
        ("community fair", None, True),
    ],
)
async def test_community_intake_asks_about_tickets_only_when_unstated(
    text: str, ticketed: bool | None, asked: bool
) -> None:
    result = await provider.extract_context(text, TODAY)
    assert result.fields.get("extras", {}).get("ticketed") == ticketed  # type: ignore[attr-defined]
    assert ("ticketed" in result.missing_fields) is asked


async def test_other_event_types_are_not_asked_about_tickets() -> None:
    result = await provider.extract_context("a wedding for 200 guests", TODAY)
    assert "ticketed" not in result.missing_fields


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("party on 8th november", date(2026, 11, 8)),
        ("party on Nov 8th", date(2026, 11, 8)),
        ("party on 3 March 2027", date(2027, 3, 3)),
        ("party on 1 Jan", date(2027, 1, 1)),  # already past this year, so next year
        ("party on 2027-02-14", date(2027, 2, 14)),
        ("party on 31 Feb", None),
        ("in 10 days, or on 8 Nov", TODAY + timedelta(days=10)),  # relative wins
    ],
)
async def test_extracts_dates(text: str, expected: date | None) -> None:
    assert (await provider.extract_context(text, TODAY)).fields.get("event_date") == expected


@pytest.mark.parametrize(
    ("text", "dietary"),
    [
        ("pure veg menu", ["vegetarian"]),
        ("non-veg and vegan options", ["vegan"]),
        ("Jain food, nut allergies", ["jain", "nut allergy"]),
    ],
)
async def test_extracts_dietary_needs(text: str, dietary: list[str]) -> None:
    assert (await provider.extract_context(text, TODAY)).fields["dietary"] == dietary


async def test_date_ordinals_are_not_ages() -> None:
    fields = (await provider.extract_context("birthday on 8th Nov for 20 guests", TODAY)).fields
    assert "extras" not in fields
    fields = (await provider.extract_context("her 30th on 8th Nov", TODAY)).fields
    assert fields["extras"] == {"honoree_age": 30}


async def test_unrecognised_text_reports_every_required_field_missing() -> None:
    result = await provider.extract_context("hello there", TODAY)
    assert result.missing_fields == [
        "event_type",
        "event_date",
        "guest_count",
        "total_budget",
        "location_tier",
        "formality",
    ]


@pytest.mark.parametrize("event_type", get_args(EventType))
async def test_ideas_round_trip_through_schema(event_type: EventType) -> None:
    context = make_context(event_type=event_type, dietary=["jain"])
    ideas = await provider.generate_ideas(context, make_plan(context).budget)
    assert len({idea.category for idea in ideas}) >= 3
    output = IdeasOutput(ideas=ideas)
    assert IdeasOutput.model_validate_json(output.model_dump_json()) == output
    assert any("jain" in idea.description for idea in ideas if idea.category == "menu")


async def test_kids_party_gets_kids_ideas() -> None:
    adult = await provider.generate_ideas(make_context(extras={"honoree_age": 40}), [])
    kids = await provider.generate_ideas(make_context(extras={"honoree_age": 6}), [])
    assert adult != kids


async def test_vendor_questions_cover_every_category() -> None:
    context = make_context(event_type="wedding")
    vendors = make_plan(context).vendors
    questions = await provider.enrich_vendors(context, vendors)
    assert set(questions) == {vendor.category for vendor in vendors}
    payload = {"vendors": [{"category": c, "questions": q} for c, q in questions.items()]}
    VendorQuestionsOutput.model_validate_json(VendorQuestionsOutput(**payload).model_dump_json())


async def test_answer_phrases_engine_facts() -> None:
    plan = make_plan(make_context())
    facts = {"remaining_budget": "16500.00", "days_until_event": 12, "late_items": []}
    answer = await provider.answer_question("How are we doing?", plan, facts)
    assert answer == "(Demo mode) Budget not yet committed: 16500.00; days to go: 12."
    AnswerOutput.model_validate({"answer": answer})


async def test_answer_prefers_ask_verdicts() -> None:
    plan = make_plan(make_context())
    ask = {"ask": "DJ", "typical_cost_inr": "25000", "verdict": "fits within the budget"}
    answer = await provider.answer_question("A DJ?", plan, {"asks": [ask], "days_until_event": 3})
    assert answer == "(Demo mode) DJ: typically about 25000 INR, fits within the budget."


async def test_mock_is_deterministic() -> None:
    context = make_context(event_type="community")
    plan = make_plan(context)
    first = await provider.extract_context(DEMO_TEXT, TODAY)
    assert first == await provider.extract_context(DEMO_TEXT, TODAY)
    ideas = await provider.generate_ideas(context, plan.budget)
    assert ideas == await provider.generate_ideas(context, plan.budget)


def test_factory_picks_provider_from_settings() -> None:
    assert isinstance(get_provider(Settings(llm_provider="mock")), MockProvider)
    anthropic_provider = get_provider(Settings(llm_provider="anthropic", anthropic_api_key="k"))
    assert isinstance(anthropic_provider, AnthropicProvider)


async def test_extraction_uses_today_for_relative_dates() -> None:
    result = await provider.extract_context("wedding in 3 months", date(2027, 1, 1))
    assert result.fields["event_date"] == date(2027, 4, 1)
