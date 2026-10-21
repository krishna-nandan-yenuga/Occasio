"""Timeline engine: schedule milestones backwards from the event date.

Each template is due a fixed number of days before the event. When the runway (days from
`today` to the event) is shorter than the plan's horizon, every offset shrinks by the same
factor, but never below MIN_COMPRESSION of its original length: work that cannot fit lands
before `today` and is marked late. Pure and deterministic; `today` is always passed in.
"""

import math
from collections.abc import Sequence
from datetime import date, timedelta
from fractions import Fraction

from app.core.models import Advisory, EventContext, Milestone
from app.core.profiles import MilestoneTemplate, get_profile

MIN_COMPRESSION = Fraction(1, 4)
# Below this compression factor the short runway is a warning rather than a note.
WARN_COMPRESSION = Fraction(1, 2)
ACCESSIBILITY_MILESTONE = MilestoneTemplate(
    "Confirm accessibility arrangements", "accessibility", 21, ("Book venue",)
)


def templates(context: EventContext) -> list[MilestoneTemplate]:
    """The profile's milestone templates plus any the context calls for."""
    result = list(get_profile(context.event_type).milestones)
    if context.accessibility_needs and all(t.category != "accessibility" for t in result):
        result.append(ACCESSIBILITY_MILESTONE)
    return result


def horizon_days(context: EventContext) -> int:
    """Lead time in days the uncompressed plan needs (the earliest milestone's offset)."""
    return max(template.offset_days_before_event for template in templates(context))


def compression(context: EventContext, today: date) -> Fraction:
    """Factor in [MIN_COMPRESSION, 1] applied to template offsets; 1 means no compression."""
    runway = (context.event_date - today).days
    horizon = horizon_days(context)
    if runway >= horizon:
        return Fraction(1)
    return max(Fraction(runway, horizon), MIN_COMPRESSION)


def schedule(context: EventContext, today: date) -> list[Milestone]:
    """Dated milestones, earliest first; any due before `today` are marked late."""
    factor = compression(context, today)
    milestones = []
    for template in templates(context):
        # Rounding up keeps compressed milestones on or before their proportional date.
        offset = math.ceil(template.offset_days_before_event * factor)
        due = context.event_date - timedelta(days=offset)
        milestones.append(
            Milestone(
                title=template.title,
                due_date=due,
                category=template.category,
                status="late" if due < today else "todo",
                depends_on=list(template.depends_on),
            )
        )
    return sorted(milestones, key=lambda milestone: milestone.due_date)


def feasibility(
    context: EventContext, milestones: Sequence[Milestone], today: date
) -> list[Advisory]:
    """Advisories about the runway and about milestones that are overdue as of `today`."""
    runway = (context.event_date - today).days
    if runway < 0:
        return [
            Advisory(
                severity="critical",
                code="EVENT_IN_PAST",
                message=f"The event date {context.event_date.isoformat()} has already passed.",
                suggested_action="Check the event date.",
            )
        ]
    return [*_check_runway(context, today), *_check_overdue(milestones, today)]


def _check_runway(context: EventContext, today: date) -> list[Advisory]:
    factor = compression(context, today)
    if factor == 1:
        return []
    runway = (context.event_date - today).days
    return [
        Advisory(
            severity="warn" if factor < WARN_COMPRESSION else "info",
            code="SHORT_RUNWAY",
            message=(
                f"Only {runway} days until the event; a {context.event_type} usually needs "
                f"about {horizon_days(context)}, so the timeline has been compressed."
            ),
            suggested_action="Book the venue and key vendors first; simplify where you can.",
        )
    ]


def _check_overdue(milestones: Sequence[Milestone], today: date) -> list[Advisory]:
    overdue = [
        m.title
        for m in milestones
        if m.status == "late" or (m.status == "todo" and m.due_date < today)
    ]
    if not overdue:
        return []
    count = "1 milestone is" if len(overdue) == 1 else f"{len(overdue)} milestones are"
    return [
        Advisory(
            severity="warn",
            code="MISSED_MILESTONE",
            message=f"{count} overdue: {', '.join(overdue)}.",
            suggested_action="Do these first, or drop any that no longer matter.",
        )
    ]
