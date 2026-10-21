"""Budget engine: allocate a total across categories, rebalance after edits, check feasibility.

Pure functions, no I/O. Amounts are the source of truth: each is rounded down to two decimal
places and the rounding remainder goes to one line (contingency when possible), so lines
always sum exactly to the total. Percentages are derived from amounts for display only.
"""

from collections.abc import Mapping, Sequence
from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal

from app.core.models import Advisory, BudgetLine, EventContext
from app.core.profiles import LOCATION_PRICE_INDEX, get_profile

CENT = Decimal("0.01")
HUNDRED = Decimal(100)
CONTINGENCY = "contingency"
MIN_CONTINGENCY_PERCENT = Decimal(5)
TICKETED_CONTINGENCY_PERCENT = Decimal(10)
TICKETED_PERMITS_BOOST = Decimal(2)
# Catering share moves by 20% of the relative headcount change, clamped to [0.8, 1.3].
CATERING_SENSITIVITY = Decimal("0.2")
CATERING_FACTOR_MIN, CATERING_FACTOR_MAX = Decimal("0.8"), Decimal("1.3")
FORMALITY_LABEL = {"casual": "casual", "semi": "semi-formal", "formal": "formal"}


class BudgetError(ValueError):
    """Raised when a requested rebalance cannot be satisfied."""


def is_kids_party(context: EventContext) -> bool:
    """True for a birthday flagged `kids_party` or whose honoree is under 18."""
    if context.event_type != "birthday":
        return False
    age = context.extras.get("honoree_age")
    is_minor = isinstance(age, int) and not isinstance(age, bool) and age < 18
    return context.extras.get("kids_party") is True or is_minor


def is_ticketed(context: EventContext) -> bool:
    """True for a community event flagged `ticketed`."""
    return context.event_type == "community" and context.extras.get("ticketed") is True


def allocate(context: EventContext) -> list[BudgetLine]:
    """Split `context.total_budget` across the profile's categories, adjusted for context."""
    shares, notes = _target_shares(context)
    total = context.total_budget
    amounts = {category: _round_down(total * share / HUNDRED) for category, share in shares.items()}
    amounts[CONTINGENCY] += total - sum(amounts.values(), Decimal(0))
    return [
        BudgetLine(
            category=category,
            amount=amount,
            percent=_percent(amount, total),
            notes=notes.get(category, ""),
        )
        for category, amount in amounts.items()
    ]


def rebalance(lines: Sequence[BudgetLine], locked: Mapping[str, Decimal]) -> list[BudgetLine]:
    """Pin the `locked` category amounts and spread the rest of the total over unlocked lines.

    The total (sum of current amounts) is preserved. Lines already marked `is_locked` keep
    their amounts; unlocked lines share what remains in proportion to their current amounts,
    except that an unlocked contingency never drops below MIN_CONTINGENCY_PERCENT while money
    remains to cover it. Raises BudgetError for unknown categories or when locked amounts
    exceed the total.
    """
    unknown = set(locked) - {line.category for line in lines}
    if unknown:
        raise BudgetError(f"Unknown budget categories: {', '.join(sorted(unknown))}.")
    total = sum((line.amount for line in lines), Decimal(0))
    pinned = {line.category: line.amount for line in lines if line.is_locked} | dict(locked)
    remainder = total - sum(pinned.values(), Decimal(0))
    if remainder < 0:
        raise BudgetError(f"Locked amounts exceed the total budget by {-remainder}.")
    amounts = pinned | _spread_keeping_reserve(
        remainder, [line for line in lines if line.category not in pinned], total
    )
    return [
        BudgetLine(
            category=line.category,
            amount=amounts[line.category],
            percent=_percent(amounts[line.category], total),
            notes=line.notes,
            is_locked=line.category in pinned,
        )
        for line in lines
    ]


def per_guest_thresholds(context: EventContext) -> tuple[Decimal, Decimal]:
    """INR per-guest (tight_below, generous_above) for the event, adjusted for location."""
    low, high = get_profile(context.event_type).per_guest_spend[context.formality]
    price_index = LOCATION_PRICE_INDEX[context.location_tier]
    return low * price_index, high * price_index


def feasibility(context: EventContext, lines: Sequence[BudgetLine]) -> list[Advisory]:
    """Check the budget against the event and return advisories (empty if all is well)."""
    return [
        *_check_total(context, lines),
        *_check_contingency(context, lines),
        *_check_per_guest_spend(context),
    ]


def _target_shares(context: EventContext) -> tuple[dict[str, Decimal], dict[str, str]]:
    """Profile percentages adjusted for context, plus a note explaining each adjustment."""
    profile = get_profile(context.event_type)
    shares = dict(profile.allocations[context.formality])
    contingency = shares[CONTINGENCY]
    notes = {CONTINGENCY: "Reserve for surprises; also absorbs rounding."}

    if is_kids_party(context):
        shares["entertainment"] += shares["bar"]
        shares["bar"] = Decimal(0)
        notes["bar"] = "No alcohol at a kids' party; this share moved to entertainment."
    price_index = LOCATION_PRICE_INDEX[context.location_tier]
    if price_index != 1:
        shares["venue"] *= price_index
        notes["venue"] = f"Adjusted for {context.location_tier} venue prices."
    catering_factor = _catering_factor(context.guest_count, profile.typical_guests)
    if catering_factor != 1:
        shares["catering"] *= catering_factor
        notes["catering"] = (
            f"Scaled for {context.guest_count} guests (profile assumes {profile.typical_guests})."
        )
    if is_ticketed(context):
        shares["permits_insurance"] += TICKETED_PERMITS_BOOST
        contingency = TICKETED_CONTINGENCY_PERCENT
        notes["permits_insurance"] = "Ticketed event: performance licence and crowd insurance."
        notes[CONTINGENCY] = "Ticketed event: larger reserve for refunds and surprises."
    return _normalise(shares, contingency), notes


def _catering_factor(guest_count: int, typical_guests: int) -> Decimal:
    ratio = Decimal(guest_count) / Decimal(typical_guests)
    factor = 1 + CATERING_SENSITIVITY * (ratio - 1)
    return min(max(factor, CATERING_FACTOR_MIN), CATERING_FACTOR_MAX)


def _normalise(shares: dict[str, Decimal], contingency: Decimal) -> dict[str, Decimal]:
    """Fix contingency at `contingency` percent and scale the other shares to fill the rest."""
    others = sum(share for category, share in shares.items() if category != CONTINGENCY)
    scale = (HUNDRED - contingency) / others
    return {
        category: contingency if category == CONTINGENCY else share * scale
        for category, share in shares.items()
    }


def _spread_keeping_reserve(
    remainder: Decimal, free: Sequence[BudgetLine], total: Decimal
) -> dict[str, Decimal]:
    """Like `_spread`, but top an unlocked contingency up to the minimum reserve if possible."""
    amounts = _spread(remainder, free)
    reserve = (total * MIN_CONTINGENCY_PERCENT / HUNDRED).quantize(CENT, rounding=ROUND_UP)
    others = [line for line in free if line.category != CONTINGENCY]
    if amounts.get(CONTINGENCY, reserve) < reserve <= remainder and others:
        amounts = _spread(remainder - reserve, others) | {CONTINGENCY: reserve}
    return amounts


def _spread(remainder: Decimal, free: Sequence[BudgetLine]) -> dict[str, Decimal]:
    """Split `remainder` across `free` lines in proportion to their current amounts."""
    if not free:
        if remainder:
            raise BudgetError("Every line is locked; unlock one to absorb the difference.")
        return {}
    weight = sum((line.amount for line in free), Decimal(0))
    amounts = {
        line.category: _round_down(remainder * line.amount / weight) if weight else Decimal(0)
        for line in free
    }
    categories = [line.category for line in free]
    absorber = (
        CONTINGENCY
        if CONTINGENCY in categories
        else max(free, key=lambda line: line.amount).category
    )
    amounts[absorber] += remainder - sum(amounts.values(), Decimal(0))
    return amounts


def _check_total(context: EventContext, lines: Sequence[BudgetLine]) -> list[Advisory]:
    allocated = sum((line.amount for line in lines), Decimal(0))
    if allocated == context.total_budget:
        return []
    return [
        Advisory(
            severity="critical",
            code="BUDGET_MISMATCH",
            message=(
                f"Budget lines add up to {allocated:,} {context.currency} but the total budget "
                f"is {context.total_budget:,} {context.currency}."
            ),
            suggested_action="Rebalance the budget or update the total.",
        )
    ]


def _check_contingency(context: EventContext, lines: Sequence[BudgetLine]) -> list[Advisory]:
    reserve = sum((line.amount for line in lines if line.category == CONTINGENCY), Decimal(0))
    if reserve >= context.total_budget * MIN_CONTINGENCY_PERCENT / HUNDRED:
        return []
    return [
        Advisory(
            severity="warn",
            code="LOW_CONTINGENCY",
            message=f"Contingency is below {MIN_CONTINGENCY_PERCENT}% of the total budget.",
            suggested_action="Keep at least 5% aside for last-minute costs.",
        )
    ]


def _check_per_guest_spend(context: EventContext) -> list[Advisory]:
    """Compare budget per guest with the profile's INR thresholds for this formality and tier."""
    if context.currency != "INR":
        return [
            Advisory(
                severity="info",
                code="SPEND_NOT_BENCHMARKED",
                message="Per-guest benchmarks are in INR, so per-guest spend was not checked.",
            )
        ]
    tight, generous = per_guest_thresholds(context)
    per_guest = context.total_budget / context.guest_count
    spend = f"About {per_guest:,.0f} INR per guest"
    tier = context.location_tier
    event = f"a {FORMALITY_LABEL[context.formality]} {context.event_type} in a {tier} location"
    if per_guest < tight:
        return [
            Advisory(
                severity="critical" if per_guest < tight / 2 else "warn",
                code="BUDGET_TIGHT",
                message=f"{spend} is below the roughly {tight:,.0f} INR typical for {event}.",
                suggested_action="Trim the guest list, pick a more casual format, or add budget.",
            )
        ]
    if per_guest > generous:
        return [
            Advisory(
                severity="info",
                code="BUDGET_GENEROUS",
                message=f"{spend} is above the roughly {generous:,.0f} INR typical for {event}.",
                suggested_action="Consider upgrading key experiences or keeping the surplus.",
            )
        ]
    return []


def _round_down(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_DOWN)


def _percent(amount: Decimal, total: Decimal) -> Decimal:
    if not total:
        return Decimal(0)
    return (amount * HUNDRED / total).quantize(CENT, rounding=ROUND_HALF_UP)
