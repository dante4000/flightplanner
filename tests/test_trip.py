import pytest

from award_trip_planner.trip import (
    Stop,
    Trip,
    add_days,
    arrival_date,
    arrival_offset_days,
    date_range,
    enumerate_date_paths,
    region_of,
)

LAX = Stop(city="Los Angeles", airports=("LAX",))
SEOUL = Stop(city="Seoul", airports=("ICN", "GMP"), min_nights=3, max_nights=7)
TOKYO = Stop(city="Tokyo", airports=("NRT", "HND"), min_nights=3, max_nights=7)


def trip(**kw):
    base = dict(
        stops=(LAX, SEOUL, TOKYO, LAX),
        depart_start="2026-09-25",
        depart_end="2026-09-27",
        arrive_by="2026-10-20",
        party_size=2,
    )
    base.update(kw)
    return Trip(**base)


def test_date_helpers():
    assert add_days("2026-09-30", 2) == "2026-10-02"
    assert date_range("2026-10-01", "2026-10-05", 2) == ["2026-10-01", "2026-10-03", "2026-10-05"]
    assert date_range("2026-10-01", "2026-10-04", 2) == ["2026-10-01", "2026-10-03", "2026-10-04"]


def test_regions_and_date_line():
    assert region_of("LAX") == "americas"
    assert region_of("ICN") == "asia"
    assert region_of("ZZZ") == "unknown"
    # westbound trans-Pacific loses a day; eastbound and intra-region do not
    assert arrival_offset_days("LAX", "ICN") == 1
    assert arrival_offset_days("ICN", "LAX") == 0
    assert arrival_offset_days("ICN", "NRT") == 0
    assert arrival_offset_days("LAX", "ZZZ") == 0
    assert arrival_date("2026-09-25", "LAX", "ICN") == "2026-09-26"
    assert arrival_date("2026-10-10", "ICN", "LAX") == "2026-10-10"


def test_segments():
    segs = trip().segments()
    assert [(a.city, b.city) for a, b in segs] == [
        ("Los Angeles", "Seoul"), ("Seoul", "Tokyo"), ("Tokyo", "Los Angeles")
    ]


def test_validation():
    with pytest.raises(ValueError, match="at least 2 stops"):
        trip(stops=(LAX,)).validate()
    with pytest.raises(ValueError, match="party_size"):
        trip(party_size=0).validate()
    with pytest.raises(ValueError, match="party_size"):
        trip(party_size=11).validate()
    with pytest.raises(ValueError, match="depart window"):
        trip(depart_start="2026-09-28", depart_end="2026-09-25").validate()
    bad = Stop(city="X", airports=("XXX",), min_nights=5, max_nights=2)
    with pytest.raises(ValueError, match="max_nights"):
        trip(stops=(LAX, bad, LAX)).validate()
    trip().validate()  # the good one does not raise


def _nights(a: str, b: str) -> int:
    import datetime as dt

    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days


def test_enumerate_respects_nights_and_deadline():
    paths, capped = enumerate_date_paths(trip(), step=1)
    assert not capped
    assert paths
    for p in paths:
        assert len(p) == 3
        assert "2026-09-25" <= p[0] <= "2026-09-27"
        # arrive Seoul the next day, stay 3-7 nights before departing to Tokyo
        assert 3 <= _nights(arrival_date(p[0], "LAX", "ICN"), p[1]) <= 7
        assert 3 <= _nights(arrival_date(p[1], "ICN", "NRT"), p[2]) <= 7
        assert arrival_date(p[2], "NRT", "LAX") <= "2026-10-20"


def test_deadline_prunes_everything():
    paths, _ = enumerate_date_paths(trip(arrive_by="2026-09-26"), step=1)
    assert paths == []


def test_cap_is_reported_not_hidden():
    paths, capped = enumerate_date_paths(trip(), step=1, cap=5)
    assert capped is True
    assert len(paths) == 5
