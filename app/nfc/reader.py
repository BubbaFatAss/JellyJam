import threading
import time


class NFCReader:
    def __init__(self, callback=None):
        self.callback = callback
        self._running = False

    def start(self):
        # In a real deployment, this would open the NFC hardware and block listening for tags.
        self._running = True
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()

    def _loop(self):
        # placeholder loop
        while self._running:
            time.sleep(1)

    def stop(self):
        self._running = False

    def simulate_scan(self, card_id):
        # helper for the web UI / tests to trigger the callback
        if self.callback:
            self.callback(card_id)
