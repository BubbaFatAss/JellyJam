import sys, os
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
try:
    import app.app as appmod
    print('imported app.app ok')
    print('has matrix:', hasattr(appmod, 'matrix'))
except Exception as e:
    print('import failed', e)
