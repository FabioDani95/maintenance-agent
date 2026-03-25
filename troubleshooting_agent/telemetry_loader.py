"""
Telemetry data loader: reads a wide-format CSV, caches it as a DataFrame,
and exposes query functions for the orchestrator and API.

The CSV is loaded lazily on first access. If the file does not exist,
all functions return empty results (graceful degradation).
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from config import TELEMETRY_PATH, TELEMETRY_WINDOW_HOURS

_df: pd.DataFrame | None = None
_available: bool | None = None
logger = logging.getLogger(__name__)


def _load() -> pd.DataFrame | None:
    """Load and cache the telemetry CSV. Returns None if file is missing."""
    global _df, _available
    if _available is False:
        return None
    if _df is not None:
        return _df
    if not TELEMETRY_PATH.exists():
        logger.warning("Telemetry CSV not found at %s — telemetry disabled", TELEMETRY_PATH)
        _available = False
        return None
    _df = pd.read_csv(TELEMETRY_PATH)
    _df["timestamp"] = pd.to_datetime(_df["timestamp"], format="ISO8601", utc=True)
    _df.set_index("timestamp", inplace=True)
    _df.sort_index(inplace=True)
    _available = True
    logger.info("Telemetry loaded: %d rows, %d signals", len(_df), len(_df.columns))
    return _df


def get_signals(column_names: list[str], hours: int = TELEMETRY_WINDOW_HOURS) -> dict[str, list[dict[str, Any]]]:
    """
    Return time series data for the requested columns over the last N hours.
    Returns: {column_name: [{"t": iso_timestamp, "v": float}, ...]}
    Missing columns are silently skipped.
    """
    df = _load()
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


def get_stats(column_names: list[str]) -> dict[str, dict[str, Any]]:
    """
    Return basic statistics for each column over the full dataset.
    Returns: {column_name: {mean, std, min, max, last, trend}}
    trend is "rising", "falling", or "stable" based on last 10% vs first 90%.
    """
    df = _load()
    if df is None or not column_names:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for col in column_names:
        if col not in df.columns:
            continue
        s = df[col].dropna()
        if len(s) < 2:
            continue
        n = len(s)
        split = int(n * 0.9)
        early_mean = float(s.iloc[:split].mean())
        late_mean = float(s.iloc[split:].mean())
        pct_change = (late_mean - early_mean) / (abs(early_mean) + 1e-9)
        if pct_change > 0.05:
            trend = "rising"
        elif pct_change < -0.05:
            trend = "falling"
        else:
            trend = "stable"
        result[col] = {
            "mean": round(float(s.mean()), 4),
            "std": round(float(s.std()), 4),
            "min": round(float(s.min()), 4),
            "max": round(float(s.max()), 4),
            "last": round(float(s.iloc[-1]), 4),
            "trend": trend,
        }
    return result
