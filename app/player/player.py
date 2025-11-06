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
        # optional callback invoked when the active track changes (e.g., next/previous/end)
        self._track_change_callback = None

    def handle_nfc(self, card_id):
        cfg = self.storage.load()
        mapping = cfg.get('mappings', {}).get(card_id)
        if not mapping:
            print(f'No mapping for card {card_id}')
            return
        if mapping['type'] == 'local':
            print(f'Playing local playlist {mapping["id"]}')
            shuffle = bool(mapping.get('shuffle'))
            repeat_mode = mapping.get('repeat', 'off') or 'off'
            vol = mapping.get('volume')
            self.local.play_playlist(mapping['id'], shuffle=shuffle, repeat_mode=repeat_mode, volume=vol)
            
            # Resume from saved position if enabled
            if mapping.get('resume_position'):
                try:
                    saved_state = mapping.get('saved_state', {})
                    saved_track = saved_state.get('track')
                    saved_position = saved_state.get('position_ms')
                    
                    if saved_track and saved_position is not None:
                        # Try to find and play the saved track
                        import time
                        time.sleep(0.5)  # Give player time to start
                        
                        # Get current playing track to compare
                        now = self.local.now_playing()
                        current_id = now.get('id') if now else None
                        
                        # If not on the right track, we'd need to find it in playlist
                        # For now, just seek if position > 0
                        if saved_position > 0:
                            self.local.seek(saved_position)
                            print(f'Resumed at position {saved_position}ms')
                except Exception as e:
                    print(f'Failed to resume position: {e}')
            
            # persist last volume
            try:
                if vol is not None:
                    cfg = self.storage.load()
                    cfg['last_volume'] = int(vol)
                    self.storage.save(cfg)
            except Exception:
                pass
            self._state.update({'playing': True, 'source': 'local', 'track': mapping['id'], 'mapping_card': card_id})
        elif mapping['type'] == 'spotify':
            print(f'Playing spotify playlist {mapping["id"]}')
            self.spotify.play_playlist(mapping['id'])
            # apply mapping options for spotify
            try:
                self.spotify.set_shuffle(bool(mapping.get('shuffle')))
                self.spotify.set_repeat(mapping.get('repeat', 'off') or 'off')
                vol = mapping.get('volume')
                if vol is not None:
                    try:
                        self.spotify.set_volume(int(vol))
                    except Exception:
                        pass
                    # persist last volume
                    try:
                        cfg = self.storage.load()
                        cfg['last_volume'] = int(vol)
                        self.storage.save(cfg)
                    except Exception:
                        pass
            except Exception:
                pass
            self._state.update({'playing': True, 'source': 'spotify', 'track': mapping['id'], 'mapping_card': card_id})

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
        # Save position if mapping has resume enabled
        self._save_resume_position()
        
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

    def set_volume(self, vol):
        # vol expected 0-100
        if self._state.get('source') == 'local':
            try:
                self.local.set_volume(vol)
            except Exception:
                pass
        elif self._state.get('source') == 'spotify':
            try:
                self.spotify.set_volume(vol)
            except Exception:
                pass

    def get_volume(self):
        if self._state.get('source') == 'local':
            return self.local.get_volume()
        elif self._state.get('source') == 'spotify':
            return self.spotify.get_volume()
        return None

    def apply_options(self, options: dict):
        """Apply temporary options such as shuffle/repeat to the active player."""
        if not options:
            return
        if self._state.get('source') == 'local':
            try:
                if 'shuffle' in options and hasattr(self.local, 'set_shuffle'):
                    self.local.set_shuffle(bool(options.get('shuffle')))
                if 'repeat' in options and hasattr(self.local, 'set_repeat'):
                    self.local.set_repeat(bool(options.get('repeat')))
            except Exception:
                pass
        elif self._state.get('source') == 'spotify':
            try:
                if 'shuffle' in options:
                    self.spotify.set_shuffle(bool(options.get('shuffle')))
                if 'repeat' in options:
                    self.spotify.set_repeat(bool(options.get('repeat')))
            except Exception:
                pass

    def get_options(self):
        # Return a dict with current options if available
        opts = {}
        if self._state.get('source') == 'local' and hasattr(self.local, 'get_options'):
            try:
                return self.local.get_options() or {}
            except Exception:
                return {}
        if self._state.get('source') == 'spotify' and hasattr(self.spotify, 'get_options'):
            try:
                return self.spotify.get_options() or {}
            except Exception:
                return {}
        return opts

    def now_playing(self):
        # Return a normalized now-playing dict
        if self._state.get('source') == 'local':
            return self.local.now_playing()
        elif self._state.get('source') == 'spotify':
            return self.spotify.now_playing()
        return {'source': None}

    def register_track_change_callback(self, cb):
        """Register a callback invoked when the currently playing track changes.
        The callback will be called with no arguments.
        """
        try:
            self._track_change_callback = cb
            if hasattr(self.local, 'set_track_change_callback'):
                try:
                    self.local.set_track_change_callback(cb)
                except Exception:
                    pass
        except Exception:
            pass

    def _save_resume_position(self):
        """Save current track and position for the active mapping if resume is enabled."""
        try:
            mapping_card = self._state.get('mapping_card')
            if not mapping_card:
                return
            
            cfg = self.storage.load()
            mapping = cfg.get('mappings', {}).get(mapping_card)
            if not mapping or not mapping.get('resume_position'):
                return
            
            # Get current playback state
            now = self.now_playing()
            if not now:
                return
            
            track_id = now.get('id')
            position_ms = now.get('position_ms', 0)
            
            # Save state to mapping
            if 'saved_state' not in mapping:
                mapping['saved_state'] = {}
            
            mapping['saved_state']['track'] = track_id
            mapping['saved_state']['position_ms'] = position_ms
            
            # Persist to storage
            cfg['mappings'][mapping_card] = mapping
            self.storage.save(cfg)
            print(f'Saved resume position: track={track_id}, position={position_ms}ms')
        except Exception as e:
            print(f'Failed to save resume position: {e}')

    def stop(self):
        # Save position if mapping has resume enabled
        self._save_resume_position()
        
        # Stop playback entirely depending on the active source
        if self._state.get('source') == 'local':
            try:
                # prefer the LocalPlayer.stop if available (sets user stop flag)
                if hasattr(self.local, 'stop'):
                    self.local.stop()
                else:
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
