"""Read data/latency_log.jsonl after a replay test run and print a
stage-by-stage latency report for Test Matrix #09 (end-to-end delay).

Usage:
    python analyze_latency.py

Expects log lines written by latency_logger.log_event() at these stages,
in this order, for each error event:
    published         (live_coach.py sent the event into the queue)
    received          (ai_core.py's consumer picked it up)
    guardrail_done     (ai_core.py finished the guardrail check)
    queued_for_voice   (AudioManager enqueued the speech - uses a different
                        key, see SOP)
    voice_start        (AudioManager actually started speaking)
"""

import json
import statistics
from pathlib import Path
from collections import defaultdict

# This file lives at PROJECT_ROOT/tests/analyze_latency.py, so the repo
# root is one level up. Adjust this if you move the file elsewhere.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = PROJECT_ROOT / "data" / "latency_log.jsonl"

# Stage pairs we report on, in pipeline order.
STAGE_PAIRS = [
    ("published", "received", "live_coach -> ai_core handoff"),
    ("received", "guardrail_done", "LLM call + guardrail (ai_core internal)"),
    ("queued_for_voice", "voice_start", "AudioManager queue wait"),
]

# The one number Test Matrix #09 actually cares about.
END_TO_END = ("published", "voice_start", "END-TO-END (error -> voice starts)")


def load_events():
    events = defaultdict(dict)  # key -> {stage: ts}
    if not LOG_PATH.exists():
        print(f"No log found at {LOG_PATH}. Run a replay test first.")
        return events
    with open(LOG_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            events[rec["key"]][rec["stage"]] = rec["ts"]
    return events


def summarize(deltas, label):
    if not deltas:
        print(f"  {label}: no samples")
        return
    deltas_sorted = sorted(deltas)
    n = len(deltas_sorted)
    p95_idx = min(n - 1, int(round(0.95 * (n - 1))))
    print(f"  {label}  (n={n})")
    print(f"    min={deltas_sorted[0]:.3f}s  "
          f"median={statistics.median(deltas_sorted):.3f}s  "
          f"p95={deltas_sorted[p95_idx]:.3f}s  "
          f"max={deltas_sorted[-1]:.3f}s")


def main():
    events = load_events()
    if not events:
        return

    print(f"Loaded {len(events)} distinct event keys from {LOG_PATH}\n")

    for start_stage, end_stage, label in STAGE_PAIRS:
        deltas = []
        for key, stages in events.items():
            if start_stage in stages and end_stage in stages:
                deltas.append(stages[end_stage] - stages[start_stage])
        summarize(deltas, label)
        print()

    start_stage, end_stage, label = END_TO_END
    deltas = []
    for key, stages in events.items():
        if start_stage in stages and end_stage in stages:
            deltas.append(stages[end_stage] - stages[start_stage])
    print("=" * 60)
    summarize(deltas, label)
    print("=" * 60)
    print("\nCompare the median/p95 above against the Latency Targets sheet:")
    print("  Good: < 3-5s | Acceptable: 5-10s | Not OK: > 10s")


if __name__ == "__main__":
    main()