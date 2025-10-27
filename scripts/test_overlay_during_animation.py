import sys, os, time
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from app.app import matrix
print('matrix:', matrix is not None)
if matrix is None:
    raise SystemExit(1)
# ensure test gif exists
from PIL import Image
path = os.path.join(ROOT, 'data','animations')
os.makedirs(path, exist_ok=True)
fname = os.path.join(path, 'test_play.gif')
if not os.path.exists(fname):
    frames = []
    for i in range(4):
        im = Image.new('RGB', (16,16), (0,0,0))
        for y in range(16):
            for x in range(4):
                xx = (i*4)+x
                if 0 <= xx < 16:
                    im.putpixel((xx,y),(255,0,0))
        frames.append(im)
    frames[0].save(fname, save_all=True, append_images=frames[1:], duration=200, loop=0)
print('playing looped animation')
matrix.stop_animation()
matrix.play_animation_from_gif(fname, speed=1.0, loop=True)
# let animation run a bit
time.sleep(0.5)
print('showing overlay (overlay mode)')
matrix.show_volume_bar(80, 2000, color='#00FF00', mode='overlay')
# sample bottom row for a few ticks
for t in range(10):
    pix = matrix.get_pixels()
    bottom = pix[-16:]
    print('tick', t, 'bottom row unique:', sorted(set(bottom))[:5])
    time.sleep(0.25)
print('done')
