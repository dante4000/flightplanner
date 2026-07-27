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
    date_grid_step_days: int = 1
    max_date_paths: int = 20_000
    max_options_per_segment: int = 8
    max_shapes: int = 4_000

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
