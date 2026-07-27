import datetime as dt

from award_trip_planner.bonuses import Bonus, bonus_lookup_from
from award_trip_planner.config import Config
from award_trip_planner.models import AwardFare, CashFare
from award_trip_planner.products import award_candidates, build_tables, fee_usd
from award_trip_planner.solver import (
    _assign_funding,
    _best_date_path,
    _enumerate_shapes,
    _segment_date_options,
    _segment_options,
    _span_options,
    solve,
)
from award_trip_planner.trip import Stop, Trip

CFG = Config()
FEE = fee_usd(CFG)
LA = Stop(city="Los Angeles", airports=("LAX",))
SEOUL = Stop(city="Seoul", airports=("ICN",), min_nights=3, max_nights=5)
TOKYO = Stop(city="Tokyo", airports=("NRT",), min_nights=3, max_nights=5)

TRIP = Trip(stops=(LA, SEOUL, TOKYO, LA), depart_start="2026-10-01",
            depart_end="2026-10-01", arrive_by="2026-10-20", party_size=1)
# LAX->ICN departs 10-01, arrives 10-02; +3..5 nights -> ICN->NRT on 10-05..10-07;
# arrive same day; +3..5 nights -> NRT->LAX on 10-08..10-12.


def aw(o, d, date, cabin, miles, program, taxes=50.0, seats=4):
    return AwardFare(o, d, date, cabin, miles, taxes, seats, True, "XX",
                     "2026-07-25T00:00:00Z", program)


def ca(o, d, date, cabin, total):
    return CashFare("ow", o, d, date, None, cabin, 1, total, "Test Air", 1_000_000.0)


CASH = [
    ca("LAX", "ICN", "2026-10-01", "Y", 600.0),
    ca("ICN", "NRT", "2026-10-05", "Y", 100.0),
    ca("NRT", "LAX", "2026-10-08", "Y", 800.0),
]
AWARDS = [
    aw("NRT", "LAX", "2026-10-08", "Y", 50_000, "aeroplan", taxes=40.0),
    aw("LAX", "ICN", "2026-10-01", "Y", 45_000, "virginatlantic", taxes=30.0),
]
RICH = {"amex_mr": 500_000, "chase_ur": 0, "aeroplan_fixed": 100_000}


def run(balances=None, bonuses=(), trip=TRIP, awards=None, cash=None, top_n=15,
        sort="cash"):
    tables = build_tables(list(awards or AWARDS), list(cash or CASH), [], CFG)
    return solve(trip, tables, balances or RICH, list(bonuses), CFG,
                 now=1_000_000.0, top_n=top_n, sort=sort)


def test_all_cash_baseline():
    r = run()
    assert r.baseline_cash == 1500.0        # 600 + 100 + 800
    base = [b for b in r.bundles if not any(b["points"].values())]
    assert base and base[0]["total_cash_usd"] == 1500.0
    assert base[0]["cpp"] is None


def test_cash_sort_puts_the_cheapest_trip_first():
    """Default sort answers 'what do I pay' — using both awards is cash-cheapest."""
    r = run()
    best = r.bundles[0]
    # both awards: VS taxes (30+fee) + cash hop 100 + aeroplan (40+fee)
    assert best["total_cash_usd"] == round((30.0 + FEE) + 100.0 + (40.0 + FEE), 2)
    assert [s["kind"] for s in best["segments"]] == ["award", "cash", "award"]
    costs = [b["total_cash_usd"] for b in r.bundles]
    assert costs == sorted(costs)


def test_value_sort_prefers_the_better_redemption():
    """`sort='value'` answers 'where do my points work hardest' instead."""
    r = run(sort="value")
    best = r.bundles[0]
    # aeroplan on the priciest leg: 600 + 100 + (40 + fee), 50k points, best cpp
    assert best["total_cash_usd"] == round(600.0 + 100.0 + 40.0 + FEE, 2)
    assert [s["kind"] for s in best["segments"]] == ["cash", "cash", "award"]
    assert best["segments"][2]["program"] == "aeroplan"
    cpps = [b["cpp"] for b in r.bundles if b["cpp"] is not None and b["cpp"] > 0]
    assert cpps == sorted(cpps, reverse=True)


def test_dates_chain_and_respect_night_bounds():
    for b in run().bundles:
        d = b["dates"]
        assert d[0] == "2026-10-01"
        assert "2026-10-05" <= d[1] <= "2026-10-07"
        assert d[1] < d[2]


def test_funding_split_across_currencies():
    # both awards used; MR can only cover one, the fixed Aeroplan balance covers the other
    balances = {"amex_mr": 45_000, "chase_ur": 0, "aeroplan_fixed": 50_000}
    r = run(balances=balances)
    both = [b for b in r.bundles
            if sum(1 for s in b["segments"] if s["kind"] == "award") == 2]
    assert both, "expected a bundle using both awards"
    p = both[0]["points"]
    assert p["amex_mr"] == 45_000 and p["aeroplan_fixed"] == 50_000


def test_insufficient_balance_rejects_the_bundle():
    r = run(balances={"amex_mr": 0, "chase_ur": 0, "aeroplan_fixed": 10_000})
    for b in r.bundles:
        assert not any(b["points"].values()), "no award should be affordable"


def test_bonus_reduces_points_drawn():
    r = run(balances={"amex_mr": 500_000, "chase_ur": 0, "aeroplan_fixed": 0},
            bonuses=[Bonus("amex_mr", "aeroplan", 0.25, None, "manual")])
    # target the bundle whose ONLY award is the aeroplan one, so the assertion
    # isolates that redemption's cost rather than summing several awards
    only_ap = [b for b in r.bundles
               if [s["program"] for s in b["segments"] if s["kind"] == "award"]
               == ["aeroplan"]]
    assert only_ap
    assert only_ap[0]["points"]["amex_mr"] == 40_000     # ceil(50000 / 1.25)

    # and the un-bonused Virgin award in the both-awards bundle still costs 1:1,
    # so a bonus on one program never discounts another
    both = [b for b in r.bundles
            if sorted(s["program"] for s in b["segments"] if s["kind"] == "award")
            == ["aeroplan", "virginatlantic"]]
    assert both and both[0]["points"]["amex_mr"] == 45_000 + 40_000


def test_shapes_are_unique():
    r = run()
    shapes = [tuple(b["shape"]) for b in r.bundles]
    assert len(shapes) == len(set(shapes))
    assert all(isinstance(s, str) for b in r.bundles for s in b["shape"])


def test_cpp_against_baseline():
    r = run()
    for b in r.bundles:
        pts = sum(b["points"].values())
        if pts:
            assert b["cpp"] == round(100 * (1500.0 - b["total_cash_usd"]) / pts, 2)


def test_unpriceable_segment_degrades_with_a_note():
    r = run(cash=[ca("LAX", "ICN", "2026-10-01", "Y", 600.0)], awards=[])
    assert r.bundles == []
    assert any("no option" in n.lower() for n in r.notes)


def test_award_matrix_marks_unfundable_programs():
    r = run(balances={"amex_mr": 0, "chase_ur": 0, "aeroplan_fixed": 0})
    rows = {(m["program"], m["origin"], m["dest"]): m for m in r.award_matrix}
    ap = rows[("aeroplan", "NRT", "LAX")]
    assert ap["fundable"] is False
    assert ap["reason"] == "insufficient balance"
    assert ap["shortfall"] == 50_000


# --- Critical 1: multi-airport cities must not collapse to one arbitrary airport ---

def test_multi_airport_stop_keeps_all_routings_not_last_write_wins():
    """`by_key.setdefault((kind, program, cabin), {})[d] = c` (no routing in the
    key) let a second airport-pair candidate on the same date silently
    overwrite the first. LAX->ICN ($300) and LAX->GMP ($900) must both survive
    as distinct options, and the cheaper one must be the one reported first.
    """
    la = Stop(city="Los Angeles", airports=("LAX",))
    seoul_multi = Stop(city="Seoul", airports=("ICN", "GMP"))
    trip = Trip(stops=(la, seoul_multi), depart_start="2026-10-01",
                depart_end="2026-10-01", arrive_by="2026-10-05", party_size=1)
    cash = [
        ca("LAX", "ICN", "2026-10-01", "Y", 300.0),
        ca("LAX", "GMP", "2026-10-01", "Y", 900.0),
    ]
    tables = build_tables([], cash, [], CFG)
    r = solve(trip, tables, RICH, [], CFG, now=1_000_000.0, top_n=15)
    assert len(r.bundles) == 2, "both airport-pair routings must survive as distinct options"
    totals = sorted(b["total_cash_usd"] for b in r.bundles)
    assert totals == [300.0, 900.0]
    assert r.bundles[0]["total_cash_usd"] == 300.0  # cheapest routing, not last-write-wins GMP
    shapes = {tuple(b["shape"]) for b in r.bundles}
    assert len(shapes) == 2  # routing keeps the shapes distinguishable
    all_labels = [s for b in r.bundles for s in b["shape"]]
    assert any("LAX-ICN" in s for s in all_labels)
    assert any("LAX-GMP" in s for s in all_labels)


# --- Critical 2: an affordable award must not be dropped just because the
#     cash-cheapest date happens to be unaffordable ---

SEOUL_ONLY = Stop(city="Seoul", airports=("ICN",))


def _points_constrained_trip():
    return Trip(stops=(SEOUL_ONLY, LA), depart_start="2026-10-05",
                depart_end="2026-10-06", arrive_by="2026-10-10", party_size=1)


def _points_constrained_awards():
    return [
        aw("ICN", "LAX", "2026-10-05", "Y", 50_000, "aeroplan", taxes=100.0),
        aw("ICN", "LAX", "2026-10-06", "Y", 90_000, "aeroplan", taxes=10.0),
    ]


def test_points_constrained_dates_retries_with_miles_objective():
    """10-06 is cash-cheapest but needs 90k miles (unaffordable); 10-05 costs
    more cash but only needs 50k miles, which the balance covers. The shape
    must not be dropped -- it must be retried on a miles-minimizing objective
    and offered with a flag, not silently discarded.
    """
    trip = _points_constrained_trip()
    tables = build_tables(_points_constrained_awards(), [], [], CFG)
    balances = {"amex_mr": 60_000, "chase_ur": 0, "aeroplan_fixed": 0}
    r = solve(trip, tables, balances, [], CFG, now=1_000_000.0, top_n=15)
    assert len(r.bundles) == 1
    b = r.bundles[0]
    assert tuple(b["dates"]) == ("2026-10-05",)
    assert b["points"]["amex_mr"] == 50_000
    assert "points-constrained-dates" in b["flags"]
    assert not r.notes

    row = next(m for m in r.award_matrix
               if m["date"] == "2026-10-05" and m["program"] == "aeroplan")
    assert row["fundable"] is True


def test_unfundable_shape_across_all_dates_gets_a_note_not_a_silent_drop():
    trip = _points_constrained_trip()
    tables = build_tables(_points_constrained_awards(), [], [], CFG)
    balances = {"amex_mr": 10_000, "chase_ur": 0, "aeroplan_fixed": 0}
    r = solve(trip, tables, balances, [], CFG, now=1_000_000.0, top_n=15)
    assert r.bundles == []
    assert any("dropped" in n.lower() and "cannot fund" in n.lower() for n in r.notes)


# --- Important 3: funding tie-break must not defeat a transfer bonus ---

def test_funding_prefers_the_bonused_currency_over_more_headroom():
    """`key = (-headroom, spent)` made headroom primary, so a 100k chase_ur
    balance (0 spent there but drained less) could out-rank spending fewer
    total points via a 25%-bonused amex_mr transfer. It must minimize spend
    first.
    """
    awards = [aw("ICN", "LAX", "2026-10-01", "Y", 50_000, "aeroplan", taxes=50.0)]
    lookup = bonus_lookup_from([Bonus("amex_mr", "aeroplan", 0.25, None, "manual")])
    balances = {"amex_mr": 45_000, "chase_ur": 100_000, "aeroplan_fixed": 100_000}
    tables = build_tables(awards, [], [], CFG)
    cands = award_candidates(tables, SEOUL_ONLY, LA, "2026-10-01", ("Y",), 1, CFG)
    assigned = _assign_funding(cands, balances, lookup)
    assert assigned == {"amex_mr": 40_000}


# --- Important 4: the stopover/span DP branches ---

def test_stopover_span_produces_a_two_leg_bundle_with_correct_dates_and_miles():
    cash = [
        ca("LAX", "ICN", "2026-10-01", "Y", 600.0),
        ca("ICN", "NRT", "2026-10-05", "Y", 100.0),
        ca("NRT", "LAX", "2026-10-08", "Y", 800.0),
    ]
    awards = [
        aw("LAX", "ICN", "2026-10-01", "Y", 40_000, "aeroplan", taxes=30.0),
        aw("ICN", "NRT", "2026-10-05", "Y", 20_000, "aeroplan", taxes=20.0),
    ]
    r = run(cash=cash, awards=awards, top_n=50)
    span_bundles = [b for b in r.bundles if any(s["spans"] == 2 for s in b["segments"])]
    assert span_bundles, "expected at least one bundle using the stopover span"
    seg = next(s for s in span_bundles[0]["segments"] if s["spans"] == 2)
    assert seg["miles"] == (40_000 + 5_000) * TRIP.party_size
    d0, d1 = seg["legs"][0]["date"], seg["legs"][1]["date"]
    assert d0 == "2026-10-01"
    assert "2026-10-05" <= d1 <= "2026-10-07"
    assert d0 < d1


# --- Important 5: `any` should be `all` for the per-segment date pre-filter ---

def test_partial_date_infeasibility_names_dates_not_pricing(monkeypatch):
    """If even one segment has no feasible date (while others do), the note
    must say so -- not fall through to the misleading pricing-layer note.
    """
    import award_trip_planner.solver as solver_mod

    def fake_segment_date_options(trip, cfg):
        return [["2026-10-01"], [], ["2026-10-08"]], False

    monkeypatch.setattr(solver_mod, "_segment_date_options", fake_segment_date_options)
    r = run()
    assert r.bundles == []
    assert any("date" in n.lower() for n in r.notes)
    assert not any("no option priced" in n.lower() for n in r.notes)


# --- Round 2 review ---
# --- Critical 1: the `miles` objective needs a cash tie-break ---

def test_miles_objective_breaks_ties_by_cash_not_earliest_date():
    """`_best_date_path`'s miles objective returned `cand.miles` alone, so cash
    contributed nothing to the DP's cost -- among two date assignments tied on
    miles, the DP kept whichever it reached first (an artifact of dict
    iteration order over the earlier segment's dates), not the cheaper one.

    Repro: cash LAX->ICN is $2000 on 10-01 and $600 on 10-02, both of which
    chain into either award date (2 or 3 nights, within the [2,5]-night Seoul
    stopover). ICN->LAX also has cash ($898.47 on 10-05, $950 on 10-06) so an
    all-cash baseline exists ($1,498.47, via 10-02+10-05), matching the
    review's cpp figures exactly. The cash-optimal award path (10-02 + the
    90k-mile 10-06 award, at $638.47) needs 90k miles -- unaffordable on a
    60k balance -- so the solver retries on the miles objective. Both
    remaining candidates for 50k miles (10-01+10-05 at $2128.47, cpp -1.26;
    10-02+10-05 at $728.47, cpp +1.54) tie on miles; the fix must pick the
    cheaper one, not whichever it visits first.
    """
    seoul = Stop(city="Seoul", airports=("ICN",), min_nights=2, max_nights=5)
    trip = Trip(stops=(LA, seoul, LA), depart_start="2026-10-01",
                depart_end="2026-10-02", arrive_by="2026-10-10", party_size=1)
    cash = [
        ca("LAX", "ICN", "2026-10-01", "Y", 2000.0),
        ca("LAX", "ICN", "2026-10-02", "Y", 600.0),
        ca("ICN", "LAX", "2026-10-05", "Y", 898.47),
        ca("ICN", "LAX", "2026-10-06", "Y", 950.0),
    ]
    awards = [
        aw("ICN", "LAX", "2026-10-05", "Y", 50_000, "aeroplan", taxes=100.0),
        aw("ICN", "LAX", "2026-10-06", "Y", 90_000, "aeroplan", taxes=10.0),
    ]
    tables = build_tables(awards, cash, [], CFG)
    balances = {"amex_mr": 60_000, "chase_ur": 0, "aeroplan_fixed": 0}
    r = solve(trip, tables, balances, [], CFG, now=1_000_000.0, top_n=15)

    assert r.baseline_cash == 1498.47
    award_bundles = [b for b in r.bundles
                     if any(s["kind"] == "award" for s in b["segments"])]
    assert len(award_bundles) == 1
    b = award_bundles[0]
    assert tuple(b["dates"]) == ("2026-10-02", "2026-10-05")
    assert b["total_cash_usd"] == 728.47
    assert b["points"]["amex_mr"] == 50_000
    assert b["cpp"] == 1.54
    assert "points-constrained-dates" in b["flags"]


# --- Critical 2: per-segment pruning must not crowd out every award ---

def test_pruning_reserves_award_slots_so_awards_are_never_crowded_out():
    """Including routing in the option key raised distinct option-kinds per
    segment from `cabins x (1+programs)` to `cabins x airport_pairs x
    (1+programs)`, but a flat cheapest-N-across-all-kinds prune ranks awards
    by a scalarizer that includes their miles (`cash_usd + 0.0115 * miles`),
    so they routinely rank "worse" than nearly any cash fare -- on a
    multi-airport, multi-program trip a tight cap can (and did) prune every
    single award option, even affordable ones. Reserving award slots per
    kind must prevent that on every segment that actually has an award.
    """
    la = Stop(city="Los Angeles", airports=("LAX", "SFO"))
    seoul = Stop(city="Seoul", airports=("ICN", "GMP"), min_nights=3, max_nights=5)
    tokyo = Stop(city="Tokyo", airports=("NRT", "HND"), min_nights=3, max_nights=5)
    trip = Trip(stops=(la, seoul, tokyo), depart_start="2026-10-01",
                depart_end="2026-10-01", arrive_by="2026-10-20", party_size=1,
                cabins=("Y",))
    cash = [
        ca("LAX", "ICN", "2026-10-01", "Y", 300.0),
        ca("LAX", "GMP", "2026-10-01", "Y", 320.0),
        ca("SFO", "ICN", "2026-10-01", "Y", 340.0),
        ca("SFO", "GMP", "2026-10-01", "Y", 360.0),
        ca("ICN", "NRT", "2026-10-05", "Y", 100.0),
        ca("ICN", "HND", "2026-10-05", "Y", 110.0),
        ca("GMP", "NRT", "2026-10-05", "Y", 120.0),
        ca("GMP", "HND", "2026-10-05", "Y", 130.0),
    ]
    awards = [
        aw("LAX", "ICN", "2026-10-01", "Y", 45_000, "aeroplan", taxes=30.0),
        aw("LAX", "ICN", "2026-10-01", "Y", 50_000, "virginatlantic", taxes=25.0),
        aw("SFO", "GMP", "2026-10-01", "Y", 60_000, "aeroplan", taxes=35.0),
        aw("ICN", "NRT", "2026-10-05", "Y", 20_000, "aeroplan", taxes=20.0),
        aw("GMP", "HND", "2026-10-05", "Y", 25_000, "virginatlantic", taxes=15.0),
    ]
    cfg = Config(max_options_per_segment=4)  # deliberately tight, to force pruning
    tables = build_tables(awards, cash, [], cfg)
    dates_per_seg, _ = _segment_date_options(trip, cfg)
    opts, missing, prune_notes = _segment_options(trip, tables, dates_per_seg, cfg,
                                                   1_000_000.0)

    assert len(opts) == 2
    for i, seg_opts in enumerate(opts):
        assert len(seg_opts) <= cfg.max_options_per_segment
        kinds = {k[0] for k in seg_opts}
        assert "award" in kinds, f"segment {i}: every award option was pruned"

    # segment 0: 3 award kinds among 7 total, cap 4 -> 2 awards guaranteed to survive
    assert sum(1 for k in opts[0] if k[0] == "award") == 2
    # segment 1: 2 award kinds among 6 total, cap 4 -> both guaranteed to survive
    assert sum(1 for k in opts[1] if k[0] == "award") == 2

    assert prune_notes, "expected the tight cap to trigger pruning notes"
    assert any("award" in note for note in prune_notes)
    assert any("cash" in note for note in prune_notes)


# --- Critical 3: stopover shapes must not be the first thing lost to the cap ---

def test_span_shapes_survive_a_tight_shape_cap():
    """`_enumerate_shapes` walked every `opts[i]` subtree before ever trying a
    span at position `i`, so on a segment/option count large enough to trip
    `max_shapes`, the span (stopover) shapes -- the more interesting
    itinerary -- were the first thing lost, not the last, and could be wholly
    absent from a capped result. Trying spans first at each position means
    they dominate the front of the walk instead.
    """
    n = 4
    # 24 single-option "kinds" per segment, simulating a multi-airport blowup.
    opts = [{f"opt{i}-{j}": None for j in range(24)} for i in range(n)]
    spans = [dict() for _ in range(n)]
    spans[0] = {"span0-a": None, "span0-b": None}
    spans[2] = {"span2-a": None}

    shapes = _enumerate_shapes(opts, spans, n, cap=10)

    assert len(shapes) == 10
    assert any(shape[0][0] == "span" for shape in shapes), \
        "span-containing shapes must appear before the cap trips, not after"


# --- Important 4: dropped-shape notes must not truncate silently ---

def test_dropped_shapes_grouped_by_program_with_overflow_note():
    """Every dropped shape got its own note keyed by a debug-formatted,
    deduped shape-label string (`one:0:award/aeroplan/...`), hard-capped at
    10 with no acknowledgement of what was cut beyond that. Notes must
    instead be user-facing text grouped by the program(s) involved, and must
    say when there are still more distinct groups than fit.
    """
    trip = Trip(stops=(SEOUL_ONLY, LA), depart_start="2026-10-05",
                depart_end="2026-10-05", arrive_by="2026-10-10", party_size=1)
    # 11 distinct, unregistered programs: `program_for` gives each an empty
    # transfer map, so `_assign_funding` always fails for them regardless of
    # balance -- every one of these 11 single-program shapes gets dropped.
    awards = [aw("ICN", "LAX", "2026-10-05", "Y", 50_000, f"acmemiles{i}")
              for i in range(11)]
    tables = build_tables(awards, [], [], CFG)
    r = solve(trip, tables, {}, [], CFG, now=1_000_000.0, top_n=15)

    assert r.bundles == []
    grouped = [n for n in r.notes if "acmemiles" in n]
    assert len(grouped) == 10
    assert all("dropped" in n and "cannot fund" in n for n in grouped)
    assert all("+" not in n for n in grouped), \
        "notes must be user-facing text, not a raw shape-label join"

    overflow = [n for n in r.notes if "more shapes dropped for funding" in n]
    assert len(overflow) == 1
    assert "1 more" in overflow[0]


# --- Important 5: the chain-into-a-span DP branch is untested ---

def test_one_to_span_transition_checks_nights_before_entering_the_span():
    """The existing span test (`test_stopover_span_produces_a_two_leg_bundle_
    with_correct_dates_and_miles`) only ever puts a span at shape-slot 0,
    which `_best_date_path` handles in its dedicated first-slot branch. The
    `else` branch -- a "one" segment feeding into a span, which must call
    `_nights_ok` for the gap before the span *and* for the span's own two
    legs -- was never exercised. Adding a third Aeroplan leg (NRT->LAX)
    populates `spans[1]` (segments 1+2), forcing a one -> span transition.
    """
    cash = [
        ca("LAX", "ICN", "2026-10-01", "Y", 600.0),
        ca("ICN", "NRT", "2026-10-05", "Y", 100.0),
        ca("NRT", "LAX", "2026-10-08", "Y", 800.0),
    ]
    awards = [
        aw("LAX", "ICN", "2026-10-01", "Y", 40_000, "aeroplan", taxes=30.0),
        aw("ICN", "NRT", "2026-10-05", "Y", 20_000, "aeroplan", taxes=20.0),
        aw("NRT", "LAX", "2026-10-08", "Y", 15_000, "aeroplan", taxes=15.0),
    ]
    tables = build_tables(awards, cash, [], CFG)
    dates_per_seg, _ = _segment_date_options(TRIP, CFG)
    opts, _, _ = _segment_options(TRIP, tables, dates_per_seg, CFG, 1_000_000.0)
    spans = _span_options(TRIP, tables, dates_per_seg, CFG)

    assert spans[1], "expected spans[1] (segments 1+2) to be populated"
    span_key = next(iter(spans[1]))
    cash_key = next(k for k in opts[0] if k[0] == "cash")

    shape = (("one", 0, cash_key), ("span", 1, span_key))
    result = _best_date_path(TRIP, shape, opts, spans)

    assert result is not None, "a one -> span transition should be feasible here"
    cash_total, chosen, path = result
    assert len(path) == 3
    d0, d1, d2 = path
    assert d0 == "2026-10-01"
    assert "2026-10-05" <= d1 <= "2026-10-07"     # Seoul stopover: 3-5 nights
    assert d1 < d2

    span_seg = chosen[1]
    assert span_seg.spans == 2
    assert span_seg.legs[0].date == d1
    assert span_seg.legs[1].date == d2
    nights_tokyo = (dt.date.fromisoformat(d2) - dt.date.fromisoformat(d1)).days
    assert 3 <= nights_tokyo <= 5                  # Tokyo stopover: 3-5 nights
