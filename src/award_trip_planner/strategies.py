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
        flags=tuple(dict.fromkeys(
            ("rt-estimated-from-oneways",)
            + _staleness_flags(out, cfg, now) + _staleness_flags(back, cfg, now))),
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


# ---------------------------------------------------------------- composition

import dataclasses                          # noqa: E402  (bottom-half imports)

from .cash_client import CashQuery          # noqa: E402
from .planner import add_days, date_range, hop_pairs, windows  # noqa: E402

PRUNE_CPP = 0.0115          # $/point scalarization used ONLY to prune per-person option lists
PER_PERSON_KEEP = 8         # options kept per person per skeleton
SKELETON_KEEP = 3           # return-date candidates kept per shape


def _cabins(): return ("Y", "J")


def _dates_with_data(t, origins, dests, window, cfg):
    lo, hi = window
    seen = set()
    for (o, d, date, _cabin) in list(t.cash_ow) + list(t.awards):
        if o in origins and d in dests and lo <= date <= hi:
            seen.add(date)
    return sorted(seen)


def _person_front_options(t, direction, t1_dest, t1_date, hop, cfg, now):
    """Options covering T1+H for one person. hop = (h_o, h_d, h_date)."""
    h_o, h_d, h_date = hop
    out = []
    for cab in _cabins():
        t1_opts = [
            ow_cash_option(t, "LAX", t1_dest, t1_date, cab, 2, now, cfg),
            ow_award_option(t, "LAX", t1_dest, t1_date, cab, 1, cfg),
        ]
        hop_opts = [
            ow_cash_option(t, h_o, h_d, h_date, "Y", 2, now, cfg),
            ow_award_option(t, h_o, h_d, h_date, "Y", 1, cfg),
            ow_award_option(t, h_o, h_d, h_date, "J", 1, cfg),
        ]
        for a in t1_opts:
            for b in hop_opts:
                if a and b:
                    out.append((a, b))
        if h_o == t1_dest:
            s = stopover_option(t, "LAX", t1_dest, t1_date, h_d, h_date, cab, cfg)
            if s:
                out.append((s,))
    return out


def _person_return_options(t, direction, t1_dest, hop, deadline, hop_date, cfg, now):
    """Options covering the way home for one person: direct or hopback."""
    h_o, h_d, h_date = hop
    earliest = add_days(hop_date, cfg.min_nights_second)
    out = []
    second_gateways = cfg.japan_gateways if direction == "KJ" else cfg.korea_gateways
    first_gateways = cfg.korea_gateways if direction == "KJ" else cfg.japan_gateways
    ret_dates = [d for d in _dates_with_data(
        t, set(second_gateways) | set(first_gateways), {"LAX"}, (earliest, deadline), cfg)]
    for rd in ret_dates[:SKELETON_KEEP * 4]:
        for g in second_gateways:
            for cab in _cabins():
                for opt in (ow_cash_option(t, g, "LAX", rd, cab, 1, now, cfg),
                            ow_award_option(t, g, "LAX", rd, cab, 1, cfg)):
                    if opt:
                        out.append((opt,))
    # hopback: 2nd -> 1st country cash leg + transpacific from 1st-country gateway
    back_pairs = hop_pairs(cfg, "JK" if direction == "KJ" else "KJ")
    for rd in ret_dates:
        for g in first_gateways:
            for cab in _cabins():
                home = (ow_cash_option(t, g, "LAX", rd, cab, 1, now, cfg)
                        or ow_award_option(t, g, "LAX", rd, cab, 1, cfg))
                if home is None:
                    continue
                for (bo, bd) in back_pairs:
                    if bd != g:
                        continue
                    for bdate in _dates_with_data(t, {bo}, {bd}, (earliest, rd), cfg)[:SKELETON_KEEP]:
                        back = ow_cash_option(t, bo, bd, bdate, "Y", 1, now, cfg)
                        if back:
                            out.append((back, home))
    # coupled transpacific RT: LAX<->first-gateway (T1 cash + return from 1st country).
    # Emitted as a marker option pair handled at bundle level via rt_cash_option in
    # _person_plans (see below) — not generated here.
    return out


def _prune(opts):
    def cost(chain):
        return sum(o.cash_pp for o in chain) + PRUNE_CPP * sum(o.points_pp for o in chain)
    uniq = {}
    for chain in opts:
        key = tuple((o.product, o.legs) for o in chain)
        if key not in uniq or cost(chain) < cost(uniq[key]):
            uniq[key] = chain
    return sorted(uniq.values(), key=cost)[:PER_PERSON_KEEP]


def _person_plans(t, direction, t1_dest, t1_date, hop, deadline, cfg, now):
    """All (front_chain, ret_chain) plans for one person, pruned."""
    plans = []
    fronts = _prune(_person_front_options(t, direction, t1_dest, t1_date, hop, cfg, now))
    rets = _prune(_person_return_options(t, direction, t1_dest, hop, deadline, hop[2], cfg, now))
    for f in fronts:
        for r in rets:
            plans.append(f + r)
    # coupled RT: cash T1 + cash return from first-country gateway as ONE rt product,
    # combined with a cash/award hop + cash hopback + nothing else transpacific.
    h_o, h_d, h_date = hop
    earliest = add_days(hop[2], cfg.min_nights_second)
    for rd in _dates_with_data(t, {t1_dest}, {"LAX"}, (earliest, deadline), cfg)[:SKELETON_KEEP]:
        for cab in _cabins():
            rt = rt_cash_option(t, "LAX", t1_dest, t1_date, rd, cab, 1, cfg, now)
            if rt is None:
                continue
            for back_date in _dates_with_data(t, {h_d}, {t1_dest}, (earliest, rd), cfg)[:SKELETON_KEEP]:
                back = ow_cash_option(t, h_d, t1_dest, back_date, "Y", 1, now, cfg)
                hop_opt = ow_cash_option(t, h_o, h_d, h_date, "Y", 2, now, cfg)
                if back and hop_opt:
                    plans.append((rt, hop_opt, back))
    return _prune(plans)


def _bundle_from(direction, plan_a, plan_b, cfg):
    lines, flags, total_cash, total_points = [], set(), 0.0, 0
    award_users: dict = {}
    seat_caps: dict = {}
    for person, chain in (("A", plan_a), ("B", plan_b)):
        for opt in chain:
            lines.append(BookingLine(
                person=person, product=opt.product,
                legs=[dataclasses.asdict(l) for l in opt.legs],
                cash_usd=opt.cash_pp, points=opt.points_pp, airline=opt.airline,
                notes=list(opt.flags),
            ))
            total_cash += opt.cash_pp
            total_points += opt.points_pp
            flags.update(opt.flags)
            for leg, seats in opt.award_seat_legs:
                award_users[leg] = award_users.get(leg, 0) + 1
                seat_caps[leg] = seats
    for leg, users in award_users.items():
        seats = seat_caps[leg]
        if seats == 0:
            flags.add("seats-unknown")
        elif users > seats:
            return None
    if total_points > cfg.points_budget:
        return None
    summary_bits = []
    for l in lines:
        if l.product.startswith("award"):
            route = "→".join([l.legs[0]["origin"]] + [x["dest"] for x in l.legs])
            summary_bits.append(f"{l.person}: {route} on points")
    summary = ("KJ: Korea first" if direction == "KJ" else "JK: Japan first") + (
        " · " + "; ".join(summary_bits) if summary_bits else " · all cash")

    date_bits = []
    out_date = next((leg["date"] for l in lines for leg in l.legs if leg["origin"] == "LAX"), None)
    if out_date:
        date_bits.append(f"out {out_date}")
    hop_date = next((leg["date"] for l in lines for leg in l.legs
                      if leg["origin"] != "LAX" and leg["dest"] != "LAX"), None)
    if hop_date:
        date_bits.append(f"hop {hop_date}")
    home_by_person: dict = {}
    for l in lines:
        for leg in l.legs:
            if leg["dest"] == "LAX":
                home_by_person[l.person] = leg["date"]
    home_a, home_b = home_by_person.get("A"), home_by_person.get("B")
    if home_a or home_b:
        date_bits.append(f"home {home_a or '?'}/{home_b or '?'}")
    if date_bits:
        summary += " · " + ", ".join(date_bits)

    return Bundle(
        direction=direction, total_cash_usd=round(total_cash, 2),
        total_points=total_points, cpp=None, lines=lines,
        flags=sorted(flags), summary=summary,
    )


def _is_transpac(leg: dict) -> bool:
    return "LAX" in (leg["origin"], leg["dest"])


def _shape_signature(bundle_dict, cfg: Config | None = None) -> tuple:
    """Signature that ignores dates but keeps routing + payment structure, so that
    date-permutation duplicates of the same underlying strategy collapse together.

    Airport codes within the same country group (e.g. NRT/HND/KIX in Japan, or
    ICN/GMP in Korea) are normalized to a single tag when `cfg` is supplied. Real
    data showed that leaving raw airport codes in the signature was still too
    granular: the SAME abstract strategy (cash front + hop, points home) recurred
    over and over differing only in which physical gateway/hop airport was used,
    which is logistics noise rather than a genuinely different strategy — it
    crowded the ranked list exactly like the date-permutation duplicates did.
    """
    def region(code: str) -> str:
        if cfg is None:
            return code
        if code in cfg.korea_airports:
            return "KR"
        if code in cfg.japan_airports:
            return "JP"
        return code

    return (bundle_dict["direction"],) + tuple(
        (line["person"], line["product"],
         tuple((region(leg["origin"]), region(leg["dest"]), leg["cabin"]) for leg in line["legs"]))
        for line in bundle_dict["lines"]
    )


def _view_of(bundle_dict) -> str:
    cabins = {leg["cabin"] for line in bundle_dict["lines"] for leg in line["legs"] if _is_transpac(leg)}
    if cabins == {"Y"}:
        return "economy"
    if cabins == {"J"}:
        return "business"
    return "other"


def compute(award_fares, cash_fares, overrides, cfg: Config, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    t = build_tables(award_fares, cash_fares, overrides, cfg)
    w = windows(cfg)
    bundles: list[Bundle] = []
    for direction in ("KJ", "JK"):
        first_gw = cfg.korea_gateways if direction == "KJ" else cfg.japan_gateways
        t1_dates = _dates_with_data(t, {"LAX"}, set(first_gw), w["t1"], cfg)
        for t1_dest in first_gw:
            for t1_date in t1_dates:
                hop_earliest = add_days(t1_date, 1 + cfg.min_nights_first)
                for (h_o, h_d) in hop_pairs(cfg, direction):
                    for h_date in _dates_with_data(t, {h_o}, {h_d}, (hop_earliest, w["hop"][1]), cfg):
                        hop = (h_o, h_d, h_date)
                        plans_a = _person_plans(t, direction, t1_dest, t1_date, hop,
                                                cfg.return_a_deadline, cfg, now)
                        plans_b = _person_plans(t, direction, t1_dest, t1_date, hop,
                                                cfg.return_b_deadline, cfg, now)
                        for pa in plans_a:
                            for pb in plans_b:
                                b = _bundle_from(direction, pa, pb, cfg)
                                if b:
                                    bundles.append(b)
    ranked = sorted(bundles, key=lambda b: b.total_cash_usd)
    # collapse date-permutation duplicates of the same strategy shape, keeping only
    # the cheapest (tie-break: fewest points) representative of each shape, BEFORE
    # per-view filtering/capping — otherwise a single cheap shape's date variants
    # crowd out genuinely different strategies from the top of every view.
    best_by_shape: dict[tuple, Bundle] = {}
    for b in ranked:
        sig = _shape_signature(b.to_dict(), cfg)
        cur = best_by_shape.get(sig)
        if cur is None or (b.total_cash_usd, b.total_points) < (cur.total_cash_usd, cur.total_points):
            best_by_shape[sig] = b
    ranked = sorted(best_by_shape.values(), key=lambda b: b.total_cash_usd)
    # classify once against a throwaway dict (kept separate from the copies we
    # actually emit below, so per-view cpp mutations never leak across views)
    classified = [(b, _view_of(b.to_dict())) for b in ranked]

    def _build_view(name: str) -> list:
        if name == "mixed":
            cands = [b for b, _ in classified]
        else:
            cands = [b for b, v in classified if v == name]
        capped = cands[: cfg.top_n]
        # guarantee the all-cash baseline (total_points == 0) is present in the
        # view whenever one exists for it, even if it's outside the cheapest
        # top_n by cash (points-heavy plans are usually cheaper in cash terms).
        if not any(b.total_points == 0 for b in capped):
            baseline_b = next((b for b in cands if b.total_points == 0), None)
            if baseline_b is not None:
                capped = (capped[:-1] if capped else []) + [baseline_b]
        capped = sorted(capped, key=lambda b: b.total_cash_usd)
        return [b.to_dict() for b in capped]

    views: dict[str, list] = {name: _build_view(name) for name in ("mixed", "economy", "business")}
    for view in views.values():
        baseline = next((b["total_cash_usd"] for b in view if b["total_points"] == 0), None)
        for b in view:
            if b["total_points"] and baseline is not None:
                b["cpp"] = round(100 * (baseline - b["total_cash_usd"]) / b["total_points"], 2)
    refine, seen = [], set()
    for b in views["mixed"][:10]:
        for line in b["lines"]:
            if line["product"] == "cash_rt" and "rt-estimated-from-oneways" in line["notes"]:
                out_leg, back_leg = line["legs"][0], line["legs"][1]
                key = (out_leg["origin"], out_leg["dest"], out_leg["date"], back_leg["date"], out_leg["cabin"])
                if key not in seen:
                    seen.add(key)
                    refine.append(CashQuery(
                        kind="rt", origin=key[0], dest=key[1], depart_date=key[2],
                        return_date=key[3], cabin=key[4], adults=1, priority=0,
                    ))
    return {
        "views": views,
        "refine_requests": refine[: cfg.refine_query_cap],
        "generated_at": now,
    }
