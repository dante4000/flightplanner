# Engine v2 — Multi-Currency Segment Solver

**Date:** 2026-07-26
**Status:** Approved by user (conversation), pending spec review
**Phase:** 1 of 3 (engine → UI + booking links → alerts)

## Why

v1 answers one hardcoded question (LAX ⇄ Korea ⇄ Japan, Aeroplan only, exactly
2 travelers) and answers it with three known defects:

1. **Aeroplan-only.** Live data shows 17 mileage programs with award space on
   these routes — 746 rows, of which Aeroplan is 71. Roughly ten of the
   seventeen are Amex MR or Chase UR transfer partners, so v1 ignores most of
   the user's actual purchasing power.
2. **Silent ground travel.** v1 treats Japan gateways as interchangeable, so it
   emits itineraries that land at NRT and depart from KIX without pricing the
   Tokyo→Osaka transfer (~$100–200/person). Rankings are wrong by that amount.
3. **Duplicate strategies.** v1's per-person shape enumeration produced 14
   near-identical top results, patched after the fact with a shape-signature
   dedup rather than fixed at the root.

v2 replaces the bespoke trip enumeration with a general segment solver.

## Scope of this phase

**In:** trip model, program/currency registry, transfer bonuses, product
builders, solver, ranking — delivered as a tested library plus a CLI that runs
it against the live cache.

**Out (later phases):** the rebuilt dashboard, best-effort booking deep links
with clipboard copy, and macOS-notification alerts. The v1 server and dashboard
keep running unmodified throughout this phase, so a working tool always exists.

## Locked decisions

- **Any trip:** ordered city stops, any count ≥ 2, any date windows.
- **Party:** N travelers (1–10) on one shared itinerary. v1's per-traveler
  return deadlines are **dropped**; a split-return trip is now two searches.
- **Currencies:** Amex MR (flexible), Chase UR (flexible), Aeroplan (fixed
  balance, default 100,000). All balances user-editable.
- **Programs:** all 17 that seats.aero returns are shown; those with no funding
  route from the user's balances are marked `fundable: false` rather than
  hidden.
- **Bonuses:** scraped from a public tracker, cached, with a manual override
  table that always wins.
- **Cabins:** economy and business.

## Trip model (`trip.py`)

```python
@dataclass(frozen=True)
class Stop:
    city: str                 # "Seoul" — display + identity
    airports: tuple[str, ...] # ("ICN", "GMP")
    min_nights: int = 0       # intermediate stops only (see below)
    max_nights: int = 0       # 0 = unbounded

@dataclass(frozen=True)
class Trip:
    stops: tuple[Stop, ...]        # ordered, len >= 2
    depart_start: str              # ISO; window for leaving stops[0]
    depart_end: str
    arrive_by: str                 # ISO; deadline to reach stops[-1]
    party_size: int = 1
    cabins: tuple[str, ...] = ("Y", "J")
```

`min_nights`/`max_nights` are meaningful **only for intermediate stops**
(index `1 .. len(stops)-2`) — the time you spend there between arriving and
departing again. They are ignored on `stops[0]` (you leave it on `d[0]`,
governed by the depart window) and on `stops[-1]` (you arrive and the trip
ends, governed by `arrive_by`). Validation rejects a `Trip` with fewer than 2
stops, `party_size` outside 1–10, `depart_start > depart_end`, or
`max_nights` non-zero and less than `min_nights`.

Segment `i` is `stops[i] → stops[i+1]`, for `i` in `0..len(stops)-2`. Because a
segment's origin is the previous segment's destination **city**, an itinerary
can never land in one city and depart from another. Multi-airport cities
(Tokyo = NRT/HND) are interchangeable *within* a stop, which is correct — those
airports serve one metro area. Choosing between Tokyo and Osaka is expressed as
two separate `Trip` candidates, each self-consistent.

To be precise about what this fixes: v2 does **not** price trains or buses. It
makes the omission structurally impossible. Either the trip stays in one city
between two flights — in which case there is no inter-city cost to miss — or
the user adds the second city as its own stop, in which case the movement
becomes a real segment that gets priced (as a flight; NRT↔KIX flights exist and
are cheap). v1's failure was a third state, now unreachable: moving between
cities with nothing priced at all. A future phase may add a ground-transport
cost per segment; this phase does not.

### Date assignment

`enumerate_date_paths(trip, cfg) -> Iterator[tuple[str, ...]]` yields one
departure date per segment, subject to:

- `depart_start ≤ d[0] ≤ depart_end`
- `d[i] ≥ arrival_date(d[i-1], seg i-1) + stops[i].min_nights`
- `d[i] ≤ arrival_date(d[i-1], seg i-1) + stops[i].max_nights` when `max_nights > 0`
- `arrival_date(d[last], seg last) ≤ arrive_by`

`arrival_date(date, segment)` adds `arrival_offset_days(origin, dest)`:
**+1 for westbound trans-Pacific (Americas → Asia/Oceania), 0 otherwise** —
the date-line approximation, documented as an approximation. Where a concrete
cash itinerary is later attached, its real arrival date is displayed instead.
Region membership comes from a small airport→region table covering the
airports in play, defaulting to "unknown" (offset 0) for anything unlisted.

Date paths are generated on a configurable grid (`date_grid_step_days`,
default 1) and capped (`max_date_paths`, default 20,000) with the cap surfaced
in the result rather than silently truncating.

## Programs and currencies (`programs.py`)

```python
@dataclass(frozen=True)
class Program:
    id: str                    # seats.aero Source value, e.g. "virginatlantic"
    name: str                  # "Virgin Atlantic Flying Club"
    transfers: dict[str, float]  # currency id -> miles per 1 point (usually 1.0)
    stopover_extra_miles: int | None = None   # None = no documented rule

CURRENCIES = ("amex_mr", "chase_ur", "aeroplan_fixed")
```

A static registry covers the 17 programs seats.aero returns for these routes,
each annotated with its transfer partners. `aeroplan_fixed` transfers only to
`aeroplan` at 1.0 and is not replenishable. Programs with no entry in the
registry are still surfaced (from live data) with `transfers = {}`, which makes
them `fundable: false`.

`stopover_extra_miles` is set **only** where a published rule exists — Aeroplan
5,000. Every other program is `None` and gets no stopover product. This
prevents inventing pricing the tool cannot verify.

### Transfer bonuses (`bonuses.py`)

```python
@dataclass(frozen=True)
class Bonus:
    currency: str    # "amex_mr"
    program: str     # "virginatlantic"
    pct: float       # 0.30 = 30%
    expires: str | None
    source: str      # "scrape" | "manual"
```

`active_bonuses(cache, cfg, now) -> list[Bonus]` returns manual overrides
merged over scraped rows, manual winning on `(currency, program)` collision.
Expired rows are dropped. The scrape is cached with a 12-hour TTL and, on any
failure, degrades to manual-only with a flag in the result — never an
exception, matching how the cash client already degrades.

### Funding math

Points drawn from currency `C` to book `M` miles in program `P`:

```
ratio = P.transfers[C]                      # miles per point
bonus = active bonus pct for (C, P), else 0
points_needed = ceil(M / (ratio * (1 + bonus)))
```

A 30% MR→Virgin bonus means 100,000 MR yields 130,000 miles, so 130,000 miles
costs 100,000 MR. This is the calculation that decides whether a transfer is
worth making, so it is unit-tested directly against that example.

## Products (`products.py`)

A **product** covers one or more *consecutive* segments for the whole party.

| Product | Covers | Cash | Points |
|---|---|---|---|
| `cash_ow` | 1 segment | per-person fare × party | 0 |
| `award_ow` | 1 segment | taxes × party + booking fee × party | miles × party |
| `award_stopover` | 2 consecutive segments | both taxes × party + one fee × party | (first-segment miles + `stopover_extra_miles`) × party |

Builders take the price tables and return `Option`s carrying `program`,
`cabin`, `flags`, and per-leg award seat counts. Award seats must be
`≥ party_size`; a row reporting 0 seats is *unknown*, not zero, and is kept
with a `seats-unknown` flag (v1 behaviour, retained).

Taxes, fee conversion (CAD→USD), and the unknown-taxes defaults carry over from
v1 unchanged, including the `award-taxes-estimated` flag.

## Solver (`solver.py`)

```
solve(trip, tables, balances, bonuses, cfg) -> SolveResult
```

1. **Award subsets.** For `n` segments, enumerate subsets of segments paid with
   points: `2^n` (n ≤ 6 → ≤ 64; larger trips cap at `max_segments`, default 6,
   reported as an error rather than silently truncated).
2. **Per subset, best dates.** Dynamic program over `(segment index, date)`:
   each segment's cost depends only on its own date, and the date constraints
   chain forward, so the cheapest feasible date path is a shortest path. Cash
   segments take the cheapest cash fare; award segments take the cheapest
   award across programs and cabins **that the balances can actually fund**.

   Unfundable programs are therefore absent from *ranked bundles* — a bundle
   you cannot book is not a strategy. They remain fully present in the award
   matrix the result exposes for display, each tagged `fundable: false` with
   the reason (`no transfer partner` or `insufficient balance`), which is what
   the "show all 17, mark the unfundable" decision refers to. A program that is
   unfundable only because a balance is too small is additionally tagged with
   the shortfall, so raising a balance shows what it would unlock.
3. **Funding feasibility.** With ≤ 3 currencies and ≤ 6 awards, enumerate
   assignments of each award to a funding currency exhaustively; keep any
   assignment whose per-currency totals fit the balances. Prefer the assignment
   that leaves the largest minimum headroom, so one currency isn't drained
   needlessly.
4. **Stopover products** are considered as an alternative covering of any two
   adjacent segments, evaluated as extra candidate coverings within the same
   subset search.
5. **Rank** by total cash out-of-pocket ascending. `cpp` per bundle =
   `100 × (all-cash baseline − bundle cash) / total points across currencies`,
   `None` when no points are spent or no baseline exists. Non-positive cpp is
   retained but flagged `worse-than-cash`.

**Deduplication is structural.** One result per *strategy shape* —
`(award subset, program per award, cabin per segment)` — holding its cheapest
date path. v1's duplicate-results problem cannot recur, because date
permutations of one shape collapse in step 2 rather than being filtered later.

`SolveResult` carries the ranked bundles, the all-cash baseline, the truncation
flags (`date_paths_capped`, `segments_exceeded`), and a `notes` list explaining
anything degraded (bonus scrape failed, a segment had no priceable option).

## Data layer changes

- `AwardFare` gains `program: str`. `seats_client.fetch_awards` **drops the
  `sources=aeroplan` filter** and maps `Source` into that field. Same quota
  cost (one call per origin/destination group per window) for ten times the
  data.
- Programs are filtered at *search* time, not fetch time, so changing balances
  needs no refetch.
- `cash_client` passes `party_size` as `adults`. Google returns the total for
  the queried passenger count (already verified), so per-person is
  `total / party_size`.
- `cache.py` unchanged.

## What v1 code is retired

`strategies.py`'s composition half (`compute`, `_person_plans`,
`_person_front_options`, `_person_return_options`, `_bundle_from`,
`_shape_signature`) is superseded by `solver.py`. Its option-builder half
(`build_tables` and the four option constructors) moves to `products.py`,
generalized over `program` and `party_size`. `planner.py`'s query planning is
rewritten against `Trip` rather than the hardcoded windows; its date helpers
(`add_days`, `date_range`) move to `trip.py` unchanged.

The v1 server, dashboard, and their tests stay untouched and passing in this
phase. The swap happens in the UI phase.

## Testing

Every module above is pure except the two clients, so tests are unit tests over
fixtures with no network:

- **trip:** date-path enumeration under min/max nights and the arrival
  deadline; date-line offsets both directions; the cap behaviour; rejection of
  trips with < 2 stops.
- **programs:** funding math including the 30%-bonus case (130,000 miles costs
  100,000 MR); unknown programs are non-fundable; stopover offered only where a
  rule exists.
- **bonuses:** manual overrides beat scraped rows; expired rows dropped; scrape
  failure degrades to manual with a note.
- **products:** party-size multiplication for cash and awards; seat gating at
  `≥ party_size` with 0 meaning unknown; taxes/fee conversion parity with v1.
- **solver:** on a hand-computed fixture — subset search finds the known
  optimum; the date DP respects chaining; funding infeasibility rejects a
  bundle; balances split correctly across two currencies; shapes dedupe;
  `cpp` matches hand arithmetic; a segment with no priceable option degrades
  with a note instead of raising.
- **regression:** the v1 Korea/Japan trip expressed as two `Trip` candidates
  (Seoul→Tokyo and Seoul→Osaka) runs against the same cached fixture data v1
  used. The assertion is directional, not a tolerance band: v2's cheapest
  all-cash total must be **greater than or equal to** v1's $2,315, because v1's
  winner moved between Tokyo and Osaka without any segment covering it, while
  v2 must either stay in one city (fewer options, so no cheaper) or price that
  movement. The test records both figures so the gap is visible rather than
  asserted away.

TDD throughout, per the project's existing practice.

## Risks

- **Bonus scrape rots.** Same class of dependency as the Google Flights parser.
  Mitigated by the manual table and by degrading rather than failing.
- **Program registry drifts** as airlines change partners. It is a small static
  table with a test asserting every seats.aero `Source` seen in the fixtures
  has a registry entry or is explicitly marked unknown.
- **Award pricing is per-program and dynamic.** The tool ranks and recommends;
  it never claims a booking will price identically. Stopover pricing stays
  flagged as an estimate to verify with the program directly.
- **Combinatorics** grow with stops × dates. Bounded by `max_segments` and
  `max_date_paths`, both surfaced in results.
