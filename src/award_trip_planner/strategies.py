from __future__ import annotations

import time
from dataclasses import dataclass, field

from .config import Config
from .models import AwardFare, BookingLine, Bundle, CashFare, Leg, Option


@dataclass
class PriceTables:
    awards: dict = field(default_factory=dict)     # (o,d,date,cabin) -> AwardFare
    cash_ow: dict = field(default_factory=dict)    # (o,d,date,cabin) -> CashFare
    cash_rt: dict = field(default_factory=dict)    # (o,d,dep,ret,cabin) -> CashFare


def build_tables(award_fares, cash_fares, overrides, cfg: Config) -> PriceTables:
    t = PriceTables()
    for a in award_fares:
        k = (a.origin, a.dest, a.date, a.cabin)
        if k not in t.awards or a.miles < t.awards[k].miles:
            t.awards[k] = a
    for c in cash_fares:
        if c.kind == "rt":
            k = (c.origin, c.dest, c.depart_date, c.return_date, c.cabin)
            if k not in t.cash_rt or c.per_person() < t.cash_rt[k].per_person():
                t.cash_rt[k] = c
        else:
            k = (c.origin, c.dest, c.depart_date, c.cabin)
            if k not in t.cash_ow or c.per_person() < t.cash_ow[k].per_person():
                t.cash_ow[k] = c
    for ov in overrides:
        k = (ov["origin"], ov["dest"], ov["date"], ov["cabin"])
        t.cash_ow[k] = CashFare(
            kind="ow", origin=ov["origin"], dest=ov["dest"], depart_date=ov["date"],
            return_date=None, cabin=ov["cabin"], adults=1, total_usd=ov["price_pp"],
            airline="manual", fetched_at=time.time(), manual=True,
        )
    return t


def _fee_usd(cfg: Config) -> float:
    return round(cfg.aeroplan_partner_fee_cad * cfg.cad_to_usd, 2)


def _default_taxes(o: str, d: str, cfg: Config) -> float:
    if "LAX" in (o, d):
        return cfg.default_award_taxes_usd
    return cfg.default_hop_award_taxes_usd


def _staleness_flags(fare: CashFare, cfg: Config, now: float) -> tuple[str, ...]:
    if fare.manual:
        return ("manual-price",)
    if now - fare.fetched_at > cfg.cash_ttl_hours * 3600:
        return ("stale-cash",)
    return ()


def ow_cash_option(t, o, d, date, cabin, adults, now, cfg) -> Option | None:
    fare = t.cash_ow.get((o, d, date, cabin))
    if fare is None:
        return None
    return Option(
        product="cash_ow", legs=(Leg(o, d, date, cabin),),
        cash_pp=round(fare.per_person(), 2), points_pp=0, airline=fare.airline,
        flags=_staleness_flags(fare, cfg, now),
    )


def ow_award_option(t, o, d, date, cabin, n_people, cfg) -> Option | None:
    a = t.awards.get((o, d, date, cabin))
    if a is None:
        return None
    flags, taxes = [], a.taxes_usd
    if taxes is None:
        taxes = _default_taxes(o, d, cfg)
        flags.append("award-taxes-estimated")
    if a.seats == 0:
        flags.append("seats-unknown")
    leg = Leg(o, d, date, cabin)
    return Option(
        product="award_ow", legs=(leg,),
        cash_pp=round(taxes + _fee_usd(cfg), 2), points_pp=a.miles,
        airline=a.airlines or "Aeroplan partner",
        award_seat_legs=((leg, a.seats),), flags=tuple(flags),
    )


def rt_cash_option(t, o, d, dep, ret, cabin, adults, cfg, now) -> Option | None:
    legs = (Leg(o, d, dep, cabin), Leg(d, o, ret, cabin))
    exact = t.cash_rt.get((o, d, dep, ret, cabin))
    if exact is not None:
        return Option(
            product="cash_rt", legs=legs, cash_pp=round(exact.per_person(), 2),
            points_pp=0, airline=exact.airline,
            flags=_staleness_flags(exact, cfg, now),
        )
    out = t.cash_ow.get((o, d, dep, cabin))
    back = t.cash_ow.get((d, o, ret, cabin))
    if out is None or back is None:
        return None
    return Option(
        product="cash_rt", legs=legs,
        cash_pp=round(out.per_person() + back.per_person(), 2), points_pp=0,
        airline=f"{out.airline} / {back.airline}",
        flags=("rt-estimated-from-oneways",),
    )


def stopover_option(t, t1_o, t1_d, t1_date, h_d, h_date, cabin, cfg) -> Option | None:
    t1 = t.awards.get((t1_o, t1_d, t1_date, cabin))
    hop = t.awards.get((t1_d, h_d, h_date, cabin))
    if t1 is None or hop is None:
        return None
    flags = ["stopover-verify-on-aeroplan"]
    taxes = 0.0
    for fare, (o, d) in ((t1, (t1_o, t1_d)), (hop, (t1_d, h_d))):
        if fare.taxes_usd is None:
            taxes += _default_taxes(o, d, cfg)
            if "award-taxes-estimated" not in flags:
                flags.append("award-taxes-estimated")
        else:
            taxes += fare.taxes_usd
    if min(t1.seats, hop.seats) == 0:
        flags.append("seats-unknown")
    l1 = Leg(t1_o, t1_d, t1_date, cabin)
    l2 = Leg(t1_d, h_d, h_date, cabin)
    return Option(
        product="award_stopover", legs=(l1, l2),
        cash_pp=round(taxes + _fee_usd(cfg), 2),
        points_pp=t1.miles + cfg.stopover_extra_miles,
        airline=t1.airlines or "Aeroplan partner",
        award_seat_legs=((l1, t1.seats), (l2, hop.seats)),
        flags=tuple(flags),
    )
