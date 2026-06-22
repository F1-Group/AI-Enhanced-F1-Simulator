import threading

class DataCache:

    def __init__(self):
        self._lock = threading.Lock()
        self._data = None

    def write(self, cleaned_data: dict):
        with self._lock:
            self._data = cleaned_data.copy()

    def read(self):
        with self._lock:
            if self._data is not None:
                return self._data.copy() 
            else:
                return None
# Shared instance
cache = DataCache()