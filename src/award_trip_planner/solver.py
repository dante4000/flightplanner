from __future__ import annotations

import datetime as dt
import time
from dataclasses import asdict, dataclass, field
from itertools import product as _iproduct

from .bonuses import bonus_lookup_from
from .config import Config
from .products import (
    SegCandidate,
    award_candidates,
    cash_candidates,
    stopover_candidates,
)
from .programs import CURRENCIES, fundability, funding_options
from .trip import Trip, arrival_date, enumerate_date_paths

MAX_SEGMENTS = 6


@dataclass
class Bundle2:
    shape: list
    total_cash_usd: float
    points: dict
    cpp: float | None
    segments: list
    flags: list
    dates: tuple

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SolveResult:
    bundles: list = field(default_factory=list)
    baseline_cash: float | None = None
    notes: list = field(default_factory=list)
    date_paths_capped: bool = False
    award_matrix: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _candidates_for(trip, tables, seg_idx, date, cfg, now):
    a, b = trip.segments()[seg_idx]
    return (
        cash_candidates(tables, a, b, date, trip.cabins, trip.party_size, cfg, now)
        + award_candidates(tables, a, b, date, trip.cabins, trip.party_size, cfg)
    )


def _assign_funding(awards: list[SegCandidate], balances, lookup):
    """Cheapest feasible currency assignment; None when infeasible.

    Exhaustive over currencies-per-award: <=3 currencies, <=6 awards.
    Primary objective is minimizing total points spent -- this is what makes a
    transfer bonus (e.g. 25% MR->Aeroplan) actually get used instead of being
    defeated by a same-size draw from an un-bonused currency. Ties (equal
    spend) are broken by the assignment leaving the largest minimum headroom,
    so a single currency is not drained needlessly.
    """
    per_award = []
    for c in awards:
        opts = funding_options(c.program, c.miles, balances, lookup)
        if not opts:
            return None
        per_award.append(opts)
    best = None
    for combo in _iproduct(*per_award):
        totals: dict[str, int] = {}
        for currency, needed in combo:
            totals[currency] = totals.get(currency, 0) + needed
        if any(totals[c] > balances.get(c, 0) for c in totals):
            continue
        headroom = min(balances.get(c, 0) - totals.get(c, 0) for c in CURRENCIES)
        spent = sum(totals.values())
        key = (spent, -headroom)
        if best is None or key < best[0]:
            best = (key, totals)
    return None if best is None else best[1]


def _bundle_rank_key(b: dict):
    """Rank by redemption value (cents-per-point), not raw cash spent.

    Sorting by `total_cash_usd` alone would put a bundle that drains many more
    points for a *worse* cpp ahead of a bundle that spends fewer points at a
    better cpp -- e.g. redeeming points on every eligible leg is not "best"
    just because it happens to minimize cash if the marginal leg is a weak
    redemption. Three tiers, each ascending (so `sort()` puts the best first):
      0. genuine positive-value redemptions, best cpp first;
      1. the cash-only baseline (cpp is None), as a reference point;
      2. "worse-than-cash" redemptions (cpp <= 0), which should never rank
         above simply paying cash, so they sort last regardless of cpp.
    """
    cpp = b["cpp"]
    if cpp is None:
        return (1, b["total_cash_usd"])
    if cpp <= 0:
        return (2, b["total_cash_usd"])
    return (0, -cpp)


def _routing_str(routing: tuple[tuple[str, str], ...]) -> str:
    """`(("LAX","ICN"),("ICN","NRT"))` -> `"LAX-ICN-NRT"`."""
    if not routing:
        return ""
    return "-".join([routing[0][0]] + [d for _, d in routing])


def _shape_labels(shape: tuple) -> list[str]:
    """JSON-safe, hashable rendering of a shape: one string per slot.

    The routing is part of the label (not just kind/program/cabin) so that two
    options that differ only by which airport pair they fly through -- e.g. a
    multi-airport city like Seoul (ICN vs GMP) -- still render as distinguishable
    shapes instead of colliding into one.
    """
    out = []
    for slot_kind, i, (kind, program, cabin, routing) in shape:
        out.append(f"{slot_kind}:{i}:{kind}/{program or 'cash'}/{cabin}/{_routing_str(routing)}")
    return out


def solve(trip: Trip, tables, balances: dict, bonuses: list, cfg: Config,
          now: float | None = None, top_n: int = 15, sort: str = "cash") -> SolveResult:
    """Rank trip strategies.

    `sort="cash"` (default) orders by total cash out-of-pocket ascending: the
    "what do I actually pay" view. `sort="value"` orders by redemption quality
    (cents per point) instead, which surfaces the most efficient use of a
    limited points balance even when a cash-cheaper option exists that burns
    far more points. Both are legitimate questions; they are not the same one.
    """
    now = time.time() if now is None else now
    res = SolveResult()
    trip.validate()
    segs = trip.segments()
    if len(segs) > MAX_SEGMENTS:
        res.notes.append(f"trip has {len(segs)} segments; the solver supports {MAX_SEGMENTS}")
        return res

    lookup = bonus_lookup_from(bonuses)
    dates_per_seg, capped = _segment_date_options(trip, cfg)
    res.date_paths_capped = capped
    if not all(dates_per_seg):
        res.notes.append(
            "no feasible dates for at least one segment (check stopover night "
            "bounds and the arrival deadline)")
        return res

    # Candidates per (segment, date), pruned to the best few per segment-option.
    # `opts[i][(kind, program, cabin, routing)] -> {date: SegCandidate}`
    opts, missing_seg, pruned_seg = _segment_options(trip, tables, dates_per_seg, cfg, now)
    if any(not o for o in opts):
        res.notes.append("no option priced for at least one segment")
        return res
    if missing_seg:
        res.notes.append("some dates had no priced option and were skipped")
    if pruned_seg:
        res.notes.append(
            f"a segment had more than {cfg.max_options_per_segment} distinct "
            "option-kinds; the priciest were pruned from consideration")

    spans = _span_options(trip, tables, dates_per_seg, cfg)
    shapes = _enumerate_shapes(opts, spans, len(segs), cfg.max_shapes)
    if len(shapes) >= cfg.max_shapes:
        res.notes.append(f"shape enumeration hit the {cfg.max_shapes} cap; "
                         "results may omit exotic combinations")

    by_shape: dict[tuple, tuple] = {}
    baseline: float | None = None
    dropped_notes: list[str] = []
    seen_dropped: set[str] = set()

    for shape in shapes:
        best = _best_date_path(trip, shape, opts, spans)
        if best is None:
            continue
        cash_total, chosen, path = best
        awards = [c for c in chosen if c.kind == "award"]
        if not awards:
            baseline = cash_total if baseline is None else min(baseline, cash_total)
            by_shape[shape] = ((cash_total, 0), cash_total,
                               {c: 0 for c in CURRENCIES}, chosen, path, False)
            continue

        points_constrained = False
        assigned = _assign_funding(awards, balances, lookup)
        if assigned is None:
            # The cash-cheapest date path for this shape can't be funded from
            # the available balances. Before giving up on the whole shape,
            # retry with the DP minimizing miles instead of cash: a date with
            # a higher cash cost can still require far fewer points (e.g. an
            # award chart that jumps 50k->90k between two dates), and that
            # date may be the one the user can actually afford.
            alt = _best_date_path(trip, shape, opts, spans, objective="miles")
            if alt is not None:
                alt_cash_total, alt_chosen, alt_path = alt
                alt_awards = [c for c in alt_chosen if c.kind == "award"]
                alt_assigned = _assign_funding(alt_awards, balances, lookup)
                if alt_assigned is not None:
                    cash_total, chosen, path = alt_cash_total, alt_chosen, alt_path
                    assigned = alt_assigned
                    points_constrained = True
        if assigned is None:
            labels = _shape_labels(shape)
            note = f"{'+'.join(labels)} dropped: balances cannot fund it"
            if note not in seen_dropped and len(dropped_notes) < 10:
                seen_dropped.add(note)
                dropped_notes.append(note)
            continue

        totals = {c: 0 for c in CURRENCIES}
        totals.update(assigned)
        by_shape[shape] = ((cash_total, sum(totals.values())),
                           cash_total, totals, chosen, path, points_constrained)

    res.baseline_cash = baseline
    res.notes.extend(dropped_notes)
    bundles = []
    for shape, (_key, cash_total, totals, chosen, path, points_constrained) in by_shape.items():
        pts = sum(totals.values())
        cpp = None
        if pts and baseline is not None:
            cpp = round(100 * (baseline - cash_total) / pts, 2)
        flags = sorted({f for c in chosen for f in c.flags})
        if points_constrained:
            flags.append("points-constrained-dates")
        if cpp is not None and cpp <= 0:
            flags.append("worse-than-cash")
        bundles.append(Bundle2(
            shape=_shape_labels(shape),
            total_cash_usd=cash_total,
            points={k: v for k, v in totals.items()},
            cpp=cpp,
            segments=[{
                "kind": c.kind, "program": c.program, "cabin": c.cabin,
                "airlines": c.airlines, "cash_usd": c.cash_usd, "miles": c.miles,
                "seats": c.seats, "spans": c.spans, "flags": list(c.flags),
                "legs": [asdict(l) for l in c.legs],
            } for c in chosen],
            flags=flags,
            dates=path,
        ).to_dict())

    if sort == "value":
        bundles.sort(key=_bundle_rank_key)
    else:
        bundles.sort(key=lambda b: b["total_cash_usd"])
    res.bundles = bundles[:top_n]
    res.award_matrix = _award_matrix(tables, balances, lookup, trip.party_size)
    return res


def _segment_date_options(trip: Trip, cfg: Config):
    """Feasible departure dates per segment, independent of which is chosen.

    A date is kept for segment i if some feasible full path uses it; the exact
    chaining is enforced again in the DP, so this is a cheap pre-filter.
    """
    paths, capped = enumerate_date_paths(trip, step=cfg.date_grid_step_days,
                                         cap=cfg.max_date_paths)
    n = len(trip.segments())
    per_seg: list[list[str]] = [[] for _ in range(n)]
    seen = [set() for _ in range(n)]
    for p in paths:
        for i, d in enumerate(p):
            if d not in seen[i]:
                seen[i].add(d)
                per_seg[i].append(d)
    return [sorted(s) for s in per_seg], capped


def _prune_key(c: SegCandidate) -> float:
    """Scalarize cash + points so pruning keeps genuinely different options."""
    return c.cash_usd + 0.0115 * c.miles


def _routing_of(c: SegCandidate) -> tuple[tuple[str, str], ...]:
    return tuple((l.origin, l.dest) for l in c.legs)


def _segment_options(trip, tables, dates_per_seg, cfg, now):
    """opts[i][(kind, program, cabin, routing)] = {date: SegCandidate}, pruned per segment.

    The key includes the routing (the candidate's leg origin/dest pairs) so
    that distinct airport pairs -- e.g. LAX->ICN vs LAX->GMP for a multi-airport
    city -- are kept as distinct options instead of one overwriting the other
    via `dict.setdefault(...)[d] = c` last-write-wins.

    Returns (opts, missing, pruned): `missing` if any date had no priced option
    at all; `pruned` if any segment had more distinct option-kinds than the cap
    and some were discarded -- both surfaced as notes by the caller so a tight
    cap never silently narrows the search.
    """
    n = len(trip.segments())
    opts: list[dict] = []
    missing = False
    pruned = False
    for i in range(n):
        by_key: dict[tuple, dict] = {}
        for d in dates_per_seg[i]:
            cands = _candidates_for(trip, tables, i, d, cfg, now)
            if not cands:
                missing = True
                continue
            for c in cands:
                key = (c.kind, c.program, c.cabin, _routing_of(c))
                by_key.setdefault(key, {})[d] = c
        if len(by_key) > cfg.max_options_per_segment:
            pruned = True
        # keep the cheapest few option-kinds per segment so shape count stays sane
        ranked = sorted(
            by_key.items(),
            key=lambda kv: min(_prune_key(c) for c in kv[1].values()),
        )[: cfg.max_options_per_segment]
        opts.append(dict(ranked))
    return opts, missing, pruned


def _span_options(trip, tables, dates_per_seg, cfg):
    """Stopover products covering segments (i, i+1).

    `spans[i][(kind, program, cabin, routing)] = {(date_i, date_i1): SegCandidate}`
    — keyed by the date *pair* because one award covers both flights, and by
    the routing (both legs' origin/dest pairs) for the same reason as
    `_segment_options`: a multi-airport city must not let one airport pair's
    stopover candidate silently overwrite another's.
    """
    segs = trip.segments()
    spans: list[dict] = [dict() for _ in range(len(segs))]
    for i in range(len(segs) - 1):
        a, b = segs[i]
        _, c2 = segs[i + 1]
        for d0 in dates_per_seg[i]:
            for d1 in dates_per_seg[i + 1]:
                if d1 <= d0:
                    continue
                for cand in stopover_candidates(tables, a, b, c2, d0, d1,
                                                trip.cabins, trip.party_size, cfg):
                    key = (cand.kind, cand.program, cand.cabin, _routing_of(cand))
                    spans[i].setdefault(key, {})[(d0, d1)] = cand
    return spans


def _enumerate_shapes(opts, spans, n: int, cap: int) -> list[tuple]:
    """Every covering of segments 0..n-1 by 1-segment and 2-segment products.

    A shape is a tuple of slots; each slot is ("one", i, key) or ("span", i, key)
    where a span consumes segments i and i+1.
    """
    shapes: list[tuple] = []

    def walk(i: int, acc: tuple) -> None:
        if len(shapes) >= cap:
            return
        if i == n:
            shapes.append(acc)
            return
        for key in opts[i]:
            walk(i + 1, acc + (("one", i, key),))
            if len(shapes) >= cap:
                return
        if i + 1 < n:
            for key in spans[i]:
                walk(i + 2, acc + (("span", i, key),))
                if len(shapes) >= cap:
                    return

    walk(0, ())
    return shapes


def _nights_ok(trip, segs, seg_idx: int, prev_date: str, this_date: str) -> bool:
    """Can we depart on segment `seg_idx` at `this_date`, having flown seg_idx-1?"""
    prev_origin, prev_dest = segs[seg_idx - 1]
    stop = trip.stops[seg_idx]
    landed = arrival_date(prev_date, prev_origin.airports[0], prev_dest.airports[0])
    nights = (dt.date.fromisoformat(this_date) - dt.date.fromisoformat(landed)).days
    if nights < stop.min_nights:
        return False
    if stop.max_nights and nights > stop.max_nights:
        return False
    return True


def _best_date_path(trip, shape, opts, spans, objective: str = "cash"):
    """Cheapest chained date assignment for one fixed shape, by DP over slots.

    State after each slot is the departure date of the last segment it covered,
    plus the running cost. Costs are additive and the chaining constraint looks
    only one segment back, so keeping the best cost per end-date is optimal.

    `objective="cash"` (default) minimizes total cash out-of-pocket, same as
    before. `objective="miles"` instead minimizes total miles required across
    the shape -- used as a fallback when the cash-optimal date assignment turns
    out to be unaffordable, so a cheaper-in-points (but not cheaper-in-cash)
    date can still be found. Either way the returned cash total is the true
    cash cost of whichever candidates were actually chosen, not the DP's
    internal objective value.
    """
    def cost_of(cand: SegCandidate) -> float:
        return cand.miles if objective == "miles" else cand.cash_usd

    segs = trip.segments()
    n = len(segs)
    # state: {last_segment_departure_date: (cost, backpointer_list_of_dates)}
    state: dict[str, tuple[float, tuple[str, ...]]] = {}

    for slot_no, slot in enumerate(shape):
        kind, i, key = slot
        table = opts[i][key] if kind == "one" else spans[i][key]
        nxt: dict[str, tuple[float, tuple[str, ...]]] = {}

        if slot_no == 0:
            if kind == "one":
                for d, cand in table.items():
                    nxt[d] = (cost_of(cand), (d,))
            else:
                for (d0, d1), cand in table.items():
                    if not _nights_ok(trip, segs, i + 1, d0, d1):
                        continue
                    nxt[d1] = min(
                        nxt.get(d1, (float("inf"), ())),
                        (cost_of(cand), (d0, d1)),
                        key=lambda x: x[0],
                    )
        else:
            for prev_date, (cost, dates) in state.items():
                if kind == "one":
                    for d, cand in table.items():
                        if not _nights_ok(trip, segs, i, prev_date, d):
                            continue
                        total = cost + cost_of(cand)
                        cur = nxt.get(d)
                        if cur is None or total < cur[0]:
                            nxt[d] = (total, dates + (d,))
                else:
                    for (d0, d1), cand in table.items():
                        if not _nights_ok(trip, segs, i, prev_date, d0):
                            continue
                        if not _nights_ok(trip, segs, i + 1, d0, d1):
                            continue
                        total = cost + cost_of(cand)
                        cur = nxt.get(d1)
                        if cur is None or total < cur[0]:
                            nxt[d1] = (total, dates + (d0, d1))
        if not nxt:
            return None
        state = nxt

    final_origin, final_dest = segs[n - 1]
    feasible = {
        d: v for d, v in state.items()
        if arrival_date(d, final_origin.airports[0],
                        final_dest.airports[0]) <= trip.arrive_by
    }
    if not feasible:
        return None
    end = min(feasible, key=lambda d: feasible[d][0])
    _, path = feasible[end]
    chosen = []
    for kind, i, key in shape:
        if kind == "one":
            chosen.append(opts[i][key][path[i]])
        else:
            chosen.append(spans[i][key][(path[i], path[i + 1])])
    cash_total = round(sum(c.cash_usd for c in chosen), 2)
    return cash_total, chosen, path


def _award_matrix(tables, balances, lookup, party) -> list[dict]:
    rows = []
    for (o, d, date, cabin, prog), fare in tables.awards.items():
        miles = fare.miles * party
        ok, reason, short = fundability(prog, miles, balances, lookup)
        rows.append({
            "program": prog, "origin": o, "dest": d, "date": date, "cabin": cabin,
            "miles": fare.miles, "party_miles": miles, "seats": fare.seats,
            "direct": fare.direct, "airlines": fare.airlines,
            "updated_at": fare.updated_at,
            "fundable": ok, "reason": reason, "shortfall": short,
        })
    return rows
