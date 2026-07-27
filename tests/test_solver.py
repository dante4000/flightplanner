from award_trip_planner.bonuses import Bonus
from award_trip_planner.config import Config
from award_trip_planner.models import AwardFare, CashFare
from award_trip_planner.products import build_tables, fee_usd
from award_trip_planner.solver import solve
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
