import sys
import threading
import time
import subprocess
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SRC_DIR.parent
sys.path.insert(0, str(SRC_DIR))
ERROR_REPORT_DIR = PROJECT_ROOT / "data" / "error_report"

from data_pipeline.input import InputHandler
from data_pipeline.client import Client
from data_pipeline.logger import CSVLogger
from data_pipeline.cache import cache, GameStatus
from granite.rag import load_knowledge_base
from audio_manager.audio_manager import AudioManager
from granite.ai_core import process_all_errors_pipeline  


# BACKGROUND FILE WATCHER THREAD
def ai_file_watcher_loop(audio_manager, stop_event, llm_connected):
    """
    Background worker thread for Team 3:
    Monitors Team 2's output directory and instantly fires the AI pipeline 
    the second a NEW JSON report file lands, ignoring all past historical files.
    """
    print(f"[Main] File watcher thread active (LLM Connected: {llm_connected}). Monitoring Team 2 JSON outputs...")
    
    # On startup, immediately mark all pre-existing files as "already processed"
    processed_files = set()
    if ERROR_REPORT_DIR.exists():
        processed_files = {f.name for f in ERROR_REPORT_DIR.glob("error_report_*.json")}
        if processed_files:
            print(f"[Main] Safely ignored {len(processed_files)} historical reports found in directory.")

    # Ensure target log directories exist
    ERROR_REPORT_DIR.mkdir(parents=True, exist_ok=True)

    # Run the loop while checking your stop_event
    while not stop_event.is_set():
        try:
            # Query all current JSON reports created by Team 2
            current_files = {f.name for f in ERROR_REPORT_DIR.glob("error_report_*.json")}
            
            # Find files that are ACTUALLY new (present in directory but not in our processed set)
            new_files = current_files - processed_files
            
            if new_files:
                # Sort them to process the absolute latest one first
                latest_new_file_name = sorted(list(new_files))[-1]
                latest_file_path = ERROR_REPORT_DIR / latest_new_file_name
                
                print(f"\n[Main] Detected REAL NEW Team 2 report: {latest_new_file_name}")
                print(f"[Main] Triggering Core AI Pipeline...")

                process_all_errors_pipeline(
                    audio_manager, 
                    error_report_path=latest_file_path, 
                    is_llm_available=llm_connected
                )
                
                # Update our set so we never process this file (or any older files) again
                processed_files.update(current_files)
                    
        except Exception as e:
            print(f"[Main] Exception caught in file monitor loop: {e}")
            
        time.sleep(0.5)  # Scan every 500ms to conserve CPU cycles


def main():
    print("\n" + "="*50)
    print("[Main] Initiating early workspace warm-up...")
    audio_manager = AudioManager()

    llm_connected = True

    # Initiating early workspace
    try:
        from granite.granite_client import init_granite_model, get_ai_link_status

        init_granite_model()
        
        # Load heavy vector embeddings right now so we experience zero lag inside the live loop
        load_knowledge_base()
        print("[Main] RAG Knowledge Base primed and ready in memory!")
        
        # Verify IBM Watsonx API connectivity status before UI handoff
        print("[Main] Testing live AI server link state...")
        ai_status = get_ai_link_status()
        
        llm_connected = ai_status.get("llm_connected", True)
        
        if llm_connected:
            print(f"[Main] {ai_status['message']} Ready for UI state handoff.")
        else:
            print(f"[Main WARNING] {ai_status['message']}")
            print("[Main WARNING] Continuing execution under local Rule-Based Fallback protocol.")
            # audio_manager.shutdown()
            # sys.exit(1)
    except Exception as e:
        print(f"[Main CRITICAL] Team 3 Knowledge Base failed initialization, aborting: {e}")
        audio_manager.shutdown()
        sys.exit(1)
    print("="*50 + "\n")

    # Data pipeline
    handler = InputHandler()
    logger = CSVLogger()
    client = Client(handler, logger, cache)

    print("[Main] Starting Data Pipeline connection thread...")
    client_thread = threading.Thread(target=client.start, daemon=True)
    client_thread.start()

    time.sleep(0.5)  # Allow data stream a split second to stabilize

    ai_stop_event = threading.Event()
    ai_thread = threading.Thread(
        target=ai_file_watcher_loop, 
        args=(audio_manager, ai_stop_event, llm_connected), 
        daemon=True
    )
    ai_thread.start()

    # Analyais Data
    print("[Main] Automatically launching Live Coach subprocess...")
    coach_process = subprocess.Popen(
        [sys.executable, "-m", "analysis.live_coach"],
        stdout=None,
        stderr=None,
        cwd=str(SRC_DIR)
    )

    # Main thread
    try:
        while cache.get_status() not in (GameStatus.ERROR, GameStatus.FINISHED):
            time.sleep(0.1)
        current_status = cache.get_status()
        if current_status == GameStatus.FINISHED:
            print("[Main] Game finished. Waiting for post-game AI analysis to complete...")
            ai_timeout = 120.0 
            start_wait = time.time()
            while True:
                if time.time() - start_wait > ai_timeout:
                    print("[Main] Warning: Post-game AI analysis timed out. Proceeding to shutdown.")
                    break
                time.sleep(0.2)
    except KeyboardInterrupt:
        print("\n[Main] Keyboard interrupt. Lost connection to TORCS.")
    except Exception as e:
        print(f"[Main] Unexpected error in orchestration loop: {e}")
    finally:
        print("[Main] Stopping data pipeline client and connections...")
        client.stop()

        print("[Main] Signaling AI File Watcher thread to stop...")
        ai_stop_event.set()

        print("[Main] Terminating Live Coach subprocess...")
        coach_process.terminate()
        try:
            coach_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            coach_process.kill()
            
        print("[Main] Releasing AI Audio Manager resources...")
        audio_manager.shutdown()

        print("[Main] System exited cleanly.")
        sys.exit(0)


if __name__ == '__main__':
    main()