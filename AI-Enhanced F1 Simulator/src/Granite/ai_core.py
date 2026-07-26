import sys
import json
import time
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

<<<<<<< HEAD
_RUNTIME_LLM_OK = True

def process_single_error(error, system_prompt, audio_manager, force_fallback=False, feedback_callback=None):
=======
# Circuit breaker for the LLM: a failed call trips it, and later calls skip
# straight to fallback for _LLM_COOLDOWN_SECONDS instead of paying the full
# retry/timeout cost again. After the cooldown, the next call is allowed to
# probe the LLM again so a transient outage self-heals instead of forcing
# fallback text for the rest of the session.
_LLM_COOLDOWN_SECONDS = 10
_llm_retry_at = 0.0

_ERROR_REPLY_MARKERS = (
    "Error contacting race engineer",
    "Error: Max retries reached",
    "I'm currently experiencing high demand",
)


def _llm_breaker_is_open():
    """True while the breaker is tripped: LLM calls should be skipped."""
    return time.time() < _llm_retry_at


def _trip_llm_breaker():
    global _llm_retry_at
    _llm_retry_at = time.time() + _LLM_COOLDOWN_SECONDS


def _reset_llm_breaker():
    global _llm_retry_at
    _llm_retry_at = 0.0

def process_single_error(error, system_prompt, audio_manager, force_fallback=False):
>>>>>>> 9ff5891 (Update granite)
    """Processes an isolated telemetry infraction."""
    error_type = error.get("type") or error.get("tag") or "generic_error"
    coaching_request = f"{error.get('message', '')} {error.get('coaching_hint', '')}".strip()

    # Fast Layer Event Interception
    if error.get("priority") == "high" or error_type in ["brake_now", "off_track"]:
        fast_text = error.get("message") or error.get("coaching_hint") or f"URGENT: {error_type.upper()}"

        if feedback_callback:
            feedback_callback(fast_text, layer="fast")

        audio_manager.play_text(
            fast_text,
            priority="high",
            cooldown_key=f"fast_{error_type}_{error.get('corner', 'general')}"
        )

        return None

    # Only enable the fallback script for slow-layer review when disconnected.
    if force_fallback or _llm_breaker_is_open():
        answer_text = get_fallback_text(error_type)
    else:
        # Execute memory-resident RAG retrieval
        knowledge_chunks = retrieve(coaching_request, top_k=2)
        knowledge_context = "\n".join(knowledge_chunks)
        # Package context together for the LLM
        user_prompt = build_user_prompt(
            error.get("telemetry", {}),
            coaching_request,
            knowledge=knowledge_context,
            errors=[]
        )

        try:
            answer_text = ask_race_engineer(system_prompt, user_prompt)
            _reset_llm_breaker()
        except Exception:
            _trip_llm_breaker()
            answer_text = get_fallback_text(error_type)

    # Execute Guardrails & Slow Layer Feedback
    result = apply_guardrail(coaching_request, answer_text, error=error)
    if result and result.get("is_valid", False):
        feedback_text = result.get("feedback", answer_text)
        print(f"\n[Slow Layer Feedback]: {feedback_text}")

        if feedback_callback:
            feedback_callback(feedback_text, layer="slow")

        # Broadcast to the driver in real time.
        audio_manager.play_text(
            feedback_text,
            priority="normal",
            cooldown_key=f"{error_type}_{error.get('corner')}"
        )
    return result

def generate_summary(all_results, system_prompt, force_fallback=False):
    """Compiles all isolated coaching insights into a macro lap review summary."""
    if not all_results:
        return None, True

    if force_fallback or _llm_breaker_is_open():
        print("[AI Fast-Track Summary] Skipping LLM for Macro Summary due to empty quota.")
        return "Significant time lost in this sector. Focus on corner exits.", True

    lines = []
    for r in all_results:
        lines.append(
            f"- [{r.get('severity','?').upper()}] {r.get('error_type','?')} at {r.get('corner','?')}: {r.get('feedback','')}"
        )

    summary_prompt = f"""You are a professional race engineer giving a post-lap debrief.
    Below is all coaching feedback from this lap.

    {chr(10).join(lines)}

    Provide a concise summary (3-5 sentences) that:
    1. Identifies the single biggest area to improve
    2. Highlights recurring patterns across corners
    3. Gives one clear priority action for the next lap

    Be direct and actionable."""

    print("\n[AI] Compiling Macro Lap Summary Review")

    try:
        summary_text = ask_race_engineer(system_prompt, summary_prompt)
        _reset_llm_breaker()
        return summary_text, False
    except Exception as e:
        print(f"[AI Warning] Summary LLM failed: {e}")
        _trip_llm_breaker()
        return "Significant time lost in this sector. Focus on corner exits.", True


<<<<<<< HEAD
def ai_queue_consumer_loop(event_queue, audio_manager, stop_event, is_llm_available=True, feedback_callback=None, style="supportive"):
    global _RUNTIME_LLM_OK
    _RUNTIME_LLM_OK = is_llm_available
    
    system_prompt = get_system_prompt(style)
=======
def ai_queue_consumer_loop(event_queue, audio_manager, stop_event, is_llm_available=True):
    """
    Asynchronously consumes error events from the queue. Receiving 'None' indicates
    that LiveCoach has finished the lap with no new events, which triggers the final summary.
    """
    if is_llm_available:
        _reset_llm_breaker()
    else:
        _trip_llm_breaker()

    system_prompt = get_system_prompt("aggressive") # Coaching style (Supportive, aggressive, technical)
>>>>>>> 9ff5891 (Update granite)
    all_results = []
    
    print("[AI Thread] Async Consumer active. Listening to shared_event_queue...")

    while not stop_event.is_set():
        try:
            event = event_queue.get(block=True, timeout=1.0)
        except queue.Empty:
            continue

        if event is None:
            print("[AI Thread] Poison pill 'None' received. Race finished cleanly.")
            event_queue.task_done()
            break
            
        try:
<<<<<<< HEAD
            result = process_single_error(
                event, 
                system_prompt,
                audio_manager,
                force_fallback=(not _RUNTIME_LLM_OK),
                feedback_callback=feedback_callback
            )
=======
            result = process_single_error(event, system_prompt, audio_manager)
>>>>>>> 9ff5891 (Update granite)
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

    if audio_manager:
        if hasattr(audio_manager, "stop_all"):
            audio_manager.stop_all()
        elif hasattr(audio_manager, "clear"):
            audio_manager.clear()

    if stop_event.is_set():
        print("[AI Thread] Force stop signal detected. Aborting summary generation.")
        while not event_queue.empty():
            try:
                event_queue.get_nowait()
                event_queue.task_done()
            except queue.Empty:
                break
        return 

    if not all_results:
        print("[AI Thread] No infractions recorded. Exiting cleanly.")
        with open(PROJECT_ROOT / "data" / "lap_summary.json", "w", encoding="utf-8") as f:
            json.dump({"type": "lap_summary", "feedback": "Clean session with no major infractions recorded!"}, f, indent=2, ensure_ascii=False)
        return

    coaching_summary_path = PROJECT_ROOT / "data" / "coaching_summary.json"
    coaching_summary_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(coaching_summary_path, "w", encoding="utf-8") as f:
            json.dump({"total_errors": len(all_results), "coaching_results": all_results}, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[AI Error] Failed to write coaching_summary: {e}")

    try:
<<<<<<< HEAD
        print("[AI Thread] Generating final post-race summary (Silent mode)...")
        summary_text, _ = generate_summary(all_results, system_prompt, force_fallback=(not _RUNTIME_LLM_OK))
=======
        summary_text, _ = generate_summary(all_results, system_prompt)
>>>>>>> 9ff5891 (Update granite)
        if summary_text:
            summary_result = {
                "type": "lap_summary", 
                "feedback": summary_text,
                "total_errors": len(all_results), 
                "corners_affected": list({r.get("corner") for r in all_results})
            }
            
            with open(PROJECT_ROOT / "data" / "lap_summary.json", "w", encoding="utf-8") as f:
                json.dump(summary_result, f, indent=2, ensure_ascii=False)
            print("[AI SUCCESS] Summary successfully generated and saved to data/lap_summary.json.")
    except Exception as e:
        print(f"[AI Error] Macro debrief generation failed: {e}")