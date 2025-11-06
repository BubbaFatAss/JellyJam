import vlc
import os
import threading
import logging


class LocalPlayer:
    def __init__(self):
        self.instance = vlc.Instance()
        self.player = self.instance.media_list_player_new()
        self.media_list = self.instance.media_list_new()
        # default base music directory: allow override via MUSIC_BASE env var,
        # otherwise resolve relative to the repository root so behavior is
        # consistent regardless of the current working directory used to run
        # the server.
        import os
        env_base = os.environ.get('MUSIC_BASE') or os.environ.get('MUSIC_DIR')
        if env_base:
            self.base = os.path.abspath(env_base)
        else:
            # __file__ is app/player/local_player.py; go up two levels to repo root
            self.base = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'music'))
        # internal state for repeat/shuffle monitoring and control
        self._shuffle = False
        self._repeat_mode = 'off'
        self._end_count = 0
        self._total_items = 0
        self._monitor_lock = threading.Lock()
        self._user_stopped = False
        # optional callback for track change notifications
        self._track_change_callback = None

    def set_track_change_callback(self, cb):
        """Register a callback to be invoked when the currently playing track changes.

        The callback will be called with no arguments. Exceptions raised by the
        callback are swallowed to avoid impacting playback.
        """
        try:
            self._track_change_callback = cb
        except Exception:
            pass

    def _clear(self):
        self.media_list = self.instance.media_list_new()
        self.player.set_media_list(self.media_list)

    def play_playlist(self, playlist_id, shuffle=False, repeat_mode='off', volume=None, resume_track=None, resume_position_ms=None):
        # playlist_id may be a directory path or an m3u file
        # resume_track: absolute path to the track to start from
        # resume_position_ms: position in milliseconds to seek to after starting
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
        # helper to resolve an m3u entry which may be a plain path or a file:// URL
        def _resolve_m3u_entry(entry, base_dir):
            try:
                import urllib.parse, re
                e = entry.strip()
                if not e or e.startswith('#'):
                    return None
                # file:// URL handling
                if e.lower().startswith('file://'):
                    u = urllib.parse.urlparse(e)
                    p = urllib.parse.unquote(u.path)
                    # On Windows VLC/URLs sometimes include a leading slash before drive letter
                    if os.name == 'nt' and re.match(r'^/[A-Za-z]:', p):
                        p = p[1:]
                    # if still not absolute, resolve against base_dir
                    if not os.path.isabs(p):
                        p = os.path.join(base_dir, p)
                    return p if os.path.exists(p) else None
                # plain path: resolve relative to base_dir
                if not os.path.isabs(e):
                    cand = os.path.join(base_dir, e)
                else:
                    cand = e
                return cand if os.path.exists(cand) else None
            except Exception:
                return None
        if os.path.isdir(playlist_id):
            # If the directory contains an .m3u playlist file, prefer using that
            # playlist file instead of adding every file in the directory. This
            # allows curated playlists to live alongside the audio files.
            m3us = [fn for fn in sorted(os.listdir(playlist_id)) if fn.lower().endswith('.m3u')]
            if m3us:
                # pick the first .m3u file
                m3u_path = os.path.join(playlist_id, m3us[0])
                try:
                    with open(m3u_path, 'r', encoding='utf-8') as f:
                        base = os.path.dirname(m3u_path)
                        for line in f:
                            p = _resolve_m3u_entry(line, base)
                            if p:
                                files.append(p)
                except Exception:
                    # fall back to adding files in directory if reading fails
                    for fn in sorted(os.listdir(playlist_id)):
                        path = os.path.join(playlist_id, fn)
                        if os.path.isfile(path):
                            files.append(path)
            else:
                for fn in sorted(os.listdir(playlist_id)):
                    path = os.path.join(playlist_id, fn)
                    if os.path.isfile(path):
                        files.append(path)
        elif os.path.isfile(playlist_id) and playlist_id.lower().endswith('.m3u'):
            with open(playlist_id, 'r', encoding='utf-8') as f:
                base = os.path.dirname(playlist_id)
                for line in f:
                    p = _resolve_m3u_entry(line, base)
                    if p:
                        files.append(p)
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
        
        # If resuming from a specific track, find its index before adding to media list
        start_index = 0
        if resume_track and files:
            try:
                # Normalize paths for comparison
                resume_path = os.path.normpath(resume_track)
                for i, file_path in enumerate(files):
                    if os.path.normpath(file_path) == resume_path:
                        start_index = i
                        print(f'Found resume track at index {i}')
                        break
            except Exception:
                logging.exception('Failed to find resume track')
        
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
            # Attach media-changed/playing events to notify when a new track starts
            try:
                def _on_media_changed(ev):
                    try:
                        if getattr(self, '_track_change_callback', None):
                            try:
                                self._track_change_callback()
                            except Exception:
                                pass
                    except Exception:
                        pass
                # prefer MediaPlayerMediaChanged if available
                if hasattr(vlc.EventType, 'MediaPlayerMediaChanged'):
                    try:
                        em.event_attach(vlc.EventType.MediaPlayerMediaChanged, _on_media_changed)
                    except Exception:
                        pass
                # also attach MediaPlayerPlaying as a fallback
                if hasattr(vlc.EventType, 'MediaPlayerPlaying'):
                    try:
                        em.event_attach(vlc.EventType.MediaPlayerPlaying, _on_media_changed)
                    except Exception:
                        pass
            except Exception:
                pass
        except Exception:
            logging.exception('Failed to attach end event')

        # Start playback at the appropriate index (determined earlier)
        if start_index > 0:
            try:
                self.player.play_item_at_index(start_index)
            except Exception:
                logging.exception('Failed to play from index, using default')
                # Fallback: just play from beginning
                self.player.play()
        else:
            self.player.play()

        # If resuming to a specific position, seek after a brief delay
        if resume_position_ms is not None and resume_position_ms > 0:
            import threading, time
            def _delayed_seek():
                time.sleep(0.3)  # Brief delay for player to initialize
                try:
                    mp = self.player.get_media_player()
                    if mp:
                        mp.set_time(int(resume_position_ms))
                        logging.info(f'Resumed at position {resume_position_ms}ms')
                except Exception:
                    logging.exception('Failed to seek to resume position')
            threading.Thread(target=_delayed_seek, daemon=True).start()

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
                # Compute a stable cache key (SHA1) based on the music file path relative to
                # the configured music base. If this fails, set h=None and do not attempt
                # to cache artwork. Only attempt to extract embedded artwork when the
                # cache is missing or older than the music file.
                import hashlib
                try:
                    try:
                        rel = os.path.relpath(path, getattr(self, 'base', os.path.dirname(path)))
                    except Exception:
                        rel = path
                    h = hashlib.sha1(rel.replace('\\', '/').encode('utf-8')).hexdigest()
                except Exception:
                    h = None

                # Prepare artwork paths if we have a cache key
                data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'data'))
                art_dir = os.path.join(data_dir, 'artwork')
                out_path = None
                write_needed = False
                if h:
                    try:
                        os.makedirs(art_dir, exist_ok=True)
                        out_path = os.path.join(art_dir, f'{h}.jpg')
                        if os.path.exists(out_path):
                            try:
                                music_mtime = os.path.getmtime(path)
                                art_mtime = os.path.getmtime(out_path)
                                if music_mtime <= art_mtime:
                                    # cache is up-to-date; no extraction needed
                                    image_url = f'/artwork/{h}.jpg'
                                    write_needed = False
                                else:
                                    # cached artwork is older -> we need to extract and overwrite
                                    write_needed = True
                            except Exception:
                                # if we can't stat, do not attempt extraction (conservative)
                                write_needed = False
                        else:
                            # no cached artwork -> extraction needed
                            write_needed = True
                    except Exception:
                        logging.exception('Failed creating artwork directory %s', art_dir)
                        # If we can't create the directory, treat as no cache
                        out_path = None
                        write_needed = False

                # Only attempt embedded extraction when we determined we need to write
                if write_needed:
                    # Extract embedded artwork via Mutagen only when necessary
                    try:
                        from mutagen import File as MutagenFile
                        from mutagen.mp3 import MP3
                        from mutagen.flac import FLAC
                        from mutagen.mp4 import MP4
                        from mutagen.id3 import ID3, APIC
                        mf = MutagenFile(path)
                        imgdata = None
                        if mf is not None:
                            try:
                                if isinstance(mf, MP3):
                                    try:
                                        id3 = ID3(path)
                                        apics = id3.getall('APIC')
                                        if apics:
                                            imgdata = apics[0].data
                                    except Exception:
                                        imgdata = None
                                elif isinstance(mf, MP4):
                                    covr = mf.tags.get('covr') if mf.tags is not None else None
                                    if covr:
                                        imgdata = covr[0]
                                elif isinstance(mf, FLAC):
                                    pics = mf.pictures
                                    if pics:
                                        imgdata = pics[0].data
                                else:
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

                        if imgdata and h and out_path:
                            try:
                                with open(out_path, 'wb') as out:
                                    out.write(imgdata)
                            except Exception:
                                logging.exception('Failed writing artwork file for %s -> %s', path, out_path)
                                image_url = None
                            else:
                                image_url = f'/artwork/{h}.jpg'
                        else:
                            # No embedded artwork found or we don't have a cache key
                            image_url = None
                    except Exception:
                        logging.exception('Error extracting artwork for %s', path)
                        image_url = None
            except Exception:
                logging.exception('Error extracting artwork for %s', path if 'path' in locals() else '<unknown>')
                image_url = None
                # include a stable id for the current track (absolute path) to allow mapping lookups
                # (note: title/artist/album kept for compatibility)
            try:
                cur_id = path
            except Exception:
                cur_id = None
            return {'source':'local','id': cur_id,'title':title,'artist':artist,'album':album,'position_ms':position,'duration_ms':duration,'playing':playing,'image_url':image_url}
        except Exception:
            return {'source':'local','id': None,'title':None,'artist':None,'album':None,'position_ms':0,'duration_ms':0,'playing':False,'image_url':None}

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
        """Return list of available playlists under the base music directory and audiobooks directory.
        Directories and .m3u files are considered playlists. If a directory contains .m3u files
        (recursively), those playlist files are added instead of the directory itself.
        Returns list of absolute paths."""
        import os
        results = []
        
        def _find_m3u_files(directory):
            """Recursively find all .m3u files in a directory."""
            m3u_files = []
            try:
                for root, dirs, files in os.walk(directory):
                    for file in files:
                        if file.lower().endswith('.m3u'):
                            m3u_files.append(os.path.join(root, file))
            except Exception:
                pass
            return m3u_files
        
        def _process_directory(base_dir):
            """Process a directory and add playlists or directories to results."""
            if not os.path.isdir(base_dir):
                return
            
            for entry in sorted(os.listdir(base_dir)):
                path = os.path.join(base_dir, entry)
                
                if os.path.isfile(path) and entry.lower().endswith('.m3u'):
                    # Add m3u files directly
                    results.append(path)
                elif os.path.isdir(path):
                    # Check if directory contains any m3u files recursively
                    m3u_files = _find_m3u_files(path)
                    if m3u_files:
                        # Add the m3u files instead of the directory
                        results.extend(sorted(m3u_files))
                    else:
                        # No m3u files found, add the directory itself
                        results.append(path)
        
        # Search in base music directory
        _process_directory(self.base)
        
        # Also search in audiobooks directory
        try:
            # __file__ is app/player/local_player.py; audiobooks is at repo root
            audiobooks_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'audiobooks'))
            _process_directory(audiobooks_dir)
        except Exception:
            # If audiobooks directory doesn't exist or can't be accessed, just skip it
            pass
        
        return results

    def get_playlist_items(self, playlist_id):
        """Return an ordered list of tracks for the given playlist_id.
        For local playlists this returns a list of dicts: {'id': <abs path>, 'title': <basename>}.
        Supports directories and .m3u files and resolves file:// entries.
        """
        import os, re, urllib.parse
        def _resolve(entry, base_dir):
            try:
                e = entry.strip()
                if not e or e.startswith('#'):
                    return None
                if e.lower().startswith('file://'):
                    u = urllib.parse.urlparse(e)
                    p = urllib.parse.unquote(u.path)
                    if os.name == 'nt' and re.match(r'^/[A-Za-z]:', p):
                        p = p[1:]
                    if not os.path.isabs(p):
                        p = os.path.join(base_dir, p)
                    return p if os.path.exists(p) else None
                if not os.path.isabs(e):
                    cand = os.path.join(base_dir, e)
                else:
                    cand = e
                return cand if os.path.exists(cand) else None
            except Exception:
                return None

        # resolve relative playlist_id against base
        if not os.path.isabs(playlist_id):
            candidate = os.path.join(self.base, playlist_id)
            if os.path.exists(candidate):
                playlist_id = candidate

        items = []
        if os.path.isdir(playlist_id):
            # prefer an .m3u file if present
            m3us = [fn for fn in sorted(os.listdir(playlist_id)) if fn.lower().endswith('.m3u')]
            if m3us:
                m3u_path = os.path.join(playlist_id, m3us[0])
                try:
                    with open(m3u_path, 'r', encoding='utf-8') as f:
                        base = os.path.dirname(m3u_path)
                        for line in f:
                            p = _resolve(line, base)
                            if p:
                                items.append({'id': os.path.abspath(p), 'title': os.path.basename(p)})
                except Exception:
                    for fn in sorted(os.listdir(playlist_id)):
                        path = os.path.join(playlist_id, fn)
                        if os.path.isfile(path):
                            items.append({'id': os.path.abspath(path), 'title': os.path.basename(path)})
            else:
                for fn in sorted(os.listdir(playlist_id)):
                    path = os.path.join(playlist_id, fn)
                    if os.path.isfile(path):
                        items.append({'id': os.path.abspath(path), 'title': os.path.basename(path)})
        elif os.path.isfile(playlist_id) and playlist_id.lower().endswith('.m3u'):
            try:
                with open(playlist_id, 'r', encoding='utf-8') as f:
                    base = os.path.dirname(playlist_id)
                    for line in f:
                        p = _resolve(line, base)
                        if p:
                            items.append({'id': os.path.abspath(p), 'title': os.path.basename(p)})
            except Exception:
                pass
        return items

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
