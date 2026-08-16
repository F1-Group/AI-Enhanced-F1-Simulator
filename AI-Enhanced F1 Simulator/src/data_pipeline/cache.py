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
        self._corners = []
        self._track_length = None

    def update_telemetry(self, cleaned_data: dict):
        with self._lock:
            if cleaned_data is None:
                self._data = None
            else:
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

    def set_corners(self, corners, track_length):
        """Called once per race (by main.py, right after LiveCoach builds its
        expert-baseline corner list) so the dashboard thread can show which
        turn the driver is currently in without needing pandas/analysis
        imports of its own - just a plain list of dicts."""
        with self._lock:
            self._corners = list(corners) if corners else []
            self._track_length = track_length

    def get_corners(self):
        with self._lock:
            return list(self._corners)

    def get_track_length(self):
        with self._lock:
            return self._track_length

# Shared instance
cache = DataCache()