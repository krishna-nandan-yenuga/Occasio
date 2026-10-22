"""Plan generation and updates: run the engine, enrich with the LLM, merge, advise.

The engine always produces a complete plan; the LLM only adds ideas and vendor questions. If
an LLM call fails, the plan is still returned, carrying an LLM_UNAVAILABLE advisory until
ideas are regenerated successfully. Ideas for an identical context and budget come from an
optional IdeasCache instead of the LLM.
"""

import asyncio
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Literal, Self, TypeVar
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.budget import BudgetError, allocate, rebalance
from app.core.decisions import SEVERITY_ORDER, advise
from app.core.models import (
    Advisory,
    BudgetLine,
    EventContext,
    Idea,
    Milestone,
    Money,
    Plan,
    ShortText,
    VendorItem,
    VendorStatus,
)
from app.core.timeline import mark_overdue, schedule
from app.core.vendors import checklist
from app.llm.provider import LLMError, LLMProvider
from app.services.ideas_cache import IdeasCache

logger = logging.getLogger(__name__)

LLM_UNAVAILABLE = Advisory(
    severity="warn",
    code="LLM_UNAVAILABLE",
    message=(
        "Ideas and tailored vendor questions could not be generated right now. The budget, "
        "timeline and vendor checklist are complete."
    ),
    suggested_action="Try regenerating ideas in a few minutes.",
)

T = TypeVar("T")
ItemT = TypeVar("ItemT", Milestone, VendorItem)


class PlanUpdateError(ValueError):
    """A patch refers to unknown items or asks for an impossible budget."""


class _Patch(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BudgetLinePatch(_Patch):
    """Lock a line (at `amount`, or its current amount) or unlock it."""

    category: ShortText
    is_locked: bool
    amount: Money | None = None

    @model_validator(mode="after")
    def _amount_needs_lock(self) -> Self:
        if self.amount is not None and not self.is_locked:
            raise ValueError("amount can only be set when locking a line")
        return self


class MilestonePatch(_Patch):
    title: ShortText
    status: Literal["todo", "done"]


class VendorPatch(_Patch):
    category: ShortText
    status: VendorStatus


class PlanPatch(_Patch):
    """User edits to a plan; every part is optional."""

    budget: list[BudgetLinePatch] = Field(default_factory=list, max_length=30)
    milestones: list[MilestonePatch] = Field(default_factory=list, max_length=50)
    vendors: list[VendorPatch] = Field(default_factory=list, max_length=30)


async def generate(
    context: EventContext,
    today: date,
    provider: LLMProvider,
    *,
    plan_id: str | None = None,
    now: datetime | None = None,
    ideas_cache: IdeasCache | None = None,
) -> Plan:
    """Build a plan: engine first, then ideas and vendor questions from the LLM concurrently."""
    budget = allocate(context)
    timeline = schedule(context, today)
    vendors = checklist(context, timeline)
    ideas_result, questions_result = await asyncio.gather(
        _ideas(context, budget, provider, ideas_cache),
        provider.enrich_vendors(context, vendors),
        return_exceptions=True,
    )
    ideas, ideas_failed = _unwrap(ideas_result, [])
    questions, questions_failed = _unwrap(questions_result, {})
    created = now or datetime.now(UTC)
    plan = Plan(
        id=plan_id or uuid4().hex,
        context=context,
        budget=budget,
        timeline=timeline,
        vendors=_merge_questions(vendors, questions),
        ideas=ideas,
        created_at=created,
        updated_at=created,
    )
    return _rederive(plan, today, llm_failed=ideas_failed or questions_failed)


def refresh(plan: Plan, today: date) -> Plan:
    """Re-derive time-dependent state (overdue milestones, advisories) as of `today`."""
    return _rederive(plan, today, llm_failed=_has_llm_advisory(plan))


def apply_patch(plan: Plan, patch: PlanPatch, today: date, now: datetime) -> Plan:
    """Apply user edits, rebalance the budget around locked lines, and re-derive advisories.

    Raises PlanUpdateError for unknown categories or titles, or an impossible budget.
    """
    patched = plan.model_copy(
        update={
            "budget": _patch_budget(plan.budget, patch.budget),
            "timeline": _patch_status(
                plan.timeline, {m.title: m.status for m in patch.milestones}, "milestone"
            ),
            "vendors": _patch_status(
                plan.vendors, {v.category: v.status for v in patch.vendors}, "vendor"
            ),
            "updated_at": now,
        }
    )
    return refresh(patched, today)


async def regenerate_ideas(
    plan: Plan,
    today: date,
    provider: LLMProvider,
    now: datetime,
    *,
    preferences: Sequence[str] | None = None,
    ideas_cache: IdeasCache | None = None,
) -> Plan:
    """Ask for fresh ideas, optionally with new preferences; the budget is not recomputed.

    On LLM failure the old ideas (and preferences) are kept and LLM_UNAVAILABLE is flagged.
    """
    context = plan.context
    if preferences is not None:
        context = context.model_copy(update={"preferences": list(preferences)})
    try:
        ideas = await provider.generate_ideas(context, plan.budget)
    except LLMError as error:
        logger.warning("Regenerating ideas failed: %s", error)
        return _rederive(plan.model_copy(update={"updated_at": now}), today, llm_failed=True)
    if ideas_cache is not None:
        ideas_cache.put(IdeasCache.key(context, plan.budget), ideas)
    updated = plan.model_copy(update={"context": context, "ideas": ideas, "updated_at": now})
    return _rederive(updated, today, llm_failed=False)


async def _ideas(
    context: EventContext,
    budget: Sequence[BudgetLine],
    provider: LLMProvider,
    cache: IdeasCache | None,
) -> list[Idea]:
    """Ideas from the cache when this exact context and budget were seen before, else the LLM."""
    key = IdeasCache.key(context, budget)
    if cache is not None and (cached := cache.get(key)) is not None:
        return cached
    ideas = await provider.generate_ideas(context, budget)
    if cache is not None:
        cache.put(key, ideas)
    return ideas


def _rederive(plan: Plan, today: date, *, llm_failed: bool) -> Plan:
    timeline = mark_overdue(plan.timeline, today)
    advisories = advise(plan.context, plan.budget, timeline, today)
    if llm_failed:
        advisories.append(LLM_UNAVAILABLE)
    advisories.sort(key=lambda advisory: SEVERITY_ORDER[advisory.severity])
    return plan.model_copy(update={"timeline": timeline, "advisories": advisories})


def _has_llm_advisory(plan: Plan) -> bool:
    return any(advisory.code == LLM_UNAVAILABLE.code for advisory in plan.advisories)


def _unwrap(result: T | BaseException, default: T) -> tuple[T, bool]:
    """The value of a gathered call, or `default` and True if it failed with an LLMError."""
    if isinstance(result, LLMError):
        logger.warning("LLM enrichment failed: %s", result)
        return default, True
    if isinstance(result, BaseException):
        raise result
    return result, False


def _merge_questions(
    vendors: Sequence[VendorItem], extra: Mapping[str, list[str]]
) -> list[VendorItem]:
    """Append the LLM's questions to each vendor's baseline ones, skipping duplicates."""
    merged = []
    for vendor in vendors:
        questions = list(vendor.questions_to_ask)
        seen = {question.casefold() for question in questions}
        for question in extra.get(vendor.category, []):
            if question.casefold() not in seen:
                questions.append(question)
                seen.add(question.casefold())
        merged.append(vendor.model_copy(update={"questions_to_ask": questions}))
    return merged


def _patch_budget(
    lines: Sequence[BudgetLine], changes: Sequence[BudgetLinePatch]
) -> list[BudgetLine]:
    if not changes:
        return list(lines)
    current = {line.category: line.amount for line in lines}
    unknown = {change.category for change in changes} - set(current)
    if unknown:
        raise PlanUpdateError(f"Unknown budget categories: {', '.join(sorted(unknown))}.")
    unlocked = {change.category for change in changes if not change.is_locked}
    lines = [
        line.model_copy(update={"is_locked": False}) if line.category in unlocked else line
        for line in lines
    ]
    locked: dict[str, Decimal] = {
        change.category: current[change.category] if change.amount is None else change.amount
        for change in changes
        if change.is_locked
    }
    try:
        return rebalance(lines, locked)
    except BudgetError as error:
        raise PlanUpdateError(str(error)) from error


def _patch_status(items: Sequence[ItemT], statuses: Mapping[str, str], kind: str) -> list[ItemT]:
    """Set the status of items named in `statuses` (by title or category)."""
    names = {_name(item) for item in items}
    unknown = set(statuses) - names
    if unknown:
        raise PlanUpdateError(f"Unknown {kind}s: {', '.join(sorted(unknown))}.")
    return [
        item.model_copy(update={"status": statuses[_name(item)]})
        if _name(item) in statuses
        else item
        for item in items
    ]


def _name(item: Milestone | VendorItem) -> str:
    return item.title if isinstance(item, Milestone) else item.category
