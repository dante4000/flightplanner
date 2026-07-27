from award_trip_planner.cli import format_result, korea_japan_trip, load_from_cache
from award_trip_planner.cache import Cache
from award_trip_planner.config import Config
from award_trip_planner.models import AwardFare, CashFare, fare_to_dict
from award_trip_planner.products import build_tables
from award_trip_planner.solver import solve


def test_saved_trip_shape():
    t = korea_japan_trip("Tokyo", party=2)
    assert [s.city for s in t.stops] == ["Los Angeles", "Seoul", "Tokyo", "Los Angeles"]
    assert t.party_size == 2
    t2 = korea_japan_trip("Osaka", party=1)
    assert [s.city for s in t2.stops] == ["Los Angeles", "Seoul", "Osaka", "Los Angeles"]
    assert t2.stops[2].airports == ("KIX",)


def test_load_from_cache_roundtrip(tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    a = AwardFare("LAX", "ICN", "2026-10-01", "Y", 50_000, 40.0, 4, True, "AC",
                  "2026-07-25T00:00:00Z", "aeroplan")
    c = CashFare("ow", "LAX", "ICN", "2026-10-01", None, "Y", 2, 1000.0, "X", 1_000_000.0)
    cache.put("awards", "all", [fare_to_dict(a)])
    cache.put("cash", "ow|LAX|ICN|2026-10-01|None|Y|2", fare_to_dict(c))
    awards, cash = load_from_cache(cache)
    assert awards == [a]
    assert cash == [c]


def test_format_result_is_readable():
    from award_trip_planner.trip import Stop, Trip
    LA = Stop(city="Los Angeles", airports=("LAX",))
    S = Stop(city="Seoul", airports=("ICN",), min_nights=1, max_nights=3)
    trip = Trip(stops=(LA, S, LA), depart_start="2026-10-01", depart_end="2026-10-01",
                arrive_by="2026-10-20", party_size=1)
    cash = [
        CashFare("ow", "LAX", "ICN", "2026-10-01", None, "Y", 1, 600.0, "T", 1_000_000.0),
        CashFare("ow", "ICN", "LAX", "2026-10-04", None, "Y", 1, 700.0, "T", 1_000_000.0),
    ]
    tables = build_tables([], cash, [], Config())
    r = solve(trip, tables, {"amex_mr": 0, "chase_ur": 0, "aeroplan_fixed": 0},
              [], Config(), now=1_000_000.0)
    text = format_result(r, limit=5)
    assert "$1,300" in text or "$1300" in text
    assert "LAX" in text and "ICN" in text
