from award_trip_planner.bonuses import Bonus, bonus_lookup_from
from award_trip_planner.config import Config
from award_trip_planner.models import AwardFare, CashFare
from award_trip_planner.products import award_candidates, build_tables, fee_usd
from award_trip_planner.solver import _assign_funding, solve
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
