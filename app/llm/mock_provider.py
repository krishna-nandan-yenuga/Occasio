"""Deterministic offline LLMProvider for demos and tests: no network, no API key.

Responses are canned per event type and pass through the same Pydantic output schemas as the
real provider. Extraction uses simple keyword and regex rules, enough for typical demo input
such as "my sister's 30th for ~40 people in Pune, budget 1.5 lakh, in 6 weeks" or "a community
mela for ~300 people on 8 Nov, budget ₹2,00,000, need wheelchair access and veg-only food".
"""

import re
from collections.abc import Mapping, Sequence
from datetime import date, timedelta

from app.core.budget import is_kids_party
from app.core.models import BudgetLine, EventContext, EventType, Idea, Plan, VendorItem
from app.llm.prompts import (
    AnswerOutput,
    ContextDraft,
    ContextExtraction,
    IdeasOutput,
    VendorQuestionsOutput,
)
from app.llm.sanitize import clean_text

EVENT_KEYWORDS: dict[EventType, tuple[str, ...]] = {
    "wedding": ("wedding", "marriage", "shaadi", "reception", "sangeet"),
    "birthday": ("birthday", "bday", "turning"),
    "community": (
        "community",
        "festival",
        "mela",
        "meetup",
        "fundraiser",
        "fair",
        "drive",
        "cleanup",
    ),
}
METROS = (
    "mumbai", "delhi", "gurgaon", "gurugram", "noida", "bengaluru", "bangalore",
    "chennai", "kolkata", "hyderabad", "pune", "ahmedabad",
)  # fmt: skip
FORMALITY_KEYWORDS = {
    "formal": ("formal", "black tie", "black-tie", "gala"),
    "casual": ("casual", "intimate", "house party", "relaxed", "potluck"),
}
DIETARY_PATTERNS = {
    "vegetarian": r"(?<!non-)(?<!non )\b(?:vegetarian|veg(?:-| )only|pure veg|veg)\b",
    "vegan": r"\bvegan\b",
    "jain": r"\bjain\b",
    "gluten-free": r"\bgluten[- ]free\b",
    "halal": r"\bhalal\b",
    "nut allergy": r"\bnut[- ]allerg(?:y|ies)\b",
}
ACCESSIBILITY_PATTERNS = {
    "wheelchair access": r"\bwheel ?chair",
    "step-free access": r"\bstep[- ]free\b",
    "sign-language interpreter": r"\bsign[- ]language\b",
    "hearing loop": r"\bhearing loop\b",
    "seating for elderly guests": r"\belderly\b",
}
MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
UNIT_MULTIPLIER = {"lakh": 100_000, "lac": 100_000, "crore": 10_000_000, "k": 1_000}
DAYS_PER_UNIT = {"day": 1, "week": 7, "month": 30}

_GUESTS = re.compile(r"(\d[\d,]*)\s*(?:people|guests|attendees|persons|pax)")
_BUDGET = re.compile(
    r"(?:budget|₹|rs\.?|inr)\D{0,12}(\d[\d,]*(?:\.\d+)?)\s*(lakhs?|lacs?|crores?|k)?\b"
)
_RUNWAY = re.compile(r"\bin\s+(\d+)\s*(day|week|month)s?\b")
_MONTH = rf"({'|'.join(MONTHS)})[a-z]*\.?"
_DAY_MONTH = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH}(?:,?\s+(\d{{4}}))?")
_MONTH_DAY = re.compile(rf"\b{_MONTH}\s+(\d{{1,2}})(?:st|nd|rd|th)?\b(?:,?\s+(\d{{4}}))?")
_ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_ORDINAL_AGE = re.compile(r"\b(\d{1,3})(?:st|nd|rd|th)\b")

IDEAS: dict[EventType, list[tuple[str, str, str]]] = {
    "wedding": [
        (
            "theme",
            "Marigold and brass",
            "Warm marigold garlands with brass urlis and diyas "
            "tie the ceremonies together and photograph beautifully.",
        ),
        (
            "menu",
            "Regional thali counters",
            "Live counters serving thalis from both families' "
            "home regions turn the meal into a conversation starter.",
        ),
        (
            "activity",
            "Family sangeet medley",
            "Each side rehearses one short number; a host "
            "keeps the running order tight so the evening never drags.",
        ),
        (
            "decor",
            "Floral entry arch",
            "A single statement arch at the entrance gives guests "
            "a photo spot without dressing the whole venue.",
        ),
        (
            "gifting",
            "Local sweet boxes",
            "Small boxes of a sweet from the couple's hometown, "
            "with a handwritten note, make a personal return gift.",
        ),
        (
            "activity",
            "Blessing wall",
            "Guests leave written wishes on cards that the couple keeps as a keepsake album.",
        ),
    ],
    "birthday": [
        (
            "theme",
            "Decade throwback",
            "Music, dress code and décor from the year the guest of "
            "honour was born make an easy, fun brief for guests.",
        ),
        (
            "menu",
            "Small-plates spread",
            "A grazing table of finger food keeps people mingling and suits mixed dietary needs.",
        ),
        (
            "activity",
            "Memory jar",
            "Guests write a favourite memory or wish; read a few aloud before the cake.",
        ),
        (
            "decor",
            "Photo timeline",
            "A string of printed photos across the years doubles as décor and a talking point.",
        ),
        (
            "gifting",
            "Personalised favours",
            "Mini plants or candles with a custom tag are a thoughtful take-home.",
        ),
    ],
    "community": [
        (
            "theme",
            "Neighbourhood showcase",
            "Feature local makers, food and performers so the event feels owned by the community.",
        ),
        (
            "menu",
            "Stall-style food court",
            "A few vetted food stalls spread queues out and give attendees variety.",
        ),
        (
            "activity",
            "Volunteer-led workshops",
            "Short hands-on sessions run by volunteers keep families engaged between main events.",
        ),
        (
            "decor",
            "Wayfinding banners",
            "Clear, high-contrast signs to entrances, toilets, "
            "first aid and quiet zones help everyone, including first-time visitors.",
        ),
        (
            "gifting",
            "Thank-you tokens",
            "Small branded tokens for volunteers and sponsors build goodwill for next year.",
        ),
    ],
}
KIDS_IDEAS = [
    (
        "theme",
        "Treasure hunt adventure",
        "A simple map-and-clues theme lets kids run around "
        "with purpose; adults hide the clues beforehand.",
    ),
    (
        "menu",
        "Build-your-own snacks",
        "Mini sandwich and fruit-skewer stations are fun, quick and easy to keep allergy-safe.",
    ),
    (
        "activity",
        "Craft corner",
        "A supervised table for colouring and simple crafts gives quieter kids a place to settle.",
    ),
    (
        "decor",
        "Balloon garland",
        "One colourful balloon garland behind the cake table is "
        "enough to make the room feel like a party.",
    ),
    (
        "gifting",
        "Activity return gifts",
        "Colouring books or seed kits beat plastic toys and keep the fun going at home.",
    ),
]
VENDOR_QUESTIONS = {
    "venue": [
        "What is the seated and standing capacity of the space we would use?",
        "Until what time can we use the venue, and is there an overtime charge?",
        "Is parking available, and how many cars does it hold?",
    ],
    "catering": [
        "Can we do a tasting before confirming the menu?",
        "How many serving staff do you provide per hundred guests?",
        "How do you keep vegetarian, Jain and allergy-safe food separate?",
    ],
    "photography": [
        "Can we see one complete album from a similar event?",
        "When will we receive edited photos, and in what format?",
    ],
    "decor": [
        "Which items are rented and which are ours to keep?",
        "How long do you need for setup and teardown?",
    ],
    "entertainment": [
        "What equipment do you bring, and what do you need from the venue?",
        "Can you share a set list or plan for the day in advance?",
    ],
}
MOCK_ANSWER_FACTS = {
    "remaining_budget": "Budget not yet committed:",
    "contingency_available": "contingency:",
    "days_until_event": "days to go:",
    "late_items": "overdue:",
}
GENERIC_VENDOR_QUESTIONS = [
    "Have you worked an event of this size before, and can we speak to that client?",
    "Who will be our point of contact on the day?",
]


class MockProvider:
    """Canned, deterministic responses keyed off the event type."""

    async def extract_context(self, text: str, today: date) -> ContextExtraction:
        """Keyword and regex extraction of the common fields."""
        return ContextExtraction.from_draft(_extract(clean_text(text).lower(), today))

    async def generate_ideas(
        self, context: EventContext, budget: Sequence[BudgetLine]
    ) -> list[Idea]:
        """Canned ideas for the event type, with dietary needs noted on menu ideas."""
        canned = KIDS_IDEAS if is_kids_party(context) else IDEAS[context.event_type]
        diet = f" Covers: {', '.join(context.dietary)}." if context.dietary else ""
        ideas = [
            {"category": c, "title": t, "description": d + (diet if c == "menu" else "")}
            for c, t, d in canned
        ]
        return IdeasOutput.model_validate({"ideas": ideas}).ideas

    async def enrich_vendors(
        self, context: EventContext, vendors: Sequence[VendorItem]
    ) -> dict[str, list[str]]:
        """Canned questions per category, generic ones for the rest."""
        payload = {
            "vendors": [
                {
                    "category": v.category,
                    "questions": VENDOR_QUESTIONS.get(v.category, GENERIC_VENDOR_QUESTIONS),
                }
                for v in vendors
            ]
        }
        result = VendorQuestionsOutput.model_validate(payload)
        return {item.category: item.questions for item in result.vendors}

    async def answer_question(self, question: str, plan: Plan, facts: Mapping[str, object]) -> str:
        """Phrase the engine's facts: ask verdicts if any, else money, time and what's next."""
        asks = facts.get("asks")
        if isinstance(asks, list) and asks:
            parts = [
                f"{a['ask']}: typically about {a['typical_cost_inr']} INR, {a['verdict']}"
                for a in asks
            ]
        else:
            parts = [
                f"{label} {facts[key]}"
                for key, label in MOCK_ANSWER_FACTS.items()
                if facts.get(key) not in (None, [], "")
            ]
        answer = f"(Demo mode) {'; '.join(parts) or 'No plan facts are available'}."
        return AnswerOutput.model_validate({"answer": answer[:1500]}).answer


def _extract(text: str, today: date) -> ContextDraft:
    # Ordinals inside dates ("8th Nov") are not ages.
    age = _ORDINAL_AGE.search(_DAY_MONTH.sub(" ", _MONTH_DAY.sub(" ", text)))
    event_type = next((t for t, words in EVENT_KEYWORDS.items() if _has_any(text, words)), None)
    if event_type is None and age:
        event_type = "birthday"
    formality = next((f for f, words in FORMALITY_KEYWORDS.items() if _has_any(text, words)), None)
    budget = _BUDGET.search(text)
    guests = _GUESTS.search(text)
    return ContextDraft(
        event_type=event_type,
        event_date=_event_date(text, today),
        guest_count=int(guests.group(1).replace(",", "")) if guests else None,
        total_budget=_amount(budget) if budget else None,
        currency="INR" if budget else None,
        location_tier="metro" if _has_any(text, METROS) else None,
        formality=formality,
        honoree_age=int(age.group(1)) if age and event_type == "birthday" else None,
        dietary=_matching(text, DIETARY_PATTERNS),
        accessibility_needs=_matching(text, ACCESSIBILITY_PATTERNS),
    )


def _has_any(text: str, words: Sequence[str]) -> bool:
    return any(re.search(rf"\b{re.escape(word)}\b", text) for word in words)


def _matching(text: str, patterns: Mapping[str, str]) -> list[str]:
    return [label for label, pattern in patterns.items() if re.search(pattern, text)]


def _amount(match: re.Match[str]) -> float:
    unit = (match.group(2) or "").rstrip("s")
    return float(match.group(1).replace(",", "")) * UNIT_MULTIPLIER.get(unit, 1)


def _event_date(text: str, today: date) -> date | None:
    """A relative ("in 6 weeks") or absolute ("8 Nov", "Nov 8th", "2026-11-08") event date."""
    if runway := _RUNWAY.search(text):
        return today + timedelta(days=int(runway.group(1)) * DAYS_PER_UNIT[runway.group(2)])
    if iso := _ISO_DATE.search(text):
        return _calendar_date(int(iso.group(3)), int(iso.group(2)), int(iso.group(1)), today)
    for pattern, day_group, month_group in ((_DAY_MONTH, 1, 2), (_MONTH_DAY, 2, 1)):
        if match := pattern.search(text):
            year = int(match.group(3)) if match.group(3) else None
            month = MONTHS.index(match.group(month_group)) + 1
            return _calendar_date(int(match.group(day_group)), month, year, today)
    return None


def _calendar_date(day: int, month: int, year: int | None, today: date) -> date | None:
    """That date in `year`, or without a year its next occurrence from today; None if invalid."""
    try:
        if year is not None:
            return date(year, month, day)
        this_year = date(today.year, month, day)
        return this_year if this_year >= today else date(today.year + 1, month, day)
    except ValueError:
        return None
