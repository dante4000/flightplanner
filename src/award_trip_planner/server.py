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
        # Pinned to Aeroplan on purpose. This dashboard has no program dimension:
        # build_tables keys awards by (origin, dest, date, cabin) only, the fee and
        # captions are hardcoded Aeroplan, and the heatmap would show one arbitrary
        # program per cell. Fetching every program here would present, say, a Virgin
        # Atlantic fare as an Aeroplan redemption. The program-aware v2 solver reads
        # the same cache without this filter.
        return seats_client.fetch_awards(
            api_key, pairs, cfg.outbound_start, cfg.return_b_deadline, cache, cfg,
            sources="aeroplan")

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
        try:
            st, cfg = state["status"], state["cfg"]
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
                    fare = fetch_cash(q)
                    if fare is not None:
                        cache.put("cash", q.key, fare_to_dict(fare), now=fare.fetched_at)
                except Exception as e:
                    st["errors"].append(f"cash {q.key}: {e}")
                st["done"] = i + 1

            st["phase"] = "compute"
            try:
                results = compute_now()
            except Exception as e:
                st["errors"].append(f"compute: {e}")
                results = {"refine_requests": []}

            st["phase"] = "refine"
            for q in results["refine_requests"]:
                try:
                    fare = fetch_cash(q)
                    if fare is not None:
                        cache.put("cash", q.key, fare_to_dict(fare), now=fare.fetched_at)
                except Exception as e:
                    st["errors"].append(f"refine {q.key}: {e}")
            try:
                compute_now()
            except Exception as e:
                st["errors"].append(f"compute: {e}")
            st["last_refresh"] = time.time()
        finally:
            state["status"]["quota_today"] = cache.quota(seats_client.today_utc())
            state["status"].update(running=False, phase="idle")

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
        state["status"]["running"] = True
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
        cash_matrix_by_key = {
            (c.origin, c.dest, c.depart_date, c.cabin): {
                "origin": c.origin, "dest": c.dest, "date": c.depart_date, "cabin": c.cabin,
                "price_pp": round(c.per_person(), 2), "airline": c.airline,
                "fetched_at": c.fetched_at, "manual": c.manual}
            for c in all_cash_fares(cfg) if c.kind == "ow"
        }
        for ov in cache.overrides():
            key = (ov["origin"], ov["dest"], ov["date"], ov["cabin"])
            cash_matrix_by_key[key] = {
                "origin": ov["origin"], "dest": ov["dest"], "date": ov["date"], "cabin": ov["cabin"],
                "price_pp": ov["price_pp"], "airline": "manual",
                "fetched_at": time.time(), "manual": True,
            }
        cash_matrix = list(cash_matrix_by_key.values())
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
