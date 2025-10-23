import vlc
import os
import threading
import logging


class LocalPlayer:
    def __init__(self):
        self.instance = vlc.Instance()
        self.player = self.instance.media_list_player_new()
        self.media_list = self.instance.media_list_new()
        # default base music directory relative to repo root
        import os
        self.base = os.path.abspath(os.path.join(os.getcwd(), 'music'))
        # internal state for repeat/shuffle monitoring and control
        self._shuffle = False
        self._repeat_mode = 'off'
        self._end_count = 0
        self._total_items = 0
        self._monitor_lock = threading.Lock()
        self._user_stopped = False

    def _clear(self):
        self.media_list = self.instance.media_list_new()
        self.player.set_media_list(self.media_list)

    def play_playlist(self, playlist_id, shuffle=False, repeat_mode='off', volume=None):
        # playlist_id may be a directory path or an m3u file
        # If something is currently playing, stop it first to ensure a clean transition
        try:
            with self._monitor_lock:
                self._user_stopped = False
        except Exception:
            pass
        try:
            # stop current playback to allow replacing media list
            self.player.stop()
        except Exception:
            pass
        self._clear()
        self._shuffle = bool(shuffle)
        self._repeat_mode = repeat_mode  # 'off', 'context', 'track'
        # if a relative path is provided, try resolving it against base
        if not os.path.isabs(playlist_id):
            candidate = os.path.join(self.base, playlist_id)
            if os.path.exists(candidate):
                playlist_id = candidate
        files = []
        if os.path.isdir(playlist_id):
            for fn in sorted(os.listdir(playlist_id)):
                path = os.path.join(playlist_id, fn)
                if os.path.isfile(path):
                    files.append(path)
        elif os.path.isfile(playlist_id) and playlist_id.lower().endswith('.m3u'):
            with open(playlist_id, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if os.path.isabs(line) and os.path.exists(line):
                        files.append(line)
        else:
            print('Unknown playlist:', playlist_id)
            return
        # apply shuffle if requested
        try:
            import random
            if self._shuffle:
                random.shuffle(files)
        except Exception:
            pass
        # add files to media list
        for path in files:
            try:
                m = self.instance.media_new(path)
                self.media_list.add_media(m)
            except Exception:
                logging.exception('Failed adding media %s', path)
        self.player.set_media_list(self.media_list)
        # track total items for fallback looping
        try:
            self._total_items = self.media_list.count()
        except Exception:
            self._total_items = len(files)
        # reset end-of-track counter and user stop flag
        with self._monitor_lock:
            self._end_count = 0
            self._user_stopped = False
        # set playback mode for context repeat if supported
        try:
            pm = getattr(vlc, 'PlaybackMode', None)
            if pm is not None:
                if self._repeat_mode == 'context' and hasattr(self.player, 'set_playback_mode'):
                    # try to set loop mode
                    try:
                        self.player.set_playback_mode(getattr(pm, 'loop', getattr(pm, 'repeat', 0)))
                    except Exception:
                        pass
                else:
                    try:
                        self.player.set_playback_mode(getattr(pm, 'default', 0))
                    except Exception:
                        pass
        except Exception:
            pass

        # attach end-of-track handler for 'track' repeat
        try:
            mp = self.player.get_media_player()
            em = mp.event_manager()
            # detach previous handler if any
            try:
                em.event_detach(vlc.EventType.MediaPlayerEndReached)
            except Exception:
                pass
            # attach a unified end handler that supports both 'track' and 'context' repeat
            def _on_end(ev):
                try:
                    # track repeat: replay current media immediately
                    if self._repeat_mode == 'track':
                        try:
                            mp.play()
                        except Exception:
                            logging.exception('Failed to restart track')
                        return

                    # context repeat fallback: count end events and when we've seen
                    # as many ends as there are items, restart the playlist from top
                    with self._monitor_lock:
                        self._end_count += 1
                        total = getattr(self, '_total_items', 0) or 0
                        # If user explicitly stopped playback, do not auto-restart
                        if getattr(self, '_user_stopped', False):
                            return
                        if self._repeat_mode == 'context' and total > 0 and self._end_count >= total:
                            # reset counter and restart playlist
                            self._end_count = 0
                            try:
                                # small delay so libVLC can transition cleanly
                                threading.Timer(0.15, lambda: self.player.play()).start()
                            except Exception:
                                logging.exception('Failed to restart playlist on context repeat')
                except Exception:
                    logging.exception('Error in end-of-track handler')

            em.event_attach(vlc.EventType.MediaPlayerEndReached, _on_end)
        except Exception:
            logging.exception('Failed to attach end event')

        self.player.play()

        # apply optional volume override if provided (retry logic inside set_volume)
        if volume is not None:
            try:
                self.set_volume(int(volume))
            except Exception:
                pass

    def now_playing(self):
        try:
            mp = self.player.get_media_player()
            media = mp.get_media()
            if not media:
                return {'source':'local','title':None,'artist':None,'album':None,'position_ms':0,'duration_ms':0,'playing':False,'image_url':None}
            title = media.get_meta(0) or media.get_mrl()
            # VLC metadata indexes: 0=title, 1=artist, 4=album (may be backend dependent)
            artist = media.get_meta(1)
            album = media.get_meta(4)
            position = int(mp.get_time() or 0)
            duration = int(mp.get_length() or 0)
            playing = (mp.is_playing() == 1)
            # Try to extract embedded artwork from the current media file path
            try:
                mrl = media.get_mrl()
                # mrl may be like file:///C:/path/to/file.mp3 or /path/to/file.mp3
                import urllib.parse, os, re
                u = urllib.parse.urlparse(mrl)
                if u.scheme == 'file':
                    path = urllib.parse.unquote(u.path)
                else:
                    path = urllib.parse.unquote(mrl)
                # On Windows VLC may return a leading slash before drive letter
                if os.name == 'nt' and re.match(r'^/[A-Za-z]:', path):
                    path = path[1:]
                path = path if os.path.isabs(path) else os.path.abspath(path)
                image_url = None
                from mutagen import File as MutagenFile
                from mutagen.mp3 import MP3
                from mutagen.flac import FLAC
                from mutagen.mp4 import MP4
                from mutagen.id3 import ID3, APIC
                mf = MutagenFile(path)
                imgdata = None
                if mf is not None:
                    # MP3 with ID3 APIC frames
                    try:
                        if isinstance(mf, MP3):
                            try:
                                id3 = ID3(path)
                                apics = id3.getall('APIC')
                                if apics:
                                    imgdata = apics[0].data
                            except Exception:
                                imgdata = None
                        # MP4 (m4a) covr atom
                        elif isinstance(mf, MP4):
                            covr = mf.tags.get('covr') if mf.tags is not None else None
                            if covr:
                                # covr[0] is bytes-like
                                imgdata = covr[0]
                        # FLAC pictures
                        elif isinstance(mf, FLAC):
                            pics = mf.pictures
                            if pics:
                                imgdata = pics[0].data
                        else:
                            # Generic fallback: inspect tags for binary data
                            try:
                                if mf.tags is not None:
                                    for v in mf.tags.values():
                                        if hasattr(v, 'data') and v.data:
                                            imgdata = v.data
                                            break
                            except Exception:
                                imgdata = None
                    except Exception:
                        imgdata = None
                if imgdata:
                    # save to static/artwork with a hash name
                    import hashlib
                    h = hashlib.sha1(imgdata).hexdigest()
                    static_dir = os.path.join(os.path.dirname(__file__), '..', 'static', 'artwork')
                    try:
                        os.makedirs(static_dir, exist_ok=True)
                        out_path = os.path.join(static_dir, f'{h}.jpg')
                        if not os.path.exists(out_path):
                            try:
                                with open(out_path, 'wb') as out:
                                    out.write(imgdata)
                            except Exception:
                                logging.exception('Failed writing artwork file for %s -> %s', path, out_path)
                                image_url = None
                            else:
                                image_url = f'/static/artwork/{h}.jpg'
                        else:
                            image_url = f'/static/artwork/{h}.jpg'
                    except Exception:
                        logging.exception('Failed creating artwork directory %s', static_dir)
                        image_url = None
                else:
                    image_url = None
            except Exception:
                logging.exception('Error extracting artwork for %s', path if 'path' in locals() else '<unknown>')
                image_url = None
            return {'source':'local','title':title,'artist':artist,'album':album,'position_ms':position,'duration_ms':duration,'playing':playing,'image_url':image_url}
        except Exception:
            return {'source':'local','title':None,'artist':None,'album':None,'position_ms':0,'duration_ms':0,'playing':False,'image_url':None}

    def seek(self, position_ms):
        try:
            mp = self.player.get_media_player()
            mp.set_time(int(position_ms))
        except Exception:
            pass

    def set_volume(self, vol):
        """Set local VLC player's volume (0-100).

        Sometimes the underlying VLC media player isn't fully initialized immediately
        after starting playback. In that case, try setting the volume a few times
        with a short delay in a background thread until it succeeds.
        """
        try:
            v = int(vol)
        except Exception:
            return
        # clamp
        v = max(0, min(100, v))

        def _try_set():
            try:
                mp = self.player.get_media_player()
                if not mp:
                    return False
                mp.audio_set_volume(int(v))
                return True
            except Exception:
                return False

        # try once immediately
        _try_set()

        # otherwise retry a few times in background
        def _retry_loop():
            import time
            for _ in range(6):
                time.sleep(0.5)
                if _try_set():
                    return

        try:
            t = threading.Thread(target=_retry_loop, daemon=True)
            t.start()
        except Exception:
            pass

    def get_volume(self):
        """Get current local VLC player's volume (0-100) or None if unavailable."""
        try:
            mp = self.player.get_media_player()
            v = mp.audio_get_volume()
            return int(v) if v is not None else None
        except Exception:
            return None

    def list_playlists(self):
        """Return list of available playlists under the base music directory.
        Directories and .m3u files are considered playlists.
        Returns list of absolute paths."""
        import os
        results = []
        if not os.path.isdir(self.base):
            return results
        for entry in sorted(os.listdir(self.base)):
            path = os.path.join(self.base, entry)
            if os.path.isdir(path) or (os.path.isfile(path) and entry.lower().endswith('.m3u')):
                results.append(path)
        return results

    # Local options are stored in-memory and applied where possible
    def set_shuffle(self, enabled: bool):
        # VLC media_list_player does not provide a direct shuffle API; this is a placeholder
        # for a future implementation where the media list would be randomized when enabled.
        try:
            self._shuffle = bool(enabled)
        except Exception:
            self._shuffle = False

    def set_repeat(self, enabled: bool):
        # For backward compatibility this toggles a boolean flag. Prefer using
        # play_playlist(..., repeat_mode=...) which sets 'off'|'context'|'track'.
        try:
            # map boolean to 'context' when True
            self._repeat_mode = 'context' if bool(enabled) else 'off'
        except Exception:
            self._repeat_mode = 'off'

    def get_options(self):
        return {'shuffle': getattr(self, '_shuffle', False), 'repeat': getattr(self, '_repeat_mode', 'off')}

    def stop(self):
        """Stop playback and mark as user-stopped to prevent auto-restarts."""
        try:
            with self._monitor_lock:
                self._user_stopped = True
            self.player.stop()
        except Exception:
            logging.exception('Failed to stop local player')
