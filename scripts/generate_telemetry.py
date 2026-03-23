"""
Generate simulated telemetry data for the Bambu Lab P1P troubleshooting agent.

Produces a wide-format CSV at TroubleShootingAgent/telemetry/telemetry_p1p.csv
with 14 days of data at 5-minute intervals (~4032 rows, 22 columns).

Usage:
    python scripts/generate_telemetry.py
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "TroubleShootingAgent" / "telemetry" / "telemetry_p1p.csv"

# ── Signal definitions ──
# Each signal: baseline value, noise std, unit (for documentation), optional anomaly/trend config
SIGNALS: dict[str, dict] = {
    # Motion / vibration
    "x_axis_vibration_rms":      {"baseline": 0.12, "noise": 0.015, "unit": "g"},
    "x_axis_motor_current_a":    {"baseline": 1.45, "noise": 0.04,  "unit": "A"},
    "y_axis_vibration_rms":      {"baseline": 0.10, "noise": 0.012, "unit": "g"},
    "z_axis_vibration_rms":      {"baseline": 0.08, "noise": 0.010, "unit": "g"},
    "z_axis_motor_current_a":    {"baseline": 0.95, "noise": 0.03,  "unit": "A"},
    "toolhead_speed_deviation_pct": {"baseline": 0.5, "noise": 0.3, "unit": "%"},

    # Belt / idler / bearing
    "belt_tension_freq_hz":      {"baseline": 48.0, "noise": 0.8,   "unit": "Hz"},
    "idler_noise_db":            {"baseline": 32.0, "noise": 1.5,   "unit": "dB"},
    "bearing_noise_db":          {"baseline": 28.0, "noise": 1.2,   "unit": "dB"},

    # Environment
    "ambient_humidity_pct":      {"baseline": 45.0, "noise": 3.0,   "unit": "%"},

    # Camera
    "camera_clarity_score":      {"baseline": 0.95, "noise": 0.02,  "unit": "score"},

    # Fans
    "hotend_fan_rpm":            {"baseline": 4800, "noise": 80,    "unit": "rpm"},
    "front_fan_rpm":             {"baseline": 3200, "noise": 60,    "unit": "rpm"},
    "aux_fan_rpm":               {"baseline": 5000, "noise": 90,    "unit": "rpm"},

    # Hotend / nozzle
    "nozzle_temp_c":             {"baseline": 210.0, "noise": 1.5,  "unit": "C"},
    "first_layer_z_offset_mm":   {"baseline": -0.10, "noise": 0.02, "unit": "mm"},
    "extrusion_pressure_pa":     {"baseline": 1200,  "noise": 40,   "unit": "Pa"},
    "heater_block_temp_delta_c": {"baseline": 2.0,   "noise": 0.4,  "unit": "C"},

    # Extruder / filament
    "extruder_motor_current_a":  {"baseline": 0.85, "noise": 0.03,  "unit": "A"},
    "filament_feed_rate_mm_s":   {"baseline": 4.5,  "noise": 0.15,  "unit": "mm/s"},
    "cutter_cycle_count":        {"baseline": 0,    "noise": 0,     "unit": "count"},
}

# ── Anomaly / trend injection definitions ──
# Each entry: signal_name, type (spike/trend/drop), day_start, day_end, magnitude
ANOMALIES = [
    # X-axis vibration spike days 10-12 (simulates grease depletion)
    ("x_axis_vibration_rms",      "spike",  10, 12, 0.08),
    ("x_axis_motor_current_a",    "spike",  10, 12, 0.25),
    # Bearing noise gradual rise over days 7-14 (lubrication wear)
    ("bearing_noise_db",          "trend",   7, 14, 8.0),
    # Idler noise spike days 11-13
    ("idler_noise_db",            "spike",  11, 13, 12.0),
    # Belt tension drop days 9-14 (contamination / slip)
    ("belt_tension_freq_hz",      "trend",   9, 14, -6.0),
    ("toolhead_speed_deviation_pct", "trend", 9, 14, 3.5),
    # Camera clarity degradation days 8-14
    ("camera_clarity_score",      "trend",   8, 14, -0.25),
    # Fan RPM drop days 12-14 (dust buildup)
    ("hotend_fan_rpm",            "trend",  12, 14, -600),
    ("front_fan_rpm",             "trend",  12, 14, -400),
    ("aux_fan_rpm",               "trend",  12, 14, -500),
    # Nozzle temp instability days 11-13
    ("nozzle_temp_c",             "spike",  11, 13, 8.0),
    # Extrusion pressure rise days 10-14 (residue buildup)
    ("extrusion_pressure_pa",     "trend",  10, 14, 350),
    # Extruder motor current rise days 9-14 (filament dust / PTFE wear)
    ("extruder_motor_current_a",  "trend",   9, 14, 0.20),
    # Filament feed rate drop days 10-14
    ("filament_feed_rate_mm_s",   "trend",  10, 14, -0.8),
    # Heater block delta rise days 11-14 (silicone sock degradation)
    ("heater_block_temp_delta_c", "trend",  11, 14, 4.0),
    # Humidity spike days 6-8 (simulates environment change → rust risk)
    ("ambient_humidity_pct",      "spike",   6,  8, 20.0),
    # Z-axis vibration slight rise days 8-14
    ("z_axis_vibration_rms",      "trend",   8, 14, 0.04),
    ("z_axis_motor_current_a",    "trend",   8, 14, 0.12),
]


def generate(days: int = 14, interval_minutes: int = 5, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)

    # Timestamp index
    n_points = days * 24 * 60 // interval_minutes
    end = pd.Timestamp("2026-03-18T00:00:00Z")
    start = end - pd.Timedelta(days=days)
    timestamps = pd.date_range(start, end, periods=n_points, tz="UTC")

    data: dict[str, np.ndarray] = {}

    for col, spec in SIGNALS.items():
        baseline = spec["baseline"]
        noise = spec["noise"]

        if col == "cutter_cycle_count":
            # Monotonically increasing counter: ~3 cuts/hour with noise
            cuts_per_interval = 3.0 * interval_minutes / 60.0
            increments = rng.poisson(cuts_per_interval, size=n_points)
            data[col] = np.cumsum(increments).astype(float)
        else:
            values = baseline + rng.normal(0, noise, size=n_points)
            data[col] = values

    # Inject anomalies
    total_minutes = days * 24 * 60
    for col, atype, day_start, day_end, magnitude in ANOMALIES:
        if col not in data:
            continue

        idx_start = int((day_start / days) * n_points)
        idx_end = int((day_end / days) * n_points)
        idx_start = max(0, min(idx_start, n_points - 1))
        idx_end = max(idx_start + 1, min(idx_end, n_points))
        window = idx_end - idx_start

        if atype == "spike":
            # Elevated baseline with extra noise in the window
            spike_noise = rng.normal(magnitude, abs(magnitude) * 0.2, size=window)
            data[col][idx_start:idx_end] += spike_noise
        elif atype == "trend":
            # Linear ramp from 0 to magnitude over the window
            ramp = np.linspace(0, magnitude, window)
            data[col][idx_start:idx_end] += ramp
        elif atype == "drop":
            # Sudden drop at day_start, stays low
            data[col][idx_start:idx_end] += magnitude  # magnitude is negative

    # Clip non-negative signals
    non_negative = [
        "x_axis_vibration_rms", "y_axis_vibration_rms", "z_axis_vibration_rms",
        "x_axis_motor_current_a", "z_axis_motor_current_a", "extruder_motor_current_a",
        "bearing_noise_db", "idler_noise_db",
        "hotend_fan_rpm", "front_fan_rpm", "aux_fan_rpm",
        "nozzle_temp_c", "extrusion_pressure_pa",
        "camera_clarity_score", "ambient_humidity_pct",
        "filament_feed_rate_mm_s", "cutter_cycle_count",
        "toolhead_speed_deviation_pct", "heater_block_temp_delta_c",
    ]
    for col in non_negative:
        if col in data:
            data[col] = np.clip(data[col], 0, None)

    # Clip camera_clarity_score to [0, 1]
    data["camera_clarity_score"] = np.clip(data["camera_clarity_score"], 0.0, 1.0)

    # Round values
    for col in data:
        data[col] = np.round(data[col], 4)

    df = pd.DataFrame(data, index=timestamps)
    df.index.name = "timestamp"
    return df


def main() -> None:
    print("Generating simulated telemetry data...")
    df = generate()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_PATH)
    print(f"Saved {len(df)} rows x {len(df.columns)} signals to {OUTPUT_PATH}")
    print(f"Columns: {', '.join(df.columns)}")
    print("Done.")


if __name__ == "__main__":
    main()
