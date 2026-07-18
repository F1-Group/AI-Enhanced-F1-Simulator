"""Week 3 auto-trigger: live coaching while Team 1's pipeline is recording.

Follows the CSV that Team 1's logger is appending (found via
data/latest_data.txt, which the logger writes the moment a session starts):

- every new frame   -> fast layer rules (urgent "Brake now!" interrupts audio)
- every finished lap -> slow layer alignment analysis -> real error JSON
                       -> Granite coaching via the adapter (or fallback audio)

Runs as its own process, so Team 1's game loop is never blocked. The session
is considered over when no new data arrives for --idle-timeout seconds
(mirroring Team 1's own 10-second ERROR rule) or on Ctrl-C.

Usage:
    .venv/bin/python -m analysis.live_coach                # wait for a session
    .venv/bin/python -m analysis.live_coach --no-granite   # skip the LLM call
"""

import argparse
import queue
import threading
import time
from pathlib import Path
import pandas as pd

from .fast_layer import FastLayer
from .alignment import build_baseline, distance_grid
from .error_detection import detect_corners
from .granite_adapter import coach_error
from .lap_utils import LAP_RESET_DROP_M, clean_telemetry, load_telemetry, split_laps
from .run_analysis import (DEFAULT_EXPERT, LATEST_POINTER,
                           TELEMETRY_FIELDS, PROJECT_ROOT, analyse_lap, session_id_from,
                           write_report)

POLL_S = 0.05
MIN_LAP_FRACTION = 0.95
REALTIME_ANALYSIS_INTERVAL_S = 0.75
MIN_REALTIME_ROWS = 50
LAP_TIME_RESET_DROP_S = 2.0
LAP_TIME_RESET_MAX_S = 2.0
LAP_COUNTER_FIELDS = ("lap_count", "lap_number", "lap", "laps", "completed_laps")
COACHING_SUMMARY_PATH = PROJECT_ROOT / "data" / "coaching_summary.json"


def wait_for_recording(timeout_s, freshness_s=10.0):
    """Block until latest_data.txt points at a recently written file.

    Team 1's logger never deletes the pointer, so after every session a stale
    latest_data.txt is left behind. An active recording is appended at 50 Hz,
    so requiring a fresh mtime cleanly ignores yesterday's file.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if LATEST_POINTER.exists():
            path = Path(LATEST_POINTER.read_text().strip())
            if path.exists() and time.time() - path.stat().st_mtime < freshness_s:
                return path
        time.sleep(0.2)
    raise SystemExit("No active recording appeared. Start TORCS + Team 1's "
                     "pipeline (or analysis/replay.py) first.")


def _read_complete_line(f):
    """Read one full line from a growing file, or None if not available yet.

    The logger may have written half a row when we read; in that case seek
    back and retry on the next poll instead of parsing a broken frame.
    """
    pos = f.tell()
    line = f.readline()
    if not line:
        return None
    if not line.endswith("\n"):
        f.seek(pos)
        return None
    return line.rstrip("\n")


def _lap_counter(frame):
    for field in LAP_COUNTER_FIELDS:
        if field in frame:
            return frame[field]
    return None


def _lap_finished(frame, prev_distance, prev_lap_time, prev_lap_counter):
    if prev_distance is not None and frame["lap_distance"] < prev_distance - LAP_RESET_DROP_M:
        return True

    lap_time = frame.get("lap_time")
    if (
        prev_lap_time is not None
        and lap_time is not None
        and prev_lap_time - lap_time >= LAP_TIME_RESET_DROP_S
        and lap_time <= LAP_TIME_RESET_MAX_S
    ):
        return True

    lap_counter = _lap_counter(frame)
    if prev_lap_counter is not None and lap_counter is not None and lap_counter > prev_lap_counter:
        return True

    return False


class LiveCoach:
    def __init__(self, expert_laps, manager, use_granite=True):
        self.expert_laps = expert_laps
        self.manager = manager
        self.use_granite = use_granite

        # Expert-side state is built once, not per lap.
        self.track_length = max(lap["lap_distance"].max() for lap in expert_laps)
        baseline = build_baseline(expert_laps[1:] or expert_laps,
                                  distance_grid(self.track_length))
        self.fast_layer = FastLayer(baseline, detect_corners(baseline), manager)
        self.lap_number = 0
        self.session_id = None
        self._schema_warned = False
        self._reported_error_tags = {}
        self._event_lock = threading.Lock()
        self._summary_lock = threading.Lock()
        self._lap_reports = []
        self._feedback = []

        # Slow-layer work (pandas + Granite network calls + TTS) runs on its
        # own thread so the 50 Hz fast layer never goes blind at lap
        # boundaries waiting for coaching to be generated.
        self._laps = queue.Queue()
        self._worker = threading.Thread(target=self._lap_worker, daemon=True)
        self._worker.start()

        # Real-time slow-layer snapshots are analysed separately from finished
        # laps. The queue keeps only the newest snapshot so live driving never
        # waits behind stale partial-lap work.
        self._snapshots = queue.Queue(maxsize=1)
        self._snapshot_worker = threading.Thread(target=self._snapshot_worker_loop, daemon=True)
        self._snapshot_worker.start()

        # This is the Team 2 -> Team 3 boundary. Blocking get() sleeps while
        # there are no events, instead of polling the filesystem.
        self._error_events = queue.Queue()
        self._event_worker = threading.Thread(target=self._event_worker_loop, daemon=True)
        self._event_worker.start()

    def on_frame(self, frame):
        self.fast_layer.check(frame)

    def on_lap(self, rows):
        self._laps.put(rows)

    def on_snapshot(self, rows):
        if len(rows) < MIN_REALTIME_ROWS:
            return
        snapshot = list(rows)
        try:
            self._snapshots.put_nowait(snapshot)
        except queue.Full:
            try:
                self._snapshots.get_nowait()
                self._snapshots.task_done()
            except queue.Empty:
                pass
            self._snapshots.put_nowait(snapshot)

    def finish(self, timeout=180.0):
        """Wait for queued lap analyses to complete, but never forever.

        A stalled Granite/network call must not hang shutdown (their client
        has no HTTP timeout and sleeps up to ~60 s on rate limits, and the
        first call also loads the RAG models). Returns True when everything
        drained, False if we gave up.
        """
        deadline = time.time() + timeout
        queues = (self._snapshots, self._laps, self._error_events)
        completed = False
        try:
            while any(work.unfinished_tasks for work in queues):
                if time.time() >= deadline:
                    pending = sum(work.unfinished_tasks for work in queues)
                    print(f"[coach] Warning: giving up on {pending} pending in-memory "
                          f"task(s) after {timeout:.0f}s; Granite or the network appears stalled.")
                    return False
                time.sleep(0.2)
            completed = True
            return True
        finally:
            # The unified artifact is the only live-workflow JSON written.
            # Even a timed-out AI call must not lose already completed laps.
            self._write_summary()
            if completed:
                for work in queues:
                    work.put(None)
                for worker in (self._snapshot_worker, self._worker, self._event_worker):
                    worker.join(timeout=1.0)

    def _lap_worker(self):
        while True:
            rows = self._laps.get()
            try:
                if rows is None:
                    return
                self._process_lap(rows)
            except Exception as exc:
                print(f"[coach] Lap analysis failed: {type(exc).__name__}: {exc}")
            finally:
                self._laps.task_done()

    def _snapshot_worker_loop(self):
        while True:
            rows = self._snapshots.get()
            try:
                if rows is None:
                    return
                self._process_snapshot(rows)
            except Exception as exc:
                print(f"[coach] Real-time analysis failed: {type(exc).__name__}: {exc}")
            finally:
                self._snapshots.task_done()

    def _event_worker_loop(self):
        while True:
            event = self._error_events.get(block=True)
            try:
                if event is None:
                    return
                feedback = coach_error(event, self.manager, use_granite=self.use_granite)
                feedback["lap_number"] = event.get("lap_number")
                feedback["is_realtime"] = event.get("is_realtime", False)
                with self._summary_lock:
                    self._feedback.append(feedback)
            except Exception as exc:
                print(f"[coach] AI event processing failed: {type(exc).__name__}: {exc}")
            finally:
                self._error_events.task_done()

    def _publish_new_errors(self, report, lap_number, is_realtime):
        with self._event_lock:
            seen = self._reported_error_tags.setdefault(lap_number, set())
            new_errors = []
            for error in report["errors"]:
                if error["tag"] in seen:
                    continue
                seen.add(error["tag"])
                new_errors.append(error)
        for error in new_errors:
            event = dict(error)
            event["lap_number"] = lap_number
            event["is_realtime"] = is_realtime
            self._error_events.put(event)
        return new_errors

    def _write_summary(self):
        with self._summary_lock:
            summary = {
                "source": "in_memory_live_coach",
                "session_id": self.session_id,
                "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
                "lap_count": len(self._lap_reports),
                "laps": list(self._lap_reports),
                "ai_feedback": list(self._feedback),
            }
        COACHING_SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
        write_report(summary, COACHING_SUMMARY_PATH)
        print(f"[coach] Unified summary written to {COACHING_SUMMARY_PATH.name}")

    def _process_snapshot(self, rows):
        lap = clean_telemetry(pd.DataFrame(rows))
        if lap.empty:
            return

        report, _ = analyse_lap(lap, self.expert_laps, lap_number=self.lap_number + 1)
        lap_number = self.lap_number + 1
        new_errors = self._publish_new_errors(report, lap_number, is_realtime=True)
        if not new_errors:
            return
        tags = ", ".join(error["tag"] for error in new_errors)
        print(f"\n[coach] Real-time error queued in memory: {tags}")

    def _process_lap(self, rows):
        lap = clean_telemetry(pd.DataFrame(rows))
        if lap["lap_distance"].max() < MIN_LAP_FRACTION * self.track_length:
            print(f"[coach] Ignoring incomplete lap fragment "
                  f"({lap['lap_distance'].max():.0f} m).")
            return
        self.lap_number += 1
        report, _ = analyse_lap(lap, self.expert_laps, lap_number=self.lap_number)

        with self._summary_lock:
            self._lap_reports.append(report)
        self._publish_new_errors(report, self.lap_number, is_realtime=False)
        print(f"\n[coach] Lap {self.lap_number} finished: "
              f"{report['player_lap_time_s']}s vs expert {report['expert_lap_time_s']}s, "
              f"{len(report['errors'])} errors accumulated in memory")

    def follow(self, csv_path, idle_timeout_s, max_seconds=None):
        self.session_id = session_id_from(csv_path)
        print(f"[coach] Following {csv_path.name} (session {self.session_id}) ...")
        start = time.time()
        header = None
        lap_rows = []
        prev_distance = None
        prev_lap_time = None
        prev_lap_counter = None
        next_realtime_analysis = time.time() + REALTIME_ANALYSIS_INTERVAL_S

        with open(csv_path, encoding="utf-8") as f:
            last_data = time.time()
            while True:
                if max_seconds and time.time() - start > max_seconds:
                    break
                line = _read_complete_line(f)
                if line is None:
                    if time.time() - last_data > idle_timeout_s:
                        print("[coach] No new data - session finished.")
                        break
                    time.sleep(POLL_S)
                    continue
                last_data = time.time()

                if header is None:
                    header = line.split(",")
                    continue
                parts = line.split(",")
                if len(parts) != len(header):
                    # A torn write from the logger - transient, just skip it.
                    print("[coach] Skipping malformed row.")
                    continue
                frame = {}
                bad_columns = []
                for key, value in zip(header, parts):
                    try:
                        frame[key] = float(value)
                    except ValueError:
                        bad_columns.append(key)
                if bad_columns:
                    # Non-numeric values in a well-formed row mean the schema
                    # changed, not a torn write. Fail loudly if a column we
                    # need is affected; otherwise drop the extras and go on.
                    required = [k for k in bad_columns if k in TELEMETRY_FIELDS]
                    if required:
                        raise SystemExit(
                            f"[coach] Telemetry schema changed: required column(s) "
                            f"{required} are no longer numeric. Sync with Team 1 "
                            f"before coaching this recording.")
                    if not self._schema_warned:
                        print(f"[coach] Ignoring new non-numeric column(s) from "
                              f"Team 1: {bad_columns}")
                        self._schema_warned = True

                self.on_frame(frame)
                d = frame["lap_distance"]
                lap_time = frame.get("lap_time")
                lap_counter = _lap_counter(frame)
                if _lap_finished(frame, prev_distance, prev_lap_time, prev_lap_counter):
                    if lap_rows:
                        self.on_lap(lap_rows)
                    lap_rows = []
                    next_realtime_analysis = time.time() + REALTIME_ANALYSIS_INTERVAL_S
                prev_distance = d
                prev_lap_time = lap_time
                prev_lap_counter = lap_counter
                lap_rows.append(frame)
                if time.time() >= next_realtime_analysis:
                    self.on_snapshot(lap_rows)
                    next_realtime_analysis = time.time() + REALTIME_ANALYSIS_INTERVAL_S

        # Player recordings often end right at the finish line without one
        # more reset row, so flush whatever is buffered as the final lap.
        if lap_rows:
            self.on_lap(lap_rows)


def main():
    parser = argparse.ArgumentParser(description="Live AI coach (Team 2 Week 3).")
    parser.add_argument("--expert", default=str(DEFAULT_EXPERT))
    parser.add_argument("--idle-timeout", type=float, default=10.0)
    parser.add_argument("--wait-timeout", type=float, default=120.0,
                        help="How long to wait for a session to start")
    parser.add_argument("--max-seconds", type=float, default=None,
                        help="Stop after this long (for tests)")
    parser.add_argument("--no-granite", action="store_true",
                        help="Skip the Granite API; use pre-recorded audio only")
    args = parser.parse_args()

    from audio_manager.audio_manager import AudioManager

    expert_laps = split_laps(load_telemetry(args.expert))
    manager = AudioManager()
    coach = LiveCoach(expert_laps, manager, use_granite=not args.no_granite)

    csv_path = wait_for_recording(args.wait_timeout)
    try:
        coach.follow(csv_path, args.idle_timeout, args.max_seconds)
        # Session over: let queued lap analyses finish (they may enqueue
        # audio), then let every coaching clip play out before exiting.
        # This runs after driving ends, so it never blocks the fast layer.
        coach.finish()
        print("[coach] Session done - letting the coaching audio finish...")
        manager.wait_until_idle(timeout=120)
    except KeyboardInterrupt:
        print("\n[coach] Stopped.")  # user asked to quit: exit immediately
    finally:
        manager.shutdown()


if __name__ == "__main__":
    main()
