import sys, os, time
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from app.app import matrix
print('matrix:', matrix is not None)
from PIL import Image
# create a tiny 16x16 gif with 3 frames
frames = []
for i in range(3):
    im = Image.new('RGB', (16,16), (0,0,0))
    x = i*4
    for y in range(16):
        for xx in range(4):
            if 0 <= x+xx < 16:
                im.putpixel((x+xx,y),(255,0,0))
    frames.append(im)
path = os.path.join(ROOT, 'data','animations')
os.makedirs(path, exist_ok=True)
fname = os.path.join(path, 'test_play.gif')
frames[0].save(fname, save_all=True, append_images=frames[1:], duration=200, loop=0)
print('wrote', fname)
if matrix is None:
    print('no matrix')
    sys.exit(1)
# stop any running animation
matrix.stop_animation()
matrix.play_animation_from_gif(fname, speed=1.0, loop=False)
prev = None
for t in range(10):
    pix = matrix.get_pixels()
    non_black = sum(1 for p in pix if p != '#000000')
    print('tick', t, 'non-black', non_black)
    time.sleep(0.25)
print('done')
