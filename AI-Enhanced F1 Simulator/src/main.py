import sys
import threading
import time
import queue
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent
sys.path.insert(0, str(SRC_DIR))

from data_pipeline.input import InputHandler
from data_pipeline.client import Client
from data_pipeline.logger import CSVLogger
from data_pipeline.cache import cache
from granite.rag import load_knowledge_base
from audio_manager.audio_manager import AudioManager
from analysis.live_coach import LiveCoach
from granite.ai_core import ai_queue_consumer_loop 

def main():
    print("\n" + "="*50)
    print("[Main] Initiating early workspace warm-up...")
    audio_manager = AudioManager()
    llm_connected = True

    # AI Core and RAG Knowledge Base Initialization
    print("\n" + "="*50)
    print("[Main] Initializing AI Components & Knowledge Base...")
    
    llm_connected = False
    try:
        from granite.granite_client import init_granite_model, get_ai_link_status
        init_granite_model()
        load_knowledge_base()
        
        ai_status = get_ai_link_status()
        llm_connected = ai_status.get("llm_connected", True)
        
        if not llm_connected:
            # Catch Granite connection failure (e.g., 403 quota exceeded) without interrupting the system.
            print(f"[AI System Warning] {ai_status.get('message')}")
        else:
            print(f"[AI System Info] {ai_status.get('message')}")

    except Exception as e:
        print(f"[Main CRITICAL] AI Architecture Initialization failed: {e}")
        audio_manager.shutdown()
        sys.exit(1)
        
    print("="*50 + "\n")
    

    # Start the data streaming pipeline
    handler = InputHandler()
    logger = CSVLogger()
    client = Client(handler, logger, cache)
    print("[Main] Starting Data Pipeline connection thread...")
    client_thread = threading.Thread(target=client.start, daemon=True)
    client_thread.start()
    time.sleep(0.5)

    # Create the in-memory core IPC communication pipeline
    shared_event_queue = queue.Queue()
    ai_stop_event = threading.Event()

    # Launch the AI background consumer thread
    print("[Main] Launching AI Queue Consumer Thread...")
    ai_thread = threading.Thread(
        target=ai_queue_consumer_loop, 
        args=(shared_event_queue, audio_manager, ai_stop_event, llm_connected), 
        daemon=True
    )
    ai_thread.start()

    # Instantiate LiveCoach and hand over control of the analysis process
    try:
        coach = LiveCoach(
            manager=audio_manager, 
            event_output_queue=shared_event_queue, 
            use_granite=llm_connected,
            cache=cache
        )
        
        print("[Main] Handing over to LiveCoach for streaming and error detection...")
        # Blocking execution: enter the main file tracking and error detection loop.
        coach.start_coaching_loop(wait_timeout=120.0, idle_timeout_s=10.0)
        
    except KeyboardInterrupt:
        print("\n[Main] Keyboard interrupt. Lost connection to TORCS.")
    except Exception as e:
        print(f"[Main] Unexpected error in orchestration loop: {e}")
    finally:

        # Wait for the AI thread to finish any remaining tasks and the final summary.
        if 'ai_thread' in locals() and ai_thread.is_alive():
            print("\n[Main] Awaiting AI Thread to finish macro summary report (generating JSON)...")
            # Allow enough time for the LLM to generate the report and write it to disk.
            ai_thread.join(timeout=30)

        print("\n" + "="*50)
        print("[Main] Initiating comprehensive system shutdown...")

        # Signal remaining background threads to force-stop after the Summary is successfully written.
        print("[Main] Signaling background threads to halt...")
        ai_stop_event.set()

        print("[Main] Flushing shared event queue...")
        while not shared_event_queue.empty():
            try:
                shared_event_queue.get_nowait()
            except queue.Empty:
                break

        # Stop data streaming pipeline and network connections
        print("[Main] Stopping data pipeline client and connections...")
        try:
            client.stop()
        except Exception as e:
            print(f"[Main Warning] Error stopping client: {e}")

        # Release audio manager resources
        print("[Main] Releasing AI Audio Manager resources...")
        try:
            audio_manager.shutdown()
        except Exception as e:
            print(f"[Main Warning] Error shutting down audio manager: {e}")

        print("[Main] All sub-systems halted successfully.")
        print("="*50 + "\n")
        sys.exit(0)

if __name__ == '__main__':
    main()