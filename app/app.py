from flask import Flask, render_template, request, redirect, url_for, jsonify, send_from_directory
from flask import session
import os
import json
import logging

from utils.logging_config import (
    setup_logging, get_logger, get_recent_logs, set_log_level,
    update_file_logging, is_file_logging_enabled
)
from storage import Storage
from player.player import Player
# NFCReader import moved to initialization section for backward compat fallback

# Initialize logging with defaults first (before any storage access)
setup_logging(level=logging.INFO, buffer_capacity=1000)
log = get_logger(__name__)

try:
    from flask_socketio import SocketIO
    _HAVE_SOCKETIO = True
except Exception:
    SocketIO = None
    _HAVE_SOCKETIO = False

try:
    from flask_talisman import Talisman
    _HAVE_TALISMAN = True
except Exception:
    Talisman = None
    _HAVE_TALISMAN = False

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET', 'dev-secret')
socketio = SocketIO(app, cors_allowed_origins='*') if _HAVE_SOCKETIO else None
# Apply Flask-Talisman if available to add secure headers (CSP is disabled by default here)
try:
    if _HAVE_TALISMAN and Talisman is not None:
        # keep a permissive CSP by default to avoid breaking inline scripts/styles in templates
        Talisman(app, content_security_policy=None)
except Exception:
    pass

# NOW initialize storage and load config
data_dir = os.path.join(os.path.dirname(__file__), '..', 'data')
os.makedirs(data_dir, exist_ok=True)
storage = Storage(os.path.join(data_dir, 'config.json'))

# Update logging level from config if available
try:
    cfg = storage.load() or {}
    logging_cfg = cfg.get('logging', {})
    if logging_cfg:
        log_level_name = logging_cfg.get('log_level', 'INFO')
        log_level = getattr(logging, log_level_name, logging.INFO)
        buffer_capacity = logging_cfg.get('buffer_capacity', 1000)
        
        # File logging settings
        enable_file_logging = logging_cfg.get('enable_file_logging', False)
        max_log_files = logging_cfg.get('max_log_files', 5)
        max_log_size_mb = logging_cfg.get('max_log_size_mb', 10)
        
        # Update logging configuration
        set_log_level(log_level)
        from utils.logging_config import get_log_buffer
        buffer = get_log_buffer()
        if buffer:
            buffer.set_capacity(buffer_capacity)
        
        # Update file logging if configured
        if enable_file_logging:
            log_dir = os.path.join(data_dir, 'logs')
            update_file_logging(
                enable=True,
                log_file_dir=log_dir,
                max_log_files=max_log_files,
                max_log_size_mb=max_log_size_mb
            )
        
        log.info('JellyJam starting up with log level: %s', log_level_name)
        if enable_file_logging:
            log.info('File logging enabled: %s (max %d files, %d MB each)', 
                    log_dir, max_log_files, max_log_size_mb)
    else:
        log.info('JellyJam starting up with log level: INFO (default)')
except Exception as e:
    log.warning('Failed to load logging config from storage: %s', e)
    log.info('JellyJam starting up with log level: INFO (default)')

player = Player(storage)

# Initialize NFC reader using plugin manager
try:
    from nfc.reader_manager import create_nfc_reader
    nfc = create_nfc_reader(callback=player.handle_nfc, storage=storage)
    nfc.start()  # Start the NFC reader plugin
    log.info('NFC reader started: %s', nfc.get_plugin_name())
except Exception as e:
    log.error('Failed to initialize NFC reader manager: %s', e, exc_info=True)
    # Fall back to old NFCReader for backward compatibility
    log.warning('Falling back to legacy NFCReader')
    from nfc.reader import NFCReader
    nfc = NFCReader(callback=player.handle_nfc)
    nfc.start()

# Initialize nightlight
nightlight = None
mqtt_client = None
try:
    from hardware.nightlight import NightLight
    # Load lighting config from storage
    lighting_cfg_data = storage.load() or {}
    lighting_cfg = lighting_cfg_data.get('lighting', {})
    gpio_pin = lighting_cfg.get('gpio_pin')  # None means use default/env var
    led_count = lighting_cfg.get('led_count')  # None means use default/env var
    nightlight = NightLight(num_leds=led_count, gpio_pin=gpio_pin)
except Exception as e:
    log.warning('Nightlight initialization failed: %s', e)

# Initialize MQTT client if configured
def init_mqtt():
    """Initialize MQTT client with current config."""
    global mqtt_client, nightlight, matrix, socketio
    try:
        cfg = storage.load() or {}
        mqtt_cfg = cfg.get('mqtt', {})
        
        if mqtt_cfg.get('enabled'):
            from mqtt.mqtt_client import create_mqtt_client
            mqtt_client = create_mqtt_client(mqtt_cfg, nightlight, matrix, socketio)
            if mqtt_client:
                log.info('MQTT client initialized')
    except Exception as e:
        log.warning('MQTT initialization failed: %s', e)

# Note: MQTT initialization is deferred until after matrix is created (see below)

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
# how many seconds to ignore repeated plays of the same animation - load from config with env fallback
try:
    _cfg = storage.load() or {}
    _display_cfg = _cfg.get('display', {})
    _DUPLICATE_SUPPRESSION_SEC = float(_display_cfg.get('animation_dup_suppress_sec', os.environ.get('ANIMATION_DUP_SUPPRESS_SEC', '0.5')))
except Exception:
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
                # Validate pin
                if 'pin' in ws and ws['pin'] is not None:
                    try:
                        pin = int(ws['pin'])
                        if pin < 0 or pin > 40:
                            errs.append('ws2812 pin must be 0-40')
                    except Exception:
                        errs.append('ws2812 pin must be an integer')
                # Validate brightness
                if 'brightness' in ws and ws['brightness'] is not None:
                    try:
                        brightness = int(ws['brightness'])
                        if brightness < 0 or brightness > 255:
                            errs.append('ws2812 brightness must be 0-255')
                    except Exception:
                        errs.append('ws2812 brightness must be an integer')
                # Validate freq_hz
                if 'freq_hz' in ws and ws['freq_hz'] is not None:
                    try:
                        freq = int(ws['freq_hz'])
                        if freq < 400000 or freq > 1000000:
                            errs.append('ws2812 freq_hz must be 400000-1000000')
                    except Exception:
                        errs.append('ws2812 freq_hz must be an integer')
                # Validate DMA
                if 'dma' in ws and ws['dma'] is not None:
                    try:
                        dma = int(ws['dma'])
                        if dma < 0 or dma > 14:
                            errs.append('ws2812 dma must be 0-14')
                    except Exception:
                        errs.append('ws2812 dma must be an integer')
                # Validate channel
                if 'channel' in ws and ws['channel'] is not None:
                    try:
                        channel = int(ws['channel'])
                        if channel < 0 or channel > 1:
                            errs.append('ws2812 channel must be 0 or 1')
                    except Exception:
                        errs.append('ws2812 channel must be an integer')
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
                ws_pin = request.form.get('ws_pin', '').strip()
                ws_brightness = request.form.get('ws_brightness', '').strip()
                ws_freq_hz = request.form.get('ws_freq_hz', '').strip()
                ws_dma = request.form.get('ws_dma', '').strip()
                ws_channel = request.form.get('ws_channel', '').strip()
                ws_serpentine = request.form.get('ws_serpentine')
                ws_invert = request.form.get('ws_invert')
                
                if ws_w or ws_h or ws_pin or ws_brightness or ws_freq_hz or ws_dma or ws_channel or ws_serpentine or ws_invert:
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
                    if ws_pin:
                        try:
                            ws_cfg['pin'] = int(ws_pin)
                        except Exception:
                            pass
                    if ws_brightness:
                        try:
                            ws_cfg['brightness'] = int(ws_brightness)
                        except Exception:
                            pass
                    if ws_freq_hz:
                        try:
                            ws_cfg['freq_hz'] = int(ws_freq_hz)
                        except Exception:
                            pass
                    if ws_dma:
                        try:
                            ws_cfg['dma'] = int(ws_dma)
                        except Exception:
                            pass
                    if ws_channel:
                        try:
                            ws_cfg['channel'] = int(ws_channel)
                        except Exception:
                            pass
                    if ws_serpentine:
                        ws_cfg['serpentine'] = True
                    if ws_invert:
                        ws_cfg['invert'] = True
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

        # Get startup animation settings from form
        play_startup_animation = request.form.get('play_startup_animation') == 'on'
        startup_animation_name = request.form.get('startup_animation_name', 'startup.gif').strip()
        startup_animation_speed = request.form.get('startup_animation_speed', '1.0').strip()
        animation_dup_suppress_sec = request.form.get('animation_dup_suppress_sec', '0.5').strip()
        
        # Validate and convert startup animation settings
        try:
            startup_animation_speed = float(startup_animation_speed)
            if startup_animation_speed < 0.1 or startup_animation_speed > 10:
                startup_animation_speed = 1.0
        except (ValueError, TypeError):
            startup_animation_speed = 1.0
        
        try:
            animation_dup_suppress_sec = float(animation_dup_suppress_sec)
            if animation_dup_suppress_sec < 0 or animation_dup_suppress_sec > 10:
                animation_dup_suppress_sec = 0.5
        except (ValueError, TypeError):
            animation_dup_suppress_sec = 0.5

        cfg['display'] = {
            'active': active, 
            'plugins': plugins,
            'play_startup_animation': play_startup_animation,
            'startup_animation_name': startup_animation_name,
            'startup_animation_speed': startup_animation_speed,
            'animation_dup_suppress_sec': animation_dup_suppress_sec
        }
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
    
    # Get MQTT config
    mqtt_cfg = cfg.get('mqtt', {})
    
    # Get Lighting config
    lighting_cfg = cfg.get('lighting', {})
    
    # Get Logging config
    logging_cfg = cfg.get('logging', {})
    
    # Get Controls config
    controls_cfg = cfg.get('controls', {})
    
    # Get Local Music config
    local_music_cfg = cfg.get('local_music', {})
    
    # Get NFC config
    nfc_cfg = cfg.get('nfc', {})
    
    return render_template('settings.html', display_cfg=disp, saved=saved_flag, plugins_json=plugins_json, mqtt_cfg=mqtt_cfg, lighting_cfg=lighting_cfg, logging_cfg=logging_cfg, controls_cfg=controls_cfg, local_music_cfg=local_music_cfg, nfc_cfg=nfc_cfg)

@app.route('/api/artwork/info', methods=['GET'])
def artwork_info():
    """Get information about the artwork cache."""
    try:
        artwork_dir = os.path.join(data_dir, 'artwork')
        if not os.path.exists(artwork_dir):
            return jsonify({'file_count': 0, 'total_size_mb': 0})
        
        file_count = 0
        total_size = 0
        
        for root, dirs, files in os.walk(artwork_dir):
            for file in files:
                file_path = os.path.join(root, file)
                try:
                    total_size += os.path.getsize(file_path)
                    file_count += 1
                except Exception:
                    pass
        
        total_size_mb = total_size / (1024 * 1024)  # Convert to MB
        return jsonify({
            'file_count': file_count,
            'total_size_mb': total_size_mb
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/artwork/clear', methods=['POST'])
def clear_artwork_cache():
    """Clear all cached artwork files."""
    try:
        import shutil
        artwork_dir = os.path.join(data_dir, 'artwork')
        
        if os.path.exists(artwork_dir):
            # Remove all files in the artwork directory
            shutil.rmtree(artwork_dir)
            # Recreate the empty directory
            os.makedirs(artwork_dir, exist_ok=True)
            return jsonify({'success': True, 'message': 'Artwork cache cleared'})
        else:
            return jsonify({'success': True, 'message': 'No artwork cache to clear'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/settings/mqtt', methods=['POST'])
def save_mqtt_settings():
    """Save MQTT configuration settings."""
    try:
        cfg = storage.load() or {}
        
        mqtt_cfg = {
            'enabled': bool(request.form.get('mqtt_enabled')),
            'broker': request.form.get('mqtt_broker', '').strip(),
            'port': int(request.form.get('mqtt_port', 1883)),
            'username': request.form.get('mqtt_username', '').strip(),
            'password': request.form.get('mqtt_password', '').strip(),
            'topic': request.form.get('mqtt_topic', 'jellyjam').strip(),
            'discovery': bool(request.form.get('mqtt_discovery'))
        }
        
        cfg['mqtt'] = mqtt_cfg
        storage.save(cfg)
        
        # Restart MQTT client
        try:
            global mqtt_client
            if mqtt_client:
                mqtt_client.disconnect()
                mqtt_client = None
            init_mqtt()
        except Exception as e:
            log.error('Error restarting MQTT client: %s', e)
        
        return redirect(url_for('settings', saved=1))
    except Exception as e:
        return redirect(url_for('settings', error=str(e)))

@app.route('/settings/lighting', methods=['POST'])
def save_lighting_settings():
    """Save lighting hardware configuration."""
    try:
        cfg = storage.load() or {}
        
        gpio_pin = int(request.form.get('nightlight_pin', 18))
        led_count = int(request.form.get('nightlight_count', 30))
        
        # Validate inputs
        if gpio_pin < 0 or gpio_pin > 27:
            return redirect(url_for('settings', error='GPIO pin must be between 0 and 27'))
        
        if led_count < 1 or led_count > 1000:
            return redirect(url_for('settings', error='LED count must be between 1 and 1000'))
        
        lighting_cfg = {
            'gpio_pin': gpio_pin,
            'led_count': led_count
        }
        
        cfg['lighting'] = lighting_cfg
        storage.save(cfg)
        
        return redirect(url_for('settings', saved=1))
    except Exception as e:
        return redirect(url_for('settings', error=str(e)))

@app.route('/settings/logging', methods=['POST'])
def save_logging_settings():
    """Save logging configuration."""
    try:
        cfg = storage.load() or {}
        
        # Get form data
        log_level_name = request.form.get('log_level', 'INFO').upper()
        display_lines = int(request.form.get('display_lines', 100))
        buffer_capacity = int(request.form.get('buffer_capacity', 1000))
        
        # File logging settings
        enable_file_logging = request.form.get('enable_file_logging') == 'on'
        max_log_files = int(request.form.get('max_log_files', 5))
        max_log_size_mb = int(request.form.get('max_log_size_mb', 10))
        
        # Validate inputs
        valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        if log_level_name not in valid_levels:
            return redirect(url_for('settings', error=f'Invalid log level. Must be one of: {", ".join(valid_levels)}'))
        
        if display_lines < 10 or display_lines > 10000:
            return redirect(url_for('settings', error='Display lines must be between 10 and 10000'))
        
        if buffer_capacity < 100 or buffer_capacity > 100000:
            return redirect(url_for('settings', error='Buffer capacity must be between 100 and 100000'))
        
        if max_log_files < 1 or max_log_files > 100:
            return redirect(url_for('settings', error='Max log files must be between 1 and 100'))
        
        if max_log_size_mb < 1 or max_log_size_mb > 1000:
            return redirect(url_for('settings', error='Max log file size must be between 1 and 1000 MB'))
        
        # Save configuration
        logging_cfg = {
            'log_level': log_level_name,
            'display_lines': display_lines,
            'buffer_capacity': buffer_capacity,
            'enable_file_logging': enable_file_logging,
            'max_log_files': max_log_files,
            'max_log_size_mb': max_log_size_mb
        }
        
        cfg['logging'] = logging_cfg
        storage.save(cfg)
        
        # Apply new log level immediately
        new_level = getattr(logging, log_level_name, logging.INFO)
        set_log_level(new_level)
        log.info('Log level changed to %s', log_level_name)
        
        # Update buffer capacity
        from utils.logging_config import get_log_buffer
        buffer = get_log_buffer()
        if buffer:
            buffer.set_capacity(buffer_capacity)
        
        # Update file logging settings
        if enable_file_logging:
            log_dir = os.path.join(data_dir, 'logs')
            update_file_logging(
                enable=True,
                log_file_dir=log_dir,
                max_log_files=max_log_files,
                max_log_size_mb=max_log_size_mb
            )
            log.info('File logging enabled: %d files, %d MB each', max_log_files, max_log_size_mb)
        else:
            update_file_logging(enable=False)
            log.info('File logging disabled')
        
        return redirect(url_for('settings', saved=1))
    except Exception as e:
        log.error('Error saving logging settings: %s', e, exc_info=True)
        return redirect(url_for('settings', error=str(e)))


@app.route('/settings/controls', methods=['POST'])
def save_controls_settings():
    """Save controls configuration (rotary encoders)."""
    try:
        cfg = storage.load() or {}
        
        # Rotary Encoder 1 (Volume) settings
        rotary1_enabled = request.form.get('rotary1_enabled') == 'on'
        rotary1_a_pin = request.form.get('rotary1_a_pin', '').strip()
        rotary1_b_pin = request.form.get('rotary1_b_pin', '').strip()
        rotary1_button_pin = request.form.get('rotary1_button_pin', '').strip()
        
        # Rotary Encoder 2 (Skip/Brightness) settings
        rotary2_enabled = request.form.get('rotary2_enabled') == 'on'
        rotary2_a_pin = request.form.get('rotary2_a_pin', '').strip()
        rotary2_b_pin = request.form.get('rotary2_b_pin', '').strip()
        rotary2_button_pin = request.form.get('rotary2_button_pin', '').strip()
        
        # Power Button settings
        power_button_enabled = request.form.get('power_button_enabled') == 'on'
        power_button_pin = request.form.get('power_button_pin', '').strip()
        
        # Validate pin numbers
        def validate_pin(pin_str, name):
            if pin_str:
                try:
                    pin = int(pin_str)
                    if pin < 0 or pin > 40:
                        return f'{name} must be between 0 and 40'
                except ValueError:
                    return f'{name} must be a valid number'
            return None
        
        # Validate all pins
        errors = []
        if rotary1_enabled:
            if not rotary1_a_pin or not rotary1_b_pin:
                errors.append('Rotary 1: Both A and B pins are required')
            else:
                err = validate_pin(rotary1_a_pin, 'Rotary 1 Pin A')
                if err: errors.append(err)
                err = validate_pin(rotary1_b_pin, 'Rotary 1 Pin B')
                if err: errors.append(err)
            if rotary1_button_pin:
                err = validate_pin(rotary1_button_pin, 'Rotary 1 Button Pin')
                if err: errors.append(err)
        
        if rotary2_enabled:
            if not rotary2_a_pin or not rotary2_b_pin:
                errors.append('Rotary 2: Both A and B pins are required')
            else:
                err = validate_pin(rotary2_a_pin, 'Rotary 2 Pin A')
                if err: errors.append(err)
                err = validate_pin(rotary2_b_pin, 'Rotary 2 Pin B')
                if err: errors.append(err)
            if rotary2_button_pin:
                err = validate_pin(rotary2_button_pin, 'Rotary 2 Button Pin')
                if err: errors.append(err)
        
        # Validate power button
        if power_button_enabled:
            if not power_button_pin:
                errors.append('Power Button: GPIO pin is required when enabled')
            else:
                err = validate_pin(power_button_pin, 'Power Button Pin')
                if err: errors.append(err)
        
        if errors:
            return redirect(url_for('settings', error='; '.join(errors)))
        
        # Save configuration
        controls_cfg = {
            'rotary1': {
                'enabled': rotary1_enabled,
                'a_pin': int(rotary1_a_pin) if rotary1_a_pin else None,
                'b_pin': int(rotary1_b_pin) if rotary1_b_pin else None,
                'button_pin': int(rotary1_button_pin) if rotary1_button_pin else None
            },
            'rotary2': {
                'enabled': rotary2_enabled,
                'a_pin': int(rotary2_a_pin) if rotary2_a_pin else None,
                'b_pin': int(rotary2_b_pin) if rotary2_b_pin else None,
                'button_pin': int(rotary2_button_pin) if rotary2_button_pin else None
            },
            'power_button': {
                'enabled': power_button_enabled,
                'pin': int(power_button_pin) if power_button_pin else None
            }
        }
        
        cfg['controls'] = controls_cfg
        storage.save(cfg)
        
        log.info('Controls settings saved. Restart required for changes to take effect.')
        
        return redirect(url_for('settings', saved=1))
    except Exception as e:
        log.error('Error saving controls settings: %s', e, exc_info=True)
        return redirect(url_for('settings', error=str(e)))

@app.route('/settings/local-music', methods=['POST'])
def settings_local_music():
    """Save local music configuration."""
    try:
        cfg = storage.load() or {}
        
        music_directory = request.form.get('music_directory', '').strip()
        
        # Validate directory path
        if not music_directory:
            return redirect(url_for('settings', error='Music directory cannot be empty'))
        
        # Save configuration
        local_music_cfg = {
            'music_directory': music_directory
        }
        
        cfg['local_music'] = local_music_cfg
        storage.save(cfg)
        
        log.info('Local music settings saved. Restart required for changes to take effect.')
        
        return redirect(url_for('settings', saved=1))
    except Exception as e:
        log.error('Error saving local music settings: %s', e, exc_info=True)
        return redirect(url_for('settings', error=str(e)))

@app.route('/settings/nfc', methods=['POST'])
def settings_nfc():
    """Save NFC reader configuration."""
    try:
        cfg = storage.load() or {}
        
        # Get active plugin
        active_plugin = request.form.get('nfc_active', 'mock').strip()
        
        # Validate plugin exists
        from nfc.reader_manager import AVAILABLE_PLUGINS
        if active_plugin not in AVAILABLE_PLUGINS:
            return redirect(url_for('settings', error=f'Unknown NFC plugin: {active_plugin}'))
        
        # Build plugin-specific configuration
        plugin_config = {}
        
        if active_plugin == 'pn532':
            # PN532 configuration
            interface = request.form.get('pn532_interface', 'i2c').strip()
            plugin_config['interface'] = interface
            
            if interface == 'i2c':
                # I2C settings
                i2c_bus = request.form.get('pn532_i2c_bus', '1').strip()
                i2c_address = request.form.get('pn532_i2c_address', '36').strip()
                
                try:
                    plugin_config['i2c_bus'] = int(i2c_bus) if i2c_bus else 1
                except ValueError:
                    return redirect(url_for('settings', error='I2C bus must be a number'))
                
                try:
                    plugin_config['i2c_address'] = int(i2c_address) if i2c_address else 36
                except ValueError:
                    return redirect(url_for('settings', error='I2C address must be a number'))
            
            elif interface == 'spi':
                # SPI settings
                spi_bus = request.form.get('pn532_spi_bus', '0').strip()
                spi_device = request.form.get('pn532_spi_device', '0').strip()
                
                try:
                    plugin_config['spi_bus'] = int(spi_bus) if spi_bus else 0
                except ValueError:
                    return redirect(url_for('settings', error='SPI bus must be a number'))
                
                try:
                    plugin_config['spi_device'] = int(spi_device) if spi_device else 0
                except ValueError:
                    return redirect(url_for('settings', error='SPI device must be a number'))
            
            # Common PN532 settings
            reset_pin = request.form.get('pn532_reset_pin', '').strip()
            if reset_pin:
                try:
                    pin = int(reset_pin)
                    if pin < 0 or pin > 40:
                        return redirect(url_for('settings', error='Reset pin must be 0-40'))
                    plugin_config['reset_pin'] = pin
                except ValueError:
                    return redirect(url_for('settings', error='Reset pin must be a number'))
            
            poll_interval = request.form.get('pn532_poll_interval', '0.5').strip()
            try:
                interval = float(poll_interval)
                if interval < 0.1 or interval > 2.0:
                    return redirect(url_for('settings', error='Poll interval must be 0.1-2.0 seconds'))
                plugin_config['poll_interval'] = interval
            except ValueError:
                return redirect(url_for('settings', error='Poll interval must be a number'))
            
            debounce_time = request.form.get('pn532_debounce_time', '1.0').strip()
            try:
                debounce = float(debounce_time)
                if debounce < 0.5 or debounce > 5.0:
                    return redirect(url_for('settings', error='Debounce time must be 0.5-5.0 seconds'))
                plugin_config['debounce_time'] = debounce
            except ValueError:
                return redirect(url_for('settings', error='Debounce time must be a number'))
        
        # Validate configuration using plugin class
        plugin_class = AVAILABLE_PLUGINS[active_plugin]
        errors = plugin_class.validate_config(plugin_config)
        if errors:
            return redirect(url_for('settings', error='; '.join(errors)))
        
        # Save configuration
        nfc_cfg = {
            'active': active_plugin,
            'plugins': {
                active_plugin: plugin_config
            }
        }
        
        cfg['nfc'] = nfc_cfg
        storage.save(cfg)
        
        log.info('NFC settings saved: %s. Restart required for changes to take effect.', active_plugin)
        
        return redirect(url_for('settings', saved=1))
    except Exception as e:
        log.error('Error saving NFC settings: %s', e, exc_info=True)
        return redirect(url_for('settings', error=str(e)))

@app.route('/nfc-cards')
def nfc_cards():
    """Display NFC card management page"""
    cfg = storage.load()
    nfc_cards = cfg.get('nfc_cards', {})
    
    saved = request.args.get('saved')
    error = request.args.get('error')
    
    return render_template('nfc_cards.html', 
                         nfc_cards=nfc_cards,
                         saved=saved,
                         error=error)

@app.route('/nfc-cards/add', methods=['POST'])
def add_nfc_card():
    """Register a new NFC card with a friendly name"""
    try:
        card_id = request.form.get('card_id', '').strip()
        friendly_name = request.form.get('friendly_name', '').strip()
        
        if not card_id:
            return redirect(url_for('nfc_cards', error='Card ID is required'))
        
        if not friendly_name:
            return redirect(url_for('nfc_cards', error='Friendly name is required'))
        
        # Load current config
        cfg = storage.load()
        nfc_cards = cfg.get('nfc_cards', {})
        
        # Check if friendly name already exists for a different card
        for existing_id, existing_name in nfc_cards.items():
            if existing_name.lower() == friendly_name.lower() and existing_id != card_id:
                return redirect(url_for('nfc_cards', error=f'Friendly name "{friendly_name}" is already in use'))
        
        # Add or update card
        nfc_cards[card_id] = friendly_name
        cfg['nfc_cards'] = nfc_cards
        
        storage.save(cfg)
        log.info('NFC card registered: %s -> %s', card_id, friendly_name)
        
        return redirect(url_for('nfc_cards', saved=1))
    
    except Exception as e:
        log.error('Error adding NFC card: %s', e, exc_info=True)
        return redirect(url_for('nfc_cards', error=str(e)))

@app.route('/nfc-cards/delete', methods=['POST'])
def delete_nfc_card():
    """Delete a registered NFC card"""
    try:
        card_id = request.form.get('card_id', '').strip()
        
        if not card_id:
            return redirect(url_for('nfc_cards', error='Card ID is required'))
        
        cfg = storage.load()
        nfc_cards = cfg.get('nfc_cards', {})
        
        if card_id in nfc_cards:
            friendly_name = nfc_cards[card_id]
            del nfc_cards[card_id]
            cfg['nfc_cards'] = nfc_cards
            storage.save(cfg)
            log.info('NFC card deleted: %s (%s)', card_id, friendly_name)
        
        return redirect(url_for('nfc_cards', saved=1))
    
    except Exception as e:
        log.error('Error deleting NFC card: %s', e, exc_info=True)
        return redirect(url_for('nfc_cards', error=str(e)))

@app.route('/api/nfc/last-scan')
def api_nfc_last_scan():
    """API endpoint to get the last scanned card (for registration flow)"""
    try:
        # Check if there's a recent scan stored (we'll need to track this)
        # For now, return placeholder - we'll need to store last scan in player
        cfg = storage.load()
        last_scan = cfg.get('_last_nfc_scan', {})
        
        return jsonify({
            'card_id': last_scan.get('card_id'),
            'timestamp': last_scan.get('timestamp', 0)
        })
    except Exception as e:
        log.error('Error getting last scan: %s', e, exc_info=True)
        return jsonify({'error': str(e)}), 500

# create LED matrix mirror (in-memory buffer, hardware-backed when available)
try:
    # Use new display manager which supports multiple plugin backends.
    from hardware.display_manager import create_matrix
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
    
    # Power state tracking for power button
    power_state = {
        'is_on': True,
        'saved_brightness': initial_b,
        'saved_lighting_state': None
    }
    
    # if socketio available, register a notifier so updates are pushed to clients
    try:
            if socketio is not None:
                def _broadcast_pixels(pix):
                    try:
                        socketio.emit('display_update', {'width': matrix.width, 'height': matrix.height, 'pixels': pix})
                    except Exception:
                        pass
                try:
                    matrix.set_on_update(_broadcast_pixels)
                except Exception:
                    # fallback: set attribute for legacy behavior
                    try:
                        matrix._on_update = _broadcast_pixels
                    except Exception:
                        pass
    except Exception:
        pass
    # Play a small startup animation if requested (default: enabled)
    try:
        # Load startup animation settings from config with env fallback
        try:
            _startup_cfg = storage.load() or {}
            _startup_display_cfg = _startup_cfg.get('display', {})
            play_startup = _startup_display_cfg.get('play_startup_animation', os.environ.get('PLAY_STARTUP_ANIMATION', '1'))
            startup_name = _startup_display_cfg.get('startup_animation_name', os.environ.get('STARTUP_ANIMATION_NAME', 'startup.gif'))
            startup_speed = float(_startup_display_cfg.get('startup_animation_speed', os.environ.get('STARTUP_ANIMATION_SPEED', '1.0')))
        except Exception:
            play_startup = os.environ.get('PLAY_STARTUP_ANIMATION', '1')
            startup_name = os.environ.get('STARTUP_ANIMATION_NAME', 'startup.gif')
            startup_speed = float(os.environ.get('STARTUP_ANIMATION_SPEED', '1.0') or 1.0)
        
        if str(play_startup).lower() in ('1', 'true', 'yes', 'on'):
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
            # Prefer size-specific variants named like <base>_<width>_<height><ext>
            # (for example startup_64_64.gif). If none is found, fall back to the
            # configured startup file, and finally generate a small placeholder.
            base, ext = os.path.splitext(startup_name)
            try:
                candidates = []
                for fn in os.listdir(static_animations_dir):
                    if not fn.lower().endswith(ext.lower()):
                        continue
                    if not fn.startswith(base + '_'):
                        continue
                    # expect pattern base_W_H.ext
                    parts = fn[len(base) + 1: -len(ext)].split('_')
                    if len(parts) != 2:
                        continue
                    try:
                        w = int(parts[0]); h = int(parts[1])
                    except Exception:
                        continue
                    candidates.append((fn, w, h))
            except Exception:
                candidates = []

            # pick the candidate closest in size to the current matrix
            chosen = None
            try:
                target_w = int(os.environ.get('LED_WIDTH', str(matrix.width if matrix is not None else 16)))
                target_h = int(os.environ.get('LED_HEIGHT', str(matrix.height if matrix is not None else 16)))
            except Exception:
                target_w, target_h = (matrix.width if matrix is not None else 16, matrix.height if matrix is not None else 16)
            best_score = None
            for fn, w, h in candidates:
                dx = w - target_w; dy = h - target_h
                score = dx * dx + dy * dy
                if best_score is None or score < best_score:
                    best_score = score
                    chosen = fn

            if chosen:
                startup_path = os.path.join(static_animations_dir, chosen)
            else:
                # if no size-specific candidate found, use the configured startup file
                if not os.path.exists(startup_path):
                    try:
                        from PIL import Image, ImageDraw
                        w = int(os.environ.get('LED_WIDTH', str(matrix.width if matrix is not None else 16)))
                        h = int(os.environ.get('LED_HEIGHT', str(matrix.height if matrix is not None else 16)))
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

# Initialize MQTT client now that both nightlight and matrix are available
try:
    init_mqtt()
except Exception as e:
    log.warning('Could not initialize MQTT: %s', e)

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


# Optional rotary encoder support: check config for pin settings
# Fallback to environment variables for backward compatibility
try:
    import os
    
    # Load controls config
    controls_cfg = storage.load().get('controls', {}) if storage else {}
    rotary1_cfg = controls_cfg.get('rotary1', {})
    rotary2_cfg = controls_cfg.get('rotary2', {})
    
    # Rotary 1 (Volume control)
    # Try config first, fallback to environment variables
    rotary1_enabled = rotary1_cfg.get('enabled', False)
    a_pin = rotary1_cfg.get('a_pin') or os.environ.get('ROTARY_A_PIN')
    b_pin = rotary1_cfg.get('b_pin') or os.environ.get('ROTARY_B_PIN')
    bbtn = rotary1_cfg.get('button_pin') or os.environ.get('ROTARY_BUTTON_PIN')
    
    if (rotary1_enabled or a_pin) and a_pin and b_pin:
        try:
            from .hardware.rotary import create_rotary

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
            log.info('Rotary encoder 1 (volume) started on pins A=%s, B=%s', a_pin, b_pin)
        except Exception as e:
            # don't fail startup if rotary cannot be started
            log.warning('Failed to start rotary encoder 1: %s', e)
    
    # Rotary 2 (Skip/Brightness control)
    # Try config first, fallback to environment variables
    rotary2_enabled = rotary2_cfg.get('enabled', False)
    a2 = rotary2_cfg.get('a_pin') or os.environ.get('ROTARY2_A_PIN')
    b2 = rotary2_cfg.get('b_pin') or os.environ.get('ROTARY2_B_PIN')
    btn2 = rotary2_cfg.get('button_pin') or os.environ.get('ROTARY2_BUTTON_PIN')
    
    if (rotary2_enabled or a2) and a2 and b2:
        try:
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
            log.info('Rotary encoder 2 (skip/brightness) started on pins A=%s, B=%s', a2, b2)
        except Exception as e:
            log.warning('Failed to start rotary encoder 2: %s', e)
except Exception as e:
    log.warning('Failed to initialize rotary encoders: %s', e)


# Optional power button support: check config for pin settings
try:
    import os
    
    # Load power button config
    controls_cfg = storage.load().get('controls', {}) if storage else {}
    power_button_cfg = controls_cfg.get('power_button', {})
    
    power_button_enabled = power_button_cfg.get('enabled', False)
    power_pin = power_button_cfg.get('pin') or os.environ.get('POWER_BUTTON_PIN')
    
    if (power_button_enabled or power_pin) and power_pin:
        try:
            import RPi.GPIO as GPIO
            import time
            from threading import Thread
            
            # Setup GPIO for power button (pull-up, button connects to ground)
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(int(power_pin), GPIO.IN, pull_up_down=GPIO.PUD_UP)
            
            # Track last button press time for debouncing
            _last_power_press_time = [0.0]
            _POWER_DEBOUNCE_SEC = 0.5
            
            def _power_button_callback(channel):
                """Handle power button press - toggle display and lights on/off."""
                try:
                    # Debounce
                    now = time.time()
                    if now - _last_power_press_time[0] < _POWER_DEBOUNCE_SEC:
                        return
                    _last_power_press_time[0] = now
                    
                    # Toggle power state
                    is_currently_on = power_state['is_on']
                    
                    if is_currently_on:
                        # Turn OFF: stop music, save states, turn off display/lights
                        log.info('Power button: turning OFF')
                        
                        # Stop any playing music
                        try:
                            if player and player._state.get('playing'):
                                player.pause()
                        except Exception as e:
                            log.warning('Failed to stop music on power off: %s', e)
                        
                        # Save current brightness
                        try:
                            if matrix:
                                power_state['saved_brightness'] = matrix.get_brightness()
                        except Exception:
                            pass
                        
                        # Turn off display
                        try:
                            if matrix:
                                matrix.set_brightness(0)
                                matrix.clear()
                        except Exception as e:
                            log.warning('Failed to turn off display: %s', e)
                        
                        # Turn off nightlight/lighting
                        try:
                            from hardware.nightlight import nightlight
                            if nightlight:
                                power_state['saved_lighting_state'] = nightlight.get_brightness()
                                nightlight.set_brightness(0)
                        except Exception as e:
                            log.debug('No nightlight to turn off: %s', e)
                        
                        power_state['is_on'] = False
                        log.info('Power OFF complete')
                        
                    else:
                        # Turn ON: restore display/lights to previous state
                        log.info('Power button: turning ON')
                        
                        # Restore display brightness
                        try:
                            if matrix:
                                saved_brightness = power_state.get('saved_brightness', 25)
                                matrix.set_brightness(saved_brightness)
                        except Exception as e:
                            log.warning('Failed to restore display brightness: %s', e)
                        
                        # Restore lighting
                        try:
                            from hardware.nightlight import nightlight
                            if nightlight and power_state.get('saved_lighting_state') is not None:
                                nightlight.set_brightness(power_state['saved_lighting_state'])
                        except Exception as e:
                            log.debug('No nightlight to restore: %s', e)
                        
                        power_state['is_on'] = True
                        log.info('Power ON complete')
                        
                except Exception as e:
                    log.error('Error in power button callback: %s', e, exc_info=True)
            
            # Setup event detection for button press (falling edge = button pressed to ground)
            GPIO.add_event_detect(int(power_pin), GPIO.FALLING, callback=_power_button_callback, bouncetime=int(_POWER_DEBOUNCE_SEC * 1000))
            
            log.info('Power button enabled on GPIO pin %s', power_pin)
        except Exception as e:
            log.warning('Failed to start power button: %s', e)
except Exception as e:
    log.warning('Failed to initialize power button: %s', e)


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
        
        # Handle card ID from dropdown or manual entry
        card = form.get('card_id', '').strip()
        if card == '__manual__':
            card = form.get('manual_card_id', '').strip()
        
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
        # optional resume position checkbox
        resume_position = True if form.get('resume_position') in ('on', 'true', '1') else False
        # preserve existing per-track animations when editing an existing mapping
        prev = mappings.get(card, {})
        preserved = prev.get('animations') if isinstance(prev, dict) else None
        # preserve saved_state if resume_position is enabled
        saved_state = prev.get('saved_state') if resume_position and isinstance(prev, dict) else None
        new_map = {'type': map_type, 'id': playlist_id, 'shuffle': shuffle, 'repeat': repeat_mode, 'volume': volume, 'resume_position': resume_position}
        if preserved:
            new_map['animations'] = preserved
        if saved_state:
            new_map['saved_state'] = saved_state
        mappings[card] = new_map
        cfg['mappings'] = mappings
        storage.save(cfg)
        # If request was AJAX, return JSON to avoid relying on redirects
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.accept_mimetypes['application/json'] > request.accept_mimetypes['text/html']:
            return jsonify({'ok': True, 'mapping': mappings[card]})
        return redirect(url_for('mappings'))
    cfg = storage.load()
    nfc_cards = cfg.get('nfc_cards', {})
    return render_template('mappings.html', config=cfg, nfc_cards=nfc_cards)


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
    cfg = storage.load()
    nfc_cards = cfg.get('nfc_cards', {})
    return render_template('simulate.html', nfc_cards=nfc_cards)


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
    music_base = player.local.base
    # Calculate audiobooks base path (same logic as in local_player.py)
    audiobooks_base = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'audiobooks'))
    
    for p in pls:
        try:
            # For m3u files, use the filename without extension as the friendly name
            if p.lower().endswith('.m3u'):
                name = os.path.splitext(os.path.basename(p))[0]
            else:
                # For directories, try to get relative path from either music or audiobooks base
                name = None
                # Try music base first
                try:
                    if p.startswith(music_base):
                        name = os.path.relpath(p, music_base)
                except Exception:
                    pass
                # If that didn't work, try audiobooks base
                if name is None:
                    try:
                        if p.startswith(audiobooks_base):
                            name = os.path.relpath(p, audiobooks_base)
                    except Exception:
                        pass
                # Fall back to the full path if neither worked
                if name is None:
                    name = p
            
            friendly.append({'path': p, 'name': name})
        except Exception:
            # If anything fails, use the full path
            friendly.append({'path': p, 'name': p})
    
    return jsonify({'playlists': friendly, 'music_base': music_base})


@app.route('/display')
def display_page():
    cfg = storage.load()
    return render_template('display.html', config=cfg)


@app.route('/lighting')
def lighting_page():
    """Nightlight control page."""
    return render_template('lighting.html')


@app.route('/api/lighting/state', methods=['GET'])
def get_lighting_state():
    """Get current nightlight state."""
    try:
        global nightlight
        if nightlight:
            return jsonify(nightlight.get_state())
        else:
            return jsonify({'on': False, 'color': '#ff6b35', 'brightness': 128})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/lighting/state', methods=['POST'])
def set_lighting_state():
    """Update nightlight state."""
    try:
        global nightlight
        if not nightlight:
            return jsonify({'success': False, 'error': 'Nightlight not initialized'}), 500
        
        data = request.get_json() or {}
        
        on = data.get('on')
        color = data.get('color')
        brightness = data.get('brightness')
        
        state = nightlight.set_state(on=on, color=color, brightness=brightness)
        
        return jsonify({'success': True, 'state': state})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


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
    global mqtt_client, matrix
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
        
        # Publish state to MQTT if available
        if mqtt_client:
            mqtt_client.publish_display_state()
        
        return jsonify({'ok': True, 'brightness': b_i})
    except Exception:
        return jsonify({'error': 'internal'}), 500


@app.route('/api/display/power', methods=['GET', 'POST'])
def api_display_power():
    """Get or set display power state."""
    global mqtt_client, matrix
    try:
        if request.method == 'GET':
            if matrix is None:
                return jsonify({'on': False})
            return jsonify({'on': matrix.get_power()})
        # POST
        data = request.json or {}
        on = data.get('on', True)
        if matrix is None:
            return jsonify({'ok': True, 'on': on})
        matrix.set_power(bool(on))
        
        # Publish state to MQTT if available
        if mqtt_client:
            mqtt_client.publish_display_state()
        
        return jsonify({'ok': True, 'on': bool(on)})
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


@app.route('/api/logs')
def api_logs():
    """Return recent log entries."""
    try:
        # Get query parameters
        n = request.args.get('n', default=100, type=int)
        min_level_name = request.args.get('level', default='DEBUG', type=str).upper()
        
        # Convert level name to number
        min_level = getattr(logging, min_level_name, logging.DEBUG)
        
        # Get logs from buffer
        logs = get_recent_logs(n=n, min_level=min_level)
        
        return jsonify({
            'logs': logs,
            'count': len(logs),
            'requested': n,
            'min_level': min_level_name
        })
    except Exception as e:
        log.error('Error retrieving logs: %s', e, exc_info=True)
        return jsonify({'error': str(e)}), 500


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

    @socketio.on('subscribe_audible_jobs')
    def _ws_subscribe_audible_jobs():
        """Subscribe to audiobook conversion job updates."""
        try:
            from audiobooks import converter
            jobs = converter.list_jobs()
            socketio.emit('audible_jobs_update', {'jobs': jobs})
        except Exception as e:
            socketio.emit('audible_jobs_update', {'error': str(e)})

    @socketio.on('get_audible_jobs')
    def _ws_get_audible_jobs():
        """Get current audiobook conversion jobs via Socket.IO."""
        try:
            from audiobooks import converter
            jobs = converter.list_jobs()
            socketio.emit('audible_jobs_update', {'jobs': jobs})
        except Exception as e:
            socketio.emit('audible_jobs_update', {'error': str(e)})


@app.route('/api/mappings')
def api_mappings():
    cfg = storage.load()
    mappings = cfg.get('mappings', {})
    nfc_cards = cfg.get('nfc_cards', {})
    # For local mappings, try to provide friendly names using local base
    result = []
    for card, m in mappings.items():
        entry = {
            'card_id': card,
            'card_friendly_name': nfc_cards.get(card),  # Add friendly name if available
            'type': m.get('type'),
            'id': m.get('id'),
            'shuffle': bool(m.get('shuffle')),
            'repeat': m.get('repeat', 'off'),
            'volume': m.get('volume'),
            'resume_position': bool(m.get('resume_position', False))
        }
        if m.get('type') == 'local':
            try:
                import os
                path = m.get('id') or ''
                
                # Use same logic as /api/playlists for consistency
                # For m3u files, use the filename without extension as the friendly name
                if path.lower().endswith('.m3u'):
                    name = os.path.splitext(os.path.basename(path))[0]
                else:
                    # For directories, try to get relative path from either music or audiobooks base
                    music_base = player.local.base
                    audiobooks_base = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'audiobooks'))
                    
                    name = None
                    # Try music base first
                    try:
                        if os.path.isabs(path) and path.startswith(music_base):
                            name = os.path.relpath(path, music_base)
                        elif not os.path.isabs(path):
                            # Relative path - use as is
                            name = path
                    except Exception:
                        pass
                    
                    # If that didn't work, try audiobooks base
                    if name is None:
                        try:
                            if os.path.isabs(path) and path.startswith(audiobooks_base):
                                name = os.path.relpath(path, audiobooks_base)
                        except Exception:
                            pass
                    
                    # Fall back to the original path if neither worked
                    if name is None:
                        name = path
                
                entry['name'] = name or path
            except Exception:
                entry['name'] = m.get('id')
        else:
            # Spotify mapping - try to fetch actual playlist name
            playlist_id = m.get('id')
            try:
                # Extract playlist ID from URI or URL if needed
                if playlist_id:
                    # Handle various formats: spotify:playlist:ID, https://open.spotify.com/playlist/ID, or just ID
                    if 'spotify:playlist:' in playlist_id:
                        clean_id = playlist_id.split('spotify:playlist:')[1].split('?')[0]
                    elif 'open.spotify.com/playlist/' in playlist_id:
                        clean_id = playlist_id.split('open.spotify.com/playlist/')[1].split('?')[0].split('/')[0]
                    else:
                        clean_id = playlist_id
                    
                    # Try to fetch playlist details from Spotify
                    try:
                        playlist_info = player.spotify._call_spotify('playlist', clean_id, fields='name')
                        if playlist_info and playlist_info.get('name'):
                            entry['name'] = playlist_info['name']
                        else:
                            entry['name'] = playlist_id
                    except Exception:
                        # If API call fails, just use the ID
                        entry['name'] = playlist_id
                else:
                    entry['name'] = playlist_id
            except Exception:
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
            # If an animation was previously paused, resume it. Otherwise trigger
            # the mapped animation for the current track.
            try:
                if matrix is not None and hasattr(matrix, 'resume_animation'):
                    matrix.resume_animation()
                    # resume succeeded (or was a no-op) - ensure mapping restart if nothing running
                    if not matrix.is_animating():
                        _trigger_animation_for_current_track()
                else:
                    _trigger_animation_for_current_track()
            except Exception:
                _trigger_animation_for_current_track()
        except Exception:
            pass
        return jsonify({'ok': True})
    if action == 'pause':
        player.pause()
        # Pause any running animation on the matrix so audio pause also
        # pauses visual playback. Clear duplicate-suppression so resuming
        # will restart animations as expected.
        try:
            if matrix is not None and hasattr(matrix, 'pause_animation'):
                try:
                    matrix.pause_animation()
                except Exception:
                    # fallback to stopping if pause not supported
                    try:
                        matrix.stop_animation()
                    except Exception:
                        pass
            # clear last-animation record so a subsequent play will restart it
            try:
                _last_animation['name'] = None
                _last_animation['started_at'] = 0.0
            except Exception:
                pass
        except Exception:
            pass
        return jsonify({'ok': True})
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


# ============================================================================
# Audiobooks routes
# ============================================================================
try:
    from audiobooks.audible_client import AudibleClient
    from audiobooks import converter
    import uuid
    
    # Initialize Audible client
    audible_client = AudibleClient()
    
    # Set socketio instance for converter to emit events
    converter.set_socketio(socketio)
    
    # Ensure audiobooks output directory exists
    audiobooks_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'audiobooks'))
    os.makedirs(audiobooks_dir, exist_ok=True)
    
    # Temp directory for AAXC downloads before conversion
    temp_downloads_dir = os.path.join(data_dir, 'audiobooks_temp')
    os.makedirs(temp_downloads_dir, exist_ok=True)
    
    @app.route('/audiobooks')
    def audiobooks_page():
        """Audiobooks management page."""
        return render_template('audiobooks.html', config=storage.load())
    
    @app.route('/api/audible/auth/status')
    def api_audible_auth_status():
        """Check if Audible is authenticated."""
        authenticated = audible_client.is_authenticated()
        needs_reauth = audible_client.needs_reauthentication()
        return jsonify({
            'authenticated': authenticated,
            'needs_reauthentication': needs_reauth
        })
    
    @app.route('/api/audible/auth/login', methods=['POST'])
    def api_audible_auth_login():
        """Authenticate with Audible."""
        try:
            username = request.json.get('username')
            password = request.json.get('password')
            country_code = request.json.get('country_code', 'us')
            otp_code = request.json.get('otp_code')  # Optional 2FA code
            
            if not username or not password:
                return jsonify({'success': False, 'error': 'Username and password required'}), 400
            
            result = audible_client.authenticate(username, password, country_code, otp_code)
            return jsonify(result)
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500
    
    @app.route('/api/audible/auth/logout', methods=['POST'])
    def api_audible_auth_logout():
        """Log out from Audible."""
        try:
            audible_client.logout()
            return jsonify({'success': True})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500
    
    @app.route('/api/audible/library')
    def api_audible_library():
        """Fetch Audible library."""
        try:
            page = int(request.args.get('page', 1))
            num_results = int(request.args.get('num_results', 50))
            
            result = audible_client.get_library(page=page, num_results=num_results)
            return jsonify(result)
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/audible/download', methods=['POST'])
    def api_audible_download():
        """Download and convert an audiobook."""
        try:
            asin = request.json.get('asin')
            auto_convert = request.json.get('auto_convert', True)
            
            if not asin:
                return jsonify({'success': False, 'error': 'ASIN required'}), 400
            
            # Download AAXC file
            download_result = audible_client.download_audiobook(
                asin=asin,
                output_dir=temp_downloads_dir
            )
            
            if not download_result.get('success'):
                return jsonify(download_result), 500
            
            aaxc_path = download_result['path']
            
            if auto_convert:
                # Start async conversion job
                job_id = str(uuid.uuid4())
                activation_bytes = audible_client.get_activation_bytes()
                
                converter.convert_aax_to_m4b(
                    input_file=aaxc_path,
                    output_dir=audiobooks_dir,
                    activation_bytes=activation_bytes,
                    job_id=job_id
                )
                
                return jsonify({
                    'success': True,
                    'asin': asin,
                    'download_path': aaxc_path,
                    'job_id': job_id,
                    'status': 'converting'
                })
            else:
                return jsonify({
                    'success': True,
                    'asin': asin,
                    'download_path': aaxc_path,
                    'status': 'downloaded'
                })
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500
    
    @app.route('/api/audible/convert', methods=['POST'])
    def api_audible_convert():
        """Convert a downloaded AAXC file to M4B."""
        try:
            input_file = request.json.get('input_file')
            
            if not input_file or not os.path.exists(input_file):
                return jsonify({'success': False, 'error': 'Valid input file required'}), 400
            
            job_id = str(uuid.uuid4())
            activation_bytes = audible_client.get_activation_bytes()
            
            converter.convert_aax_to_m4b(
                input_file=input_file,
                output_dir=audiobooks_dir,
                activation_bytes=activation_bytes,
                job_id=job_id
            )
            
            return jsonify({
                'success': True,
                'job_id': job_id,
                'status': 'converting'
            })
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500
    
    @app.route('/api/audible/jobs')
    def api_audible_jobs():
        """List all conversion jobs."""
        try:
            jobs = converter.list_jobs()
            return jsonify({'jobs': jobs})
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/audible/jobs/<job_id>')
    def api_audible_job_status(job_id):
        """Get status of a specific conversion job."""
        try:
            status = converter.get_job_status(job_id)
            if status:
                return jsonify(status)
            else:
                return jsonify({'error': 'Job not found'}), 404
        except Exception as e:
            return jsonify({'error': str(e)}), 500
    
    @app.route('/api/audible/jobs/<job_id>/cancel', methods=['POST'])
    def api_audible_job_cancel(job_id):
        """Cancel a conversion job."""
        try:
            success = converter.cancel_job(job_id)
            return jsonify({'success': success})
        except Exception as e:
            return jsonify({'success': False, 'error': str(e)}), 500

except Exception:
    # Audiobooks module not available; routes will not be registered
    log.exception('Audiobooks module failed to load; audiobooks features disabled')


if __name__ == '__main__':
    # Determine SSL context: prefer user-provided cert+key via env vars,
    # otherwise fall back to adhoc TLS if available.
    ssl_context = None
    try:
        cert_path = os.environ.get('SSL_CERT_PATH') or os.environ.get('SSL_CERT')
        key_path = os.environ.get('SSL_KEY_PATH') or os.environ.get('SSL_KEY')
        if cert_path and key_path and os.path.exists(cert_path) and os.path.exists(key_path):
            ssl_context = (cert_path, key_path)
        else:
            # try to use adhoc if available (Werkzeug will create a temp cert)
            ssl_context = 'adhoc'
    except Exception:
        ssl_context = None

    # Run server with SSL if possible
    if _HAVE_SOCKETIO and socketio is not None:
        try:
            if ssl_context:
                socketio.run(app, host='0.0.0.0', port=5000, ssl_context=ssl_context)
            else:
                socketio.run(app, host='0.0.0.0', port=5000)
        except TypeError:
            # older socketio may not accept ssl_context param; fall back to plain run
            socketio.run(app, host='0.0.0.0', port=5000)
    else:
        if ssl_context:
            app.run(host='0.0.0.0', port=5000, ssl_context=ssl_context)
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
