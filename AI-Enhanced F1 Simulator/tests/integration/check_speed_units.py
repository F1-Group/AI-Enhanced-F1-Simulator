"""Check whether the speed_kmh column matches the speed implied by the telemetry.

lap_distance is in metres and lap_time is in seconds, so d(distance)/d(time)
is the car's speed in m/s by definition. Multiplying by 3.6 gives km/h. That
derived value is compared against the logged speed_kmh column.

A ratio near 1.0 means the column is correct.
A ratio near 3.6 means the m/s -> km/h conversion in parser.py has been applied
to a value that was already in km/h.

Usage:
    python3 check_speed_units.py <telemetry.csv> [more.csv ...]
"""

import sys
import pandas as pd


def check(path):
    df = pd.read_csv(path)
    delta = df[["lap_time", "lap_distance", "speed_kmh"]].diff()

    # Keep only forward-moving frames, and ignore near-standstill where the
    # ratio is dominated by noise.
    valid = (delta.lap_time > 0) & (delta.lap_distance > 0) & (df.speed_kmh > 20)

    derived = (delta.lap_distance[valid] / delta.lap_time[valid]) * 3.6
    logged = df.speed_kmh[valid]
    ratio = (logged / derived).median()

    name = path.split("/")[-1]
    print(f"{name:<44} derived={derived.median():7.1f} km/h   "
          f"logged={logged.median():7.1f}   ratio={ratio:.2f}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    for path in sys.argv[1:]:
        check(path)
