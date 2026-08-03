"""Manual check for Test Matrix #11: "Fast layer / real-time snapshot /
full-lap analysis - 3-tier delay".

Measures the PURE COMPUTE TIME of each of Team2's 3 analysis tiers,
directly, on a real recorded lap - no TORCS, no main.py, no audio needed:

    Tier 1 - fast_layer.py's FastLayer.check(), called once per telemetry
             frame (50 Hz live). Budget: must comfortably fit inside the
             ~20ms/frame gap between frames (see FRAME_INTERVAL_S in
             analysis/replay.py), or the fast layer would start lagging
             behind incoming TORCS packets.
    Tier 2 - LiveCoach's real-time snapshot analysis (analyse_lap(...,
             is_realtime=True) on ~200-frame windows), which fires every
             REALTIME_ANALYSIS_INTERVAL_S seconds mid-lap. Budget: must
             finish faster than that interval, or snapshots start piling up
             behind each other.
    Tier 3 - full-lap analysis (analyse_lap(..., is_realtime=False)) run
             once when a lap finishes. Budget: must finish faster than the
             lap itself took to drive, so analysis never permanently falls
             behind lap-over-lap.

This measures each tier's OWN computation only - not the downstream
ai_core -> guardrail -> AudioManager chain (that's Test Matrix #09, already
covered by tests/integration/latency_logger.py + analyze_latency.py), and
not the "decision -> audio actually starts playing" gap (best observed by
ear/console during a live replay, or by reading data/latency_log.jsonl's
queued_for_voice/voice_start pairs after one).

Per the team's Internal_Test_Plan_1 "Latency Targets" sheet, the Fast layer
row ("TORCS packet arrives -> fast_layer.py decides -> AudioManager.play()
starts playing", target <0.5s / acceptable 0.5-1s / not OK >1s) is explicitly
cross-referenced to this test (#11), so tier 1 below reports against those
bands too - as an informational overlay alongside the stricter 20ms/frame
budget, not a replacement for it, since the sheet's number covers the
audio-start step this tier deliberately doesn't measure. The sheet has no
row for tiers 2/3, so those keep only the code-derived budgets.

Run directly:
    python tests/manual/test_three_tier_latency.py

Or via pytest:
    pytest tests/manual/test_three_tier_latency.py -v -s

Needs a real expert baseline CSV (data/expert_data/) and at least one
complete player lap (data/player_data/) - both local-only recordings the
team never commits to git, so this SKIPS instead of failing when they're
missing.
"""

import statistics
import sys
import time
from pathlib import Path

import pandas as pd
import pytest

THIS_FILE = Path(__file__).resolve()
PROJECT_ROOT = THIS_FILE.parents[2]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from analysis.alignment import build_baseline, distance_grid  # noqa: E402
from analysis.error_detection import detect_corners  # noqa: E402
from analysis.fast_layer import FastLayer  # noqa: E402
from analysis.lap_utils import clean_telemetry, load_telemetry, split_laps  # noqa: E402
from analysis.live_coach import (  # noqa: E402
    MIN_LAP_FRACTION,
    MIN_REALTIME_ROWS,
    REALTIME_ANALYSIS_INTERVAL_S,
)
from analysis.run_analysis import DEFAULT_EXPERT, analyse_lap  # noqa: E402

PLAYER_DATA_DIR = PROJECT_ROOT / "data" / "player_data"
SNAPSHOT_WINDOW_SIZE = 200          # matches live_coach.py's follow() loop
FRAME_INTERVAL_S = 0.02             # TORCS' 50Hz rate, matches replay.py
MAX_SNAPSHOT_SAMPLES = 15           # cap runtime of this manual script
MAX_LAP_SAMPLES = 3

# Internal_Test_Plan_1's "Latency Targets" sheet, Fast layer row - the only
# row explicitly cross-referenced to Test Matrix #11. Covers the FULL
# "packet arrives -> decides -> AudioManager.play() starts playing" chain;
# tier 1 below only measures the "decides" part, so treat this as an
# informational upper-bound check, not a tighter substitute for the
# FRAME_INTERVAL_S budget above.
SHEET_FAST_LAYER_GOOD_S = 0.5
SHEET_FAST_LAYER_ACCEPTABLE_S = 1.0


def _sheet_band(elapsed_s, good_s, acceptable_s):
    if elapsed_s < good_s:
        return "GOOD"
    if elapsed_s < acceptable_s:
        return "ACCEPTABLE"
    return "NOT OK"


def _player_csvs_by_size():
    """Bigger files are more likely to contain full laps than the newest one
    (which might be a 2-second aborted recording) - but not guaranteed, so
    callers still need to try more than one candidate."""
    return sorted(PLAYER_DATA_DIR.glob("*.csv"), key=lambda p: p.stat().st_size, reverse=True)


def _find_complete_laps_in_any(candidates, track_length, limit=MAX_LAP_SAMPLES):
    """Tries candidate CSVs largest-first and returns up to `limit` complete
    laps from the first file that has any, since some recordings (aborted
    runs, disconnects) never reach a full lap even if the file is large."""
    for player_csv in candidates:
        df = load_telemetry(player_csv)
        laps = [lap for lap in split_laps(df) if lap["lap_distance"].max() >= MIN_LAP_FRACTION * track_length]
        if laps:
            return player_csv, laps[:limit]
    return None, []


def _load_fixture():
    """Shared setup for all 3 tiers: expert baseline + one or more complete
    player laps. Skips (rather than fails) if this machine doesn't have the
    local recordings needed."""
    if not DEFAULT_EXPERT.exists():
        pytest.skip(f"No expert baseline at {DEFAULT_EXPERT} (local-only data, not committed to git)")

    candidates = _player_csvs_by_size()
    if not candidates:
        pytest.skip(f"No player recordings found in {PLAYER_DATA_DIR} (local-only data, not committed to git)")

    expert_laps = split_laps(load_telemetry(DEFAULT_EXPERT))
    track_length = max(lap["lap_distance"].max() for lap in expert_laps)

    player_csv, player_laps = _find_complete_laps_in_any(candidates, track_length)
    if not player_laps:
        pytest.skip(f"No complete lap (>= {MIN_LAP_FRACTION:.0%} of track length) found in any of "
                    f"{len(candidates)} recordings in {PLAYER_DATA_DIR}")

    return expert_laps, track_length, player_laps, player_csv.name


def _summarize(samples_s, label, unit_ms=True):
    if not samples_s:
        print(f"  {label}: no samples")
        return None
    values = sorted(v * 1000 if unit_ms else v for v in samples_s)
    n = len(values)
    p95_idx = min(n - 1, int(round(0.95 * (n - 1))))
    unit = "ms" if unit_ms else "s"
    print(f"  {label}  (n={n})")
    print(f"    min={values[0]:.3f}{unit}  median={statistics.median(values):.3f}{unit}  "
          f"p95={values[p95_idx]:.3f}{unit}  max={values[-1]:.3f}{unit}")
    return values


def test_tier1_fast_layer_is_near_instant():
    """Fast layer runs on every 50Hz frame; its own decision-making must fit
    comfortably inside the ~20ms gap between frames."""
    expert_laps, track_length, player_laps, csv_name = _load_fixture()
    player_lap = player_laps[0]

    # Mirrors LiveCoach.__init__'s own baseline/corner setup (live_coach.py)
    # exactly, so this is the same FastLayer configuration production uses.
    baseline = build_baseline(expert_laps[1:] or expert_laps, distance_grid(track_length))
    corners = detect_corners(baseline)
    fast_layer = FastLayer(baseline, corners, manager=None, event_queue=None)

    frames = player_lap.to_dict("records")
    timings = []
    for frame in frames:
        t0 = time.perf_counter()
        fast_layer.check(frame)
        timings.append(time.perf_counter() - t0)

    print(f"\n[tier 1] fast_layer.check() over {len(frames)} real frames from {csv_name}:")
    _summarize(timings, "per-frame decision time")

    mean_s = statistics.mean(timings)
    p95_s = sorted(timings)[min(len(timings) - 1, int(round(0.95 * (len(timings) - 1))))]
    print(f"  vs. Latency Targets sheet's Fast-layer row (decide-only subset of its "
          f"<{SHEET_FAST_LAYER_GOOD_S:.1f}s good / <{SHEET_FAST_LAYER_ACCEPTABLE_S:.1f}s acceptable band):")
    print(f"    mean band={_sheet_band(mean_s, SHEET_FAST_LAYER_GOOD_S, SHEET_FAST_LAYER_ACCEPTABLE_S)}  "
          f"p95 band={_sheet_band(p95_s, SHEET_FAST_LAYER_GOOD_S, SHEET_FAST_LAYER_ACCEPTABLE_S)}  "
          f"(note: this excludes the sheet's 'AudioManager.play() starts playing' segment)")

    assert mean_s < FRAME_INTERVAL_S, (
        f"fast_layer.check() averages {mean_s * 1000:.3f}ms/frame, "
        f"which does not fit inside the {FRAME_INTERVAL_S * 1000:.0f}ms/frame budget at 50Hz"
    )
    assert p95_s < SHEET_FAST_LAYER_ACCEPTABLE_S, (
        f"fast_layer.check()'s p95 ({p95_s:.3f}s) alone already exceeds the sheet's "
        f"{SHEET_FAST_LAYER_ACCEPTABLE_S}s 'Not OK' bound for the WHOLE fast-layer chain"
    )


def test_tier2_realtime_snapshot_beats_its_own_interval():
    """Real-time snapshot analysis fires every REALTIME_ANALYSIS_INTERVAL_S
    seconds; each pass must finish faster than that or snapshots start
    backing up behind each other."""
    expert_laps, track_length, player_laps, csv_name = _load_fixture()
    player_lap = player_laps[0]

    frames = player_lap.to_dict("records")
    windows = [frames[i:i + SNAPSHOT_WINDOW_SIZE] for i in range(0, len(frames), SNAPSHOT_WINDOW_SIZE)]
    windows = [w for w in windows if len(w) >= MIN_REALTIME_ROWS]
    if len(windows) > MAX_SNAPSHOT_SAMPLES:
        step = len(windows) / MAX_SNAPSHOT_SAMPLES
        windows = [windows[int(i * step)] for i in range(MAX_SNAPSHOT_SAMPLES)]
    if not windows:
        pytest.skip(f"{csv_name}'s lap is too short to form a {MIN_REALTIME_ROWS}-row snapshot window")

    timings = []
    for i, window in enumerate(windows, start=1):
        lap_df = clean_telemetry(pd.DataFrame(window))
        if lap_df.empty or len(lap_df["lap_distance"].unique()) < 10:
            continue
        t0 = time.perf_counter()
        analyse_lap(lap_df, expert_laps, lap_number=1, is_realtime=True)
        timings.append(time.perf_counter() - t0)

    print(f"\n[tier 2] real-time snapshot analysis over {len(timings)} windows "
          f"(~{SNAPSHOT_WINDOW_SIZE} frames each) from {csv_name}:")
    values_ms = _summarize(timings, "per-snapshot analysis time")

    assert timings, "no snapshot window produced a valid analysis to time"
    median_s = statistics.median(timings)
    assert median_s < REALTIME_ANALYSIS_INTERVAL_S, (
        f"median snapshot analysis takes {median_s:.3f}s, slower than the "
        f"{REALTIME_ANALYSIS_INTERVAL_S}s interval snapshots are requested at - "
        f"they would start piling up behind each other"
    )


def test_tier3_full_lap_analysis_keeps_pace_with_the_lap():
    """Full-lap analysis runs once per finished lap; it must complete faster
    than the lap itself took to drive, or analysis would permanently fall
    behind lap-over-lap."""
    expert_laps, track_length, player_laps, csv_name = _load_fixture()

    timings = []
    lap_times_s = []
    for i, player_lap in enumerate(player_laps, start=1):
        t0 = time.perf_counter()
        report, _ = analyse_lap(player_lap, expert_laps, lap_number=i, is_realtime=False)
        timings.append(time.perf_counter() - t0)
        lap_times_s.append(report["player_lap_time_s"])

    print(f"\n[tier 3] full-lap analysis over {len(timings)} complete lap(s) from {csv_name}:")
    _summarize(timings, "full-lap analysis time", unit_ms=False)
    print(f"  (for comparison, the lap(s) themselves took: "
          f"{', '.join(f'{t:.1f}s' for t in lap_times_s)})")

    for analysis_s, lap_s in zip(timings, lap_times_s):
        assert analysis_s < lap_s, (
            f"full-lap analysis took {analysis_s:.2f}s, longer than the {lap_s:.2f}s "
            f"the lap itself took to drive - analysis would fall behind lap-over-lap"
        )


if __name__ == "__main__":
    print("=== Test Matrix #11: 3-tier analysis delay ===")
    tests = [
        test_tier1_fast_layer_is_near_instant,
        test_tier2_realtime_snapshot_beats_its_own_interval,
        test_tier3_full_lap_analysis_keeps_pace_with_the_lap,
    ]
    for test in tests:
        try:
            test()
            print(f"PASS: {test.__name__}\n")
        except pytest.skip.Exception as exc:
            print(f"SKIPPED: {test.__name__}: {exc}\n")
        except AssertionError as exc:
            print(f"FAIL: {test.__name__}: {exc}\n")
