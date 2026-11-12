"""Frontend i18n check (no server needed): python tests/i18n_web_check.py
(The backend counterpart is tests/i18n_check.py.)

1. No CJK characters in web/src code (.vue .ts .js .css .html .json) outside web/src/locales/zh-CN.json.
   Data that must match Chinese text coming from the backend or from CAD files is written with backslash-u escapes.
   ALLOW lists exact (file, substring) exceptions.
2. en.json and zh-CN.json have the same keys, and the same {n} placeholders per key; en.json has no CJK.
3. Every literal $t('ns.key') used in web/src exists in en.json.
Exit code 1 on any failure.
"""
import json, os, re, sys

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'web', 'src')
CJK = re.compile(r'[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]')
EXTS = ('.vue', '.ts', '.js', '.css', '.html', '.json')
SKIP = {os.path.normpath('locales/zh-CN.json')}
ALLOW = [  # (relative path, substring that may contain CJK)
]

fails = {'cjk': [], 'locale': [], 'missing': []}
used = set()
for dp, dn, fn in os.walk(ROOT):
    for f in fn:
        if not f.endswith(EXTS):
            continue
        p = os.path.join(dp, f)
        rel = os.path.normpath(os.path.relpath(p, ROOT))
        src = open(p, encoding='utf-8').read()
        used.update(re.findall(r"\$t\('(\w+\.\w+)'", src))
        if rel in SKIP:
            continue
        for i, line in enumerate(src.split('\n'), 1):
            if CJK.search(line) and not any(rel == os.path.normpath(a) and s in line for a, s in ALLOW):
                fails['cjk'].append('CJK %s:%d: %s' % (rel.replace(os.sep, '/'), i, line.strip()[:120]))
for html in ('index.html',):
    p = os.path.join(ROOT, '..', html)
    for i, line in enumerate(open(p, encoding='utf-8').read().split('\n'), 1):
        if CJK.search(line): fails['cjk'].append('CJK web/%s:%d: %s' % (html, i, line.strip()[:120]))

loc = os.path.join(ROOT, 'locales')
en = json.load(open(os.path.join(loc, 'en.json'), encoding='utf-8'))
zh = json.load(open(os.path.join(loc, 'zh-CN.json'), encoding='utf-8'))
flat = lambda d: {ns + '.' + k: v for ns, kv in d.items() for k, v in kv.items()}
fe, fz = flat(en), flat(zh)
for k in sorted(set(fe) ^ set(fz)):
    fails['locale'].append('KEY only in %s: %s' % ('en' if k in fe else 'zh-CN', k))
ph = lambda s: sorted(re.findall(r'\{\d+\}', s))
for k in sorted(set(fe) & set(fz)):
    if ph(fe[k]) != ph(fz[k]):
        fails['locale'].append('PLACEHOLDER mismatch %s: %r / %r' % (k, fe[k], fz[k]))
    if CJK.search(fe[k]):
        fails['locale'].append('CJK in en.json %s: %r' % (k, fe[k]))
for k in sorted(used - set(fe)):
    fails['missing'].append('MISSING key used in code: %s' % k)

allf = [x for v in fails.values() for x in v]
for f in allf[:200]:
    print('FAIL', f)
print('%d/3 i18n web checks passed (%d keys, %d used in code)' % (sum(1 for v in fails.values() if not v), len(fe), len(used)))
sys.exit(1 if allf else 0)
