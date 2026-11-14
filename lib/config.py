"""Route config loader and validator.

A route config is a YAML file describing a flexible-dates corridor: origins,
destinations, search window, stay preferences, sweep parameters, alert
parameters. The same code runs for any route — the file is the only
route-specific input.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised when a route config fails validation."""


# Rolling windows advance in whole steps counted from this Monday, so a
# 7-day step always moves the window on a Monday.
ROLLING_EPOCH = date(2024, 1, 1)


def add_months(d: date, months: int) -> date:
    """`d` plus whole calendar months, clamped to the month's last day
    (31 Jan + 1 month = 28/29 Feb)."""
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    first_of_next = date(y + (m == 12), m % 12 + 1, 1)
    return date(y, m, min(d.day, (first_of_next - timedelta(days=1)).day))


@dataclass(frozen=True)
class RollingWindow:
    """A departure window defined relative to today (Carlos, 2026-11-14:
    three months, from next month, never before 27 Dec 2026, no end
    date). Resolved to concrete dates on every load.

    The window moves in `step_days` steps, not daily: the calendar
    sweep's freshness rule treats a moved window as partly unswept, so a
    daily drift would make strips due on every scan."""
    start_months: int           # first departure = step date + this
    span_months: int            # last departure = step date + start + span
    not_before: date | None = None
    step_days: int = 7

    def step_date(self, today: date) -> date:
        n = (today - ROLLING_EPOCH).days // self.step_days
        return ROLLING_EPOCH + timedelta(days=n * self.step_days)

    def resolve(self, today: date, max_stay: int) -> "SearchWindow":
        anchor = self.step_date(today)
        first = add_months(anchor, self.start_months)
        last = add_months(anchor, self.start_months + self.span_months)
        if self.not_before is not None and first < self.not_before:
            first = self.not_before
        last = max(last, first)
        return SearchWindow(earliest_departure=first,
                            latest_return=last + timedelta(days=max_stay),
                            latest_departure=last, rolling=self)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"start_months": self.start_months,
                               "span_months": self.span_months,
                               "step_days": self.step_days}
        if self.not_before is not None:
            out["not_before"] = self.not_before.isoformat()
        return out


@dataclass(frozen=True)
class SearchWindow:
    earliest_departure: date
    latest_return: date
    # Optional hard cap on the departure date. Without it the last
    # departure is implied by latest_return - min_stay, which can't say
    # "depart by 1 Mar, stay up to 90 days". None = no cap (configs that
    # omit it serialize byte-identically to before).
    latest_departure: date | None = None
    # Set when the dates above were resolved from a rolling spec (they
    # are then today's resolution, not fixed config).
    rolling: RollingWindow | None = None

    def last_departure(self, min_stay: int) -> date:
        """Latest departure that still fits `min_stay` days before
        latest_return, capped by latest_departure. One-way passes 0."""
        d = self.latest_return - timedelta(days=min_stay)
        if self.latest_departure is not None and self.latest_departure < d:
            d = self.latest_departure
        return d


@dataclass(frozen=True)
class StayPreferences:
    min_days: int
    max_days: int
    # Price every `step_days`-th stay length from min_days, plus max_days
    # (30, 37, ..., 86, 90 for step 7). 1 = every stay. The calendar
    # sweep buys one call per stay length, so the step is the budget.
    step_days: int = 1

    def sampled(self) -> tuple[int, ...]:
        """The stay lengths this search prices, ascending."""
        lo = max(1, self.min_days)
        if self.max_days < lo:
            return ()
        out = list(range(lo, self.max_days + 1, max(1, self.step_days)))
        if out[-1] != self.max_days:
            out.append(self.max_days)
        return tuple(out)


@dataclass(frozen=True)
class SweepParams:
    # LEGACY, ignored by the planner: window geometry is now derived from
    # the stay range (see lib/sweep.plan_windows). Kept as fields with
    # defaults so existing YAML files and SweepParams(14, 14, 3, 14)
    # constructions keep working.
    outbound_window_days: int = 14
    return_window_days: int = 14
    overlap_days: int = 3
    cadence_days: int = 14
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
    trip_type: str = "round_trip"   # 'round_trip' | 'one_way'

    @property
    def is_one_way(self) -> bool:
        return self.trip_type == "one_way"

    def to_json(self) -> str:
        """Stable JSON snapshot for persisting to the routes table.

        Serializes the CANONICAL config shape (`route_to_yaml_dict`) —
        the same mapping the YAML files hold and `_from_dict` validates.
        One persisted shape everywhere; `route_from_json` reads it back.
        (Legacy sweep window-size keys are deliberately dropped, same as
        the YAML write-back.)
        """
        return json.dumps(route_to_yaml_dict(self), sort_keys=True)


def load_route(path: str | Path, *, today: date | None = None) -> RouteConfig:
    """Read and validate a route YAML config from disk. `today` resolves
    a rolling window (default: the real today)."""
    path = Path(path)
    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top-level must be a mapping")
    try:
        return _from_dict(raw, today=today)
    except ConfigError as exc:
        raise ConfigError(f"{path}: {exc}") from None


def route_from_json(text: str, *, today: date | None = None) -> RouteConfig:
    """Parse a routes.config_json value back into a RouteConfig.

    Accepts two shapes:
    - canonical (top-level "route" key) — what `to_json` writes now;
    - legacy asdict snapshot (top-level "name"/"origins") — what every
      scan wrote to the DB before 2026-07-06. Converted then validated
      through the same `_from_dict` as everything else.
    """
    try:
        raw = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"config_json is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("config_json: top-level must be a mapping")
    if "route" not in raw and "name" in raw:
        raw = _from_asdict_shape(raw)
    return _from_dict(raw, today=today)


def _from_asdict_shape(raw: dict[str, Any]) -> dict[str, Any]:
    """Convert a legacy `asdict(RouteConfig)` snapshot to canonical shape."""
    return {
        "route": {
            "name": raw.get("name"),
            "origins": raw.get("origins"),
            "destinations": raw.get("destinations"),
        },
        "search_window": raw.get("search_window") or {},
        "stay_preferences": raw.get("stay") or {},
        "currency": raw.get("currency"),
        "sweep": raw.get("sweep") or {},
        "followup": raw.get("followup") or {},
        "alerts": raw.get("alerts") or {},
    }


def _parse_rolling(rr: Any) -> RollingWindow:
    if not isinstance(rr, dict):
        raise ConfigError("search_window.rolling: must be a mapping")
    start = _require_nonneg_int(rr, "start_months",
                                path="search_window.rolling.start_months")
    span = _require_positive_int(rr, "span_months",
                                 path="search_window.rolling.span_months")
    step = (_require_positive_int(rr, "step_days",
                                  path="search_window.rolling.step_days")
            if "step_days" in rr else 7)
    not_before = (_require_date(rr, "not_before",
                                path="search_window.rolling.not_before")
                  if rr.get("not_before") not in (None, "") else None)
    return RollingWindow(start_months=start, span_months=span,
                         not_before=not_before, step_days=step)


def _from_dict(raw: dict[str, Any], *, today: date | None = None) -> RouteConfig:
    route = _require_mapping(raw, "route")
    name = _require_str(route, "name", path="route.name")
    origins = _require_iata_list(route, "origins", path="route.origins")
    destinations = _require_iata_list(route, "destinations", path="route.destinations")

    # trip_type is emitted in config_json ONLY for one_way (round-trip
    # configs stay byte-identical to pre-one-way code, so old readers and
    # the owner's mission search are untouched — see route_to_yaml_dict).
    trip_type = raw.get("trip_type", "round_trip")
    if trip_type not in ("round_trip", "one_way"):
        raise ConfigError("trip_type must be 'round_trip' or 'one_way'")

    sw_raw = _require_mapping(raw, "search_window")
    rolling = (_parse_rolling(sw_raw["rolling"])
               if sw_raw.get("rolling") is not None else None)
    if rolling is not None and trip_type == "one_way":
        raise ConfigError("search_window.rolling: round trips only")
    if rolling is not None:
        # Stored dates are only the last resolution (kept for readers
        # that do not resolve, such as the web app); today's win.
        stay_max = _require_positive_int(
            _require_mapping(raw, "stay_preferences"), "max_days",
            path="stay_preferences.max_days")
        res = rolling.resolve(today or date.today(), stay_max)
        sw_raw = {"earliest_departure": res.earliest_departure,
                  "latest_return": res.latest_return,
                  "latest_departure": res.latest_departure}
    earliest = _require_date(sw_raw, "earliest_departure", path="search_window.earliest_departure")
    latest = _require_date(sw_raw, "latest_return", path="search_window.latest_return")
    if latest <= earliest:
        raise ConfigError("search_window.latest_return must be after earliest_departure")
    latest_dep_cap = None
    if sw_raw.get("latest_departure") not in (None, ""):
        latest_dep_cap = _require_date(sw_raw, "latest_departure",
                                       path="search_window.latest_departure")
        if latest_dep_cap < earliest:
            raise ConfigError("search_window.latest_departure must be on or after earliest_departure")
        if latest_dep_cap > latest:
            raise ConfigError("search_window.latest_departure must be on or before latest_return")

    if trip_type == "one_way":
        # One-way has no stay dimension; the field is synthesized as (0,0)
        # so return_date='' / stay_days=0 sentinel rows pass the stay-range
        # filters everywhere (which use min_days>=1 for round-trips).
        min_days = max_days = 0
    else:
        stay_raw = _require_mapping(raw, "stay_preferences")
        min_days = _require_positive_int(stay_raw, "min_days", path="stay_preferences.min_days")
        max_days = _require_positive_int(stay_raw, "max_days", path="stay_preferences.max_days")
        if max_days < min_days:
            raise ConfigError("stay_preferences.max_days must be >= min_days")
    stay_step = 1
    if trip_type != "one_way" and "step_days" in (raw.get("stay_preferences") or {}):
        stay_step = _require_positive_int(raw["stay_preferences"], "step_days",
                                          path="stay_preferences.step_days")

    currency = _require_str(raw, "currency", path="currency")
    if len(currency) != 3 or not currency.isalpha() or currency != currency.upper():
        raise ConfigError("currency must be a 3-letter uppercase code (e.g. EUR)")

    # `sweep` block: window-size keys are LEGACY (geometry is derived from
    # the stay range in lib/sweep.plan_windows). They're parsed leniently
    # if present so old YAML files load, but not validated against the
    # 200-combo cap — the planner guarantees that internally.
    sweep_raw = raw.get("sweep") or {}
    if not isinstance(sweep_raw, dict):
        raise ConfigError("sweep: must be a mapping when present")
    outbound = sweep_raw.get("outbound_window_days") or 14
    ret = sweep_raw.get("return_window_days") or 14
    overlap = sweep_raw.get("overlap_days")
    overlap = overlap if isinstance(overlap, int) and not isinstance(overlap, bool) else 3
    cadence = _require_positive_int(sweep_raw, "cadence_days", path="sweep.cadence_days") \
        if "cadence_days" in sweep_raw else 14
    skip_if_min_above = _optional_positive_int(
        sweep_raw, "skip_if_min_above", path="sweep.skip_if_min_above")
    skip_grace_days = _optional_nonneg_int(
        sweep_raw, "skip_grace_days", path="sweep.skip_grace_days")

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
        search_window=SearchWindow(earliest_departure=earliest, latest_return=latest,
                                   latest_departure=latest_dep_cap,
                                   rolling=rolling),
        stay=StayPreferences(min_days=min_days, max_days=max_days,
                             step_days=stay_step),
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
        trip_type=trip_type,
    )


# --- write-back -----------------------------------------------------------


def route_to_yaml_dict(route: RouteConfig) -> dict[str, Any]:
    """Build the YAML-serialisable dict for a RouteConfig.

    Deliberately omits the LEGACY sweep window-size keys
    (outbound_window_days / return_window_days / overlap_days) — geometry
    is derived from the stay range now, so writing them back would be
    misleading. `cadence_days` and the smart-skip keys are preserved.
    """
    sweep: dict[str, Any] = {"cadence_days": route.sweep.cadence_days}
    if route.sweep.skip_if_min_above is not None:
        sweep["skip_if_min_above"] = route.sweep.skip_if_min_above
    if route.sweep.skip_grace_days is not None:
        sweep["skip_grace_days"] = route.sweep.skip_grace_days

    followup: dict[str, Any] = {}
    if route.followup.watch_below_price is not None:
        followup["watch_below_price"] = route.followup.watch_below_price
    if route.followup.drop_above_price is not None:
        followup["drop_above_price"] = route.followup.drop_above_price

    out: dict[str, Any] = {
        "route": {
            "name": route.name,
            "origins": list(route.origins),
            "destinations": list(route.destinations),
        },
        "search_window": {
            "earliest_departure": route.search_window.earliest_departure.isoformat(),
            "latest_return": route.search_window.latest_return.isoformat(),
        },
        "stay_preferences": {
            "min_days": route.stay.min_days,
            "max_days": route.stay.max_days,
        },
        "currency": route.currency,
        "sweep": sweep,
        "alerts": {
            "drop_threshold_pct": route.alerts.drop_threshold_pct,
            "baseline_window_days": route.alerts.baseline_window_days,
            "min_observations": route.alerts.min_observations,
        },
    }
    # Emit trip_type ONLY for one_way: round-trip configs serialize
    # byte-identically to pre-one-way code, so the owner's mission search
    # and any old reader (route_store self-heal, Streamlit) are untouched
    # — no config ping-pong (red-team B2).
    if route.is_one_way:
        out["trip_type"] = "one_way"
    # Emitted only when set, for the same byte-identity reason.
    if route.search_window.latest_departure is not None:
        out["search_window"]["latest_departure"] = (
            route.search_window.latest_departure.isoformat())
    # Rolling: the spec AND today's resolution. Python re-resolves on
    # every load; readers that do not (the web app) read the dates, which
    # route_store rewrites whenever a load resolves different ones.
    if route.search_window.rolling is not None:
        out["search_window"]["rolling"] = route.search_window.rolling.to_dict()
    if route.stay.step_days != 1:
        out["stay_preferences"]["step_days"] = route.stay.step_days
    if followup:
        out["followup"] = followup
    return out


def save_route(path: str | Path, route: RouteConfig) -> None:
    """Atomically write `route` back to its YAML file.

    Validates by round-tripping through `_from_dict` BEFORE touching the
    file, so a bad config can never overwrite a good one. The write is
    atomic (temp file + os.replace). NOTE: YAML comments in the original
    file are lost — this is accepted; the file is treated as data, not a
    hand-maintained document once the UI owns it.
    """
    import os

    payload = route_to_yaml_dict(route)
    # Round-trip validation: the dict we're about to write must parse back
    # into an equivalent RouteConfig.
    _from_dict(payload)  # raises ConfigError on any problem

    path = Path(path)
    text = yaml.safe_dump(payload, sort_keys=False, default_flow_style=False,
                          allow_unicode=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


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
