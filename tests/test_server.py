import time
from pathlib import Path

from fastapi.testclient import TestClient

from award_trip_planner.models import AwardFare, CashFare
from award_trip_planner.server import create_app


def fake_award_fetcher(**kw):
    return [AwardFare("LAX", "ICN", "2026-10-01", "Y", 55_000, 50.0, 4, True, "AC", "2026-07-25T00:00:00Z")]


def fake_cash_fetcher(q, **kw):
    return CashFare(q.kind, q.origin, q.dest, q.depart_date, q.return_date,
                    q.cabin, q.adults, 1000.0, "Fake Air", 1_000_000.0)


def make_client(tmp_path: Path) -> TestClient:
    app = create_app(tmp_path, "test_key",
                     award_fetcher=fake_award_fetcher, cash_fetcher=fake_cash_fetcher)
    return TestClient(app)


def test_config_roundtrip(tmp_path):
    c = make_client(tmp_path)
    assert c.get("/api/config").json()["points_budget"] == 100_000
    r = c.put("/api/config", json={"points_budget": 90_000})
    assert r.json()["points_budget"] == 90_000
    assert c.get("/api/config").json()["points_budget"] == 90_000


def test_refresh_and_results(tmp_path):
    c = make_client(tmp_path)
    assert c.post("/api/refresh").json()["started"] is True
    # TestClient runs the thread; poll status until idle
    for _ in range(200):
        s = c.get("/api/status").json()
        if not s["running"]:
            break
        time.sleep(0.05)
    assert s["errors"] == []
    r = c.get("/api/results").json()
    assert "views" in r and "award_matrix" in r and "cash_matrix" in r
    assert r["award_matrix"][0]["miles"] == 55_000


def test_override_endpoint(tmp_path):
    c = make_client(tmp_path)
    r = c.put("/api/override", json={
        "origin": "ICN", "dest": "NRT", "date": "2026-10-05", "cabin": "Y", "price_pp": 111.0})
    assert r.json() == [{"origin": "ICN", "dest": "NRT", "date": "2026-10-05", "cabin": "Y", "price_pp": 111.0}]
    r = c.put("/api/override", json={
        "origin": "ICN", "dest": "NRT", "date": "2026-10-05", "cabin": "Y", "price_pp": None})
    assert r.json() == []


def test_second_refresh_conflicts_while_running(tmp_path):
    import threading as _t

    release = _t.Event()
    entered = _t.Event()

    def slow_cash_fetcher(q, **kw):
        entered.set()
        release.wait(5)
        return fake_cash_fetcher(q, **kw)

    app = create_app(tmp_path, "test_key",
                     award_fetcher=fake_award_fetcher, cash_fetcher=slow_cash_fetcher)
    c = TestClient(app)
    assert c.post("/api/refresh").json()["started"] is True
    assert entered.wait(5), "refresh thread never started fetching"
    assert c.post("/api/refresh").status_code == 409
    release.set()
    for _ in range(200):
        if not c.get("/api/status").json()["running"]:
            break
        time.sleep(0.05)
    assert c.get("/api/status").json()["running"] is False


def test_override_appears_in_cash_matrix(tmp_path):
    c = make_client(tmp_path)
    c.put("/api/override", json={
        "origin": "ICN", "dest": "NRT", "date": "2026-10-05", "cabin": "Y", "price_pp": 111.0})
    rows = c.get("/api/results").json()["cash_matrix"]
    hit = [r for r in rows
           if (r["origin"], r["dest"], r["date"], r["cabin"]) == ("ICN", "NRT", "2026-10-05", "Y")]
    assert len(hit) == 1
    assert hit[0]["price_pp"] == 111.0
    assert hit[0]["manual"] is True
    assert hit[0]["airline"] == "manual"


def test_override_replaces_fetched_row(tmp_path):
    c = make_client(tmp_path)
    c.post("/api/refresh")
    for _ in range(200):
        if not c.get("/api/status").json()["running"]:
            break
        time.sleep(0.05)
    rows = c.get("/api/results").json()["cash_matrix"]
    assert rows, "expected fetched cash rows after refresh"
    r0 = rows[0]
    c.put("/api/override", json={
        "origin": r0["origin"], "dest": r0["dest"], "date": r0["date"],
        "cabin": r0["cabin"], "price_pp": 42.0})
    rows2 = c.get("/api/results").json()["cash_matrix"]
    key = (r0["origin"], r0["dest"], r0["date"], r0["cabin"])
    hits = [r for r in rows2 if (r["origin"], r["dest"], r["date"], r["cabin"]) == key]
    assert len(hits) == 1
    assert hits[0]["price_pp"] == 42.0 and hits[0]["manual"] is True
    assert len(rows2) == len(rows)
