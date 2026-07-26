from award_trip_planner.config import Config
from award_trip_planner.planner import (
    add_days,
    date_range,
    hop_pairs,
    plan_phase1,
    windows,
)


def test_date_helpers():
    assert add_days("2026-09-30", 2) == "2026-10-02"
    assert date_range("2026-10-01", "2026-10-05", 2) == ["2026-10-01", "2026-10-03", "2026-10-05"]
    # end is always included even off-grid
    assert date_range("2026-10-01", "2026-10-04", 2) == ["2026-10-01", "2026-10-03", "2026-10-04"]


def test_hop_pairs_excludes_gmp_nrt():
    cfg = Config()
    kj = hop_pairs(cfg, "KJ")
    assert ("ICN", "NRT") in kj and ("GMP", "HND") in kj and ("GMP", "KIX") in kj
    assert ("GMP", "NRT") not in kj
    jk = hop_pairs(cfg, "JK")
    assert ("NRT", "ICN") in jk and ("NRT", "GMP") not in jk


def test_windows():
    w = windows(Config())
    assert w["t1"] == ("2026-09-25", "2026-10-07")
    # arrive Sep 26 earliest + 3 nights = hop from Sep 29; must leave 2nd country >= 3 nights before Oct 14
    assert w["hop"] == ("2026-09-29", "2026-10-11")
    assert w["ret_a"] == ("2026-10-02", "2026-10-14")
    assert w["ret_b"] == ("2026-10-02", "2026-10-31")


def test_plan_phase1_shape():
    qs = plan_phase1(Config())
    keys = [q.key for q in qs]
    assert len(keys) == len(set(keys))                       # deduped
    assert [q.priority for q in qs] == sorted(q.priority for q in qs)
    t1 = [q for q in qs if q.priority == 1]
    assert all(q.origin == "LAX" and q.adults == 2 and q.kind == "ow" for q in t1)
    assert {q.dest for q in t1} == {"ICN", "NRT", "HND"}
    assert {q.cabin for q in t1} == {"Y", "J"}
    hops = [q for q in qs if q.priority == 2]
    assert all(q.cabin == "Y" and q.adults == 2 for q in hops)
    rets = [q for q in qs if q.priority in (3, 4)]
    assert all(q.dest == "LAX" and q.adults == 1 for q in rets)
