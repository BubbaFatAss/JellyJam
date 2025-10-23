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

    def _clear(self):
        self.media_list = self.instance.media_list_new()
        self.player.set_media_list(self.media_list)

    def play_playlist(self, playlist_id):
        # playlist_id may be a directory path or an m3u file
        self._clear()
        # if a relative path is provided, try resolving it against base
        if not os.path.isabs(playlist_id):
            candidate = os.path.join(self.base, playlist_id)
            if os.path.exists(candidate):
                playlist_id = candidate
        if os.path.isdir(playlist_id):
            for fn in sorted(os.listdir(playlist_id)):
                path = os.path.join(playlist_id, fn)
                if os.path.isfile(path):
                    m = self.instance.media_new(path)
                    self.media_list.add_media(m)
        elif os.path.isfile(playlist_id) and playlist_id.lower().endswith('.m3u'):
            with open(playlist_id, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    if os.path.isabs(line) and os.path.exists(line):
                        m = self.instance.media_new(line)
                        self.media_list.add_media(m)
        else:
            print('Unknown playlist:', playlist_id)
            return
        self.player.set_media_list(self.media_list)
        self.player.play()

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
