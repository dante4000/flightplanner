from award_trip_planner.config import Config
from award_trip_planner.models import AwardFare, CashFare
from award_trip_planner.strategies import compute

CFG = Config()
FEE = round(39.0 * CFG.cad_to_usd, 2)


def aw(o, d, date, cabin, miles, taxes=50.0, seats=4):
    return AwardFare(o, d, date, cabin, miles, taxes, seats, True, "AC", "2026-07-25T00:00:00Z")


def ca(o, d, date, cabin, total, adults, kind="ow", ret=None):
    return CashFare(kind, o, d, date, ret, cabin, adults, total, "Test Air", 1_000_000.0)


AWARDS = [
    aw("LAX", "ICN", "2026-10-01", "Y", 55_000),
    aw("LAX", "ICN", "2026-10-01", "J", 85_000, seats=2),
    aw("ICN", "NRT", "2026-10-06", "Y", 7_500),
    aw("NRT", "LAX", "2026-10-12", "Y", 55_000),
]
CASH = [
    ca("LAX", "ICN", "2026-10-01", "Y", 1300.0, 2),          # $650 pp
    ca("LAX", "ICN", "2026-10-01", "J", 5000.0, 2),          # $2500 pp
    ca("ICN", "NRT", "2026-10-06", "Y", 300.0, 2),           # $150 pp
    ca("NRT", "LAX", "2026-10-12", "Y", 700.0, 1),           # A return
    ca("NRT", "LAX", "2026-10-25", "Y", 650.0, 1),           # B return
    ca("NRT", "LAX", "2026-10-12", "J", 2800.0, 1),
    ca("NRT", "LAX", "2026-10-25", "J", 2600.0, 1),
    ca("NRT", "ICN", "2026-10-10", "Y", 160.0, 1),           # hopback leg
    ca("ICN", "LAX", "2026-10-12", "Y", 750.0, 1),
    ca("ICN", "LAX", "2026-10-25", "Y", 720.0, 1),
]


def result():
    return compute(AWARDS, CASH, [], CFG, now=1_000_000.0)


def test_allcash_baseline_math():
    r = result()
    econ = r["views"]["economy"]
    baseline = next(b for b in econ if b["total_points"] == 0)
    # cheapest all-cash economy: T1 650*2 + hop 150*2 + A 700 + B 650 = 2950
    assert baseline["total_cash_usd"] == 2950.0
    assert baseline["cpp"] is None


def test_stopover_bundle_present_and_costed():
    r = result()
    mixed = r["views"]["mixed"]
    stop = [b for b in mixed if any(l["product"] == "award_stopover" for l in b["lines"])]
    assert stop, "expected a stopover bundle in mixed view"
    b = stop[0]
    # both persons on stopover award: 2 * 60_000 pts > budget -> must be one person max
    assert b["total_points"] <= CFG.points_budget


def test_budget_gate():
    small = Config()
    small.points_budget = 50_000
    r = compute(AWARDS, CASH, [], small, now=1_000_000.0)
    for b in r["views"]["mixed"]:
        assert b["total_points"] <= 50_000


def test_seat_gate():
    # J award has 2 seats -> both can use it; shrink to 1 -> bundles with both-J-award vanish
    awards = [a for a in AWARDS if not (a.cabin == "J" and a.origin == "LAX")]
    awards.append(aw("LAX", "ICN", "2026-10-01", "J", 85_000, seats=1))
    r = compute(awards, CASH, [], CFG, now=1_000_000.0)
    for b in r["views"]["mixed"]:
        j_award_users = sum(
            1 for l in b["lines"]
            if l["product"] in ("award_ow", "award_stopover")
            and any(leg["cabin"] == "J" and leg["origin"] == "LAX" for leg in l["legs"])
        )
        assert j_award_users <= 1


def test_cpp_computed_against_view_baseline():
    r = result()
    econ = r["views"]["economy"]
    baseline = next(b for b in econ if b["total_points"] == 0)["total_cash_usd"]
    for b in econ:
        if b["total_points"]:
            expected = round(100 * (baseline - b["total_cash_usd"]) / b["total_points"], 2)
            assert b["cpp"] == expected


def test_refine_requests_target_rt_estimates():
    r = result()
    for q in r["refine_requests"]:
        assert q.kind == "rt" and q.adults == 1 and q.priority == 0
    assert len(r["refine_requests"]) <= CFG.refine_query_cap


def test_ranked_ascending_and_capped():
    r = result()
    for view in r["views"].values():
        costs = [b["total_cash_usd"] for b in view]
        assert costs == sorted(costs)
        assert len(view) <= CFG.top_n
