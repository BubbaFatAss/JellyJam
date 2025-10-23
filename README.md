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
