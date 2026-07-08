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
import os
import pandas as pd

from .fast_layer import FastLayer
from .alignment import build_baseline, distance_grid
from .error_detection import detect_corners
from .granite_adapter import coach_report
from .lap_utils import LAP_RESET_DROP_M, clean_telemetry, load_telemetry, split_laps
from .run_analysis import (DEFAULT_EXPERT, LATEST_POINTER,
                           TELEMETRY_FIELDS, ERROR_REPORT_DIR_PATH, analyse_lap, session_id_from,
                           write_report)

POLL_S = 0.05
MIN_LAP_FRACTION = 0.95


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

        # Slow-layer work (pandas + Granite network calls + TTS) runs on its
        # own thread so the 50 Hz fast layer never goes blind at lap
        # boundaries waiting for coaching to be generated.
        self._laps = queue.Queue()
        self._worker = threading.Thread(target=self._lap_worker, daemon=True)
        self._worker.start()

    def on_frame(self, frame):
        self.fast_layer.check(frame)

    def on_lap(self, rows):
        self._laps.put(rows)

    def finish(self, timeout=180.0):
        """Wait for queued lap analyses to complete, but never forever.

        A stalled Granite/network call must not hang shutdown (their client
        has no HTTP timeout and sleeps up to ~60 s on rate limits, and the
        first call also loads the RAG models). Returns True when everything
        drained, False if we gave up.
        """
        deadline = time.time() + timeout
        while self._laps.unfinished_tasks:
            if time.time() >= deadline:
                print(f"[coach] Warning: giving up on {self._laps.unfinished_tasks} "
                      f"pending lap analysis(es) after {timeout:.0f}s - Granite or "
                      f"the network appears stalled. Reports for those laps were "
                      f"NOT written; re-run 'python -m analysis.run_analysis' on "
                      f"the recording to generate them offline.")
                return False
            time.sleep(0.2)
        return True

    def _lap_worker(self):
        while True:
            rows = self._laps.get()
            try:
                self._process_lap(rows)
            except Exception as exc:
                print(f"[coach] Lap analysis failed: {type(exc).__name__}: {exc}")
            finally:
                self._laps.task_done()

    def _process_lap(self, rows):
        lap = clean_telemetry(pd.DataFrame(rows))
        if lap["lap_distance"].max() < MIN_LAP_FRACTION * self.track_length:
            print(f"[coach] Ignoring incomplete lap fragment "
                  f"({lap['lap_distance'].max():.0f} m).")
            return
        self.lap_number += 1
        report, _ = analyse_lap(lap, self.expert_laps, lap_number=self.lap_number)

        os.makedirs(ERROR_REPORT_DIR_PATH, exist_ok = True)
        output = write_report(report, ERROR_REPORT_DIR_PATH /
                              f"error_report_{self.session_id}_lap{self.lap_number}.json")
        print(f"\n[coach] Lap {self.lap_number} finished: "
              f"{report['player_lap_time_s']}s vs expert {report['expert_lap_time_s']}s, "
              f"{len(report['errors'])} errors -> {output.name}")

        coach_report(report, self.manager, use_granite=self.use_granite)

    def follow(self, csv_path, idle_timeout_s, max_seconds=None):
        self.session_id = session_id_from(csv_path)
        print(f"[coach] Following {csv_path.name} (session {self.session_id}) ...")
        start = time.time()
        header = None
        lap_rows = []
        prev_distance = None

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
                if prev_distance is not None and d < prev_distance - LAP_RESET_DROP_M:
                    self.on_lap(lap_rows)
                    lap_rows = []
                prev_distance = d
                lap_rows.append(frame)

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
