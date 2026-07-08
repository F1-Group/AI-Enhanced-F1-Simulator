from data_pipeline.input import InputHandler
from data_pipeline.client import Client
from data_pipeline.logger import CSVLogger
from data_pipeline.cache import cache, GameStatus
import threading
import time
import sys
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

def main():
    handler = InputHandler()
    logger = CSVLogger()

    client = Client(handler, logger, cache)

    client_thread = threading.Thread(target=client.start, daemon=True)
    client_thread.start()

    time.sleep(0.5)

    print("[Main] Launching Live Coach process automatically...")
    coach_process = subprocess.Popen(
        [sys.executable, "-m", "analysis.live_coach"],
        stdout=None,
        stderr=None,
        cwd=str(PROJECT_ROOT)
    )

    try:
        while cache.get_status() not in (GameStatus.ERROR, GameStatus.FINISHED):
            # The main thread waits for this task or performs other work.
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nKeyboard interrupt. Lost connection to TORCS.")
    except Exception as e:
        print(f"Unexpected error: {e}")
    finally:
        print("[Main] Stopping data pipeline client and connections...")
        client.stop()

        print("[Main] Terminating Live Coach subprocess...")
        coach_process.terminate()
        try:
            # Allow up to 5s for Team 2 to save final lap JSON reports
            coach_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            # Force kill if it exceeds the timeout threshold
            coach_process.kill()
            
        print("[Main] System exited cleanly.")
        sys.exit(0)


if __name__ == '__main__':
    main()