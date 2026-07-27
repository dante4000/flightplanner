from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass(frozen=True)
class AwardFare:
    origin: str
    dest: str
    date: str
    cabin: str                 # "Y" | "J"
    miles: int                 # per person
    taxes_usd: float | None    # per person; None = unknown, engine substitutes default
    seats: int
    direct: bool
    airlines: str
    updated_at: str
    program: str = ""          # seats.aero Source, e.g. "aeroplan"


@dataclass(frozen=True)
class CashFare:
    kind: str                  # "ow" | "rt"
    origin: str
    dest: str
    depart_date: str
    return_date: str | None
    cabin: str
    adults: int
    total_usd: float           # total for `adults`
    airline: str
    fetched_at: float
    manual: bool = False

    def per_person(self) -> float:
        return self.total_usd / self.adults


@dataclass(frozen=True)
class Leg:
    origin: str
    dest: str
    date: str
    cabin: str


@dataclass(frozen=True)
class Option:
    product: str                                   # cash_ow | cash_rt | award_ow | award_stopover
    legs: tuple[Leg, ...]
    cash_pp: float
    points_pp: int
    airline: str
    award_seat_legs: tuple[tuple[Leg, int], ...] = ()
    flags: tuple[str, ...] = ()


@dataclass
class BookingLine:
    person: str                # "A" | "B"
    product: str
    legs: list[dict]
    cash_usd: float
    points: int
    airline: str
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Bundle:
    direction: str             # "KJ" | "JK"
    total_cash_usd: float
    total_points: int
    cpp: float | None
    lines: list[BookingLine]
    flags: list[str]
    summary: str

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def fare_to_dict(x) -> dict:
    d = asdict(x)
    d["_type"] = type(x).__name__
    return d


def award_from_dict(d: dict) -> AwardFare:
    d = {k: v for k, v in d.items() if k != "_type"}
    return AwardFare(**d)


def cash_from_dict(d: dict) -> CashFare:
    d = {k: v for k, v in d.items() if k != "_type"}
    return CashFare(**d)
