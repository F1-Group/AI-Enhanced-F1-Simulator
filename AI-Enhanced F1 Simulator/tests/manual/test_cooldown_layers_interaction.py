"""Manual check for Test Matrix #16: "Multiple cooldown/throttle layers
interaction" - FastLayer's own 3s cooldown, LiveCoach.cooldown_config
(3-10s by error type), AudioManager's 4s cooldown, and the audio queue's
2.5s stale-drop, "all 4 run independently". Expected result per the sheet:
"The 4 layers don't conflict; voice pacing feels right, no over-suppression
or spam."

Each layer is tested against the REAL production code (not a re-implemented
copy of the throttling logic), one at a time so a failure points at exactly
which layer misbehaved:

    1. FastLayer's own per-tag 3s cooldown (fast_layer.py, pure/synthetic -
       no recorded data or audio needed).
    2. LiveCoach.cooldown_config's per-location throttling in
       _publish_new_errors() (live_coach.py, pure/synthetic). This directly
       re-exercises the corner-naming + per-location-key fix from
       error_detection.py/live_coach.py - before that fix, every corner
       shared one location key and this layer could not throttle correctly
       per corner.
    3. AudioManager's own per (type + corner) cooldown in play_error()
       (audio_manager.py, real AudioManager instance).
    4. The audio queue's 2.5s stale-drop in _audio_loop() (real
       AudioManager, real TTS - a queued line stuck behind a long
       utterance must be dropped, not spoken late).
    5. Voice interrupt reaction time (real AudioManager, real TTS): does an
       urgent brake_now/off_track alert still cut in quickly while another
       layer's cooldown-gated audio is speaking? Reported against the team's
       Internal_Test_Plan_1 "Latency Targets" sheet's Voice-interrupt-
       reaction row (<0.3s good / 0.3-0.8s acceptable / >0.8s not OK).

Sub-tests 3-5 use the REAL AudioManager end to end. Sub-test 3 only checks
play_error()'s return value (accepted vs cooldown-suppressed), which is
decided synchronously before anything is actually spoken, so it needs no
working TTS and runs on every platform. Sub-tests 4 and 5 are different:
they need a filler phrase to occupy real wall-clock seconds of ACTUAL
speech, so there's a genuine window to test "does the next line go stale"
/ "does an interrupt cut in quickly". That depends on whichever TTS path
AudioManager._speak_text() actually has for the current OS:
    - macOS: always runs - the real 'say' command backs this today.
    - Windows: runs only if `pyttsx3` is importable (AudioManager's own
      Windows fallback needs it, and it isn't currently one of this
      project's tracked dependencies - see _tts_support_reason() below).
    - Linux: always skipped, with a clear reason - AudioManager has no
      Linux branch in _speak_text() at all (only Darwin's 'say' and
      Windows' pyttsx3 are handled), so there is no real speech to test
      against on Linux today.
This is a test-side platform check, not a change to AudioManager itself -
see _tts_support_reason() for the exact conditions mirrored from
audio_manager.py's own _speak_text()/__init__ branches.

On a platform where sub-tests 4-5 run, expect them to briefly play a few
short lines of audio out loud and take a few real seconds each (they
deliberately wait out part of a cooldown/stale window).

Run directly:
    python tests/manual/test_cooldown_layers_interaction.py

Or via pytest:
    pytest tests/manual/test_cooldown_layers_interaction.py -v -s

Sub-tests 1-2 use small hand-built synthetic data and need no local
recordings. Sub-tests 3-5 only need a working audio/TTS setup (same as the
rest of the app), not recorded CSVs.
"""

import platform
import queue
import shutil
import sys
import threading
import time
from pathlib import Path

import pandas as pd
import pytest

THIS_FILE = Path(__file__).resolve()
PROJECT_ROOT = THIS_FILE.parents[2]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from analysis.fast_layer import FastLayer  # noqa: E402
from analysis.live_coach import LiveCoach  # noqa: E402
from audio_manager.audio_manager import AudioManager  # noqa: E402

# Internal_Test_Plan_1's "Latency Targets" sheet, Voice interrupt reaction
# row - used by sub-test 5 below.
SHEET_INTERRUPT_GOOD_S = 0.3
SHEET_INTERRUPT_ACCEPTABLE_S = 0.8


def _sheet_band(elapsed_s, good_s, acceptable_s):
    if elapsed_s < good_s:
        return "GOOD"
    if elapsed_s < acceptable_s:
        return "ACCEPTABLE"
    return "NOT OK"


def _tts_support_reason():
    """None if this platform's AudioManager can actually produce real,
    time-consuming speech (so sub-tests 4/5 below have something real to
    test); otherwise a human-readable reason to skip them.

    Mirrors audio_manager.py's own platform branches (__init__ and
    _speak_text()) exactly, without importing or modifying that file:
    only Darwin (via the 'say' command) and Windows (via pyttsx3, if
    installed) have a working TTS path there today. Any other OS -
    Linux included - falls through to _speak_text()'s final
    "Speech fallback platform unavailable" branch and returns near-
    instantly with no real speech, which would make sub-tests 4/5 fail
    for a reason that has nothing to do with the cooldown/interrupt logic
    they're meant to check.
    """
    system = platform.system()
    if system == "Darwin":
        if shutil.which("say") is None:
            return "macOS detected, but the 'say' command is not available on PATH"
        return None
    if system == "Windows":
        try:
            import pyttsx3  # noqa: F401
        except ImportError:
            return ("Windows detected, but pyttsx3 is not installed - AudioManager's Windows "
                    "TTS fallback needs it (pip install pyttsx3), and it isn't currently one "
                    "of this project's tracked dependencies")
        return None
    return (f"{system} has no TTS path in AudioManager._speak_text() at all (only Darwin's "
            f"'say' and Windows' pyttsx3 are handled) - the production AudioManager currently "
            f"has no Linux TTS support, so there is no real speech on {system} for these "
            f"sub-tests to occupy real time with")


# --- Layer 1: FastLayer's own 3s per-tag cooldown ---------------------------

def test_layer1_fastlayer_own_cooldown():
    """A synthetic corner + a frame that satisfies brake_now's conditions,
    checked twice back-to-back with no real time elapsed: the 2nd call must
    be suppressed by FastLayer's internal 3s cooldown (fast_layer.py's
    _last_fast_play), independent of AudioManager entirely (manager=None)."""
    baseline = pd.DataFrame({
        "lap_distance": [0.0, 50.0, 100.0, 150.0, 200.0, 500.0],
        "speed_kmh": [150.0, 150.0, 150.0, 150.0, 150.0, 150.0],
    })
    corners = [{"name": "T1", "start_m": 100.0, "end_m": 150.0, "apex_m": 130.0}]
    fast_layer = FastLayer(baseline, corners, manager=None, event_queue=None)

    # Inside T1's approach zone (start_m - 150 = -50, up to apex 130),
    # clearly overspeeding (>25km/h over the 150km/h baseline) and not
    # braking - satisfies fast_layer.py's brake_now condition.
    frame = {"lap_distance": 60.0, "speed_kmh": 200.0, "brake": 0.0, "track_pos": 0.0}

    first = fast_layer.check(frame)
    second = fast_layer.check(frame)

    assert first == "brake_now", f"expected the first check() to fire brake_now, got {first!r}"
    assert second is None, (
        f"expected the immediate repeat to be suppressed by FastLayer's own 3s cooldown, got {second!r}"
    )
    print("\n[layer 1] FastLayer's own cooldown suppressed an immediate repeat, as expected")


# --- Layer 2: LiveCoach.cooldown_config per-location throttling -------------

def _make_error(tag, corner, error_type, priority="normal"):
    return {
        "tag": tag, "corner": corner, "type": error_type, "priority": priority,
        "message": f"{error_type} at {corner}", "coaching_hint": "hint",
        "audio_key": error_type, "audio_file": f"audio/{error_type}.wav",
    }


def _bare_live_coach():
    coach = LiveCoach.__new__(LiveCoach)
    coach._is_draining = False
    coach._event_lock = threading.Lock()
    coach._corner_last_queued_time = {}
    coach.cooldown_config = {
        "off_track": 3.0, "late_braking": 3.5, "poor_corner_exit": 4.0,
        "sector_time_loss": 10.0, "default": 3.0,
    }
    coach.event_output_queue = queue.Queue()
    coach.session_id = "cooldown-layer-test"
    return coach


def test_layer2_livecoach_cooldown_is_per_location():
    """The same corner+type repeated immediately must be throttled by
    LiveCoach.cooldown_config; a DIFFERENT corner in the same instant must
    NOT be throttled by it. This is exactly the behaviour the corner-naming
    fix (detect_corners() previously named every corner "corner") and the
    per-location _corner_last_queued_time keying depend on."""
    coach = _bare_live_coach()

    report_t1_first = {"errors": [_make_error("T1_late_braking", "T1", "late_braking")]}
    report_t1_again = {"errors": [_make_error("T1_late_braking", "T1", "late_braking")]}
    report_t2 = {"errors": [_make_error("T2_late_braking", "T2", "late_braking")]}

    first = coach._publish_new_errors(report_t1_first, lap_number=1, is_realtime=False)
    repeat = coach._publish_new_errors(report_t1_again, lap_number=1, is_realtime=False)
    other_corner = coach._publish_new_errors(report_t2, lap_number=1, is_realtime=False)

    assert len(first) == 1, f"expected T1's first late_braking through, got {first}"
    assert len(repeat) == 0, f"expected the immediate T1 repeat to be cooldown-suppressed, got {repeat}"
    assert len(other_corner) == 1, f"expected T2's late_braking to be unaffected by T1's cooldown, got {other_corner}"
    print("\n[layer 2] LiveCoach.cooldown_config throttled a same-corner repeat "
          "without affecting a different corner")


# --- Layer 3: AudioManager's own per (type + corner) cooldown ---------------

def test_layer3_audiomanager_cooldown_is_per_type_and_corner():
    """The same (type, corner) repeated immediately must be suppressed by
    AudioManager's own cooldown (play_error()'s _cooldown_key_for_error);
    a different corner must not be. Shuts down right after the calls so the
    (short) queued lines don't need to fully play out."""
    manager = AudioManager()
    try:
        err_t1 = _make_error("T1_late_braking", "T1", "late_braking")
        err_t1_repeat = _make_error("T1_late_braking", "T1", "late_braking")
        err_t2 = _make_error("T2_late_braking", "T2", "late_braking")

        first = manager.play_error(err_t1)
        repeat = manager.play_error(err_t1_repeat)
        other_corner = manager.play_error(err_t2)

        assert first is True, "expected T1's first late_braking to be accepted"
        assert repeat is False, "expected the immediate T1 repeat to be cooldown-suppressed"
        assert other_corner is True, "expected T2's late_braking to be unaffected by T1's cooldown"
        print("\n[layer 3] AudioManager's cooldown throttled a same-(type,corner) repeat "
              "without affecting a different corner")
    finally:
        manager.shutdown()


# --- Layer 4: the 2.5s stale-drop -------------------------------------------

def test_layer4_stale_items_are_dropped_not_spoken_late():
    """A short line queued right behind a long filler utterance should sit
    long enough to cross the audio queue's 2.5s staleness budget and get
    dropped - never spoken, not even late. Uses real TTS so the real
    _audio_loop staleness check (age > 2.5s) is what's actually exercised."""
    skip_reason = _tts_support_reason()
    if skip_reason:
        pytest.skip(skip_reason)

    manager = AudioManager()
    spoken = []
    original_speak = manager._speak_text

    def recording_speak(text):
        spoken.append(text)
        return original_speak(text)

    manager._speak_text = recording_speak
    try:
        manager.play_text(
            "This filler sentence exists only to occupy the voice channel for several "
            "seconds so the next queued line goes stale before its turn comes up",
            priority="normal", cooldown_key="layer4-filler",
        )
        time.sleep(0.15)  # let the worker actually start the filler
        manager.play_text("this line should be dropped as stale", priority="normal", cooldown_key="layer4-dropped")
        manager.wait_until_idle(timeout=15.0)
    finally:
        manager.shutdown()

    assert any("filler sentence" in s for s in spoken), "the filler phrase itself never played"
    assert not any("dropped as stale" in s for s in spoken), (
        "a line queued behind a long utterance should be dropped once stale (>2.5s), not spoken late"
    )
    print("\n[layer 4] the line queued behind the filler was correctly dropped as stale, not spoken late")


# --- Layer 5: interrupt reaction time while another layer is active --------

def test_layer5_interrupt_reaction_time_vs_sheet_target():
    """An urgent brake_now alert must still cut in quickly even while normal
    coaching audio (subject to the other 3 cooldown/throttle layers above)
    is actively speaking. Reaction time is reported against the Latency
    Targets sheet's Voice-interrupt-reaction row."""
    skip_reason = _tts_support_reason()
    if skip_reason:
        pytest.skip(skip_reason)

    manager = AudioManager()
    speak_events = []
    original_speak = manager._speak_text

    def recording_speak(text):
        speak_events.append((text, time.perf_counter()))
        return original_speak(text)

    manager._speak_text = recording_speak
    try:
        manager.play_text(
            "This is a long filler sentence used only to keep the voice channel busy "
            "long enough to test whether an urgent interrupt actually cuts it off quickly",
            priority="normal", cooldown_key="layer5-filler",
        )
        time.sleep(0.2)  # let the worker actually start speaking the filler

        t_interrupt = time.perf_counter()
        manager.play("brake_now", interrupt=True)
        manager.wait_until_idle(timeout=10.0)
    finally:
        manager.shutdown()

    brake_events = [t for text, t in speak_events if text == "Brake now"]
    assert brake_events, "the interrupting brake_now alert never actually started speaking"
    reaction_s = brake_events[0] - t_interrupt

    band = _sheet_band(reaction_s, SHEET_INTERRUPT_GOOD_S, SHEET_INTERRUPT_ACCEPTABLE_S)
    print(f"\n[layer 5] interrupt reaction time: {reaction_s * 1000:.1f}ms  (sheet band: {band})")
    print(f"  Latency Targets sheet - Voice interrupt reaction: "
          f"<{SHEET_INTERRUPT_GOOD_S * 1000:.0f}ms good / "
          f"{SHEET_INTERRUPT_GOOD_S * 1000:.0f}-{SHEET_INTERRUPT_ACCEPTABLE_S * 1000:.0f}ms acceptable / "
          f">{SHEET_INTERRUPT_ACCEPTABLE_S * 1000:.0f}ms not OK")

    assert reaction_s < SHEET_INTERRUPT_ACCEPTABLE_S, (
        f"interrupt took {reaction_s:.3f}s to actually start speaking - "
        f"past the sheet's 'Not OK' (>{SHEET_INTERRUPT_ACCEPTABLE_S}s) bound"
    )


if __name__ == "__main__":
    print("=== Test Matrix #16: multiple cooldown/throttle layers interaction ===")
    tests = [
        test_layer1_fastlayer_own_cooldown,
        test_layer2_livecoach_cooldown_is_per_location,
        test_layer3_audiomanager_cooldown_is_per_type_and_corner,
        test_layer4_stale_items_are_dropped_not_spoken_late,
        test_layer5_interrupt_reaction_time_vs_sheet_target,
    ]
    for test in tests:
        try:
            test()
            print(f"PASS: {test.__name__}\n")
        except pytest.skip.Exception as exc:
            print(f"SKIPPED: {test.__name__}: {exc}\n")
        except AssertionError as exc:
            print(f"FAIL: {test.__name__}: {exc}\n")
