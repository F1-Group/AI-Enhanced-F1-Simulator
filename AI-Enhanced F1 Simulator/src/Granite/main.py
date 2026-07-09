import sys
import os
import json
import csv

sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from prompts import build_user_prompt
from granite_client import ask_race_engineer, get_fallback_wav
from guardrail import apply_guardrail
from coaching_style import get_system_prompt
from rag import retrieve, load_knowledge_base
from tts import generate_wav
from audio_manager.audio_manager import AudioManager
from pathlib import Path

PROJECT_ROOT          = Path(__file__).resolve().parent.parent.parent
ERROR_REPORT_DIR_PATH = PROJECT_ROOT / "data" / "error_report"
GRANITE_DIR           = Path(__file__).parent

load_knowledge_base()
audio_manager = AudioManager()


def load_errors(error_report_path=None):
    def _load_json(path):
        with open(path, "r") as f:
            report = json.load(f)
        return report["errors"] if isinstance(report, dict) and "errors" in report else report

    def _load_csv(path):
        errors = []
        with open(path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                errors.append(row)
        return errors

    if error_report_path is None:
        json_reports = sorted(ERROR_REPORT_DIR_PATH.glob("error_report_*.json"))
        csv_reports  = sorted(ERROR_REPORT_DIR_PATH.glob("error_report_*.csv"))
        all_reports  = sorted(
            [(p, "json") for p in json_reports] + [(p, "csv") for p in csv_reports],
            key=lambda x: x[0].name
        )
        if all_reports:
            error_report_path, detected_type = all_reports[-1]
        else:
            detected_type = None
    else:
        error_report_path = Path(error_report_path)
        detected_type     = error_report_path.suffix.lstrip(".")

    if error_report_path and os.path.exists(error_report_path):
        errors = _load_json(error_report_path) if detected_type == "json" else _load_csv(error_report_path)
        print(f"Loaded {len(errors)} errors from {error_report_path}")
        return errors

    mock_path = Path(__file__).parent / "mock" / "error_template.json"
    if mock_path.exists():
        errors = _load_json(mock_path)
        print(f"Loaded {len(errors)} mock errors from {mock_path}")
        return errors

    print("No error report or mock file found.")
    return []


def generate_summary(all_results, system_prompt):
    if not all_results:
        return None, False

    lines = []
    for r in all_results:
        lines.append(
            f"- [{r.get('severity','?').upper()}] "
            f"{r.get('error_type','?')} at {r.get('corner','?')}: "
            f"{r.get('feedback','')}"
        )

    summary_prompt = f"""You are a professional race engineer giving a post-lap debrief.
Below is all coaching feedback from this lap on olethros_road_1.

{chr(10).join(lines)}

Provide a concise summary (3-5 sentences) that:
1. Identifies the single biggest area to improve
2. Highlights recurring patterns across corners
3. Gives one clear priority action for the next lap

Be direct and actionable."""

    print("\n" + "="*60)
    print("GENERATING LAP SUMMARY...")
    print("="*60)

    return ask_race_engineer(system_prompt, summary_prompt, error_type="sector_time_loss")


fake_telemetry = {
    "timestamp": 45.3, "lap_distance": 1820.5, "speed_kmh": 212.4,
    "track_pos": 0.15, "angle": 0.03,          "wheel_spin": 0.12,
    "lap_time":  88.3, "best_lap": 86.1,        "throttle": 0.68,
    "brake":     0.45, "steer": -0.12,           "gear": 5,
    "rpm":       11200,"sector_1": 28.3,         "sector_2": 35.1,
    "sector_3":  24.9, "laps_remaining": 18,     "gap_ahead": 2.1,
    "gap_behind": 4.2,
}

errors        = load_errors()
style         = "technical"
system_prompt = get_system_prompt(style)
all_results   = []

for error in errors:
    if error.get("layer") == "fast":
        print(f"\nSkipping fast layer error: {error.get('tag', '(no tag)')}")
        continue

    error_type       = error.get("type", "")
    coaching_request = f"{error.get('message', '')} {error.get('coaching_hint', '')}".strip()

    print(f"\nError: [{str(error.get('severity', '?')).upper()}] "
          f"{error_type} at {error.get('corner', '?')}")
    print(f"Coaching request: {coaching_request}")

    knowledge_chunks  = retrieve(coaching_request, top_k=3)
    knowledge_context = "\n\n".join(knowledge_chunks)

    user_prompt = build_user_prompt(
        fake_telemetry, coaching_request,
        track="olethros_road_1",
        knowledge=knowledge_context,
        errors=errors,
    )

    answer, is_fallback = ask_race_engineer(
        system_prompt, user_prompt, error_type=error_type
    )

    result = apply_guardrail(coaching_request, answer, error=error)
    print(f"{'[FALLBACK] ' if is_fallback else ''}Race engineer: {result['feedback']}")

    if result.get("is_valid", False):
        all_results.append(result)

        if is_fallback:
            wav_path = get_fallback_wav(error_type)
            if wav_path:
                print(f"[Fallback] Playing pre-recorded: {wav_path}")
                audio_manager.play_sound(wav_path, priority="slow", interrupt=False)
            else:
                print("[Fallback] No WAV found, skipping audio.")
        else:
            wav_path = generate_wav(result["feedback"])
            wav_abs  = str(GRANITE_DIR / wav_path)
            print(f"TTS saved: {wav_abs}")
            audio_manager.play_sound(wav_abs, priority="slow", interrupt=False)

    output_dir = PROJECT_ROOT / "data"
    os.makedirs(output_dir, exist_ok=True)
    with open(output_dir / "latest_coaching.json", "w") as f:
        json.dump(result, f, indent=2)

print("\nWaiting for all coaching audio to finish...")
audio_manager.wait_until_idle(timeout=300)

if all_results:
    summary_text, is_fallback = generate_summary(all_results, system_prompt)

    if summary_text:
        print(f"\nLap Summary:\n{summary_text}")

        if is_fallback:
            wav_path = get_fallback_wav("sector_time_loss")
            if wav_path:
                audio_manager.play_sound(wav_path, priority="normal")
        else:
            summary_wav     = generate_wav(summary_text, filename="lap_summary.wav")
            summary_wav_abs = str(GRANITE_DIR / summary_wav)
            audio_manager.play_sound(summary_wav_abs, priority="normal")

        summary_result = {
            "type":             "lap_summary",
            "feedback":         summary_text,
            "is_fallback":      is_fallback,
            "total_errors":     len(all_results),
            "corners_affected": list({r.get("corner") for r in all_results}),
        }
        with open(PROJECT_ROOT / "data" / "lap_summary.json", "w") as f:
            json.dump(summary_result, f, indent=2)
        print("Lap summary saved to data/lap_summary.json")

        audio_manager.wait_until_idle(timeout=120)

audio_manager.shutdown()
print("\nDone.")
