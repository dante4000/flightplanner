from award_trip_planner.config import Config
from award_trip_planner.models import AwardFare, CashFare
from award_trip_planner.products import (
    award_candidates,
    build_tables,
    cash_candidates,
    fee_usd,
    stopover_candidates,
)
from award_trip_planner.trip import Stop

CFG = Config()
FEE = round(39.0 * CFG.cad_to_usd, 2)
LA = Stop(city="Los Angeles", airports=("LAX",))
SEOUL = Stop(city="Seoul", airports=("ICN", "GMP"))
TOKYO = Stop(city="Tokyo", airports=("NRT", "HND"))


def aw(o, d, date, cabin, miles, program, taxes=50.0, seats=4):
    return AwardFare(o, d, date, cabin, miles, taxes, seats, True, "AC",
                     "2026-07-25T00:00:00Z", program)


def ca(o, d, date, cabin, total, adults=2, fetched=1_000_000.0):
    return CashFare("ow", o, d, date, None, cabin, adults, total, "Test Air", fetched)


def tables(awards=(), cash=(), overrides=()):
    return build_tables(list(awards), list(cash), list(overrides), CFG)


def test_tables_key_awards_by_program():
    t = tables(awards=[
        aw("LAX", "ICN", "2026-10-01", "Y", 55_000, "aeroplan"),
        aw("LAX", "ICN", "2026-10-01", "Y", 40_000, "flyingblue"),
        aw("LAX", "ICN", "2026-10-01", "Y", 70_000, "aeroplan"),   # worse, loses
    ])
    assert t.awards[("LAX", "ICN", "2026-10-01", "Y", "aeroplan")].miles == 55_000
    assert t.awards[("LAX", "ICN", "2026-10-01", "Y", "flyingblue")].miles == 40_000


def test_cash_candidate_multiplies_by_party():
    t = tables(cash=[ca("LAX", "ICN", "2026-10-01", "Y", 1000.0, adults=2)])  # $500 pp
    got = cash_candidates(t, LA, SEOUL, "2026-10-01", ("Y",), party=3, cfg=CFG, now=1_000_000.0)
    assert len(got) == 1
    assert got[0].kind == "cash"
    assert got[0].cash_usd == 1500.0        # $500 pp x 3
    assert got[0].miles == 0


def test_award_candidate_multiplies_and_gates_seats():
    t = tables(awards=[
        aw("LAX", "ICN", "2026-10-01", "Y", 50_000, "aeroplan", taxes=40.0, seats=2),
        aw("LAX", "GMP", "2026-10-01", "Y", 30_000, "flyingblue", taxes=None, seats=0),
    ])
    got = {c.program: c for c in
           award_candidates(t, LA, SEOUL, "2026-10-01", ("Y",), party=2, cfg=CFG)}
    ap = got["aeroplan"]
    assert ap.miles == 100_000                              # 50k x 2 people
    assert ap.cash_usd == round((40.0 + FEE) * 2, 2)        # (taxes + fee) x 2
    # seats == 0 means unknown: kept, flagged
    assert "seats-unknown" in got["flyingblue"].flags
    assert "award-taxes-estimated" in got["flyingblue"].flags
    # a 3-person party exceeds the 2 known seats on the aeroplan row
    got3 = {c.program: c for c in
            award_candidates(t, LA, SEOUL, "2026-10-01", ("Y",), party=3, cfg=CFG)}
    assert "aeroplan" not in got3
    assert "flyingblue" in got3          # unknown seats are not a hard gate


def test_award_candidates_span_all_airport_pairs_in_the_cities():
    t = tables(awards=[
        aw("GMP", "HND", "2026-10-05", "Y", 15_000, "aeroplan"),
    ])
    got = award_candidates(t, SEOUL, TOKYO, "2026-10-05", ("Y",), party=1, cfg=CFG)
    assert len(got) == 1
    assert (got[0].legs[0].origin, got[0].legs[0].dest) == ("GMP", "HND")


def test_stopover_only_for_programs_with_a_rule():
    t = tables(awards=[
        aw("LAX", "ICN", "2026-10-01", "Y", 55_000, "aeroplan", taxes=50.0),
        aw("ICN", "NRT", "2026-10-05", "Y", 7_500, "aeroplan", taxes=None),
        aw("LAX", "ICN", "2026-10-01", "Y", 40_000, "flyingblue", taxes=50.0),
        aw("ICN", "NRT", "2026-10-05", "Y", 8_000, "flyingblue", taxes=10.0),
    ])
    got = stopover_candidates(t, LA, SEOUL, TOKYO, "2026-10-01", "2026-10-05",
                              ("Y",), party=2, cfg=CFG)
    assert {c.program for c in got} == {"aeroplan"}      # flyingblue has no rule
    s = got[0]
    assert s.miles == (55_000 + 5_000) * 2
    assert s.cash_usd == round((50.0 + CFG.default_hop_award_taxes_usd + FEE) * 2, 2)
    assert "stopover-verify-with-program" in s.flags
    assert len(s.legs) == 2


def test_fee_helper():
    assert fee_usd(CFG) == FEE
