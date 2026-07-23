import sys
import os
import json
from pathlib import Path

GRANITE_DIR = Path(__file__).resolve().parent
SRC_DIR = GRANITE_DIR.parent
PROJECT_ROOT = SRC_DIR.parent

sys.path.insert(0, str(SRC_DIR))

from Granite.prompts import build_user_prompt
from Granite.granite_client import ask_race_engineer, init_granite_model, get_ai_link_status
from Granite.guardrail import apply_guardrail
from Granite.coaching_style import get_system_prompt
from Granite.rag import retrieve, load_knowledge_base

# Global dynamic fallback lock flag for runtime circuit breaking
_RUNTIME_LLM_OK = True

# Markers that indicate Granite returned an error string instead of coaching
_ERROR_REPLY_MARKERS = (
    "Error contacting race engineer",
    "Error: Max retries reached",
    "I'm currently experiencing high demand",
)


# CORE SINGLE ERROR PROCESSING PIPELINE
def process_single_error(error, errors, system_prompt, audio_manager, force_fallback=False):
    """Processes an isolated telemetry infraction."""
    global _RUNTIME_LLM_OK

    if error.get("layer") == "fast":
        print(f"[AI] Skipping fast-layer feedback: {error.get('tag', '(no tag)')}")
        return None

    error_type = error.get("type", "generic_error")
    coaching_request = f"{error.get('message', '')} {error.get('coaching_hint', '')}".strip()

    print(f"[AI] Analyzing Infraction: [{str(error.get('severity', '?')).upper()}] {error_type} at {error.get('corner', '?')}")

    # Use time-aligned telemetry from Team 2, fallback if missing
    telemetry = error.get("telemetry") or error.get("telemetry_snapshot")
    if not telemetry:
        print(f"[AI Hint] Telemetry missing from error. Injecting testing fallback.")
        telemetry = {
            "timestamp": error.get("timestamp", 0.0),
            "lap_distance": 1500.0,
            "speed_kmh": 185.5,
            "track_pos": 0.5,
            "angle": 0.0,
            "wheel_spin": 0.05,
            "lap_time": error.get("timestamp", 12.34),
            "best_lap": 90.15,
            "throttle": 0.5,
            "brake": 0.2,
            "steer": 0.0,
            "gear": 4,
            "rpm": 9500,
            "sector_1": 31.25,
            "sector_2": 33.40,
            "sector_3": 25.50,
            "laps_remaining": 3,
            "gap_ahead": 1.5,
            "gap_behind": 2.4
        }
    else:
        print(f"[AI Success] Successfully parsed time-aligned telemetry frame provided by Team 2.")

    # Bypass HTTP overhead if quota is exhausted or connection failed at startup
    if force_fallback or not _RUNTIME_LLM_OK:
        print(f"[AI Fast-Track] LLM unavailable/exhausted. Skipping HTTP request for {error_type}.")
        answer_text = error.get("coaching_hint", "Focus on your braking points and throttle control.")
    else:
        # Execute memory-resident RAG retrieval
        knowledge_chunks = retrieve(coaching_request, top_k=3)
        knowledge_context = "\n\n".join(knowledge_chunks)

        # Package context together for the LLM
        user_prompt = build_user_prompt(
            telemetry,
            coaching_request,
            track="olethros_road_1",
            knowledge=knowledge_context,
            errors=errors,
        )

        # Query LLM with connection handling safety net
        try:
            answer_text = ask_race_engineer(system_prompt, user_prompt, error_type=error_type)
            if any(marker in str(answer_text) for marker in _ERROR_REPLY_MARKERS):
                print(f"[AI Warning] Granite LLM failed. Using fallback.")
                _RUNTIME_LLM_OK = False
                answer_text = error.get("coaching_hint", "Focus on your braking points.")
        except Exception as e:
            print(f"[AI Warning] HTTP Exception during chat: {e}")
            _RUNTIME_LLM_OK = False
            answer_text = error.get("coaching_hint", "Focus on your braking points.")

    if not answer_text:
        raise RuntimeError(f"AI Pipeline returned empty response for error type: {error_type}")

    # Execute Guardrails
    result = apply_guardrail(coaching_request, answer_text, error=error)
    if not result or not result.get("is_valid", False):
        raise ValueError(f"Guardrail check failed or output is invalid for corner: {error.get('corner')}")

    print(f"[AI Race Engineer]: {result['feedback']}")

    # Send coaching text directly to Audio Manager (no TTS file needed)
    audio_manager.play_text(
        result["feedback"],
        priority=error.get("priority", "slow"),
        interrupt=error.get("interrupt", False),
        cooldown_key=error.get("tag"),
    )

    return result


# POST-LAP SUMMARY
def generate_summary(all_results, system_prompt, force_fallback=False):
    """Compiles all isolated coaching insights into a macro lap review summary."""
    global _RUNTIME_LLM_OK

    if not all_results:
        return None, True

    if force_fallback or not _RUNTIME_LLM_OK:
        print("[AI Fast-Track Summary] Skipping LLM for Macro Summary due to empty quota.")
        return "Significant time lost in this sector. Focus on corner exits.", True

    lines = []
    for r in all_results:
        lines.append(
            f"- [{r.get('severity','?').upper()}] {r.get('error_type','?')} at {r.get('corner','?')}: {r.get('feedback','')}"
        )

    summary_prompt = f"""You are a professional race engineer giving a post-lap debrief.
Below is all coaching feedback from this lap on olethros_road_1.

{chr(10).join(lines)}

Provide a concise summary (3-5 sentences) that:
1. Identifies the single biggest area to improve
2. Highlights recurring patterns across corners
3. Gives one clear priority action for the next lap

Be direct and actionable."""

    print("\n[AI] Compiling Macro Lap Summary Review")
    try:
        summary_text = ask_race_engineer(system_prompt, summary_prompt, error_type="sector_time_loss")
        return summary_text, False
    except Exception as e:
        print(f"[AI Warning] Summary LLM failed: {e}")
        return "Significant time lost in this sector. Focus on corner exits.", True


# EXTERNAL COUPLING PIPELINE ENTRY POINT
def process_all_errors_pipeline(audio_manager, error_report_path, is_llm_available=True):
    global _RUNTIME_LLM_OK

    _RUNTIME_LLM_OK = is_llm_available

    with open(error_report_path, "r", encoding="utf-8") as f:
        report = json.load(f)
    errors = report["errors"] if isinstance(report, dict) and "errors" in report else report
    print(f"[AI] Successfully read Team 2 report file. Found {len(errors)} error snapshots.")

    # Limit to top N errors per lap to avoid overwhelming the driver
    MAX_ERRORS_PER_LAP = 3
    errors_to_process = errors[:MAX_ERRORS_PER_LAP]
    print(f"[AI] Processing top {len(errors_to_process)} errors (severity-sorted by Team 2).")

    style = "technical"
    system_prompt = get_system_prompt(style)
    all_results = []

    for error in errors_to_process:
        try:
            result = process_single_error(
                error,
                errors,
                system_prompt,
                audio_manager,
                force_fallback=(not _RUNTIME_LLM_OK)
            )
            if result:
                all_results.append(result)
        except Exception as e:
            print(f"[AI Error] Skipping individual infraction due to processing error: {e}")
            continue

    print("\n[AI] Awaiting individual localized playback clearance...")
    audio_manager.wait_until_idle(timeout=120)

    if not all_results:
        print("[AI] No infractions successfully processed for review logs.")
        return

    # Write unified coaching summary
    coaching_summary_path = PROJECT_ROOT / "data" / "coaching_summary.json"
    coaching_summary_path.parent.mkdir(parents=True, exist_ok=True)
    with open(coaching_summary_path, "w", encoding="utf-8") as f:
        json.dump({"total_errors": len(all_results), "coaching_results": all_results}, f, indent=2, ensure_ascii=False)

    # Generate post-lap summary via LLM
    try:
        summary_text, _ = generate_summary(all_results, system_prompt, force_fallback=(not _RUNTIME_LLM_OK))

        if not summary_text:
            raise RuntimeError("Failed to generate dynamic lap summary from LLM.")

        print(f"\n[AI Lap Summary Overview]:\n  {summary_text}\n")

        # Play summary via Audio Manager directly
        audio_manager.play_text(summary_text, priority="normal")

        summary_result = {
            "type": "lap_summary",
            "feedback": summary_text,
            "total_errors": len(all_results),
            "corners_affected": list({r.get("corner") for r in all_results}),
        }
        with open(PROJECT_ROOT / "data" / "lap_summary.json", "w", encoding="utf-8") as f:
            json.dump(summary_result, f, indent=2, ensure_ascii=False)
        print("[AI SUCCESS] Dynamic text overview records updated.")

    except Exception as e:
        print(f"[AI Error] Post-lap debrief failed generation checks: {e}")

    audio_manager.wait_until_idle(timeout=120)


# STANDALONE DIRECT TESTING HARNESS
if __name__ == "__main__":
    print("\n" + "="*60)
    print("[AI Core Local Test] Initiating standalone testing pipeline...")
    print("="*60)

    ERROR_REPORT_DIR = PROJECT_ROOT / "data" / "error_report"

    if not ERROR_REPORT_DIR.exists():
        print(f"Error: Target directory does not exist: {ERROR_REPORT_DIR}")
        ERROR_REPORT_DIR.mkdir(parents=True, exist_ok=True)

    json_files = list(ERROR_REPORT_DIR.glob("error_report_*.json"))
    if not json_files:
        print(f"Error: No Team 2 JSON reports found in {ERROR_REPORT_DIR}")
        print("Place a mock 'error_report_XXXX.json' file inside data/error_report/ to run isolated tests.")
        sys.exit(1)

    latest_json_path = sorted(json_files, key=os.path.getmtime)[-1]
    print(f"Found latest modification test file: {latest_json_path.name}")

    try:
        from audio_manager.audio_manager import AudioManager

        print("\n[Test Init] Warming up local workspace modules...")
        audio_manager = AudioManager()

        init_granite_model()
        load_knowledge_base()
        print("[Test Init] RAG Knowledge Base loaded in memory successfully.")

        print("[Test Init] Verifying API Server Connectivity...")
        ai_status = get_ai_link_status()
        llm_connected = ai_status.get("llm_connected", True)
        print(f"[Test Init] LLM Connectivity Status: {llm_connected} ({ai_status.get('message', '')})")

        print("\n--- Starting Core Execution Pipeline ---")
        process_all_errors_pipeline(
            audio_manager=audio_manager,
            error_report_path=latest_json_path,
            is_llm_available=llm_connected
        )

        audio_manager.shutdown()
        print("[AI Core Local Test] Standalone processing completed successfully!")

    except Exception as test_err:
        print(f"\n[Test Critical Failure]: {test_err}")
        if 'audio_manager' in locals():
            audio_manager.shutdown()
        sys.exit(1)