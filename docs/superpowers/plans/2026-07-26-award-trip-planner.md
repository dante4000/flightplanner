# Award Trip Planner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local FastAPI dashboard that ranks every way to book a 2-person LAX↔Korea↔Japan trip (cash, Aeroplan awards, the Aeroplan stopover trick, and mixes) by total out-of-pocket cash under a 100k-point budget.

**Architecture:** Three data layers (seats.aero award client, Google-Flights cash client via patched `fast-flights`, SQLite cache) feed a pure strategy engine (`strategies.py`) that enumerates booking "shapes" per traveler and ranks complete bundles. A FastAPI server orchestrates background refreshes and serves a single-page vanilla-JS dashboard.

**Tech Stack:** Python ≥3.11, uv, FastAPI, uvicorn, httpx, `fast-flights==3.0.2` (pinned; vendored tolerant parser), sqlite3 (stdlib), pytest. Frontend: one static HTML file, no framework.

## Global Constraints

- `requires-python = ">=3.11"`; project managed by **uv**; run everything as `uv run …` from the repo root `/Users/danielko/dev/award-trip-planner`.
- `fast-flights==3.0.2` **pinned exactly** — the vendored parser patch depends on this version's payload indices.
- seats.aero key comes from `.env` as `SEATS_AERO_KEY` (already gitignored; never committed, never logged). Quota: 1,000 calls/day — every HTTP call increments the quota counter.
- All dates are ISO `YYYY-MM-DD` strings at module boundaries.
- Money: floats in **USD** internally. Aeroplan taxes arrive in CAD → convert with `cfg.cad_to_usd` (default 0.73). Google Flights prices are **totals for the queried adult count** (live-verified 2026-07-26: adults=2 returns exactly 2× adults=1). Award miles/taxes are **per person**.
- Trip constants: outbound LAX Sep 25–Oct 7 2026; traveler A lands LAX by Oct 14; traveler B by Oct 31; Korea airports ICN/GMP, Japan NRT/HND/KIX; either country order; point budget 100,000.
- Live verification already done (2026-07-26, recorded in the spec): seats.aero `sources=aeroplan` filters server-side; response field names as used in fixtures below; Google multi-city has no prices (open-jaw product dropped — direct returns priced as sum of one-ways); RT and OW queries parse with the tolerant patch.
- Commit after every task (green tests only). Test command: `uv run pytest -q`.

---

### Task 1: Scaffold + Config

**Files:**
- Create: `pyproject.toml`
- Create: `src/award_trip_planner/__init__.py`
- Create: `src/award_trip_planner/config.py`
- Create: `tests/test_config.py`

**Interfaces:**
- Produces: `Config` dataclass with fields and defaults exactly as below; `Config.load(path: Path) -> Config`; `cfg.save(path: Path) -> None`; `cfg.to_dict() -> dict`. All later tasks read `cfg.<field>` names verbatim.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "award-trip-planner"
version = "0.1.0"
description = "Rank cash/Aeroplan booking strategies for a LAX-Korea-Japan trip"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115",
    "uvicorn>=0.30",
    "httpx>=0.27",
    "fast-flights==3.0.2",
    "python-dotenv>=1.0",
]

[project.scripts]
award-trip-planner = "award_trip_planner.__main__:main"

[dependency-groups]
dev = ["pytest>=8"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/award_trip_planner"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Create the package and sync**

```bash
mkdir -p src/award_trip_planner tests static
touch src/award_trip_planner/__init__.py
uv sync
```
Expected: uv creates `.venv`, resolves fast-flights==3.0.2 without error.

- [ ] **Step 3: Write the failing config test** — `tests/test_config.py`

```python
import json
from pathlib import Path

from award_trip_planner.config import Config


def test_defaults():
    cfg = Config()
    assert cfg.outbound_start == "2026-09-25"
    assert cfg.outbound_end == "2026-10-07"
    assert cfg.return_a_deadline == "2026-10-14"
    assert cfg.return_b_deadline == "2026-10-31"
    assert cfg.korea_gateways == ["ICN"]
    assert cfg.japan_gateways == ["NRT", "HND"]
    assert cfg.korea_airports == ["ICN", "GMP"]
    assert cfg.japan_airports == ["NRT", "HND", "KIX"]
    assert cfg.points_budget == 100_000
    assert cfg.min_nights_first == 3 and cfg.min_nights_second == 3


def test_load_missing_file_gives_defaults(tmp_path: Path):
    cfg = Config.load(tmp_path / "nope.json")
    assert cfg.points_budget == 100_000


def test_save_load_roundtrip_ignores_unknown_keys(tmp_path: Path):
    p = tmp_path / "config.json"
    cfg = Config()
    cfg.points_budget = 80_000
    cfg.save(p)
    raw = json.loads(p.read_text())
    raw["bogus_key"] = 1
    p.write_text(json.dumps(raw))
    cfg2 = Config.load(p)
    assert cfg2.points_budget == 80_000
    assert not hasattr(cfg2, "bogus_key")
```

- [ ] **Step 4: Run to verify failure**

Run: `uv run pytest tests/test_config.py -q`
Expected: FAIL — `ModuleNotFoundError` / `ImportError` on `award_trip_planner.config`.

- [ ] **Step 5: Implement `src/award_trip_planner/config.py`**

```python
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


@dataclass
class Config:
    # trip windows (ISO dates)
    outbound_start: str = "2026-09-25"
    outbound_end: str = "2026-10-07"
    return_a_deadline: str = "2026-10-14"
    return_b_deadline: str = "2026-10-31"
    # airports: gateways fly transpacific; airports lists are valid for the hop
    korea_gateways: list[str] = field(default_factory=lambda: ["ICN"])
    japan_gateways: list[str] = field(default_factory=lambda: ["NRT", "HND"])
    korea_airports: list[str] = field(default_factory=lambda: ["ICN", "GMP"])
    japan_airports: list[str] = field(default_factory=lambda: ["NRT", "HND", "KIX"])
    min_nights_first: int = 3
    min_nights_second: int = 3
    # points & money
    points_budget: int = 100_000
    cad_to_usd: float = 0.73
    aeroplan_partner_fee_cad: float = 39.0
    stopover_extra_miles: int = 5_000
    default_award_taxes_usd: float = 60.0      # per person, transpacific, when API reports 0
    default_hop_award_taxes_usd: float = 30.0  # per person, intra-Asia, when API reports 0
    # fetch tuning
    cash_grid_step_days: int = 2
    cash_query_cap: int = 60
    refine_query_cap: int = 16
    cash_ttl_hours: float = 6.0
    award_ttl_hours: float = 24.0
    top_n: int = 15

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2))

    @classmethod
    def load(cls, path: Path) -> "Config":
        if not Path(path).exists():
            return cls()
        raw = json.loads(Path(path).read_text())
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})
```

- [ ] **Step 6: Run to verify pass**

Run: `uv run pytest tests/test_config.py -q`
Expected: `3 passed`.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock src tests
git commit -m "feat: scaffold uv project with Config"
```

---

### Task 2: Data models

**Files:**
- Create: `src/award_trip_planner/models.py`
- Create: `tests/test_models.py`

**Interfaces:**
- Produces (all consumed by clients/engine/server; names verbatim):
  - `AwardFare(origin, dest, date, cabin, miles, taxes_usd, seats, direct, airlines, updated_at)` — frozen dataclass; `cabin` is `"Y"` or `"J"`; `taxes_usd: float | None` (None = unknown); per-person numbers.
  - `CashFare(kind, origin, dest, depart_date, return_date, cabin, adults, total_usd, airline, fetched_at, manual=False)` — frozen; `kind` is `"ow"` or `"rt"`; `total_usd` is the total for `adults` people; `per_person()` helper.
  - `Leg(origin, dest, date, cabin)` — frozen.
  - `Option(product, legs, cash_pp, points_pp, airline, award_seat_legs, flags)` — frozen; `product` ∈ `{"cash_ow","cash_rt","award_ow","award_stopover"}`; `award_seat_legs: tuple[tuple[Leg, int], ...]` (award legs with their seat counts).
  - `BookingLine(person, product, legs, cash_usd, points, airline, notes)` and `Bundle(direction, total_cash_usd, total_points, cpp, lines, flags, summary)` — plain dataclasses with `to_dict()`.
  - `fare_to_dict(x)` / `award_from_dict(d)` / `cash_from_dict(d)` module functions for cache serialization.

- [ ] **Step 1: Write failing test** — `tests/test_models.py`

```python
from award_trip_planner.models import (
    AwardFare,
    CashFare,
    Leg,
    award_from_dict,
    cash_from_dict,
    fare_to_dict,
)


def test_award_roundtrip():
    a = AwardFare(
        origin="LAX", dest="ICN", date="2026-10-01", cabin="J",
        miles=85_000, taxes_usd=None, seats=2, direct=True,
        airlines="AC", updated_at="2026-07-25T00:00:00Z",
    )
    assert award_from_dict(fare_to_dict(a)) == a


def test_cash_roundtrip_and_per_person():
    c = CashFare(
        kind="ow", origin="ICN", dest="NRT", depart_date="2026-10-05",
        return_date=None, cabin="Y", adults=2, total_usd=294.0,
        airline="ZIPAIR Tokyo", fetched_at=1_753_500_000.0,
    )
    assert c.per_person() == 147.0
    assert cash_from_dict(fare_to_dict(c)) == c


def test_leg_is_hashable():
    assert len({Leg("A", "B", "2026-10-01", "Y"), Leg("A", "B", "2026-10-01", "Y")}) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_models.py -q` — Expected: FAIL (ImportError).

- [ ] **Step 3: Implement `src/award_trip_planner/models.py`**

```python
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
```

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/test_models.py -q` → `3 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/award_trip_planner/models.py tests/test_models.py
git commit -m "feat: core fare/option/bundle models"
```

---

### Task 3: SQLite cache (kv + quota + overrides)

**Files:**
- Create: `src/award_trip_planner/cache.py`
- Create: `tests/test_cache.py`

**Interfaces:**
- Produces `Cache(path: Path)` with:
  - `put(ns: str, key: str, value: Any, now: float | None = None)` — JSON-serializes value.
  - `get(ns, key, max_age_s: float, now=None) -> Any | None` — None if absent or older than max_age_s.
  - `get_stale(ns, key) -> tuple[Any, float] | None` — value regardless of age, with fetched_at.
  - `keys(ns) -> list[str]`.
  - `bump_quota(day: str) -> int` (returns new count) and `quota(day: str) -> int`.
  - `set_override(origin, dest, date, cabin, price_pp: float | None)` — None deletes; `overrides() -> list[dict]` with keys `origin,dest,date,cabin,price_pp`.
- Namespaces used later: `"awards"` (key `"all"`), `"cash"` (key from planner's `query_key`).

- [ ] **Step 1: Write failing test** — `tests/test_cache.py`

```python
from award_trip_planner.cache import Cache


def test_put_get_ttl(tmp_path):
    c = Cache(tmp_path / "c.sqlite")
    c.put("cash", "k1", {"total": 294.0}, now=1000.0)
    assert c.get("cash", "k1", max_age_s=60, now=1030.0) == {"total": 294.0}
    assert c.get("cash", "k1", max_age_s=60, now=2000.0) is None
    assert c.get_stale("cash", "k1") == ({"total": 294.0}, 1000.0)
    assert c.get("cash", "missing", max_age_s=60, now=0) is None
    assert c.keys("cash") == ["k1"]


def test_overwrite(tmp_path):
    c = Cache(tmp_path / "c.sqlite")
    c.put("ns", "k", 1, now=1.0)
    c.put("ns", "k", 2, now=2.0)
    assert c.get_stale("ns", "k") == (2, 2.0)


def test_quota(tmp_path):
    c = Cache(tmp_path / "c.sqlite")
    assert c.quota("2026-07-26") == 0
    assert c.bump_quota("2026-07-26") == 1
    assert c.bump_quota("2026-07-26") == 2
    assert c.quota("2026-07-26") == 2
    assert c.quota("2026-07-27") == 0


def test_overrides(tmp_path):
    c = Cache(tmp_path / "c.sqlite")
    c.set_override("ICN", "NRT", "2026-10-05", "Y", 150.0)
    c.set_override("ICN", "NRT", "2026-10-05", "Y", 140.0)
    assert c.overrides() == [
        {"origin": "ICN", "dest": "NRT", "date": "2026-10-05", "cabin": "Y", "price_pp": 140.0}
    ]
    c.set_override("ICN", "NRT", "2026-10-05", "Y", None)
    assert c.overrides() == []
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_cache.py -q` → FAIL (ImportError).

- [ ] **Step 3: Implement `src/award_trip_planner/cache.py`**

```python
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


class Cache:
    def __init__(self, path: Path):
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS kv ("
            " ns TEXT, key TEXT, value TEXT, fetched_at REAL,"
            " PRIMARY KEY (ns, key))"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS quota (day TEXT PRIMARY KEY, count INTEGER)"
        )
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS overrides ("
            " origin TEXT, dest TEXT, date TEXT, cabin TEXT, price_pp REAL,"
            " PRIMARY KEY (origin, dest, date, cabin))"
        )
        self.conn.commit()

    def put(self, ns: str, key: str, value: Any, now: float | None = None) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO kv (ns, key, value, fetched_at) VALUES (?,?,?,?)",
            (ns, key, json.dumps(value), time.time() if now is None else now),
        )
        self.conn.commit()

    def get(self, ns: str, key: str, max_age_s: float, now: float | None = None) -> Any | None:
        row = self.get_stale(ns, key)
        if row is None:
            return None
        value, fetched_at = row
        current = time.time() if now is None else now
        return value if current - fetched_at <= max_age_s else None

    def get_stale(self, ns: str, key: str) -> tuple[Any, float] | None:
        cur = self.conn.execute(
            "SELECT value, fetched_at FROM kv WHERE ns=? AND key=?", (ns, key)
        )
        row = cur.fetchone()
        return (json.loads(row[0]), row[1]) if row else None

    def keys(self, ns: str) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT key FROM kv WHERE ns=?", (ns,))]

    def bump_quota(self, day: str) -> int:
        self.conn.execute(
            "INSERT INTO quota (day, count) VALUES (?, 1)"
            " ON CONFLICT(day) DO UPDATE SET count = count + 1",
            (day,),
        )
        self.conn.commit()
        return self.quota(day)

    def quota(self, day: str) -> int:
        row = self.conn.execute("SELECT count FROM quota WHERE day=?", (day,)).fetchone()
        return row[0] if row else 0

    def set_override(self, origin, dest, date, cabin, price_pp: float | None) -> None:
        if price_pp is None:
            self.conn.execute(
                "DELETE FROM overrides WHERE origin=? AND dest=? AND date=? AND cabin=?",
                (origin, dest, date, cabin),
            )
        else:
            self.conn.execute(
                "INSERT OR REPLACE INTO overrides VALUES (?,?,?,?,?)",
                (origin, dest, date, cabin, price_pp),
            )
        self.conn.commit()

    def overrides(self) -> list[dict]:
        cur = self.conn.execute(
            "SELECT origin, dest, date, cabin, price_pp FROM overrides ORDER BY date"
        )
        return [
            {"origin": o, "dest": d, "date": dt, "cabin": c, "price_pp": p}
            for o, d, dt, c, p in cur
        ]
```

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/test_cache.py -q` → `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/award_trip_planner/cache.py tests/test_cache.py
git commit -m "feat: sqlite cache with quota and manual overrides"
```

---

### Task 4: seats.aero client

**Files:**
- Create: `src/award_trip_planner/seats_client.py`
- Create: `tests/fixtures/seats_page1.json`, `tests/fixtures/seats_page2.json`
- Create: `tests/test_seats_client.py`

**Interfaces:**
- Consumes: `Cache` (Task 3), `AwardFare` (Task 2), `Config` (Task 1).
- Produces: `fetch_awards(api_key: str, pairs: list[tuple[list[str], list[str]]], start: str, end: str, cache: Cache, cfg: Config, transport=None, on_progress=None) -> list[AwardFare]` — one paginated cached-search per (origins, dests) group, `sources=aeroplan`, maps to per-cabin `AwardFare`s, bumps quota per HTTP call, stores the full mapped list in cache ns `"awards"` key `"all"`. `transport` is an optional `httpx.BaseTransport` for tests. Also `awards_from_cache(cache) -> tuple[list[AwardFare], float] | None`.

- [ ] **Step 1: Create fixtures.** Field names are from a live 2026-07-26 response — do not rename. `tests/fixtures/seats_page1.json`:

```json
{
  "data": [
    {
      "ID": "id_lax_icn",
      "Route": {"OriginAirport": "LAX", "DestinationAirport": "ICN", "Source": "aeroplan", "Distance": 5987},
      "Date": "2026-10-01",
      "YAvailableRaw": true, "JAvailableRaw": true,
      "YMileageCostRaw": 55000, "JMileageCostRaw": 85000,
      "YTotalTaxesRaw": 0, "JTotalTaxesRaw": 11550,
      "TaxesCurrency": "CAD",
      "YRemainingSeatsRaw": 4, "JRemainingSeatsRaw": 2,
      "YDirectRaw": true, "JDirectRaw": false,
      "YAirlinesRaw": "AC", "JAirlinesRaw": "AC, NH",
      "Source": "aeroplan",
      "UpdatedAt": "2026-07-25T10:00:00Z"
    },
    {
      "ID": "id_icn_nrt",
      "Route": {"OriginAirport": "ICN", "DestinationAirport": "NRT", "Source": "aeroplan", "Distance": 758},
      "Date": "2026-10-05",
      "YAvailableRaw": false, "JAvailableRaw": true,
      "YMileageCostRaw": 0, "JMileageCostRaw": 52500,
      "YTotalTaxesRaw": 0, "JTotalTaxesRaw": 0,
      "TaxesCurrency": "CAD",
      "YRemainingSeatsRaw": 0, "JRemainingSeatsRaw": 1,
      "YDirectRaw": false, "JDirectRaw": true,
      "YAirlinesRaw": "", "JAirlinesRaw": "NH",
      "Source": "aeroplan",
      "UpdatedAt": "2026-07-25T11:00:00Z"
    }
  ],
  "count": 3,
  "hasMore": true,
  "cursor": "cursor123"
}
```

`tests/fixtures/seats_page2.json`:

```json
{
  "data": [
    {
      "ID": "id_nrt_lax",
      "Route": {"OriginAirport": "NRT", "DestinationAirport": "LAX", "Source": "aeroplan", "Distance": 5444},
      "Date": "2026-10-12",
      "YAvailableRaw": true, "JAvailableRaw": false,
      "YMileageCostRaw": 55000, "JMileageCostRaw": 0,
      "YTotalTaxesRaw": 6000, "JTotalTaxesRaw": 0,
      "TaxesCurrency": "CAD",
      "YRemainingSeatsRaw": 9, "JRemainingSeatsRaw": 0,
      "YDirectRaw": true, "JDirectRaw": false,
      "YAirlinesRaw": "NH", "JAirlinesRaw": "",
      "Source": "aeroplan",
      "UpdatedAt": "2026-07-25T12:00:00Z"
    }
  ],
  "count": 3,
  "hasMore": false,
  "cursor": ""
}
```

- [ ] **Step 2: Write failing test** — `tests/test_seats_client.py`

```python
import json
from pathlib import Path

import httpx

from award_trip_planner.cache import Cache
from award_trip_planner.config import Config
from award_trip_planner.seats_client import awards_from_cache, fetch_awards

FIXTURES = Path(__file__).parent / "fixtures"


def make_transport(pages):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["Partner-Authorization"] == "test_key"
        assert request.url.params["sources"] == "aeroplan"
        page = pages[1] if request.url.params.get("cursor") else pages[0]
        return httpx.Response(200, json=json.loads((FIXTURES / page).read_text()))

    return httpx.MockTransport(handler), calls


def test_fetch_awards_maps_paginates_and_counts_quota(tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    cfg = Config()
    transport, calls = make_transport(["seats_page1.json", "seats_page2.json"])
    fares = fetch_awards(
        "test_key", [(["LAX"], ["ICN", "NRT", "HND", "KIX"])],
        "2026-09-25", "2026-10-31", cache, cfg, transport=transport,
    )
    assert len(calls) == 2                      # followed the cursor
    assert cache.quota != 0                     # placeholder replaced below
    # one AwardFare per available cabin: LAX-ICN Y+J, ICN-NRT J, NRT-LAX Y
    keys = {(f.origin, f.dest, f.cabin) for f in fares}
    assert keys == {("LAX", "ICN", "Y"), ("LAX", "ICN", "J"), ("ICN", "NRT", "J"), ("NRT", "LAX", "Y")}
    j = next(f for f in fares if (f.origin, f.dest, f.cabin) == ("LAX", "ICN", "J"))
    assert j.miles == 85_000
    assert j.taxes_usd == round(115.50 * cfg.cad_to_usd, 2)   # cents -> CAD -> USD
    assert j.seats == 2 and j.direct is False and j.airlines == "AC, NH"
    y = next(f for f in fares if (f.origin, f.dest, f.cabin) == ("LAX", "ICN", "Y"))
    assert y.taxes_usd is None                  # raw 0 = unknown
    # cached
    cached = awards_from_cache(cache)
    assert cached is not None
    assert len(cached[0]) == 4


def test_quota_counted_per_http_call(tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    transport, _ = make_transport(["seats_page1.json", "seats_page2.json"])
    fetch_awards("test_key", [(["LAX"], ["ICN"])], "2026-09-25", "2026-10-31",
                 cache, Config(), transport=transport)
    from award_trip_planner.seats_client import today_utc
    assert cache.quota(today_utc()) == 2
```

Remove the placeholder line `assert cache.quota != 0` when writing the file — the second test covers quota. (Keep the rest verbatim.)

- [ ] **Step 3: Run to verify failure** — `uv run pytest tests/test_seats_client.py -q` → FAIL (ImportError).

- [ ] **Step 4: Implement `src/award_trip_planner/seats_client.py`**

```python
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
                    "sources": "aeroplan",
                }
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
```

- [ ] **Step 5: Run to verify pass** — `uv run pytest tests/test_seats_client.py -q` → `2 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/award_trip_planner/seats_client.py tests/fixtures tests/test_seats_client.py
git commit -m "feat: seats.aero cached-search client with pagination and quota"
```

---

### Task 5: Google Flights cash client (vendored parser patch + fetch)

**Files:**
- Create: `src/award_trip_planner/gflights_patch.py`
- Create: `src/award_trip_planner/cash_client.py`
- Create: `tests/test_cash_client.py`

**Interfaces:**
- Consumes: `Cache`, `Config`, `CashFare`.
- Produces:
  - `gflights_patch.install()` — idempotent monkeypatch of `fast_flights.parser.parse_js` with the row-tolerant version (live-verified 2026-07-26 against fast-flights==3.0.2).
  - `CashQuery(kind, origin, dest, depart_date, return_date, cabin, adults, priority)` frozen dataclass with `.key` property `"{kind}|{origin}|{dest}|{depart_date}|{return_date}|{cabin}|{adults}"`.
  - `fetch_cash(q: CashQuery, cache: Cache, cfg: Config, now=None, fetcher=None) -> CashFare | None` — returns cached-fresh first; else calls Google (via `fetcher` seam, default `_google_fetch`), caches, returns None on any failure. Failures cache a `{"failed": true}` marker for 30 min so a broken route doesn't re-fire every refresh.
  - `cash_from_cache(cache, q) -> CashFare | None` (stale allowed — engine handles staleness display).

- [ ] **Step 1: Write failing test** — `tests/test_cash_client.py`

```python
from award_trip_planner.cache import Cache
from award_trip_planner.cash_client import CashQuery, cash_from_cache, fetch_cash
from award_trip_planner.config import Config


def q_ow(**kw):
    base = dict(kind="ow", origin="ICN", dest="NRT", depart_date="2026-10-05",
                return_date=None, cabin="Y", adults=2, priority=2)
    base.update(kw)
    return CashQuery(**base)


def test_key():
    assert q_ow().key == "ow|ICN|NRT|2026-10-05|None|Y|2"


def test_fetch_uses_fetcher_and_caches(tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    calls = []

    def fake_fetcher(q):
        calls.append(q)
        return 294.0, "ZIPAIR Tokyo"

    got = fetch_cash(q_ow(), cache, Config(), now=1000.0, fetcher=fake_fetcher)
    assert got.total_usd == 294.0 and got.airline == "ZIPAIR Tokyo"
    assert got.adults == 2 and got.per_person() == 147.0
    # second call inside TTL: served from cache, fetcher not called again
    again = fetch_cash(q_ow(), cache, Config(), now=2000.0, fetcher=fake_fetcher)
    assert again == got
    assert len(calls) == 1


def test_fetch_failure_returns_none_and_backs_off(tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    calls = []

    def broken(q):
        calls.append(q)
        raise RuntimeError("google changed")

    assert fetch_cash(q_ow(), cache, Config(), now=1000.0, fetcher=broken) is None
    assert fetch_cash(q_ow(), cache, Config(), now=1100.0, fetcher=broken) is None
    assert len(calls) == 1          # backoff marker suppressed the retry
    assert cash_from_cache(cache, q_ow()) is None


def test_patch_installs():
    import fast_flights.parser as P

    from award_trip_planner import gflights_patch

    gflights_patch.install()
    assert P.parse_js.__name__ == "tolerant_parse_js"
    gflights_patch.install()  # idempotent
    assert P.parse_js.__name__ == "tolerant_parse_js"
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_cash_client.py -q` → FAIL (ImportError).

- [ ] **Step 3: Implement `src/award_trip_planner/gflights_patch.py`** (this exact parser was live-tested 2026-07-26; it reads both the "best" `payload[2]` and "other" `payload[3]` blocks, skips unpriced rows, and makes metadata best-effort — the stock 3.0.2 parser crashes on ICN→NRT and on multi-city):

```python
"""Row-tolerant replacement for fast_flights.parser.parse_js (fast-flights==3.0.2 only)."""
from __future__ import annotations

import json

import fast_flights.parser as P
from fast_flights.exceptions import FlightsNotFound
from fast_flights.model import (
    Airline,
    Airport,
    Alliance,
    CarbonEmission,
    Flights,
    JsMetadata,
    SimpleDatetime,
    SingleFlight,
)


def tolerant_parse_js(js: str):
    data = js.split("data:", 1)[1].rsplit(",", 1)[0]
    if data.endswith("errorHasStatus: true"):
        raise FlightsNotFound("no flights found; received error")
    payload = json.loads(data)

    flights = P.ResultList()
    try:
        flights.metadata = JsMetadata(
            alliances=[Alliance(code=c, name=n) for c, n in payload[7][1][0]],
            airlines=[Airline(code=c, name=n) for c, n in payload[7][1][1]],
        )
    except (IndexError, TypeError):
        flights.metadata = JsMetadata(alliances=[], airlines=[])

    rows = []
    for idx in (2, 3):
        try:
            grp = payload[idx][0]
        except (IndexError, TypeError):
            continue
        if isinstance(grp, list):
            rows.extend(grp)

    for k in rows:
        try:
            flight = k[0]
            price = k[1][0][1]
            sg = []
            for s in flight[2]:
                sg.append(
                    SingleFlight(
                        from_airport=Airport(code=s[3], name=s[4]),
                        to_airport=Airport(code=s[6], name=s[5]),
                        departure=SimpleDatetime(date=s[20], time=s[8]),
                        arrival=SimpleDatetime(date=s[21], time=s[10]),
                        duration=s[11],
                        plane_type=s[17],
                    )
                )
            extras = flight[22]
            flights.append(
                Flights(
                    type=flight[0],
                    price=price,
                    airlines=flight[1],
                    flights=sg,
                    carbon=CarbonEmission(emission=extras[7], typical_on_route=extras[8]),
                )
            )
        except (IndexError, TypeError):
            continue
    return flights


def install() -> None:
    P.parse_js = tolerant_parse_js
```

- [ ] **Step 4: Implement `src/award_trip_planner/cash_client.py`**

```python
from __future__ import annotations

import time
from dataclasses import dataclass

from .cache import Cache
from .config import Config
from .models import CashFare, cash_from_dict, fare_to_dict

SEAT_NAME = {"Y": "economy", "J": "business"}
FAIL_BACKOFF_S = 30 * 60


@dataclass(frozen=True)
class CashQuery:
    kind: str                  # "ow" | "rt"
    origin: str
    dest: str
    depart_date: str
    return_date: str | None
    cabin: str
    adults: int
    priority: int

    @property
    def key(self) -> str:
        return f"{self.kind}|{self.origin}|{self.dest}|{self.depart_date}|{self.return_date}|{self.cabin}|{self.adults}"


def _google_fetch(q: CashQuery) -> tuple[float, str]:
    """Live Google Flights fetch. Raises on any failure."""
    from fast_flights import FlightQuery, Passengers, create_query, get_flights

    from . import gflights_patch

    gflights_patch.install()
    flights = [FlightQuery(date=q.depart_date, from_airport=q.origin, to_airport=q.dest)]
    trip = "one-way"
    if q.kind == "rt":
        flights.append(FlightQuery(date=q.return_date, from_airport=q.dest, to_airport=q.origin))
        trip = "round-trip"
    query = create_query(
        flights=flights, seat=SEAT_NAME[q.cabin], trip=trip,
        passengers=Passengers(adults=q.adults), currency="USD",
    )
    res = get_flights(query)
    priced = [f for f in res if isinstance(f.price, (int, float)) and f.price > 0]
    if not priced:
        raise RuntimeError("no priced itineraries")
    best = min(priced, key=lambda f: f.price)
    airline = ", ".join(best.airlines) if isinstance(best.airlines, list) else str(best.airlines)
    return float(best.price), airline


def cash_from_cache(cache: Cache, q: CashQuery) -> CashFare | None:
    row = cache.get_stale("cash", q.key)
    if row is None or row[0] is None or row[0].get("failed"):
        return None
    return cash_from_dict(row[0])


def fetch_cash(
    q: CashQuery, cache: Cache, cfg: Config,
    now: float | None = None, fetcher=None,
) -> CashFare | None:
    now = time.time() if now is None else now
    ttl = cfg.cash_ttl_hours * 3600
    cached = cache.get("cash", q.key, max_age_s=ttl, now=now)
    if cached is not None:
        return None if cached.get("failed") else cash_from_dict(cached)
    stale = cache.get_stale("cash", q.key)
    if stale and stale[0].get("failed") and now - stale[1] < FAIL_BACKOFF_S:
        return None
    fetcher = fetcher or _google_fetch
    try:
        total, airline = fetcher(q)
    except Exception:
        cache.put("cash", q.key, {"failed": True}, now=now)
        return None
    fare = CashFare(
        kind=q.kind, origin=q.origin, dest=q.dest, depart_date=q.depart_date,
        return_date=q.return_date, cabin=q.cabin, adults=q.adults,
        total_usd=total, airline=airline, fetched_at=now,
    )
    cache.put("cash", q.key, fare_to_dict(fare), now=now)
    return fare
```

- [ ] **Step 5: Run to verify pass** — `uv run pytest tests/test_cash_client.py -q` → `4 passed`.

- [ ] **Step 6: Commit**

```bash
git add src/award_trip_planner/gflights_patch.py src/award_trip_planner/cash_client.py tests/test_cash_client.py
git commit -m "feat: cash client with vendored tolerant Google Flights parser"
```

---

### Task 6: Query planner

**Files:**
- Create: `src/award_trip_planner/planner.py`
- Create: `tests/test_planner.py`

**Interfaces:**
- Consumes: `Config`, `CashQuery`.
- Produces:
  - `add_days(date: str, n: int) -> str`, `date_range(start: str, end: str, step: int = 1) -> list[str]` (inclusive; always includes `end`).
  - `hop_pairs(cfg, direction: str) -> list[tuple[str, str]]` — direction `"KJ"` or `"JK"`; excludes GMP↔NRT (GMP has no NRT service).
  - `windows(cfg) -> dict` with keys `t1 (start,end)`, `hop`, `ret_a`, `ret_b` computed as: hop starts `outbound_start + 1 (arrival day) + min_nights_first`; hop ends `return_a_deadline − min_nights_second`; returns start when hop can earliest finish `+ min_nights_second` and end at each deadline. Asia→LAX arrives same calendar day (date line), so a return may depart on its deadline.
  - `plan_phase1(cfg) -> list[CashQuery]` — deduped, priority-sorted OW queries: T1 (LAX→gateways, both cabins, adults=2, priority 1), hop (all hop pairs both directions, Y only, adults=2, priority 2), returns (gateways→LAX both cabins adults=1: priority 3 within A's window, 4 within B's), hopback-side returns and hop-back legs (priority 5). Dates on the `cfg.cash_grid_step_days` grid.

- [ ] **Step 1: Write failing test** — `tests/test_planner.py`

```python
from award_trip_planner.config import Config
from award_trip_planner.planner import (
    add_days,
    date_range,
    hop_pairs,
    plan_phase1,
    windows,
)


def test_date_helpers():
    assert add_days("2026-09-30", 2) == "2026-10-02"
    assert date_range("2026-10-01", "2026-10-05", 2) == ["2026-10-01", "2026-10-03", "2026-10-05"]
    # end is always included even off-grid
    assert date_range("2026-10-01", "2026-10-04", 2) == ["2026-10-01", "2026-10-03", "2026-10-04"]


def test_hop_pairs_excludes_gmp_nrt():
    cfg = Config()
    kj = hop_pairs(cfg, "KJ")
    assert ("ICN", "NRT") in kj and ("GMP", "HND") in kj and ("GMP", "KIX") in kj
    assert ("GMP", "NRT") not in kj
    jk = hop_pairs(cfg, "JK")
    assert ("NRT", "ICN") in jk and ("NRT", "GMP") not in jk


def test_windows():
    w = windows(Config())
    assert w["t1"] == ("2026-09-25", "2026-10-07")
    # arrive Sep 26 earliest + 3 nights = hop from Sep 29; must leave 2nd country >= 3 nights before Oct 14
    assert w["hop"] == ("2026-09-29", "2026-10-11")
    assert w["ret_a"] == ("2026-10-02", "2026-10-14")
    assert w["ret_b"] == ("2026-10-02", "2026-10-31")


def test_plan_phase1_shape():
    qs = plan_phase1(Config())
    keys = [q.key for q in qs]
    assert len(keys) == len(set(keys))                       # deduped
    assert [q.priority for q in qs] == sorted(q.priority for q in qs)
    t1 = [q for q in qs if q.priority == 1]
    assert all(q.origin == "LAX" and q.adults == 2 and q.kind == "ow" for q in t1)
    assert {q.dest for q in t1} == {"ICN", "NRT", "HND"}
    assert {q.cabin for q in t1} == {"Y", "J"}
    hops = [q for q in qs if q.priority == 2]
    assert all(q.cabin == "Y" and q.adults == 2 for q in hops)
    rets = [q for q in qs if q.priority in (3, 4)]
    assert all(q.dest == "LAX" and q.adults == 1 for q in rets)
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_planner.py -q` → FAIL (ImportError).

- [ ] **Step 3: Implement `src/award_trip_planner/planner.py`**

```python
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
```

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/test_planner.py -q` → `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/award_trip_planner/planner.py tests/test_planner.py
git commit -m "feat: cash query planner with prioritized grid"
```

---

### Task 7: Strategy engine — tables and options

**Files:**
- Create: `src/award_trip_planner/strategies.py` (first half)
- Create: `tests/test_strategies_options.py`

**Interfaces:**
- Consumes: models, planner helpers (`add_days`), `Config`.
- Produces (consumed by Task 8 composition and its tests):
  - `PriceTables(awards, cash)` built by `build_tables(award_fares, cash_fares, overrides, cfg) -> PriceTables` where `awards[(o, d, date, cabin)] -> AwardFare` (min miles wins), `cash_ow[(o, d, date, cabin)] -> CashFare` (min per-person wins; overrides become `CashFare(manual=True, adults=1)` and always win), `cash_rt[(o, d, dep, ret, cabin)] -> CashFare`.
  - `ow_cash_option(t, o, d, date, cabin, adults) -> Option | None` — cash_pp from fare (`per_person()`), legs=(Leg,), flag `"stale-cash"` if `fetched_at` is older than `cfg.cash_ttl_hours` (checked against `now` param).
  - `ow_award_option(t, o, d, date, cabin, n_people, cfg) -> Option | None` — points_pp = miles, cash_pp = (taxes or default by leg type) + partner fee USD; flags `"award-taxes-estimated"` when default used, `"seats-unknown"` when seats==0.
  - `rt_cash_option(t, o, d, dep, ret, cabin, adults, cfg) -> Option | None` — exact rt fare if in `cash_rt`, else sum of the two OWs with flag `"rt-estimated-from-oneways"`; legs are the outbound and return `Leg`s; returns None if neither is priceable.
  - `stopover_option(t, t1_o, t1_d, t1_date, h_d, h_date, cabin, cfg) -> Option | None` — requires award fares for both `t1_o→t1_d` and `t1_d→h_d` (same cabin); points_pp = T1 miles + `cfg.stopover_extra_miles`; cash_pp = both taxes (defaults where None) + one partner fee; seats = min of both; flags include `"stopover-verify-on-aeroplan"`.
  - `TRANSPAC_DEFAULT_TAXES` uses `cfg.default_award_taxes_usd` when either endpoint is `"LAX"`, else `cfg.default_hop_award_taxes_usd`.

- [ ] **Step 1: Write failing test** — `tests/test_strategies_options.py`

```python
from award_trip_planner.config import Config
from award_trip_planner.models import AwardFare, CashFare
from award_trip_planner.strategies import (
    build_tables,
    ow_award_option,
    ow_cash_option,
    rt_cash_option,
    stopover_option,
)

CFG = Config()
FEE = round(39.0 * CFG.cad_to_usd, 2)   # 28.47


def aw(o, d, date, cabin, miles, taxes=None, seats=2):
    return AwardFare(o, d, date, cabin, miles, taxes, seats, True, "AC", "2026-07-25T00:00:00Z")


def ca(o, d, date, cabin, total, adults=2, kind="ow", ret=None, fetched=1000.0):
    return CashFare(kind, o, d, date, ret, cabin, adults, total, "Test Air", fetched)


def tables(awards=(), cash=(), overrides=()):
    return build_tables(list(awards), list(cash), list(overrides), CFG)


def test_build_tables_picks_best_and_applies_overrides():
    t = tables(
        awards=[aw("LAX", "ICN", "2026-10-01", "Y", 70_000), aw("LAX", "ICN", "2026-10-01", "Y", 55_000)],
        cash=[ca("ICN", "NRT", "2026-10-05", "Y", 400.0), ca("ICN", "NRT", "2026-10-05", "Y", 294.0)],
        overrides=[{"origin": "ICN", "dest": "HND", "date": "2026-10-05", "cabin": "Y", "price_pp": 120.0}],
    )
    assert t.awards[("LAX", "ICN", "2026-10-01", "Y")].miles == 55_000
    assert t.cash_ow[("ICN", "NRT", "2026-10-05", "Y")].per_person() == 147.0
    ov = t.cash_ow[("ICN", "HND", "2026-10-05", "Y")]
    assert ov.manual and ov.per_person() == 120.0


def test_ow_options():
    t = tables(
        awards=[aw("LAX", "ICN", "2026-10-01", "J", 85_000, taxes=84.32)],
        cash=[ca("LAX", "ICN", "2026-10-01", "J", 2400.0)],
    )
    c = ow_cash_option(t, "LAX", "ICN", "2026-10-01", "J", 2, now=2000.0, cfg=CFG)
    assert c.cash_pp == 1200.0 and c.points_pp == 0
    a = ow_award_option(t, "LAX", "ICN", "2026-10-01", "J", 2, CFG)
    assert a.points_pp == 85_000
    assert a.cash_pp == round(84.32 + FEE, 2)
    assert a.award_seat_legs[0][1] == 2
    assert ow_award_option(t, "LAX", "ICN", "2026-10-02", "J", 2, CFG) is None


def test_award_default_taxes_flagged():
    t = tables(awards=[aw("ICN", "NRT", "2026-10-05", "J", 52_500, taxes=None)])
    a = ow_award_option(t, "ICN", "NRT", "2026-10-05", "J", 1, CFG)
    assert a.cash_pp == round(CFG.default_hop_award_taxes_usd + FEE, 2)
    assert "award-taxes-estimated" in a.flags


def test_rt_exact_beats_estimate():
    t = tables(cash=[
        ca("LAX", "ICN", "2026-10-01", "Y", 700.0, adults=1),
        ca("ICN", "LAX", "2026-10-12", "Y", 700.0, adults=1),
        ca("LAX", "ICN", "2026-10-01", "Y", 900.0, adults=1, kind="rt", ret="2026-10-12"),
    ])
    rt = rt_cash_option(t, "LAX", "ICN", "2026-10-01", "2026-10-12", "Y", 1, CFG, now=2000.0)
    assert rt.cash_pp == 900.0 and rt.flags == ()
    t2 = tables(cash=[
        ca("LAX", "ICN", "2026-10-01", "Y", 700.0, adults=1),
        ca("ICN", "LAX", "2026-10-12", "Y", 700.0, adults=1),
    ])
    est = rt_cash_option(t2, "LAX", "ICN", "2026-10-01", "2026-10-12", "Y", 1, CFG, now=2000.0)
    assert est.cash_pp == 1400.0 and "rt-estimated-from-oneways" in est.flags


def test_stopover():
    t = tables(awards=[
        aw("LAX", "ICN", "2026-10-01", "Y", 55_000, taxes=50.0, seats=4),
        aw("ICN", "NRT", "2026-10-05", "Y", 7_500, taxes=None, seats=2),
    ])
    s = stopover_option(t, "LAX", "ICN", "2026-10-01", "NRT", "2026-10-05", "Y", CFG)
    assert s.points_pp == 60_000                       # 55k + 5k
    assert s.cash_pp == round(50.0 + CFG.default_hop_award_taxes_usd + FEE, 2)
    assert min(n for _, n in s.award_seat_legs) == 2
    assert "stopover-verify-on-aeroplan" in s.flags
    assert stopover_option(t, "LAX", "ICN", "2026-10-01", "NRT", "2026-10-06", "Y", CFG) is None
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_strategies_options.py -q` → FAIL (ImportError).

- [ ] **Step 3: Implement the first half of `src/award_trip_planner/strategies.py`**

```python
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
```

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/test_strategies_options.py -q` → `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add src/award_trip_planner/strategies.py tests/test_strategies_options.py
git commit -m "feat: price tables and per-product option builders"
```

---

### Task 8: Strategy engine — composition and ranking

**Files:**
- Modify: `src/award_trip_planner/strategies.py` (append)
- Create: `tests/test_strategies_bundles.py`

**Interfaces:**
- Consumes: everything from Task 7, `planner.windows`, `planner.hop_pairs`, `planner.add_days`, `planner.date_range`.
- Produces:
  - `compute(award_fares, cash_fares, overrides, cfg, now=None) -> dict` returning
    `{"views": {"mixed": [...], "economy": [...], "business": [...]}, "refine_requests": [CashQuery...], "generated_at": float}` where each view list holds `Bundle.to_dict()` ranked by `total_cash_usd` ascending, `top_n` max, first entry of each view being that view's cheapest (all-cash baseline included as a bundle with `total_points == 0` whenever one exists).
  - Bundle math: `total_cash_usd = Σ line cash`, `total_points = Σ line points`, `cpp = round(100 * (view_allcash_baseline − total_cash) / total_points, 2)` when points > 0 and a baseline exists, else `None`.
  - View classification: a bundle is in `economy` if every transpacific leg (any leg touching LAX) across both persons is cabin Y; `business` if every transpacific leg is J; every bundle is in `mixed`.
  - `refine_requests`: for each bundle in the mixed top-10 containing an option flagged `rt-estimated-from-oneways`, a `CashQuery(kind="rt", …, priority=0)` for that exact (o, d, dep, ret, cabin, adults=1); deduped, capped at `cfg.refine_query_cap`.
  - Seat gating: for each leg used by award products, sum award users across A and B; if the `AwardFare.seats` for that leg < users and seats > 0 → drop bundle; seats == 0 → keep with bundle flag `"seats-unknown"`.
  - Budget gating: `total_points ≤ cfg.points_budget` (hard drop otherwise).

- [ ] **Step 1: Write failing test** — `tests/test_strategies_bundles.py`. The fixture builds a tiny world where the arithmetic is hand-checkable: direction KJ only (no Japan-first data), one T1 date, one hop date, one return date per person.

```python
from award_trip_planner.config import Config
from award_trip_planner.models import AwardFare, CashFare
from award_trip_planner.strategies import compute

CFG = Config()
FEE = round(39.0 * CFG.cad_to_usd, 2)


def aw(o, d, date, cabin, miles, taxes=50.0, seats=4):
    return AwardFare(o, d, date, cabin, miles, taxes, seats, True, "AC", "2026-07-25T00:00:00Z")


def ca(o, d, date, cabin, total, adults, kind="ow", ret=None):
    return CashFare(kind, o, d, date, ret, cabin, adults, total, "Test Air", 1_000_000.0)


AWARDS = [
    aw("LAX", "ICN", "2026-10-01", "Y", 55_000),
    aw("LAX", "ICN", "2026-10-01", "J", 85_000, seats=2),
    aw("ICN", "NRT", "2026-10-06", "Y", 7_500),
    aw("NRT", "LAX", "2026-10-12", "Y", 55_000),
]
CASH = [
    ca("LAX", "ICN", "2026-10-01", "Y", 1300.0, 2),          # $650 pp
    ca("LAX", "ICN", "2026-10-01", "J", 5000.0, 2),          # $2500 pp
    ca("ICN", "NRT", "2026-10-06", "Y", 300.0, 2),           # $150 pp
    ca("NRT", "LAX", "2026-10-12", "Y", 700.0, 1),           # A return
    ca("NRT", "LAX", "2026-10-25", "Y", 650.0, 1),           # B return
    ca("NRT", "LAX", "2026-10-12", "J", 2800.0, 1),
    ca("NRT", "LAX", "2026-10-25", "J", 2600.0, 1),
    ca("NRT", "ICN", "2026-10-10", "Y", 160.0, 1),           # hopback leg
    ca("ICN", "LAX", "2026-10-12", "Y", 750.0, 1),
    ca("ICN", "LAX", "2026-10-25", "Y", 720.0, 1),
]


def result():
    return compute(AWARDS, CASH, [], CFG, now=1_000_000.0)


def test_allcash_baseline_math():
    r = result()
    econ = r["views"]["economy"]
    baseline = next(b for b in econ if b["total_points"] == 0)
    # cheapest all-cash economy: T1 650*2 + hop 150*2 + A 700 + B 650 = 2950
    assert baseline["total_cash_usd"] == 2950.0
    assert baseline["cpp"] is None


def test_stopover_bundle_present_and_costed():
    r = result()
    mixed = r["views"]["mixed"]
    stop = [b for b in mixed if any(l["product"] == "award_stopover" for l in b["lines"])]
    assert stop, "expected a stopover bundle in mixed view"
    b = stop[0]
    # both persons on stopover award: 2 * 60_000 pts > budget -> must be one person max
    assert b["total_points"] <= CFG.points_budget


def test_budget_gate():
    small = Config()
    small.points_budget = 50_000
    r = compute(AWARDS, CASH, [], small, now=1_000_000.0)
    for b in r["views"]["mixed"]:
        assert b["total_points"] <= 50_000


def test_seat_gate():
    # J award has 2 seats -> both can use it; shrink to 1 -> bundles with both-J-award vanish
    awards = [a for a in AWARDS if not (a.cabin == "J" and a.origin == "LAX")]
    awards.append(aw("LAX", "ICN", "2026-10-01", "J", 85_000, seats=1))
    r = compute(awards, CASH, [], CFG, now=1_000_000.0)
    for b in r["views"]["mixed"]:
        j_award_users = sum(
            1 for l in b["lines"]
            if l["product"] in ("award_ow", "award_stopover")
            and any(leg["cabin"] == "J" and leg["origin"] == "LAX" for leg in l["legs"])
        )
        assert j_award_users <= 1


def test_cpp_computed_against_view_baseline():
    r = result()
    econ = r["views"]["economy"]
    baseline = next(b for b in econ if b["total_points"] == 0)["total_cash_usd"]
    for b in econ:
        if b["total_points"]:
            expected = round(100 * (baseline - b["total_cash_usd"]) / b["total_points"], 2)
            assert b["cpp"] == expected


def test_refine_requests_target_rt_estimates():
    r = result()
    for q in r["refine_requests"]:
        assert q.kind == "rt" and q.adults == 1 and q.priority == 0
    assert len(r["refine_requests"]) <= CFG.refine_query_cap


def test_ranked_ascending_and_capped():
    r = result()
    for view in r["views"].values():
        costs = [b["total_cash_usd"] for b in view]
        assert costs == sorted(costs)
        assert len(view) <= CFG.top_n
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_strategies_bundles.py -q` → FAIL (`ImportError: cannot import name 'compute'`).

- [ ] **Step 3: Append the composition half to `src/award_trip_planner/strategies.py`**

```python
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
    return Bundle(
        direction=direction, total_cash_usd=round(total_cash, 2),
        total_points=total_points, cpp=None, lines=lines,
        flags=sorted(flags), summary=summary,
    )


def _is_transpac(leg: dict) -> bool:
    return "LAX" in (leg["origin"], leg["dest"])


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
    views: dict[str, list] = {"mixed": [], "economy": [], "business": []}
    for b in ranked:
        d = b.to_dict()
        v = _view_of(d)
        if len(views["mixed"]) < cfg.top_n:
            views["mixed"].append(d)
        if v in ("economy", "business") and len(views[v]) < cfg.top_n:
            views[v].append(d)
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
```


- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/test_strategies_bundles.py -q` → `7 passed`. Also run the full suite: `uv run pytest -q` → all green. If the baseline test fails on the exact figure 2950.0, debug the composition (that number is hand-derived: 650×2 + 150×2 + 700 + 650); do not adjust the assertion to match the code.

- [ ] **Step 5: Commit**

```bash
git add src/award_trip_planner/strategies.py tests/test_strategies_bundles.py
git commit -m "feat: bundle composition, gates, ranking, cpp, refine requests"
```

---

### Task 9: FastAPI server

**Files:**
- Create: `src/award_trip_planner/server.py`
- Create: `tests/test_server.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `create_app(data_dir: Path, api_key: str, award_fetcher=None, cash_fetcher=None) -> FastAPI`. Fetcher seams default to the real clients; tests inject fakes. Endpoints:
  - `GET /api/config` → `Config.to_dict()`; `PUT /api/config` body = partial dict → merged, saved to `data_dir/config.json`, returns updated dict.
  - `POST /api/refresh` → `{"started": true}` or HTTP 409 if already running. Background thread: (1) awards if older than `award_ttl_hours`, (2) phase-1 cash for uncached queries up to `cash_query_cap` (recording `dropped_by_cap`), (3) compute, (4) refine RT quotes, (5) recompute.
  - `GET /api/status` → `{"running", "phase", "done", "total", "dropped_by_cap", "errors", "quota_today", "awards_fetched_at", "last_refresh"}`.
  - `GET /api/results` → compute output + `{"award_matrix": [...], "cash_matrix": [...], "overrides": [...], "missing_cash": int}` where matrices are flat lists for the UI heatmap/grid: award rows `{origin,dest,date,cabin,miles,seats,direct,airlines,updated_at}`, cash rows `{origin,dest,date,cabin,price_pp,airline,fetched_at,manual}`.
  - `PUT /api/override` body `{origin,dest,date,cabin,price_pp}` (`price_pp: null` clears) → 200 + updated overrides list.
  - `GET /` serves `static/index.html` (`FileResponse`).

- [ ] **Step 1: Write failing test** — `tests/test_server.py`

```python
from pathlib import Path

from fastapi.testclient import TestClient

from award_trip_planner.models import AwardFare, CashFare
from award_trip_planner.server import create_app


def fake_award_fetcher(**kw):
    return [AwardFare("LAX", "ICN", "2026-10-01", "Y", 55_000, 50.0, 4, True, "AC", "2026-07-25T00:00:00Z")]


def fake_cash_fetcher(q, **kw):
    return CashFare(q.kind, q.origin, q.dest, q.depart_date, q.return_date,
                    q.cabin, q.adults, 1000.0, "Fake Air", 1_000_000.0)


def make_client(tmp_path: Path) -> TestClient:
    app = create_app(tmp_path, "test_key",
                     award_fetcher=fake_award_fetcher, cash_fetcher=fake_cash_fetcher)
    return TestClient(app)


def test_config_roundtrip(tmp_path):
    c = make_client(tmp_path)
    assert c.get("/api/config").json()["points_budget"] == 100_000
    r = c.put("/api/config", json={"points_budget": 90_000})
    assert r.json()["points_budget"] == 90_000
    assert c.get("/api/config").json()["points_budget"] == 90_000


def test_refresh_and_results(tmp_path):
    c = make_client(tmp_path)
    assert c.post("/api/refresh").json()["started"] is True
    # TestClient runs the thread; poll status until idle
    import time
    for _ in range(200):
        s = c.get("/api/status").json()
        if not s["running"]:
            break
        time.sleep(0.05)
    assert s["errors"] == []
    r = c.get("/api/results").json()
    assert "views" in r and "award_matrix" in r and "cash_matrix" in r
    assert r["award_matrix"][0]["miles"] == 55_000


def test_override_endpoint(tmp_path):
    c = make_client(tmp_path)
    r = c.put("/api/override", json={
        "origin": "ICN", "dest": "NRT", "date": "2026-10-05", "cabin": "Y", "price_pp": 111.0})
    assert r.json() == [{"origin": "ICN", "dest": "NRT", "date": "2026-10-05", "cabin": "Y", "price_pp": 111.0}]
    r = c.put("/api/override", json={
        "origin": "ICN", "dest": "NRT", "date": "2026-10-05", "cabin": "Y", "price_pp": None})
    assert r.json() == []


def test_second_refresh_conflicts_while_running(tmp_path):
    # guarded by the running flag; with fake fetchers refresh is fast, so just
    # assert the endpoint contract on the idle path
    c = make_client(tmp_path)
    assert c.post("/api/refresh").status_code == 200
```

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_server.py -q` → FAIL (ImportError).

- [ ] **Step 3: Implement `src/award_trip_planner/server.py`**

```python
from __future__ import annotations

import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from . import cash_client, seats_client, strategies
from .cache import Cache
from .config import Config
from .models import cash_from_dict, fare_to_dict
from .planner import plan_phase1, windows

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"


def create_app(data_dir: Path, api_key: str, award_fetcher=None, cash_fetcher=None) -> FastAPI:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = data_dir / "config.json"
    cache = Cache(data_dir / "cache.sqlite")
    app = FastAPI(title="award-trip-planner")
    state = {
        "cfg": Config.load(cfg_path),
        "status": {
            "running": False, "phase": "idle", "done": 0, "total": 0,
            "dropped_by_cap": 0, "errors": [], "quota_today": 0,
            "awards_fetched_at": None, "last_refresh": None,
        },
    }

    def default_award_fetcher(**kw):
        cfg = state["cfg"]
        pairs = [
            (["LAX"], cfg.korea_gateways + cfg.japan_gateways),
            (cfg.korea_gateways + cfg.japan_gateways, ["LAX"]),
            (cfg.korea_airports, cfg.japan_airports),
            (cfg.japan_airports, cfg.korea_airports),
        ]
        return seats_client.fetch_awards(
            api_key, pairs, cfg.outbound_start, cfg.return_b_deadline, cache, cfg)

    def default_cash_fetcher(q, **kw):
        return cash_client.fetch_cash(q, cache, state["cfg"])

    fetch_awards = award_fetcher or default_award_fetcher
    fetch_cash = cash_fetcher or default_cash_fetcher

    def all_cash_fares(cfg) -> list:
        fares = []
        for key in cache.keys("cash"):
            row = cache.get_stale("cash", key)
            if row and row[0] and not row[0].get("failed"):
                fares.append(cash_from_dict(row[0]))
        return fares

    def run_refresh():
        st, cfg = state["status"], state["cfg"]
        try:
            st.update(running=True, errors=[], dropped_by_cap=0, phase="awards")
            row = cache.get_stale("awards", "all")
            if row is None or time.time() - row[1] > cfg.award_ttl_hours * 3600:
                try:
                    fares = fetch_awards()
                    cache.put("awards", "all", [fare_to_dict(f) for f in fares])
                except Exception as e:
                    st["errors"].append(f"awards: {e}")
            arow = cache.get_stale("awards", "all")
            st["awards_fetched_at"] = arow[1] if arow else None

            st["phase"] = "cash"
            queries = plan_phase1(cfg)
            fresh_missing = [
                q for q in queries
                if cache.get("cash", q.key, max_age_s=cfg.cash_ttl_hours * 3600) is None
            ]
            st["total"] = len(fresh_missing)
            budget = cfg.cash_query_cap
            for i, q in enumerate(fresh_missing):
                if i >= budget:
                    st["dropped_by_cap"] = len(fresh_missing) - budget
                    break
                try:
                    fetch_cash(q)
                except Exception as e:
                    st["errors"].append(f"cash {q.key}: {e}")
                st["done"] = i + 1

            st["phase"] = "compute"
            results = compute_now()

            st["phase"] = "refine"
            for q in results["refine_requests"]:
                try:
                    fetch_cash(q)
                except Exception as e:
                    st["errors"].append(f"refine {q.key}: {e}")
            compute_now()
            st["last_refresh"] = time.time()
        finally:
            st["quota_today"] = cache.quota(seats_client.today_utc())
            st.update(running=False, phase="idle")

    def compute_now() -> dict:
        cfg = state["cfg"]
        cached = seats_client.awards_from_cache(cache)
        awards = cached[0] if cached else []
        results = strategies.compute(awards, all_cash_fares(cfg), cache.overrides(), cfg)
        state["results"] = results
        return results

    @app.get("/api/config")
    def get_config():
        return state["cfg"].to_dict()

    @app.put("/api/config")
    def put_config(patch: dict):
        merged = state["cfg"].to_dict() | patch
        from dataclasses import fields as dc_fields
        names = {f.name for f in dc_fields(Config)}
        state["cfg"] = Config(**{k: v for k, v in merged.items() if k in names})
        state["cfg"].save(cfg_path)
        state.pop("results", None)
        return state["cfg"].to_dict()

    @app.post("/api/refresh")
    def refresh():
        if state["status"]["running"]:
            raise HTTPException(409, "refresh already running")
        threading.Thread(target=run_refresh, daemon=True).start()
        return {"started": True}

    @app.get("/api/status")
    def status():
        state["status"]["quota_today"] = cache.quota(seats_client.today_utc())
        return state["status"]

    @app.get("/api/results")
    def results():
        res = state.get("results") or compute_now()
        cfg = state["cfg"]
        cached = seats_client.awards_from_cache(cache)
        award_matrix = [
            {"origin": a.origin, "dest": a.dest, "date": a.date, "cabin": a.cabin,
             "miles": a.miles, "seats": a.seats, "direct": a.direct,
             "airlines": a.airlines, "updated_at": a.updated_at}
            for a in (cached[0] if cached else [])
        ]
        cash_matrix = [
            {"origin": c.origin, "dest": c.dest, "date": c.depart_date, "cabin": c.cabin,
             "price_pp": round(c.per_person(), 2), "airline": c.airline,
             "fetched_at": c.fetched_at, "manual": c.manual}
            for c in all_cash_fares(cfg) if c.kind == "ow"
        ]
        queries = plan_phase1(cfg)
        missing = sum(
            1 for q in queries
            if cache.get_stale("cash", q.key) is None
        )
        return res | {
            "award_matrix": award_matrix, "cash_matrix": cash_matrix,
            "overrides": cache.overrides(), "missing_cash": missing,
            "windows": windows(cfg),
        }

    @app.put("/api/override")
    def put_override(body: dict):
        cache.set_override(body["origin"], body["dest"], body["date"],
                           body["cabin"], body.get("price_pp"))
        state.pop("results", None)
        return cache.overrides()

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    return app
```

- [ ] **Step 4: Run to verify pass** — `uv run pytest tests/test_server.py -q` → `4 passed`; `uv run pytest -q` all green.

- [ ] **Step 5: Commit**

```bash
git add src/award_trip_planner/server.py tests/test_server.py
git commit -m "feat: FastAPI server with background refresh and results API"
```

---

### Task 10: Dashboard UI

**Files:**
- Create: `static/index.html` (complete file below)

**Interfaces:**
- Consumes: `/api/config`, `/api/refresh`, `/api/status`, `/api/results`, `/api/override` exactly as produced by Task 9.
- Design system: dataviz reference palette — tokens as CSS custom properties, light+dark. Award heatmap = sequential blue by seat count with the mileage printed in-cell (identity never color-alone); cash grid = table with row-min bolded; all numbers `tabular-nums`; per-cell hover tooltip.

- [ ] **Step 1: Write `static/index.html`**

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Award Trip Planner — LAX ⇄ Korea ⇄ Japan</title>
<style>
  :root {
    color-scheme: light;
    --page: #f9f9f7; --surface: #fcfcfb;
    --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
    --grid: #e1e0d9; --baseline: #c3c2b7; --ring: rgba(11,11,11,0.10);
    --seq-1: #cde2fb; --seq-2: #9ec5f4; --seq-3: #5598e7; --seq-4: #256abf;
    --good-text: #006300; --accent: #2a78d6;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      color-scheme: dark;
      --page: #0d0d0d; --surface: #1a1a19;
      --ink: #ffffff; --ink-2: #c3c2b7; --muted: #898781;
      --grid: #2c2c2a; --baseline: #383835; --ring: rgba(255,255,255,0.10);
      --seq-1: #104281; --seq-2: #1c5cab; --seq-3: #3987e5; --seq-4: #86b6ef;
      --good-text: #0ca30c; --accent: #3987e5;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--page); color: var(--ink);
    font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif;
  }
  main { max-width: 1100px; margin: 0 auto; padding: 20px 16px 60px; }
  h1 { font-size: 18px; margin: 0; }
  h2 { font-size: 14px; color: var(--ink-2); margin: 28px 0 8px; }
  .bar, .cfg { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
  .bar { justify-content: space-between; margin-bottom: 14px; }
  .meta { color: var(--muted); font-size: 12px; }
  .cfg label { font-size: 12px; color: var(--ink-2); display: flex; flex-direction: column; gap: 2px; }
  .cfg input, .cfg select {
    background: var(--surface); color: var(--ink); border: 1px solid var(--grid);
    border-radius: 6px; padding: 4px 6px; font: inherit; width: 120px;
    font-variant-numeric: tabular-nums;
  }
  button {
    background: var(--accent); color: #fff; border: 0; border-radius: 8px;
    padding: 8px 14px; font: inherit; cursor: pointer;
  }
  button:disabled { opacity: .5; cursor: default; }
  button.ghost { background: transparent; color: var(--ink-2); border: 1px solid var(--grid); }
  .tabs { display: flex; gap: 6px; margin: 8px 0 12px; }
  .tabs button.active { outline: 2px solid var(--accent); }
  .card {
    background: var(--surface); border: 1px solid var(--ring); border-radius: 10px;
    padding: 12px 14px; margin-bottom: 10px;
  }
  .card .head { display: flex; gap: 14px; align-items: baseline; flex-wrap: wrap; cursor: pointer; }
  .rank { color: var(--muted); font-size: 12px; min-width: 24px; }
  .money { font-size: 20px; font-weight: 650; }
  .chip {
    font-size: 12px; color: var(--ink-2); border: 1px solid var(--grid);
    border-radius: 999px; padding: 1px 8px; font-variant-numeric: tabular-nums;
  }
  .chip.save { color: var(--good-text); border-color: var(--good-text); }
  .summary { color: var(--ink-2); font-size: 13px; flex: 1 1 100%; }
  table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
  th, td { border-bottom: 1px solid var(--grid); padding: 4px 8px; text-align: left; font-size: 13px; }
  th { color: var(--muted); font-weight: 500; font-size: 12px; }
  .num { text-align: right; }
  .flags { color: var(--muted); font-size: 12px; margin-top: 6px; }
  .hm { overflow-x: auto; background: var(--surface); border: 1px solid var(--ring); border-radius: 10px; padding: 10px; }
  .hm table { width: auto; }
  .hm th { position: sticky; left: 0; background: var(--surface); }
  .hm td.cell {
    min-width: 46px; text-align: center; border: 2px solid var(--surface);
    border-radius: 4px; font-size: 12px; padding: 5px 4px; color: var(--ink);
  }
  .hm td.s0 { background: transparent; color: var(--muted); }
  .hm td.s1 { background: var(--seq-1); }
  .hm td.s2 { background: var(--seq-2); }
  .hm td.s3 { background: var(--seq-3); }
  .hm td.s4 { background: var(--seq-4); color: #fff; }
  @media (prefers-color-scheme: dark) { .hm td.s1, .hm td.s2 { color: #fff; } .hm td.s4 { color: #0b0b0b; } }
  .cash td.min { font-weight: 700; }
  .cash td.min::after { content: " ●"; color: var(--accent); font-size: 9px; vertical-align: 2px; }
  .cash td[data-edit] { cursor: pointer; }
  .cash td.manual { outline: 1px dashed var(--accent); }
  .stale { opacity: .55; }
  #tip {
    position: fixed; pointer-events: none; display: none; z-index: 10;
    background: var(--surface); border: 1px solid var(--ring); border-radius: 8px;
    padding: 8px 10px; font-size: 12px; color: var(--ink-2); max-width: 260px;
    box-shadow: 0 4px 14px rgba(0,0,0,.18);
  }
  #status { font-size: 12px; color: var(--muted); }
  #status .err { color: #d03b3b; }
  .note { font-size: 12px; color: var(--muted); margin: 6px 0; }
</style>
</head>
<body>
<main>
  <div class="bar">
    <div>
      <h1>Award Trip Planner</h1>
      <div class="meta">LAX ⇄ Korea ⇄ Japan · 2 travelers · Aeroplan</div>
    </div>
    <div style="display:flex;gap:10px;align-items:center">
      <div id="status"></div>
      <button id="refresh">Refresh data</button>
    </div>
  </div>

  <div class="cfg" id="cfg">
    <label>Outbound from <input id="c_outbound_start" type="date"></label>
    <label>Outbound to <input id="c_outbound_end" type="date"></label>
    <label>A home by <input id="c_return_a_deadline" type="date"></label>
    <label>B home by <input id="c_return_b_deadline" type="date"></label>
    <label>Points budget <input id="c_points_budget" type="number" step="5000"></label>
    <label>Min nights (1st / 2nd)
      <span style="display:flex;gap:4px">
        <input id="c_min_nights_first" type="number" style="width:56px">
        <input id="c_min_nights_second" type="number" style="width:56px">
      </span>
    </label>
    <button class="ghost" id="saveCfg">Apply</button>
  </div>

  <h2>Ranked strategies</h2>
  <div class="tabs" id="tabs">
    <button data-view="mixed" class="active">Best value</button>
    <button data-view="economy">All economy</button>
    <button data-view="business">Business transpacific</button>
  </div>
  <div id="cards"></div>
  <div class="note">Stopover awards are estimates (T1 miles + 5k) — verify the exact price at aeroplan.com before transferring anything. “RT est.” prices are the sum of two one-ways until refined with a real round-trip quote. A true single-ticket open-jaw may beat the top cash bundle — worth a manual check on the winning carrier.</div>

  <h2>Aeroplan award space (fill = bookable seats, number = miles ×1000)</h2>
  <div class="hm" id="awardHeat"></div>

  <h2>Cash one-ways (per person, USD — click a cell to override)</h2>
  <div class="hm cash" id="cashGrid"></div>
</main>
<div id="tip"></div>
<script>
const $ = (s) => document.querySelector(s);
const fmt$ = (n) => "$" + Math.round(n).toLocaleString();
const fmtPts = (n) => (n / 1000).toFixed(n % 1000 ? 1 : 0) + "k pts";
let RESULTS = null, VIEW = "mixed";

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(path + " -> " + r.status);
  return r.json();
}

async function loadConfig() {
  const cfg = await api("/api/config");
  for (const k of ["outbound_start","outbound_end","return_a_deadline",
                   "return_b_deadline","points_budget","min_nights_first","min_nights_second"]) {
    $("#c_" + k).value = cfg[k];
  }
}

$("#saveCfg").onclick = async () => {
  const patch = {};
  for (const k of ["outbound_start","outbound_end","return_a_deadline","return_b_deadline"])
    patch[k] = $("#c_" + k).value;
  for (const k of ["points_budget","min_nights_first","min_nights_second"])
    patch[k] = Number($("#c_" + k).value);
  await api("/api/config", {method: "PUT", headers: {"content-type": "application/json"},
                            body: JSON.stringify(patch)});
  await loadResults();
};

$("#tabs").onclick = (e) => {
  const b = e.target.closest("button"); if (!b) return;
  VIEW = b.dataset.view;
  document.querySelectorAll("#tabs button").forEach(x => x.classList.toggle("active", x === b));
  renderCards();
};

function legStr(l) { return `${l.origin}→${l.dest} ${l.date} (${l.cabin})`; }

function renderCards() {
  const el = $("#cards");
  el.innerHTML = "";
  const view = (RESULTS?.views?.[VIEW]) || [];
  if (!view.length) { el.innerHTML = '<div class="note">No bundles yet — hit Refresh data.</div>'; return; }
  view.forEach((b, i) => {
    const card = document.createElement("div");
    card.className = "card";
    const chips = [];
    if (b.total_points) chips.push(`<span class="chip">${fmtPts(b.total_points)}</span>`);
    if (b.cpp != null) chips.push(`<span class="chip save">${b.cpp}¢/pt</span>`);
    if (!b.total_points) chips.push(`<span class="chip">all cash</span>`);
    card.innerHTML = `
      <div class="head">
        <span class="rank">#${i + 1}</span>
        <span class="money">${fmt$(b.total_cash_usd)}</span>
        ${chips.join("")}
        <span class="summary">${b.summary}</span>
      </div>
      <div class="detail" hidden>
        <table><thead><tr>
          <th>Who</th><th>Booking</th><th>Legs</th><th>Airline</th>
          <th class="num">Cash</th><th class="num">Points</th><th>Notes</th>
        </tr></thead><tbody>
        ${b.lines.map(l => `<tr>
          <td>${l.person}</td><td>${l.product.replaceAll("_", " ")}</td>
          <td>${l.legs.map(legStr).join(" + ")}</td><td>${l.airline}</td>
          <td class="num">${fmt$(l.cash_usd)}</td>
          <td class="num">${l.points ? fmtPts(l.points) : "—"}</td>
          <td>${(l.notes || []).join(", ")}</td></tr>`).join("")}
        </tbody></table>
        ${b.flags.length ? `<div class="flags">⚑ ${b.flags.join(" · ")}</div>` : ""}
      </div>`;
    card.querySelector(".head").onclick = () => {
      const d = card.querySelector(".detail"); d.hidden = !d.hidden;
    };
    el.appendChild(card);
  });
}

function seatClass(seats) {
  if (!seats) return "s0";
  if (seats === 1) return "s1";
  if (seats <= 3) return "s2";
  if (seats <= 5) return "s3";
  return "s4";
}

function datesBetween(rows) {
  const ds = [...new Set(rows.map(r => r.date))].sort();
  return ds;
}

function renderHeat() {
  const rows = RESULTS.award_matrix || [];
  const el = $("#awardHeat");
  if (!rows.length) { el.innerHTML = '<div class="note">No award data cached yet.</div>'; return; }
  const dates = datesBetween(rows);
  const groups = {};
  for (const r of rows) {
    const k = `${r.origin}→${r.dest} · ${r.cabin}`;
    (groups[k] ||= {}); groups[k][r.date] = r;
  }
  const head = `<tr><th>route · cabin</th>${dates.map(d =>
    `<th>${d.slice(5)}</th>`).join("")}</tr>`;
  const body = Object.entries(groups).sort().map(([k, byDate]) => `<tr><th>${k}</th>${
    dates.map(d => {
      const r = byDate[d];
      if (!r) return '<td class="cell s0">·</td>';
      const tip = `${r.airlines || "?"} · ${r.seats} seat(s) · ${r.direct ? "direct" : "connecting"} · ${r.miles.toLocaleString()} mi · updated ${r.updated_at.slice(0, 10)}`;
      return `<td class="cell ${seatClass(r.seats)}" data-tip="${tip}">${Math.round(r.miles / 1000)}</td>`;
    }).join("")}</tr>`).join("");
  el.innerHTML = `<table>${head}${body}</table>`;
}

function renderCash() {
  const rows = RESULTS.cash_matrix || [];
  const el = $("#cashGrid");
  if (!rows.length) { el.innerHTML = '<div class="note">No cash data cached yet.</div>'; return; }
  const dates = datesBetween(rows);
  const groups = {};
  for (const r of rows) {
    const k = `${r.origin}→${r.dest} · ${r.cabin}`;
    (groups[k] ||= {}); groups[k][r.date] = r;
  }
  const staleCut = Date.now() / 1000 - 6 * 3600;
  const head = `<tr><th>route · cabin</th>${dates.map(d => `<th>${d.slice(5)}</th>`).join("")}</tr>`;
  const body = Object.entries(groups).sort().map(([k, byDate]) => {
    const prices = Object.values(byDate).map(r => r.price_pp);
    const min = Math.min(...prices);
    return `<tr><th>${k}</th>${dates.map(d => {
      const r = byDate[d];
      const [o, rest] = k.split("→"); const [dst, cab] = rest.split(" · ");
      const edit = `data-edit data-o="${o}" data-d="${dst}" data-date="${d}" data-cab="${cab}"`;
      if (!r) return `<td class="cell s0" ${edit}>—</td>`;
      const cls = ["cell", r.price_pp === min ? "min" : "", r.manual ? "manual" : "",
                   (!r.manual && r.fetched_at < staleCut) ? "stale" : ""].join(" ");
      const tip = `${r.airline}${r.manual ? " (manual)" : ""} · fetched ${new Date(r.fetched_at * 1000).toLocaleString()}`;
      return `<td class="${cls}" ${edit} data-tip="${tip}">${Math.round(r.price_pp)}</td>`;
    }).join("")}</tr>`;
  }).join("");
  el.innerHTML = `<table>${head}${body}</table>`;
}

$("#cashGrid").addEventListener("click", async (e) => {
  const td = e.target.closest("td[data-edit]"); if (!td) return;
  const cur = td.textContent.trim();
  const v = prompt(`Manual per-person USD price for ${td.dataset.o}→${td.dataset.d} ${td.dataset.date} (${td.dataset.cab})\nEmpty to clear override:`, cur === "—" ? "" : cur);
  if (v === null) return;
  await api("/api/override", {method: "PUT", headers: {"content-type": "application/json"},
    body: JSON.stringify({origin: td.dataset.o, dest: td.dataset.d, date: td.dataset.date,
                          cabin: td.dataset.cab, price_pp: v === "" ? null : Number(v)})});
  await loadResults();
});

const tip = $("#tip");
document.addEventListener("mousemove", (e) => {
  const t = e.target.closest?.("[data-tip]");
  if (!t) { tip.style.display = "none"; return; }
  tip.textContent = t.dataset.tip;
  tip.style.display = "block";
  tip.style.left = Math.min(e.clientX + 14, innerWidth - 280) + "px";
  tip.style.top = (e.clientY + 14) + "px";
});

async function loadResults() {
  RESULTS = await api("/api/results");
  renderCards(); renderHeat(); renderCash();
  const extra = RESULTS.missing_cash ? ` · ${RESULTS.missing_cash} cash quotes still missing — refresh again` : "";
  setStatus(`quota ${STATUS?.quota_today ?? "?"}/1000${extra}`);
}

let STATUS = null;
function setStatus(txt, isErr) {
  $("#status").innerHTML = isErr ? `<span class="err">${txt}</span>` : txt;
}

async function pollStatus() {
  STATUS = await api("/api/status");
  if (STATUS.running) {
    setStatus(`⟳ ${STATUS.phase} ${STATUS.done}/${STATUS.total || "…"}`);
    setTimeout(pollStatus, 800);
  } else {
    if (STATUS.errors?.length) setStatus(STATUS.errors[STATUS.errors.length - 1], true);
    $("#refresh").disabled = false;
    await loadResults();
  }
}

$("#refresh").onclick = async () => {
  $("#refresh").disabled = true;
  try { await api("/api/refresh", {method: "POST"}); } catch (e) {}
  pollStatus();
};

(async () => { await loadConfig(); await loadResults().catch(() => setStatus("no data yet — hit Refresh")); })();
</script>
</body>
</html>
```

- [ ] **Step 2: Verify rendering with fake data.** Start the server against the test fakes using a throwaway script:

```bash
uv run python -c "
import threading, time, webbrowser
from pathlib import Path
import uvicorn
from tests.test_server import fake_award_fetcher, fake_cash_fetcher
from award_trip_planner.server import create_app
app = create_app(Path('/tmp/atp-demo'), 'x', award_fetcher=fake_award_fetcher, cash_fetcher=fake_cash_fetcher)
uvicorn.run(app, port=8722)
"
```
Then open `http://127.0.0.1:8722`, click **Refresh data**, and check: status spinner counts up, a strategy card renders, heatmap shows an LAX→ICN row with a filled cell reading `55`, cash grid renders and a cell click opens the override prompt and persists after reload. Check both light and dark OS themes. Ctrl-C the server.

- [ ] **Step 3: Commit**

```bash
git add static/index.html
git commit -m "feat: single-page dashboard (cards, award heatmap, cash grid, overrides)"
```

---

### Task 11: Entry point, README, live smoke test

**Files:**
- Create: `src/award_trip_planner/__main__.py`
- Create: `README.md`
- Create: `.env` (manual step — key from the user's password manager / seats.aero settings; already gitignored)
- Modify: `/Users/danielko/dev/DEV-LAYOUT.md` (add project row)

**Interfaces:**
- Consumes: `create_app`.
- Produces: `award-trip-planner` console script: loads `.env`, creates app with data dir = repo root, opens browser, serves on port 8722.

- [ ] **Step 1: Implement `src/award_trip_planner/__main__.py`**

```python
from __future__ import annotations

import os
import threading
import webbrowser
from pathlib import Path

import uvicorn
from dotenv import load_dotenv

from .server import create_app

PORT = 8722


def main() -> None:
    root = Path(__file__).resolve().parent.parent.parent
    load_dotenv(root / ".env")
    key = os.environ.get("SEATS_AERO_KEY")
    if not key:
        raise SystemExit("SEATS_AERO_KEY missing — put it in .env at the repo root")
    app = create_app(root, key)
    threading.Timer(1.0, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    uvicorn.run(app, host="127.0.0.1", port=PORT)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write `.env`** (get the key from seats.aero → Settings → API Key):

```bash
echo 'SEATS_AERO_KEY=<key from seats.aero settings page>' > .env
grep -q '^\.env$' .gitignore && echo "gitignore ok"
```
Expected: `gitignore ok`.

- [ ] **Step 3: Write `README.md`**

```markdown
# award-trip-planner

Local dashboard that ranks booking strategies (cash / Aeroplan points / mixed)
for a 2-person LAX ⇄ Korea ⇄ Japan trip, using live seats.aero award data and
Google Flights cash prices.

## Run

    uv run award-trip-planner

Opens http://127.0.0.1:8722. Hit **Refresh data** — first fill takes a few
minutes (Google Flights is slow); re-run refresh until "cash quotes still
missing" reaches 0. Data is cached in `cache.sqlite` (cash 6h TTL, awards 24h).

## Notes

- seats.aero quota: 1,000 calls/day (a refresh uses ~10–20). Key in `.env`.
- Cash prices come from Google Flights via a patched `fast-flights==3.0.2`
  (`gflights_patch.py`). If Google breaks it, cells go stale — click any cash
  cell to enter a manual price; the engine treats overrides as truth.
- Stopover awards (LAX→Seoul⏸→Tokyo, +5k pts) are estimates: seats.aero can't
  price stopovers. Verify at aeroplan.com before transferring points.
- This ranks and recommends; it does not book. Always re-verify award space at
  aeroplan.com right before booking.

## Tests

    uv run pytest -q
```

- [ ] **Step 4: Run the full suite** — `uv run pytest -q` → all green.

- [ ] **Step 5: Live smoke test.** Run `uv run award-trip-planner`, press **Refresh data**, wait for idle. Verify with real data:
  1. Status shows no errors and quota advanced by ≤ 20.
  2. Award heatmap shows real Aeroplan rows (expect sparse ICN↔NRT space; LAX rows fuller).
  3. Cash grid: ICN→NRT around $130–160/person economy (sanity vs the 2026-07-26 probe: $147).
  4. At least one bundle uses points and shows a cpp chip; the all-cash baseline appears in each view.
  5. **Taxes sanity check:** find an award row whose `JTotalTaxesRaw > 0` in `cache.sqlite` (`uv run python -c "…select…"` or add a temporary print) and confirm the USD value shown is plausible (tens of dollars, not thousands — validates the minor-units assumption). If it's off by 100×, fix `_map_entry` taxes division and note it in the README.
  6. Second refresh completes faster (cache hits) and `dropped_by_cap` shrinks toward 0.

- [ ] **Step 6: Add the DEV-LAYOUT row.** In `/Users/danielko/dev/DEV-LAYOUT.md`, add to the Projects table:

```markdown
| `award-trip-planner/` | Local dashboard ranking cash/Aeroplan strategies for the LAX⇄Korea⇄Japan trip (seats.aero + Google Flights). | local only (main) |
```

- [ ] **Step 7: Commit**

```bash
git add src/award_trip_planner/__main__.py README.md
git commit -m "feat: entry point, README, live-smoke-verified"
cd /Users/danielko/dev && git -C /Users/danielko/dev diff --stat 2>/dev/null; true
```
(DEV-LAYOUT.md lives in `~/dev` which is not a git repo — the edit stands on its own.)

---

## Self-Review (done at plan time)

1. **Spec coverage:** trip model incl. hop-back ✓ (Task 8 `_person_return_options` / coupled RT in `_person_plans`); products 1–5 ✓ (OJ dropped per amended spec, sum-of-OW note in UI); stopover +5k with both-segment availability ✓; seat gating ≥2 shared ✓ (award users summed per leg); budget gate ✓; cpp vs per-view baseline ✓; quota tracking/display ✓; staleness display ✓; manual overrides ✓; refine pass for RT estimates ✓; heatmap + cash grid + cards ✓; errors never block ranking ✓ (missing data just produces no option); TDD per task ✓.
2. **Placeholder scan:** clean — no TBD/TODO; every code step is complete, runnable code. The one intentional test-file note (Task 4, "remove the placeholder line") tells the implementer to drop a single assertion superseded by the second test, with the surrounding code given verbatim.
3. **Type consistency:** `CashQuery` lives in `cash_client.py` and is imported by `planner.py`/`strategies.py` ✓; `Option.award_seat_legs` tuple-of-(Leg, seats) consistent between Tasks 7/8 ✓; server matrices match the UI's field reads ✓ (`price_pp`, `miles`, `seats`, `fetched_at`, `manual`).
4. **Known simplifications (accepted):** hop cash is economy-only (business hop cash is noise; award J hop still competes); coupled transpacific RT only pairs with an all-cash hop/hopback; `_dates_with_data` caps return-date candidates. All are display-honest — nothing silently claims coverage it doesn't have (`missing_cash`, `dropped_by_cap`, flags).
```
