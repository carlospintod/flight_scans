"""Vuelazo deal-pipeline config loader (routes/vuelazo.yaml).

Same philosophy as lib/config.py: YAML is the seed, typed dataclasses are
the runtime shape, validation fails loudly at load time. Numeric defaults
here are config; their SEMANTICS are fixed by docs/DECISIONS.md D2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO / "routes" / "vuelazo.yaml"
ROUTE_CLASSES = REPO / "routes" / "route_classes.yaml"

ROUTE_CLASS_NAMES = ("intra_eu", "medium", "long")


class DealConfigError(ValueError):
    """Raised when the deal config fails validation."""


@dataclass(frozen=True)
class DealConfig:
    origins: tuple[str, ...]
    currency: str
    sweep_months_ahead: int
    watchlist: tuple[str, ...]
    floors: dict[str, int]
    crosssection_pct: float
    min_observations: int
    baseline_window_days: int
    mistake_pct_of_typical: float
    daily_candidate_cap: int
    route_cooldown_days: int
    cooldown_break_pct: float
    dedup_band_pct: float
    max_candidates_per_run: int
    live_tolerance_pct: float
    aspiration_weights: dict[str, float]
    draft_model: str
    draft_max_tokens: int
    alert_email_to: str
    email_from: str
    route_classes: dict[str, str] = field(default_factory=dict)  # IATA -> class


def load_deal_config(path: Path = DEFAULT_CONFIG) -> DealConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    try:
        det = raw["detector"]
        guard = raw["guardrails"]
        verify = raw["verification"]
        scoring = raw["scoring"]
        drafting = raw["drafting"]
        publish = raw["publish"]
        cfg = DealConfig(
            origins=tuple(str(o).upper() for o in raw["origins"]),
            currency=str(raw.get("currency", "EUR")).upper(),
            sweep_months_ahead=int(raw.get("sweep_months_ahead", 2)),
            watchlist=tuple(str(d).upper() for d in raw.get("watchlist", [])),
            floors={k: int(v) for k, v in det["floors"].items()},
            crosssection_pct=float(det["crosssection_pct"]),
            min_observations=int(det.get("min_observations", 8)),
            baseline_window_days=int(det.get("baseline_window_days", 60)),
            mistake_pct_of_typical=float(det["mistake_pct_of_typical"]),
            daily_candidate_cap=int(guard["daily_candidate_cap"]),
            route_cooldown_days=int(guard["route_cooldown_days"]),
            cooldown_break_pct=float(guard["cooldown_break_pct"]),
            dedup_band_pct=float(guard["dedup_band_pct"]),
            max_candidates_per_run=int(verify["max_candidates_per_run"]),
            live_tolerance_pct=float(verify["live_tolerance_pct"]),
            aspiration_weights={k: float(v)
                                for k, v in scoring["aspiration_weights"].items()},
            draft_model=str(drafting["model"]),
            draft_max_tokens=int(drafting["max_tokens"]),
            alert_email_to=str(publish["alert_email_to"]),
            email_from=str(publish["email_from"]),
            route_classes=load_route_classes(),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DealConfigError(f"invalid deal config {path}: {exc}") from exc
    missing = [c for c in ROUTE_CLASS_NAMES if c not in cfg.floors]
    if missing:
        raise DealConfigError(f"floors missing route classes: {missing}")
    if not cfg.origins:
        raise DealConfigError("origins must not be empty")
    return cfg


def load_route_classes(path: Path = ROUTE_CLASSES) -> dict[str, str]:
    """{IATA: class} from routes/route_classes.yaml; unknown IATAs are
    resolved to 'medium' by classify_route (lib/dealgate)."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    out: dict[str, str] = {}
    for cls, codes in raw.items():
        if cls not in ROUTE_CLASS_NAMES:
            raise DealConfigError(f"unknown route class {cls!r} in {path}")
        for code in codes or []:
            out[str(code).upper()] = cls
    return out
