import sys, os, time
# ensure repo root is on sys.path
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from app.app import matrix
print('matrix available:', matrix is not None)
if matrix is not None and hasattr(matrix, 'show_volume_bar'):
    try:
        matrix.show_volume_bar(42, 1200)
        print('show_volume_bar called')
    except Exception as e:
        print('show_volume_bar error', e)
else:
    print('matrix or method not available')
# keep process alive briefly so overlay thread runs
time.sleep(2)
print('done')
