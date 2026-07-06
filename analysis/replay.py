"""Replay a recorded telemetry CSV as if TORCS were running live.

Stands in for Team 1's pipeline during integration and stress tests: it
creates a fresh telemetry_<timestamp>.csv, updates data/latest_data.txt
immediately (exactly like Team 1's CSVLogger), then streams the source
file's rows at the recorded 50 Hz rate (or faster with --speed).

Usage:
    .venv/bin/python -m analysis.replay data/player_data/telemetry_20260630_150736.csv --speed 20
"""

import argparse
import time
from datetime import datetime
from pathlib import Path

from .run_analysis import LATEST_POINTER, PROJECT_ROOT

FRAME_INTERVAL_S = 0.02  # 50 Hz, same as TORCS


def main():
    parser = argparse.ArgumentParser(description="Simulate a live TORCS session.")
    parser.add_argument("source", help="Recorded telemetry CSV to replay")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="Playback speed multiplier (default: real time)")
    parser.add_argument("--out-dir", default=str(PROJECT_ROOT / "data" / "player_data"))
    args = parser.parse_args()

    lines = Path(args.source).read_text(encoding="utf-8").splitlines()
    header, rows = lines[0], lines[1:]

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"telemetry_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(header + "\n")
        f.flush()
        LATEST_POINTER.write_text(str(out_path.resolve()))
        print(f"Replaying {len(rows)} frames of {Path(args.source).name} "
              f"at {args.speed:g}x -> {out_path.name}")

        interval = FRAME_INTERVAL_S / args.speed
        started = time.time()
        for i, row in enumerate(rows):
            f.write(row + "\n")
            f.flush()
            # Pace against the wall clock so slow disk writes don't drift.
            target = started + (i + 1) * interval
            delay = target - time.time()
            if delay > 0:
                time.sleep(delay)

    print(f"Replay finished in {time.time() - started:.1f}s.")


if __name__ == "__main__":
    main()
