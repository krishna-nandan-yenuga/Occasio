"""Plan assistant: the engine computes every number, the LLM only phrases the answer.

`engine_facts` gathers what a question about the plan usually needs (money left, what is due
next, what is late) and, for common asks such as "can I afford a live band?", compares an
illustrative typical cost with the matching budget line.
"""

import logging
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from pydantic import BaseModel

from app.core.budget import CONTINGENCY, is_kids_party
from app.core.models import Plan
from app.core.profiles import LOCATION_PRICE_INDEX
from app.llm.provider import LLMError, LLMProvider

logger = logging.getLogger(__name__)

NEXT_MILESTONES = 3
CENT = Decimal("0.01")


@dataclass(frozen=True)
class CommonAsk:
    """A frequently asked-for extra with an illustrative city-tier cost in INR."""

    label: str
    pattern: re.Pattern[str]
    category: str
    typical_cost: Decimal
    per_guest: bool = False


def _ask(label: str, pattern: str, category: str, cost: int, per_guest: bool = False) -> CommonAsk:
    return CommonAsk(label, re.compile(pattern, re.IGNORECASE), category, Decimal(cost), per_guest)


COMMON_ASKS = (
    _ask("live band", r"\blive (band|music)\b", "entertainment", 75_000),
    _ask("DJ", r"\bdj\b", "entertainment", 25_000),
    _ask("magician", r"\bmagician\b", "entertainment", 10_000),
    _ask("photographer", r"\bphotograph(er|y)\b", "photography", 30_000),
    _ask("videographer", r"\bvideo(grapher|graphy)?\b", "photography", 40_000),
    _ask("open bar", r"\b(open bar|alcohol|drinks)\b", "bar", 900, per_guest=True),
)
_EXTRA_GUESTS = re.compile(r"\b(\d{1,5})\s*(?:more|extra|additional)\s*(?:guests|people)\b", re.I)


class AssistantReply(BaseModel):
    """The answer, the facts it was based on, and whether the LLM was bypassed."""

    answer: str
    facts: dict[str, object]
    degraded: bool = False


async def answer(plan: Plan, question: str, provider: LLMProvider, today: date) -> AssistantReply:
    """Answer `question` about `plan`; if the LLM fails, summarise the facts instead."""
    facts = engine_facts(plan, question, today)
    try:
        text = await provider.answer_question(question, plan, facts)
    except LLMError as error:
        logger.warning("Assistant answer failed: %s", error)
        return AssistantReply(answer=_fallback_answer(facts), facts=facts, degraded=True)
    return AssistantReply(answer=text, facts=facts)


def engine_facts(plan: Plan, question: str, today: date) -> dict[str, object]:
    """Numbers and dates from the plan that are relevant to `question`, as JSON-ready values."""
    context = plan.context
    amounts = {line.category: line.amount for line in plan.budget}
    booked = {vendor.category for vendor in plan.vendors if vendor.status == "booked"}
    committed = sum((amounts.get(category, Decimal(0)) for category in booked), Decimal(0))
    upcoming = sorted(
        (m for m in plan.timeline if m.status == "todo" and m.due_date >= today),
        key=lambda m: m.due_date,
    )
    facts: dict[str, object] = {
        "currency": context.currency,
        "total_budget": str(context.total_budget),
        "budget_by_category": {category: str(amount) for category, amount in amounts.items()},
        "committed_to_booked_vendors": str(committed),
        "remaining_budget": str(context.total_budget - committed),
        "contingency_available": str(amounts.get(CONTINGENCY, Decimal(0))),
        "budget_per_guest": str((context.total_budget / context.guest_count).quantize(CENT)),
        "days_until_event": (context.event_date - today).days,
        "next_milestones": [
            {"title": m.title, "due_date": m.due_date.isoformat()}
            for m in upcoming[:NEXT_MILESTONES]
        ],
        "late_items": _late_items(plan, today),
    }
    asks = ask_feasibility(plan, question)
    if asks:
        facts["asks"] = asks
        facts["ask_costs_note"] = "Typical costs are illustrative estimates, not vendor quotes."
    return facts


def ask_feasibility(plan: Plan, question: str) -> list[dict[str, str]]:
    """For each common ask mentioned in `question`, whether its typical cost fits the budget."""
    context = plan.context
    if context.currency != "INR":
        return []
    amounts = {line.category: line.amount for line in plan.budget}
    index = LOCATION_PRICE_INDEX[context.location_tier]
    costs: list[tuple[str, str, Decimal]] = [
        (
            ask.label,
            ask.category,
            ask.typical_cost * index * (context.guest_count if ask.per_guest else 1),
        )
        for ask in COMMON_ASKS
        if ask.pattern.search(question)
    ]
    if extra := _EXTRA_GUESTS.search(question):
        per_guest = amounts.get("catering", Decimal(0)) / context.guest_count
        costs.append((f"{extra.group(1)} more guests", "catering", per_guest * int(extra.group(1))))
    return [
        _verdict(label, category, cost.quantize(Decimal(1)), amounts, is_kids_party(context))
        for label, category, cost in costs
    ]


def _verdict(
    label: str, category: str, cost: Decimal, amounts: dict[str, Decimal], kids_party: bool
) -> dict[str, str]:
    available = amounts.get(category, Decimal(0))
    reserve = amounts.get(CONTINGENCY, Decimal(0))
    if kids_party and category == "bar":
        verdict = "not suitable for a kids' party"
    elif cost <= available:
        verdict = f"fits within the {category} budget"
    elif cost <= available + reserve:
        verdict = f"needs {cost - available} more than the {category} budget; contingency covers it"
    else:
        verdict = f"over budget by {cost - available - reserve} even after using contingency"
    return {
        "ask": label,
        "typical_cost_inr": str(cost),
        "budget_category": category,
        "category_budget_inr": str(available),
        "verdict": verdict,
    }


def _late_items(plan: Plan, today: date) -> list[str]:
    late = [
        f"{m.title} (was due {m.due_date.isoformat()})"
        for m in plan.timeline
        if m.status == "late" or (m.status == "todo" and m.due_date < today)
    ]
    late += [
        f"Book {v.category} (by {v.booked_by.isoformat()})"
        for v in plan.vendors
        if v.status != "booked" and v.booked_by < today
    ]
    return late


def _fallback_answer(facts: dict[str, object]) -> str:
    currency = facts["currency"]
    parts = [
        f"remaining budget {facts['remaining_budget']} {currency}",
        f"contingency {facts['contingency_available']} {currency}",
        f"{facts['days_until_event']} days to go",
    ]
    asks = facts.get("asks")
    if isinstance(asks, list):
        parts += [f"{ask['ask']}: {ask['verdict']}" for ask in asks]
    return "The assistant is unavailable right now. From your plan: " + "; ".join(parts) + "."
