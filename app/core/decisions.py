"""Decision rules: gather advisories from every engine and add cross-cutting checks.

Cross-cutting rules catch problems no single engine sees, typically after the user edits the
budget: a venue budget too small for the guest count, catering below a workable per-guest
amount, alcohol at a kids' party, or a community event without permits or accessibility funds.
"""

from collections.abc import Sequence
from datetime import date
from decimal import Decimal

from app.core import budget, timeline
from app.core.models import Advisory, BudgetLine, EventContext, Milestone

SEVERITY_ORDER = {"critical": 0, "warn": 1, "info": 2}
# A category is flagged only when it is more than 10% below its per-guest floor.
FLOOR_TOLERANCE = Decimal("0.9")
COMMUNITY_MANDATORY = {
    "permits_insurance": "permits and insurance",
    "accessibility": "accessibility",
}


def advise(
    context: EventContext,
    budget_lines: Sequence[BudgetLine],
    milestones: Sequence[Milestone],
    today: date,
) -> list[Advisory]:
    """All advisories for a plan, most severe first."""
    budget_advisories = budget.feasibility(context, budget_lines)
    amounts = {line.category: line.amount for line in budget_lines}
    overall_tight = any(a.code == "BUDGET_TIGHT" for a in budget_advisories)
    advisories = [
        *budget_advisories,
        *timeline.feasibility(context, milestones, today),
        *([] if overall_tight else _check_venue_and_catering(context, amounts)),
        *_check_kids_alcohol(context, amounts),
        *_check_community_mandatory(context, amounts),
    ]
    return sorted(advisories, key=lambda advisory: SEVERITY_ORDER[advisory.severity])


def _check_venue_and_catering(context: EventContext, amounts: dict[str, Decimal]) -> list[Advisory]:
    if context.currency != "INR":
        return []
    floors = _per_guest_floors(context)
    return [*_check_venue(context, amounts, floors), *_check_catering(context, amounts, floors)]


def _per_guest_floors(context: EventContext) -> dict[str, Decimal]:
    """Typical minimum INR per guest for venue and catering.

    A floor is the overall tight per-guest threshold times the share the engine would allocate
    to that category, so these checks only fire when an edit has starved the category.
    """
    tight, _ = budget.per_guest_thresholds(context)
    target = {line.category: line.percent for line in budget.allocate(context)}
    return {category: tight * target[category] / 100 for category in ("venue", "catering")}


def _check_venue(
    context: EventContext, amounts: dict[str, Decimal], floors: dict[str, Decimal]
) -> list[Advisory]:
    venue, guests = amounts.get("venue", Decimal(0)), context.guest_count
    if venue >= floors["venue"] * guests * FLOOR_TOLERANCE:
        return []
    return [
        Advisory(
            severity="warn",
            code="VENUE_TOO_SMALL",
            message=(
                f"A venue budget of {venue:,} INR suits about {int(venue / floors['venue'])} "
                f"guests at typical {context.location_tier} prices, but {guests} are expected."
            ),
            suggested_action="Raise the venue budget, trim the guest list, or host at home.",
        )
    ]


def _check_catering(
    context: EventContext, amounts: dict[str, Decimal], floors: dict[str, Decimal]
) -> list[Advisory]:
    catering, guests = amounts.get("catering", Decimal(0)), context.guest_count
    if catering >= floors["catering"] * guests * FLOOR_TOLERANCE:
        return []
    return [
        Advisory(
            severity="warn",
            code="CATERING_TIGHT",
            message=(
                f"Catering works out to about {catering / guests:,.0f} INR per guest, below "
                f"the roughly {floors['catering']:,.0f} INR typical for this event."
            ),
            suggested_action="Raise the catering budget or choose a simpler menu or buffet.",
        )
    ]


def _check_kids_alcohol(context: EventContext, amounts: dict[str, Decimal]) -> list[Advisory]:
    bar = amounts.get("bar", Decimal(0))
    if not budget.is_kids_party(context) or bar <= 0:
        return []
    return [
        Advisory(
            severity="warn",
            code="ALCOHOL_AT_KIDS_PARTY",
            message=f"{bar:,} {context.currency} is budgeted for a bar at a kids' party.",
            suggested_action="Move the bar budget to entertainment or food.",
        )
    ]


def _check_community_mandatory(
    context: EventContext, amounts: dict[str, Decimal]
) -> list[Advisory]:
    if context.event_type != "community":
        return []
    return [
        Advisory(
            severity="critical",
            code=f"{category.upper()}_UNFUNDED",
            message=f"Community events need a budget for {label}, but none is set aside.",
            suggested_action=f"Restore the {label} line before booking anything else.",
        )
        for category, label in COMMUNITY_MANDATORY.items()
        if amounts.get(category, Decimal(0)) <= 0
    ]
