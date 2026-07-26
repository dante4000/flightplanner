from award_trip_planner.config import Config
from award_trip_planner.models import AwardFare, CashFare
from award_trip_planner.strategies import (
    build_tables,
    ow_award_option,
    ow_cash_option,
    rt_cash_option,
    stopover_option,
)

CFG = Config()
FEE = round(39.0 * CFG.cad_to_usd, 2)   # 28.47


def aw(o, d, date, cabin, miles, taxes=None, seats=2):
    return AwardFare(o, d, date, cabin, miles, taxes, seats, True, "AC", "2026-07-25T00:00:00Z")


def ca(o, d, date, cabin, total, adults=2, kind="ow", ret=None, fetched=1000.0):
    return CashFare(kind, o, d, date, ret, cabin, adults, total, "Test Air", fetched)


def tables(awards=(), cash=(), overrides=()):
    return build_tables(list(awards), list(cash), list(overrides), CFG)


def test_build_tables_picks_best_and_applies_overrides():
    t = tables(
        awards=[aw("LAX", "ICN", "2026-10-01", "Y", 70_000), aw("LAX", "ICN", "2026-10-01", "Y", 55_000)],
        cash=[ca("ICN", "NRT", "2026-10-05", "Y", 400.0), ca("ICN", "NRT", "2026-10-05", "Y", 294.0)],
        overrides=[{"origin": "ICN", "dest": "HND", "date": "2026-10-05", "cabin": "Y", "price_pp": 120.0}],
    )
    assert t.awards[("LAX", "ICN", "2026-10-01", "Y")].miles == 55_000
    assert t.cash_ow[("ICN", "NRT", "2026-10-05", "Y")].per_person() == 147.0
    ov = t.cash_ow[("ICN", "HND", "2026-10-05", "Y")]
    assert ov.manual and ov.per_person() == 120.0


def test_ow_options():
    t = tables(
        awards=[aw("LAX", "ICN", "2026-10-01", "J", 85_000, taxes=84.32)],
        cash=[ca("LAX", "ICN", "2026-10-01", "J", 2400.0)],
    )
    c = ow_cash_option(t, "LAX", "ICN", "2026-10-01", "J", 2, now=2000.0, cfg=CFG)
    assert c.cash_pp == 1200.0 and c.points_pp == 0
    a = ow_award_option(t, "LAX", "ICN", "2026-10-01", "J", 2, CFG)
    assert a.points_pp == 85_000
    assert a.cash_pp == round(84.32 + FEE, 2)
    assert a.award_seat_legs[0][1] == 2
    assert ow_award_option(t, "LAX", "ICN", "2026-10-02", "J", 2, CFG) is None


def test_award_default_taxes_flagged():
    t = tables(awards=[aw("ICN", "NRT", "2026-10-05", "J", 52_500, taxes=None)])
    a = ow_award_option(t, "ICN", "NRT", "2026-10-05", "J", 1, CFG)
    assert a.cash_pp == round(CFG.default_hop_award_taxes_usd + FEE, 2)
    assert "award-taxes-estimated" in a.flags


def test_rt_exact_beats_estimate():
    t = tables(cash=[
        ca("LAX", "ICN", "2026-10-01", "Y", 700.0, adults=1),
        ca("ICN", "LAX", "2026-10-12", "Y", 700.0, adults=1),
        ca("LAX", "ICN", "2026-10-01", "Y", 900.0, adults=1, kind="rt", ret="2026-10-12"),
    ])
    rt = rt_cash_option(t, "LAX", "ICN", "2026-10-01", "2026-10-12", "Y", 1, CFG, now=2000.0)
    assert rt.cash_pp == 900.0 and rt.flags == ()
    t2 = tables(cash=[
        ca("LAX", "ICN", "2026-10-01", "Y", 700.0, adults=1),
        ca("ICN", "LAX", "2026-10-12", "Y", 700.0, adults=1),
    ])
    est = rt_cash_option(t2, "LAX", "ICN", "2026-10-01", "2026-10-12", "Y", 1, CFG, now=2000.0)
    assert est.cash_pp == 1400.0 and "rt-estimated-from-oneways" in est.flags


def test_stopover():
    t = tables(awards=[
        aw("LAX", "ICN", "2026-10-01", "Y", 55_000, taxes=50.0, seats=4),
        aw("ICN", "NRT", "2026-10-05", "Y", 7_500, taxes=None, seats=2),
    ])
    s = stopover_option(t, "LAX", "ICN", "2026-10-01", "NRT", "2026-10-05", "Y", CFG)
    assert s.points_pp == 60_000                       # 55k + 5k
    assert s.cash_pp == round(50.0 + CFG.default_hop_award_taxes_usd + FEE, 2)
    assert min(n for _, n in s.award_seat_legs) == 2
    assert "stopover-verify-on-aeroplan" in s.flags
    assert stopover_option(t, "LAX", "ICN", "2026-10-01", "NRT", "2026-10-06", "Y", CFG) is None


def test_rt_estimate_propagates_staleness():
    stale = 1000.0
    t2 = tables(cash=[
        ca("LAX", "ICN", "2026-10-01", "Y", 700.0, adults=1, fetched=stale),
        ca("ICN", "LAX", "2026-10-12", "Y", 700.0, adults=1, fetched=stale),
    ])
    est = rt_cash_option(t2, "LAX", "ICN", "2026-10-01", "2026-10-12", "Y", 1, CFG,
                         now=stale + CFG.cash_ttl_hours * 3600 + 1)
    assert "rt-estimated-from-oneways" in est.flags
    assert "stale-cash" in est.flags
