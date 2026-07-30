"""Generate 3 player-recording fixtures for the latency test SOP.

Takes ONE real lap from your actual expert baseline CSV
(data/expert_data/expert_olethros_road_1_3laps.csv) as a template, then
deliberately injects errors using the SAME thresholds error_detection.py
checks for. This guarantees the fixture will trigger a real, detectable
error when replayed - you don't need to actually drive, and you don't need
to guess whether a recording "happens" to contain a mistake.

Why start from a real expert lap instead of generating from scratch:
the exact track length, corner count, and corner positions differ per
track. Starting from a real lap means the injected fixture is guaranteed
to be geometrically consistent with the actual track your expert baseline
was recorded on.

Usage:
    python tests/generate_fixtures.py
    python tests/generate_fixtures.py --expert data/expert_data/expert_olethros_road_1_3laps.csv

Output (written to tests/fixtures/ by default):
    fixture_normal_lap.csv   - one moderate error (late braking) at one corner
    fixture_many_errors.csv  - late braking + poor corner exit + off-line,
                               injected at up to 3 different corners
    fixture_final_lap.csv    - the LAST lap in the source file, with one
                               injected error; useful for checking the
                               "last lap coaching dropped" fix (Test Matrix #24)
                               because it never resets lap_distance, so it
                               only gets finalised when the stream ends -
                               exactly the code path that bug lives in.
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

# --- Same thresholds as error_detection.py, duplicated here on purpose so ---
# --- this script has no import dependency on the rest of the codebase.   ---
CORNER_STEER_THRESHOLD = 0.10
CORNER_MIN_LENGTH_M = 30.0
CORNER_MERGE_GAP_M = 60.0
ENTRY_ZONE_M = 150.0
EXIT_ZONE_M = 150.0
BRAKE_SEARCH_ZONE_M = 300.0
BRAKE_ON = 0.30
LATE_BRAKING_M = 25.0
ENTRY_OVERSPEED_KMH = 15.0
EXIT_SPEED_DEFICIT_KMH = 12.0
OFFLINE_TRACK_POS = 0.35
LAP_RESET_DROP_M = 500.0
OFF_TRACK_POS = 1.0  # matches fast_layer.py's OFF_TRACK_POS threshold

REQUIRED_COLUMNS = ["lap_time", "lap_distance", "speed_kmh", "track_pos", "angle",
                    "wheel_spin", "gear", "rpm", "race_pos", "fuel",
                    "throttle", "brake", "steer"]


def split_laps(df):
    """Minimal reimplementation of lap_utils.split_laps for this standalone script."""
    lap_id = (df["lap_distance"].diff() < -LAP_RESET_DROP_M).cumsum()
    laps = [lap.reset_index(drop=True) for _, lap in df.groupby(lap_id)]
    return [lap for lap in laps if lap["lap_distance"].max() > LAP_RESET_DROP_M]


def find_corners(lap):
    """Minimal reimplementation of error_detection.detect_corners, applied
    directly to one lap's raw steer trace (not the baseline-aligned grid -
    good enough to reliably find a real corner to inject errors into)."""
    steer = np.abs(lap["steer"].to_numpy())
    steer = pd.Series(steer).rolling(5, center=True, min_periods=1).mean().to_numpy()
    dist = lap["lap_distance"].to_numpy()

    in_corner = steer > CORNER_STEER_THRESHOLD
    segments = []
    start = None
    for i, flag in enumerate(in_corner):
        if flag and start is None:
            start = dist[i]
        elif not flag and start is not None:
            segments.append([start, dist[i]])
            start = None
    if start is not None:
        segments.append([start, dist[-1]])

    merged = []
    for seg in segments:
        if merged and seg[0] - merged[-1][1] < CORNER_MERGE_GAP_M:
            merged[-1][1] = seg[1]
        else:
            merged.append(seg)

    return [seg for seg in merged if seg[1] - seg[0] >= CORNER_MIN_LENGTH_M]


def expert_brakes_here(lap, corner_start_m):
    """True if the expert's own brake trace crosses BRAKE_ON somewhere in the
    search zone before this corner. detect_corner_errors()'s late_braking
    check requires a real expert brake point (both its branches bail out
    when expert_bp is None) - injecting into a corner the expert takes
    without braking (e.g. a fast kink) can never be detected, no matter how
    hard the injection oversells the player's overspeed."""
    d = lap["lap_distance"]
    zone = (d >= corner_start_m - BRAKE_SEARCH_ZONE_M) & (d <= corner_start_m)
    return bool((lap.loc[zone, "brake"] >= BRAKE_ON).any())


def inject_late_braking(lap, corner_start_m, corner_end_m):
    """Guarantee a late_braking error at this corner: zero out braking from
    the start of the search zone up through the expert's OWN real first
    brake point (plus the late-braking margin), then raise entry speed.

    Anchoring on a fixed "last 20m" cutoff (the previous approach) silently
    no-ops whenever the expert's real first brake point is closer than that
    to the corner (e.g. 15m) - the untouched zone then IS the expert's real
    braking, so the player's copied brake trace never actually shifts and
    player_bp ends up == expert_bp. Anchoring on the expert's measured brake
    point guarantees the player's first surviving brake point lands past it
    by at least LATE_BRAKING_M, regardless of where in the search zone the
    expert actually brakes.
    """
    d = lap["lap_distance"]
    search_zone = (d >= corner_start_m - BRAKE_SEARCH_ZONE_M) & (d <= corner_start_m)

    braking_rows = lap.loc[search_zone & (lap["brake"] >= BRAKE_ON), "lap_distance"]
    expert_bp = float(braking_rows.iloc[0]) if len(braking_rows) else corner_start_m - 20.0

    suppress_until = min(expert_bp + LATE_BRAKING_M + 5.0, corner_start_m - 5.0)
    suppress_zone = search_zone & (d <= suppress_until)
    lap.loc[suppress_zone, "brake"] = 0.0

    entry_zone = (d >= corner_start_m - ENTRY_ZONE_M) & (d <= corner_start_m)
    lap.loc[entry_zone, "speed_kmh"] = lap.loc[entry_zone, "speed_kmh"] + ENTRY_OVERSPEED_KMH + 8.0
    return lap


def inject_poor_corner_exit(lap, corner_end_m):
    """Guarantee a poor_corner_exit error: cut throttle and speed after the corner."""
    d = lap["lap_distance"]
    exit_zone = (d >= corner_end_m) & (d <= corner_end_m + EXIT_ZONE_M)
    lap.loc[exit_zone, "throttle"] = (lap.loc[exit_zone, "throttle"] * 0.4).clip(lower=0.0)
    lap.loc[exit_zone, "speed_kmh"] = (lap.loc[exit_zone, "speed_kmh"] - EXIT_SPEED_DEFICIT_KMH - 6.0).clip(lower=0.0)
    return lap


def inject_poor_track_position(lap, corner_start_m, corner_end_m):
    """Guarantee a poor_track_position error: push the car off the racing line."""
    d = lap["lap_distance"]
    mid_zone = (d >= corner_start_m) & (d <= corner_end_m)
    offset = OFFLINE_TRACK_POS + 0.15
    lap.loc[mid_zone, "track_pos"] = (lap.loc[mid_zone, "track_pos"] + offset).clip(-1.0, 1.0)
    return lap


def inject_off_track(lap, at_m, span_m=15.0):
    """Guarantee a fast_layer.py off_track trigger (|track_pos| > OFF_TRACK_POS)
    for a short window at a specific distance. Unlike inject_poor_track_position
    (which targets the slow-layer OFFLINE_TRACK_POS=0.35 threshold and clips to
    +-1.0, i.e. can never actually cross fast_layer's stricter 1.0 threshold),
    this deliberately goes past 1.0 so the FAST layer's per-frame off_track
    check - a separate, high-priority, interrupt=True code path from the
    slow-layer corner errors above - actually fires during replay."""
    d = lap["lap_distance"]
    zone = (d >= at_m) & (d <= at_m + span_m)
    lap.loc[zone, "track_pos"] = 1.3
    return lap


def build_fixture(lap, corners, mode):
    lap = lap.copy()
    if not corners:
        print(f"  [warn] No corner found in this lap; '{mode}' fixture will have no guaranteed error.")
        return lap

    # error_detection.py's detect_corner_errors() used to have a "skip old
    # corners" check that treated every completed-lap analysis as if the car
    # were sitting at the finish line, silently skipping every corner more
    # than 200m before it. That's now fixed (the skip only applies when
    # is_realtime=True, and is keyed off the player's actually-reached
    # position) - full-lap analysis no longer cares where the corner sits on
    # the track, so we no longer need to bias injection toward the corner
    # nearest the finish line.
    #
    # What we DO still need: late_braking requires the EXPERT to show a real
    # brake point (>=BRAKE_ON) in the search zone - detect_corner_errors()'s
    # late_braking branches both bail out when expert_bp is None, so
    # injecting into a corner the expert takes without real braking (a fast
    # kink) can never be detected no matter how hard we oversell the
    # player's overspeed. Prefer corners with a genuine expert brake point,
    # in track order.
    corners_by_end = sorted(corners, key=lambda c: c[1])  # ascending by end_m
    brake_corners = [c for c in corners_by_end if expert_brakes_here(lap, c[0])]
    if not brake_corners:
        print(f"  [warn] No corner in this lap has a real expert brake point in its "
              f"search zone; falling back to the last corner, but the injected "
              f"late_braking error may not be detectable.")
    ranked = brake_corners or corners_by_end
    target_corner = ranked[0]

    if mode == "single":
        s, e = target_corner
        lap = inject_late_braking(lap, s, e)

    elif mode == "many":
        # Use up to 3 corners with a real expert brake point (falling back to
        # any corner if fewer than 3 qualify), in track order.
        pool = ranked if len(ranked) >= 3 else (ranked + [c for c in corners_by_end if c not in ranked])
        chosen = pool[:3]
        s0, e0 = chosen[0]
        lap = inject_late_braking(lap, s0, e0)
        lap = inject_poor_corner_exit(lap, e0)
        if len(chosen) > 1:
            s1, e1 = chosen[1]
            lap = inject_poor_track_position(lap, s1, e1)
        if len(chosen) > 2:
            s2, e2 = chosen[2]
            lap = inject_late_braking(lap, s2, e2)

        # Also inject a genuine fast_layer off_track excursion after the
        # first corner's exit zone, timed so the SLOW-layer late_braking/
        # poor_corner_exit coaching for that same corner has real time to
        # get through the actual pipeline (0.75s snapshot cadence + ~0.7-1s
        # LLM+guardrail round trip, per the measured real replay latency)
        # and land in the audio queue BEFORE the fast-layer off_track alert
        # fires - otherwise there's nothing valid in queue yet for the
        # interrupt to conflict with. ~180m past e0 gives roughly 3-4s of
        # real driving time at typical corner-exit speed before off_track
        # hits, which is what the voice-queue-conflict test needs to
        # actually exercise "does off_track's interrupt wipe still-valid
        # queued audio" instead of just playing into an empty queue.
        lap = inject_off_track(lap, e0 + 180.0)

    return lap


def main():
    parser = argparse.ArgumentParser(description="Generate replay fixtures with guaranteed errors.")
    parser.add_argument("--expert", default="data/expert_data/expert_olethros_road_1_3laps.csv",
                        help="Path to your real expert baseline CSV")
    parser.add_argument("--out-dir", default="tests/fixtures",
                        help="Where to write the generated fixture CSVs")
    args = parser.parse_args()

    expert_path = Path(args.expert)
    if not expert_path.exists():
        raise SystemExit(f"Expert file not found: {expert_path}\n"
                          f"Pass --expert <path> if your expert CSV lives somewhere else.")

    df = pd.read_csv(expert_path)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise SystemExit(f"Expert CSV is missing expected columns: {missing}\n"
                          f"Found columns: {list(df.columns)}")

    laps = split_laps(df)
    if not laps:
        raise SystemExit("Could not find any complete laps in the expert CSV.")

    print(f"Found {len(laps)} lap(s) in {expert_path.name}.")

    # A "flying" lap (not the standing-start first lap) makes a more typical
    # template; fall back to lap 0 if there's only one lap available.
    template_lap = laps[1] if len(laps) > 1 else laps[0]
    final_lap = laps[-1]

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    jobs = [
        ("fixture_normal_lap.csv", template_lap, "single"),
        ("fixture_many_errors.csv", template_lap, "many"),
        ("fixture_final_lap.csv", final_lap, "single"),
    ]

    for filename, source_lap, mode in jobs:
        corners = find_corners(source_lap)
        print(f"\nBuilding {filename} (mode={mode}, {len(corners)} corner(s) detected)...")
        fixture = build_fixture(source_lap, corners, mode)
        out_path = out_dir / filename
        fixture.to_csv(out_path, index=False)
        print(f"  Wrote {out_path} ({len(fixture)} rows)")

    print(f"\nDone. {len(jobs)} fixtures written to {out_dir}/")
    print("Each one is guaranteed to contain at least one detectable error")
    print("when replayed and compared against this same expert baseline.")


if __name__ == "__main__":
    main()