from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask import session
from .player.player import Player
from .nfc.reader import NFCReader
from .storage import Storage
import os

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET', 'dev-secret')
data_dir = os.path.join(os.path.dirname(__file__), '..', 'data')
os.makedirs(data_dir, exist_ok=True)
storage = Storage(os.path.join(data_dir, 'config.json'))

player = Player(storage)
nfc = NFCReader(callback=player.handle_nfc)


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
        mappings[card] = {'type': map_type, 'id': playlist_id, 'shuffle': shuffle, 'repeat': repeat_mode, 'volume': volume}
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
    return jsonify(player.now_playing())


@app.route('/api/control', methods=['POST'])
def api_control():
    action = request.json.get('action')
    if action == 'play':
        player.play(); return jsonify({'ok': True})
    if action == 'pause':
        player.pause(); return jsonify({'ok': True})
    if action == 'next':
        player.next(); return jsonify({'ok': True})
    if action == 'previous':
        player.previous(); return jsonify({'ok': True})
    if action == 'seek':
        pos = int(request.json.get('position_ms', 0))
        player.seek(pos); return jsonify({'ok': True})
    if action == 'stop':
        player.stop(); return jsonify({'ok': True})
    if action == 'volume':
        # expected payload: { volume: 0-100 }
        vol = request.json.get('volume')
        try:
            vol_i = int(vol)
            vol_i = max(0, min(100, vol_i))
        except Exception:
            return jsonify({'error': 'invalid volume'}), 400
        player.set_volume(vol_i)
        return jsonify({'ok': True, 'volume': vol_i})
    return jsonify({'error': 'unknown action'}), 400


@app.route('/api/volume')
def api_volume():
    # return current volume for active source if available
    vol = player.get_volume()
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
    app.run(host='0.0.0.0', port=5000)
