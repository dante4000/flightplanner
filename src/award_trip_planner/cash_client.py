from __future__ import annotations

import time
from dataclasses import dataclass

from .cache import Cache
from .config import Config
from .models import CashFare, cash_from_dict, fare_to_dict

SEAT_NAME = {"Y": "economy", "J": "business"}
FAIL_BACKOFF_S = 30 * 60


@dataclass(frozen=True)
class CashQuery:
    kind: str                  # "ow" | "rt"
    origin: str
    dest: str
    depart_date: str
    return_date: str | None
    cabin: str
    adults: int
    priority: int

    @property
    def key(self) -> str:
        return f"{self.kind}|{self.origin}|{self.dest}|{self.depart_date}|{self.return_date}|{self.cabin}|{self.adults}"


def _google_fetch(q: CashQuery) -> tuple[float, str]:
    """Live Google Flights fetch. Raises on any failure."""
    from fast_flights import FlightQuery, Passengers, create_query, get_flights

    from . import gflights_patch

    gflights_patch.install()
    flights = [FlightQuery(date=q.depart_date, from_airport=q.origin, to_airport=q.dest)]
    trip = "one-way"
    if q.kind == "rt":
        flights.append(FlightQuery(date=q.return_date, from_airport=q.dest, to_airport=q.origin))
        trip = "round-trip"
    query = create_query(
        flights=flights, seat=SEAT_NAME[q.cabin], trip=trip,
        passengers=Passengers(adults=q.adults), currency="USD",
    )
    res = get_flights(query)
    priced = [f for f in res if isinstance(f.price, (int, float)) and f.price > 0]
    if not priced:
        raise RuntimeError("no priced itineraries")
    best = min(priced, key=lambda f: f.price)
    airline = ", ".join(best.airlines) if isinstance(best.airlines, list) else str(best.airlines)
    return float(best.price), airline


def cash_from_cache(cache: Cache, q: CashQuery) -> CashFare | None:
    row = cache.get_stale("cash", q.key)
    if row is None or row[0] is None or row[0].get("failed"):
        return None
    return cash_from_dict(row[0])


def fetch_cash(
    q: CashQuery, cache: Cache, cfg: Config,
    now: float | None = None, fetcher=None,
) -> CashFare | None:
    now = time.time() if now is None else now
    ttl = cfg.cash_ttl_hours * 3600
    cached = cache.get("cash", q.key, max_age_s=ttl, now=now)
    if cached is not None:
        return None if cached.get("failed") else cash_from_dict(cached)
    stale = cache.get_stale("cash", q.key)
    if stale and stale[0].get("failed") and now - stale[1] < FAIL_BACKOFF_S:
        return None
    fetcher = fetcher or _google_fetch
    try:
        total, airline = fetcher(q)
    except Exception:
        cache.put("cash", q.key, {"failed": True}, now=now)
        return None
    fare = CashFare(
        kind=q.kind, origin=q.origin, dest=q.dest, depart_date=q.depart_date,
        return_date=q.return_date, cabin=q.cabin, adults=q.adults,
        total_usd=total, airline=airline, fetched_at=now,
    )
    cache.put("cash", q.key, fare_to_dict(fare), now=now)
    return fare
