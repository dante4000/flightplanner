from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from itertools import product

# Airports we price. Region drives the date-line offset only.
_REGIONS: dict[str, str] = {
    "LAX": "americas", "SFO": "americas", "SEA": "americas", "JFK": "americas",
    "ORD": "americas", "YVR": "americas", "YYZ": "americas", "DFW": "americas",
    "ICN": "asia", "GMP": "asia", "PUS": "asia",
    "NRT": "asia", "HND": "asia", "KIX": "asia", "ITM": "asia", "FUK": "asia",
    "TPE": "asia", "HKG": "asia", "SIN": "asia", "BKK": "asia", "PVG": "asia",
    "PEK": "asia", "CAN": "asia", "MNL": "asia", "SGN": "asia", "HAN": "asia",
    "SYD": "oceania", "MEL": "oceania", "AKL": "oceania",
}
_WESTBOUND_TO = {"asia", "oceania"}


def region_of(airport: str) -> str:
    return _REGIONS.get(airport.upper(), "unknown")


def arrival_offset_days(origin: str, dest: str) -> int:
    """Date-line approximation: westbound trans-Pacific arrives the next day."""
    o, d = region_of(origin), region_of(dest)
    if o == "americas" and d in _WESTBOUND_TO:
        return 1
    return 0


def add_days(date: str, n: int) -> str:
    return (dt.date.fromisoformat(date) + dt.timedelta(days=n)).isoformat()


def date_range(start: str, end: str, step: int = 1) -> list[str]:
    out: list[str] = []
    d, e = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    while d <= e:
        out.append(d.isoformat())
        d += dt.timedelta(days=step)
    if out and out[-1] != end:
        out.append(end)
    return out


def arrival_date(depart_date: str, origin: str, dest: str) -> str:
    return add_days(depart_date, arrival_offset_days(origin, dest))


@dataclass(frozen=True)
class Stop:
    city: str
    airports: tuple[str, ...]
    min_nights: int = 0
    max_nights: int = 0        # 0 = unbounded


@dataclass(frozen=True)
class Trip:
    stops: tuple[Stop, ...]
    depart_start: str
    depart_end: str
    arrive_by: str
    party_size: int = 1
    cabins: tuple[str, ...] = ("Y", "J")

    def segments(self) -> list[tuple[Stop, Stop]]:
        return [(self.stops[i], self.stops[i + 1]) for i in range(len(self.stops) - 1)]

    def validate(self) -> None:
        if len(self.stops) < 2:
            raise ValueError("a trip needs at least 2 stops")
        if not 1 <= self.party_size <= 10:
            raise ValueError("party_size must be between 1 and 10")
        if self.depart_start > self.depart_end:
            raise ValueError("depart window is inverted")
        for s in self.stops:
            if not s.airports:
                raise ValueError(f"stop {s.city} has no airports")
            if s.max_nights and s.max_nights < s.min_nights:
                raise ValueError(f"stop {s.city}: max_nights < min_nights")


def _repr_airport(stop: Stop) -> str:
    """The airport used for date-line math; any airport in a city shares its region."""
    return stop.airports[0]


def enumerate_date_paths(
    trip: Trip, step: int = 1, cap: int = 20_000
) -> tuple[list[tuple[str, ...]], bool]:
    """Depart dates per segment satisfying windows, night bounds and the deadline."""
    trip.validate()
    segs = trip.segments()
    paths: list[tuple[str, ...]] = []
    capped = False

    def walk(idx: int, chosen: list[str]) -> None:
        nonlocal capped
        if capped:
            return
        if idx == len(segs):
            paths.append(tuple(chosen))
            if len(paths) >= cap:
                capped = True
            return
        origin, dest = segs[idx]
        if idx == 0:
            candidates = date_range(trip.depart_start, trip.depart_end, step)
        else:
            prev_origin, prev_dest = segs[idx - 1]
            landed = arrival_date(
                chosen[idx - 1], _repr_airport(prev_origin), _repr_airport(prev_dest)
            )
            lo = add_days(landed, dest_stop_min(trip, idx))
            hi_nights = dest_stop_max(trip, idx)
            hi = add_days(landed, hi_nights) if hi_nights else trip.arrive_by
            if lo > hi:
                return
            candidates = date_range(lo, hi, step)
        for d in candidates:
            if arrival_date(d, _repr_airport(origin), _repr_airport(dest)) > trip.arrive_by:
                continue
            walk(idx + 1, chosen + [d])
            if capped:
                return

    walk(0, [])
    return paths, capped


def dest_stop_min(trip: Trip, seg_idx: int) -> int:
    """min_nights of the stop you are sitting in before departing on segment seg_idx."""
    return trip.stops[seg_idx].min_nights


def dest_stop_max(trip: Trip, seg_idx: int) -> int:
    return trip.stops[seg_idx].max_nights
