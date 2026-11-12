"""知识库文件：作业指导书之外的加工图纸（PDF、图片、DXF）。

POST /api/kb/{id}/file   {name, data(base64)}  上传/替换附件（≤ 20 MB；存到 <数据目录>/kb_files/<id>.<扩展名>，文件名只用 ASCII）
GET  /api/kb/{id}/file   原文件（PDF/图片浏览器直接预览）
GET  /api/kb/{id}/svg    DXF 转 SVG（自带的小解析器：LINE / LWPOLYLINE / POLYLINE / CIRCLE / ARC / ELLIPSE / SPLINE / TEXT / MTEXT / POINT / INSERT），不依赖外部服务
GET  /api/kb/for?product=&process=  某产品某工序关联的作业指导书和图纸（工位终端用）
"""
from ..i18n import _tr
import base64
import math
import os
import re
import struct
import zlib
from html import escape

from fastapi import APIRouter, Body, Depends, HTTPException
from fastapi.responses import FileResponse, Response

from .. import db
from ..auth import current_user, require_write
from ..crud import attach_labels, out
from ..db import clock

router = APIRouter()
TYPES = {'.pdf': ('pdf', 'application/pdf'), '.png': ('image', 'image/png'), '.jpg': ('image', 'image/jpeg'), '.jpeg': ('image', 'image/jpeg'),
         '.gif': ('image', 'image/gif'), '.webp': ('image', 'image/webp'), '.svg': ('image', 'image/svg+xml'), '.dxf': ('dxf', 'application/dxf')}
MAX = 20 * 1024 * 1024


def files_dir():
    d = os.path.join(db.DATA, 'kb_files')
    os.makedirs(d, exist_ok=True)
    return d


def file_path(doc):
    ext = os.path.splitext(doc['file_name'] or '')[1].lower()
    return os.path.join(files_dir(), f"{doc['id']}{ext}")


def save_file(c, doc_id, name, data: bytes):
    ext = os.path.splitext(name or '')[1].lower()
    if ext not in TYPES:
        raise HTTPException(400, _tr('不支持的文件类型 {v}：支持 PDF、PNG/JPG/GIF/WebP/SVG、DXF', v=ext or _tr('（无扩展名）')))
    if len(data) > MAX:
        raise HTTPException(400, _tr('文件太大（最大 20 MB）'))
    d = files_dir()
    for f in os.listdir(d):          # 换了扩展名的旧文件删掉
        if f.split('.')[0] == str(doc_id):
            os.remove(os.path.join(d, f))
    with open(os.path.join(d, f'{doc_id}{ext}'), 'wb') as f:
        f.write(data)
    db.update(c, 'kb_doc', doc_id, {'file_name': name, 'file_type': TYPES[ext][0], 'file_size': len(data), 'updated': clock.now()}, raw=True)


@router.post('/api/kb/{rid}/file')
def upload(rid: int, body: dict = Body(...), user=Depends(current_user)):
    require_write(user, 'kb_doc', 'update')
    try:
        data = base64.b64decode((body.get('data') or '').split(',')[-1])
    except ValueError:
        raise HTTPException(400, _tr('文件内容不是 base64'))
    with db.engine.begin() as c:
        if not db.get(c, 'kb_doc', rid):
            raise HTTPException(404, _tr('文档不存在'))
        save_file(c, rid, body.get('name') or 'file', data)
        d = db.get(c, 'kb_doc', rid)
    return {'ok': True, 'file_name': d['file_name'], 'file_type': d['file_type'], 'file_size': d['file_size']}


def _doc(rid):
    with db.engine.connect() as c:
        d = db.get(c, 'kb_doc', rid)
    if not d or not d['file_name']:
        raise HTTPException(404, _tr('这条知识没有附件'))
    p = file_path(d)
    if not os.path.isfile(p):
        raise HTTPException(404, _tr('附件文件不存在'))
    return d, p


@router.get('/api/kb/{rid}/file')
def get_file(rid: int, user=Depends(current_user)):
    d, p = _doc(rid)
    ext = os.path.splitext(p)[1].lower()
    return FileResponse(p, media_type=TYPES.get(ext, ('', 'application/octet-stream'))[1], headers={'Content-Disposition': f'inline; filename="drawing-{rid}{ext}"'})


@router.get('/api/kb/{rid}/svg')
def get_svg(rid: int, user=Depends(current_user)):
    d, p = _doc(rid)
    if d['file_type'] != 'dxf':
        raise HTTPException(400, _tr('只有 DXF 需要转换'))
    with open(p, 'rb') as f:
        raw = f.read()
    try:
        svg = dxf_to_svg(raw.decode('utf-8'))
    except UnicodeDecodeError:
        svg = dxf_to_svg(raw.decode('gbk', errors='replace'))
    return Response(svg, media_type='image/svg+xml')


@router.get('/api/kb/for')
def kb_for(product: int = 0, process: int = 0, user=Depends(current_user)):
    from .shopfloor import instruction_for
    with db.engine.connect() as c:
        return instruction_for(c, product or None, process or None)


@router.get('/api/kb/list')
def kb_list(kind: str = '', product: int = 0, process: int = 0, q: str = '', all: int = 0, user=Depends(current_user)):
    from ..schema import TABLES
    import sqlalchemy as sa
    t = TABLES['kb_doc']
    cond = []
    if not all:          # 受控文件只显示生效版本（草稿 / 审核中 / 作废的在"受控文件"里看，all=1）
        cond.append(sa.or_(t.c.ctrl_status.is_(None), t.c.ctrl_status == 'effective'))
    if kind:
        cond.append(sa.func.coalesce(t.c.kind, 'sop') == kind)
    if product:
        cond.append(t.c.product == product)
    if process:
        cond.append(t.c.process == process)
    if q.strip():
        cond.append(sa.or_(t.c.title.ilike(f'%{q.strip()}%'), t.c.tags.ilike(f'%{q.strip()}%'), t.c.content.ilike(f'%{q.strip()}%')))
    with db.engine.connect() as c:
        rows = db.rows(c, 'kb_doc', *cond, order=t.c.id.desc(), limit=500)
        attach_labels(c, 'kb_doc', rows)
        kind = sa.func.coalesce(t.c.kind, sa.literal_column("'sop'"))      # 常量内联：PostgreSQL 要求 SELECT 和 GROUP BY 是同一个表达式
        counts = dict(c.execute(sa.select(kind, sa.func.count()).group_by(kind)).all())
    for r in rows:
        r['kind'] = r['kind'] or 'sop'
        r['excerpt'] = (r['content'] or '')[:160]
    return {'items': out(rows), 'counts': counts}


# ---------------- DXF → SVG ----------------
ACI = {1: '#d43c3c', 2: '#c79a00', 3: '#2e9d5b', 4: '#0f8ea8', 5: '#2f5fd0', 6: '#a03fb5', 7: '#1d2433', 8: '#6b7480', 9: '#9aa1ab'}


def _pairs(text):
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    it = iter(lines)
    for code in it:
        try:
            val = next(it)
        except StopIteration:
            return
        code = code.strip()
        if code.lstrip('-').isdigit():
            yield int(code), val.rstrip()


def parse_dxf(text):
    """返回 (实体列表, 块定义)。实体 = {'type', 'layer', 'color', 数值组码…, 'pts': [...]}。"""
    section = None
    blocks, cur_block = {}, None
    entities = []
    cur = None
    layers = {}
    in_layer_table = False
    lay = None

    def push():
        nonlocal cur
        if cur is None:
            return
        if cur['type'] == 'VERTEX' and entities_target() and entities_target()[-1]['type'] == 'POLYLINE':
            entities_target()[-1]['pts'].append((cur.get(10, 0.0), cur.get(20, 0.0)))
        elif cur['type'] not in ('SEQEND', 'VERTEX', 'ENDBLK', 'BLOCK'):
            entities_target().append(cur)
        cur = None

    def entities_target():
        return blocks[cur_block]['ents'] if cur_block is not None else entities

    for code, val in _pairs(text):
        if code == 0:
            if section in ('ENTITIES', 'BLOCKS'):
                push()
            if in_layer_table and lay is not None:
                layers[lay.get('name')] = lay.get('color', 7)
                lay = None
            if val == 'SECTION':
                section = '?'
                continue
            if val == 'ENDSEC':
                section = None
                continue
            if section == 'TABLES' and val == 'LAYER':
                in_layer_table = True
                lay = {}
                continue
            if val == 'ENDTAB':
                in_layer_table = False
            if section == 'BLOCKS' and val == 'BLOCK':
                cur_block = '__pending__'
                blocks[cur_block] = {'ents': [], 'base': (0.0, 0.0)}
                cur = None
                continue
            if section == 'BLOCKS' and val == 'ENDBLK':
                cur_block = None
                continue
            if section in ('ENTITIES', 'BLOCKS'):
                cur = {'type': val, 'pts': []}
            continue
        if section == '?' and code == 2:
            section = val.strip()
            continue
        if in_layer_table and lay is not None:
            if code == 2:
                lay['name'] = val.strip()
            elif code == 62:
                lay['color'] = abs(int(val))
            continue
        if section == 'BLOCKS' and cur_block == '__pending__' and cur is None:
            if code == 2:
                blocks[val.strip()] = blocks.pop('__pending__')
                cur_block = val.strip()
            continue
        if section == 'BLOCKS' and cur is None and cur_block in blocks:
            if code in (10, 20):
                b = blocks[cur_block]['base']
                blocks[cur_block]['base'] = (float(val), b[1]) if code == 10 else (b[0], float(val))
            continue
        if cur is None:
            continue
        try:
            if code == 8:
                cur['layer'] = val.strip()
            elif code == 62:
                cur['color'] = abs(int(val))
            elif code in (1, 2, 3):
                cur[code] = cur.get(code, '') + val if code == 3 else val
            elif cur['type'] in ('LWPOLYLINE', 'SPLINE') and code in (10, 20):
                if code == 10:
                    cur['pts'].append([float(val), 0.0])
                elif cur['pts']:
                    cur['pts'][-1][1] = float(val)
            elif cur['type'] == 'LWPOLYLINE' and code == 42 and cur['pts']:
                cur.setdefault('bulge', {})[len(cur['pts']) - 1] = float(val)
            elif code in (10, 20, 11, 21, 40, 41, 42, 50, 51, 70, 71, 72, 41, 43):
                cur[code] = float(val)
        except ValueError:
            pass
    if section in ('ENTITIES', 'BLOCKS'):
        push()
    for e in entities:
        if not e.get('color') and e.get('layer') in layers:
            e['color'] = layers[e['layer']]
    return entities, blocks


def _tx(pt, ins):
    if not ins:
        return pt
    x, y = pt
    bx, by = ins['base']
    x, y = (x - bx) * ins['sx'], (y - by) * ins['sy']
    a = math.radians(ins['rot'])
    return (ins['x'] + x * math.cos(a) - y * math.sin(a), ins['y'] + x * math.sin(a) + y * math.cos(a))


def _bulge_arc(p1, p2, b, n=12):
    """LWPOLYLINE 凸度段 → 折线点。"""
    if abs(b) < 1e-9:
        return [p2]
    th = 4 * math.atan(b)
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    chord = math.hypot(dx, dy)
    if chord < 1e-12:
        return [p2]
    r = chord / (2 * math.sin(th / 2))
    mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
    h = r * math.cos(th / 2)
    nx, ny = -dy / chord, dx / chord
    cx, cy = mx + nx * h, my + ny * h
    a0 = math.atan2(p1[1] - cy, p1[0] - cx)
    return [(cx + abs(r) * math.cos(a0 + th * i / n) * (1 if r > 0 else 1), cy + abs(r) * math.sin(a0 + th * i / n)) for i in range(1, n + 1)]


def to_prims(entities, blocks, ins=None, depth=0):
    """实体 → 图元：('poly', [(x,y)…], closed, color) / ('circle', cx, cy, r, color) / ('text', x, y, h, rot, s, color)。"""
    prims = []
    for e in entities:
        col = ACI.get(e.get('color') or 7, '#1d2433')
        t = e['type']
        if t == 'LINE':
            prims.append(('poly', [_tx((e.get(10, 0), e.get(20, 0)), ins), _tx((e.get(11, 0), e.get(21, 0)), ins)], False, col))
        elif t in ('LWPOLYLINE', 'POLYLINE'):
            pts = [tuple(p) for p in e['pts']]
            if not pts:
                continue
            closed = bool(int(e.get(70, 0)) & 1)
            res = [pts[0]]
            bul = e.get('bulge') or {}
            seq = pts + ([pts[0]] if closed else [])
            for i in range(1, len(seq)):
                res += _bulge_arc(seq[i - 1], seq[i], bul.get(i - 1, 0)) if bul.get(i - 1) else [seq[i]]
            prims.append(('poly', [_tx(p, ins) for p in res], closed, col))
        elif t == 'CIRCLE':
            c = _tx((e.get(10, 0), e.get(20, 0)), ins)
            prims.append(('circle', c[0], c[1], e.get(40, 0) * abs(ins['sx'] if ins else 1), col))
        elif t == 'ARC':
            cx, cy, r = e.get(10, 0), e.get(20, 0), e.get(40, 0)
            a0, a1 = e.get(50, 0), e.get(51, 360)
            if a1 < a0:
                a1 += 360
            n = max(8, int((a1 - a0) / 6))
            prims.append(('poly', [_tx((cx + r * math.cos(math.radians(a0 + (a1 - a0) * i / n)), cy + r * math.sin(math.radians(a0 + (a1 - a0) * i / n))), ins)
                                   for i in range(n + 1)], False, col))
        elif t == 'ELLIPSE':
            cx, cy, mx, my = e.get(10, 0), e.get(20, 0), e.get(11, 1), e.get(21, 0)
            ratio, s, en = e.get(40, 1), e.get(41, 0), e.get(42, 2 * math.pi)
            if en < s:
                en += 2 * math.pi
            a = math.hypot(mx, my)
            rot = math.atan2(my, mx)
            n = 64
            pts = []
            for i in range(n + 1):
                u = s + (en - s) * i / n
                x, y = a * math.cos(u), a * ratio * math.sin(u)
                pts.append(_tx((cx + x * math.cos(rot) - y * math.sin(rot), cy + x * math.sin(rot) + y * math.cos(rot)), ins))
            prims.append(('poly', pts, False, col))
        elif t == 'SPLINE':
            if e['pts']:
                prims.append(('poly', [_tx(tuple(p), ins) for p in e['pts']], False, col))
        elif t in ('TEXT', 'MTEXT'):
            s = e.get(1, '') if t == 'TEXT' else e.get(3, '') + e.get(1, '')
            s = re.sub(r'\\[A-Za-z][^;\\]*;', '', s).replace('\\P', ' ').replace('{', '').replace('}', '').replace('%%c', 'Ø').replace('%%d', '°').replace('%%p', '±')
            x, y = _tx((e.get(10, 0), e.get(20, 0)), ins)
            prims.append(('text', x, y, e.get(40, 2.5) * abs(ins['sy'] if ins else 1), e.get(50, 0) + (ins['rot'] if ins else 0), s, col))
        elif t == 'POINT':
            x, y = _tx((e.get(10, 0), e.get(20, 0)), ins)
            prims.append(('circle', x, y, 0.3, col))
        elif t == 'INSERT' and depth < 4:
            b = blocks.get(e.get(2, ''))
            if b:
                sub = {'x': e.get(10, 0), 'y': e.get(20, 0), 'sx': e.get(41, 1) or 1, 'sy': e.get(42, 1) or 1, 'rot': e.get(50, 0), 'base': b['base']}
                if ins:
                    p = _tx((sub['x'], sub['y']), ins)
                    sub.update(x=p[0], y=p[1], sx=sub['sx'] * ins['sx'], sy=sub['sy'] * ins['sy'], rot=sub['rot'] + ins['rot'])
                prims += to_prims(b['ents'], blocks, sub, depth + 1)
    return prims


def dxf_to_svg(text, pad_ratio=0.04):
    ents, blocks = parse_dxf(text)
    prims = to_prims(ents, blocks)
    xs, ys = [], []
    for p in prims:
        if p[0] == 'poly':
            xs += [q[0] for q in p[1]]
            ys += [q[1] for q in p[1]]
        elif p[0] == 'circle':
            xs += [p[1] - p[3], p[1] + p[3]]
            ys += [p[2] - p[3], p[2] + p[3]]
        else:
            xs += [p[1], p[1] + len(p[5]) * p[3] * 0.8]
            ys += [p[2], p[2] + p[3]]
    if not xs:
        return _tr('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 60"><text x="10" y="35" font-size="14">DXF 里没有可显示的图元</text></svg>')
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    w, h = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    pad = max(w, h) * pad_ratio
    sw = max(w, h) / 900
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0 - pad:.4f} {-(y1 + pad):.4f} {w + 2 * pad:.4f} {h + 2 * pad:.4f}" '
             f'data-entities="{len(ents)}" data-prims="{len(prims)}" style="background:#fff">',
             f'<g fill="none" stroke-width="{sw:.4f}" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke">']
    for p in prims:
        if p[0] == 'poly':
            d = ' '.join(f'{x:.4f},{-y:.4f}' for x, y in p[1])
            parts.append(f'<{"polygon" if p[2] else "polyline"} points="{d}" stroke="{p[3]}"/>')
        elif p[0] == 'circle':
            parts.append(f'<circle cx="{p[1]:.4f}" cy="{-p[2]:.4f}" r="{p[3]:.4f}" stroke="{p[4]}"/>')
    parts.append('</g>')
    for p in prims:
        if p[0] == 'text':
            parts.append(f'<text x="{p[1]:.4f}" y="{-p[2]:.4f}" font-size="{p[3]:.4f}" fill="{p[6]}" font-family="sans-serif" '
                         f'transform="rotate({-p[4]:.2f} {p[1]:.4f} {-p[2]:.4f})">{escape(p[5])}</text>')
    parts.append('</svg>')
    return '\n'.join(parts)


# ---------------- 演示图纸（seed 用；纯 Python 生成，不依赖外部库） ----------------
def demo_dxf():
    """SGH-450 壳体机加工序图（简化）：外形、缸孔、螺栓孔、油道孔、中心线、尺寸文字。"""
    L = ['0', 'SECTION', '2', 'HEADER', '0', 'ENDSEC', '0', 'SECTION', '2', 'TABLES', '0', 'TABLE', '2', 'LAYER',
         '0', 'LAYER', '2', 'OUTLINE', '62', '7', '0', 'LAYER', '2', 'CENTER', '62', '1', '0', 'LAYER', '2', 'DIM', '62', '5', '0', 'LAYER', '2', 'HOLE', '62', '3',
         '0', 'ENDTAB', '0', 'ENDSEC', '0', 'SECTION', '2', 'BLOCKS',
         '0', 'BLOCK', '8', '0', '2', 'BOLT', '70', '0', '10', '0', '20', '0',
         '0', 'CIRCLE', '8', 'HOLE', '10', '0', '20', '0', '40', '5.5', '0', 'CIRCLE', '8', 'HOLE', '10', '0', '20', '0', '40', '8',
         '0', 'ENDBLK', '0', 'ENDSEC', '0', 'SECTION', '2', 'ENTITIES']

    def ent(t, layer, *kv):
        L.extend(['0', t, '8', layer])
        for k, v in kv:
            L.extend([str(k), f'{v:g}' if isinstance(v, (int, float)) else str(v)])
    pts = [(0, 0), (160, 0), (160, 70), (130, 100), (30, 100), (0, 70)]
    L.extend(['0', 'LWPOLYLINE', '8', 'OUTLINE', '90', str(len(pts)), '70', '1'])
    for x, y in pts:
        L.extend(['10', f'{x:g}', '20', f'{y:g}'])
    ent('CIRCLE', 'OUTLINE', (10, 80), (20, 50), (40, 20))
    ent('CIRCLE', 'HOLE', (10, 80), (20, 50), (40, 12.5))
    ent('ARC', 'OUTLINE', (10, 80), (20, 50), (40, 28), (50, 200), (51, 340))
    for x, y in ((18, 15), (142, 15), (40, 88), (120, 88)):
        ent('INSERT', 'HOLE', (2, 'BOLT'), (10, x), (20, y))
    ent('ELLIPSE', 'HOLE', (10, 80), (20, 18), (11, 9), (21, 0), (40, 0.45), (41, 0), (42, 6.283185))
    ent('LINE', 'CENTER', (10, -8), (20, 50), (11, 168), (21, 50))
    ent('LINE', 'CENTER', (10, 80), (20, -8), (11, 80), (21, 108))
    ent('LINE', 'DIM', (10, 0), (20, -14), (11, 160), (21, -14))
    ent('TEXT', 'DIM', (10, 64), (20, -12), (40, 5), (1, '160 ±0.1'))
    ent('TEXT', 'DIM', (10, 96), (20, 56), (40, 4.5), (1, '%%c40 H7 (+0.025/0)'))
    ent('TEXT', 'DIM', (10, 4), (20, 104), (40, 4.5), (1, 'SGH-450 OP60 CNC  4x M10  Ra1.6'))
    ent('TEXT', 'DIM', (10, 172), (20, 20), (40, 4), (50, 90), (1, 'FLATNESS 0.03'))
    L.extend(['0', 'ENDSEC', '0', 'EOF'])
    return '\n'.join(L) + '\n'


def demo_pdf():
    """一页 A4 横向的检验图（矢量 + 英文标注），手写最小 PDF。"""
    ops = ['1 w 0.1 0.15 0.25 RG', '60 80 m 560 80 l 560 330 l 470 430 l 150 430 l 60 330 h S', '310 255 m 310 255 l S']
    k = 0.5523
    def circ(cx, cy, r):
        return (f'{cx + r} {cy} m {cx + r} {cy + r * k} {cx + r * k} {cy + r} {cx} {cy + r} c {cx - r * k} {cy + r} {cx - r} {cy + r * k} {cx - r} {cy} c '
                f'{cx - r} {cy - r * k} {cx - r * k} {cy - r} {cx} {cy - r} c {cx + r * k} {cy - r} {cx + r} {cy - r * k} {cx + r} {cy} c S')
    ops += [circ(310, 255, 70), circ(310, 255, 44), circ(110, 130, 14), circ(510, 130, 14), circ(190, 400, 14), circ(430, 400, 14)]
    ops += ['0.8 0.2 0.2 RG [6 3] 0 d 40 255 m 580 255 l S 310 60 m 310 450 l S [] 0 d']
    ops += ['0 0 0 rg BT /F1 18 Tf 60 520 Td (SGH-450 Housing - Inspection Drawing / OP80) Tj ET',
            'BT /F1 11 Tf 60 500 Td (Bore D40 H7 +0.025/0  |  Flatness 0.03  |  Oil-hole position 0.1  |  Burr <= 0.1 mm) Tj ET',
            'BT /F1 10 Tf 600 120 Td (Rev V3  2026-10  QA) Tj ET']
    content = '\n'.join(ops).encode('latin-1')
    objs = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
            b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 842 595] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>',
            b'<< /Length ' + str(len(content)).encode() + b' >>\nstream\n' + content + b'\nendstream',
            b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    pdf = b'%PDF-1.4\n'
    offs = []
    for i, o in enumerate(objs, 1):
        offs.append(len(pdf))
        pdf += f'{i} 0 obj\n'.encode() + o + b'\nendobj\n'
    xref = len(pdf)
    pdf += f'xref\n0 {len(objs) + 1}\n0000000000 65535 f \n'.encode() + b''.join(f'{o:010d} 00000 n \n'.encode() for o in offs)
    pdf += f'trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()
    return pdf


def demo_png(w=640, h=400):
    """毛坯图（示意）：浅底、壳体轮廓、缸孔、浇口位置，纯 Python 写 PNG。"""
    bg, ink, acc, blue = (248, 250, 252), (40, 52, 70), (217, 130, 43), (47, 95, 208)
    px = [[bg] * w for _ in range(h)]

    def dot(x, y, c, r=1):
        for dy in range(-r, r + 1):
            for dx in range(-r, r + 1):
                if 0 <= x + dx < w and 0 <= y + dy < h:
                    px[y + dy][x + dx] = c

    def line(x0, y0, x1, y1, c, r=1):
        n = int(max(abs(x1 - x0), abs(y1 - y0))) or 1
        for i in range(n + 1):
            dot(int(x0 + (x1 - x0) * i / n), int(y0 + (y1 - y0) * i / n), c, r)

    def circle(cx, cy, rr, c, r=1):
        n = int(2 * math.pi * rr)
        for i in range(n):
            a = 2 * math.pi * i / n
            dot(int(cx + rr * math.cos(a)), int(cy + rr * math.sin(a)), c, r)
    for x in range(0, w, 20):
        for y in range(0, h, 20):
            px[y][x] = (220, 226, 234)
    poly = [(80, 330), (560, 330), (560, 140), (470, 60), (170, 60), (80, 140), (80, 330)]
    for a, b in zip(poly, poly[1:]):
        line(*a, *b, ink, 2)
    circle(320, 200, 80, ink, 2)
    circle(320, 200, 50, blue, 1)
    for cx, cy in ((130, 290), (510, 290), (200, 100), (440, 100)):
        circle(cx, cy, 16, ink, 1)
    line(300, 30, 300, 60, acc, 3)
    line(340, 30, 340, 60, acc, 3)
    line(300, 30, 340, 30, acc, 3)
    raw = b''.join(b'\x00' + bytes(v for p in row for v in p) for row in px)

    def chunk(t, d):
        return struct.pack('>I', len(d)) + t + d + struct.pack('>I', zlib.crc32(t + d) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b'')
