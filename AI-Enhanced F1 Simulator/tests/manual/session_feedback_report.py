"""Post-session helper for Test Matrix #27: "Real driver feedback for a full
session" - Whole system (all 3 coaching styles). Expected result: "Collect
feedback into a written summary."

This is NOT a pass/fail test (there's no name starting with test_, so pytest
never auto-collects it): row #27 explicitly needs a real non-developer to
drive a full session and give a subjective opinion on voice frequency,
timing, and style preference. No script can substitute for that judgment.

What this DOES do: after a real session, pull the objective numbers a human
reviewer would otherwise have to guess from memory out of the same logs the
rest of the system already writes - data/latency_log.jsonl (per-stage
timestamps) and data/coaching_summary.json / data/lap_summary.json (what
was actually said) - and write them into a markdown file next to a blank,
structured template for the tester's own notes. The tester fills in the
subjective half; this script fills in the factual half.

SOP:
    1. Before the session:  python -c "from latency_logger import reset_log; reset_log()"
       (run from tests/integration/, or see tests/integration/latency_logger.py)
    2. Start the app and have the non-developer drive a full session with
       one of the 3 coaching styles active.
    3. After the session ends:
           python tests/manual/session_feedback_report.py --style supportive --driver "Jane"
       (--style is just a label stamped into the report - the system does
       not currently persist which style was active anywhere in the logs,
       so it can't be auto-detected; pass whichever style you configured.)
    4. Open the generated data/session_feedback_<timestamp>.md, have the
       driver fill in the blank sections, and file it as the "written
       summary" the test row asks for.

Safe to run with no prior session data: it says so plainly and still writes
a mostly-blank template rather than crashing.
"""

import argparse
import json
import statistics
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

THIS_FILE = Path(__file__).resolve()
PROJECT_ROOT = THIS_FILE.parents[2]

# latency_logger.py computes its own PROJECT_ROOT as one level up from
# itself (tests/integration/latency_logger.py -> tests/), so the log
# actually lives at tests/data/, not the real project root's data/.
LATENCY_LOG = PROJECT_ROOT / "tests" / "data" / "latency_log.jsonl"
COACHING_SUMMARY = PROJECT_ROOT / "data" / "coaching_summary.json"
LAP_SUMMARY = PROJECT_ROOT / "data" / "lap_summary.json"
OUTPUT_DIR = PROJECT_ROOT / "data"


def _load_events(path):
    """Groups latency_log.jsonl lines by their correlation key, the same way
    tests/integration/analyze_latency.py does, so a voice_start entry can be
    joined back to the published/guardrail_done entry that shares its key
    (see ai_core.py's latency_key = f"{session_id}:{tag}:{lap_number}",
    passed straight through to AudioManager.play_text())."""
    events = {}
    if not path.exists():
        return events
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            events.setdefault(rec["key"], {})[rec["stage"]] = rec
    return events


def _voice_item_type(key, stages):
    """Best-effort error type for one spoken/played item. The main coaching
    loop's speech is logged under "AI Coaching Speech" generically (see
    AudioManager.play_text()), so the real type has to come from the
    published/guardrail_done entry sharing the same key. Fast-layer alerts
    (brake_now) use a standalone key with no such entry, but their own
    voice_start description already says exactly what fired."""
    for stage in ("published", "guardrail_done", "received"):
        if stage in stages:
            detail = stages[stage].get("detail")
            if detail:
                return detail

    voice_start = stages.get("voice_start", {})
    try:
        description = json.loads(voice_start.get("detail", "{}")).get("description")
    except (json.JSONDecodeError, TypeError):
        description = None

    if description and ("/" in description or "\\" in description):
        # play_error()'s WAV-fallback path (play_sound()) logs the full audio
        # file path as the description - use just the filename stem instead.
        description = Path(description).stem

    return description or "unknown"


def _summarize_voice_timeline(events):
    items = []
    for key, stages in events.items():
        if "voice_start" not in stages:
            continue
        items.append({
            "ts": stages["voice_start"]["ts"],
            "type": _voice_item_type(key, stages),
        })
    items.sort(key=lambda item: item["ts"])
    return items


def _format_gap_stats(gaps_s):
    if not gaps_s:
        return "  (fewer than 2 voice events - no gaps to measure)"
    return (f"  count={len(gaps_s)}  min={min(gaps_s):.2f}s  "
            f"median={statistics.median(gaps_s):.2f}s  max={max(gaps_s):.2f}s  "
            f"mean={statistics.mean(gaps_s):.2f}s")


def build_report(style_label, driver_name):
    events = _load_events(LATENCY_LOG)
    voice_items = _summarize_voice_timeline(events)

    lines = []
    lines.append(f"# Session Feedback Report - Test Matrix #27")
    lines.append("")
    lines.append(f"- Generated: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- Driver: {driver_name or '_(fill in)_'}")
    lines.append(f"- Coaching style active: {style_label or '_(fill in - not auto-recorded anywhere yet)_'}")
    lines.append("")
    lines.append("## Objective stats (pulled from data/latency_log.jsonl, "
                  "data/coaching_summary.json, data/lap_summary.json)")
    lines.append("")

    if not voice_items:
        lines.append("No voice events found in data/latency_log.jsonl. Either no session has run "
                      "since the log was last reset, or the log wasn't reset before this one - "
                      "see this script's docstring for the SOP.")
    else:
        span_s = voice_items[-1]["ts"] - voice_items[0]["ts"]
        gaps_s = [b["ts"] - a["ts"] for a, b in zip(voice_items, voice_items[1:])]
        type_counts = Counter(item["type"] for item in voice_items)

        lines.append(f"**Voice events:** {len(voice_items)} over a {span_s:.1f}s span")
        lines.append("")
        lines.append("**Time between consecutive voice events (\"voice frequency\"):**")
        lines.append("```")
        lines.append(_format_gap_stats(gaps_s))
        lines.append("```")
        lines.append("")
        lines.append("**Breakdown by type:**")
        for error_type, count in type_counts.most_common():
            lines.append(f"- {error_type}: {count}")
        lines.append("")

    if COACHING_SUMMARY.exists():
        try:
            summary = json.loads(COACHING_SUMMARY.read_text(encoding="utf-8"))
            results = summary.get("coaching_results", [])
            valid = sum(1 for r in results if r.get("is_valid"))
            lines.append(f"**Coaching lines generated:** {summary.get('total_errors', len(results))} "
                         f"({valid}/{len(results)} passed the guardrail)")
            lines.append("")
        except (json.JSONDecodeError, OSError):
            pass

    if LAP_SUMMARY.exists():
        try:
            lap_summary = json.loads(LAP_SUMMARY.read_text(encoding="utf-8"))
            lines.append("**Final lap debrief text shown to the driver:**")
            lines.append("> " + lap_summary.get("feedback", "").replace("\n", "\n> "))
            corners = lap_summary.get("corners_affected")
            if corners:
                lines.append(f"\nCorners affected: {', '.join(str(c) for c in corners)}")
            lines.append("")
        except (json.JSONDecodeError, OSError):
            pass

    lines.append("## Driver's subjective feedback (fill in after the session)")
    lines.append("")
    lines.append("**Voice frequency** - too much / about right / too little, and why:")
    lines.append("")
    lines.append("")
    lines.append("**Timing** - did coaching ever feel late, feel like it interrupted a corner "
                  "you were mid-way through, or arrive at a confusing moment?")
    lines.append("")
    lines.append("")
    lines.append("**Style preference** - which of aggressive / supportive / technical would "
                  "you want, and why? (Compare against the objective stats above if you drove "
                  "more than one style this session.)")
    lines.append("")
    lines.append("")
    lines.append("**Anything else** (confusing phrasing, wrong-feeling advice, general impression):")
    lines.append("")
    lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Generate a session feedback report for Test Matrix #27.")
    parser.add_argument("--style", default=None,
                        help="Which coaching style was active this session (aggressive/supportive/technical) - "
                             "not auto-recorded anywhere yet, so pass it manually.")
    parser.add_argument("--driver", default=None, help="Name of the person who drove this session.")
    args = parser.parse_args()

    report = build_report(args.style, args.driver)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"session_feedback_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    out_path.write_text(report, encoding="utf-8")

    print(report)
    print(f"\nWritten to {out_path}")
    if not LATENCY_LOG.exists():
        print(f"\nNote: {LATENCY_LOG} doesn't exist yet - see this script's docstring for the SOP "
              f"(reset the log, run a real session, then run this script).")


if __name__ == "__main__":
    sys.exit(main())
