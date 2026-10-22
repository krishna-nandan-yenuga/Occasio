from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.core.models import Plan
from app.llm.mock_provider import MockProvider
from app.services.assistant import answer, ask_feasibility, engine_facts
from tests.factories import TODAY, FailingProvider, make_context, make_plan


def plan_with(**context: Any) -> Plan:
    return make_plan(make_context(**context))


def asks(plan: Plan, question: str) -> dict[str, str]:
    return {item["ask"]: item["verdict"] for item in ask_feasibility(plan, question)}


def test_facts_cover_budget_milestones_and_late_items() -> None:
    plan = plan_with()
    vendors = [
        v.model_copy(update={"status": "booked"}) if v.category == "venue" else v
        for v in plan.vendors
    ]
    booked = plan.model_copy(update={"vendors": vendors})
    facts = engine_facts(booked, "How are we doing?", TODAY)
    assert facts["total_budget"] == "150000"
    assert facts["committed_to_booked_vendors"] == "30000.00"
    assert facts["remaining_budget"] == "120000.00"
    assert facts["contingency_available"] == "7500.00"
    assert facts["budget_per_guest"] == "3750.00"
    assert facts["days_until_event"] == 45
    assert len(facts["next_milestones"]) == 3  # type: ignore[arg-type]
    assert facts["late_items"] == []
    assert "asks" not in facts


def test_late_items_include_overdue_milestones_and_unbooked_vendors() -> None:
    plan = plan_with()
    later = TODAY + timedelta(days=12)
    late = engine_facts(plan, "?", later)["late_items"]
    assert "Set budget and guest list (was due 2026-10-24)" in late  # type: ignore[operator]
    assert "Book venue (by 2026-10-31)" in late  # type: ignore[operator]


def test_common_ask_that_fits() -> None:
    plan = plan_with(total_budget=Decimal("1000000"))  # entertainment line is 110,000
    assert asks(plan, "Can I afford a live band?") == {
        "live band": "fits within the entertainment budget"
    }


def test_common_ask_covered_only_by_contingency() -> None:
    plan = plan_with(total_budget=Decimal("300000"))  # entertainment 33,000; contingency 15,000
    verdict = asks(plan, "Could we get a DJ and a magician?")
    assert verdict["DJ"] == "fits within the entertainment budget"
    assert verdict["magician"] == "fits within the entertainment budget"
    tighter = plan_with(total_budget=Decimal("200000"))  # entertainment 22,000; contingency 10,000
    assert "contingency covers it" in asks(tighter, "a DJ?")["DJ"]


def test_common_ask_over_budget() -> None:
    assert "over budget by" in asks(plan_with(), "Can I afford a live band?")["live band"]


def test_typical_costs_scale_with_location() -> None:
    city = ask_feasibility(plan_with(), "live band")[0]["typical_cost_inr"]
    metro = ask_feasibility(plan_with(location_tier="metro"), "live band")[0]["typical_cost_inr"]
    assert city.startswith("75000") and metro.startswith("93750")


def test_extra_guests_are_costed_from_catering() -> None:
    item = ask_feasibility(plan_with(), "What if we invite 10 more guests?")[0]
    assert item["ask"] == "10 more guests"
    assert item["typical_cost_inr"].startswith("11625")  # 46,500 catering / 40 guests * 10


def test_alcohol_ask_at_kids_party() -> None:
    plan = plan_with(extras={"honoree_age": 7})
    assert asks(plan, "Can we serve drinks?") == {"open bar": "not suitable for a kids' party"}


def test_asks_need_inr() -> None:
    assert ask_feasibility(plan_with(currency="USD"), "live band") == []


async def test_answer_passes_facts_to_provider() -> None:
    reply = await answer(plan_with(), "Can I afford a live band?", MockProvider(), TODAY)
    assert not reply.degraded
    assert "live band" in reply.answer and reply.facts["asks"]


async def test_answer_falls_back_to_facts_when_llm_fails() -> None:
    reply = await answer(plan_with(), "Can I afford a live band?", FailingProvider(), TODAY)
    assert reply.degraded
    assert reply.answer.startswith("The assistant is unavailable right now.")
    assert "remaining budget 150000 INR" in reply.answer
    assert "live band: over budget" in reply.answer


@pytest.mark.parametrize("question", ["anything?", "live band and 5 more guests"])
def test_facts_are_deterministic(question: str) -> None:
    plan = plan_with()
    assert engine_facts(plan, question, TODAY) == engine_facts(plan, question, TODAY)
