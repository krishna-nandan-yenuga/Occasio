"""Per-event-type planning profiles: budget splits, milestone templates and vendor categories.

Percentages are illustrative defaults for the Indian market. Every allocation table sums to
exactly 100 and reserves 5-10% for contingency; the budget engine applies context-specific
adjustments (guest count, location tier, kids' parties, ticketing) on top of these.
Per-guest spend thresholds are in INR.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from app.core.models import EventType, Formality, LocationTier


@dataclass(frozen=True)
class MilestoneTemplate:
    """A milestone due `offset_days_before_event` days before the event date."""

    title: str
    category: str
    offset_days_before_event: int
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class VendorCategory:
    """A vendor category to hire for, with the kinds of vendor that can fill it."""

    category: str
    suggested_vendor_types: tuple[str, ...]


@dataclass(frozen=True)
class EventProfile:
    """All static planning data for one event type.

    `typical_guests` is the headcount the allocation tables assume. `per_guest_spend` gives,
    per formality, the INR per-guest budget below which a plan is tight and above which it is
    generous.
    """

    allocations: Mapping[Formality, Mapping[str, Decimal]]
    milestones: tuple[MilestoneTemplate, ...]
    vendors: tuple[VendorCategory, ...]
    typical_guests: int
    per_guest_spend: Mapping[Formality, tuple[Decimal, Decimal]]


# Relative price level by location; scales the venue share and the per-guest thresholds.
LOCATION_PRICE_INDEX: Mapping[LocationTier, Decimal] = {
    "metro": Decimal("1.25"),
    "city": Decimal("1.00"),
    "town": Decimal("0.80"),
}


def _allocations(**rows: tuple[int, int, int]) -> dict[Formality, dict[str, Decimal]]:
    """Build per-formality tables from rows of (casual, semi, formal) percentages."""
    formalities: tuple[Formality, ...] = ("casual", "semi", "formal")
    return {
        formality: {category: Decimal(row[i]) for category, row in rows.items()}
        for i, formality in enumerate(formalities)
    }


def _spend(
    casual: tuple[int, int], semi: tuple[int, int], formal: tuple[int, int]
) -> dict[Formality, tuple[Decimal, Decimal]]:
    """Build per-formality (tight_below, generous_above) INR per-guest thresholds."""
    rows: dict[Formality, tuple[int, int]] = {"casual": casual, "semi": semi, "formal": formal}
    return {formality: (Decimal(low), Decimal(high)) for formality, (low, high) in rows.items()}


WEDDING = EventProfile(
    allocations=_allocations(
        # category      casual, semi, formal
        venue=(22, 23, 25),
        catering=(30, 28, 27),
        decor=(10, 12, 13),
        photography=(8, 9, 10),
        attire=(8, 8, 7),
        entertainment=(5, 5, 5),
        bar=(4, 4, 3),
        invitations=(2, 2, 2),
        gifts_favors=(3, 2, 1),
        transport=(3, 2, 2),
        contingency=(5, 5, 5),
    ),
    milestones=(
        MilestoneTemplate("Set budget and guest list", "planning", 300),
        MilestoneTemplate("Book venue", "venue", 240, ("Set budget and guest list",)),
        MilestoneTemplate("Book caterer", "catering", 180, ("Book venue",)),
        MilestoneTemplate("Book photographer", "photography", 180, ("Book venue",)),
        MilestoneTemplate("Book decorator", "decor", 150, ("Book venue",)),
        MilestoneTemplate("Book entertainment", "entertainment", 120, ("Book venue",)),
        MilestoneTemplate("Finalise attire", "attire", 90),
        MilestoneTemplate(
            "Send invitations", "invitations", 63, ("Set budget and guest list", "Book venue")
        ),
        MilestoneTemplate("Arrange guest transport", "transport", 45, ("Book venue",)),
        MilestoneTemplate("Menu tasting and finalise menu", "catering", 30, ("Book caterer",)),
        MilestoneTemplate(
            "Confirm RSVPs and final headcount", "planning", 14, ("Send invitations",)
        ),
        MilestoneTemplate("Final vendor confirmations", "planning", 7),
        MilestoneTemplate("Pay balances and pack day-of kit", "planning", 2),
    ),
    vendors=(
        VendorCategory("venue", ("banquet hall", "lawn", "resort", "hotel ballroom")),
        VendorCategory("catering", ("caterer", "hotel in-house catering")),
        VendorCategory("decor", ("decorator", "florist", "lighting vendor")),
        VendorCategory("photography", ("photographer", "videographer")),
        VendorCategory("attire", ("designer", "tailor", "rental boutique")),
        VendorCategory("entertainment", ("DJ", "live band", "choreographer")),
        VendorCategory("bar", ("bar service", "bartender")),
        VendorCategory("invitations", ("printer", "digital invite designer")),
        VendorCategory("transport", ("car rental", "bus operator")),
    ),
    typical_guests=200,
    per_guest_spend=_spend((1_500, 8_000), (2_500, 12_000), (4_000, 20_000)),
)

BIRTHDAY = EventProfile(
    allocations=_allocations(
        # category      casual, semi, formal
        venue=(15, 20, 24),
        catering=(35, 31, 30),
        cake=(8, 7, 6),
        decor=(10, 11, 12),
        entertainment=(12, 11, 9),
        bar=(8, 7, 7),
        photography=(4, 5, 5),
        gifts_favors=(3, 3, 2),
        contingency=(5, 5, 5),
    ),
    milestones=(
        MilestoneTemplate("Set budget and guest list", "planning", 42),
        MilestoneTemplate("Book venue", "venue", 35, ("Set budget and guest list",)),
        MilestoneTemplate("Book caterer", "catering", 28, ("Book venue",)),
        MilestoneTemplate("Book entertainment", "entertainment", 28, ("Book venue",)),
        MilestoneTemplate("Send invitations", "invitations", 21, ("Book venue",)),
        MilestoneTemplate("Order cake", "cake", 10),
        MilestoneTemplate("Buy decor and return gifts", "decor", 7),
        MilestoneTemplate("Confirm RSVPs", "planning", 5, ("Send invitations",)),
        MilestoneTemplate("Final vendor confirmations", "planning", 2),
    ),
    vendors=(
        VendorCategory("venue", ("restaurant", "party hall", "café", "clubhouse")),
        VendorCategory("catering", ("caterer", "restaurant set menu")),
        VendorCategory("cake", ("bakery", "home baker")),
        VendorCategory("decor", ("balloon decorator", "party supplies store")),
        VendorCategory("entertainment", ("DJ", "magician", "game host", "puppeteer")),
        VendorCategory("photography", ("photographer",)),
    ),
    typical_guests=40,
    per_guest_spend=_spend((500, 3_000), (800, 5_000), (1_500, 8_000)),
)

COMMUNITY = EventProfile(
    allocations=_allocations(
        # category          casual, semi, formal
        venue=(15, 18, 20),
        catering=(25, 24, 24),
        av_equipment=(12, 13, 14),
        permits_insurance=(8, 8, 7),
        accessibility=(7, 7, 6),
        marketing=(10, 9, 8),
        volunteers=(8, 6, 5),
        decor=(7, 7, 8),
        contingency=(8, 8, 8),
    ),
    milestones=(
        MilestoneTemplate("Define scope, budget and attendance estimate", "planning", 120),
        MilestoneTemplate(
            "Book venue", "venue", 90, ("Define scope, budget and attendance estimate",)
        ),
        MilestoneTemplate(
            "Apply for permits and event insurance", "permits_insurance", 75, ("Book venue",)
        ),
        MilestoneTemplate("Secure sponsors", "marketing", 60),
        MilestoneTemplate("Recruit volunteers", "volunteers", 60),
        MilestoneTemplate("Book sound and stage", "av_equipment", 45, ("Book venue",)),
        MilestoneTemplate("Book caterer", "catering", 45, ("Book venue",)),
        MilestoneTemplate(
            "Plan accessibility (ramps, seating, interpreters)",
            "accessibility",
            45,
            ("Book venue",),
        ),
        MilestoneTemplate("Launch promotion and registrations", "marketing", 30),
        MilestoneTemplate("Brief volunteers", "volunteers", 7, ("Recruit volunteers",)),
        MilestoneTemplate(
            "Confirm permits received",
            "permits_insurance",
            7,
            ("Apply for permits and event insurance",),
        ),
        MilestoneTemplate("Final vendor confirmations", "planning", 3),
    ),
    vendors=(
        VendorCategory("venue", ("community hall", "park", "school auditorium")),
        VendorCategory("catering", ("caterer", "food stalls")),
        VendorCategory("av_equipment", ("sound and light vendor", "stage rental")),
        VendorCategory("permits_insurance", ("event insurer", "local authority liaison")),
        VendorCategory("accessibility", ("ramp rental", "sign-language interpreter")),
        VendorCategory("marketing", ("printer", "social media promoter")),
        VendorCategory("decor", ("decorator", "banner printer")),
    ),
    typical_guests=150,
    per_guest_spend=_spend((150, 1_500), (300, 2_500), (800, 5_000)),
)

PROFILES: Mapping[EventType, EventProfile] = {
    "wedding": WEDDING,
    "birthday": BIRTHDAY,
    "community": COMMUNITY,
}


def get_profile(event_type: EventType) -> EventProfile:
    """Return the planning profile for `event_type`."""
    return PROFILES[event_type]
