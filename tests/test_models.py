from award_trip_planner.models import (
    AwardFare,
    CashFare,
    Leg,
    award_from_dict,
    cash_from_dict,
    fare_to_dict,
)


def test_award_roundtrip():
    a = AwardFare(
        origin="LAX", dest="ICN", date="2026-10-01", cabin="J",
        miles=85_000, taxes_usd=None, seats=2, direct=True,
        airlines="AC", updated_at="2026-07-25T00:00:00Z",
    )
    assert award_from_dict(fare_to_dict(a)) == a


def test_cash_roundtrip_and_per_person():
    c = CashFare(
        kind="ow", origin="ICN", dest="NRT", depart_date="2026-10-05",
        return_date=None, cabin="Y", adults=2, total_usd=294.0,
        airline="ZIPAIR Tokyo", fetched_at=1_753_500_000.0,
    )
    assert c.per_person() == 147.0
    assert cash_from_dict(fare_to_dict(c)) == c


def test_leg_is_hashable():
    assert len({Leg("A", "B", "2026-10-01", "Y"), Leg("A", "B", "2026-10-01", "Y")}) == 1
