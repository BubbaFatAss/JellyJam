import re, json
p = r"c:\Users\andre\Documents\Code\JellyJam\data\animations\WLED_1761432314711.json"
raw = open(p, 'r', encoding='utf-8').read()

matches = re.findall(r"\{.*?\}", raw, flags=re.DOTALL)
print('matches count:', len(matches))
for i, m in enumerate(matches):
    print('\n--- match', i, 'len', len(m))
    lines = m.splitlines()
    for ln, l in enumerate(lines, start=1):
        print(f'{ln:03d}: {l}')
    try:
        d = json.loads(m)
        print(' -> json ok, keys=', list(d.keys())[:10])
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(' -> json failed:', e)
