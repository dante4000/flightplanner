from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass

from .cache import Cache

TRACKER_URL = "https://frequentmiler.com/transfer-bonuses/"
CACHE_NS = "bonuses"
CACHE_KEY = "scrape"
MANUAL_KEY = "manual"

# Currency labels as they appear in tracker tables.
_CURRENCY_ALIASES = {
    "american express": "amex_mr",
    "amex": "amex_mr",
    "membership rewards": "amex_mr",
    "chase": "chase_ur",
    "chase ultimate rewards": "chase_ur",
    "ultimate rewards": "chase_ur",
}

# Airline labels -> seats.aero Source ids. Unlisted airlines are skipped, never guessed.
_PROGRAM_ALIASES = {
    "air canada aeroplan": "aeroplan",
    "aeroplan": "aeroplan",
    "virgin atlantic flying club": "virginatlantic",
    "virgin atlantic": "virginatlantic",
    "air france/klm flying blue": "flyingblue",
    "flying blue": "flyingblue",
    "singapore krisflyer": "singapore",
    "krisflyer": "singapore",
    "delta skymiles": "delta",
    "emirates skywards": "emirates",
    "etihad guest": "etihad",
    "qantas frequent flyer": "qantas",
    "jetblue trueblue": "jetblue",
    "qatar airways privilege club": "qatar",
    "united mileageplus": "united",
}


@dataclass(frozen=True)
class Bonus:
    currency: str
    program: str
    pct: float
    expires: str | None
    source: str          # "scrape" | "manual"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s)).strip().lower()


def parse_bonus_rows(html: str) -> list[Bonus]:
    """Parse tracker rows: currency | program | percent | expiry."""
    out: list[Bonus] = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S | re.I):
        cells = [_norm(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S | re.I)]
        if len(cells) < 3:
            continue
        currency = _CURRENCY_ALIASES.get(cells[0])
        program = _PROGRAM_ALIASES.get(cells[1])
        if not currency or not program:
            continue
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", cells[2])
        if not m:
            continue
        expires = None
        if len(cells) > 3:
            m2 = re.search(r"\d{4}-\d{2}-\d{2}", cells[3])
            expires = m2.group(0) if m2 else None
        out.append(Bonus(currency, program, float(m.group(1)) / 100.0, expires, "scrape"))
    return out


def _default_fetcher() -> str:
    import httpx

    resp = httpx.get(TRACKER_URL, timeout=20.0, headers={"user-agent": "award-trip-planner"})
    resp.raise_for_status()
    return resp.text


def set_manual_bonus(cache: Cache, currency: str, program: str,
                     pct: float | None, expires: str | None) -> None:
    rows = {(b.currency, b.program): b for b in manual_bonuses(cache)}
    if pct is None:
        rows.pop((currency, program), None)
    else:
        rows[(currency, program)] = Bonus(currency, program, pct, expires, "manual")
    cache.put(CACHE_NS, MANUAL_KEY, [asdict(b) for b in rows.values()])


def manual_bonuses(cache: Cache) -> list[Bonus]:
    row = cache.get_stale(CACHE_NS, MANUAL_KEY)
    return [Bonus(**d) for d in row[0]] if row else []


def _not_expired(b: Bonus, today: str) -> bool:
    return b.expires is None or b.expires >= today


def active_bonuses(
    cache: Cache, today: str, fetcher=None, ttl_hours: float = 12.0,
    now: float | None = None,
) -> tuple[list[Bonus], list[str]]:
    now = time.time() if now is None else now
    notes: list[str] = []
    scraped: list[Bonus] = []

    cached = cache.get(CACHE_NS, CACHE_KEY, max_age_s=ttl_hours * 3600, now=now)
    if cached is not None:
        scraped = [Bonus(**d) for d in cached]
    else:
        try:
            html = (fetcher or _default_fetcher)()
            scraped = parse_bonus_rows(html)
            cache.put(CACHE_NS, CACHE_KEY, [asdict(b) for b in scraped], now=now)
        except Exception as e:
            notes.append(f"transfer-bonus scrape failed ({e}); using manual entries only")

    merged: dict[tuple[str, str], Bonus] = {(b.currency, b.program): b for b in scraped}
    for b in manual_bonuses(cache):
        merged[(b.currency, b.program)] = b       # manual always wins
    return [b for b in merged.values() if _not_expired(b, today)], notes


def bonus_lookup_from(bonuses: list[Bonus]):
    table = {(b.currency, b.program): b.pct for b in bonuses}
    return lambda currency, program: table.get((currency, program), 0.0)
