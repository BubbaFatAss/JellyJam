#!/usr/bin/env python3
import json, re, sys
from pathlib import Path
p = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(r'c:\Users\andre\Documents\Code\JellyJam\data\animations\WLED_1761432314711.json')
raw = p.read_text(encoding='utf-8')
# strip pure-line // comments
text = re.sub(r'^\s*//.*$', '', raw, flags=re.MULTILINE)
# try to parse one or more JSON objects
decoder = json.JSONDecoder()
idx = 0
objs = []
L = len(text)
while True:
    while idx < L and text[idx].isspace():
        idx += 1
    if idx >= L:
        break
    try:
        obj, end = decoder.raw_decode(text, idx)
        objs.append(obj)
        idx = end
    except ValueError:
        # try to find next '{' and continue
        next_brace = text.find('{', idx+1)
        if next_brace == -1:
            break
        idx = next_brace

if not objs:
    print('No JSON objects found in', p)
    sys.exit(2)
# choose single object if only one else array
to_write = objs[0] if len(objs) == 1 else objs
p.write_text(json.dumps(to_write, indent=2, ensure_ascii=False), encoding='utf-8')
print('Wrote normalized JSON to', p, 'objects=', len(objs))
