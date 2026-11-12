"""演示模式：录制 / 回放 AI 回答（不花 token 也能演示）。

开关在 ai_switch：global + demo → 回放；global + record（demo 关）→ 正常调用大模型，并把回答录下来。
录制按"功能 + 操作 + 归一化的请求"存成 JSON 文件：honeymom/fixtures/ai_demo/<feature>.json（可用 MOM_AI_FIXTURES，旧名 HM_AI_FIXTURES，换目录）。

归一化（normalize）：去掉易变字段（thread_id、时间、页面地址…），字符串做全角半角统一、小写、去空白、去句末标点。
查找（lookup）：先精确匹配；找不到时在同一操作里按文本（q / text / prompt）的二元组相似度找最像的一条（≥ FUZZY）。

后端用法：
    from . import ai_demo
    return ai_demo.run('kb', 'kb_ask', {'q': q}, live=lambda: kb_answer(q), fallback=lambda: rule(q), recordable=lambda r: r.get('llm'))
    # SSE（右侧助手）：ai_demo.run_stream(...)，见 assistant.chat

录制也可以手工加：POST /api/ai/demo/recordings {feature, op, request, response}（管理员）。
"""
from ..env import get as env_get
from ..i18n import _tr
import hashlib
import json
import os
import re
import threading
import time
import unicodedata
from collections import Counter

from fastapi import APIRouter, Body, Depends, HTTPException

from .. import db
from ..auth import current_user
from . import ai_switch

router = APIRouter()
FIX_DIR = env_get('MOM_AI_FIXTURES') or os.path.join(db.ROOT, 'honeymom', 'fixtures', 'ai_demo')
VOLATILE = {'thread_id', 'ts', 'time', 'now', 'page', 'page_title', 'at', 'id_token', 'history'}
FUZZY = 0.62
_lock = threading.Lock()
_mem: dict = {}


def _norm_str(s: str) -> str:
    s = unicodedata.normalize('NFKC', s).lower()
    s = re.sub(r'\s+', '', s)
    return s.rstrip('?？。.!！~～')


def normalize(v):
    if isinstance(v, dict):
        return {k: normalize(x) for k, x in sorted(v.items()) if k not in VOLATILE and x not in (None, '', [], {})}
    if isinstance(v, (list, tuple)):
        return [normalize(x) for x in v]
    if isinstance(v, str):
        return _norm_str(v)
    if isinstance(v, float):
        return round(v, 4)
    return v


def key_of(op: str, payload) -> str:
    return op + ':' + hashlib.sha1(json.dumps(normalize(payload), ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]


def _text_of(n) -> str:
    if isinstance(n, dict):
        for k in ('q', 'text', 'prompt', 'question'):
            if isinstance(n.get(k), str):
                return n[k]
    return json.dumps(n, ensure_ascii=False, sort_keys=True)


def _bigrams(s):
    return Counter(s[i:i + 2] for i in range(len(s) - 1)) or Counter([s])


def similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    ga, gb = _bigrams(a), _bigrams(b)
    inter = sum((ga & gb).values())
    return 2 * inter / (sum(ga.values()) + sum(gb.values()))


def _rec_lang():
    """Recordings are per UI language: <feature>.json = zh-CN (the shipped demo set), <feature>.en.json = English."""
    from .. import i18n
    return '' if i18n.is_zh() else '.en'


def _path(feature):
    return os.path.join(FIX_DIR, re.sub(r'[^a-z0-9_]', '_', feature.split('|')[0]) + feature.partition('|')[2] + '.json')


DEMO_NOTE = 'Demo mode: no recorded English AI answer matches this request, so the built-in rule-based result is shown. '             'Configure a model (config/llm.local.json or MOM_LLM_* variables) and turn demo playback off for live AI answers.'


def _note_missing(r):
    """English UI, no English recording: say so (the shipped recordings are Chinese)."""
    if _rec_lang() and isinstance(r, dict):
        return {**r, 'demo_note': DEMO_NOTE}
    return r


def _load(feature) -> dict:
    feature = feature + '|' + _rec_lang()
    with _lock:
        if feature not in _mem:
            p = _path(feature)
            try:
                with open(p, encoding='utf-8') as f:
                    _mem[feature] = json.load(f).get('items') or {}
            except (OSError, ValueError):
                _mem[feature] = {}
        return _mem[feature]


def _write(feature):
    feature = feature + '|' + _rec_lang()
    os.makedirs(FIX_DIR, exist_ok=True)
    tmp = _path(feature) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump({'version': 1, 'feature': feature, 'items': _mem.get(feature) or {}}, f, ensure_ascii=False, indent=1)
    os.replace(tmp, _path(feature))


def lookup(feature: str, op: str, payload, exact=False, accept=None):
    """exact=True：只认完全一致的请求（回答里有具体对象 / 数字、换个对象就不对的场景，如值守解读、草稿润色）。
    accept(录制的 request) → bool：相似匹配时额外的筛选（如"解释这个数"要求是同一个指标）。"""
    items = _load(feature)
    k = key_of(op, payload)
    if k in items:
        return items[k]['response']
    if exact:
        return None
    want = _text_of(normalize(payload))
    best, score = None, 0.0
    for it in items.values():
        if it.get('op') != op or (accept and not accept(it.get('raw_request') or it.get('request') or {})):
            continue
        s = similarity(want, _text_of(it.get('request')))
        if s > score:
            best, score = it, s
    return best['response'] if best and score >= FUZZY else None


def save(feature: str, op: str, payload, response):
    items = _load(feature)
    with _lock:
        items[key_of(op, payload)] = {'op': op, 'request': normalize(payload), 'raw_request': payload, 'response': response,
                                      'recorded_at': time.strftime('%Y-%m-%d %H:%M:%S')}
        _write(feature)


def run(feature: str, op: str, payload, live, fallback=None, recordable=None, mark=None, exact=False):
    """演示回放 / 录制包装。live() 正常执行；fallback() 是演示模式下没有录制时的兜底（默认 live()，此时大模型已不可用 → 规则版）。
    mark(resp) 给回放的结果打标记（默认 dict 加 demo=True）。exact=True 时不做相似匹配（见 lookup）。"""
    if ai_switch.demo():
        r = lookup(feature, op, payload, exact)
        if r is not None:
            return mark(r) if mark else ({**r, 'demo': True} if isinstance(r, dict) else r)
        return _note_missing((fallback or live)())
    res = live()
    if ai_switch.recording() and (recordable(res) if recordable else True):
        try:
            save(feature, op, payload, res)
        except OSError:
            pass
    return res


def run_stream(feature: str, op: str, payload, live_gen, fallback_gen=None, delay=0.25, accept=None):
    """SSE 版：录制时收集 (event, data) 序列；回放时按原顺序吐出（每条间隔 delay 秒，像真的在想）。
    live_gen / fallback_gen 产出 (event, data) 元组；这里返回 (event, data) 的生成器。"""
    if ai_switch.demo():
        rec = lookup(feature, op, payload, accept=accept)
        if rec is not None:
            for ev, data in rec:
                if ev in ('progress', 'tool'):
                    time.sleep(delay)
                yield ev, data
            return
        if _rec_lang():
            yield 'note', {'text': DEMO_NOTE, 'demo_note': True}
        yield from (fallback_gen or live_gen)()
        return
    seq = []
    for ev, data in live_gen():
        seq.append([ev, data])
        yield ev, data
    if ai_switch.recording() and any(e == 'answer' for e, _ in seq) and not any(e == 'error' for e, _ in seq):
        try:
            save(feature, op, payload, [[e, d] for e, d in seq if e not in ('done', 'confirm', 'card', 'wrote')])
        except OSError:
            pass


# ---------------- 接口（管理员） ----------------
def _admin(user):
    from .. import perms
    perms.check(user, 'system.ai.manage', _tr('只有管理员可以管理演示数据'))


@router.get('/api/ai/demo/recordings')
def list_recordings(feature: str = '', user=Depends(current_user)):
    _admin(user)
    feats = [feature] if feature else [k for k, _, _ in ai_switch.FEATURES]
    out = []
    for f in feats:
        for k, it in _load(f).items():
            out.append({'feature': f, 'key': k, 'op': it.get('op'), 'request': it.get('raw_request'), 'recorded_at': it.get('recorded_at')})
    return {'dir': FIX_DIR, 'items': out}


@router.post('/api/ai/demo/recordings')
def add_recording(body: dict = Body(...), user=Depends(current_user)):
    _admin(user)
    f, op = body.get('feature'), body.get('op')
    if f not in ai_switch.LABEL or not op or 'response' not in body:
        raise HTTPException(400, _tr('需要 feature（功能 key）、op、request、response'))
    save(f, op, body.get('request') or {}, body['response'])
    return {'ok': True, 'key': key_of(op, body.get('request') or {})}


@router.delete('/api/ai/demo/recordings/{feature}/{key}')
def del_recording(feature: str, key: str, user=Depends(current_user)):
    _admin(user)
    items = _load(feature)
    with _lock:
        items.pop(key, None)
        _write(feature)
    return {'ok': True}
