"""Domain models shared by the engine, LLM layer, storage and API.

Money is always `Decimal` with at most two decimal places; floats never enter the engine.
Models carry no clock or randomness: ids and timestamps are supplied by the caller so that
the same input always produces the same plan.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

EventType = Literal["wedding", "birthday", "community"]
LocationTier = Literal["metro", "city", "town"]
Formality = Literal["casual", "semi", "formal"]
MilestoneStatus = Literal["todo", "done", "late"]
VendorStatus = Literal["todo", "contacted", "booked"]
Severity = Literal["info", "warn", "critical"]
IdeaCategory = Literal["theme", "menu", "activity", "decor", "gifting"]

Money = Annotated[Decimal, Field(ge=0, decimal_places=2)]
Percent = Annotated[Decimal, Field(ge=0, le=100, decimal_places=2)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
AdvisoryCode = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]*$", max_length=64)]


class _Model(BaseModel):
    """Base for all domain models: unknown fields are rejected, not silently dropped."""

    model_config = ConfigDict(extra="forbid")


class EventContext(_Model):
    """Everything the engine needs to know about an event."""

    event_type: EventType
    event_date: date
    guest_count: int = Field(ge=1, le=100_000)
    total_budget: Money
    currency: CurrencyCode = "INR"
    location_tier: LocationTier
    formality: Formality
    # Type-specific fields, e.g. ceremonies, honoree_age, ticketed, permits_needed.
    extras: dict[ShortText, ShortText | int | bool] = Field(default_factory=dict, max_length=20)
    preferences: list[ShortText] = Field(default_factory=list, max_length=20)
    accessibility_needs: list[ShortText] = Field(default_factory=list, max_length=20)
    dietary: list[ShortText] = Field(default_factory=list, max_length=20)


# Fields a user must supply (the rest have defaults), in form order.
REQUIRED_CONTEXT_FIELDS = tuple(
    name for name, info in EventContext.model_fields.items() if info.is_required()
)


class BudgetLine(_Model):
    """One category of the budget; `is_locked` marks a user override kept on rebalance."""

    category: ShortText
    amount: Money
    percent: Percent
    notes: LongText = ""
    is_locked: bool = False


class Milestone(_Model):
    """A dated task; `depends_on` lists titles of milestones that should be done first."""

    title: ShortText
    due_date: date
    category: ShortText
    status: MilestoneStatus = "todo"
    depends_on: list[ShortText] = Field(default_factory=list)


class VendorItem(_Model):
    """A vendor to hire, what to ask them, and the date to have them booked by."""

    category: ShortText
    suggested_vendor_types: list[ShortText] = Field(default_factory=list)
    questions_to_ask: list[ShortText] = Field(default_factory=list)
    status: VendorStatus = "todo"
    booked_by: date


class Advisory(_Model):
    """A rule-engine finding, e.g. code `BUDGET_TIGHT` with a suggested fix."""

    severity: Severity
    code: AdvisoryCode
    message: LongText
    suggested_action: LongText = ""


class Idea(_Model):
    """A personalised suggestion (theme, menu, activity, décor or gifting)."""

    category: IdeaCategory
    title: ShortText
    description: LongText


class Plan(_Model):
    """A complete event plan as stored and served by the API."""

    id: ShortText
    context: EventContext
    budget: list[BudgetLine] = Field(default_factory=list)
    timeline: list[Milestone] = Field(default_factory=list)
    vendors: list[VendorItem] = Field(default_factory=list)
    ideas: list[Idea] = Field(default_factory=list)
    advisories: list[Advisory] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
