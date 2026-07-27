from __future__ import annotations

import datetime as dt

import httpx

from .cache import Cache
from .config import Config
from .models import AwardFare, award_from_dict, fare_to_dict

BASE = "https://seats.aero/partnerapi"
PAGE_SIZE = 500
CABINS = ("Y", "J")


def today_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


def _map_entry(a: dict, cfg: Config) -> list[AwardFare]:
    out = []
    for c in CABINS:
        if not a.get(f"{c}AvailableRaw") or not a.get(f"{c}MileageCostRaw"):
            continue
        taxes_raw = a.get(f"{c}TotalTaxesRaw") or 0
        if taxes_raw > 0:
            taxes = taxes_raw / 100.0  # minor units
            if a.get("TaxesCurrency") == "CAD":
                taxes = round(taxes * cfg.cad_to_usd, 2)
        else:
            taxes = None
        out.append(
            AwardFare(
                origin=a["Route"]["OriginAirport"],
                dest=a["Route"]["DestinationAirport"],
                date=a["Date"],
                cabin=c,
                miles=int(a[f"{c}MileageCostRaw"]),
                taxes_usd=taxes,
                seats=int(a.get(f"{c}RemainingSeatsRaw") or 0),
                direct=bool(a.get(f"{c}DirectRaw")),
                airlines=a.get(f"{c}AirlinesRaw") or "",
                updated_at=a.get("UpdatedAt", ""),
                program=a.get("Source", ""),
            )
        )
    return out


def fetch_awards(
    api_key: str,
    pairs: list[tuple[list[str], list[str]]],
    start: str,
    end: str,
    cache: Cache,
    cfg: Config,
    transport: httpx.BaseTransport | None = None,
    on_progress=None,
    sources: str | None = None,
) -> list[AwardFare]:
    fares: list[AwardFare] = []
    client = httpx.Client(
        transport=transport,
        headers={"Partner-Authorization": api_key, "accept": "application/json"},
        timeout=30.0,
    )
    with client:
        for origins, dests in pairs:
            cursor = None
            while True:
                params = {
                    "origin_airport": ",".join(origins),
                    "destination_airport": ",".join(dests),
                    "start_date": start,
                    "end_date": end,
                    "take": PAGE_SIZE,
                }
                if sources:
                    params["sources"] = sources
                if cursor:
                    params["cursor"] = cursor
                resp = client.get(f"{BASE}/search", params=params)
                cache.bump_quota(today_utc())
                resp.raise_for_status()
                body = resp.json()
                for entry in body.get("data", []):
                    fares.extend(_map_entry(entry, cfg))
                if on_progress:
                    on_progress(len(fares))
                if body.get("hasMore") and body.get("cursor"):
                    cursor = body["cursor"]
                else:
                    break
    cache.put("awards", "all", [fare_to_dict(f) for f in fares])
    return fares


def awards_from_cache(cache: Cache) -> tuple[list[AwardFare], float] | None:
    row = cache.get_stale("awards", "all")
    if row is None:
        return None
    raw, fetched_at = row
    return [award_from_dict(d) for d in raw], fetched_at
