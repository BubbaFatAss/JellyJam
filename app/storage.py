import json
import threading


class Storage:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()

    def load(self):
        try:
            with open(self.path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def save(self, obj):
        with self._lock:
            with open(self.path, 'w', encoding='utf-8') as f:
                json.dump(obj, f, indent=2)
