import os
import platform
import sys
import threading
import queue
from pathlib import Path

if platform.system() == "Darwin":
    os.environ["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] = "YES"

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

    from data_pipeline.input import InputHandler
    from data_pipeline.client import Client
    from data_pipeline.logger import CSVLogger
    from data_pipeline.cache import cache
    from analysis.live_coach import LiveCoach
    from llm.ai_core import ai_queue_consumer_loop

    print("\n" + "="*50)
    print("[Main] New Race clicked! Creating TORCS Client & Data Pipeline...")

    # Send global stop signal and cut off current audio immediately
    ai_stop_event.set()
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

    # Reset stop_event after old threads have exited
    ai_stop_event.clear()

    # Clear remaining items in shared_event_queue
    while not shared_event_queue.empty():
        try:
            shared_event_queue.get_nowait()
            shared_event_queue.task_done()
        except queue.Empty:
            break

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

def main():
    global dash
    print("[Main] Launching Dashboard GUI directly...")
    
    dash = TelemetryDashboard(
        init_callback=run_initialization,
        start_race_callback=start_race_session,
        on_game_finished_callback=handle_game_finished
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