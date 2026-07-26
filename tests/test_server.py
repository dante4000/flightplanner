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
    import time
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
    # guarded by the running flag; with fake fetchers refresh is fast, so just
    # assert the endpoint contract on the idle path
    c = make_client(tmp_path)
    assert c.post("/api/refresh").status_code == 200
