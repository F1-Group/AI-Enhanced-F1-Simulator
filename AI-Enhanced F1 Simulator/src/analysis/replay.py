"""Replay a recorded telemetry CSV as if TORCS were running live.

Rebuilt for the latency test SOP: lets us feed a FIXED recording into
live_coach.py at real-time (or accelerated) pace, without needing a real
TORCS session or a real driver. Creates a fresh telemetry_<timestamp>.csv,
updates data/latest_data.txt immediately (matching Team 1's CSVLogger
behaviour), then streams the source file's rows at the recorded 50 Hz rate
(or faster with --speed).

Usage:
    python -m analysis.replay data/test_fixtures/fixture_normal_lap.csv
    python -m analysis.replay data/test_fixtures/fixture_normal_lap.csv --speed 5

For the latency test SOP, use --speed 1 (real time) unless you are
specifically testing queue backpressure under a faster-than-real feed.
"""

import argparse
import time
from datetime import datetime
from pathlib import Path

# Adjust this import if run_analysis.py's location differs in the current
# repo layout; PROJECT_ROOT / LATEST_POINTER just need to point at the same
# paths Team 1's CSVLogger and live_coach.py's wait_for_recording() use.
try:
    from .run_analysis import LATEST_POINTER, PROJECT_ROOT
except ImportError:
    # Fallback if run_analysis.py's constants moved or this is run standalone
    PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
    LATEST_POINTER = PROJECT_ROOT / "data" / "latest_data.txt"

FRAME_INTERVAL_S = 0.02  # 50 Hz, same as TORCS


def main():
    parser = argparse.ArgumentParser(description="Simulate a live TORCS session by replaying a recorded CSV.")
    parser.add_argument("source", help="Recorded telemetry CSV to replay")
    parser.add_argument("--speed", type=float, default=1.0,
                        help="Playback speed multiplier (default: real time, 1.0)")
    parser.add_argument("--out-dir", default=str(PROJECT_ROOT / "data" / "player_data"),
                        help="Where to write the replayed CSV (default: data/player_data/)")
    args = parser.parse_args()

    source_path = Path(args.source)
    if not source_path.exists():
        raise SystemExit(f"Source file not found: {source_path}")

    lines = source_path.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise SystemExit(f"Source file is empty: {source_path}")
    header, rows = lines[0], lines[1:]

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"telemetry_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(header + "\n")
        f.flush()
        # Point latest_data.txt at this new file immediately, exactly like
        # Team 1's CSVLogger.create_file() does, so live_coach.py's
        # wait_for_recording()/_read_complete_line() pick it up right away.
        LATEST_POINTER.parent.mkdir(parents=True, exist_ok=True)
        LATEST_POINTER.write_text(str(out_path.resolve()))

        print(f"Replaying {len(rows)} frames of {source_path.name} "
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

    elapsed = time.time() - started
    print(f"Replay finished in {elapsed:.1f}s ({len(rows)} frames, "
          f"{len(rows) / elapsed:.1f} frames/sec actual rate).")


if __name__ == "__main__":
    main()