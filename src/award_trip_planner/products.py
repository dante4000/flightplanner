from __future__ import annotations

import time
from dataclasses import dataclass, field
from itertools import product as _iproduct

from .config import Config
from .models import AwardFare, CashFare, Leg
from .programs import program_for
from .trip import Stop


@dataclass
class Tables:
    awards: dict = field(default_factory=dict)   # (o,d,date,cabin,program) -> AwardFare
    cash: dict = field(default_factory=dict)     # (o,d,date,cabin) -> CashFare
    # index so candidate lookup is O(matches), not a scan of every award row
    awards_by_od: dict = field(default_factory=dict)  # (o,d,date,cabin) -> [AwardFare]


@dataclass(frozen=True)
class SegCandidate:
    kind: str                       # "cash" | "award"
    legs: tuple[Leg, ...]
    cash_usd: float                 # party total
    miles: int                      # party total, 0 for cash
    program: str                    # "" for cash
    cabin: str
    airlines: str
    seats: int                      # min across legs; 0 = unknown
    flags: tuple[str, ...] = ()
    spans: int = 1                  # how many trip segments this covers


def fee_usd(cfg: Config) -> float:
    return round(cfg.aeroplan_partner_fee_cad * cfg.cad_to_usd, 2)


def default_taxes(o: str, d: str, cfg: Config) -> float:
    return cfg.default_award_taxes_usd if "LAX" in (o, d) else cfg.default_hop_award_taxes_usd


def build_tables(award_fares, cash_fares, overrides, cfg: Config) -> Tables:
    t = Tables()
    for a in award_fares:
        k = (a.origin, a.dest, a.date, a.cabin, a.program)
        if k not in t.awards or a.miles < t.awards[k].miles:
            t.awards[k] = a
    for c in cash_fares:
        if c.kind != "ow":
            continue
        k = (c.origin, c.dest, c.depart_date, c.cabin)
        if k not in t.cash or c.per_person() < t.cash[k].per_person():
            t.cash[k] = c
    for ov in overrides:
        t.cash[(ov["origin"], ov["dest"], ov["date"], ov["cabin"])] = CashFare(
            kind="ow", origin=ov["origin"], dest=ov["dest"], depart_date=ov["date"],
            return_date=None, cabin=ov["cabin"], adults=1, total_usd=ov["price_pp"],
            airline="manual", fetched_at=time.time(), manual=True,
        )
    for (o, d, date, cabin, _prog), fare in t.awards.items():
        t.awards_by_od.setdefault((o, d, date, cabin), []).append(fare)
    return t


def _pairs(a: Stop, b: Stop):
    return list(_iproduct(a.airports, b.airports))


def cash_candidates(tables, a: Stop, b: Stop, date, cabins, party, cfg, now) -> list[SegCandidate]:
    out = []
    for o, d in _pairs(a, b):
        for cabin in cabins:
            fare = tables.cash.get((o, d, date, cabin))
            if fare is None:
                continue
            flags = ()
            if fare.manual:
                flags = ("manual-price",)
            elif now - fare.fetched_at > cfg.cash_ttl_hours * 3600:
                flags = ("stale-cash",)
            out.append(SegCandidate(
                kind="cash", legs=(Leg(o, d, date, cabin),),
                cash_usd=round(fare.per_person() * party, 2), miles=0, program="",
                cabin=cabin, airlines=fare.airline, seats=party, flags=flags,
            ))
    return out


def _award_from(fare: AwardFare, o, d, date, cabin, party, cfg) -> SegCandidate:
    flags = []
    taxes = fare.taxes_usd
    if taxes is None:
        taxes = default_taxes(o, d, cfg)
        flags.append("award-taxes-estimated")
    if fare.seats == 0:
        flags.append("seats-unknown")
    return SegCandidate(
        kind="award", legs=(Leg(o, d, date, cabin),),
        cash_usd=round((taxes + fee_usd(cfg)) * party, 2),
        miles=fare.miles * party, program=fare.program, cabin=cabin,
        airlines=fare.airlines or fare.program, seats=fare.seats, flags=tuple(flags),
    )


def award_candidates(tables, a: Stop, b: Stop, date, cabins, party, cfg) -> list[SegCandidate]:
    out = []
    for o, d in _pairs(a, b):
        for cabin in cabins:
            for fare in tables.awards_by_od.get((o, d, date, cabin), []):
                if 0 < fare.seats < party:      # known and insufficient
                    continue
                out.append(_award_from(fare, o, d, date, cabin, party, cfg))
    return out


def stopover_candidates(tables, a: Stop, b: Stop, c: Stop, date_ab, date_bc,
                        cabins, party, cfg) -> list[SegCandidate]:
    """One award covering a->b->c with a stopover at b. Only where a rule exists."""
    out = []
    for cabin in cabins:
        for o, mid in _pairs(a, b):
            for mid2, d in _pairs(b, c):
                if mid2 != mid:
                    continue          # must stop over in the airport you landed at
                for (ko, kd, kdate, kcabin, prog), first in tables.awards.items():
                    if (ko, kd, kdate, kcabin) != (o, mid, date_ab, cabin):
                        continue
                    extra = program_for(prog).stopover_extra_miles
                    if extra is None:
                        continue
                    second = tables.awards.get((mid, d, date_bc, cabin, prog))
                    if second is None:
                        continue
                    # 0 means "unknown", not "none". If either leg is unknown the
                    # pair is unknown — reporting the other leg's count would
                    # advertise confirmed seats we cannot actually confirm.
                    known = [s for s in (first.seats, second.seats) if s > 0]
                    any_unknown = first.seats == 0 or second.seats == 0
                    seats = min(known) if known else 0
                    if 0 < seats < party:
                        continue
                    flags = ["stopover-verify-with-program"]
                    taxes = 0.0
                    for fare, (fo, fd) in ((first, (o, mid)), (second, (mid, d))):
                        if fare.taxes_usd is None:
                            taxes += default_taxes(fo, fd, cfg)
                            if "award-taxes-estimated" not in flags:
                                flags.append("award-taxes-estimated")
                        else:
                            taxes += fare.taxes_usd
                    if any_unknown:
                        flags.append("seats-unknown")
                    out.append(SegCandidate(
                        kind="award",
                        legs=(Leg(o, mid, date_ab, cabin), Leg(mid, d, date_bc, cabin)),
                        cash_usd=round((taxes + fee_usd(cfg)) * party, 2),
                        miles=(first.miles + extra) * party, program=prog, cabin=cabin,
                        airlines=first.airlines or prog, seats=seats,
                        flags=tuple(flags), spans=2,
                    ))
    return out
