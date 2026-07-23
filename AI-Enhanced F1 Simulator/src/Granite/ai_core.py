import sys
import json
from pathlib import Path
import queue

GRANITE_DIR = Path(__file__).resolve().parent
SRC_DIR = GRANITE_DIR.parent
PROJECT_ROOT = SRC_DIR.parent

sys.path.insert(0, str(SRC_DIR))

from granite.prompts import build_user_prompt
from granite.granite_client import ask_race_engineer
from granite.guardrail import apply_guardrail
from granite.coaching_style import get_system_prompt
from granite.rag import retrieve
from granite.granite_client import get_fallback_text

_RUNTIME_LLM_OK = True
_ERROR_REPLY_MARKERS = (
    "Error contacting race engineer",
    "Error: Max retries reached",
    "I'm currently experiencing high demand",
)

def process_single_error(error, system_prompt, audio_manager, force_fallback=False):
    """Processes an isolated telemetry infraction."""
    global _RUNTIME_LLM_OK
    
    error_type = error.get("type") or error.get("tag") or "generic_error"
    coaching_request = f"{error.get('message', '')} {error.get('coaching_hint', '')}".strip()

    # Fast Layer Event Interception
    EMERGENCY_TYPES = ["off_track", "collision_warning", "brake_now"]
    if error_type in EMERGENCY_TYPES or error.get("priority") == "high":
        return

    # Only enable the fallback script for slow-layer review when disconnected.
    if force_fallback or not _RUNTIME_LLM_OK:
        answer_text = get_fallback_text(error_type)
    else:
        # Execute memory-resident RAG retrieval
        knowledge_chunks = retrieve(coaching_request, top_k=2)
        knowledge_context = "\n".join(knowledge_chunks)
        # Package context together for the LLM
        user_prompt = build_user_prompt(
            error.get("telemetry", {}),
            coaching_request,
            track="olethros_road_1",
            knowledge=knowledge_context,
            errors=[]
        )

        try:
            answer_text = ask_race_engineer(system_prompt, user_prompt, error_type=error_type)
        except Exception:
            answer_text = get_fallback_text(error_type)

    # Execute Guardrails
    result = apply_guardrail(coaching_request, answer_text, error=error)
    if result and result.get("is_valid", False):
        feedback_text = result.get("feedback", answer_text)
        print(f"\n[Slow Layer Feedback]: {feedback_text}")
        # Broadcast to the driver in real time.
        audio_manager.play_text(
            feedback_text,
            priority="normal",
            cooldown_key=f"{error_type}_{error.get('corner')}"
        )
    return result

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


def ai_queue_consumer_loop(event_queue, audio_manager, stop_event, is_llm_available=True):
    """
    Asynchronously consumes error events from the queue. Receiving 'None' indicates 
    that LiveCoach has finished the lap with no new events, which triggers the final summary.
    """
    global _RUNTIME_LLM_OK
    _RUNTIME_LLM_OK = is_llm_available
    
    system_prompt = get_system_prompt("technical")
    all_results = []
    
    print("[AI Thread] Async Consumer active. Listening to shared_event_queue...")

    # Use a non-blocking timeout to balance immediate stopping and normal lap completion.
    while not stop_event.is_set():
        try:
            # Add a timeout so the thread periodically returns to the while condition to check stop_event.
            event = event_queue.get(block=True, timeout=1.0)
        except queue.Empty:
            continue
        
        # Receiving the poison pill 'None' indicates the upstream LiveCoach 
        # has finished broadcasting successfully, breaking the loop to generate the final summary.
        if event is None:
            print("[AI Thread] Poison pill 'None' received. Preparing lap summary...")
            event_queue.task_done()
            break
            
        try:
            result = process_single_error(
                event, system_prompt, audio_manager, force_fallback=(not _RUNTIME_LLM_OK)
            )
            if result:
                merged = dict(result)
                merged["error_type"] = event.get("type", "generic_error")
                merged["corner"] = event.get("corner", "unknown")
                merged["severity"] = event.get("severity", "normal")
                all_results.append(merged)
        except Exception as e:
            print(f"[AI Thread Warning] Infraction processing failed: {e}")
        finally:
            event_queue.task_done()

    # Cleanup block for forced exits
    if stop_event.is_set():
        print("[AI Thread] Stop signal detected! Flushing remaining events in queue...")
        while not event_queue.empty():
            try:
                event_queue.get_nowait()
                event_queue.task_done()
            except queue.Empty:
                break
        print("[AI Thread] Queue flushed. Thread exiting immediately without summary.")
        return 


    # Macro analysis report generation and storage upon lap completion
    if not all_results:
        print("[AI Thread] No infractions recorded. Exiting cleanly.")
        return

    print(f"[AI Thread] Draining audio play queue, awaiting idle state...")
    audio_manager.wait_until_idle(timeout=60)

    # Store local records locally
    coaching_summary_path = PROJECT_ROOT / "data" / "coaching_summary.json"
    coaching_summary_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(coaching_summary_path, "w", encoding="utf-8") as f:
            json.dump({"total_errors": len(all_results), "coaching_results": all_results}, f, indent=2, ensure_ascii=False)
        print(f"[AI SUCCESS] Infraction logs saved to {coaching_summary_path.name}")
    except Exception as e:
        print(f"[AI Error] Failed to write coaching_summary: {e}")

    # Generate and play the macro analysis
    try:
        summary_text, _ = generate_summary(all_results, system_prompt, force_fallback=(not _RUNTIME_LLM_OK))
        if summary_text:
            print(f"\n[AI Lap Summary Overview]: {summary_text}\n")
            audio_manager.play_text(summary_text, priority="normal")

            summary_result = {
                "type": "lap_summary", "feedback": summary_text,
                "total_errors": len(all_results), "corners_affected": list({r.get("corner") for r in all_results})
            }
            
            with open(PROJECT_ROOT / "data" / "lap_summary.json", "w", encoding="utf-8") as f:
                json.dump(summary_result, f, indent=2, ensure_ascii=False)
            print("[AI SUCCESS] Summary logs saved to data/lap_summary.json.")
    except Exception as e:
        print(f"[AI Error] Macro debrief generation failed: {e}")
        
    audio_manager.wait_until_idle(timeout=60)