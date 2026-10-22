from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.core.budget import allocate
from app.core.models import BudgetLine, EventContext, Idea, Plan, VendorItem
from app.core.timeline import schedule
from app.core.vendors import checklist
from app.llm.mock_provider import MockProvider
from app.llm.prompts import ContextExtraction
from app.llm.provider import LLMOutputError

TODAY = date(2026, 10, 21)
NOW = datetime(2026, 10, 21, 12, 0)
LLM_METHODS = ("extract_context", "generate_ideas", "enrich_vendors", "answer_question")
DEMO_TEXT = "I'm planning my sister's 30th for ~40 people in Pune, budget 1.5 lakh, in 6 weeks"


def make_context(**overrides: Any) -> EventContext:
    """A semi-formal 40-guest city birthday on 5 Dec 2026 with a 1.5 lakh budget."""
    fields: dict[str, Any] = {
        "event_type": "birthday",
        "event_date": date(2026, 12, 5),
        "guest_count": 40,
        "total_budget": Decimal("150000"),
        "location_tier": "city",
        "formality": "semi",
    }
    return EventContext(**(fields | overrides))


def make_plan(context: EventContext) -> Plan:
    """An engine-only plan for `context`, as of TODAY."""
    timeline = schedule(context, TODAY)
    return Plan(
        id="plan-1",
        context=context,
        budget=allocate(context),
        timeline=timeline,
        vendors=checklist(context, timeline),
        created_at=NOW,
        updated_at=NOW,
    )


class FailingProvider(MockProvider):
    """Raises LLMOutputError from the named methods (all of them if none are named)."""

    def __init__(self, *failing: str) -> None:
        self.failing = set(failing) or set(LLM_METHODS)

    def _check(self, method: str) -> None:
        if method in self.failing:
            raise LLMOutputError(f"{method} failed")

    async def extract_context(self, text: str, today: date) -> ContextExtraction:
        self._check("extract_context")
        return await super().extract_context(text, today)

    async def generate_ideas(
        self, context: EventContext, budget: Sequence[BudgetLine]
    ) -> list[Idea]:
        self._check("generate_ideas")
        return await super().generate_ideas(context, budget)

    async def enrich_vendors(
        self, context: EventContext, vendors: Sequence[VendorItem]
    ) -> dict[str, list[str]]:
        self._check("enrich_vendors")
        return await super().enrich_vendors(context, vendors)

    async def answer_question(self, question: str, plan: Plan, facts: Mapping[str, object]) -> str:
        self._check("answer_question")
        return await super().answer_question(question, plan, facts)
