"""Internationalisation: request language + message catalogue (English default, zh-CN).

Message ids are the original Chinese source texts (the product was written in Chinese); `honeymom/locale/en.json` maps
each id to English, `honeymom/locale/zh-CN.json` holds optional zh-CN overrides. Code calls `_tr('...', name=value)`;
`str.format` placeholders are named. Module-level data (schema labels, enum labels, templates) stays as data and is
translated where it is served (`tr_obj`, /api/meta, the JSON response filter below).

Language of a request: header `X-Lang` > query `?lang=` > `Accept-Language` > server default.
Server default (background jobs, notices, seeding): env `MOM_LANG`, else the demo dataset language in demo mode, else `en`.
Flags: `MOM_DEMO=1` demo mode (demo accounts + demo data), `MOM_DEMO_LANG=en|zh` demo dataset language (default en).
"""
import contextlib
import contextvars
import json
import os
import re
import sys
from urllib.parse import parse_qs

LANGS = ('en', 'zh-CN')
_LANG = contextvars.ContextVar('mom_lang', default=None)
_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'locale')
_CAT: dict = {}
_KW = re.compile(r'\{([A-Za-z_][A-Za-z0-9_]*)')


def _load(name):
    if name not in _CAT:
        try:
            with open(os.path.join(_DIR, name + '.json'), encoding='utf-8') as f:
                _CAT[name] = json.load(f)
        except FileNotFoundError:
            _CAT[name] = {}
    return _CAT[name]


def norm(v) -> str | None:
    v = (v or '').strip().lower().replace('_', '-')
    if v.startswith('zh'):
        return 'zh-CN'
    if v.startswith('en'):
        return 'en'
    return None


def _flag(name) -> bool:
    return os.environ.get(name, '').strip().lower() in ('1', 'true', 'yes', 'on')


def demo_mode() -> bool:
    """Demo mode (demo accounts, demo dataset). Off by default: production installs start with the admin set-up step."""
    return _flag('MOM_DEMO')


def demo_lang() -> str:
    return norm(os.environ.get('MOM_DEMO_LANG')) or 'en'


def default_lang() -> str:
    return norm(os.environ.get('MOM_LANG')) or (demo_lang() if demo_mode() else 'en')


def lang() -> str:
    return _LANG.get() or default_lang()


def is_zh() -> bool:
    return lang() == 'zh-CN'


@contextlib.contextmanager
def use(language):
    tok = _LANG.set(norm(language) or 'en')
    try:
        yield
    finally:
        _LANG.reset(tok)


def _lookup(s, language):
    if language == 'zh-CN':
        return _load('zh-CN').get(s, s)
    return _load('en').get(s)


def _importing() -> bool:
    """True while a module body is being executed by the import system: data built at import time (entity definitions,
    label tables made by helper functions) keeps the source text and is translated where it is served."""
    f = sys._getframe(2)
    while f is not None:
        if f.f_code.co_filename.startswith('<frozen importlib'):
            return True
        f = f.f_back
    return False


def _tr(_s, /, **kw):
    """Translate message id `_s` into the current language and format named placeholders."""
    language = 'zh-CN' if _importing() else lang()
    out = _lookup(_s, language)
    if out is None:                       # no English yet: fall back to the source text
        out = _s
    elif language == 'en' and kw:         # labels passed as values (enum labels, module names) are translated too
        en = _load('en')
        kw = {k: (en.get(v, v) if isinstance(v, str) else v) for k, v in kw.items()}
    if not kw:
        return out
    try:
        return out.format(**kw)
    except (KeyError, IndexError, ValueError):
        return _s.format(**kw)


tr = _tr


def tr_obj(o, skip=frozenset()):
    """Translate every string value of a JSON-like object that is a catalogue id (exact match). Keys in `skip` are left alone."""
    if lang() != 'en':
        return o
    en = _load('en')
    den = _load('data-en')

    def walk(x):
        if isinstance(x, str):
            return en.get(x) or den.get(x) or phrase_en(x)
        if isinstance(x, list):
            return [walk(v) for v in x]
        if isinstance(x, tuple):
            return tuple(walk(v) for v in x)
        if isinstance(x, dict):
            return {k: (v if k in skip else walk(v)) for k, v in x.items()}
        return x
    return walk(o)


META_SKIP = frozenset({'name', 'module', 'ref', 't', 'auto', 'default', 'mkey', 'children'})


def tr_meta(ents):
    """/api/meta entities: labels, hints, enum labels, action labels; `module` stays the key, `module_label` is added."""
    for e in ents:
        e['module_label'] = tr(e.get('module') or '')
    return tr_obj(ents, META_SKIP)


def llm_instruction() -> str:
    """Appended to every model call: answer in the UI language."""
    if is_zh():
        return '请用简体中文回答（界面语言是中文）。'
    return 'The user interface language is English: always answer in English, including headings, table headers and labels, ' \
           'even when the data, tool results or instructions above are in Chinese. Keep codes, numbers and names of records as they are.'


def resolve(headers: dict, query: str) -> str:
    v = norm(headers.get(b'x-lang', b'').decode('latin-1'))
    if v:
        return v
    q = parse_qs(query or '')
    v = norm((q.get('lang') or [''])[0])
    if v:
        return v
    al = headers.get(b'accept-language', b'').decode('latin-1')
    if al:
        first = al.split(',')[0].split(';')[0]
        v = norm(first)
        if v:
            return v
    return default_lang()


# JSON responses (not the generic record API, which round-trips data through forms) get module-level labels translated.
_NO_FILTER = ('/api/e/', '/api/login', '/mcp')
FILTER_SKIP = frozenset({'module', 'writable', 'mkey', 'username', 'code', 'key', 'value', 'id', 'sql', 'token', 'password'})


def _readonly_entity(path, scope):
    """GET /api/e/<entity> of a system-written (internal) entity such as alarms: never edited in forms, safe to translate."""
    if scope.get('method') != 'GET' or not path.startswith('/api/e/'):
        return False
    from .schema import BY_NAME
    e = BY_NAME.get(path[7:].split('/')[0].split('.')[0])
    return bool(e and e.internal)


def install(app):
    """Pure ASGI middleware: sets the request language, echoes Content-Language, translates catalogue labels in JSON responses."""
    class _LangMw:
        def __init__(self, inner):
            self.inner = inner

        async def __call__(self, scope, receive, send):
            if scope.get('type') not in ('http', 'websocket'):
                return await self.inner(scope, receive, send)
            hdrs = dict(scope.get('headers') or [])
            language = resolve(hdrs, scope.get('query_string', b'').decode('latin-1'))
            tok = _LANG.set(language)
            path = scope.get('path', '')
            filt = language == 'en' and scope.get('type') == 'http' and path.startswith('/api/') and (not path.startswith(_NO_FILTER) or _readonly_entity(path, scope))
            state = {'start': None, 'body': [], 'json': False}

            async def send2(msg):
                if msg['type'] == 'http.response.start':
                    headers = [h for h in msg.get('headers', []) if h[0].lower() != b'content-language'] + [(b'content-language', language.encode())]
                    msg = {**msg, 'headers': headers}
                    ctype = dict((k.lower(), v) for k, v in headers).get(b'content-type', b'')
                    if filt and ctype.startswith(b'application/json'):
                        state['start'], state['json'] = msg, True
                        return
                    return await send(msg)
                if msg['type'] == 'http.response.body' and state['json']:
                    state['body'].append(msg.get('body', b''))
                    if msg.get('more_body'):
                        return
                    raw = b''.join(state['body'])
                    try:
                        raw = json.dumps(tr_obj(json.loads(raw), FILTER_SKIP), ensure_ascii=False, separators=(',', ':')).encode('utf-8')
                    except Exception:
                        pass
                    start = state['start']
                    headers = [h for h in start['headers'] if h[0].lower() != b'content-length'] + [(b'content-length', str(len(raw)).encode())]
                    await send({**start, 'headers': headers})
                    return await send({'type': 'http.response.body', 'body': raw})
                return await send(msg)
            try:
                return await self.inner(scope, receive, send2)
            finally:
                _LANG.reset(tok)
    app.add_middleware(_LangMw)


# ---------------- demo / template data in English ----------------
# Seeding (demo dataset, industry templates, built-in label templates, the demo delivery package) writes through db.insert/update.
# With `seed_translation()` active every Chinese string value is looked up in locale/data-en.json (then en.json) as it is written,
# and lookups (db.get / db.rows) by Chinese values are translated the same way, so seed code that finds rows by name keeps working.
# The Chinese demo dataset is the source; locale/data-en.json is the English dataset next to it.
_SEED = contextvars.ContextVar('mom_seed_tr', default=False)
SEED_MISSES: set = set()
_CJK = re.compile(r'[　-〿一-鿿＀-￯]')


@contextlib.contextmanager
def seed_translation(on=True):
    tok = _SEED.set(bool(on))
    try:
        yield
    finally:
        _SEED.reset(tok)


def demo_data():
    """Runtime writes of demo data (e.g. auto-approved demo FAI): in the dataset language of the demo."""
    return seed_translation(_SEED.get() or (demo_mode() and demo_lang() == 'en'))


def seeding_en() -> bool:
    return _SEED.get()


def seed_value(v):
    """Translate one value written by seeding (str, or JSON-like structures)."""
    if isinstance(v, str):
        if not _CJK.search(v):
            return v
        out = _load('data-en').get(v) or _load('en').get(v)
        if out is None:
            SEED_MISSES.add(v)
            return phrase_en(v)
        return out
    if isinstance(v, list):
        return [seed_value(x) for x in v]
    if isinstance(v, dict):
        return {k: seed_value(x) for k, x in v.items()}
    return v


def seed_row(row: dict) -> dict:
    if not _SEED.get():
        return row
    out = {}
    for k, v in row.items():
        if isinstance(v, str) and v[:1] in '[{':
            try:
                out[k] = json.dumps(seed_value(json.loads(v)), ensure_ascii=False)
                continue
            except ValueError:
                pass
        out[k] = seed_value(v)
    return out


# ---------------- logic on texts that may be translated ----------------
def variants(msgid) -> set:
    """The source text and its English translation (texts written earlier may be in either language)."""
    return {msgid, _load('en').get(msgid, msgid)}


def _rx(template):
    parts = re.split(r'\{[^{}]*\}', template)
    return re.compile('.*?'.join(re.escape(p) for p in parts), re.S)


def matches(text, msgid) -> bool:
    """True if `text` was produced from message `msgid` (any language; placeholders match anything)."""
    if not text:
        return False
    return any(_rx(v).search(text) for v in variants(msgid))


# ---------------- data text fallback (English mode) ----------------
# Data that is not an exact catalogue entry (plant data from the simulator/replayer such as "1#加工中心（cnc）", alarm texts,
# composite demo names like "<device> 日常点检"): known Chinese phrases are replaced word by word (locale/phrases-en.json,
# longest first) and full-width punctuation becomes ASCII. Only applied to strings that still contain Chinese.
_PH = {}
_PUNCT = {'（': ' (', '）': ')', '，': ', ', '、': ', ', '：': ': ', '；': '; ', '。': '. ', '！': '! ', '？': '? ', '「': '"', '」': '"', '【': '[', '】': ']', '　': ' '}


def _phrase_rx():
    if 'rx' not in _PH:
        table = _load('phrases-en')
        keys = sorted(table, key=len, reverse=True)
        _PH['table'] = table
        _PH['rx'] = re.compile('|'.join(re.escape(k) for k in keys)) if keys else None
    return _PH['rx'], _PH['table']


def phrase_en(s: str) -> str:
    if not isinstance(s, str) or not _CJK.search(s):
        return s
    rx, table = _phrase_rx()
    out = s
    if rx is not None:
        out = rx.sub(lambda m: ' ' + table[m.group(0)] + ' ', out)
    for a, b in _PUNCT.items():
        out = out.replace(a, b)
    out = re.sub(r' {2,}', ' ', out).strip()
    out = re.sub(r' ([,.:;!?)\]])', r'\1', out)
    out = re.sub(r'([(\[]) ', r'\1', out)
    return out
