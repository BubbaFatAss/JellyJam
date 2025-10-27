import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException
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
        # Create SpotifyOAuth helper for refreshing tokens
        auth = SpotifyOAuth(client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri, scope='user-modify-playback-state,user-read-playback-state')
        # keep auth helper on self for refresh attempts
        self._auth = auth
        token_info = self.storage.load().get('spotify_token')
        if token_info:
            # If token_info has an expires_at timestamp, and it's expired, try to refresh
            expires_at = token_info.get('expires_at')
            now = int(time.time())
            if expires_at and expires_at <= now:
                # attempt refresh using refresh_token
                refresh_token = token_info.get('refresh_token')
                if refresh_token:
                    try:
                        new_token = auth.refresh_access_token(refresh_token)
                        # SpotifyOAuth.refresh_access_token returns a token dict similar to token_info
                        token_info.update(new_token)
                        # persist updated token info
                        cfg_all = self.storage.load()
                        cfg_all['spotify_token'] = token_info
                        self.storage.save(cfg_all)
                    except Exception as e:
                        print('Failed to refresh spotify token:', e)
                        # fallthrough and try to construct client with existing token (may fail)
            # attempt to create client with (possibly refreshed) token
            sp = spotipy.Spotify(auth=token_info.get('access_token'))
            self.sp = sp
            return sp
        # In this scaffold we do not implement full OAuth flow in the backend; UI should handle and save token
        print('No cached token - please complete OAuth via the web UI (not implemented)')
        return None

    def _call_spotify(self, method_name, *args, **kwargs):
        """Call a Spotify Web API method and refresh token once on 401 errors."""
        sp = self._ensure_client()
        if not sp:
            return None
        func = getattr(sp, method_name)
        try:
            return func(*args, **kwargs)
        except SpotifyException as e:
            status = getattr(e, 'http_status', None)
            msg = str(e).lower()
            if status == 401 or 'token' in msg or 'expired' in msg:
                # try to refresh token and retry once
                token_info = self.storage.load().get('spotify_token') or {}
                refresh_token = token_info.get('refresh_token')
                if refresh_token and getattr(self, '_auth', None):
                    try:
                        new_token = self._auth.refresh_access_token(refresh_token)
                        token_info.update(new_token)
                        cfg_all = self.storage.load()
                        cfg_all['spotify_token'] = token_info
                        self.storage.save(cfg_all)
                        # recreate client with new access token
                        self.sp = spotipy.Spotify(auth=token_info.get('access_token'))
                        func = getattr(self.sp, method_name)
                        return func(*args, **kwargs)
                    except Exception as e2:
                        print('Spotify token refresh failed during retry:', e2)
            # re-raise or return None
            raise

    def play_playlist(self, playlist_uri):
        # Use helper which handles token refresh
        devices = self._call_spotify('devices')
        if not devices or not devices.get('devices'):
            print('No active spotify devices found. Start a device (librespot or official client)')
            return
        cfg = self.storage.load()
        selected = cfg.get('spotify_selected_device')
        active_ids = [d['id'] for d in devices['devices']]
        device_id = selected if (selected and selected in active_ids) else devices['devices'][0]['id']
        self._call_spotify('start_playback', device_id=device_id, context_uri=playlist_uri)

    def play(self):
        cfg = self.storage.load()
        selected = cfg.get('spotify_selected_device')
        try:
            if selected:
                try:
                    self._call_spotify('start_playback', device_id=selected)
                    return
                except Exception:
                    pass
            self._call_spotify('start_playback')
        except Exception:
            pass

    def pause(self):
        try:
            self._call_spotify('pause_playback')
        except Exception:
            pass

    def next(self):
        try:
            self._call_spotify('next_track')
        except Exception:
            pass

    def previous(self):
        try:
            self._call_spotify('previous_track')
        except Exception:
            pass

    def seek(self, position_ms):
        try:
            self._call_spotify('seek_track', position_ms)
        except Exception:
            pass

    def set_volume(self, vol):
        try:
            cfg = self.storage.load()
            device_id = cfg.get('spotify_selected_device')
            if device_id:
                self._call_spotify('volume', vol, device_id=device_id)
            else:
                self._call_spotify('volume', vol)
        except Exception:
            pass

    def get_volume(self):
        # Spotify Web API doesn't provide a direct volume query for a device; try to get it from current playback state
        sp = self._ensure_client()
        if not sp: return None
        try:
            state = self._call_spotify('current_playback')
            if not state:
                return None
            device = state.get('device') or {}
            vol = device.get('volume_percent')
            return int(vol) if vol is not None else None
        except Exception:
            return None

    def now_playing(self):
        state = self._call_spotify('current_playback')
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
        # include track id to allow mapping lookups
        track_id = item.get('id') or item.get('uri')
        return {'source':'spotify','id': track_id,'title':title,'artist':artists,'album':album,'position_ms':position,'duration_ms':duration,'playing':playing,'image_url':image_url}

    def list_devices(self):
        try:
            dev = self._call_spotify('devices')
            return dev.get('devices', []) if dev else []
        except Exception:
            return []

    def set_shuffle(self, enabled: bool):
        try:
            cfg = self.storage.load()
            device_id = cfg.get('spotify_selected_device')
            if device_id:
                self._call_spotify('shuffle', enabled, device_id=device_id)
            else:
                self._call_spotify('shuffle', enabled)
        except Exception:
            pass

    def set_repeat(self, mode):
        try:
            cfg = self.storage.load()
            device_id = cfg.get('spotify_selected_device')
            if device_id:
                self._call_spotify('repeat', mode, device_id=device_id)
            else:
                self._call_spotify('repeat', mode)
        except Exception:
            pass

    def get_options(self):
        try:
            state = self.sp.current_playback() if self.sp else None
            if not state:
                return {'shuffle': False, 'repeat': False}
            shuffle = state.get('shuffle_state', False)
            repeat_state = state.get('repeat_state', 'off')
            repeat = repeat_state != 'off'
            return {'shuffle': bool(shuffle), 'repeat': bool(repeat)}
        except Exception:
            return {'shuffle': False, 'repeat': False}
