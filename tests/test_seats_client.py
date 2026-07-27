import json
from pathlib import Path

import httpx

from award_trip_planner.cache import Cache
from award_trip_planner.config import Config
from award_trip_planner.seats_client import awards_from_cache, fetch_awards

FIXTURES = Path(__file__).parent / "fixtures"


def make_transport(pages):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["Partner-Authorization"] == "test_key"
        page = pages[1] if request.url.params.get("cursor") else pages[0]
        return httpx.Response(200, json=json.loads((FIXTURES / page).read_text()))

    return httpx.MockTransport(handler), calls


def test_fetch_awards_maps_paginates_and_counts_quota(tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    cfg = Config()
    transport, calls = make_transport(["seats_page1.json", "seats_page2.json"])
    fares = fetch_awards(
        "test_key", [(["LAX"], ["ICN", "NRT", "HND", "KIX"])],
        "2026-09-25", "2026-10-31", cache, cfg, transport=transport,
    )
    assert len(calls) == 2                      # followed the cursor
    # one AwardFare per available cabin: LAX-ICN Y+J, ICN-NRT J, NRT-LAX Y
    keys = {(f.origin, f.dest, f.cabin) for f in fares}
    assert keys == {("LAX", "ICN", "Y"), ("LAX", "ICN", "J"), ("ICN", "NRT", "J"), ("NRT", "LAX", "Y")}
    j = next(f for f in fares if (f.origin, f.dest, f.cabin) == ("LAX", "ICN", "J"))
    assert j.miles == 85_000
    assert j.taxes_usd == round(115.50 * cfg.cad_to_usd, 2)   # cents -> CAD -> USD
    assert j.seats == 2 and j.direct is False and j.airlines == "AC, NH"
    y = next(f for f in fares if (f.origin, f.dest, f.cabin) == ("LAX", "ICN", "Y"))
    assert y.taxes_usd is None                  # raw 0 = unknown
    # cached
    cached = awards_from_cache(cache)
    assert cached is not None
    assert len(cached[0]) == 4


def test_quota_counted_per_http_call(tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    transport, _ = make_transport(["seats_page1.json", "seats_page2.json"])
    fetch_awards("test_key", [(["LAX"], ["ICN"])], "2026-09-25", "2026-10-31",
                 cache, Config(), transport=transport)
    from award_trip_planner.seats_client import today_utc
    assert cache.quota(today_utc()) == 2


def test_fetch_all_programs_by_default(tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=json.loads((FIXTURES / "seats_multi.json").read_text()))

    fares = fetch_awards(
        "test_key", [(["LAX"], ["ICN"])], "2026-09-25", "2026-10-31",
        cache, Config(), transport=httpx.MockTransport(handler),
    )
    # no sources filter is sent at all -> the API returns every program
    assert "sources" not in seen["params"]
    by_program = {(f.program, f.cabin): f for f in fares}
    assert by_program[("aeroplan", "Y")].miles == 55_000
    assert by_program[("virginatlantic", "J")].miles == 60_000
    # USD taxes are not run through the CAD conversion
    assert by_program[("virginatlantic", "J")].taxes_usd == 43.00


def test_sources_filter_still_available(tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=json.loads((FIXTURES / "seats_multi.json").read_text()))

    fetch_awards(
        "test_key", [(["LAX"], ["ICN"])], "2026-09-25", "2026-10-31",
        cache, Config(), transport=httpx.MockTransport(handler), sources="aeroplan",
    )
    assert seen["params"]["sources"] == "aeroplan"


def test_rate_limit_is_retried_with_backoff(tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    calls, waits = [], []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "2"}, json={})
        return httpx.Response(200, json=json.loads((FIXTURES / "seats_multi.json").read_text()))

    fares = fetch_awards(
        "test_key", [(["LAX"], ["ICN"])], "2026-09-25", "2026-10-31",
        cache, Config(), transport=httpx.MockTransport(handler),
        sleep=waits.append,
    )
    assert len(calls) == 2          # retried after the 429
    assert waits == [2.0]           # honoured Retry-After
    assert fares                    # and the retry's data was used
    # both attempts counted against the daily quota, because the server counted them
    from award_trip_planner.seats_client import today_utc
    assert cache.quota(today_utc()) == 2


def test_quota_guard_stops_before_exceeding_the_daily_budget(tmp_path):
    from award_trip_planner.seats_client import DAILY_QUOTA, QuotaExhausted, today_utc

    cache = Cache(tmp_path / "c.sqlite")
    for _ in range(DAILY_QUOTA):
        cache.bump_quota(today_utc())
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, json={"data": [], "count": 0, "hasMore": False})

    try:
        fetch_awards("test_key", [(["LAX"], ["ICN"])], "2026-09-25", "2026-10-31",
                     cache, Config(), transport=httpx.MockTransport(handler))
        raise AssertionError("expected QuotaExhausted")
    except QuotaExhausted as e:
        assert "resets at 00:00 UTC" in str(e)
    assert calls == [], "no HTTP call may be made once the quota is spent"
