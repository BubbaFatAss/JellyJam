import threading
import time
from .local_player import LocalPlayer
from .spotify_player import SpotifyPlayer


class Player:
    def __init__(self, storage):
        self.storage = storage
        self.local = LocalPlayer()
        self.spotify = SpotifyPlayer(storage)
        self._state = {'playing': False, 'source': None, 'track': None}

    def handle_nfc(self, card_id):
        cfg = self.storage.load()
        mapping = cfg.get('mappings', {}).get(card_id)
        if not mapping:
            print(f'No mapping for card {card_id}')
            return
        if mapping['type'] == 'local':
            print(f'Playing local playlist {mapping["id"]}')
            self.local.play_playlist(mapping['id'])
            self._state.update({'playing': True, 'source': 'local', 'track': mapping['id']})
        elif mapping['type'] == 'spotify':
            print(f'Playing spotify playlist {mapping["id"]}')
            self.spotify.play_playlist(mapping['id'])
            self._state.update({'playing': True, 'source': 'spotify', 'track': mapping['id']})

    def status(self):
        return self._state

    # Control methods used by the web UI
    def play(self):
        if self._state.get('source') == 'local':
            self.local.player.play()
            self._state['playing'] = True
        elif self._state.get('source') == 'spotify':
            self.spotify.play()
            self._state['playing'] = True

    def pause(self):
        if self._state.get('source') == 'local':
            self.local.player.pause()
            self._state['playing'] = False
        elif self._state.get('source') == 'spotify':
            self.spotify.pause()
            self._state['playing'] = False

    def next(self):
        if self._state.get('source') == 'local':
            self.local.player.next()
        elif self._state.get('source') == 'spotify':
            self.spotify.next()

    def previous(self):
        if self._state.get('source') == 'local':
            self.local.player.previous()
        elif self._state.get('source') == 'spotify':
            self.spotify.previous()

    def seek(self, position_ms):
        if self._state.get('source') == 'local':
            self.local.seek(position_ms)
        elif self._state.get('source') == 'spotify':
            self.spotify.seek(position_ms)

    def now_playing(self):
        # Return a normalized now-playing dict
        if self._state.get('source') == 'local':
            return self.local.now_playing()
        elif self._state.get('source') == 'spotify':
            return self.spotify.now_playing()
        return {'source': None}

    def stop(self):
        # Stop playback entirely depending on the active source
        if self._state.get('source') == 'local':
            try:
                self.local.player.stop()
            except Exception:
                pass
            self._state['playing'] = False
        elif self._state.get('source') == 'spotify':
            try:
                # Spotify doesn't have a dedicated 'stop' - pause is closest
                self.spotify.pause()
            except Exception:
                pass
            self._state['playing'] = False
