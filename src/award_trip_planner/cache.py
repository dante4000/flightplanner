from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class Cache:
    def __init__(self, path: Path):
        self._lock = threading.Lock()
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
        with self._lock:
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
        with self._lock:
            cur = self.conn.execute(
                "SELECT value, fetched_at FROM kv WHERE ns=? AND key=?", (ns, key)
            )
            row = cur.fetchone()
            return (json.loads(row[0]), row[1]) if row else None

    def keys(self, ns: str) -> list[str]:
        with self._lock:
            return [r[0] for r in self.conn.execute("SELECT key FROM kv WHERE ns=?", (ns,))]

    def bump_quota(self, day: str) -> int:
        with self._lock:
            self.conn.execute(
                "INSERT INTO quota (day, count) VALUES (?, 1)"
                " ON CONFLICT(day) DO UPDATE SET count = count + 1",
                (day,),
            )
            self.conn.commit()
            row = self.conn.execute("SELECT count FROM quota WHERE day=?", (day,)).fetchone()
            return row[0] if row else 0

    def quota(self, day: str) -> int:
        with self._lock:
            row = self.conn.execute("SELECT count FROM quota WHERE day=?", (day,)).fetchone()
            return row[0] if row else 0

    def set_override(self, origin, dest, date, cabin, price_pp: float | None) -> None:
        with self._lock:
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
        with self._lock:
            cur = self.conn.execute(
                "SELECT origin, dest, date, cabin, price_pp FROM overrides ORDER BY date"
            )
            return [
                {"origin": o, "dest": d, "date": dt, "cabin": c, "price_pp": p}
                for o, d, dt, c, p in cur
            ]
