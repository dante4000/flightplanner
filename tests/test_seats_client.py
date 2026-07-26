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
        assert request.url.params["sources"] == "aeroplan"
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
