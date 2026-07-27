from __future__ import annotations

import argparse
import json
from pathlib import Path

from .bonuses import active_bonuses
from .cache import Cache
from .config import Config
from .models import AwardFare, CashFare, award_from_dict, cash_from_dict
from .products import build_tables
from .solver import solve
from .trip import Stop, Trip

JAPAN_CITIES = {
    "Tokyo": ("NRT", "HND"),
    "Osaka": ("KIX",),
}


def load_from_cache(cache: Cache) -> tuple[list[AwardFare], list[CashFare]]:
    awards: list[AwardFare] = []
    row = cache.get_stale("awards", "all")
    if row:
        awards = [award_from_dict(d) for d in row[0]]
    cash: list[CashFare] = []
    for key in cache.keys("cash"):
        r = cache.get_stale("cash", key)
        if r and r[0] and not r[0].get("failed"):
            cash.append(cash_from_dict(r[0]))
    return awards, cash


def korea_japan_trip(japan_city: str, party: int) -> Trip:
    la = Stop(city="Los Angeles", airports=("LAX",))
    seoul = Stop(city="Seoul", airports=("ICN", "GMP"), min_nights=3, max_nights=8)
    japan = Stop(city=japan_city, airports=JAPAN_CITIES[japan_city],
                 min_nights=3, max_nights=8)
    return Trip(stops=(la, seoul, japan, la), depart_start="2026-09-25",
                depart_end="2026-10-07", arrive_by="2026-10-31", party_size=party)


def format_result(result, limit: int) -> str:
    lines: list[str] = []
    if result.baseline_cash is not None:
        lines.append(f"all-cash baseline: ${result.baseline_cash:,.0f}")
    for n in result.notes:
        lines.append(f"note: {n}")
    if result.date_paths_capped:
        lines.append("note: date-path cap reached; widen the grid step or narrow the window")
    for i, b in enumerate(result.bundles[:limit], 1):
        pts = ", ".join(f"{k}={v:,}" for k, v in b["points"].items() if v) or "no points"
        cpp = f"{b['cpp']}c/pt" if b["cpp"] is not None else "-"
        lines.append(f"#{i}  ${b['total_cash_usd']:,.0f}  {pts}  {cpp}")
        for s in b["segments"]:
            route = " + ".join(f"{l['origin']}->{l['dest']} {l['date']} {l['cabin']}"
                               for l in s["legs"])
            tag = s["program"] or "cash"
            lines.append(f"      {tag:16s} {route:44s} ${s['cash_usd']:>8,.0f}"
                         f" {s['miles'] or '':>9}")
        if b["flags"]:
            lines.append(f"      flags: {', '.join(b['flags'])}")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="atp-solve",
                                 description="Solve a saved trip against cached prices")
    ap.add_argument("--japan-city", default="Tokyo", choices=sorted(JAPAN_CITIES))
    ap.add_argument("--party", type=int, default=2)
    ap.add_argument("--amex", type=int, default=0, help="Amex MR balance")
    ap.add_argument("--chase", type=int, default=0, help="Chase UR balance")
    ap.add_argument("--aeroplan", type=int, default=100_000, help="existing Aeroplan balance")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--sort", default="cash", choices=("cash", "value"),
                    help="cash: cheapest out-of-pocket first (default); "
                         "value: best cents-per-point first")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = ap.parse_args(argv)

    root = Path(__file__).resolve().parent.parent.parent
    cache = Cache(root / "cache.sqlite")
    cfg = Config.load(root / "config.json")
    awards, cash = load_from_cache(cache)
    tables = build_tables(awards, cash, cache.overrides(), cfg)
    bonuses, notes = active_bonuses(cache, today=cfg.outbound_start)
    balances = {"amex_mr": args.amex, "chase_ur": args.chase,
                "aeroplan_fixed": args.aeroplan}
    result = solve(korea_japan_trip(args.japan_city, args.party), tables,
                   balances, bonuses, cfg, top_n=args.limit, sort=args.sort)
    result.notes.extend(notes)
    print(json.dumps(result.to_dict(), indent=2) if args.json
          else format_result(result, args.limit))
    return 0
