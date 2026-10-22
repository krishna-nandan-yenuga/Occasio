# Assumptions

Occasio's numbers come from a deterministic rules engine (`app/core/`). This page lists every
assumption that engine makes, so the output can be judged and tuned. **All percentages,
thresholds and costs are illustrative planning defaults for the Indian market, not market
research or vendor quotes.** They live in `app/core/profiles.py` and `app/services/assistant.py`
and can be changed without touching any logic.

## Money and currency

- The default currency is **INR**. Any three-letter ISO code is accepted.
- Amounts use `Decimal` with two decimal places. Each budget line is rounded down to the paisa
  and the remainder goes to the contingency line, so lines always add up exactly to the total.
- Per-guest benchmarks and typical costs are in INR. For any other currency the per-guest check
  is skipped (advisory `SPEND_NOT_BENCHMARKED`) and the assistant does not cost common asks,
  because the INR figures would be meaningless there. Exchange rates are not modelled.
- There is no tracking of actual spending. "Remaining budget" in the assistant means the total
  minus the budget lines of vendors marked **booked**.

## Intake

- A plan needs the event type, date, guest count, total budget, location and style; anything the
  description does not state is listed as missing and highlighted in the form.
- For community events, intake also asks whether the event is ticketed when the description does
  not say, because ticketing raises contingency to 10% and adds to permits & insurance. The form
  offers Yes / No rather than a checkbox, so "not answered" is distinct from "no".

## Budget split

Each event type has a table of percentages per formality; every table sums to exactly 100.

| Wedding | casual | semi | formal |
|---|---|---|---|
| Venue | 22 | 23 | 25 |
| Catering | 30 | 28 | 27 |
| Decor | 10 | 12 | 13 |
| Photography | 8 | 9 | 10 |
| Attire | 8 | 8 | 7 |
| Entertainment | 5 | 5 | 5 |
| Bar | 4 | 4 | 3 |
| Invitations | 2 | 2 | 2 |
| Gifts & favours | 3 | 2 | 1 |
| Transport | 3 | 2 | 2 |
| Contingency | 5 | 5 | 5 |

| Birthday | casual | semi | formal |
|---|---|---|---|
| Venue | 15 | 20 | 24 |
| Catering | 35 | 31 | 30 |
| Cake | 8 | 7 | 6 |
| Decor | 10 | 11 | 12 |
| Entertainment | 12 | 11 | 9 |
| Bar | 8 | 7 | 7 |
| Photography | 4 | 5 | 5 |
| Gifts & favours | 3 | 3 | 2 |
| Contingency | 5 | 5 | 5 |

| Community event | casual | semi | formal |
|---|---|---|---|
| Venue | 15 | 18 | 20 |
| Catering | 25 | 24 | 24 |
| Sound & stage | 12 | 13 | 14 |
| Permits & insurance | 8 | 8 | 7 |
| Accessibility | 7 | 7 | 6 |
| Marketing & sponsorship | 10 | 9 | 8 |
| Volunteers | 8 | 6 | 5 |
| Decor | 7 | 7 | 8 |
| Contingency | 8 | 8 | 8 |

Adjustments applied on top, after which the non-contingency lines are rescaled to fill the rest:

- **Location.** The venue share is multiplied by a price index: metro 1.25, city 1.00, town 0.80.
  "Metro" means Mumbai, Delhi NCR, Bengaluru, Chennai, Kolkata, Hyderabad, Pune and Ahmedabad.
- **Guest count.** The tables assume a typical headcount (wedding 200, birthday 40, community
  150). Catering's share moves by 20% of the relative difference, limited to 0.8–1.3×.
- **Kids' parties.** A birthday is a kids' party if `honoree_age` is under 18 or `kids_party` is
  set. The bar share drops to zero and moves to entertainment.
- **Ticketed community events.** Contingency rises to 10% and permits & insurance gains 2 points.
- **Contingency** always stays between 5% and 10%. When the user locks lines, rebalancing keeps
  an unlocked contingency at 5% or more while money remains to cover it.

## Is the budget realistic?

The budget per guest is compared with a band for the event type and formality, scaled by the
location index. Below the band is `BUDGET_TIGHT` (critical below half of it); above is
`BUDGET_GENEROUS`.

| INR per guest (city) | casual | semi | formal |
|---|---|---|---|
| Wedding | 1,500 – 8,000 | 2,500 – 12,000 | 4,000 – 20,000 |
| Birthday | 500 – 3,000 | 800 – 5,000 | 1,500 – 8,000 |
| Community | 150 – 1,500 | 300 – 2,500 | 800 – 5,000 |

When the overall budget is fine, venue and catering are still checked individually: each has a
floor of "tight per-guest amount × the share the engine would give it". A line more than 10%
below its floor (usually after a manual edit) raises `VENUE_TOO_SMALL` or `CATERING_TIGHT`.
Community events must fund permits & insurance and accessibility (`*_UNFUNDED`, critical).

## Timeline

- Milestones are templates "N days before the event". The full plan needs 300 days for a
  wedding, 42 for a birthday and 120 for a community event (the earliest milestone's offset).
- Wedding rules from the brief: venue booked 240 days out (at least six months), invitations
  63 days out (within the 8–10 week window).
- With less time than that, every offset shrinks by the same factor, but never below a quarter
  of its length. Work that cannot fit is due before today and marked **late**
  (`MISSED_MILESTONE`). Compression to under half is a warning; milder compression is a note.
- Community events always include permit and accessibility milestones. Any event with
  accessibility needs gets "Confirm accessibility arrangements" 21 days out.
- "Today" is the server's local date. Events are single-day; times of day are not modelled.

## Vendors

- Vendor categories and suggested vendor types are generic per event type. **There is no live
  vendor data**: no names, prices, ratings or availability.
- Each vendor's "book by" date is the due date of the first milestone in its category.
- Every vendor gets three baseline questions (availability, what the quote includes, payment
  and cancellation). The venue also gets guest-count and accessibility questions, and catering
  gets dietary ones. The LLM may add more.

## Assistant: typical costs of common asks

Illustrative city-tier costs in INR, scaled by the location index:

| Ask | Budget line | Typical cost |
|---|---|---|
| Live band | Entertainment | 75,000 |
| DJ | Entertainment | 25,000 |
| Magician | Entertainment | 10,000 |
| Photographer | Photography | 30,000 |
| Videographer | Photography | 40,000 |
| Open bar | Bar | 900 per guest |
| Sound system | Sound & stage, else Entertainment | 30,000 |
| Stage | Sound & stage, else Decor | 40,000 |
| N more guests | Catering | current catering per guest × N |

The verdict is "fits" if the line covers it, "contingency covers it" if line plus contingency
does, otherwise "over budget by X". Alcohol at a kids' party is always "not suitable".

## The LLM

- The LLM never produces a number used by the plan. It extracts details from text, writes ideas
  and vendor questions, and phrases answers from facts the engine computed.
- Without an API key, the **mock provider** stands in. Its ideas and questions are canned per
  event type. Its extraction uses keyword and pattern rules: relative dates ("in 6 weeks"),
  absolute ones ("8 Nov", "Nov 8th", "2026-11-08"; without a year, the next occurrence), amounts
  in lakh, crore or k, guest counts, metro city names, common dietary needs ("veg-only", "Jain")
  and accessibility needs ("wheelchair access", "step-free"). Anything else is left for the form.
