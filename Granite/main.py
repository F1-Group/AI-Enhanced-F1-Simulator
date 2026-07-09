import sys
import os
import json
import glob
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from prompts import build_user_prompt
from granite_client import ask_race_engineer
from guardrail import apply_guardrail
from coaching_style import get_system_prompt
from rag import retrieve, load_knowledge_base
from tts import generate_wav
from audio_manager.audio_manager import AudioManager

# ---------------------------------------------------------------------------
# Path setup (adopted from file 1's PROJECT_ROOT approach — cwd-independent)
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ERROR_REPORT_DIR_PATH = PROJECT_ROOT / "data" / "error_report"
MOCK_ERROR_PATH = Path(__file__).resolve().parent / "mock" / "error_template.json"
OUTPUT_JSON_PATH = PROJECT_ROOT / "data" / "latest_coaching.json"

# Load knowledge base on startup
load_knowledge_base()

# Initialise Audio Manager
audio_manager = AudioManager()


def load_errors(error_report_path=None):
    """
    Load error objects from Team 2's error report JSON.
    Priority:
      1. Explicit path passed in
      2. Latest real report found under data/error_report/
      3. Local mock/error_template.json
      4. Empty list
    Handles both raw-list schema and Team2 full schema {"source": ..., "errors": [...]}.
    """

    def _unwrap(report):
        if isinstance(report, dict) and "errors" in report:
            return report["errors"]
        return report

    # 1. Explicit path
    if error_report_path and os.path.exists(error_report_path):
        with open(error_report_path, "r") as f:
            report = json.load(f)
        errors = _unwrap(report)
        print(f"Loaded {len(errors)} errors from {error_report_path}")
        return errors

    # 2. Latest real report from Team 2
    reports = sorted(ERROR_REPORT_DIR_PATH.glob("error_report_*.json"))
    if reports:
        latest = reports[-1]
        with open(latest, "r") as f:
            report = json.load(f)
        errors = _unwrap(report)
        print(f"Loaded {len(errors)} errors from {latest}")
        return errors

    # 3. Local mock file
    if MOCK_ERROR_PATH.exists():
        with open(MOCK_ERROR_PATH, "r") as f:
            report = json.load(f)
        errors = _unwrap(report)
        print(f"Loaded {len(errors)} mock errors from {MOCK_ERROR_PATH}")
        return errors

    # 4. Nothing found
    print("No error report or mock file found.")
    return []


# Fake telemetry data (aligned with team schema)
fake_telemetry = {
    "timestamp": 45.3,
    "lap_distance": 1820.5,
    "speed_kmh": 212.4,
    "track_pos": 0.15,
    "angle": 0.03,
    "wheel_spin": 0.12,
    "lap_time": 88.3,
    "best_lap": 86.1,
    "throttle": 0.68,
    "brake": 0.45,
    "steer": -0.12,
    "gear": 5,
    "rpm": 11200,
    "sector_1": 28.3,
    "sector_2": 35.1,
    "sector_3": 24.9,
    "laps_remaining": 18,
    "gap_ahead": 2.1,
    "gap_behind": 4.2
}

# Load errors (real or mock)
errors = load_errors()

# Coaching style: aggressive / supportive / technical
style = "technical"
system_prompt = get_system_prompt(style)

# Process each error from Team 2's report
for error in errors:

    # Skip fast layer errors — handled directly by Team 2's Audio Manager
    if error.get("layer") == "fast":
        print(f"\nSkipping fast layer error: {error['tag']}")
        continue

    # Build coaching request from error report
    coaching_request = f"{error['message']} {error['coaching_hint']}"

    print(f"\nError: [{error['severity'].upper()}] {error['type']} at {error['corner']}")
    print(f"Coaching request: {coaching_request}")

    # Retrieve relevant knowledge from RAG
    knowledge_chunks = retrieve(coaching_request, top_k=3)
    knowledge_context = "\n\n".join(knowledge_chunks)

    # Build prompt with RAG knowledge + errors from Analysis team
    user_prompt = build_user_prompt(
        fake_telemetry,
        coaching_request,
        track="olethros_road_1",
        knowledge=knowledge_context,
        errors=errors
    )

    answer = ask_race_engineer(system_prompt, user_prompt)
    result = apply_guardrail(coaching_request, answer, error=error)

    print(f"Race engineer: {result['feedback']}")
    print(f"Output JSON: {result}")

    if result.get("is_valid", False):
        # TTS — convert Granite's response to .wav
        wav_path = generate_wav(result["feedback"])
        print(f"TTS saved: {wav_path}")

        # Send .wav to Team 2's Audio Manager and wait for playback
        audio_manager.stop_all()
        audio_manager._clear_queue()
        audio_manager.play_sound(wav_path, priority="slow")
        print("Audio queued for playback")
        audio_manager._audio_queue.join()

        # Save output JSON for Team 4 (Frontend)
        os.makedirs(OUTPUT_JSON_PATH.parent, exist_ok=True)
        with open(OUTPUT_JSON_PATH, "w") as f:
            json.dump(result, f, indent=2)
        print(f"Coaching output saved to {OUTPUT_JSON_PATH}")

# Shutdown Audio Manager cleanly
audio_manager.shutdown()
print("\nDone.")
