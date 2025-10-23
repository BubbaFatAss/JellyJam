import spotipy
from spotipy.oauth2 import SpotifyOAuth
import threading
import time


class SpotifyPlayer:
    def __init__(self, storage):
        self.storage = storage
        self.sp = None

    def _ensure_client(self):
        cfg = self.storage.load().get('spotify', {})
        client_id = cfg.get('client_id')
        client_secret = cfg.get('client_secret')
        redirect_uri = cfg.get('redirect_uri')
        if not (client_id and client_secret and redirect_uri):
            print('Spotify not configured')
            return None
        auth = SpotifyOAuth(client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri, scope='user-modify-playback-state,user-read-playback-state')
        token_info = self.storage.load().get('spotify_token')
        if token_info:
            # attempt to create client with cached token
            sp = spotipy.Spotify(auth=token_info['access_token'])
            self.sp = sp
            return sp
        # In this scaffold we do not implement full OAuth flow in the backend; UI should handle and save token
        print('No cached token - please complete OAuth via the web UI (not implemented)')
        return None

    def play_playlist(self, playlist_uri):
        sp = self._ensure_client()
        if not sp:
            print('Spotify client unavailable')
            return
        # find an active device
        # prefer stored selected device if available
        cfg = self.storage.load()
        selected = cfg.get('spotify_selected_device')
        devices = sp.devices()
        if not devices.get('devices'):
            print('No active spotify devices found. Start a device (librespot or official client)')
            return
        # if selected device id is present and active, use it, otherwise use first device
        active_ids = [d['id'] for d in devices['devices']]
        if selected and selected in active_ids:
            device_id = selected
        else:
            device_id = devices['devices'][0]['id']
        sp.start_playback(device_id=device_id, context_uri=playlist_uri)

    def play(self):
        sp = self._ensure_client()
        if not sp: return
        # attempt to use selected device
        cfg = self.storage.load()
        selected = cfg.get('spotify_selected_device')
        if selected:
            try:
                sp.start_playback(device_id=selected)
                return
            except Exception:
                pass
        sp.start_playback()

    def pause(self):
        sp = self._ensure_client()
        if not sp: return
        sp.pause_playback()

    def next(self):
        sp = self._ensure_client()
        if not sp: return
        sp.next_track()

    def previous(self):
        sp = self._ensure_client()
        if not sp: return
        sp.previous_track()

    def seek(self, position_ms):
        sp = self._ensure_client()
        if not sp: return
        sp.seek_track(position_ms)

    def now_playing(self):
        sp = self._ensure_client()
        if not sp: return {'source':'spotify','title':None,'artist':None,'album':None,'position_ms':0,'duration_ms':0,'playing':False}
        state = sp.current_playback()
        if not state or not state.get('item'):
            return {'source':'spotify','title':None,'artist':None,'album':None,'position_ms':0,'duration_ms':0,'playing':False}
        item = state['item']
        title = item.get('name')
        artists = ', '.join([a['name'] for a in item.get('artists', [])])
        album = item.get('album', {}).get('name')
        position = int(state.get('progress_ms') or 0)
        duration = int(item.get('duration_ms') or 0)
        playing = state.get('is_playing', False)
        # album art
        images = item.get('album', {}).get('images') or []
        image_url = images[0]['url'] if images else None
        return {'source':'spotify','title':title,'artist':artists,'album':album,'position_ms':position,'duration_ms':duration,'playing':playing,'image_url':image_url}

    def list_devices(self):
        sp = self._ensure_client()
        if not sp: return []
        dev = sp.devices()
        return dev.get('devices', [])
