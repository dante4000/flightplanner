from __future__ import annotations

import datetime as dt

from .cash_client import CashQuery
from .config import Config

# GMP has no service to NRT; every other cross pair is queryable.
EXCLUDED_HOP_PAIRS = {("GMP", "NRT"), ("NRT", "GMP")}


def add_days(date: str, n: int) -> str:
    return (dt.date.fromisoformat(date) + dt.timedelta(days=n)).isoformat()


def date_range(start: str, end: str, step: int = 1) -> list[str]:
    out = []
    d, e = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    while d <= e:
        out.append(d.isoformat())
        d += dt.timedelta(days=step)
    if out and out[-1] != end:
        out.append(end)
    return out


def hop_pairs(cfg: Config, direction: str) -> list[tuple[str, str]]:
    if direction == "KJ":
        raw = [(k, j) for k in cfg.korea_airports for j in cfg.japan_airports]
    else:
        raw = [(j, k) for j in cfg.japan_airports for k in cfg.korea_airports]
    return [p for p in raw if p not in EXCLUDED_HOP_PAIRS]


def windows(cfg: Config) -> dict:
    hop_start = add_days(cfg.outbound_start, 1 + cfg.min_nights_first)
    hop_end = add_days(cfg.return_a_deadline, -cfg.min_nights_second)
    ret_start = add_days(hop_start, cfg.min_nights_second)
    return {
        "t1": (cfg.outbound_start, cfg.outbound_end),
        "hop": (hop_start, hop_end),
        "ret_a": (ret_start, cfg.return_a_deadline),
        "ret_b": (ret_start, cfg.return_b_deadline),
    }


def plan_phase1(cfg: Config) -> list[CashQuery]:
    w = windows(cfg)
    step = cfg.cash_grid_step_days
    gateways = cfg.korea_gateways + cfg.japan_gateways
    queries: dict[str, CashQuery] = {}

    def put(q: CashQuery) -> None:
        queries.setdefault(q.key, q)

    for date in date_range(*w["t1"], step):                     # T1: LAX -> gateways
        for g in gateways:
            for cabin in ("Y", "J"):
                put(CashQuery("ow", "LAX", g, date, None, cabin, 2, 1))
    for direction in ("KJ", "JK"):                              # hop, economy cash only
        for o, d in hop_pairs(cfg, direction):
            for date in date_range(*w["hop"], step):
                put(CashQuery("ow", o, d, date, None, "Y", 2, 2))
    for prio, wkey in ((3, "ret_a"), (4, "ret_b")):             # direct returns
        for date in date_range(*w[wkey], step):
            for g in gateways:
                for cabin in ("Y", "J"):
                    put(CashQuery("ow", g, "LAX", date, None, cabin, 1, prio))
    # hopback legs (person flies 2nd country -> 1st country before the transpacific).
    # Same hop pairs reversed relative to each direction; reuse ret windows, Y only.
    for direction in ("KJ", "JK"):
        for o, d in hop_pairs(cfg, "JK" if direction == "KJ" else "KJ"):
            for date in date_range(*w["ret_b"], step):
                put(CashQuery("ow", o, d, date, None, "Y", 1, 5))
    return sorted(queries.values(), key=lambda q: (q.priority, q.key))
