"""i18n check: lists Chinese string literals in honeymom/ that are not covered by the message catalogue.

    python tests/i18n_check.py                 report; exit 1 when something is not in the allowlist
    python tests/i18n_check.py --write-allowlist   (maintainers) rewrite tests/i18n_allowlist.json with the current findings

Rules (comments and docstrings are ignored):
  hard-coded    a Chinese literal inside a function that is not passed through _tr(...)
                (parsing keywords, regexes, comparisons with stored values... belong in the allowlist with a reason)
  untranslated  a _tr() message id or a module-level Chinese label without an English entry in honeymom/locale/en.json
  data          demo/seed data (seed*.py, label_builtin.py, migrations/, packs/*.json) without an entry in locale/data-en.json
Not checked: fixtures/ai_demo (recorded zh demo answers; English falls back with a note), locale/.
"""
import ast
import json
import os
import re
import sys

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'honeymom')
ALLOW = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'i18n_allowlist.json')
CJK = re.compile(r'[　-〿一-鿿＀-￯]')
DATA = re.compile(r'(^|[\\/])(seed[a-z_]*\.py|label_builtin\.py)$|(^|[\\/])migrations[\\/]')
SKIP = re.compile(r'(^|[\\/])i18n\.py$|(^|[\\/])fixtures[\\/]|(^|[\\/])locale[\\/]')


def load(name):
    with open(os.path.join(ROOT, 'locale', name), encoding='utf-8') as f:
        return json.load(f)


EN, DEN = load('en.json'), load('data-en.json')


def literal_text(n):
    if isinstance(n, ast.Constant) and isinstance(n.value, str):
        return n.value
    if isinstance(n, ast.JoinedStr):
        return ''.join(v.value if isinstance(v, ast.Constant) else '{}' for v in n.values)
    return None


class V(ast.NodeVisitor):
    def __init__(self):
        self.fn = 0
        self.tr = 0
        self.out = []      # (kind, line, text)

    def _body(self, body):
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            body = body[1:]
        for s in body:
            self.visit(s)

    def visit_Module(self, n):
        self._body(n.body)

    def visit_ClassDef(self, n):
        for d in n.decorator_list:
            self.visit(d)
        self._body(n.body)

    def _fn(self, n):
        for d in n.decorator_list:
            self.visit(d)
        self.visit(n.args)
        self.fn += 1
        self._body(n.body)
        self.fn -= 1
    visit_FunctionDef = visit_AsyncFunctionDef = _fn

    def visit_Lambda(self, n):
        self.fn += 1
        self.visit(n.body)
        self.fn -= 1

    # calls whose argument is a message id translated later (at read time) or matched in every language
    MSGID_ARG = {'_tr': 0, 'tr': 0, 'set_status': 1, 'put': 1, 'matches': 1, 'variants': 0}

    def visit_Call(self, n):
        fname = n.func.id if isinstance(n.func, ast.Name) else n.func.attr if isinstance(n.func, ast.Attribute) else ''
        idx = self.MSGID_ARG.get(fname)
        if idx is not None and len(n.args) > idx and literal_text(n.args[idx]) is not None:
            for i, a in enumerate(n.args):
                if i != idx:
                    self.visit(a)
            t = literal_text(n.args[idx])
            if t is not None and CJK.search(t):
                self.out.append(('msgid', n.lineno, t))
            self.tr += 1
            for k in n.keywords:
                self.visit(k.value)
            self.tr -= 1
            return
        self.generic_visit(n)

    def _lit(self, n):
        t = literal_text(n)
        if t is None or not CJK.search(t):
            return
        self.out.append(('fn' if self.fn else 'mod', n.lineno, t))

    def visit_Constant(self, n):
        self._lit(n)

    def visit_JoinedStr(self, n):
        self._lit(n)
        for v in n.values:
            if isinstance(v, ast.FormattedValue):
                self.visit(v.value)


def scan():
    found = []
    for dp, dn, fn in os.walk(ROOT):
        if '__pycache__' in dp:
            continue
        for name in fn:
            p = os.path.join(dp, name)
            rel = os.path.relpath(p, ROOT).replace('\\', '/')
            if SKIP.search(rel):
                continue
            if name.endswith('.json') and rel.startswith('packs/'):
                def walk(x):
                    if isinstance(x, str) and CJK.search(x) and x not in DEN and x not in EN:
                        found.append(('data', rel, 0, x))
                    elif isinstance(x, list):
                        for v in x:
                            walk(v)
                    elif isinstance(x, dict):
                        for v in x.values():
                            walk(v)
                with open(p, encoding='utf-8') as f:
                    walk(json.load(f))
                continue
            if not name.endswith('.py'):
                continue
            with open(p, encoding='utf-8') as f:
                v = V()
                v.visit(ast.parse(f.read()))
            data = bool(DATA.search(rel))
            for kind, line, t in v.out:
                if data:
                    if '{}' not in t and t not in DEN and t not in EN:
                        found.append(('data', rel, line, t))
                elif kind == 'msgid':
                    if t not in EN:
                        found.append(('untranslated', rel, line, t))
                elif kind == 'mod':
                    if t not in EN and '{}' not in t:
                        found.append(('untranslated', rel, line, t))
                else:
                    found.append(('hard-coded', rel, line, t))
    return found


REASONS = {'hard-coded': 'matches Chinese input/stored values (keywords, regex, comparisons, dict keys)',
           'untranslated': 'model prompt / JSON template kept in the source language; every model call adds the UI-language instruction',
           'data': 'demo data value without English entry (stays Chinese in the en dataset)'}


def main():
    found = scan()
    allow = {}
    if os.path.exists(ALLOW):
        with open(ALLOW, encoding='utf-8') as f:
            allow = {(a['file'], a['text']): a for a in json.load(f)['items']}
    if '--write-allowlist' in sys.argv:
        items = []
        for kind, rel, line, t in found:
            old = allow.get((rel, t))
            items.append({'file': rel, 'text': t, 'kind': kind, 'reason': (old or {}).get('reason') or REASONS.get(kind, '')})
        uniq = {(i['file'], i['text']): i for i in items}
        with open(ALLOW, 'w', encoding='utf-8') as f:
            json.dump({'note': 'Chinese literals that intentionally stay as they are (see tests/i18n_check.py)', 'items': list(uniq.values())},
                      f, ensure_ascii=False, indent=1)
        print('allowlist written:', len(uniq))
        return 0
    bad = [x for x in found if (x[1], x[3]) not in allow]
    by = {}
    for kind, rel, line, t in found:
        by[kind] = by.get(kind, 0) + 1
    print('Chinese literals not covered by the catalogue:', by, '| allowlisted', len(found) - len(bad), '| NEW', len(bad))
    for kind, rel, line, t in bad[:200]:
        print(f'  {kind:13} {rel}:{line}  {t[:90]!r}')
    print(f'{len(found) - len(bad)}/{len(found)} allowlisted')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
