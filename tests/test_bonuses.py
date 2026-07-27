from award_trip_planner.bonuses import (
    Bonus,
    active_bonuses,
    bonus_lookup_from,
    manual_bonuses,
    parse_bonus_rows,
    set_manual_bonus,
)
from award_trip_planner.cache import Cache

HTML = """
<table><tbody>
<tr><td>American Express</td><td>Virgin Atlantic Flying Club</td><td>30%</td><td>2026-08-31</td></tr>
<tr><td>American Express</td><td>Air Canada Aeroplan</td><td>20%</td><td>2026-09-15</td></tr>
<tr><td>Chase Ultimate Rewards</td><td>Air France/KLM Flying Blue</td><td>25%</td><td>2026-08-01</td></tr>
<tr><td>Citi ThankYou</td><td>Some Unmapped Airline</td><td>40%</td><td>2026-08-01</td></tr>
</tbody></table>
"""


def test_parse_maps_names_to_ids_and_skips_unmapped():
    rows = parse_bonus_rows(HTML)
    got = {(b.currency, b.program): (b.pct, b.expires) for b in rows}
    assert got[("amex_mr", "virginatlantic")] == (0.30, "2026-08-31")
    assert got[("amex_mr", "aeroplan")] == (0.20, "2026-09-15")
    assert got[("chase_ur", "flyingblue")] == (0.25, "2026-08-01")
    # unknown currency or unmapped airline is skipped, not guessed
    assert all(b.currency in ("amex_mr", "chase_ur") for b in rows)
    assert all(b.source == "scrape" for b in rows)


def test_manual_overrides_beat_scrape(tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    set_manual_bonus(cache, "amex_mr", "virginatlantic", 0.50, "2026-12-31")
    bonuses, notes = active_bonuses(
        cache, today="2026-07-26", fetcher=lambda: HTML, now=1000.0
    )
    pairs = {(b.currency, b.program): b for b in bonuses}
    assert pairs[("amex_mr", "virginatlantic")].pct == 0.50
    assert pairs[("amex_mr", "virginatlantic")].source == "manual"
    assert pairs[("amex_mr", "aeroplan")].pct == 0.20   # scrape still present
    assert notes == []


def test_expired_rows_dropped(tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    bonuses, _ = active_bonuses(
        cache, today="2026-09-01", fetcher=lambda: HTML, now=1000.0
    )
    pairs = {(b.currency, b.program) for b in bonuses}
    assert ("amex_mr", "virginatlantic") not in pairs   # expired 2026-08-31
    assert ("amex_mr", "aeroplan") in pairs             # expires 2026-09-15


def test_scrape_failure_degrades_to_manual_with_a_note(tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    set_manual_bonus(cache, "amex_mr", "aeroplan", 0.15, None)

    def broken():
        raise RuntimeError("tracker moved")

    bonuses, notes = active_bonuses(cache, today="2026-07-26", fetcher=broken, now=1000.0)
    assert [(b.currency, b.program, b.pct) for b in bonuses] == [("amex_mr", "aeroplan", 0.15)]
    assert any("bonus" in n.lower() for n in notes)


def test_scrape_is_cached(tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    calls = []

    def counting():
        calls.append(1)
        return HTML

    active_bonuses(cache, today="2026-07-26", fetcher=counting, now=1000.0)
    active_bonuses(cache, today="2026-07-26", fetcher=counting, now=2000.0)
    assert len(calls) == 1          # inside the 12h TTL


def test_manual_delete_and_lookup(tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    set_manual_bonus(cache, "amex_mr", "aeroplan", 0.15, None)
    assert len(manual_bonuses(cache)) == 1
    set_manual_bonus(cache, "amex_mr", "aeroplan", None, None)
    assert manual_bonuses(cache) == []

    look = bonus_lookup_from([Bonus("amex_mr", "aeroplan", 0.30, None, "manual")])
    assert look("amex_mr", "aeroplan") == 0.30
    assert look("chase_ur", "aeroplan") == 0.0
    assert look("amex_mr", "singapore") == 0.0


def test_corrupt_cache_rows_are_skipped_not_raised(tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    # a row written by a future/older schema, plus one good row
    cache.put("bonuses", "manual", [
        {"currency": "amex_mr", "program": "aeroplan", "pct": 0.2,
         "expires": None, "source": "manual"},
        {"currency": "amex_mr", "program": "singapore", "pct": 0.4,
         "expires": None, "source": "manual", "unexpected_new_field": 1},
    ])
    got = manual_bonuses(cache)
    assert [(b.program, b.pct) for b in got] == [("aeroplan", 0.2)]

    bonuses, notes = active_bonuses(cache, today="2026-07-26",
                                    fetcher=lambda: HTML, now=1000.0)
    assert ("amex_mr", "aeroplan") in {(b.currency, b.program) for b in bonuses}


def test_zero_parsed_rows_emits_a_note(tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    bonuses, notes = active_bonuses(cache, today="2026-07-26",
                                    fetcher=lambda: "<html>no tables here</html>",
                                    now=1000.0)
    assert bonuses == []
    assert any("no parsable rows" in n for n in notes)
