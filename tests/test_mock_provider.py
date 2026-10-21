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


async def test_answer_restates_engine_facts() -> None:
    plan = make_plan(make_context())
    answer = await provider.answer_question(
        "Can I afford a live band?", plan, {"remaining_budget": "16500.00 INR"}
    )
    assert "remaining budget: 16500.00 INR" in answer
    AnswerOutput.model_validate({"answer": answer})


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
