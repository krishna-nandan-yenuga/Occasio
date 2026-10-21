"""Vendor checklist: which vendors to hire, baseline questions to ask, and when to book them.

The LLM layer may add tailored questions later; these baseline ones keep an engine-only plan
useful on its own.
"""

from collections.abc import Sequence
from datetime import date
from textwrap import shorten

from app.core.models import EventContext, Milestone, VendorItem
from app.core.profiles import get_profile

MAX_QUESTION_LENGTH = 200
BASE_QUESTIONS = (
    "Are you available on the event date, and how long can you hold it?",
    "What exactly is included in the quote, and what costs extra?",
    "What are the deposit, payment schedule and cancellation terms?",
)


def checklist(context: EventContext, milestones: Sequence[Milestone]) -> list[VendorItem]:
    """One item per profile vendor category, to be booked by that category's first milestone.

    `milestones` is the plan's timeline (see `timeline.schedule`), so booking dates follow any
    compression. A category without a milestone falls back to the event date.
    """
    first_due: dict[str, date] = {}
    for milestone in sorted(milestones, key=lambda m: m.due_date):
        first_due.setdefault(milestone.category, milestone.due_date)
    items = [
        VendorItem(
            category=vendor.category,
            suggested_vendor_types=list(vendor.suggested_vendor_types),
            questions_to_ask=questions(context, vendor.category),
            booked_by=first_due.get(vendor.category, context.event_date),
        )
        for vendor in get_profile(context.event_type).vendors
    ]
    return sorted(items, key=lambda item: item.booked_by)


def questions(context: EventContext, category: str) -> list[str]:
    """Baseline questions for a vendor, plus ones driven by guest count, diet and access."""
    extra = []
    if category == "venue":
        extra.append(f"Can the space comfortably hold {context.guest_count} guests?")
        if context.accessibility_needs:
            needs = ", ".join(context.accessibility_needs)
            extra.append(f"Can you accommodate these accessibility needs: {needs}?")
    if category == "catering" and context.dietary:
        extra.append(f"Can you cater for these dietary needs: {', '.join(context.dietary)}?")
    return [*BASE_QUESTIONS, *(shorten(q, MAX_QUESTION_LENGTH, placeholder="…") for q in extra)]
