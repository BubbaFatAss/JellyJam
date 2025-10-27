import sys, json
# Ensure repo root on sys.path
sys.path.append(r"C:\Users\andre\Documents\Code\JellyJam")
from app.app import app
c = app.test_client()

def jget(resp):
    try:
        return json.dumps(resp.get_json(), indent=2)
    except Exception:
        return resp.get_data(as_text=True)

endpoints = [
    '/api/display',
    '/api/animations',
    '/api/socketio_client',
    '/api/display/brightness',
    '/api/rotary2/mode'
]

for ep in endpoints:
    resp = c.get(ep)
    print(ep, '->', resp.status_code)
    print(jget(resp))
    print('-' * 60)

# Try playing a non-existent animation to see error handling
print('/api/animations/play (missing file) ->')
resp = c.post('/api/animations/play', json={'name': 'nonexistent.json'})
print(resp.status_code)
print(jget(resp))
