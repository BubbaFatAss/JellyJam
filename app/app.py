from flask import Flask, render_template, request, redirect, url_for, jsonify
from flask import session
from player.player import Player
from nfc.reader import NFCReader
from storage import Storage
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
            return redirect(url_for('mappings'))
        playlist_id = form.get('playlist_id', '').strip()
        map_type = form.get('type', 'local')
        # If local mapping requested but no playlists are available and no manual path provided, reject
        if map_type == 'local':
            available = player.local.list_playlists()
            if not available and not playlist_id:
                # nothing to map to; ignore request
                return redirect(url_for('mappings'))
        mappings[card] = {'type': map_type, 'id': playlist_id}
        cfg['mappings'] = mappings
        storage.save(cfg)
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
    return jsonify({'error': 'unknown action'}), 400


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
