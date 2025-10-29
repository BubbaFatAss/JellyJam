from flask import Flask, render_template, request, redirect, url_for, jsonify, send_from_directory
from flask import session
from .player.player import Player
from .nfc.reader import NFCReader
from .storage import Storage
import os
import json
try:
    from flask_socketio import SocketIO
    _HAVE_SOCKETIO = True
except Exception:
    SocketIO = None
    _HAVE_SOCKETIO = False

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET', 'dev-secret')
socketio = SocketIO(app, cors_allowed_origins='*') if _HAVE_SOCKETIO else None
data_dir = os.path.join(os.path.dirname(__file__), '..', 'data')
os.makedirs(data_dir, exist_ok=True)
storage = Storage(os.path.join(data_dir, 'config.json'))

player = Player(storage)
nfc = NFCReader(callback=player.handle_nfc)

# Helper: normalize track identifiers (especially local file paths) before hashing.
import hashlib, urllib.parse, re
def _canonical_track_id(s):
    """Return a canonical string for a track id so hashing is stable across
    places that may represent the same local file differently (file:// URLs,
    forward/back slashes, case differences on Windows). Non-file ids are
    returned unchanged.
    """
    try:
        if not s:
            return s
        # handle explicit file:// URLs
        if isinstance(s, str) and s.lower().startswith('file://'):
            u = urllib.parse.urlparse(s)
            p = urllib.parse.unquote(u.path)
            if os.name == 'nt' and re.match(r'^/[A-Za-z]:', p):
                p = p[1:]
            p = os.path.abspath(p)
            return os.path.normcase(os.path.normpath(p))

        # If it looks like a filesystem path or contains path separators, try
        # to normalize it to an absolute, normalized path. If that fails, fall
        # back to returning the original id (likely a Spotify id/uri).
        if isinstance(s, str) and (os.path.isabs(s) or os.path.exists(s) or os.path.sep in s or (os.name == 'nt' and ':' in s)):
            try:
                p = s
                # strip a leading file:/// form if present
                if p.lower().startswith('file:///'):
                    p = urllib.parse.unquote(urllib.parse.urlparse(p).path)
                    if os.name == 'nt' and re.match(r'^/[A-Za-z]:', p):
                        p = p[1:]
                p = os.path.abspath(p)
                return os.path.normcase(os.path.normpath(p))
            except Exception:
                return s

        return s
    except Exception:
        return s

def _hash_id(s):
    try:
        key = _canonical_track_id(s) or ''
        return hashlib.sha1(key.encode('utf-8')).hexdigest()
    except Exception:
        return None

# Special marker that, when used as the 'animation' value for a track,
# instructs the server to stop any running animation and clear the matrix
# to all black for that track.
STOP_ANIMATION_MARKER = '__STOP__'

# Thread-safety and duplicate-suppression for animation playback
import threading, time
_animation_lock = threading.Lock()
# record last played animation name and timestamp to suppress rapid duplicates
_last_animation = {'name': None, 'started_at': 0.0}
# how many seconds to ignore repeated plays of the same animation
_DUPLICATE_SUPPRESSION_SEC = float(os.environ.get('ANIMATION_DUP_SUPPRESS_SEC', '0.5'))

def _play_animation_safe(fname, loop=False, speed=1.0):
    """Start or stop an animation in a thread-safe way.

    - If fname is STOP_ANIMATION_MARKER, stop and clear the matrix.
    - If the same filename was started less than _DUPLICATE_SUPPRESSION_SEC ago,
      the call is ignored to avoid thrash from concurrent triggers.
    """
    if matrix is None:
        return
    try:
        now = time.time()
        with _animation_lock:
            # Suppress repeated starts of the same animation in quick succession
            last = _last_animation.get('name')
            last_t = _last_animation.get('started_at', 0.0)
            if fname and last == fname and (now - last_t) < _DUPLICATE_SUPPRESSION_SEC:
                return

            # If STOP marker, just stop and clear
            if fname == STOP_ANIMATION_MARKER:
                try:
                    matrix.stop_animation()
                except Exception:
                    pass
                try:
                    matrix.set_pixels(['#000000'] * (matrix.width * matrix.height), bypass_overlay=True)
                except Exception:
                    pass
                _last_animation['name'] = fname
                _last_animation['started_at'] = now
                return

            # path resolution
            path = os.path.join(animations_dir or '', fname)
            if not fname or not os.path.exists(path):
                return

            # stop any previous animation before starting the new one
            try:
                matrix.stop_animation()
            except Exception:
                pass

            if fname.lower().endswith('.gif'):
                # respect provided speed and loop
                try:
                    matrix.play_animation_from_gif(path, speed=speed, loop=loop)
                except Exception:
                    pass
            elif fname.lower().endswith('.json') and hasattr(matrix, 'play_wled_json'):
                try:
                    matrix.play_wled_json(path)
                except Exception:
                    pass

            _last_animation['name'] = fname
            _last_animation['started_at'] = now
    except Exception:
        pass


def _trigger_animation_for_current_track(async_run: bool = True):
    """Attempt to trigger the mapping animation for the currently playing track.
    If async_run is True, this will spawn a background thread and return immediately.
    """
    def _do():
        try:
            now = player.now_playing() or {}
            track_id = now.get('id')
            mapping_card = player._state.get('mapping_card')
            if not (mapping_card and track_id and matrix is not None):
                return
            cfg = storage.load()
            mapping = cfg.get('mappings', {}).get(mapping_card, {})
            stored = mapping.get('animations', {}) or {}
            h = _hash_id(track_id)
            assoc = stored.get(h)
            if not assoc:
                return
            fname = assoc.get('animation')
            loop = bool(assoc.get('loop', False))
            # centralize thread-safe play/stop logic and suppress rapid duplicates
            try:
                _play_animation_safe(fname, loop=loop, speed=1.0)
            except Exception:
                pass
        except Exception:
            pass

    if async_run:
        try:
            import threading
            threading.Thread(target=_do, daemon=True).start()
        except Exception:
            # fallback to sync
            _do()
    else:
        _do()


# Register a track-change callback on the player so playback events (local)
# can immediately trigger per-track animations and notify connected clients
# via Socket.IO. If socketio is not available, clients will fall back to
# polling /api/nowplaying.
try:
    def _on_track_change():
        try:
            _trigger_animation_for_current_track(async_run=True)
        except Exception:
            pass
        try:
            if socketio is not None:
                socketio.emit('nowplaying', player.now_playing() or {})
        except Exception:
            pass

    player.register_track_change_callback(_on_track_change)
except Exception:
    pass

# If Socket.IO is available, start a small broadcaster that emits frequent
# now-playing updates (position/progress) so clients can show a live progress
# bar. If Socket.IO is not present clients can poll /api/nowplaying instead.
try:
    if socketio is not None:
        def _start_nowplaying_broadcaster(interval_s=1.0):
            import threading, time

            def _broadcaster():
                while True:
                    try:
                        socketio.emit('nowplaying', player.now_playing() or {})
                    except Exception:
                        pass
                    try:
                        time.sleep(interval_s)
                    except Exception:
                        pass

            t = threading.Thread(target=_broadcaster, daemon=True)
            t.start()

        _start_nowplaying_broadcaster()
except Exception:
    pass

# Serve cached artwork saved under the data directory. Artwork is stored in
# <repo root>/data/artwork and served at /artwork/<filename> so it can be
# managed separately from package static files.
@app.route('/artwork/<path:filename>')
def artwork_file(filename):
    try:
        art_dir = os.path.join(data_dir, 'artwork')
        return send_from_directory(art_dir, filename)
    except Exception:
        return ('', 404)


@app.route('/settings', methods=['GET', 'POST'])
def settings():
    try:
        cfg = storage.load() or {}
    except Exception:
        cfg = {}
    disp = cfg.get('display', {}) if isinstance(cfg, dict) else {}
    def _validate_plugins(p: dict) -> list:
        """Validate plugins dict shape and return list of error messages (empty if ok)."""
        errs = []
        if not isinstance(p, dict):
            errs.append('Plugins JSON must be an object with plugin keys')
            return errs
        # ws2812 validations
        ws = p.get('ws2812')
        if ws is not None:
            if not isinstance(ws, dict):
                errs.append('ws2812 config must be an object')
            else:
                w = ws.get('width')
                h = ws.get('height')
                if w is not None:
                    try:
                        iw = int(w)
                        if iw < 1 or iw > 512:
                            errs.append('ws2812 width must be 1-512')
                    except Exception:
                        errs.append('ws2812 width must be an integer')
                if h is not None:
                    try:
                        ih = int(h)
                        if ih < 1 or ih > 512:
                            errs.append('ws2812 height must be 1-512')
                    except Exception:
                        errs.append('ws2812 height must be an integer')
        # rgbmatrix validations
        rg = p.get('rgbmatrix')
        if rg is not None:
            if not isinstance(rg, dict):
                errs.append('rgbmatrix config must be an object')
            else:
                for k in ('rows', 'cols'):
                    if k in rg and rg[k] is not None:
                        try:
                            v = int(rg[k])
                            if v < 8 or v > 2048:
                                errs.append(f'rgbmatrix {k} must be 8-2048')
                        except Exception:
                            errs.append(f'rgbmatrix {k} must be an integer')
                for k in ('chain', 'parallel'):
                    if k in rg and rg[k] is not None:
                        try:
                            v = int(rg[k])
                            if v < 1 or v > 64:
                                errs.append(f'rgbmatrix {k} must be 1-64')
                        except Exception:
                            errs.append(f'rgbmatrix {k} must be an integer')
                if 'gpio_slowdown' in rg and rg.get('gpio_slowdown') is not None:
                    try:
                        v = int(rg.get('gpio_slowdown'))
                        if v < 0 or v > 100:
                            errs.append('rgbmatrix gpio_slowdown must be 0-100')
                    except Exception:
                        errs.append('rgbmatrix gpio_slowdown must be an integer')
        return errs

    if request.method == 'POST':
        active = request.form.get('active') or 'ws2812'
        # If advanced JSON editor provided, prefer it
        plugins = {}
        plugins_json_raw = request.form.get('plugins_json', '').strip()
        if plugins_json_raw:
            try:
                p = json.loads(plugins_json_raw)
                if isinstance(p, dict):
                    # validate JSON structure
                    verrs = _validate_plugins(p)
                    if verrs:
                        # render template with error messages
                        err = '; '.join(verrs)
                        return render_template('settings.html', display_cfg=disp, saved=False, plugins_json=plugins_json_raw, error=err)
                    plugins = p
            except Exception:
                # fall through to structured parsing if JSON invalid
                return render_template('settings.html', display_cfg=disp, saved=False, plugins_json=plugins_json_raw, error='Invalid JSON in Advanced editor')
        # Build structured plugin config from form fields (only if plugins empty)
        if not plugins:
            # WS2812 fields
            try:
                ws_w = request.form.get('ws_width', '').strip()
                ws_h = request.form.get('ws_height', '').strip()
                if ws_w or ws_h or request.form.get('ws_serpentine'):
                    ws_cfg = {}
                    if ws_w:
                        try:
                            ws_cfg['width'] = int(ws_w)
                        except Exception:
                            pass
                    if ws_h:
                        try:
                            ws_cfg['height'] = int(ws_h)
                        except Exception:
                            pass
                    if request.form.get('ws_serpentine'):
                        ws_cfg['serpentine'] = True
                    plugins['ws2812'] = ws_cfg
            except Exception:
                pass

            # RGB Matrix fields
            try:
                rgb_rows = request.form.get('rgb_rows', '').strip()
                rgb_cols = request.form.get('rgb_cols', '').strip()
                rgb_chain = request.form.get('rgb_chain', '').strip()
                rgb_parallel = request.form.get('rgb_parallel', '').strip()
                rgb_gpio = request.form.get('rgb_gpio_slowdown', '').strip()
                if rgb_rows or rgb_cols or rgb_chain or rgb_parallel or rgb_gpio:
                    r_cfg = {}
                    if rgb_rows:
                        try:
                            r_cfg['rows'] = int(rgb_rows)
                        except Exception:
                            pass
                    if rgb_cols:
                        try:
                            r_cfg['cols'] = int(rgb_cols)
                        except Exception:
                            pass
                    if rgb_chain:
                        try:
                            r_cfg['chain'] = int(rgb_chain)
                        except Exception:
                            pass
                    if rgb_parallel:
                        try:
                            r_cfg['parallel'] = int(rgb_parallel)
                        except Exception:
                            pass
                    if rgb_gpio:
                        try:
                            r_cfg['gpio_slowdown'] = int(rgb_gpio)
                        except Exception:
                            pass
                    plugins['rgbmatrix'] = r_cfg
            except Exception:
                pass

        # validate structured result before saving
        verrs = _validate_plugins(plugins)
        if verrs:
            return render_template('settings.html', display_cfg=disp, saved=False, plugins_json=json.dumps(plugins, indent=2), error='; '.join(verrs))

        cfg['display'] = {'active': active, 'plugins': plugins}
        try:
            storage.save(cfg)
        except Exception:
            pass
        # apply new selection live if possible
        try:
            if matrix is not None:
                # DisplayManager exposes set_active_plugin
                try:
                    matrix.set_active_plugin(active, plugins.get(active, {}))
                except Exception:
                    pass
                if socketio is not None:
                    def _broadcast_pixels(pix):
                        try:
                            socketio.emit('display_update', {'width': matrix.width, 'height': matrix.height, 'pixels': pix})
                        except Exception:
                            pass
                    try:
                        matrix.set_on_update(_broadcast_pixels)
                    except Exception:
                        pass
        except Exception:
            pass
        return redirect(url_for('settings', saved=1))

    saved_flag = (request.args.get('saved') == '1')
    # provide plugins JSON for advanced editor
    try:
        plugins_json = json.dumps(disp.get('plugins', {}), indent=2)
    except Exception:
        plugins_json = '{}'
    return render_template('settings.html', display_cfg=disp, saved=saved_flag, plugins_json=plugins_json)

# create LED matrix mirror (in-memory buffer, hardware-backed when available)
try:
    # Use new display manager which supports multiple plugin backends.
    from .hardware.display_manager import create_matrix
    MATRIX_W = int(os.environ.get('LED_WIDTH', '16'))
    MATRIX_H = int(os.environ.get('LED_HEIGHT', '16'))
    # pass storage so the display manager can load active plugin/config from storage
    matrix = create_matrix(MATRIX_W, MATRIX_H, storage=storage)
    # ensure matrix has a sensible initial brightness (0-100)
    try:
        initial_b = int(os.environ.get('LED_BRIGHTNESS_PERCENT', str(int(os.environ.get('LED_BRIGHTNESS','64')) * 100 // 255)))
    except Exception:
        initial_b = 25
    try:
        matrix.set_brightness(initial_b)
    except Exception:
        pass
    # mode for second rotary encoder: 'skip' or 'brightness'
    rotary2_mode = 'skip'
    # if socketio available, register a notifier so updates are pushed to clients
    try:
        if socketio is not None:
            def _broadcast_pixels(pix):
                try:
                    socketio.emit('display_update', {'width': matrix.width, 'height': matrix.height, 'pixels': pix})
                except Exception:
                    pass
            matrix._on_update = _broadcast_pixels
    except Exception:
        pass
    # Play a small startup animation if requested (default: enabled)
    try:
        play_startup = os.environ.get('PLAY_STARTUP_ANIMATION', '1')
        if str(play_startup).lower() in ('1', 'true', 'yes', 'on'):
            startup_name = os.environ.get('STARTUP_ANIMATION_NAME', 'startup.gif')
            startup_speed = float(os.environ.get('STARTUP_ANIMATION_SPEED', '1.0') or 1.0)
            startup_loop = False
            # ensure the app static animations dir exists and place the startup
            # animation there so it cannot be overwritten/deleted through the
            # web UI (web UI manages files under data/animations).
            try:
                static_animations_dir = os.path.join(os.path.dirname(__file__), 'static', 'animations')
                os.makedirs(static_animations_dir, exist_ok=True)
            except Exception:
                # fallback to the package static folder
                static_animations_dir = os.path.join(os.path.dirname(__file__), 'static')
            startup_path = os.path.join(static_animations_dir, startup_name)
            # If no startup GIF found, try to generate a small placeholder using Pillow
            if not os.path.exists(startup_path):
                try:
                    from PIL import Image, ImageDraw
                    w = int(os.environ.get('LED_WIDTH', '16'))
                    h = int(os.environ.get('LED_HEIGHT', '16'))
                    frames = []
                    # create 6 frames with a simple sweeping dot
                    for i in range(6):
                        im = Image.new('RGB', (w, h), (0, 0, 0))
                        draw = ImageDraw.Draw(im)
                        x = i % w
                        y = (i * 3) % h
                        # draw a colored dot
                        draw.rectangle([x, y, x, y], fill=(255, 255, 255))
                        frames.append(im.resize((w, h), Image.NEAREST))
                    # save animated GIF
                    frames[0].save(startup_path, save_all=True, append_images=frames[1:], duration=120, loop=0)
                except Exception:
                    # failed to generate placeholder; ignore
                    pass
            # play it once (non-blocking)
            try:
                if os.path.exists(startup_path) and hasattr(matrix, 'play_animation_from_gif'):
                    matrix.play_animation_from_gif(startup_path, speed=startup_speed, loop=startup_loop)
            except Exception:
                pass
    except Exception:
        pass
except Exception:
    matrix = None

# ensure animations dir exists
try:
    animations_dir = os.path.join(data_dir, 'animations')
    os.makedirs(animations_dir, exist_ok=True)
except Exception:
    animations_dir = None

# restore last volume persisted in storage at startup (best-effort)
try:
    cfg = storage.load()
    last_vol = cfg.get('last_volume')
    if last_vol is not None:
        try:
            player.set_volume(int(last_vol))
        except Exception:
            pass
except Exception:
    pass


# Optional rotary encoder support: if ROTARY_A_PIN and ROTARY_B_PIN are set
# we'll create a background reader that calls back with detent deltas.
try:
    import os
    a_pin = os.environ.get('ROTARY_A_PIN')
    b_pin = os.environ.get('ROTARY_B_PIN')
    if a_pin and b_pin:
        try:
            from .hardware.rotary import create_rotary

            # optional button for volume encoder
            bbtn = os.environ.get('ROTARY_BUTTON_PIN')

            def _rotary_volume_delta(delta):
                try:
                    cur = player.get_volume()
                    if cur is None:
                        cfg = storage.load()
                        cur = cfg.get('last_volume') or 50
                    newv = int(max(0, min(100, int(cur) + int(delta))))
                    player.set_volume(newv)
                    # persist last set volume
                    try:
                        cfg = storage.load()
                        cfg['last_volume'] = newv
                        storage.save(cfg)
                    except Exception:
                        pass
                except Exception:
                    pass

            def _rotary_volume_button():
                # toggle play / pause
                try:
                    playing = player._state.get('playing')
                    if playing:
                        player.pause()
                    else:
                        player.play()
                except Exception:
                    pass

            rotary = create_rotary(int(a_pin), int(b_pin), _rotary_volume_delta, button_pin=(int(bbtn) if bbtn else None), button_callback=(_rotary_volume_button if bbtn else None))
            rotary.start()
        except Exception:
            # don't fail startup if rotary cannot be started
            pass
    # support a second encoder for skipping tracks
    try:
        a2 = os.environ.get('ROTARY2_A_PIN')
        b2 = os.environ.get('ROTARY2_B_PIN')
        btn2 = os.environ.get('ROTARY2_BUTTON_PIN')
        if a2 and b2:
            from .hardware.rotary import create_rotary

            def _rotary_skip_delta(delta):
                try:
                    # Behavior depends on current mode: 'skip' -> next/previous; 'brightness' -> adjust matrix brightness
                    try:
                        mode = rotary2_mode
                    except NameError:
                        mode = 'skip'
                    if mode == 'brightness':
                        if matrix is None:
                            return
                        try:
                            cur = matrix.get_brightness()
                            newp = int(max(0, min(100, int(cur) + int(delta))))
                            matrix.set_brightness(newp)
                        except Exception:
                            pass
                    else:
                        # positive -> next, negative -> previous
                        if delta > 0:
                            for _ in range(abs(delta)):
                                player.next()
                        elif delta < 0:
                            for _ in range(abs(delta)):
                                player.previous()
                except Exception:
                    pass

            def _rotary_skip_button():
                # toggle the mode between 'skip' and 'brightness'
                global rotary2_mode
                try:
                    rotary2_mode = 'brightness' if rotary2_mode != 'brightness' else 'skip'
                except Exception:
                    pass
                try:
                    if socketio is not None:
                        socketio.emit('rotary2_mode', {'mode': rotary2_mode})
                except Exception:
                    pass

            rotary2 = create_rotary(int(a2), int(b2), _rotary_skip_delta, button_pin=(int(btn2) if btn2 else None), button_callback=(_rotary_skip_button if btn2 else None))
            rotary2.start()
    except Exception:
        pass
except Exception:
    pass


@app.route('/')
def index():
    cfg = storage.load()
    return render_template('home.html', config=cfg)


@app.route('/spotify', methods=['GET', 'POST'])
def spotify_config():
    if request.method == 'POST':
        data = request.form.to_dict()
        cfg = storage.load()
        cfg.setdefault('spotify', {}).update(data)
        storage.save(cfg)
        return redirect(url_for('spotify_config'))
    cfg = storage.load()
    return render_template('spotify.html', config=cfg)


@app.route('/spotify/connect')
def spotify_connect():
    cfg = storage.load().get('spotify', {})
    client_id = cfg.get('client_id')
    client_secret = cfg.get('client_secret')
    redirect_uri = cfg.get('redirect_uri')
    if not (client_id and client_secret and redirect_uri):
        return redirect(url_for('spotify_config'))
    from spotipy.oauth2 import SpotifyOAuth
    sp_oauth = SpotifyOAuth(client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri, scope='user-modify-playback-state user-read-playback-state')
    auth_url = sp_oauth.get_authorize_url()
    # store state in session
    session['sp_state'] = sp_oauth.cache_handler.get_cached_token() if hasattr(sp_oauth, 'cache_handler') else None
    return redirect(auth_url)


@app.route('/spotify/callback')
def spotify_callback():
    code = request.args.get('code')
    state = request.args.get('state')
    cfg = storage.load().get('spotify', {})
    client_id = cfg.get('client_id')
    client_secret = cfg.get('client_secret')
    redirect_uri = cfg.get('redirect_uri')
    if not (client_id and client_secret and redirect_uri):
        return redirect(url_for('spotify_config'))
    from spotipy.oauth2 import SpotifyOAuth
    sp_oauth = SpotifyOAuth(client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri, scope='user-modify-playback-state user-read-playback-state')
    token_info = sp_oauth.get_access_token(code)
    # token_info may be dict with access_token/refresh_token/expires_at
    cfg_all = storage.load()
    cfg_all['spotify_token'] = token_info
    storage.save(cfg_all)
    return redirect(url_for('spotify_config'))


@app.route('/spotify/disconnect', methods=['POST'])
def spotify_disconnect():
    cfg_all = storage.load()
    if 'spotify_token' in cfg_all:
        del cfg_all['spotify_token']
        storage.save(cfg_all)
    return redirect(url_for('spotify_config'))


@app.route('/mappings', methods=['GET', 'POST'])
def mappings():
    if request.method == 'POST':
        cfg = storage.load()
        mappings = cfg.get('mappings', {})
        form = request.form
        card = form.get('card_id', '').strip()
        if not card:
            # Do not create mappings with empty card id
            # If AJAX, return JSON error
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes['application/json'] > request.accept_mimetypes['text/html']:
                return jsonify({'error': 'card_id required'}), 400
            return redirect(url_for('mappings'))
        playlist_id = form.get('playlist_id', '').strip()
        # optional shuffle checkbox and repeat select (off/context/track)
        shuffle = True if form.get('shuffle') in ('on', 'true', '1') else False
        repeat_mode = form.get('repeat', 'off') or 'off'
        map_type = form.get('type', 'local')
        # If local mapping requested but no playlists are available and no manual path provided, reject
        if map_type == 'local':
            available = player.local.list_playlists()
            if not available and not playlist_id:
                # nothing to map to; ignore request
                return redirect(url_for('mappings'))
        # optional volume override (0-100) - empty means no override
        vol_raw = form.get('volume', '').strip()
        volume = None
        if vol_raw != '':
            try:
                v = int(vol_raw)
                v = max(0, min(100, v))
                volume = v
            except Exception:
                volume = None
        # preserve existing per-track animations when editing an existing mapping
        prev = mappings.get(card, {})
        preserved = prev.get('animations') if isinstance(prev, dict) else None
        new_map = {'type': map_type, 'id': playlist_id, 'shuffle': shuffle, 'repeat': repeat_mode, 'volume': volume}
        if preserved:
            new_map['animations'] = preserved
        mappings[card] = new_map
        cfg['mappings'] = mappings
        storage.save(cfg)
        # If request was AJAX, return JSON to avoid relying on redirects
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes['application/json'] > request.accept_mimetypes['text/html']:
            return jsonify({'ok': True, 'mapping': mappings[card]})
        return redirect(url_for('mappings'))
    cfg = storage.load()
    return render_template('mappings.html', config=cfg)


@app.route('/mappings/delete', methods=['POST'])
def mappings_delete():
    card = request.form.get('card_id')
    if not card:
        return redirect(url_for('mappings'))
    cfg = storage.load()
    mappings = cfg.get('mappings', {})
    if card in mappings:
        del mappings[card]
        cfg['mappings'] = mappings
        storage.save(cfg)
    return redirect(url_for('mappings'))


@app.route('/mappings/erase', methods=['POST'])
def mappings_erase():
    cfg = storage.load()
    if 'mappings' in cfg:
        cfg['mappings'] = {}
        storage.save(cfg)
    return redirect(url_for('mappings'))


@app.route('/simulate', methods=['GET','POST'])
def simulate():
    if request.method == 'POST':
        card = request.json.get('card_id')
        if not card:
            return jsonify({'error': 'card_id required'}), 400
        # simulate calling NFC callback
        player.handle_nfc(card)
        # Try to immediately trigger any per-track animation for this mapping so
        # web UI-initiated mappings start their animations without waiting for
        # the background poller interval.
        try:
            def _try_trigger():
                try:
                    now = player.now_playing() or {}
                    track_id = now.get('id')
                    mapping_card = player._state.get('mapping_card')
                    if mapping_card and track_id and matrix is not None:
                        h = _hash_id(track_id)
                        cfg = storage.load()
                        mapping = cfg.get('mappings', {}).get(mapping_card, {})
                        stored = mapping.get('animations', {}) or {}
                        assoc = stored.get(h)
                        if assoc:
                            fname = assoc.get('animation')
                            loop = bool(assoc.get('loop', False))
                            try:
                                _play_animation_safe(fname, loop=loop, speed=1.0)
                            except Exception:
                                pass
                except Exception:
                    pass
            # run in background so simulate returns quickly
            import threading
            threading.Thread(target=_try_trigger, daemon=True).start()
        except Exception:
            pass
        return jsonify({'ok': True})
    # GET -> render simulate page
    return render_template('simulate.html')


@app.route('/status')
def status():
    return jsonify(player.status())


@app.route('/api/playlists')
def api_playlists():
    # Return local playlists list (directories and m3u files) as JSON
    pls = player.local.list_playlists()
    # Convert to friendly relative names when possible
    friendly = []
    import os
    base = player.local.base
    for p in pls:
        try:
            rel = os.path.relpath(p, base)
        except Exception:
            rel = p
        friendly.append({'path': p, 'name': rel})
    return jsonify({'playlists': friendly, 'music_base': base})


@app.route('/display')
def display_page():
    cfg = storage.load()
    return render_template('display.html', config=cfg)


@app.route('/api/display')
def api_display():
    # return current LED matrix buffer
    try:
        if matrix is None:
            return jsonify({'width': 0, 'height': 0, 'pixels': []})
        pix = matrix.get_pixels()
        return jsonify({'width': matrix.width, 'height': matrix.height, 'pixels': pix})
    except Exception:
        return jsonify({'width': 0, 'height': 0, 'pixels': []})


@app.route('/api/animations')
def api_animations_list():
    try:
        if not animations_dir:
            return jsonify({'animations': []})
        files = []
        import os
        for fn in os.listdir(animations_dir):
            # include GIFs and WLED-style JSON exports
            if fn.lower().endswith('.gif') or fn.lower().endswith('.json'):
                files.append(fn)
        return jsonify({'animations': files})
    except Exception:
        return jsonify({'animations': []})


@app.route('/api/animations/upload', methods=['POST'])
def api_animations_upload():
    if 'file' not in request.files:
        return jsonify({'error': 'file required'}), 400
    f = request.files['file']
    if f.filename == '':
        return jsonify({'error': 'filename required'}), 400
    # Accept GIF animations and WLED JSON exports
    fname = f.filename or ''
    if not (fname.lower().endswith('.gif') or fname.lower().endswith('.json')):
        return jsonify({'error': 'only .gif or .json files allowed'}), 400
    try:
        import werkzeug
        safe = werkzeug.utils.secure_filename(f.filename)
        dst = os.path.join(animations_dir, safe)
        # If JSON, do a lightweight validation to ensure it's valid JSON.
        # Accept WLED exports which may include leading comment lines (// ...) and
        # may contain multiple concatenated JSON objects. We will strip // lines
        # and attempt to parse one-or-more JSON objects from the file body.
        if safe.lower().endswith('.json'):
            try:
                import json, re
                # read full text
                raw = f.stream.read()
                # decode bytes to str if necessary
                if isinstance(raw, bytes):
                    try:
                        text = raw.decode('utf-8')
                    except Exception:
                        text = raw.decode('utf-8', errors='replace')
                else:
                    text = str(raw)
                # remove lines that are purely // comments (common in WLED exports)
                text = re.sub(r'^\s*//.*$', '', text, flags=re.MULTILINE)
                # try to parse one or more JSON objects from the text
                decoder = json.JSONDecoder()
                idx = 0
                objs = []
                text_len = len(text)
                while True:
                    # skip whitespace
                    while idx < text_len and text[idx].isspace():
                        idx += 1
                    if idx >= text_len:
                        break
                    try:
                        obj, end = decoder.raw_decode(text, idx)
                        objs.append(obj)
                        idx = end
                    except ValueError:
                        # failed to parse remaining text
                        break
                if len(objs) == 0:
                    return jsonify({'error': 'invalid json file', 'details': 'no JSON objects found'}), 400
                # Normalize and write a cleaned JSON file: single object if one, or an array if many.
                to_write = objs[0] if len(objs) == 1 else objs
                with open(dst, 'w', encoding='utf-8') as outf:
                    json.dump(to_write, outf, indent=2, ensure_ascii=False)
                return jsonify({'ok': True, 'filename': safe})
            except Exception as e:
                return jsonify({'error': 'invalid json file', 'details': str(e)}), 400
        # Non-JSON files: save directly
        f.save(dst)
        return jsonify({'ok': True, 'filename': safe})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/animations/play', methods=['POST'])
def api_animations_play():
    data = request.json or {}
    name = data.get('name')
    if not name:
        return jsonify({'error': 'name required'}), 400
    try:
        path = os.path.join(animations_dir, name)
        if not os.path.exists(path):
            return jsonify({'error': 'not found'}), 404
        speed = float(data.get('speed', 1.0))
        loop = bool(data.get('loop', True))
        if matrix is None:
            return jsonify({'error': 'matrix not available'}), 500
        # Support GIF animations and WLED JSON presets
        if name.lower().endswith('.gif'):
            try:
                _play_animation_safe(name, loop=loop, speed=speed)
            except Exception:
                pass
        elif name.lower().endswith('.json'):
            # try to parse and apply WLED JSON preset
            try:
                # prefer method name if available
                if hasattr(matrix, 'play_wled_json'):
                    try:
                        _play_animation_safe(name, loop=loop, speed=speed)
                    except Exception:
                        pass
                else:
                    # fallback: use helper parser from module if present
                    try:
                        from .hardware.ledmatrix import parse_wled_json_from_file
                        pix, bri = parse_wled_json_from_file(path, matrix.width * matrix.height)
                        # set pixels under lock to avoid concurrent changes
                        try:
                            with _animation_lock:
                                matrix.set_pixels(pix)
                                if bri is not None:
                                    try:
                                        matrix.set_brightness(int(bri))
                                    except Exception:
                                        pass
                                _last_animation['name'] = name
                                _last_animation['started_at'] = time.time()
                        except Exception:
                            pass
                    except Exception as e:
                        return jsonify({'error': 'failed to parse wled json', 'details': str(e)}), 500
            except Exception as e:
                return jsonify({'error': str(e)}), 500
        else:
            return jsonify({'error': 'unsupported file type'}), 400
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/animations/stop', methods=['POST'])
def api_animations_stop():
    try:
        if matrix is None:
            return jsonify({'ok': True})
        data = request.json or {}
        # optional clear flag to also blank the matrix after stopping
        if data.get('clear'):
            try:
                _play_animation_safe(STOP_ANIMATION_MARKER)
            except Exception:
                pass
        else:
            try:
                with _animation_lock:
                    matrix.stop_animation()
                    # clear last animation record so subsequent plays aren't suppressed
                    _last_animation['name'] = None
                    _last_animation['started_at'] = time.time()
            except Exception:
                pass
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/animations/delete', methods=['POST'])
def api_animations_delete():
    """Delete an uploaded animation file. Accepts JSON {name: <filename>} or form data 'name'.
    Uses secure_filename to avoid path traversal and only deletes files inside animations_dir.
    """
    try:
        data = request.json or request.form or {}
        name = data.get('name') if isinstance(data, dict) else None
        if not name:
            return jsonify({'error': 'name required'}), 400
        import werkzeug
        safe = werkzeug.utils.secure_filename(name)
        path = os.path.join(animations_dir, safe)
        if not os.path.exists(path):
            return jsonify({'error': 'not found'}), 404
        # only remove regular files
        if not os.path.isfile(path):
            return jsonify({'error': 'not a file'}), 400
        os.remove(path)
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/display/brightness', methods=['GET','POST'])
def api_display_brightness():
    try:
        if request.method == 'GET':
            if matrix is None:
                return jsonify({'brightness': 0})
            return jsonify({'brightness': matrix.get_brightness()})
        # POST
        data = request.json or {}
        b = data.get('brightness')
        try:
            b_i = int(b)
        except Exception:
            return jsonify({'error': 'invalid brightness'}), 400
        if matrix is None:
            return jsonify({'ok': True, 'brightness': b_i})
        matrix.set_brightness(b_i)
        return jsonify({'ok': True, 'brightness': b_i})
    except Exception:
        return jsonify({'error': 'internal'}), 500


@app.route('/api/display/volume_bar_duration', methods=['GET','POST'])
def api_display_volume_duration():
    try:
        if request.method == 'GET':
            cfg = storage.load()
            disp = cfg.get('display', {})
            ms = int(disp.get('volume_bar_duration_ms', 1500))
            color = disp.get('volume_bar_color', '#00FF00')
            mode = disp.get('volume_bar_mode', 'overlay')
            return jsonify({'volume_bar_duration_ms': ms, 'volume_bar_duration_s': ms/1000.0, 'volume_bar_color': color, 'volume_bar_mode': mode})
        # POST -> set duration in seconds (preferred) or ms
        data = request.json or {}
        sec = data.get('seconds')
        ms = data.get('ms')
        if sec is None and ms is None:
            return jsonify({'error': 'seconds or ms required'}), 400
        if sec is not None:
            try:
                ms_val = int(float(sec) * 1000)
            except Exception:
                return jsonify({'error': 'invalid seconds value'}), 400
        else:
            try:
                ms_val = int(ms)
            except Exception:
                return jsonify({'error': 'invalid ms value'}), 400
        cfg = storage.load()
        disp = cfg.get('display', {})
        disp['volume_bar_duration_ms'] = ms_val
        # optional color and mode in same POST
        col = data.get('color')
        mode = data.get('mode')
        if col is not None:
            try:
                s = str(col).strip()
                if not s.startswith('#'):
                    s = '#' + s
                # expand 3-char
                if len(s) == 4:
                    s = '#' + s[1]*2 + s[2]*2 + s[3]*2
                s = s.upper()
                disp['volume_bar_color'] = s
            except Exception:
                pass
        if mode is not None:
            try:
                m = str(mode).lower()
                if m in ('overlay', 'pause'):
                    disp['volume_bar_mode'] = m
            except Exception:
                pass
        cfg['display'] = disp
        storage.save(cfg)
        resp = {'ok': True, 'volume_bar_duration_ms': ms_val, 'volume_bar_duration_s': ms_val/1000.0, 'volume_bar_color': disp.get('volume_bar_color', '#00FF00'), 'volume_bar_mode': disp.get('volume_bar_mode', 'overlay')}
        return jsonify(resp)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/display/volume_bar_preview', methods=['POST'])
def api_display_volume_preview():
    """Trigger a one-off preview of the volume bar using provided or stored settings.

    Accepts JSON: { seconds: <float>, ms: <int>, color: '#RRGGBB', mode: 'overlay'|'pause', volume: 0-100 }
    Does not persist changes to config; just triggers matrix.show_volume_bar.
    """
    try:
        data = request.json or {}
        cfg = storage.load()
        disp = cfg.get('display', {})
        # duration
        sec = data.get('seconds')
        ms = data.get('ms')
        if sec is not None:
            try:
                dur_ms = int(float(sec) * 1000)
            except Exception:
                return jsonify({'error': 'invalid seconds'}), 400
        elif ms is not None:
            try:
                dur_ms = int(ms)
            except Exception:
                return jsonify({'error': 'invalid ms'}), 400
        else:
            dur_ms = int(disp.get('volume_bar_duration_ms', 1500))
        # color
        color = data.get('color') or disp.get('volume_bar_color', '#00FF00')
        # mode
        mode = data.get('mode') or disp.get('volume_bar_mode', 'overlay')
        # volume
        vol = data.get('volume')
        try:
            if vol is None:
                vol = 50
            vol_i = int(vol)
            vol_i = max(0, min(100, vol_i))
        except Exception:
            return jsonify({'error': 'invalid volume'}), 400

        if matrix is None or not hasattr(matrix, 'show_volume_bar'):
            return jsonify({'error': 'matrix not available'}), 500
        try:
            matrix.show_volume_bar(vol_i, dur_ms, color=color, mode=mode)
        except Exception as e:
            return jsonify({'error': str(e)}), 500
        return jsonify({'ok': True, 'volume': vol_i, 'duration_ms': dur_ms, 'color': color, 'mode': mode})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/rotary2/mode', methods=['GET'])
def api_rotary2_mode():
    try:
        return jsonify({'mode': rotary2_mode})
    except Exception:
        return jsonify({'mode': 'skip'})


@app.route('/api/socketio_client')
def api_socketio_client():
    """Return a recommended Socket.IO client CDN URL that matches the server python-socketio major version."""
    try:
        import socketio as pysio
        ver = getattr(pysio, '__version__', '5.0.0')
        major = int(str(ver).split('.')[0])
    except Exception:
        major = 5
    # map python-socketio major -> JS client major
    if major >= 5:
        client_major = "4.8.1"
    elif major == 4:
        client_major = "3.1.3"
    else:
        client_major = 2
    # provide a major-prefixed CDN URL; CDN will serve the major series
    url = f'https://cdn.socket.io/{client_major}/socket.io.min.js'
    return jsonify({'cdn': url})


if _HAVE_SOCKETIO:
    @socketio.on('connect')
    def _ws_connect():
        try:
            # send initial display buffer
            if matrix is not None:
                try:
                    socketio.emit('display_update', {'width': matrix.width, 'height': matrix.height, 'pixels': matrix.get_pixels()})
                except Exception:
                    pass
        except Exception:
            pass

        # Also send initial now-playing and rotary2 mode so clients get a full
        # initial state without polling.
        try:
            try:
                socketio.emit('nowplaying', player.now_playing() or {})
            except Exception:
                pass
            try:
                socketio.emit('rotary2_mode', {'mode': rotary2_mode})
            except Exception:
                pass
        except Exception:
            pass


@app.route('/api/mappings')
def api_mappings():
    cfg = storage.load()
    mappings = cfg.get('mappings', {})
    # For local mappings, try to provide friendly names using local base
    result = []
    for card, m in mappings.items():
        entry = {'card_id': card, 'type': m.get('type'), 'id': m.get('id'), 'shuffle': bool(m.get('shuffle')), 'repeat': m.get('repeat', 'off'), 'volume': m.get('volume')}
        if m.get('type') == 'local':
            try:
                base = player.local.base
                import os
                path = m.get('id') or ''
                # if absolute, derive relpath
                if os.path.isabs(path):
                    name = os.path.relpath(path, base)
                else:
                    name = path
                entry['name'] = name or path
            except Exception:
                entry['name'] = m.get('id')
        else:
            entry['name'] = m.get('id')
        result.append(entry)
    return jsonify({'mappings': result})


@app.route('/api/mappings/<card_id>/tracks')
def api_mapping_tracks(card_id):
    """Return list of tracks for a mapping (only available for local mappings or when Spotify is configured)."""
    cfg = storage.load()
    mappings = cfg.get('mappings', {})
    mapping = mappings.get(card_id)
    if not mapping:
        return jsonify({'error': 'mapping not found'}), 404
    try:
        if mapping.get('type') == 'local':
            items = player.local.get_playlist_items(mapping.get('id') or '')
            return jsonify({'tracks': items})
        else:
            # try to fetch spotify playlist tracks if available
            try:
                sp_items = []
                pl_id = mapping.get('id')
                # spotipy returns paging objects; use playlist_items helper
                resp = player.spotify._call_spotify('playlist_items', pl_id)
                if resp and isinstance(resp, dict):
                    for it in resp.get('items', []):
                        tr = it.get('track') or {}
                        tid = tr.get('id') or tr.get('uri') or tr.get('name')
                        title = tr.get('name') or tid
                        artists = ', '.join([a.get('name') for a in tr.get('artists', [])]) if tr.get('artists') else ''
                        display = title + (f' — {artists}' if artists else '')
                        sp_items.append({'id': tid, 'title': display})
                return jsonify({'tracks': sp_items})
            except Exception:
                return jsonify({'tracks': []})
    except Exception:
        return jsonify({'tracks': []})


@app.route('/api/mappings/<card_id>/animations', methods=['GET','POST'])
def api_mapping_animations(card_id):
    """GET -> return existing associations for mapping. POST -> set associations in batch.
    POST payload: { associations: { <track_id>: { animation: <filename|null>, loop: bool } } }
    """
    # use module _hash_id helper for stable canonicalization + hashing

    cfg = storage.load()
    mappings = cfg.get('mappings', {})
    mapping = mappings.get(card_id)
    if not mapping:
        return jsonify({'error': 'mapping not found'}), 404
    if request.method == 'GET':
        # mapping['animations'] is stored keyed by hashed track ids. We need to
        # present associations keyed by the original track ids returned by
        # /api/mappings/<card_id>/tracks for the editor.
        stored = mapping.get('animations', {}) or {}
        # fetch current tracks to map hashes back to ids
        tracks_resp = api_mapping_tracks(card_id)
        try:
            tracks = tracks_resp.get_json().get('tracks', [])
        except Exception:
            tracks = []
        out = {}
        for t in tracks:
            tid = t.get('id')
            if not tid:
                continue
            h = _hash_id(tid)
            if h and h in stored:
                out[tid] = stored.get(h)
        return jsonify({'animations': out})
    # POST -> update associations
    data = request.json or {}
    assocs = data.get('associations')
    if not isinstance(assocs, dict):
        return jsonify({'error': 'associations required'}), 400
    try:
        # ensure animations dict exists (stored keyed by hash)
        mapping.setdefault('animations', {})
        stored = mapping['animations']
        for tid, v in assocs.items():
            h = _hash_id(tid)
            if not h:
                continue
            if v is None or v.get('animation') is None:
                # remove association if present
                if h in stored:
                    del stored[h]
            else:
                stored[h] = {'animation': v.get('animation'), 'loop': bool(v.get('loop', False))}
        mappings[card_id] = mapping
        cfg['mappings'] = mappings
        storage.save(cfg)
        return jsonify({'ok': True, 'animations': mapping.get('animations', {})})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/options', methods=['GET','POST'])
def api_options():
    # GET -> return current effective options (shuffle/repeat)
    if request.method == 'GET':
        cfg = storage.load()
        return jsonify({'options': cfg.get('options', {})})
    # POST -> set persistent options (applies globally) or temporary when playing
    data = request.json or {}
    opts = {}
    if 'shuffle' in data:
        opts['shuffle'] = bool(data.get('shuffle'))
    if 'repeat' in data:
        opts['repeat'] = bool(data.get('repeat'))
    cfg = storage.load()
    cfg_opts = cfg.get('options', {})
    cfg_opts.update(opts)
    cfg['options'] = cfg_opts
    storage.save(cfg)
    return jsonify({'ok': True, 'options': cfg_opts})


@app.route('/api/apply_options', methods=['POST'])
def api_apply_options():
    data = request.json or {}
    # Apply options temporarily to the running player
    opts = {}
    if 'shuffle' in data:
        opts['shuffle'] = bool(data.get('shuffle'))
    if 'repeat' in data:
        opts['repeat'] = bool(data.get('repeat'))
    try:
        player.apply_options(opts)
    except Exception:
        pass
    return jsonify({'ok': True, 'applied': opts})


# /nowplaying route removed — Now Playing UI is part of the home page


@app.route('/api/nowplaying')
def api_now_playing():
    try:
        now = player.now_playing() or {}
        # Normalize fields so the UI can rely on stable keys and types.
        src = now.get('source') or None
        tid = now.get('id') or None
        title = now.get('title') or None
        artist = now.get('artist') or None
        album = now.get('album') or None
        # coerce numeric timing values to ints when possible
        try:
            pos = int(now.get('position_ms') or 0)
        except Exception:
            try:
                pos = int(float(now.get('position') or 0))
            except Exception:
                pos = 0
        try:
            dur = int(now.get('duration_ms') or 0)
        except Exception:
            try:
                dur = int(float(now.get('duration') or 0))
            except Exception:
                dur = 0
        playing = bool(now.get('playing'))
        # image_url may be a remote URL or a local /artwork/<name> path; ensure '' -> None
        img = now.get('image_url') if 'image_url' in now else now.get('image')
        if img is None or img == '' or img == -1:
            img = None

        # compute progress percent if duration available
        progress_pct = None
        try:
            if dur and dur > 0:
                progress_pct = int((pos / dur) * 100)
        except Exception:
            progress_pct = None

        resp = {
            'source': src,
            'id': tid,
            'title': title,
            'artist': artist,
            'album': album,
            'position_ms': pos,
            'duration_ms': dur,
            'playing': playing,
            'image_url': img,
            'progress_pct': progress_pct,
        }
        return jsonify(resp)
    except Exception:
        return jsonify({'source': None, 'position_ms': 0, 'duration_ms': 0, 'playing': False, 'image_url': None})


@app.route('/api/control', methods=['POST'])
def api_control():
    action = request.json.get('action')
    if action == 'play':
        player.play()
        # Try to trigger animation for current track immediately
        try:
            _trigger_animation_for_current_track()
        except Exception:
            pass
        return jsonify({'ok': True})
    if action == 'pause':
        player.pause(); return jsonify({'ok': True})
    if action == 'next':
        player.next()
        try:
            _trigger_animation_for_current_track()
        except Exception:
            pass
        return jsonify({'ok': True})
    if action == 'previous':
        player.previous()
        try:
            _trigger_animation_for_current_track()
        except Exception:
            pass
        return jsonify({'ok': True})
    if action == 'seek':
        pos = int(request.json.get('position_ms', 0))
        player.seek(pos); return jsonify({'ok': True})
    if action == 'stop':
        player.stop()
        # stop any running animation and clear display (thread-safe)
        try:
            if matrix is not None:
                try:
                    _play_animation_safe(STOP_ANIMATION_MARKER)
                except Exception:
                    pass
        except Exception:
            pass
        return jsonify({'ok': True})
    if action == 'volume':
        # expected payload: { volume: 0-100 }
        vol = request.json.get('volume')
        try:
            vol_i = int(vol)
            vol_i = max(0, min(100, vol_i))
        except Exception:
            return jsonify({'error': 'invalid volume'}), 400
        player.set_volume(vol_i)
        # persist last set volume
        try:
            cfg = storage.load()
            cfg['last_volume'] = vol_i
            storage.save(cfg)
        except Exception:
            pass
        # show a temporary volume bar on the LED matrix if available
        try:
            # get configured duration, color and mode from storage
            cfg = storage.load()
            disp_cfg = cfg.get('display', {})
            dur_ms = int(disp_cfg.get('volume_bar_duration_ms', 1500))
            color = disp_cfg.get('volume_bar_color', '#00FF00')
            mode = disp_cfg.get('volume_bar_mode', 'overlay')
            if matrix is not None and hasattr(matrix, 'show_volume_bar'):
                try:
                    matrix.show_volume_bar(vol_i, dur_ms, color=color, mode=mode)
                except Exception:
                    pass
        except Exception:
            pass
        return jsonify({'ok': True, 'volume': vol_i})
    return jsonify({'error': 'unknown action'}), 400


@app.route('/api/volume')
def api_volume():
    # return current volume for active source if available
    vol = player.get_volume()
    if vol is None:
        # fall back to last persisted volume if present
        cfg = storage.load()
        vol = cfg.get('last_volume')
    return jsonify({'volume': vol})


@app.route('/api/spotify/devices')
def api_spotify_devices():
    # return available devices and selected device id
    devs = []
    try:
        devs = player.spotify.list_devices()
    except Exception:
        devs = []
    cfg = storage.load()
    selected = cfg.get('spotify_selected_device')
    return jsonify({'devices': devs, 'selected': selected})


@app.route('/api/spotify/select', methods=['POST'])
def api_spotify_select():
    device_id = request.json.get('device_id')
    if not device_id:
        return jsonify({'error':'device_id required'}), 400
    cfg = storage.load()
    cfg['spotify_selected_device'] = device_id
    storage.save(cfg)
    return jsonify({'ok': True})


if __name__ == '__main__':
    if _HAVE_SOCKETIO and socketio is not None:
        socketio.run(app, host='0.0.0.0', port=5000)
    else:
        app.run(host='0.0.0.0', port=5000)

# Background poller to monitor playback changes (Spotify/local) and trigger per-track animations
def _start_playback_poller(interval_s=2.0):
    import threading, time, hashlib


    def _poller():
        last_track = None
        while True:
            try:
                now = player.now_playing() or {}
                track_id = now.get('id')
                mapping_card = player._state.get('mapping_card')
                if mapping_card and track_id and matrix is not None:
                    try:
                        cfg = storage.load()
                        mapping = cfg.get('mappings', {}).get(mapping_card, {})
                        stored = mapping.get('animations', {}) or {}
                        h = _hash_id(track_id)
                        assoc = stored.get(h)
                        if assoc and track_id != last_track:
                            fname = assoc.get('animation')
                            loop = bool(assoc.get('loop', False))
                            try:
                                _play_animation_safe(fname, loop=loop, speed=1.0)
                            except Exception:
                                pass
                            last_track = track_id
                        elif not assoc:
                            last_track = None
                    except Exception:
                        pass
                time.sleep(interval_s)
            except Exception:
                try:
                    time.sleep(interval_s)
                except Exception:
                    pass

    t = threading.Thread(target=_poller, daemon=True)
    t.start()

# start poller on module import so server-side animations trigger automatically
try:
    _start_playback_poller()
except Exception:
    pass
