from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from kg_agents.config import DEFAULT_TELEMETRY_PATH, TELEMETRY_WINDOW_HOURS

_CACHE: dict[str, pd.DataFrame] = {}
_UNAVAILABLE: set[str] = set()
logger = logging.getLogger(__name__)


def _resolve_telemetry_path(
    telemetry_path: Path | None = None,
    telemetry_dir: Path | None = None,
) -> Path | None:
    if telemetry_path is not None:
        return telemetry_path

    if telemetry_dir is not None:
        if not telemetry_dir.exists():
            return None
        csv_files = sorted(telemetry_dir.glob("*.csv"))
        if csv_files:
            return csv_files[0]
        return None

    return DEFAULT_TELEMETRY_PATH


def _load(
    telemetry_path: Path | None = None,
    telemetry_dir: Path | None = None,
) -> pd.DataFrame | None:
    resolved_path = _resolve_telemetry_path(telemetry_path=telemetry_path, telemetry_dir=telemetry_dir)
    if resolved_path is None:
        return None

    key = str(resolved_path.resolve())
    if key in _UNAVAILABLE:
        return None
    if key in _CACHE:
        return _CACHE[key]
    if not resolved_path.exists():
        logger.warning("Telemetry CSV not found at %s — telemetry disabled", resolved_path)
        _UNAVAILABLE.add(key)
        return None

    df = pd.read_csv(resolved_path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="ISO8601", utc=True)
    df.set_index("timestamp", inplace=True)
    df.sort_index(inplace=True)
    _CACHE[key] = df
    logger.info("Telemetry loaded from %s: %d rows, %d signals", resolved_path, len(df), len(df.columns))
    return df


def get_signals(
    column_names: list[str],
    hours: int = TELEMETRY_WINDOW_HOURS,
    telemetry_path: Path | None = None,
    telemetry_dir: Path | None = None,
) -> dict[str, list[dict[str, Any]]]:
    df = _load(telemetry_path=telemetry_path, telemetry_dir=telemetry_dir)
    if df is None or not column_names:
        return {}
    cutoff = df.index.max() - pd.Timedelta(hours=hours)
    window = df.loc[cutoff:]
    result: dict[str, list[dict[str, Any]]] = {}
    for col in column_names:
        if col not in df.columns:
            logger.warning("Telemetry column '%s' not found in CSV", col)
            continue
        series = window[col].dropna()
        result[col] = [
            {"t": ts.isoformat(), "v": round(float(val), 4)}
            for ts, val in series.items()
        ]
    return result


def get_stats(
    column_names: list[str],
    telemetry_path: Path | None = None,
    telemetry_dir: Path | None = None,
) -> dict[str, dict[str, Any]]:
    df = _load(telemetry_path=telemetry_path, telemetry_dir=telemetry_dir)
    if df is None or not column_names:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for col in column_names:
        if col not in df.columns:
            continue
        series = df[col].dropna()
        if len(series) < 2:
            continue
        n = len(series)
        split = int(n * 0.9)
        early_mean = float(series.iloc[:split].mean())
        late_mean = float(series.iloc[split:].mean())
        pct_change = (late_mean - early_mean) / (abs(early_mean) + 1e-9)
        if pct_change > 0.05:
            trend = "rising"
        elif pct_change < -0.05:
            trend = "falling"
        else:
            trend = "stable"
        result[col] = {
            "mean": round(float(series.mean()), 4),
            "std": round(float(series.std()), 4),
            "min": round(float(series.min()), 4),
            "max": round(float(series.max()), 4),
            "last": round(float(series.iloc[-1]), 4),
            "trend": trend,
        }
    return result
