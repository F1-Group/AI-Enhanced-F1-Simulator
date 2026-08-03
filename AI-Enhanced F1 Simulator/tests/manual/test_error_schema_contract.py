"""Manual check for Test Matrix #3: "error object schema match"
(Team2 error_detection.py -> Team3 ai_core.py, via the in-memory queue).

Question this answers: does the error dict Team2's pipeline actually
produces contain every field Team3's ai_core.py / guardrail.py read off of
it, and does the real pipeline run end-to-end without crashing?

This does NOT hand-build a fake error dict. It runs the real production
functions on real recorded data:
    analysis.run_analysis.analyse_lap()          (error_detection.py's output)
        -> analysis.live_coach.LiveCoach._publish_new_errors()  (adds
           session_id/lap_number/priority/interrupt - the same enrichment
           step the live system performs before an error ever reaches
           Team3)
        -> llm.ai_core.process_single_error()     (Team3's consumer)
so a genuine field mismatch anywhere in that chain will show up here.

Run directly:
    python tests/manual/test_error_schema_contract.py

Or via pytest:
    pytest tests/manual/test_error_schema_contract.py -v -s

Needs a real expert baseline CSV (data/expert_data/) and at least one
player recording (data/player_data/) with a complete, messy-enough lap to
trigger a detectable error. Both are local-only recordings that the team
deliberately never commits to git, so if this machine doesn't have them,
the checks SKIP instead of failing the rest of the suite.
"""

import queue
import sys
import threading
from pathlib import Path

import pytest

THIS_FILE = Path(__file__).resolve()
PROJECT_ROOT = THIS_FILE.parents[2]        # tests/manual/x.py -> tests -> project root
SRC_DIR = PROJECT_ROOT / "src"

# ai_core.py is imported the same way tests/unit/llm_tests/test_ai_core.py
# already does it: put src/llm on sys.path so `import ai_core` finds it as a
# top-level module, and put src/ on sys.path so ai_core.py's own
# `from llm.xxx import ...` imports resolve.
for _p in (str(SRC_DIR), str(SRC_DIR / "llm")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import ai_core  # noqa: E402
from analysis.lap_utils import load_telemetry, split_laps  # noqa: E402
from analysis.live_coach import MIN_LAP_FRACTION, LiveCoach  # noqa: E402
from analysis.run_analysis import DEFAULT_EXPERT, analyse_lap  # noqa: E402

PLAYER_DATA_DIR = PROJECT_ROOT / "data" / "player_data"

# The exact keys ai_core.py / guardrail.py read off an error dict, found by
# grepping `error.get(...)` / `error[...]` in src/llm/ai_core.py and
# src/llm/guardrail.py. If either file starts reading a new field, add it
# here so this test actually covers it.
REQUIRED_FIELDS = [
    "type", "tag", "message", "coaching_hint",
    "session_id", "lap_number", "priority", "telemetry",
    "corner", "severity",
]


def _player_csvs_by_size():
    """Bigger files are more likely to contain a full, messy lap worth
    testing with than the newest one (which might be a 2-second aborted
    recording) - but size alone isn't a guarantee, so callers should still
    try more than one candidate."""
    return sorted(PLAYER_DATA_DIR.glob("*.csv"), key=lambda p: p.stat().st_size, reverse=True)


def _find_complete_lap_in_any(candidates, track_length):
    """Tries candidate CSVs largest-first and returns the first complete lap
    found, since some recordings (aborted runs, disconnects) never reach a
    full lap even if the file itself is large."""
    for player_csv in candidates:
        df = load_telemetry(player_csv)
        for lap in split_laps(df):
            if lap["lap_distance"].max() >= MIN_LAP_FRACTION * track_length:
                return player_csv, lap
    return None, None


def _build_real_event():
    """Runs the real Team2 pipeline on real data and returns ONE fully
    enriched event, exactly as it would come off event_output_queue in
    production. Skips (rather than fails) when the local-only data this
    needs isn't present on this machine."""
    if not DEFAULT_EXPERT.exists():
        pytest.skip(f"No expert baseline at {DEFAULT_EXPERT} (local-only data, not committed to git)")

    candidates = _player_csvs_by_size()
    if not candidates:
        pytest.skip(f"No player recordings found in {PLAYER_DATA_DIR} (local-only data, not committed to git)")

    expert_laps = split_laps(load_telemetry(DEFAULT_EXPERT))
    track_length = max(lap["lap_distance"].max() for lap in expert_laps)

    player_csv, player_lap = _find_complete_lap_in_any(candidates, track_length)
    if player_lap is None:
        pytest.skip(f"No complete lap (>= {MIN_LAP_FRACTION:.0%} of track length) found in any of "
                    f"{len(candidates)} recordings in {PLAYER_DATA_DIR}")

    report, _ = analyse_lap(player_lap, expert_laps, lap_number=1)
    if not report["errors"]:
        pytest.skip(f"{player_csv.name}'s lap triggered no detectable errors - try a messier recording")

    # Real enrichment step, not a hand-rolled guess at what it should add.
    coach = LiveCoach.__new__(LiveCoach)
    coach._is_draining = False
    coach._event_lock = threading.Lock()
    coach._corner_last_queued_time = {}
    coach.cooldown_config = {"default": 0.0}
    coach.event_output_queue = queue.Queue()
    coach.session_id = "schema-contract-test"

    coach._publish_new_errors(report, lap_number=1, is_realtime=False)

    if coach.event_output_queue.empty():
        pytest.skip("_publish_new_errors() produced no events for this recording")

    return coach.event_output_queue.get_nowait()


class _FakeAudioManager:
    """Mirrors the real AudioManager.play_text() signature (see
    src/audio_manager/audio_manager.py) so this test doesn't need to spin up
    pygame/TTS just to prove the call shape is compatible."""

    def __init__(self):
        self.played = []

    def play_text(self, text, priority="normal", interrupt=False, cooldown_key=None, latency_key=None):
        self.played.append(text)


def test_error_event_has_every_field_ai_core_reads():
    """Test Matrix #3: does the real error object contain every field
    ai_core.py / guardrail.py actually read from it?"""
    event = _build_real_event()

    missing = [field for field in REQUIRED_FIELDS if field not in event]
    assert not missing, (
        f"Error event is missing field(s) ai_core.py/guardrail.py expect: {missing}\n"
        f"Fields actually present: {sorted(event.keys())}"
    )

    # telemetry must be a real, non-empty dict, or Team3's LLM prompt gets
    # built with no concrete numbers to reference (feeds into Test Matrix
    # #19 - "are the numbers in the advice correct?").
    assert isinstance(event.get("telemetry"), dict) and event["telemetry"], (
        "event['telemetry'] is empty - the AI prompt would have no real telemetry to reference"
    )

    print(f"\n[schema] all required fields present: {sorted(event.keys())}")
    print(f"[schema] type={event.get('type')!r}  corner={event.get('corner')!r}  "
          f"telemetry keys={sorted(event['telemetry'].keys())}")


def test_process_single_error_runs_without_crashing():
    """Level 2 of the manual check: feed the real event into the real
    ai_core.process_single_error(), the way the live pipeline does, and
    confirm nothing crashes. force_fallback=True skips the real LLM/network
    call, so this needs no API key and stays fast."""
    event = _build_real_event()
    audio = _FakeAudioManager()
    feedback = []

    result = ai_core.process_single_error(
        event,
        system_prompt="You are a concise racing coach.",
        audio_manager=audio,
        force_fallback=True,
        feedback_callback=lambda text, layer: feedback.append((layer, text)),
    )

    assert result is not None, "process_single_error() returned None for a normal-priority error"
    assert result.get("is_valid"), f"guardrail rejected the fallback text: {result}"
    assert feedback, "feedback_callback was never called"
    assert audio.played, "AudioManager.play_text() was never called"

    print(f"\n[pipeline] feedback delivered: {feedback[0][1]!r}")


if __name__ == "__main__":
    print("=== Test Matrix #3: error object schema contract ===")
    try:
        test_error_event_has_every_field_ai_core_reads()
        print("PASS: schema check\n")

        test_process_single_error_runs_without_crashing()
        print("PASS: end-to-end run through ai_core.process_single_error()\n")
    except pytest.skip.Exception as exc:
        print(f"SKIPPED: {exc}")
    except AssertionError as exc:
        print(f"FAIL: {exc}")
        raise SystemExit(1)
