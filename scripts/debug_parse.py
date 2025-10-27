import sys
sys.path.append(r"C:\Users\andre\Documents\Code\JellyJam")
from app.hardware.ledmatrix import parse_wled_json_from_file
p = r"c:\Users\andre\Documents\Code\JellyJam\data\animations\WLED_1761432314711.json"
try:
    pix, bri = parse_wled_json_from_file(p, 16*16)
    print('Parsed:', len(pix), 'brightness', bri)
    print('First 16:', pix[:16])
except Exception as e:
    import traceback
    traceback.print_exc()
    print('Error:', e)
