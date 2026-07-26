# Award Trip Planner — Design

**Date:** 2026-07-26
**Status:** Approved by user (conversation), pending spec review

## Purpose

A local web dashboard that answers: *what is the cheapest way to book a 2-person
LAX → Korea + Japan → LAX trip, given 100,000 Aeroplan points to spend where they
add the most value?* It compares every sensible booking shape — all-cash round
trips, open jaws, one-ways, Aeroplan awards, the Aeroplan stopover award, and
mixed cash/points bundles — using live award availability (seats.aero) and live
cash prices (Google Flights), and ranks complete itineraries by total
out-of-pocket cash.

## Locked requirements

- **Travelers:** 2. Traveler A must be back in LAX by **Oct 14, 2026**;
  traveler B by **Oct 31, 2026**. They fly the outbound and the intra-Asia hop
  together; returns are separate.
- **Outbound window:** LAX departure **Sep 25 – Oct 7, 2026**.
- **Cities:** Seoul (ICN/GMP) on the Korea side; Tokyo (NRT/HND) and Osaka
  (KIX) on the Japan side. **Either country order** is allowed.
- **Points:** one shared pool of **100,000 Aeroplan** points (budget adjustable
  in UI). No other transferable currencies in scope.
- **Cabins:** economy and business, shown side by side.
- **Cash prices:** live (Google Flights via `fast-flights`), not estimates.
- **Delivery:** local single-page web dashboard, refresh on demand.

## Trip model

Shared legs, then per-person returns:

| Leg | Who | Route | Date constraint |
|-----|-----|-------|-----------------|
| T1 | both | LAX → City1 | Sep 25 – Oct 7 |
| H  | both | City1 → City2 | after T1 + minNights(City1); before return − minNights(City2) |
| Hx | per person, optional | City2 → City1 | hop-back, only in bundles whose transpacific return departs City1 |
| RA | A | City2 (or City1 after Hx) → LAX | arrive ≤ Oct 14 |
| RB | B | City2 (or City1 after Hx) → LAX | arrive ≤ Oct 31 |

City1/City2 = (Seoul, Tokyo-or-Osaka) in either order. `minNights` defaults to
3 per country, configurable. Both travelers take the same T1 and H flights
(shared legs). Each person's return is either **direct** (depart City2) or
**via hop-back** (Hx then transpacific from City1) — the hop-back exists so
the user's headline shape "RT LAX↔Korea + RT Korea↔Japan" is expressible.
Hx and RA/RB are per-person and may differ in date; when both travelers'
hop-backs coincide they share seats naturally.

Out of scope (YAGNI): traveler B re-hopping countries after A leaves, non-LAX
US airports, positioning flights, hotels.

## Booking products

Legs are covered by **products**; a product may span multiple legs, which is
exactly why naive per-leg pricing is wrong:

1. **Cash one-way** — any single leg.
2. **Cash round-trip** — LAX↔City1 (covers T1 + that person's transpacific
   return, which then departs City1 and requires their Hx hop-back), or
   City1↔City2 (covers H + Hx for one person). Together these express the
   classic "RT to Korea + RT Korea↔Japan" shape.
3. ~~Cash open-jaw (multi-city)~~ — **dropped after live verification
   (2026-07-26):** Google Flights' multi-city page returns no priced
   itineraries in its initial payload (it requires interactive leg-by-leg
   selection), so single-ticket open-jaw quotes are unobtainable via
   `fast-flights`. Direct-return shapes are priced as **sum of one-ways**
   (bookable as separate tickets); the UI notes that a true open-jaw ticket
   on the top bundle's carrier may price better and is worth a manual check.
4. **Aeroplan one-way award** — any leg; Aeroplan prices one-ways at half a
   round trip, so awards are composed per-leg with no RT coupling.
5. **Aeroplan stopover award** — LAX→City1 (stopover ≤45 days) →City2 booked
   as **one award for +5,000 points** over the LAX→City1 cost. Covers T1+H in
   one product. Priced as (T1 award cost + 5,000); requires aeroplan award
   space on both segments on the chosen dates. Flagged in UI as "estimate —
   verify at aeroplan.com" because seats.aero cannot price stopovers and the
   distance-band edge case (total flown distance crossing a band boundary) is
   not computed.

Aeroplan rules encoded: no fuel surcharges on partner awards; ~CAD 39 partner
booking fee per ticket plus government taxes (taken from seats.aero trip data
where present, else a flat estimate clearly marked); award mileage costs always
come from live seats.aero data, never a hardcoded chart; shared award legs
require **RemainingSeats ≥ 2** (seats unknown → allowed but flagged).

## Strategy engine (`strategies.py`)

Pure functions: `(award_table, cash_table, config) → ranked bundles`. No I/O.

1. Build per-leg, per-date, per-cabin price tables from the two sources.
2. Enumerate bundle templates per person (which product covers which legs),
   joining on shared-leg dates. Dates are pruned to best-per-product-per-window
   with top-k retained to keep combinatorics small (≤ a few thousand
   candidates).
3. Filter: date-order constraints, return deadlines, min-nights, award seat
   counts, total points ≤ budget.
4. Rank by **total out-of-pocket cash** (fares + award taxes/fees). Each
   bundle reports: points used, cash total, per-person per-leg breakdown
   (date, airline, cabin, product), and **effective cents-per-point** =
   (all-cash baseline − bundle cash) / points used.
5. Output top N (default 15) plus the all-cash baseline, for each cabin mix
   requested (all-economy, all-business, and best-value mixed).

## Data sources

### seats.aero Pro (awards)

- Auth: `Partner-Authorization` header; key in `.env` (gitignored), never
  committed. Quota 1,000 calls/day — tracked and displayed in UI.
- `GET /partnerapi/search?origin_airport=…&destination_airport=…&start_date=…&end_date=…`
  (cached search, comma-separated multi-airport, cursor pagination) filtered to
  `source=aeroplan`, for the ~6 pair-groups: LAX↔{ICN}, LAX↔{NRT,HND,KIX},
  {ICN}↔{NRT,HND,KIX} across the relevant windows. ~10–20 calls per refresh.
- `GET /partnerapi/trips/{id}` on demand (user expands a bundle) for
  flight-level detail: flight numbers, times, airline, seat counts, taxes.
- Exact response field names verified at implementation time against live
  responses; client isolates raw-JSON → typed model mapping in one place.

### Google Flights via `fast-flights` (cash)

- Free, no key; queries Google Flights' internal protobuf API. **Risk: can
  break without notice.** Live-verified 2026-07-26: pinned `fast-flights==3.0.2`
  whose stock parser crashes on rows without prices (e.g. ICN→NRT) — we vendor
  a row-tolerant copy of `parse_js` (skips unpriced rows, best-effort metadata,
  reads both the "best" and "other" itinerary blocks) and monkeypatch it in.
  Any remaining breakage degrades to manual price entry (UI input per leg)
  instead of taking down the dashboard.
- Query shapes: one-ways per leg; round-trips where the RT templates apply
  (verified parsing). Prices returned are **totals for the queried passenger
  count** (verified: adults=2 = 2 × adults=1). Adults=2 on shared legs, 1 on
  per-person returns.
- Cost control: coarse grid first (every 2–3 days across each window), refine
  ±1 day around minima; hard cap ~60 queries per refresh; SQLite cache with
  6-hour TTL so repeat refreshes are mostly free.

## Architecture

```
~/dev/award-trip-planner/          (own git repo, local only)
  pyproject.toml                   (uv-managed, Python 3.12)
  .env                             (SEATS_AERO_KEY — gitignored)
  config.json                      (windows, cities, budget, minNights — UI-editable)
  cache.sqlite                     (award + cash responses, timestamps)
  src/award_trip_planner/
    seats_client.py                seats.aero wrapper + quota tracking
    cash_client.py                 fast-flights wrapper + grid/refine + cache
    strategies.py                  pure strategy engine
    server.py                      FastAPI: /api/config, /api/refresh, /api/results
  static/index.html                single-page UI (vanilla JS)
  tests/                           engine unit tests on fixtures; mocked client tests
```

`GET /api/refresh` kicks a background fetch task and streams progress;
`/api/results` returns the computed bundles plus raw leg matrices; config
round-trips through `/api/config`. One command to run
(`uv run award-trip-planner`), opens the browser to localhost.

## UI (single page)

- **Config bar:** date windows, point budget, cabin filter, city toggles,
  min-nights, refresh button with progress + quota/staleness indicators.
- **Ranked strategy cards:** top bundles ranked by cash out-of-pocket; card
  face shows total cash, points used, cpp, one-line shape summary ("Stopover
  award LAX→ICN→NRT + cash returns"); expands to per-person per-leg table.
- **Availability heatmap:** calendar per city pair colored by award state
  (business+economy / economy only / none) so good dates pop visually.
- **Cash grid:** cheapest cash price per pair per date, with manual override
  inputs (doubles as the fast-flights failure fallback).
- Economy vs business shown side by side per bundle. The dataviz skill governs
  heatmap/chart styling at implementation time.

## Error handling

- Partial data never blocks ranking; affected bundles carry a "missing data"
  flag instead of disappearing.
- seats.aero non-200s surface in the UI with the quota count; fast-flights
  failures flip the affected pairs to manual-entry mode.
- Every displayed price shows its fetched-at timestamp; stale (>6h cash,
  >24h award) values are visually muted.

## Testing

- `strategies.py` is deterministic → unit tests against fixture price tables
  covering: template enumeration, shared-date joining, deadline/min-nights
  filtering, seat-count gating, points-budget knapsack, cpp math, stopover
  pricing (+5k, both-segment availability).
- Clients tested against recorded/mock responses; no live-API calls in tests.
- Built TDD per superpowers process.

## Risks / notes

- **fast-flights breakage** is the main operational risk; mitigated by manual
  entry, but if it's broken at implementation time the fallback becomes the
  primary path and we note it to the user.
- **Asiana's Star Alliance exit** (Korean Air merger) may thin Aeroplan space
  to Seoul in late 2026 — no code impact (data-driven), just an expectation to
  set.
- Stopover awards may require specific routings; the tool recommends, the user
  books/verifies on aeroplan.com.
- seats.aero cached data can lag reality; the tool is a decision aid, not a
  booking guarantee.
