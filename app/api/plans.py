"""Plan routes: intake, create, read, edit, regenerate ideas."""

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import Ideas, Now, Provider, Repo, StoredPlan, Today, rate_limit
from app.core.models import REQUIRED_CONTEXT_FIELDS, EventContext, Plan, ShortText
from app.llm.provider import LLMError
from app.services import planner
from app.services.planner import PlanPatch, PlanUpdateError

router = APIRouter(prefix="/api/plans", tags=["plans"])
LLM_ROUTE = [Depends(rate_limit)]


class IntakeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2000)


class RegenerateRequest(BaseModel):
    """Optional new preferences to steer the next set of ideas."""

    model_config = ConfigDict(extra="forbid")

    preferences: list[ShortText] | None = Field(default=None, max_length=20)


class IntakeResponse(BaseModel):
    """Fields found in the description, those still needed, and whether the LLM was bypassed."""

    fields: dict[str, object]
    missing_fields: list[str]
    degraded: bool = False


@router.post("/intake", dependencies=LLM_ROUTE)
async def intake(body: IntakeRequest, provider: Provider, today: Today) -> IntakeResponse:
    """Turn a free-text description into form fields, listing the ones still missing."""
    try:
        result = await provider.extract_context(body.text, today)
    except LLMError:
        return IntakeResponse(
            fields={}, missing_fields=list(REQUIRED_CONTEXT_FIELDS), degraded=True
        )
    return IntakeResponse(fields=result.fields, missing_fields=result.missing_fields)


@router.post("", status_code=status.HTTP_201_CREATED, dependencies=LLM_ROUTE)
async def create_plan(
    context: EventContext, provider: Provider, repo: Repo, ideas: Ideas, today: Today, now: Now
) -> Plan:
    """Generate and store a plan for the event."""
    if context.event_date < today:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "The event date has passed.")
    plan = await planner.generate(
        context, today, provider, plan_id=uuid4().hex, now=now, ideas_cache=ideas
    )
    await run_in_threadpool(repo.create, plan)
    return plan


@router.get("/{plan_id}")
def get_plan(plan: StoredPlan, today: Today) -> Plan:
    """The plan, with overdue milestones and advisories brought up to date."""
    return planner.refresh(plan, today)


@router.patch("/{plan_id}")
def update_plan(patch: PlanPatch, plan: StoredPlan, repo: Repo, today: Today, now: Now) -> Plan:
    """Lock or unlock budget lines (rebalancing the rest) and update milestone/vendor status."""
    try:
        updated = planner.apply_patch(plan, patch, today, now)
    except PlanUpdateError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    repo.update(updated)
    return updated


@router.post("/{plan_id}/regenerate-ideas", dependencies=LLM_ROUTE)
async def regenerate_ideas(
    plan: StoredPlan,
    provider: Provider,
    repo: Repo,
    ideas: Ideas,
    today: Today,
    now: Now,
    body: RegenerateRequest | None = None,
) -> Plan:
    """Ask the LLM for fresh ideas, optionally with new preferences; the budget is unchanged."""
    preferences = body.preferences if body else None
    updated = await planner.regenerate_ideas(
        plan, today, provider, now, preferences=preferences, ideas_cache=ideas
    )
    await run_in_threadpool(repo.update, updated)
    return updated
