"""Run ONLY the coaching pipeline (LiveCoach + ai_core consumer +
AudioManager) for latency testing, WITHOUT starting data_pipeline.Client
(which needs a real running TORCS instance to hand-shake with).

Why this exists: main.py starts Client and LiveCoach together, sharing one
`cache` object. If Client can't reach TORCS, it sets cache's status to
GameStatus.ERROR, and LiveCoach's follow() loop sees that and immediately
exits ("GameStatus.ERROR detected... Exiting coaching loop cleanly"),
before replay.py ever gets a chance to feed it data. For a replay-based
latency test we don't have (and don't need) a real TORCS session, so this
script skips Client entirely and gives LiveCoach a fake cache that always
reports "everything is fine" instead of ERROR.

BEFORE running this: nothing extra needed - this script replaces the
"terminal A / start main.py" step in the SOP.

    # Terminal A
    python tests/integration/run_coach_only.py

    # Terminal B (after Terminal A prints it's ready)
    python tests/integration/run_latency_test.py --fixture normal --repeats 8

If your project renamed the `granite/` package to something else (e.g.
`llm/`), fix the import marked below to match.
"""

import sys
import threading
import queue
from pathlib import Path

# stdout is fully block-buffered (not line-buffered) whenever it's not a TTY -
# e.g. redirected to a log file, as the latency-test SOP does. Without this,
# every print() here and in the ai_core/live_coach background threads (status,
# "[Local Granite Error]" failures, everything) sits invisible in the buffer
# for a long time. That looks exactly like the AI thread being stuck with no
# error message, even though the pipeline is actually working normally -
# confirmed by comparing `python3 run_coach_only.py > log` (nothing appears
# for a long time) against `python3 -u run_coach_only.py > log` (appears
# immediately).
sys.stdout.reconfigure(line_buffering=True)

# This file lives at <repo>/tests/integration/run_coach_only.py.
THIS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = THIS_DIR.parent.parent
SRC_DIR = PROJECT_ROOT / "src"

if not SRC_DIR.exists():
    raise SystemExit(f"Expected src/ folder at {SRC_DIR}. "
                      f"Edit PROJECT_ROOT/SRC_DIR at the top of this script if your layout differs.")

sys.path.insert(0, str(SRC_DIR))

from audio_manager.audio_manager import AudioManager
from analysis.live_coach import LiveCoach

from llm.ai_core import ai_queue_consumer_loop


class _AlwaysOkCache:
    """Stand-in for data_pipeline.cache.DataCache during latency tests.

    Always reports RACING (never ERROR), so LiveCoach never thinks the
    (nonexistent, for this test) TORCS connection dropped. Implements the
    same small interface as the real DataCache so it's a safe drop-in
    regardless of exactly how live_coach.py uses `self.cache` internally.
    """

    def __init__(self):
        from data_pipeline.cache import GameStatus
        self._status = GameStatus.RACING

    def get_status(self):
        return self._status

    def set_status(self, status):
        self._status = status

    def get_telemetry(self):
        return None

    def update_telemetry(self, data):
        pass


def run_one_session(audio_manager, llm_connected, session_num):
    """One full record -> analyse -> coach -> summary session.

    Mirrors main.py's start_race_session()/handle_game_finished() pattern: a
    FRESH queue/stop_event/AI-thread/LiveCoach per session (ai_core's
    ai_queue_consumer_loop returns for good once it processes one poison
    pill, so the same thread can't be reused for a second replay), and the
    poison pill is sent the moment the stream ends - NOT bundled with
    ai_stop_event.set() the way the old force-stop fallback did, since
    ai_core treats a set stop_event as an abort signal and skips writing the
    lap summary entirely. ai_stop_event here is reserved for the true
    last-resort case: the AI thread still hasn't finished after 300s.
    """
    shared_event_queue = queue.Queue()
    ai_stop_event = threading.Event()
    fake_cache = _AlwaysOkCache()

    ai_thread = threading.Thread(
        target=ai_queue_consumer_loop,
        args=(shared_event_queue, audio_manager, ai_stop_event, llm_connected),
        daemon=True,
    )
    ai_thread.start()

    coach = LiveCoach(
        manager=audio_manager,
        event_output_queue=shared_event_queue,
        use_granite=llm_connected,
        cache=fake_cache,
    )

    print(f"Ready for session {session_num}. Waiting for a replayed recording "
          f"(run tests/integration/run_latency_test.py in another terminal now)...\n")

    # Same call shape main.py uses; wait_timeout is how long it waits for a
    # recording to appear before giving up, idle_timeout_s is how long it
    # waits after data stops flowing before treating the session as finished.
    coach.start_coaching_loop(wait_timeout=600.0, idle_timeout_s=10.0)

    # Mirror main.py's handle_game_finished(): send the poison pill the
    # moment this session's stream ends, so ai_core drains normally and
    # writes its lap summary instead of relying on a forced-stop path.
    shared_event_queue.put(None)

    print("\nWaiting for the AI thread to finish processing and write its summary...")
    ai_thread.join(timeout=300)
    if ai_thread.is_alive():
        print("AI thread still running after 300s - forcing stop.")
        ai_stop_event.set()
        ai_thread.join(timeout=10)


def main():
    print(f"Project root : {PROJECT_ROOT}")
    print(f"src/ dir     : {SRC_DIR}")
    print("Starting coaching pipeline WITHOUT data_pipeline.Client (no TORCS needed)...\n")

    # Check LLM connectivity BEFORE starting anything, same as main.py does.
    # Without this, ai_core.py may try real Ollama calls that hang or take
    # a long time if the service isn't running / the model isn't pulled,
    # which looks like the AI thread being "stuck" during shutdown.
    llm_connected = False
    try:
        from llm.llm_client import init_granite_model, get_ai_link_status
        print("Checking local Ollama / Granite connectivity...")
        init_granite_model()
        ai_status = get_ai_link_status()
        llm_connected = ai_status.get("llm_connected", False)
        print(f"LLM connected: {llm_connected} ({ai_status.get('message', '')})")
    except Exception as e:
        print(f"[warn] Could not check LLM status ({e}); proceeding with llm_connected=False (fallback text only).")

    audio_manager = AudioManager()

    try:
        # Loop sessions so `run_latency_test.py --repeats N` (N > 1) actually
        # gets analysed N times. The old version called start_coaching_loop()
        # exactly once and shut everything down as soon as that first replay
        # went idle - every later replay in the same --repeats run was
        # written to disk but never picked up by anything.
        session_num = 0
        while True:
            session_num += 1
            run_one_session(audio_manager, llm_connected, session_num)
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    finally:
        audio_manager.shutdown()
        print("Shutdown complete.")


if __name__ == "__main__":
    main()