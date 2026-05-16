"""Route config loader and validator.

A route config is a YAML file describing a flexible-dates corridor: origins,
destinations, search window, stay preferences, sweep parameters, alert
parameters. The same code runs for any route — the file is the only
route-specific input.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised when a route config fails validation."""


@dataclass(frozen=True)
class SearchWindow:
    earliest_departure: date
    latest_return: date


@dataclass(frozen=True)
class StayPreferences:
    min_days: int
    max_days: int


@dataclass(frozen=True)
class SweepParams:
    outbound_window_days: int
    return_window_days: int
    overlap_days: int
    cadence_days: int
    # Smart-skip: if not None, a window will be skipped in subsequent
    # sweeps when its most recent snapshot had no prices at or below
    # this threshold, provided its earliest outbound is more than
    # `skip_grace_days` away. Both must be set for skip to apply.
    skip_if_min_above: int | None = None
    skip_grace_days: int | None = None


@dataclass(frozen=True)
class FollowupParams:
    # When both are None, fall back to the legacy is_lowest_price OR
    # alerts-baseline candidate selection.
    watch_below_price: int | None = None
    drop_above_price: int | None = None


@dataclass(frozen=True)
class AlertParams:
    drop_threshold_pct: float
    baseline_window_days: int
    min_observations: int


@dataclass(frozen=True)
class RouteConfig:
    name: str
    origins: tuple[str, ...]
    destinations: tuple[str, ...]
    search_window: SearchWindow
    stay: StayPreferences
    currency: str
    sweep: SweepParams
    followup: FollowupParams
    alerts: AlertParams

    def to_json(self) -> str:
        """Stable JSON snapshot for persisting to the routes table."""
        return json.dumps(asdict(self), sort_keys=True, default=_default)


def _default(value: Any) -> Any:
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"unserializable type: {type(value).__name__}")


def load_route(path: str | Path) -> RouteConfig:
    """Read and validate a route YAML config from disk."""
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top-level must be a mapping")
    try:
        return _from_dict(raw)
    except ConfigError as exc:
        raise ConfigError(f"{path}: {exc}") from None


def _from_dict(raw: dict[str, Any]) -> RouteConfig:
    route = _require_mapping(raw, "route")
    name = _require_str(route, "name", path="route.name")
    origins = _require_iata_list(route, "origins", path="route.origins")
    destinations = _require_iata_list(route, "destinations", path="route.destinations")

    sw_raw = _require_mapping(raw, "search_window")
    earliest = _require_date(sw_raw, "earliest_departure", path="search_window.earliest_departure")
    latest = _require_date(sw_raw, "latest_return", path="search_window.latest_return")
    if latest <= earliest:
        raise ConfigError("search_window.latest_return must be after earliest_departure")

    stay_raw = _require_mapping(raw, "stay_preferences")
    min_days = _require_positive_int(stay_raw, "min_days", path="stay_preferences.min_days")
    max_days = _require_positive_int(stay_raw, "max_days", path="stay_preferences.max_days")
    if max_days < min_days:
        raise ConfigError("stay_preferences.max_days must be >= min_days")

    currency = _require_str(raw, "currency", path="currency")
    if len(currency) != 3 or not currency.isalpha() or currency != currency.upper():
        raise ConfigError("currency must be a 3-letter uppercase code (e.g. EUR)")

    sweep_raw = _require_mapping(raw, "sweep")
    outbound = _require_positive_int(sweep_raw, "outbound_window_days", path="sweep.outbound_window_days")
    ret = _require_positive_int(sweep_raw, "return_window_days", path="sweep.return_window_days")
    overlap = _require_nonneg_int(sweep_raw, "overlap_days", path="sweep.overlap_days")
    cadence = _require_positive_int(sweep_raw, "cadence_days", path="sweep.cadence_days")
    skip_if_min_above = _optional_positive_int(
        sweep_raw, "skip_if_min_above", path="sweep.skip_if_min_above")
    skip_grace_days = _optional_nonneg_int(
        sweep_raw, "skip_grace_days", path="sweep.skip_grace_days")
    # 200-combo API cap on the calendar engine.
    if outbound * ret > 200:
        raise ConfigError(
            f"sweep window {outbound}x{ret}={outbound*ret} exceeds the 200-combo "
            "calendar API cap"
        )
    if overlap >= outbound:
        raise ConfigError("sweep.overlap_days must be < outbound_window_days")

    # `followup` block is optional. When omitted, thresholds are None and
    # the legacy candidate-selection rule applies.
    followup_raw = raw.get("followup") or {}
    if not isinstance(followup_raw, dict):
        raise ConfigError("followup: must be a mapping when present")
    watch_below = _optional_positive_int(
        followup_raw, "watch_below_price", path="followup.watch_below_price")
    drop_above = _optional_positive_int(
        followup_raw, "drop_above_price", path="followup.drop_above_price")
    if watch_below is not None and drop_above is not None and drop_above < watch_below:
        raise ConfigError("followup.drop_above_price must be >= watch_below_price")

    alerts_raw = _require_mapping(raw, "alerts")
    drop_pct = _require_positive_float(alerts_raw, "drop_threshold_pct",
                                       path="alerts.drop_threshold_pct")
    baseline = _require_positive_int(alerts_raw, "baseline_window_days",
                                     path="alerts.baseline_window_days")
    min_obs = _require_positive_int(alerts_raw, "min_observations",
                                    path="alerts.min_observations")

    return RouteConfig(
        name=name,
        origins=origins,
        destinations=destinations,
        search_window=SearchWindow(earliest_departure=earliest, latest_return=latest),
        stay=StayPreferences(min_days=min_days, max_days=max_days),
        currency=currency,
        sweep=SweepParams(
            outbound_window_days=outbound,
            return_window_days=ret,
            overlap_days=overlap,
            cadence_days=cadence,
            skip_if_min_above=skip_if_min_above,
            skip_grace_days=skip_grace_days,
        ),
        followup=FollowupParams(
            watch_below_price=watch_below,
            drop_above_price=drop_above,
        ),
        alerts=AlertParams(
            drop_threshold_pct=drop_pct,
            baseline_window_days=baseline,
            min_observations=min_obs,
        ),
    )


# --- helpers --------------------------------------------------------------


def _require_mapping(d: dict[str, Any], key: str) -> dict[str, Any]:
    val = d.get(key)
    if not isinstance(val, dict):
        raise ConfigError(f"missing or non-mapping key: {key}")
    return val


def _require_str(d: dict[str, Any], key: str, *, path: str) -> str:
    val = d.get(key)
    if not isinstance(val, str) or not val.strip():
        raise ConfigError(f"{path}: expected non-empty string")
    return val.strip()


def _require_iata_list(d: dict[str, Any], key: str, *, path: str) -> tuple[str, ...]:
    val = d.get(key)
    if not isinstance(val, list) or not val:
        raise ConfigError(f"{path}: expected non-empty list of IATA codes")
    out: list[str] = []
    for i, code in enumerate(val):
        if not isinstance(code, str) or len(code) != 3 or not code.isalpha():
            raise ConfigError(f"{path}[{i}]: expected 3-letter IATA code, got {code!r}")
        out.append(code.upper())
    return tuple(out)


def _require_date(d: dict[str, Any], key: str, *, path: str) -> date:
    val = d.get(key)
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, str):
        try:
            return date.fromisoformat(val)
        except ValueError as exc:
            raise ConfigError(f"{path}: invalid date {val!r}") from exc
    raise ConfigError(f"{path}: expected YYYY-MM-DD date")


def _require_positive_int(d: dict[str, Any], key: str, *, path: str) -> int:
    val = d.get(key)
    if not isinstance(val, int) or isinstance(val, bool) or val <= 0:
        raise ConfigError(f"{path}: expected positive integer, got {val!r}")
    return val


def _require_nonneg_int(d: dict[str, Any], key: str, *, path: str) -> int:
    val = d.get(key)
    if not isinstance(val, int) or isinstance(val, bool) or val < 0:
        raise ConfigError(f"{path}: expected non-negative integer, got {val!r}")
    return val


def _optional_positive_int(d: dict[str, Any], key: str, *, path: str) -> int | None:
    val = d.get(key)
    if val is None:
        return None
    if not isinstance(val, int) or isinstance(val, bool) or val <= 0:
        raise ConfigError(f"{path}: expected positive integer or null, got {val!r}")
    return val


def _optional_nonneg_int(d: dict[str, Any], key: str, *, path: str) -> int | None:
    val = d.get(key)
    if val is None:
        return None
    if not isinstance(val, int) or isinstance(val, bool) or val < 0:
        raise ConfigError(f"{path}: expected non-negative integer or null, got {val!r}")
    return val


def _require_positive_float(d: dict[str, Any], key: str, *, path: str) -> float:
    val = d.get(key)
    if isinstance(val, bool) or not isinstance(val, (int, float)) or val <= 0:
        raise ConfigError(f"{path}: expected positive number, got {val!r}")
    return float(val)
