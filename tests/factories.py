from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.core.budget import allocate
from app.core.models import EventContext, Plan
from app.core.timeline import schedule
from app.core.vendors import checklist

TODAY = date(2026, 10, 21)
NOW = datetime(2026, 10, 21, 12, 0)
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
