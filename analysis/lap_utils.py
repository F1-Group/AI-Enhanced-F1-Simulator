"""Loading and lap-splitting helpers for TORCS telemetry CSV files.

Data characteristics (see Data Handover doc):
- lap_time / lap_distance reset to 0 every time the car crosses the finish
  line, producing a sawtooth pattern in multi-lap files.
- speed_kmh is documented as 0-260 km/h, but collisions produce huge spikes
  (1000+ km/h) that must be cleaned before comparing against the baseline.
"""

import numpy as np
import pandas as pd

# Values above this are physically impossible for our car and are treated
# as sensor glitches (collisions can spike speed_kmh past 1000).
SPEED_MAX_VALID = 300.0

# A lap reset shows up as lap_distance suddenly dropping by (almost) a full
# track length. Any backwards jump bigger than this is treated as a new lap.
LAP_RESET_DROP_M = 500.0


def load_telemetry(path):
    """Read a telemetry CSV and clean out physically impossible values."""
    df = pd.read_csv(path)

    spikes = (df["speed_kmh"] < 0.0) | (df["speed_kmh"] > SPEED_MAX_VALID)
    df.loc[spikes, "speed_kmh"] = np.nan
    df["speed_kmh"] = df["speed_kmh"].interpolate(limit_direction="both")

    return df


def _lap_ids(df, reset_drop_m):
    """Number each row with its lap (0, 1, ...) by detecting the sudden drop
    of lap_distance back to 0 when the car crosses the start/finish line."""
    return (df["lap_distance"].diff() < -reset_drop_m).cumsum()


def split_laps(df, reset_drop_m=LAP_RESET_DROP_M):
    """Split a continuous telemetry log into a list of single-lap DataFrames."""
    laps = [lap.reset_index(drop=True)
            for _, lap in df.groupby(_lap_ids(df, reset_drop_m))]

    # Drop trailing fragments (e.g. a couple of rows after the final finish
    # line cross) that are too short to be a real lap.
    return [lap for lap in laps if lap["lap_distance"].max() > reset_drop_m]


def make_monotonic(lap):
    """Keep only rows where lap_distance strictly advances.

    Spins, reversing or standing still create duplicate/backwards distance
    values, which would break distance-based alignment.
    """
    running_max = lap["lap_distance"].cummax()
    keep = lap["lap_distance"] >= running_max
    keep &= ~lap["lap_distance"].duplicated(keep="first")
    return lap[keep].reset_index(drop=True)


def add_global_columns(df, reset_drop_m=LAP_RESET_DROP_M):
    """Add global_distance / global_time columns that keep growing across laps.

    Useful for plotting continuous multi-lap graphs (Team 4).
    """
    df = df.copy()
    lap_id = _lap_ids(df, reset_drop_m)

    for col, out in (("lap_distance", "global_distance"), ("lap_time", "global_time")):
        # Each lap's offset is the running total of all previous laps' finals.
        last_per_lap = df.groupby(lap_id)[col].max()
        offsets = last_per_lap.cumsum().shift(fill_value=0.0)
        df[out] = df[col] + lap_id.map(offsets)

    return df
