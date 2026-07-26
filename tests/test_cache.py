from award_trip_planner.cache import Cache


def test_put_get_ttl(tmp_path):
    c = Cache(tmp_path / "c.sqlite")
    c.put("cash", "k1", {"total": 294.0}, now=1000.0)
    assert c.get("cash", "k1", max_age_s=60, now=1030.0) == {"total": 294.0}
    assert c.get("cash", "k1", max_age_s=60, now=2000.0) is None
    assert c.get_stale("cash", "k1") == ({"total": 294.0}, 1000.0)
    assert c.get("cash", "missing", max_age_s=60, now=0) is None
    assert c.keys("cash") == ["k1"]


def test_overwrite(tmp_path):
    c = Cache(tmp_path / "c.sqlite")
    c.put("ns", "k", 1, now=1.0)
    c.put("ns", "k", 2, now=2.0)
    assert c.get_stale("ns", "k") == (2, 2.0)


def test_quota(tmp_path):
    c = Cache(tmp_path / "c.sqlite")
    assert c.quota("2026-07-26") == 0
    assert c.bump_quota("2026-07-26") == 1
    assert c.bump_quota("2026-07-26") == 2
    assert c.quota("2026-07-26") == 2
    assert c.quota("2026-07-27") == 0


def test_overrides(tmp_path):
    c = Cache(tmp_path / "c.sqlite")
    c.set_override("ICN", "NRT", "2026-10-05", "Y", 150.0)
    c.set_override("ICN", "NRT", "2026-10-05", "Y", 140.0)
    assert c.overrides() == [
        {"origin": "ICN", "dest": "NRT", "date": "2026-10-05", "cabin": "Y", "price_pp": 140.0}
    ]
    c.set_override("ICN", "NRT", "2026-10-05", "Y", None)
    assert c.overrides() == []
