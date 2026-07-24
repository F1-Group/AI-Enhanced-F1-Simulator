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
import queue
import threading
import time
from pathlib import Path
import pandas as pd

from .fast_layer import FastLayer
from .alignment import build_baseline, distance_grid
from .error_detection import detect_corners
from .lap_utils import LAP_RESET_DROP_M, clean_telemetry, load_telemetry, split_laps
from .run_analysis import (DEFAULT_EXPERT, LATEST_POINTER,
                           PROJECT_ROOT, analyse_lap, session_id_from)

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
    raise SystemExit("No active recording appeared. Start TORCS first.")


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
    def __init__(self, manager, event_output_queue, use_granite=True, expert_path=None, cache=None):
        self.manager = manager
        self.event_output_queue = event_output_queue
        self.use_granite = use_granite
        self.cache = cache
        self._is_draining = False

        target_expert = expert_path or DEFAULT_EXPERT
        self.expert_laps = split_laps(load_telemetry(target_expert))

        # Expert-side state is built once, not per lap.
        self.track_length = max(lap["lap_distance"].max() for lap in self.expert_laps)
        baseline = build_baseline(self.expert_laps[1:] or self.expert_laps,
                                  distance_grid(self.track_length))
        self.fast_layer = FastLayer(baseline, detect_corners(baseline), manager)
        
        self.lap_number = 0
        self.session_id = None
        self._schema_warned = False
        self._tag_last_queued_time = {}
        self._corner_last_queued_time = {}
        self.cooldown_config = {
            "off_track": 3.0,
            "late_braking": 3.5,
            "poor_corner_exit": 4.0,
            "sector_time_loss": 10.0,
            "default": 3.0
        }


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

    def start_coaching_loop(self, wait_timeout=120.0, idle_timeout_s=10.0, max_seconds=None):
        """Handle the wait for dynamic file generation and start the telemetry tracking loop."""
        print(f"[Coach] Waiting for active recording CSV...")
        csv_path = wait_for_recording(timeout_s=wait_timeout)
        self.follow(csv_path, idle_timeout_s=idle_timeout_s, max_seconds=max_seconds)

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
        queues = (self._snapshots, self._laps)
        completed = False
        try:
            while any(work.unfinished_tasks for work in queues):
                if time.time() >= deadline:
                    self._is_draining = True
                    return False
                time.sleep(0.2)
            completed = True
            return True
        finally:
            if completed:
                for work in queues:
                    work.put(None)
                for worker in (self._snapshot_worker, self._worker):
                    worker.join(timeout=1.0)

    def _lap_worker(self):
        while True:
            rows = self._laps.get()
            try:
                if rows is None:
                    return
                self._process_lap(rows)
            except Exception as exc:
                print(f"[Coach] Lap analysis failed: {exc}")
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
                print(f"[Coach] Real-time analysis failed: {exc}")
            finally:
                self._snapshots.task_done()

    def _publish_new_errors(self, report, lap_number, is_realtime):
        if self._is_draining:
            return []
        
        with self._event_lock:
            # Define priority weights
            priority_weights = {"urgent": 0, "high": 1, "normal": 2, "low": 3, "slow": 4}
            
            valid_errors = []
            for error in report["errors"]:
                location_key = None
                
                # Prioritize reading the structured corner field
                if error.get("corner"):
                    location_key = error["corner"]
                else:
                    # Fall back to text feature matching if structured field is missing
                    msg = error.get("message", "")
                    hint = error.get("coaching_hint", "")
                    for i in range(1, 15):
                        if f"turn{i}" in msg or f"turn{i}" in hint:
                            location_key = f"turn{i}"
                            break
                    if not location_key:
                        for s in (1, 2, 3):
                            if f"Sector {s}" in msg or f"Sector {s}" in hint:
                                location_key = f"Sector {s}"
                                break
                
                # Use tag + lap number as a fallback 
                # if no specific location is found to prevent cross-corner overrides
                error["_location_key"] = location_key or f"{error['tag']}_lap{lap_number}"
                valid_errors.append(error)

            # Keep only the most severe error per specific location in a single snapshot analysis
            location_best_error = {}
            for error in valid_errors:
                loc = error["_location_key"]
                prio = error.get("priority", "normal")
                weight = priority_weights.get(prio, 2)
                
                if loc not in location_best_error:
                    location_best_error[loc] = (weight, error)
                else:
                    # Lower weight represents higher severity
                    if weight < location_best_error[loc][0]:
                        location_best_error[loc] = (weight, error)

            # Implement timeline-based cross-signal throttling defense
            now = time.time()
            new_errors = []

            for loc, (weight, error) in location_best_error.items():
                error_type = error.get("type") or error.get("tag") or "generic_error"
                cooldown_s = self.cooldown_config.get(error_type, self.cooldown_config["default"])

                if loc in self._corner_last_queued_time:
                    if now - self._corner_last_queued_time[loc] < cooldown_s:
                        continue 

                self._corner_last_queued_time[loc] = now
                new_errors.append(error)

        # Limit the maximum number of errors sent per lap or snapshot,
        # then push to the external IPC queue
        MAX_ERRORS_PER_LAP = 2  
        errors_to_process = new_errors[:MAX_ERRORS_PER_LAP]

        for error in errors_to_process:
            event = dict(error)
            event["lap_number"] = lap_number
            event["is_realtime"] = is_realtime
            event["session_id"] = self.session_id
            event["creation_realtime"] = time.time()

            error_type = event.get("type") or event.get("tag")
            if error_type in ["off_track", "brake_now"]:
                event["priority"] = "high"
                event["interrupt"] = True
            else:
                event["priority"] = "normal"
                event["interrupt"] = False

            event.pop("_location_key", None)
            self.event_output_queue.put(event)

        return errors_to_process

    def _process_snapshot(self, rows):
        # Abort and skip analyse_lap if the incoming rows are empty or insufficient
        if not rows or len(rows) < MIN_REALTIME_ROWS:
            return
            
        lap = clean_telemetry(pd.DataFrame(rows))
        if lap.empty:
            return

        # Ensure there are enough unique distance points 
        # to prevent the alignment algorithm from crashing
        if len(lap["lap_distance"].unique()) < 10:
            return

        report, _ = analyse_lap(lap, self.expert_laps, lap_number=self.lap_number + 1)
        self._publish_new_errors(report, self.lap_number + 1, is_realtime=True)


    def _process_lap(self, rows):
        lap = clean_telemetry(pd.DataFrame(rows))
        if lap["lap_distance"].max() < MIN_LAP_FRACTION * self.track_length:
            return
        self.lap_number += 1
        report, _ = analyse_lap(lap, self.expert_laps, lap_number=self.lap_number)

        with self._summary_lock:
            self._lap_reports.append(report)
        self._publish_new_errors(report, self.lap_number, is_realtime=False)

    def follow(self, csv_path, idle_timeout_s, max_seconds=None):
        self.session_id = session_id_from(csv_path)
        print(f"[Coach] Following telemetry logs from {csv_path.name}...")
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
                if self.cache:
                    from data_pipeline.cache import GameStatus
                    
                    if self.cache.get_status() == GameStatus.ERROR:
                        print("[Coach] GameStatus.ERROR detected! (Data pipeline lost connection)")
                        print("[Coach] Forcing emergency shutdown of audio manager...")
                        
                        # Stop audio immediately, block speakers, and flush all queues
                        if self.manager:
                            self.manager.shutdown()

                        self.finish(timeout=5.0)  

                        # Send a poison pill to the AI queue to wrap up immediately
                        if self.event_output_queue:
                            self.event_output_queue.put(None)

                        # Break the telemetry tracking loop instantly to end async processing
                        return
                if max_seconds and time.time() - start > max_seconds:
                    break
                line = _read_complete_line(f)
                if line is None:
                    if time.time() - last_data > idle_timeout_s:
                        print("[Coach] Data stream idled out - ending session.")
                        if self.manager:
                            self.manager.stop_all()
                        break
                    time.sleep(POLL_S)
                    continue
                last_data = time.time()

                if header is None:
                    header = line.split(",")
                    continue
                parts = line.split(",")
                if len(parts) != len(header):
                    continue
                
                frame = {}
                for key, value in zip(header, parts):
                    try:
                        frame[key] = float(value)
                    except ValueError:
                        pass

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
                    # Slice only the last 4 seconds of telemetry (approx. 200 frames)
                    # to prevents the analyzer from pulling duplicate errors from older corners
                    SNAPSHOT_WINDOW_SIZE = 200 
                    recent_snapshot = lap_rows[-SNAPSHOT_WINDOW_SIZE:] if len(lap_rows) > SNAPSHOT_WINDOW_SIZE else lap_rows
                    
                    self.on_snapshot(recent_snapshot)
                    next_realtime_analysis = time.time() + REALTIME_ANALYSIS_INTERVAL_S

        if lap_rows:
            self.on_lap(lap_rows)

        print("[Coach] Telemetry end detected. Draining analysis...")
        self.finish()

        if self.event_output_queue:
            print("[Coach] Injecting completion Poison Pill to AI Worker.")
            self.event_output_queue.put(None)