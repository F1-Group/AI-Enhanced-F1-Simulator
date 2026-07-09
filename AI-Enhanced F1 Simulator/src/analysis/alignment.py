"""Spatial-axis alignment between player telemetry and the expert baseline.

Core idea (see Data Handover doc): the player and the expert never cross the
same point at the same *time*, so time is useless as a comparison axis.
Instead everything is resampled onto a shared lap_distance grid, so we can
ask "at the 500 m mark, how do speed / brake / throttle compare?".
"""

import numpy as np
import pandas as pd

from .lap_utils import make_monotonic

# Telemetry channels that get aligned and compared.
CHANNELS = [
    "lap_time",
    "speed_kmh",
    "track_pos",
    "angle",
    "wheel_spin",
    "throttle",
    "brake",
    "steer",
]

GRID_STEP_M = 5.0


def distance_grid(track_length_m, step_m=GRID_STEP_M):
    """Shared lap_distance axis: one sample every `step_m` metres."""
    return np.arange(0.0, track_length_m, step_m)


def resample_lap(lap, grid):
    """Linearly interpolate one lap onto the shared distance grid."""
    lap = make_monotonic(lap)
    d = lap["lap_distance"].to_numpy()

    out = {"lap_distance": grid}
    for col in CHANNELS:
        out[col] = np.interp(grid, d, lap[col].to_numpy())
    return pd.DataFrame(out)


def align_nearest(lap, grid):
    """Nearest-neighbour alignment of a lap onto the shared distance grid.

    For every grid point (e.g. 500.0 m) this picks the telemetry row whose
    lap_distance is closest (e.g. the player sample at 500.2 m). Vectorised
    with np.searchsorted, so it stays fast even on 50 Hz logs.
    """
    lap = make_monotonic(lap)
    d = lap["lap_distance"].to_numpy()

    right = np.searchsorted(d, grid)
    right = np.clip(right, 1, len(d) - 1)
    left = right - 1

    pick_left = (grid - d[left]) <= (d[right] - grid)
    nearest = np.where(pick_left, left, right)

    out = lap.iloc[nearest][CHANNELS].reset_index(drop=True)
    out.insert(0, "lap_distance", grid)
    # How far the chosen sample really is from the grid point; big gaps mean
    # the car was off-track / reversing there and the row is unreliable.
    out["align_gap_m"] = np.abs(d[nearest] - grid)
    return out


def build_baseline(expert_laps, grid):
    """Average one or more expert laps into a single gold-standard baseline.

    Pass expert laps 2+ (flying laps) for normal use; pass [lap1] to build a
    standing-start baseline for comparing against a player's first lap.
    """
    resampled = [resample_lap(lap, grid) for lap in expert_laps]
    baseline = sum(df[CHANNELS] for df in resampled) / len(resampled)
    baseline.insert(0, "lap_distance", grid)
    return baseline


def compute_deltas(player_aligned, baseline):
    """Subtraction step: player minus expert at every grid point.

    Positive delta_speed_kmh means the player is faster at that point;
    positive delta_lap_time means the player has lost time by that point.
    """
    deltas = pd.DataFrame({"lap_distance": baseline["lap_distance"]})
    for col in CHANNELS:
        deltas[f"delta_{col}"] = player_aligned[col] - baseline[col]

    deltas["player_speed_kmh"] = player_aligned["speed_kmh"]
    deltas["expert_speed_kmh"] = baseline["speed_kmh"]
    deltas["player_brake"] = player_aligned["brake"]
    deltas["expert_brake"] = baseline["brake"]
    deltas["player_throttle"] = player_aligned["throttle"]
    deltas["expert_throttle"] = baseline["throttle"]
    deltas["align_gap_m"] = player_aligned["align_gap_m"]
    return deltas
