"""LLMProvider backed by the Anthropic Messages API.

Each call uses structured outputs: the SDK sends the Pydantic schema and validates the reply.
Invalid or missing output is retried once, then raised as LLMOutputError. API errors raise
LLMUnavailableError at once, since the SDK has already retried rate limits and server errors.
Main-model calls opt into server-side refusal fallbacks; the cheap model has none.
"""

import logging
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Literal, Self, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.core.models import BudgetLine, EventContext, Idea, Plan, VendorItem
from app.llm import prompts
from app.llm.prompts import (
    AnswerOutput,
    ContextDraft,
    ContextExtraction,
    IdeasOutput,
    VendorQuestionsOutput,
)
from app.llm.provider import LLMOutputError, LLMUnavailableError

logger = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
ATTEMPTS = 2
REQUEST_TIMEOUT_SECONDS = 60.0

Effort = Literal["low", "medium", "high"]
OutputT = TypeVar("OutputT", bound=BaseModel)


class AnthropicProvider:
    """Calls Claude: `model` for creative work, `cheap_model` for extraction."""

    def __init__(self, client: anthropic.AsyncAnthropic, model: str, cheap_model: str) -> None:
        self._client = client
        self._model = model
        self._cheap_model = cheap_model

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        """Build a provider; an empty key lets the SDK find credentials in the environment."""
        client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key or None, timeout=REQUEST_TIMEOUT_SECONDS
        )
        return cls(client, settings.llm_model, settings.llm_cheap_model)

    async def extract_context(self, text: str, today: date) -> ContextExtraction:
        """Pull event details out of a free-text description (cheap model)."""
        draft = await self._call(
            ContextDraft,
            prompts.EXTRACT_SYSTEM,
            prompts.extract_message(text, today),
            model=self._cheap_model,
            max_tokens=2_000,
            effort="low",
            fallback=False,
        )
        return ContextExtraction.from_draft(draft)

    async def generate_ideas(
        self, context: EventContext, budget: Sequence[BudgetLine]
    ) -> list[Idea]:
        """Personalised ideas for the event."""
        result = await self._call(
            IdeasOutput,
            prompts.IDEAS_SYSTEM,
            prompts.ideas_message(context, budget),
            model=self._model,
            max_tokens=8_000,
            effort="medium",
        )
        return result.ideas

    async def enrich_vendors(
        self, context: EventContext, vendors: Sequence[VendorItem]
    ) -> dict[str, list[str]]:
        """Extra vendor questions, keeping only the categories that were asked about."""
        result = await self._call(
            VendorQuestionsOutput,
            prompts.VENDORS_SYSTEM,
            prompts.vendors_message(context, vendors),
            model=self._model,
            max_tokens=8_000,
            effort="low",
        )
        wanted = {vendor.category for vendor in vendors}
        return {item.category: item.questions for item in result.vendors if item.category in wanted}

    async def answer_question(self, question: str, plan: Plan, facts: Mapping[str, object]) -> str:
        """Answer a question about the plan using only the engine's facts for numbers."""
        result = await self._call(
            AnswerOutput,
            prompts.ANSWER_SYSTEM,
            prompts.answer_message(question, plan, facts),
            model=self._model,
            max_tokens=4_000,
            effort="low",
        )
        return result.answer

    async def _call(
        self,
        output_type: type[OutputT],
        system: str,
        user_message: str,
        *,
        model: str,
        max_tokens: int,
        effort: Effort,
        fallback: bool = True,
    ) -> OutputT:
        """One structured-output request, retried once on invalid or missing output."""
        problem = "no output"
        for attempt in range(1, ATTEMPTS + 1):
            try:
                response = await self._client.beta.messages.parse(
                    model=model,
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user_message}],
                    output_format=output_type,
                    output_config={"effort": effort},
                    betas=[FALLBACK_BETA] if fallback else anthropic.omit,
                    fallbacks="default" if fallback else anthropic.omit,
                )
            except ValidationError as error:
                problem = f"invalid output ({error.error_count()} errors)"
            except anthropic.APIError as error:
                raise LLMUnavailableError(f"{output_type.__name__}: {error}") from error
            else:
                if response.stop_reason == "refusal":
                    raise LLMOutputError(f"{output_type.__name__}: the model declined")
                if response.parsed_output is not None:
                    return response.parsed_output
                problem = f"no output (stop reason {response.stop_reason})"
            logger.warning("%s attempt %d: %s", output_type.__name__, attempt, problem)
        raise LLMOutputError(f"{output_type.__name__}: {problem} after {ATTEMPTS} attempts")
