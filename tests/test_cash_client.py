from award_trip_planner.cache import Cache
from award_trip_planner.cash_client import CashQuery, cash_from_cache, fetch_cash
from award_trip_planner.config import Config


def q_ow(**kw):
    base = dict(kind="ow", origin="ICN", dest="NRT", depart_date="2026-10-05",
                return_date=None, cabin="Y", adults=2, priority=2)
    base.update(kw)
    return CashQuery(**base)


def test_key():
    assert q_ow().key == "ow|ICN|NRT|2026-10-05|None|Y|2"


def test_fetch_uses_fetcher_and_caches(tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    calls = []

    def fake_fetcher(q):
        calls.append(q)
        return 294.0, "ZIPAIR Tokyo"

    got = fetch_cash(q_ow(), cache, Config(), now=1000.0, fetcher=fake_fetcher)
    assert got.total_usd == 294.0 and got.airline == "ZIPAIR Tokyo"
    assert got.adults == 2 and got.per_person() == 147.0
    # second call inside TTL: served from cache, fetcher not called again
    again = fetch_cash(q_ow(), cache, Config(), now=2000.0, fetcher=fake_fetcher)
    assert again == got
    assert len(calls) == 1


def test_fetch_failure_returns_none_and_backs_off(tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    calls = []

    def broken(q):
        calls.append(q)
        raise RuntimeError("google changed")

    assert fetch_cash(q_ow(), cache, Config(), now=1000.0, fetcher=broken) is None
    assert fetch_cash(q_ow(), cache, Config(), now=1100.0, fetcher=broken) is None
    assert len(calls) == 1          # backoff marker suppressed the retry
    assert cash_from_cache(cache, q_ow()) is None


def test_patch_installs():
    import fast_flights.parser as P

    from award_trip_planner import gflights_patch

    gflights_patch.install()
    assert P.parse_js.__name__ == "tolerant_parse_js"
    gflights_patch.install()  # idempotent
    assert P.parse_js.__name__ == "tolerant_parse_js"


def _mk_payload():
    sf = [None] * 22
    sf[3], sf[4] = "ICN", "Incheon"
    sf[5], sf[6] = "Narita", "NRT"
    sf[8], sf[10] = [9, 0], [11, 15]
    sf[11], sf[17] = 135, "789"
    sf[20], sf[21] = [2026, 10, 1], [2026, 10, 1]
    flight = [None] * 23
    flight[0], flight[1], flight[2] = "ZG", ["ZIPAIR Tokyo"], [sf]
    flight[22] = [None] * 9
    flight[22][7], flight[22][8] = 100, 90
    good = [flight, [[None, 294]]]
    bad = [flight, [[]]]  # unpriced row -> must be skipped, not crash
    payload = [None] * 8
    payload[3] = [[good, bad]]
    payload[7] = [None, [[["STAR_ALLIANCE", "Star Alliance"]], [["ZG", "ZIPAIR"]]]]
    return payload


def test_tolerant_parser_skips_unpriced_rows():
    import json

    from award_trip_planner.gflights_patch import tolerant_parse_js

    js = "data:" + json.dumps(_mk_payload()) + ", sideChannel: {}})"
    res = tolerant_parse_js(js)
    assert len(res) == 1
    f = res[0]
    assert f.price == 294 and f.airlines == ["ZIPAIR Tokyo"]
    sg = f.flights[0]
    assert (sg.from_airport.code, sg.to_airport.code) == ("ICN", "NRT")
