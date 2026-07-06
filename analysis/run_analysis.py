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


def session_id_from(player_path):
    """A stable per-session id for report filenames, taken from Team 1's
    telemetry_<timestamp>.csv naming so reports from different sessions can
    never overwrite each other."""
    stem = Path(player_path).stem
    if stem.startswith("telemetry_"):
        return stem.removeprefix("telemetry_")
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def write_report(report, output):
    """Write a report atomically (temp file + rename).

    Team 4's UI polls these files, so a report must never be readable in a
    half-written state; os.replace/Path.replace is atomic on one filesystem.
    """
    output = Path(output)
    tmp = output.with_name(output.name + ".tmp")
    tmp.write_text(json.dumps(report, indent=2))
    tmp.replace(output)
    return output


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


# The 13 fields of Team 1's CSV schema, in order - exactly what Team 3's
# build_user_prompt(telemetry, question) expects.
TELEMETRY_FIELDS = ["lap_time", "lap_distance", "speed_kmh", "track_pos", "angle",
                    "wheel_spin", "gear", "rpm", "race_pos", "fuel",
                    "throttle", "brake", "steer"]


def _attach_telemetry_snapshots(report, player_lap):
    """Give each error the player's telemetry frame at the error location,
    so Team 3 can feed it straight into build_user_prompt()."""
    # Mid-write reads can leave NaN in the last row(s); a NaN snapshot would
    # crash int() or put an invalid NaN token into the JSON report.
    valid_rows = player_lap.dropna(subset=TELEMETRY_FIELDS)
    if valid_rows.empty:
        return

    apex_by_corner = {c["name"]: c["apex_m"] for c in report["corners_detected"]}
    for error in report["errors"]:
        evidence = error.get("evidence", {})
        if error["corner"] in apex_by_corner:
            anchor = apex_by_corner[error["corner"]]
        elif "sector_start_m" in evidence:
            anchor = (evidence["sector_start_m"] + evidence["sector_end_m"]) / 2
        else:
            continue
        row = valid_rows.loc[(valid_rows["lap_distance"] - anchor).abs().idxmin()]
        error["telemetry"] = {
            field: (int(row[field]) if field in ("gear", "race_pos")
                    else round(float(row[field]), 4))
            for field in TELEMETRY_FIELDS
        }


STANDING_START_KMH = 30.0


def _is_standing_start(lap):
    """A lap that begins from (near) standstill. Measured from the data, not
    from the lap's position in the file - a recording can start mid-session.

    Uses the median of the first ~0.5 s (25 frames at 50 Hz) so that a single
    glitched or interpolated frame can never flip the baseline choice.
    """
    return float(lap["speed_kmh"].head(25).median()) < STANDING_START_KMH


def analyse_lap(player_lap, expert_laps, lap_number=1, track="olethros_road_1"):
    """Full slow-layer pass for one player lap. Returns the error report dict."""
    track_length = max(lap["lap_distance"].max() for lap in expert_laps)
    grid = distance_grid(track_length)

    # A standing-start lap is only fairly compared against the expert's own
    # standing-start lap (lap 1). Flying laps get the averaged lap 2+
    # baseline recommended in the Data Handover doc.
    standing = _is_standing_start(player_lap)
    if standing or len(expert_laps) == 1:
        baseline = build_baseline(expert_laps[:1], grid)
    else:
        baseline = build_baseline(expert_laps[1:], grid)

    player_aligned = align_nearest(player_lap, grid)
    deltas = compute_deltas(player_aligned, baseline)
    corners = detect_corners(baseline)
    errors = detect_errors(deltas, corners, track_length)

    report = {
        "source": "team2_slow_layer",
        "template_id": "team2_error_template_v3",
        "track": track,
        "lap_number": lap_number,
        "baseline_type": "standing_start" if standing or len(expert_laps) == 1 else "flying",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "player_lap_time_s": round(float(player_lap["lap_time"].max()), 2),
        "expert_lap_time_s": round(float(baseline["lap_time"].iloc[-1]), 2),
        "corners_detected": corners,
        "errors": errors,
    }
    _attach_telemetry_snapshots(report, player_lap)
    return report, deltas


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
        elif args.output:
            output = args.output
        else:
            session = session_id_from(player_path)
            output = PROJECT_ROOT / "data" / f"error_report_{session}_lap{lap_number}.json"
        write_report(report, output)
        print(f"Report written to {output}")


if __name__ == "__main__":
    main()
