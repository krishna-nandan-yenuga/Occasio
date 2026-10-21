from datetime import date
from decimal import Decimal
from typing import Any

from app.core.models import EventContext

TODAY = date(2026, 10, 21)


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
