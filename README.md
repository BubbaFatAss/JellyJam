Raspberry Pi NFC Music Player

This project is a minimal scaffold for a Raspberry Pi (2W) application that plays music when NFC cards are scanned. It supports Spotify (via Web API + a Spotify Connect device) and local playback (via VLC). A small web UI lets you configure Spotify credentials and map NFC card IDs to playlists.

This repo is a starter scaffold. It includes a mocked NFC reader for development. For production on a Pi, install NFC hardware libraries (for example `nfcpy` or a PN532 stack) and enable a Spotify Connect device (or use librespot) on the Pi.

Getting started (development):

1. Create a Python 3.10+ virtual environment and install dependencies:

```powershell
python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -r requirements.txt
```

2. Run the app:

```powershell
flask --app app.app run --host=0.0.0.0 --port=5000
```

3. Open http://127.0.0.1:5000 (or use your Pi LAN IP) and use the web UI to add mappings. The NFC reader is mocked — use the "Simulate NFC" button to test playback.

Pi notes:
- For Spotify playback you need an active Spotify Connect device reachable by the Spotify account you authenticate. Consider installing librespot or running the official Spotify client on the Pi.
- For NFC hardware on Pi, use `nfcpy` or a PN532 driver and update `nfc/reader.py` to use the real driver.

Security:
- Stored tokens and config are saved in `data/` as JSON for the scaffold. Do not commit secrets.

Environment variables
---------------------

The app supports a number of environment variables to control runtime behavior, TLS, and LED matrix settings. Set them in your shell or via your service manager.

- FLASK_SECRET
	- Purpose: Flask session secret. Default: `dev-secret` (change in production).

- SSL_CERT_PATH or SSL_CERT
	- Purpose: Path to a PEM-encoded TLS certificate file to use for HTTPS when starting the built-in server.

- SSL_KEY_PATH or SSL_KEY
	- Purpose: Path to a PEM-encoded private key file that pairs with the certificate above.
	- Behavior: If both cert and key exist at the given paths the server will start with TLS. If not provided the server will attempt to use an adhoc certificate (useful for development) if the Werkzeug/Flask environment supports it.

- PLAY_STARTUP_ANIMATION
	- Purpose: Enable/disable playing the startup animation on boot. Values: `1|true|yes|on` to enable. Default: `1`.

- STARTUP_ANIMATION_NAME
	- Purpose: Filename (under `app/static/animations`) of the startup animation. Default: `startup.gif`.

- STARTUP_ANIMATION_SPEED
	- Purpose: Playback speed multiplier for the startup animation. Default: `1.0`.

- LED_WIDTH and LED_HEIGHT
	- Purpose: Logical matrix width and height used as defaults for plugins that don't provide their own size. Default: `16` each.

- LED_BRIGHTNESS or LED_BRIGHTNESS_PERCENT
	- Purpose: Default brightness. `LED_BRIGHTNESS` expects 0-255 (legacy); `LED_BRIGHTNESS_PERCENT` may be used to provide a 0-100 percent value.

- LED_PIN, LED_FREQ_HZ, LED_DMA, LED_INVERT, LED_CHANNEL, LED_SERPENTINE
	- Purpose: Configure legacy rpi_ws281x-backed WS2812 matrix hardware. See comments in `app/hardware/ledmatrix.py` for details.

Notes
-----
- Flask-Talisman: If `flask-talisman` is installed in your environment the app will enable basic secure headers (Content-Security-Policy is left permissive by default to avoid breaking inline templates). Install with `pip install flask-talisman` and adjust the code if you want stricter policies.
- Production TLS: Running Flask's built-in server with TLS is convenient for testing but not recommended for production. Prefer terminating TLS at a reverse proxy (nginx, Caddy, Traefik) in front of the app.
