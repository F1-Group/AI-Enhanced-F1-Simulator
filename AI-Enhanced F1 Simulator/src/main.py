from data_pipeline.input import InputHandler
from data_pipeline.client import Client
from data_pipeline.logger import CSVLogger
from data_pipeline.cache import cache, GameStatus
import threading
import time
import sys

def main():
    handler = InputHandler()
    logger = CSVLogger()

    client = Client(handler, logger, cache)

    client_thread = threading.Thread(target=client.start, daemon=True)
    client_thread.start()

    try:
        while cache.get_status() not in (GameStatus.ERROR, GameStatus.FINISHED):
            # The main thread waits for this task or performs other work.
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nKeyboard interrupt. Lost connection to TORCS.")
    except Exception as e:
        print(f"Unexpected error: {e}")
    finally:
        client.stop()
        sys.exit(0)


if __name__ == '__main__':
    main()