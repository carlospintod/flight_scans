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
METRO_AIRPORTS = REPO / "routes" / "metro_airports.yaml"

ROUTE_CLASS_NAMES = ("intra_eu", "medium", "long")
# Classified but deliberately never alerted (unsellable to the audience).
EXCLUDED_CLASS = "excluded"
# Destination we have no class for: it must NEVER inherit a floor or an
# aspiration weight by accident (that is what turned a Turin hop into a
# top-scoring "vuelazo"). Excluded from cross-sections and from candidacy.
UNCLASSIFIED = "unclassified"


class DealConfigError(ValueError):
    """Raised when the deal config fails validation."""


@dataclass(frozen=True)
class DealConfig:
    origins: tuple[str, ...]
    currency: str
    sweep_months_ahead: int
    sweep_sortings: tuple[str, ...]
    sweep_limits: dict[str, int]           # sorting -> limit
    watchlist: dict[str, tuple[str, ...]]  # route class -> destinations
    watchlist_months: dict[str, int]       # route class -> months of coverage
    floors: dict[str, int]
    crosssection_pct: float
    min_observations: int
    baseline_window_days: int
    mistake_pct_of_typical: float
    insights_floor: bool
    explore_enabled: bool
    explore_provider: str
    explore_calls_per_day: int
    explore_areas: tuple[str, ...]
    publish_channels: tuple[str, ...]
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
    metro_airports: dict[str, str] = field(default_factory=dict)  # metro -> airport


def load_deal_config(path: Path = DEFAULT_CONFIG) -> DealConfig:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    try:
        det = raw["detector"]
        guard = raw["guardrails"]
        verify = raw["verification"]
        scoring = raw["scoring"]
        drafting = raw["drafting"]
        publish = raw["publish"]
        explore = raw.get("explore") or {}
        cfg = DealConfig(
            origins=tuple(str(o).upper() for o in raw["origins"]),
            currency=str(raw.get("currency", "EUR")).upper(),
            sweep_months_ahead=int(raw.get("sweep_months_ahead", 6)),
            sweep_sortings=tuple(str(s) for s in
                                 raw.get("sweep_sortings", ["price"])),
            sweep_limits={
                "price": int(raw.get("sweep_limit_price", 100)),
                "route": int(raw.get("sweep_limit_route", 1000)),
            },
            watchlist={
                cls: tuple(str(d).upper() for d in dests or [])
                for cls, dests in (raw.get("watchlist") or {}).items()
            },
            watchlist_months={
                cls: int(n) for cls, n in
                (raw.get("watchlist_months") or {}).items()
            },
            floors={k: int(v) for k, v in det["floors"].items()},
            crosssection_pct=float(det["crosssection_pct"]),
            min_observations=int(det.get("min_observations", 8)),
            baseline_window_days=int(det.get("baseline_window_days", 60)),
            mistake_pct_of_typical=float(det["mistake_pct_of_typical"]),
            insights_floor=bool(det.get("insights_floor", False)),
            explore_enabled=bool(explore.get("enabled", False)),
            explore_provider=str(explore.get("provider", "serpapi")),
            explore_calls_per_day=int(explore.get("calls_per_day", 0)),
            explore_areas=tuple(str(a) for a in explore.get("areas") or []),
            publish_channels=tuple(
                str(c) for c in publish.get("channels", ["tg_private", "email"])),
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
            metro_airports=load_metro_airports(),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise DealConfigError(f"invalid deal config {path}: {exc}") from exc
    missing = [c for c in ROUTE_CLASS_NAMES if c not in cfg.floors]
    if missing:
        raise DealConfigError(f"floors missing route classes: {missing}")
    if not cfg.origins:
        raise DealConfigError("origins must not be empty")
    return cfg


def load_metro_airports(path: Path = METRO_AIRPORTS) -> dict[str, str]:
    """{metro IATA: airport IATA} for verification. Google Flights
    rejects Aviasales' city codes outright (see the file's header), so
    every metro-coded candidate needs an airport before it can be
    verified. Unmapped codes are passed through untouched."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    out: dict[str, str] = {}
    for metro, airport in raw.items():
        m, a = str(metro).upper(), str(airport).upper()
        if m == a:
            raise DealConfigError(
                f"{path}: {m} maps to itself — that is not a substitution")
        out[m] = a
    return out


def load_route_classes(path: Path = ROUTE_CLASSES) -> dict[str, str]:
    """{IATA: class} from routes/route_classes.yaml. Unknown IATAs are
    resolved to UNCLASSIFIED by classify_route (lib/dealgate) — never to
    a real class."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    known = set(ROUTE_CLASS_NAMES) | {EXCLUDED_CLASS}
    out: dict[str, str] = {}
    dupes: dict[str, list[str]] = {}
    for cls, codes in raw.items():
        if cls not in known:
            raise DealConfigError(f"unknown route class {cls!r} in {path}")
        for code in codes or []:
            code = str(code).upper()
            if code in out and out[code] != cls:
                dupes.setdefault(code, [out[code]]).append(cls)
            out[code] = cls
    # A code listed twice used to resolve by YAML key order — silently,
    # and against the author's intent (EVN sat in both `excluded` and
    # `medium`; `medium` won because it comes later in the file).
    if dupes:
        detail = "; ".join(f"{c}: {'+'.join(v)}" for c, v in sorted(dupes.items()))
        raise DealConfigError(f"{path}: destination in multiple classes — {detail}")
    return out
