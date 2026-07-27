from __future__ import annotations

import math
from dataclasses import dataclass, field

CURRENCIES: tuple[str, ...] = ("amex_mr", "chase_ur", "aeroplan_fixed")


@dataclass(frozen=True)
class Program:
    id: str
    name: str
    transfers: dict[str, float] = field(default_factory=dict)  # currency -> miles per point
    stopover_extra_miles: int | None = None                    # None = no documented rule


def _p(pid, name, *, amex=False, chase=False, aeroplan_fixed=False, stopover=None):
    t: dict[str, float] = {}
    if amex:
        t["amex_mr"] = 1.0
    if chase:
        t["chase_ur"] = 1.0
    if aeroplan_fixed:
        t["aeroplan_fixed"] = 1.0
    return Program(id=pid, name=name, transfers=t, stopover_extra_miles=stopover)


REGISTRY: dict[str, Program] = {
    p.id: p
    for p in [
        _p("aeroplan", "Air Canada Aeroplan", amex=True, chase=True,
           aeroplan_fixed=True, stopover=5_000),
        _p("flyingblue", "Air France/KLM Flying Blue", amex=True, chase=True),
        _p("virginatlantic", "Virgin Atlantic Flying Club", amex=True, chase=True),
        _p("singapore", "Singapore KrisFlyer", amex=True, chase=True),
        _p("delta", "Delta SkyMiles", amex=True),
        _p("emirates", "Emirates Skywards", amex=True, chase=True),
        _p("etihad", "Etihad Guest", amex=True),
        _p("qantas", "Qantas Frequent Flyer", amex=True),
        _p("jetblue", "JetBlue TrueBlue", amex=True, chase=True),
        _p("qatar", "Qatar Airways Privilege Club", amex=True),
        _p("united", "United MileagePlus", chase=True),
        # No flexible-currency route from MR or UR in the US market:
        _p("alaska", "Alaska Mileage Plan"),
        _p("american", "American AAdvantage"),
        _p("smiles", "GOL Smiles"),
        _p("azul", "Azul TudoAzul"),
        _p("velocity", "Virgin Australia Velocity"),
        _p("ethiopian", "Ethiopian ShebaMiles"),
    ]
}


def program_for(source: str) -> Program:
    """Known program, or a non-fundable placeholder so new sources never crash us."""
    return REGISTRY.get(source) or Program(id=source, name=source, transfers={})


def points_needed(miles: int, ratio: float, bonus_pct: float) -> int:
    """Points to transfer to obtain `miles`. Always rounds up."""
    return math.ceil(miles / (ratio * (1.0 + bonus_pct)))


def funding_options(
    program_id: str, miles: int, balances: dict[str, int], bonus_lookup
) -> list[tuple[str, int]]:
    """(currency, points_needed) for each currency reaching the program, cheapest first.

    Not filtered by balance — callers decide affordability so they can report
    shortfalls.
    """
    prog = program_for(program_id)
    out = [
        (currency, points_needed(miles, ratio, bonus_lookup(currency, program_id)))
        for currency, ratio in prog.transfers.items()
    ]
    return sorted(out, key=lambda x: (x[1], x[0]))


def fundability(
    program_id: str, miles: int, balances: dict[str, int], bonus_lookup
) -> tuple[bool, str, int]:
    """(fundable, reason, shortfall) against the given balances."""
    opts = funding_options(program_id, miles, balances, bonus_lookup)
    if not opts:
        return False, "no transfer partner", 0
    best_short = None
    for currency, needed in opts:
        have = balances.get(currency, 0)
        if have >= needed:
            return True, "ok", 0
        short = needed - have
        best_short = short if best_short is None else min(best_short, short)
    return False, "insufficient balance", int(best_short or 0)
