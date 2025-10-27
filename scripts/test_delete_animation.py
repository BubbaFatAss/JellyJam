# Small test: create a temp animation file in data/animations, call delete endpoint via Flask test_client, verify file removed
import sys, os, json, time
sys.path.append(r"C:\Users\andre\Documents\Code\JellyJam")
from app.app import app
from pathlib import Path

animations_dir = os.path.join(os.path.dirname(__file__), '..', 'data', 'animations')
Path(animations_dir).mkdir(parents=True, exist_ok=True)
fn = 'temp_delete_test.json'
path = os.path.join(animations_dir, fn)
# write a minimal JSON file
with open(path, 'w', encoding='utf-8') as f:
    json.dump({'on': True, 'bri': 128, 'seg': {'id':0,'i':[0,256,'00ff00']}}, f)

with app.test_client() as c:
    # ensure file exists
    r = c.get('/api/animations')
    print('Before:', r.get_json())
    # call delete
    r = c.post('/api/animations/delete', json={'name': fn})
    print('Delete response:', r.status_code, r.get_json())
    # list again
    r2 = c.get('/api/animations')
    print('After:', r2.get_json())
    # cleanup leftover if any
    if os.path.exists(path):
        os.remove(path)
