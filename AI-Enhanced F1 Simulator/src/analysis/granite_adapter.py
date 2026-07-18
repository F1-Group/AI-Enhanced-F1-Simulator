"""Bridge from Team 2's error JSON to Team 3's Granite coaching and audio.

For each top error the flow is:
    error JSON -> Granite prompt (Team 3's build_user_prompt + RAG)
               -> coaching text  (ask_race_engineer + guardrail)
               -> AudioManager's in-memory speech queue

If Granite is not available (no API key, package missing, network down),
the adapter speaks the deterministic coaching hint through the same queue.
"""

import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GRANITE_DIR = str(PROJECT_ROOT / "Granite")
COACHING_STYLE = "supportive"

# Team 3's granite_client catches every API failure internally and RETURNS
# an error message string instead of raising, so we must recognise those
# replies or the driver would hear a raw error read aloud. Ask Team 3 to
# raise exceptions instead, then this list can go away.
_ERROR_REPLY_MARKERS = (
    "Error contacting race engineer",
    "Error: Max retries reached",
    "I'm currently experiencing high demand",
)


def _import_granite():
    if GRANITE_DIR not in sys.path:
        sys.path.insert(0, GRANITE_DIR)
    # Team 3's rag.py loads its knowledge base from the CWD-relative path
    # "knowledge_base" at import time, so it only works when imported from
    # inside Granite/. Change directory just for the first import; drop this
    # once Team 3 anchors the path to their own file location.
    cwd = os.getcwd()
    os.chdir(GRANITE_DIR)
    try:
        from coaching_style import get_system_prompt
        from granite_client import ask_race_engineer
        from guardrail import apply_guardrail
        from prompts import build_user_prompt
    finally:
        os.chdir(cwd)
    return get_system_prompt, ask_race_engineer, apply_guardrail, build_user_prompt


def _granite_coaching_text(error):
    """Ask Granite for coaching about one detected error. Raises on failure,
    returns None when Granite has no valid answer."""
    get_system_prompt, ask_race_engineer, apply_guardrail, build_user_prompt = _import_granite()

    question = (f"The analysis of my last lap says: {error['message']} "
                f"({error['coaching_hint']}) What exactly should I change next lap?")
    user_prompt = build_user_prompt(error["telemetry"], question)
    answer = ask_race_engineer(get_system_prompt(COACHING_STYLE), user_prompt)
    if any(marker in answer for marker in _ERROR_REPLY_MARKERS):
        print(f"[coach] Granite API failed: {answer[:80]}")
        return None
    is_valid, text = apply_guardrail(question, answer)
    return text if is_valid else None


def coach_error(error, manager, use_granite=True):
    """Process one in-memory event and return summary-ready AI feedback."""
    text = None
    source = "deterministic_fallback"
    if use_granite and "telemetry" in error:
        try:
            text = _granite_coaching_text(error)
            if text:
                source = "granite"
        except Exception as exc:
            print(f"[coach] Granite unavailable ({type(exc).__name__}); "
                  f"using deterministic coaching.")

    text = text or error.get("coaching_hint") or error.get("message") or "Driving alert"
    queued = manager.play_text(
        text,
        priority=error.get("priority", "slow"),
        interrupt=error.get("interrupt", False),
        cooldown_key=error.get("tag"),
    )
    return {
        "tag": error.get("tag"),
        "type": error.get("type"),
        "corner": error.get("corner"),
        "coaching_text": text,
        "feedback_source": source,
        "audio_queued": queued,
    }


def coach_report(report, manager, max_errors=2, use_granite=True):
    """Speak coaching for the top errors of one lap report.

    Picks at most one error per type so the driver hears varied feedback
    rather than the same clip type twice.
    """
    coached_types = set()
    for error in report["errors"]:
        if error["type"] in coached_types:
            continue
        coached_types.add(error["type"])

        coach_error(error, manager, use_granite=use_granite)

        if len(coached_types) >= max_errors:
            break
