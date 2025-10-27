import sys, time, json
sys.path.append(r"C:\Users\andre\Documents\Code\JellyJam")
from app.app import app

c = app.test_client()

name = 'WLED_1761432314711.json'
print('Available animations:', c.get('/api/animations').get_json())

print('Playing', name)
resp = c.post('/api/animations/play', json={'name': name, 'speed': 1.0, 'loop': True})
print('play ->', resp.status_code, resp.get_json())

# Poll display a few times
for i in range(6):
    r = c.get('/api/display')
    j = r.get_json()
    pix = j.get('pixels', [])
    non_black = sum(1 for p in pix if (p and p != '#000000'))
    print(f'Frame {i}: non-black pixels =', non_black)
    # print first 16 pixels for a quick view
    print('first row:', pix[:16])
    time.sleep(0.5)

print('Stopping animation')
r = c.post('/api/animations/stop')
print('stop ->', r.status_code, r.get_json())

# final display
r = c.get('/api/display')
j = r.get_json()
print('Final non-black pixels =', sum(1 for p in j.get('pixels', []) if p and p != '#000000'))
