import json
from pathlib import Path

from award_trip_planner.config import Config


def test_defaults():
    cfg = Config()
    assert cfg.outbound_start == "2026-09-25"
    assert cfg.outbound_end == "2026-10-07"
    assert cfg.return_a_deadline == "2026-10-14"
    assert cfg.return_b_deadline == "2026-10-31"
    assert cfg.korea_gateways == ["ICN"]
    assert cfg.japan_gateways == ["NRT", "HND"]
    assert cfg.korea_airports == ["ICN", "GMP"]
    assert cfg.japan_airports == ["NRT", "HND", "KIX"]
    assert cfg.points_budget == 100_000
    assert cfg.min_nights_first == 3 and cfg.min_nights_second == 3


def test_load_missing_file_gives_defaults(tmp_path: Path):
    cfg = Config.load(tmp_path / "nope.json")
    assert cfg.points_budget == 100_000


def test_save_load_roundtrip_ignores_unknown_keys(tmp_path: Path):
    p = tmp_path / "config.json"
    cfg = Config()
    cfg.points_budget = 80_000
    cfg.save(p)
    raw = json.loads(p.read_text())
    raw["bogus_key"] = 1
    p.write_text(json.dumps(raw))
    cfg2 = Config.load(p)
    assert cfg2.points_budget == 80_000
    assert not hasattr(cfg2, "bogus_key")
