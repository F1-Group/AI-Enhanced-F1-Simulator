import os
import platform
import sys
import threading
import queue
from pathlib import Path
import multiprocessing as mp

if platform.system() == "Darwin":
    os.environ["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] = "YES"

if platform.system() == "Linux":
    try:
        mp.set_start_method('spawn', force=True)
    except RuntimeError:
        pass

SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent
sys.path.insert(0, str(SRC_DIR))

from ui.dashboard import TelemetryDashboard

shared_event_queue = queue.Queue()
ai_stop_event = threading.Event()
audio_manager = None
client = None
dash = None
ai_consumer_thread = None
coach_thread = None

def run_initialization():
    """Background initialization for AI and RAG after clicking START SYSTEM"""
    global audio_manager
    print("\n" + "="*50)
    print("[Main] User clicked Start. Beginning AI Core & RAG Initialization...")
    
    from audio_manager.audio_manager import AudioManager
    from llm.rag import load_knowledge_base
    from llm.llm_client import init_granite_model, get_ai_link_status

    if audio_manager is None:
        audio_manager = AudioManager()

    llm_connected = False
    try:
        init_granite_model()
        load_knowledge_base()
        
        ai_status = get_ai_link_status()
        llm_connected = ai_status.get("llm_connected", True)
        msg = ai_status.get("message", "")

        return True, llm_connected, msg

    except Exception as e:
        print(f"[Main CRITICAL] AI Initialization failed: {e}")
        return False, False, str(e)


def start_race_session(llm_connected, style="supportive"):
    global client, audio_manager, dash, ai_consumer_thread, coach_thread
    global shared_event_queue, ai_stop_event

    from data_pipeline.input import InputHandler
    from data_pipeline.client import Client
    from data_pipeline.logger import CSVLogger
    from data_pipeline.cache import cache
    from analysis.live_coach import LiveCoach
    from llm.ai_core import ai_queue_consumer_loop

    print("\n" + "="*50)
    print("[Main] New Race clicked! Creating TORCS Client & Data Pipeline...")

    # Signal the OLD session's threads to stop, via the OLD stop_event object
    # (kept alive by this local reference even after the global is swapped
    # below). ai_queue_consumer_loop's ask_race_engineer() call has no HTTP
    # timeout and can block for tens of seconds, so the old AI consumer
    # thread may still be mid-call when the 2s join below gives up on it.
    old_stop_event = ai_stop_event
    old_stop_event.set()
    if audio_manager:
        if hasattr(audio_manager, "stop_all"):
            audio_manager.stop_all()

    # Clean up old AI Consumer Thread
    if ai_consumer_thread and ai_consumer_thread.is_alive():
        print("[Main Warning] Stopping old AI Consumer thread...")
        ai_consumer_thread.join(timeout=2.0)

    # Clean up old Coach Thread
    if coach_thread and coach_thread.is_alive():
        print("[Main Warning] Stopping old Coach thread...")
        coach_thread.join(timeout=2.0)

    # Fresh stop_event + queue for this session, instead of clearing/reusing
    # the old ones. Reusing a single global Event was a race: if an old
    # thread was still blocked past the 2s join above, clearing the shared
    # flag here would un-signal its stop request behind its back, and it
    # would keep polling shared_event_queue and speaking through the shared
    # audio_manager indefinitely. A brand new Event/Queue per session means
    # any straggling old thread keeps seeing *its own* stop_event as set
    # (never touched again by this or future sessions) and can never publish
    # into - or be fed by - the new session's queue.
    ai_stop_event = threading.Event()
    shared_event_queue = queue.Queue()

    if client:
        try:
            client.stop()
        except Exception:
            pass
        client = None

    # Start the data streaming pipeline
    handler = InputHandler()
    logger = CSVLogger()
    client = Client(handler, logger, cache)
    threading.Thread(target=client.start, daemon=True).start()

    def on_coach_feedback(text, layer="slow"):
        if dash:
            dash.update_coach_feedback(text, layer=layer)

    # Start new AI Consumer thread
    ai_consumer_thread = threading.Thread(
        target=ai_queue_consumer_loop, 
        args=(shared_event_queue, audio_manager, ai_stop_event, llm_connected, on_coach_feedback, style), 
        daemon=True
    )
    ai_consumer_thread.start()

    # Start new Coach Thread
    coach = LiveCoach(
        manager=audio_manager, 
        event_output_queue=shared_event_queue, 
        use_granite=llm_connected,
        cache=cache
    )
    # show "which turn am I in" live without doing its own pandas analysis
    cache.set_corners(coach.corners, coach.track_length)
    
    coach_thread = threading.Thread(
        target=coach.start_coaching_loop,
        kwargs={"wait_timeout": 120.0, "idle_timeout_s": 10.0, "stop_event": ai_stop_event},
        daemon=True
    )
    coach_thread.start()

def handle_game_finished():
    """Called when the Dashboard detects that the TORCS race has ended"""
    print("[Main] TORCS finished. Cleaning up live coaching & generating post-race summary...")
    
    # Cut off any unfinished audio playback from the race immediately
    if audio_manager and hasattr(audio_manager, "stop_all"):
        audio_manager.stop_all()

    # Clear remaining live audio messages in the queue to prevent old audio from playing
    while not shared_event_queue.empty():
        try:
            shared_event_queue.get_nowait()
            shared_event_queue.task_done()
        except queue.Empty:
            break

    # Send a poison pill (None) to let the AI Thread start generating the lap summary
    shared_event_queue.put(None)

def stop_race_session():
    """Called when user presses BACK on Dashboard to force stop the race"""
    global client, audio_manager, ai_stop_event, shared_event_queue
    print("[Main] Stop race requested via Back button. Stopping threads and client...")

    ai_stop_event.set()

    if audio_manager and hasattr(audio_manager, "stop_all"):
        audio_manager.stop_all()

    while not shared_event_queue.empty():
        try:
            shared_event_queue.get_nowait()
            shared_event_queue.task_done()
        except queue.Empty:
            break

    if client:
        try:
            client.stop()
        except Exception as e:
            print(f"[Main Warning] Error stopping client on back: {e}")
        client = None

def main():
    global dash
    print("[Main] Launching Dashboard GUI directly...")
    
    dash = TelemetryDashboard(
        init_callback=run_initialization,
        start_race_callback=start_race_session,
        on_game_finished_callback=handle_game_finished,
        stop_race_callback=stop_race_session
    )

    try:
        dash.run()
    except KeyboardInterrupt:
        print("\n[Main] Keyboard interrupt detected.")
    finally:
        print("\n" + "="*50)
        print("[Main] Shutting down system...")
        
        # Send stop signal to background Consumer / Coach threads
        ai_stop_event.set()

        # Stop TORCS Client
        if client:
            try:
                client.stop()
            except Exception as e:
                print(f"[Main Warning] Error stopping client: {e}")

        # Stop audio manager
        if audio_manager:
            try:
                audio_manager.shutdown()
            except Exception as e:
                print(f"[Main Warning] Error shutting down audio manager: {e}")

        # Ensure the GUI window is fully destroyed
        if dash and hasattr(dash, "root") and dash.root:
            try:
                dash.root.destroy()
            except Exception:
                pass

        print("[Main] System halted cleanly.")

if __name__ == '__main__':
    main()