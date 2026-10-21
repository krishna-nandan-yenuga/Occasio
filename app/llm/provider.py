"""The LLMProvider interface, its errors, and the factory that picks an implementation."""

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Protocol

from app.config import Settings
from app.core.models import BudgetLine, EventContext, Idea, Plan, VendorItem
from app.llm.prompts import ContextExtraction


class LLMError(Exception):
    """Base class for LLM failures; callers fall back to engine-only output."""


class LLMOutputError(LLMError):
    """The model's output was missing, refused or failed validation after a retry."""


class LLMUnavailableError(LLMError):
    """The LLM API could not be reached or rejected the request."""


class LLMProvider(Protocol):
    """Everything the services ask of a language model."""

    async def extract_context(self, text: str, today: date) -> ContextExtraction:
        """Pull event details out of a free-text description."""
        ...

    async def generate_ideas(
        self, context: EventContext, budget: Sequence[BudgetLine]
    ) -> list[Idea]:
        """Personalised theme, menu, activity, décor and gifting ideas."""
        ...

    async def enrich_vendors(
        self, context: EventContext, vendors: Sequence[VendorItem]
    ) -> dict[str, list[str]]:
        """Extra questions to ask, keyed by vendor category."""
        ...

    async def answer_question(self, question: str, plan: Plan, facts: Mapping[str, object]) -> str:
        """Answer a question about `plan`, taking numbers only from `facts`."""
        ...


def get_provider(settings: Settings) -> LLMProvider:
    """The provider selected by `settings.llm_provider`."""
    # Imported here so mock mode never loads the Anthropic SDK client.
    if settings.llm_provider == "anthropic":
        from app.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider.from_settings(settings)
    from app.llm.mock_provider import MockProvider

    return MockProvider()
