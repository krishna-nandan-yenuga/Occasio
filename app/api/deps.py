"""Shared route dependencies: storage, LLM provider, clock, rate limiting, plan lookup.

The clock lives here, at the edge, so services and the engine always receive `today`
explicitly and tests can pin it with `app.dependency_overrides`.
"""

import math
from datetime import UTC, date, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Path, Request, status

from app.api.ratelimit import RateLimiter
from app.core.models import Plan
from app.llm.provider import LLMProvider
from app.services.ideas_cache import IdeasCache
from app.storage.repo import PlanRepository

PLAN_ID_PATTERN = r"^[0-9a-f]{32}$"


def get_repo(request: Request) -> PlanRepository:
    """The app's plan repository."""
    repo: PlanRepository = request.app.state.repo
    return repo


def get_llm(request: Request) -> LLMProvider:
    """The app's LLM provider (mock or Anthropic)."""
    provider: LLMProvider = request.app.state.provider
    return provider


def get_ideas_cache(request: Request) -> IdeasCache:
    """The app-wide cache of generated ideas."""
    cache: IdeasCache = request.app.state.ideas_cache
    return cache


def get_today() -> date:
    """Today's date: the only place the API reads the calendar."""
    return date.today()


def get_now() -> datetime:
    """The current UTC time, for plan timestamps."""
    return datetime.now(UTC)


async def rate_limit(request: Request) -> None:
    """Reject the request with 429 when this client has used up its LLM allowance."""
    limiter: RateLimiter = request.app.state.rate_limiter
    client = request.client.host if request.client else "unknown"
    retry_after = limiter.check(client)
    if retry_after:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many requests; please wait a moment and try again.",
            headers={"Retry-After": str(math.ceil(retry_after))},
        )


Repo = Annotated[PlanRepository, Depends(get_repo)]
Provider = Annotated[LLMProvider, Depends(get_llm)]
Ideas = Annotated[IdeasCache, Depends(get_ideas_cache)]
Today = Annotated[date, Depends(get_today)]
Now = Annotated[datetime, Depends(get_now)]


def load_plan(plan_id: Annotated[str, Path(pattern=PLAN_ID_PATTERN)], repo: Repo) -> Plan:
    """The stored plan named in the path, or 404."""
    plan = repo.get(plan_id)
    if plan is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plan not found.")
    return plan


StoredPlan = Annotated[Plan, Depends(load_plan)]
