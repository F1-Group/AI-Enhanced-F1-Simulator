import threading
from enum import Enum, auto

class GameStatus(Enum):
    CONNECTING = auto()
    RACING = auto()
    FINISHED = auto()
    ERROR = auto()

class DataCache:

    def __init__(self):
        self._lock = threading.Lock()
        self._data = None
        self._status = None

    def update_telemetry(self, cleaned_data: dict):
        with self._lock:
            self._data = cleaned_data.copy()

    def get_telemetry(self):
        with self._lock:
            if self._data is not None:
                return self._data.copy() 
            else:
                return None
    
    def set_status(self, status: GameStatus):
        with self._lock:
            self._status = status

    def get_status(self) -> GameStatus:
        with self._lock:
            return self._status

# Shared instance
cache = DataCache()