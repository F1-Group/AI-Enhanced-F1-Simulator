"""Slow-layer entry point: compare a player lap against the expert baseline.

Usage:
    python -m analysis.run_analysis                          # latest player file
    python -m analysis.run_analysis data/player_data/telemetry_20260630_150736.csv
    python -m analysis.run_analysis <player.csv> -o data/error_report.json

The resulting JSON matches mock/error_template.json, so it can be fed
directly to Team 3 (Granite prompts) and AudioManager.play_error().
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from .alignment import align_nearest, build_baseline, compute_deltas, distance_grid
from .error_detection import detect_corners, detect_errors
from .lap_utils import load_telemetry, split_laps

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EXPERT = PROJECT_ROOT / "data/expert_data/expert_olethros_road_1_3laps.csv"
LATEST_POINTER = PROJECT_ROOT / "data/latest_data.txt"


def latest_player_file():
    """Resolve the newest player CSV, preferring Team 1's latest_data.txt pointer."""
    if LATEST_POINTER.exists():
        path = Path(LATEST_POINTER.read_text().strip())
        if path.exists():
            return path
        print(f"Warning: {LATEST_POINTER.name} points to a missing file ({path}); "
              f"falling back to the newest file in data/player_data/.")
    candidates = sorted((PROJECT_ROOT / "data/player_data").glob("*.csv"))
    if not candidates:
        raise FileNotFoundError("No player telemetry CSV found in data/player_data/")
    return candidates[-1]


def analyse_lap(player_lap, expert_laps, lap_number=1, track="olethros_road_1"):
    """Full slow-layer pass for one player lap. Returns the error report dict."""
    track_length = max(lap["lap_distance"].max() for lap in expert_laps)
    grid = distance_grid(track_length)

    # A player's lap 1 is a standing start, so it is only fair to compare it
    # against the expert's standing-start lap. Later laps are flying laps and
    # get the averaged lap 2+ baseline recommended in the Data Handover doc.
    if lap_number == 1 or len(expert_laps) == 1:
        baseline = build_baseline(expert_laps[:1], grid)
    else:
        baseline = build_baseline(expert_laps[1:], grid)

    player_aligned = align_nearest(player_lap, grid)
    deltas = compute_deltas(player_aligned, baseline)
    corners = detect_corners(baseline)
    errors = detect_errors(deltas, corners, track_length)

    return {
        "source": "team2_slow_layer",
        "template_id": "team2_error_template_v3",
        "track": track,
        "lap_number": lap_number,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "player_lap_time_s": round(float(player_lap["lap_time"].max()), 2),
        "expert_lap_time_s": round(float(baseline["lap_time"].iloc[-1]), 2),
        "corners_detected": corners,
        "errors": errors,
    }, deltas


def main():
    parser = argparse.ArgumentParser(description="Compare player telemetry against the expert baseline.")
    parser.add_argument("player_csv", nargs="?", help="Player telemetry CSV (default: latest recording)")
    parser.add_argument("--expert", default=str(DEFAULT_EXPERT), help="Expert baseline CSV")
    parser.add_argument("-o", "--output", help="Where to write the error report JSON")
    args = parser.parse_args()

    player_path = Path(args.player_csv) if args.player_csv else latest_player_file()
    expert_laps = split_laps(load_telemetry(args.expert))

    # Team 1's logger creates the CSV (and the latest_data.txt pointer) the
    # moment a recording starts, so the file may still be empty or mid-lap.
    try:
        player_laps = split_laps(load_telemetry(player_path))
    except pd.errors.EmptyDataError:
        raise SystemExit(f"{player_path.name} is empty - the recording is probably "
                         f"still in progress. Try again after completing a lap.")

    # An aborted recording leaves a trailing fragment that never reached the
    # finish line; comparing it against a full expert lap would be nonsense.
    track_length = max(lap["lap_distance"].max() for lap in expert_laps)
    complete_laps = []
    for lap in player_laps:
        if lap["lap_distance"].max() >= 0.95 * track_length:
            complete_laps.append(lap)
        else:
            print(f"Skipping incomplete lap fragment "
                  f"({lap['lap_distance'].max():.0f} m of {track_length:.0f} m).")
    player_laps = complete_laps
    if not player_laps:
        raise SystemExit(f"No completed lap found in {player_path.name} - the recording "
                         f"is probably still in progress. Try again after completing a lap.")

    print(f"Player:   {player_path.name} ({len(player_laps)} lap(s))")
    print(f"Baseline: {Path(args.expert).name} ({len(expert_laps)} lap(s))")

    for lap_number, player_lap in enumerate(player_laps, start=1):
        report, _ = analyse_lap(player_lap, expert_laps, lap_number=lap_number)

        print(f"\n=== Lap {lap_number}: {report['player_lap_time_s']}s "
              f"vs expert {report['expert_lap_time_s']}s | "
              f"{len(report['errors'])} error(s) ===")
        for error in report["errors"]:
            print(f"  [{error['severity']:>6}] {error['tag']:<28} {error['message']}")

        if args.output and len(player_laps) > 1:
            # One -o path but several laps: suffix the lap number so earlier
            # laps' reports are not overwritten.
            base = Path(args.output)
            output = base.with_stem(f"{base.stem}_lap{lap_number}")
        else:
            output = args.output or (PROJECT_ROOT / "data" / f"error_report_lap{lap_number}.json")
        Path(output).write_text(json.dumps(report, indent=2))
        print(f"Report written to {output}")


if __name__ == "__main__":
    main()
