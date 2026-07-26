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
