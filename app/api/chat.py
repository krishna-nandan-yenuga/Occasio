"""Assistant chat over a stored plan."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import Provider, StoredPlan, Today, rate_limit
from app.services import assistant, planner
from app.services.assistant import AssistantReply

router = APIRouter(prefix="/api/plans", tags=["chat"])


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)


@router.post("/{plan_id}/chat", dependencies=[Depends(rate_limit)])
async def chat(
    body: ChatRequest, plan: StoredPlan, provider: Provider, today: Today
) -> AssistantReply:
    """Answer a question about the plan from engine-computed facts."""
    return await assistant.answer(planner.refresh(plan, today), body.question, provider, today)
