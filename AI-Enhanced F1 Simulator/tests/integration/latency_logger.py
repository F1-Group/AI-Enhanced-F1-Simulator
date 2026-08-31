"""Shared latency logging helper for the end-to-end delay test (Test Matrix #09).

Everyone on the team uses this SAME module so timestamps from different
stages (live_coach.py, ai_core.py, audio_manager.py) end up in one file with
a consistent format, and can be lined up afterwards by analyze_latency.py.

Usage:
    from latency_logger import log_event
    log_event("published", key=f"{event['session_id']}:{event['tag']}")

Log file location: data/latency_log.jsonl (one JSON object per line).
Safe to call from multiple threads - each write is a single atomic line.
"""

import json
import time
import threading
from pathlib import Path

# This file lives at PROJECT_ROOT/tests/latency_logger.py, so the repo
# root is one level up. Adjust this if you move the file elsewhere.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = PROJECT_ROOT / "data" / "latency_log.jsonl"

_lock = threading.Lock()


def log_event(stage: str, key: str, detail: str = ""):
    """Append one timestamped event to the shared latency log.

    stage:  which pipeline stage this marks, e.g. "published", "received",
            "guardrail_done", "queued_for_voice", "voice_start".
    key:    a string that lets analyze_latency.py match up the SAME event
            across different stages. Use the error's session_id+tag+lap
            for the live_coach -> ai_core -> guardrail stages, and the
            AudioManager's internal created_time (as a string) for the
            queued_for_voice -> voice_start stages (see audio_manager
            instrumentation notes in Latency_Test_SOP.md).
    detail: optional free-text, e.g. the error type, for readability when
            eyeballing the raw log file.
    """
    record = {"ts": time.time(), "stage": stage, "key": str(key), "detail": detail}
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False)
    with _lock:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def reset_log():
    """Call this once at the start of a fresh test run to clear old data."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text("", encoding="utf-8")
    print(f"[latency_logger] Cleared {LOG_PATH}")


if __name__ == "__main__":
    reset_log()
    log_event("published", key="demo:Turn 3_late_braking:1", detail="smoke test")
    log_event("received", key="demo:Turn 3_late_braking:1", detail="smoke test")
    print(f"Wrote a demo entry to {LOG_PATH}")