from datetime import timedelta
from decimal import Decimal

import pytest

from app.core.models import Plan
from app.core.vendors import checklist
from app.llm.mock_provider import MockProvider
from app.services import planner
from app.services.planner import PlanPatch, PlanUpdateError
from tests.factories import NOW, TODAY, FailingProvider, make_context


async def generate(provider: object = None, **context: object) -> Plan:
    return await planner.generate(
        make_context(**context), TODAY, provider or MockProvider(), plan_id="p1", now=NOW
    )


def codes(plan: Plan) -> list[str]:
    return [advisory.code for advisory in plan.advisories]


def by_category(plan: Plan) -> dict[str, Decimal]:
    return {line.category: line.amount for line in plan.budget}


async def test_generate_builds_a_full_plan() -> None:
    plan = await generate(event_type="wedding", guest_count=200, total_budget=Decimal("2000000"))
    assert plan.id == "p1" and plan.created_at == plan.updated_at == NOW
    assert sum(by_category(plan).values()) == Decimal("2000000")
    assert plan.timeline and plan.vendors and plan.ideas
    assert "LLM_UNAVAILABLE" not in codes(plan)


async def test_vendor_questions_are_merged_without_duplicates() -> None:
    plan = await generate()
    venue = next(v for v in plan.vendors if v.category == "venue")
    assert venue.questions_to_ask[0].startswith("Are you available")  # engine baseline first
    assert any("capacity" in q for q in venue.questions_to_ask)  # then the LLM's
    assert len({q.casefold() for q in venue.questions_to_ask}) == len(venue.questions_to_ask)


async def test_llm_failure_returns_engine_only_plan() -> None:
    plan = await generate(FailingProvider())
    engine_only = await generate()
    assert plan.ideas == []
    assert plan.budget == engine_only.budget and plan.timeline == engine_only.timeline
    assert plan.vendors == checklist(plan.context, plan.timeline)  # baseline questions only
    assert "LLM_UNAVAILABLE" in codes(plan)


async def test_partial_llm_failure_keeps_what_succeeded() -> None:
    plan = await generate(FailingProvider("generate_ideas"))
    assert plan.ideas == []
    assert plan.vendors != checklist(plan.context, plan.timeline)
    assert "LLM_UNAVAILABLE" in codes(plan)


async def test_unexpected_errors_are_not_swallowed() -> None:
    class Broken(MockProvider):
        async def generate_ideas(self, *args: object) -> list:  # type: ignore[override]
            raise RuntimeError("bug")

    with pytest.raises(RuntimeError, match="bug"):
        await generate(Broken())


async def test_generate_is_deterministic() -> None:
    assert await generate(event_type="community") == await generate(event_type="community")


async def test_patch_locks_line_and_rebalances() -> None:
    plan = await generate()
    patch = PlanPatch.model_validate(
        {"budget": [{"category": "venue", "is_locked": True, "amount": "45000"}]}
    )
    updated = planner.apply_patch(plan, patch, TODAY, NOW + timedelta(hours=1))
    amounts = by_category(updated)
    assert amounts["venue"] == Decimal("45000")
    assert sum(amounts.values()) == plan.context.total_budget
    assert amounts["catering"] < by_category(plan)["catering"]
    assert updated.updated_at == NOW + timedelta(hours=1)


async def test_patch_lock_without_amount_pins_current_value_and_unlock_releases() -> None:
    plan = await generate()
    lock = PlanPatch.model_validate({"budget": [{"category": "decor", "is_locked": True}]})
    locked = planner.apply_patch(plan, lock, TODAY, NOW)
    decor = next(line for line in locked.budget if line.category == "decor")
    assert decor.is_locked and decor.amount == by_category(plan)["decor"]

    unlock = PlanPatch.model_validate({"budget": [{"category": "decor", "is_locked": False}]})
    unlocked = planner.apply_patch(locked, unlock, TODAY, NOW)
    assert not any(line.is_locked for line in unlocked.budget)


async def test_patch_marks_milestones_and_vendors() -> None:
    plan = await generate()
    patch = PlanPatch.model_validate(
        {
            "milestones": [{"title": "Book venue", "status": "done"}],
            "vendors": [{"category": "venue", "status": "booked"}],
        }
    )
    updated = planner.apply_patch(plan, patch, TODAY, NOW)
    assert next(m for m in updated.timeline if m.title == "Book venue").status == "done"
    assert next(v for v in updated.vendors if v.category == "venue").status == "booked"


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"budget": [{"category": "fireworks", "is_locked": True}]}, "Unknown budget"),
        ({"budget": [{"category": "venue", "is_locked": True, "amount": "999999"}]}, "exceed"),
        ({"milestones": [{"title": "Hire a dragon", "status": "done"}]}, "Unknown milestone"),
        ({"vendors": [{"category": "dragon", "status": "booked"}]}, "Unknown vendor"),
    ],
)
async def test_patch_rejects_bad_edits(patch: dict[str, object], message: str) -> None:
    plan = await generate()
    with pytest.raises(PlanUpdateError, match=message):
        planner.apply_patch(plan, PlanPatch.model_validate(patch), TODAY, NOW)


def test_unlock_with_amount_is_invalid() -> None:
    with pytest.raises(ValueError, match="only be set when locking"):
        PlanPatch.model_validate(
            {"budget": [{"category": "venue", "is_locked": False, "amount": "1"}]}
        )


async def test_refresh_marks_newly_overdue_milestones() -> None:
    plan = await generate()
    later = plan.timeline[1].due_date + timedelta(days=1)
    refreshed = planner.refresh(plan, later)
    assert refreshed.timeline[0].status == "late"
    assert "MISSED_MILESTONE" in codes(refreshed)
    assert refreshed.updated_at == plan.updated_at


async def test_refresh_keeps_llm_advisory_until_ideas_regenerate() -> None:
    plan = await generate(FailingProvider())
    assert "LLM_UNAVAILABLE" in codes(planner.refresh(plan, TODAY))

    still_failing = await planner.regenerate_ideas(plan, TODAY, FailingProvider(), NOW)
    assert "LLM_UNAVAILABLE" in codes(still_failing) and still_failing.ideas == []

    recovered = await planner.regenerate_ideas(plan, TODAY, MockProvider(), NOW)
    assert recovered.ideas
    assert "LLM_UNAVAILABLE" not in codes(recovered)
