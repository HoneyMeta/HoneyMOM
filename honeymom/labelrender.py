"""标签模板渲染：模板 JSON（毫米坐标，左上角为原点）→ SVG / ZPL / TSPL / PDF。

模板：{"width_mm":100,"height_mm":60,"elements":[{type: text|barcode|line|box|image, ...}]}
- text    x,y,h,text,bold,align(left|center|right),w(对齐用框宽，可选),rotate(0|90|180|270，顺时针，旋转后仍以 x,y 为外框左上角)
- barcode x,y,w,h,symbology(code128|qr|datamatrix),data,hr(一维码下方显示文字),gs1,ecc(QR 纠错，默认 M)
          GS1 数据可写 '\\x1d' 分隔，也可写 "(01)04012345678901(10)ABC" 括号形式
- line    x,y,x2,y2,t（线宽 mm）
- box     x,y,w,h,t,fill
- image   x,y,w,h,src（data:image/...;base64,...）
占位符 {{key}} / {{a.b}} / {{key|默认}}；缺失或空 → 默认值或 ''。
条码不含静区，模板里请自己留白（一维码两侧≥10 模块，QR≥4，DM≥1）。
"""
from .i18n import _tr
import base64
import io
import math
import re
import unicodedata
from xml.sax.saxutils import escape as _xml_escape

from . import barcode as bc

TYPES = ('text', 'barcode', 'line', 'box', 'image')
SVG_FONT = 'Microsoft YaHei, SimHei, sans-serif'
_PH = re.compile(r'\{\{\s*([^{}|]*?)\s*(?:\|([^{}]*))?\}\}')
_ROT_ZPL = {0: 'N', 90: 'R', 180: 'I', 270: 'B'}

# ====================== 基础工具 ======================


def dots(mm: float, dpi: int = 203) -> int:
    """毫米 → 打印点数（四舍五入，0.5 进位）。"""
    return int(math.floor(float(mm) * dpi / 25.4 + 0.5))


def _lookup(data, key: str):
    cur = data
    for part in key.split('.'):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, (list, tuple)) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def fill(text, data) -> str:
    """替换 {{key}} / {{a.b}} / {{key|默认}}。"""
    if text is None:
        return ''

    def rep(m):
        v = _lookup(data or {}, m.group(1))
        if v is None or v == '':
            return m.group(2).strip() if m.group(2) is not None else ''
        return str(v)
    return _PH.sub(rep, str(text))


def _num(el: dict, key: str, default: float = 0.0) -> float:
    v = el.get(key)
    if v is None or v == '':
        return float(default)
    return float(v)


def _n(v: float) -> str:
    """SVG 数字：最多 3 位小数，去尾零。"""
    s = f'{v:.3f}'.rstrip('0').rstrip('.')
    return '0' if s in ('-0', '') else s


def _char_w(c: str) -> float:
    return 1.0 if unicodedata.east_asian_width(c) in ('W', 'F') else 0.55


def _text_w(s: str, h: float) -> float:
    """估算文字宽度（mm）：全角按 1 个字高，半角按 0.55。"""
    return sum(_char_w(c) for c in s) * h


def _rot(el) -> int:
    r = int(_num(el, 'rotate', 0)) % 360
    return r if r in _ROT_ZPL else 0


def _rot_offset(rot: int, L: float, h: float) -> tuple[float, float]:
    """旋转后让外框左上角仍落在 (x,y)：返回未旋转文字左上角相对 (x,y) 的平移。"""
    return {0: (0, 0), 90: (h, 0), 180: (L, h), 270: (0, L)}[rot]


def _align_dx(align: str, box_w: float, text_w: float) -> float:
    if align == 'center':
        return (box_w - text_w) / 2
    if align == 'right':
        return box_w - text_w
    return 0.0


_GS1_FIXED = ('00', '01', '02', '03', '04', '11', '12', '13', '14', '15', '16', '17', '18', '19', '20',
              '31', '32', '33', '34', '35', '36', '41')


def _gs1_data(s: str) -> tuple[str, str]:
    """GS1：返回 (编码用数据[\\x1d 分隔], 人读文字)。支持 '(01)…(10)…' 括号写法。"""
    if s.startswith('('):
        parts = re.findall(r'\((\d{2,4})\)([^(]*)', s)
        if parts:
            out = ''
            for i, (ai, val) in enumerate(parts):
                out += ai + val
                if i < len(parts) - 1 and ai[:2] not in _GS1_FIXED:
                    out += '\x1d'
            return out, s
    return s, s.replace('\x1d', '')


def _code(el: dict, data: dict):
    """取条码：返回 None（无数据）或 dict(sym, kind, mods, data, hr_text, gs1, ecc)。"""
    txt = fill(el.get('data', ''), data)
    if txt == '':
        return None
    sym = bc.normalize(el.get('symbology') or 'code128')
    gs1 = bool(el.get('gs1')) and sym != 'qr'
    hr_text = txt
    if gs1:
        txt, hr_text = _gs1_data(txt)
    ecc = str(el.get('ecc') or 'M').upper()
    kind, mods = bc.matrix_for(sym, txt, gs1=gs1, ecc=ecc)
    return {'sym': sym, 'kind': kind, 'mods': mods, 'data': txt, 'hr_text': hr_text, 'gs1': gs1, 'ecc': ecc}


def _hr_h(h: float) -> float:
    """一维码人读文字高度（mm）。"""
    return max(2.0, min(3.5, h * 0.22))


def _bars_h(el: dict) -> float:
    h = _num(el, 'h', 10)
    return h - _hr_h(h) - 0.4 if el.get('hr') else h


def _runs(row) -> list[tuple[int, int]]:
    """一行中连续深色段 [(起点, 长度)]；row 可以是 '1'/'0' 串或 bool 列表。"""
    out, i, n = [], 0, len(row)
    while i < n:
        if row[i] in (True, '1'):
            j = i
            while j < n and row[j] in (True, '1'):
                j += 1
            out.append((i, j - i))
            i = j
        else:
            i += 1
    return out


def _image_bytes(src: str) -> bytes | None:
    if not isinstance(src, str) or not src.startswith('data:'):
        return None
    head, _, body = src.partition(',')
    try:
        return base64.b64decode(body) if ';base64' in head else body.encode('latin-1')
    except Exception:
        return None


def _mono_rows(raw: bytes, wd: int, hd: int):
    """图片 → wd×hd 一位图（True=黑）。没有 pillow 返回 None。"""
    try:
        from PIL import Image
    except ImportError:
        return None
    im = Image.open(io.BytesIO(raw))
    if im.mode in ('RGBA', 'LA', 'P'):
        im = im.convert('RGBA')
        bg = Image.new('RGBA', im.size, (255, 255, 255, 255))
        bg.alpha_composite(im)
        im = bg
    im = im.convert('L').resize((max(1, wd), max(1, hd)))
    px = im.load()
    return [[px[x, y] < 128 for x in range(im.size[0])] for y in range(im.size[1])]


def _pack(rows, black_bit: int) -> tuple[int, bytes]:
    """一位图打包成字节：black_bit=1 时黑为 1（ZPL），=0 时黑为 0（TSPL）。"""
    bpr = (len(rows[0]) + 7) // 8
    out = bytearray()
    for row in rows:
        for b in range(bpr):
            v = 0
            for k in range(8):
                x = b * 8 + k
                dark = x < len(row) and row[x]
                bit = (1 if dark else 0) if black_bit else (0 if dark else 1)
                v = (v << 1) | bit
            out.append(v)
    return bpr, bytes(out)

# ====================== 校验 ======================


def validate(tpl: dict) -> list[str]:
    """检查模板问题，返回中文描述列表（空列表 = 没问题）。"""
    probs = []
    try:
        W, H = float(tpl.get('width_mm') or 0), float(tpl.get('height_mm') or 0)
    except (TypeError, ValueError):
        return [_tr('标签宽高必须是数字')]
    if W <= 0 or H <= 0:
        probs.append(_tr('标签宽高必须为正数'))
    els = tpl.get('elements')
    if not isinstance(els, list):
        return probs + [_tr('elements 必须是列表')]
    tol = 0.01
    for i, el in enumerate(els):
        tag = _tr('第{v}个元素', v=i + 1)
        if not isinstance(el, dict):
            probs.append(_tr('{tag}：不是对象', tag=tag))
            continue
        t = el.get('type')
        if t not in TYPES:
            probs.append(_tr('{tag}：未知类型 {t!r}', tag=tag, t=t))
            continue
        tag = f'{tag}({t})'
        try:
            x, y = _num(el, 'x'), _num(el, 'y')
            if t == 'text':
                h = _num(el, 'h', 4)
                txt = str(el.get('text') or '')
                if not txt:
                    probs.append(_tr('{tag}：text 为空', tag=tag))
                if h <= 0:
                    probs.append(_tr('{tag}：字高 h 必须 > 0', tag=tag))
                if el.get('align', 'left') not in ('left', 'center', 'right'):
                    probs.append(_tr('{tag}：align 只能是 left/center/right', tag=tag))
                if int(_num(el, 'rotate', 0)) % 360 not in _ROT_ZPL:
                    probs.append(_tr('{tag}：rotate 只能是 0/90/180/270', tag=tag))
                L = _num(el, 'w', 0) or (_text_w(fill(txt, {}), h) if '{{' not in txt else 0)
                bw, bh = (L, h) if _rot(el) in (0, 180) else (h, L)
                box = (x, y, x + bw, y + bh)
            elif t == 'line':
                x2, y2 = _num(el, 'x2', x), _num(el, 'y2', y)
                if _num(el, 't', 0.3) <= 0:
                    probs.append(_tr('{tag}：线宽 t 必须 > 0', tag=tag))
                box = (min(x, x2), min(y, y2), max(x, x2), max(y, y2))
            else:
                w, h = _num(el, 'w'), _num(el, 'h')
                if w <= 0 or h <= 0:
                    probs.append(_tr('{tag}：宽高必须 > 0', tag=tag))
                box = (x, y, x + w, y + h)
                if t == 'image' and _image_bytes(el.get('src')) is None:
                    probs.append(_tr('{tag}：src 需要 data:image/...;base64 数据', tag=tag))
                if t == 'barcode':
                    raw = str(el.get('data') or '')
                    try:
                        sym = bc.normalize(el.get('symbology') or 'code128')
                    except ValueError as e:
                        probs.append(_tr('{tag}：{e}', tag=tag, e=e))
                        sym = None
                    if not raw.strip():
                        probs.append(_tr('{tag}：缺少 data', tag=tag))
                    elif sym and '{{' not in raw:
                        try:
                            c = _code(el, {})
                            if c['kind'] == '1d':
                                mw = w / len(c['mods'])
                            else:
                                mw = min(w, h) / len(c['mods'])
                            if mw < 0.19:
                                probs.append(_tr('{tag}：模块宽 {mw:.2f} mm 太小，可能扫不出（建议 ≥0.25 mm）', tag=tag, mw=mw))
                        except ValueError as e:
                            probs.append(_tr('{tag}：{e}', tag=tag, e=e))
            if box[0] < -tol or box[1] < -tol or box[2] > W + tol or box[3] > H + tol:
                probs.append(_tr('{tag}：超出标签范围', tag=tag))
        except (TypeError, ValueError):
            probs.append(_tr('{tag}：坐标/尺寸必须是数字', tag=tag))
    return probs

# ====================== SVG ======================


def render_svg(tpl: dict, data: dict | None = None) -> str:
    data = data or {}
    W, H = float(tpl['width_mm']), float(tpl['height_mm'])
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{_n(W)}mm" height="{_n(H)}mm" viewBox="0 0 {_n(W)} {_n(H)}">',
         f'<rect x="0" y="0" width="{_n(W)}" height="{_n(H)}" fill="#fff"/>']
    for el in tpl.get('elements') or []:
        t = el.get('type')
        x, y = _num(el, 'x'), _num(el, 'y')
        if t == 'text':
            s = fill(el.get('text'), data)
            if not s:
                continue
            h = _num(el, 'h', 4)
            rot = _rot(el)
            tw = _text_w(s, h)
            L = _num(el, 'w', 0) or tw
            align = el.get('align') or 'left'
            anchor, ux = {'center': ('middle', L / 2), 'right': ('end', L)}.get(align, ('start', 0)) if el.get('w') else ('start', 0)
            dx, dy = _rot_offset(rot, L, h)
            tr = f'translate({_n(x + dx)} {_n(y + dy)})' + (f' rotate({rot})' if rot else '')
            bold = ' font-weight="bold"' if el.get('bold') else ''
            anc = f' text-anchor="{anchor}"' if anchor != 'start' else ''
            o.append(f'<text transform="{tr}" x="{_n(ux)}" y="{_n(h * 0.8)}" font-size="{_n(h)}" '
                     f'font-family="{SVG_FONT}"{bold}{anc} fill="#000">{_xml_escape(s)}</text>')
        elif t == 'barcode':
            c = _code(el, data)
            if not c:
                continue
            w, h = _num(el, 'w'), _num(el, 'h')
            rects = []
            if c['kind'] == '1d':
                mods = c['mods']
                mw = w / len(mods)
                bh = _bars_h(el)
                for i, k in _runs(mods):
                    rects.append(f'<rect x="{_n(x + i * mw)}" y="{_n(y)}" width="{_n(k * mw)}" height="{_n(bh)}"/>')
            else:
                m = c['mods']
                mw = min(w, h) / len(m)
                for r, row in enumerate(m):
                    for i, k in _runs(row):
                        rects.append(f'<rect x="{_n(x + i * mw)}" y="{_n(y + r * mw)}" width="{_n(k * mw)}" height="{_n(mw)}"/>')
            o.append('<g fill="#000" shape-rendering="crispEdges">' + ''.join(rects) + '</g>')
            if c['kind'] == '1d' and el.get('hr'):
                th = _hr_h(h)
                o.append(f'<text x="{_n(x + w / 2)}" y="{_n(y + _bars_h(el) + 0.4 + th * 0.8)}" font-size="{_n(th)}" '
                         f'font-family="{SVG_FONT}" text-anchor="middle" fill="#000">{_xml_escape(c["hr_text"])}</text>')
        elif t == 'line':
            o.append(f'<line x1="{_n(x)}" y1="{_n(y)}" x2="{_n(_num(el, "x2", x))}" y2="{_n(_num(el, "y2", y))}" '
                     f'stroke="#000" stroke-width="{_n(_num(el, "t", 0.3))}"/>')
        elif t == 'box':
            w, h, th = _num(el, 'w'), _num(el, 'h'), _num(el, 't', 0.3)
            if el.get('fill'):
                o.append(f'<rect x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" height="{_n(h)}" fill="#000"/>')
            else:   # 线宽画在框内（与 ZPL ^GB 一致）
                o.append(f'<rect x="{_n(x + th / 2)}" y="{_n(y + th / 2)}" width="{_n(max(0, w - th))}" height="{_n(max(0, h - th))}" '
                         f'fill="none" stroke="#000" stroke-width="{_n(th)}"/>')
        elif t == 'image':
            src = el.get('src') or ''
            if src.startswith('data:image/'):
                o.append(f'<image x="{_n(x)}" y="{_n(y)}" width="{_n(_num(el, "w"))}" height="{_n(_num(el, "h"))}" '
                         f'preserveAspectRatio="none" href="{_xml_escape(src, {chr(34): "&quot;"})}"/>')
    o.append('</svg>')
    return '\n'.join(o)

# ====================== ZPL ======================


def _zfd(s: str, prefix: str = '') -> str:
    """^FD 内容：含 ^ 或 ~ 时用 ^FH 十六进制转义。"""
    if '^' in s or '~' in s:
        return '^FH_^FD' + prefix + s.replace('_', '_5F').replace('^', '_5E').replace('~', '_7E')
    return '^FD' + prefix + s


def _c128_invocations(vals: list[int], style: str) -> str:
    """把码字序列翻成打印机的手动子集写法。style='zpl'（>; >8 …）或 'tspl'（128M：!105 !102 …）。"""
    zpl = style == 'zpl'
    out, cur = [], None
    start = {103: 'A', 104: 'B', 105: 'C'}
    for i, v in enumerate(vals):
        if i == 0:
            cur = start[v]
            out.append({'A': '>9', 'B': '>:', 'C': '>;'}[cur] if zpl else f'!{v:03d}')
            continue
        if v == 102:
            out.append('>8' if zpl else '!102')
        elif cur == 'C' and v < 100:
            out.append(f'{v:02d}')
        elif v == 99:
            out.append('>5' if zpl else '!099')
            cur = 'C'
        elif (v == 100 and cur != 'B') or (v == 101 and cur != 'A'):
            nxt = 'B' if v == 100 else 'A'
            out.append(({'B': '>6', 'A': '>7'}[nxt]) if zpl else f'!{v:03d}')
            cur = nxt
        else:
            ch = chr(v + 32) if (cur == 'B' or v < 64) else chr(v - 64)
            if zpl and ch == '>':
                ch = '><'
            out.append(ch)
    return ''.join(out)


def render_zpl(tpl: dict, data: dict | None = None, dpi: int = 203, copies: int = 1,
               cjk_font: str = 'E:SIMSUN.TTF', darkness: int | None = None) -> bytes:
    data = data or {}
    D = lambda mm: dots(mm, dpi)  # noqa: E731
    o = ['^XA', '^CI28']
    if darkness is not None:
        o.append(f'~SD{int(darkness):02d}')
    o += [f'^PW{D(tpl["width_mm"])}', f'^LL{D(tpl["height_mm"])}', '^LH0,0']
    for el in tpl.get('elements') or []:
        t = el.get('type')
        x, y = _num(el, 'x'), _num(el, 'y')
        if t == 'text':
            s = fill(el.get('text'), data)
            if not s:
                continue
            h = _num(el, 'h', 4)
            hd = max(10, D(h))
            rot = _rot(el)
            r = _ROT_ZPL[rot]
            font = f'^A0{r},{hd},{hd}' if s.isascii() else f'^A@{r},{hd},{hd},{cjk_font}'
            fb = ''
            if el.get('w'):
                fb = f'^FB{D(_num(el, "w"))},1,0,{ {"center": "C", "right": "R"}.get(el.get("align"), "L") },0'
            passes = [(0, 0), (1, 0) if rot in (0, 180) else (0, 1)] if el.get('bold') else [(0, 0)]
            for ox, oy in passes:   # 粗体 = 错 1 点重打
                o.append(f'^FO{D(x) + ox},{D(y) + oy}{font}{fb}{_zfd(s)}^FS')
        elif t == 'barcode':
            c = _code(el, data)
            if not c:
                continue
            w, h = _num(el, 'w'), _num(el, 'h')
            if c['kind'] == '1d':
                m = max(1, min(10, D(w) // len(c['mods'])))
                hr = 'Y' if el.get('hr') else 'N'
                if c['gs1']:
                    fd = '^FD' + _c128_invocations(bc.code128_values(c['data'], True), 'zpl')
                    o.append(f'^FO{D(x)},{D(y)}^BY{m},2^BCN,{D(_bars_h(el))},{hr},N,N{fd}^FS')
                else:
                    o.append(f'^FO{D(x)},{D(y)}^BY{m},2^BCN,{D(_bars_h(el))},{hr},N,N,A{_zfd(c["data"])}^FS')
            elif c['sym'] == 'qr':
                mag = max(1, min(10, D(min(w, h)) // len(c['mods'])))
                o.append(f'^FO{D(x)},{D(y)}^BQN,2,{mag}{_zfd(c["data"], c["ecc"] + "A,")}^FS')
            else:
                mod = max(1, D(min(w, h)) // len(c['mods']))
                if c['gs1']:
                    o.append(f'^FO{D(x)},{D(y)}^BXN,{mod},200,0,0,6,_^FD_1{c["data"].replace(chr(29), "_1")}^FS')
                else:
                    o.append(f'^FO{D(x)},{D(y)}^BXN,{mod},200{_zfd(c["data"])}^FS')
        elif t == 'line':
            x2, y2, th = _num(el, 'x2', x), _num(el, 'y2', y), max(1, D(_num(el, 't', 0.3)))
            if abs(y2 - y) < 1e-9:
                o.append(f'^FO{D(min(x, x2))},{max(0, D(y) - th // 2)}^GB{max(th, D(abs(x2 - x)))},{th},{th}^FS')
            elif abs(x2 - x) < 1e-9:
                o.append(f'^FO{max(0, D(x) - th // 2)},{D(min(y, y2))}^GB{th},{max(th, D(abs(y2 - y)))},{th}^FS')
            else:
                lean = 'L' if (x2 - x) * (y2 - y) > 0 else 'R'
                o.append(f'^FO{D(min(x, x2))},{D(min(y, y2))}^GD{D(abs(x2 - x))},{D(abs(y2 - y))},{th},B,{lean}^FS')
        elif t == 'box':
            wd, hd = D(_num(el, 'w')), D(_num(el, 'h'))
            th = min(wd, hd) if el.get('fill') else max(1, D(_num(el, 't', 0.3)))
            o.append(f'^FO{D(x)},{D(y)}^GB{max(wd, th)},{max(hd, th)},{th}^FS')
        elif t == 'image':
            raw = _image_bytes(el.get('src'))
            rows = _mono_rows(raw, D(_num(el, 'w')), D(_num(el, 'h'))) if raw else None
            if rows is None:
                o.append(_tr('^FX image skipped (需要 pillow 和 data URI)^FS'))
                continue
            bpr, packed = _pack(rows, 1)
            o.append(f'^FO{D(x)},{D(y)}^GFA,{len(packed)},{len(packed)},{bpr},{packed.hex().upper()}^FS')
    o += [f'^PQ{max(1, int(copies))}', '^XZ']
    return ('\n'.join(o) + '\n').encode('utf-8')

# ====================== TSPL ======================


def _mmfmt(v: float) -> str:
    v = float(v)
    return str(int(v)) if v == int(v) else f'{v:.2f}'.rstrip('0').rstrip('.')


def _tq(s: str) -> str:
    return '"' + s.replace('"', '\\["]') + '"'


def render_tspl(tpl: dict, data: dict | None = None, dpi: int = 203, copies: int = 1,
                cjk_font: str = 'TSS24.BF2', gap_mm: float = 2) -> bytes:
    data = data or {}
    D = lambda mm: dots(mm, dpi)  # noqa: E731
    out: list[bytes] = []

    def put(line: str):
        out.append(line.encode('gbk', errors='replace') + b'\r\n')

    put(f'SIZE {_mmfmt(tpl["width_mm"])} mm,{_mmfmt(tpl["height_mm"])} mm')
    put(f'GAP {_mmfmt(gap_mm)} mm,0 mm')
    put('DIRECTION 1')
    put('CLS')
    for el in tpl.get('elements') or []:
        t = el.get('type')
        x, y = _num(el, 'x'), _num(el, 'y')
        if t == 'text':
            s = fill(el.get('text'), data)
            if not s:
                continue
            h = _num(el, 'h', 4)
            hd = D(h)
            rot = _rot(el)
            if s.isascii():   # 内置 "3" 号字 16x24 点
                font, mul = '3', max(1, round(hd / 24))
                tw = len(s) * 16 * mul
            else:
                font, mul = cjk_font, max(1, round(hd / 24))
                tw = sum(24 if _char_w(c) == 1.0 else 12 for c in s) * mul
            th = 24 * mul
            L = D(_num(el, 'w')) if el.get('w') else tw
            ux = int(_align_dx(el.get('align') or 'left', L, tw)) if el.get('w') else 0
            dx, dy = _rot_offset(rot, L, th)
            # 对齐偏移沿文字方向
            ax, ay = {0: (ux, 0), 90: (0, ux), 180: (-ux, 0), 270: (0, -ux)}[rot]
            px, py = D(x) + int(dx) + ax, D(y) + int(dy) + ay
            passes = [(0, 0), (1, 0) if rot in (0, 180) else (0, 1)] if el.get('bold') else [(0, 0)]
            for ox, oy in passes:
                put(f'TEXT {px + ox},{py + oy},"{font}",{rot},{mul},{mul},{_tq(s)}')
        elif t == 'barcode':
            c = _code(el, data)
            if not c:
                continue
            w, h = _num(el, 'w'), _num(el, 'h')
            if c['kind'] == '1d':
                m = max(1, min(10, D(w) // len(c['mods'])))
                hr = 2 if el.get('hr') else 0
                if c['gs1']:
                    put(f'BARCODE {D(x)},{D(y)},"128M",{D(_bars_h(el))},{hr},0,{m},{m},'
                        f'{_tq(_c128_invocations(bc.code128_values(c["data"], True), "tspl"))}')
                else:
                    put(f'BARCODE {D(x)},{D(y)},"128",{D(_bars_h(el))},{hr},0,{m},{m},{_tq(c["data"])}')
            elif c['sym'] == 'qr':
                cell = max(1, min(10, D(min(w, h)) // len(c['mods'])))
                put(f'QRCODE {D(x)},{D(y)},{c["ecc"]},{cell},A,0,{_tq(c["data"])}')
            else:
                sd = D(min(w, h))
                mod = max(1, sd // len(c['mods']))
                if c['gs1']:
                    put(f'DMATRIX {D(x)},{D(y)},{sd},{sd},c126,x{mod},{_tq("~1" + c["data"].replace(chr(29), "~1"))}')
                else:
                    put(f'DMATRIX {D(x)},{D(y)},{sd},{sd},x{mod},{_tq(c["data"])}')
        elif t == 'line':
            x2, y2, th = _num(el, 'x2', x), _num(el, 'y2', y), max(1, D(_num(el, 't', 0.3)))
            if abs(y2 - y) < 1e-9:
                put(f'BAR {D(min(x, x2))},{max(0, D(y) - th // 2)},{max(th, D(abs(x2 - x)))},{th}')
            elif abs(x2 - x) < 1e-9:
                put(f'BAR {max(0, D(x) - th // 2)},{D(min(y, y2))},{th},{max(th, D(abs(y2 - y)))}')
            else:
                put(f'DIAGONAL {D(x)},{D(y)},{D(x2)},{D(y2)},{th}')
        elif t == 'box':
            wd, hd = D(_num(el, 'w')), D(_num(el, 'h'))
            if el.get('fill'):
                put(f'BAR {D(x)},{D(y)},{wd},{hd}')
            else:
                put(f'BOX {D(x)},{D(y)},{D(x) + wd},{D(y) + hd},{max(1, D(_num(el, "t", 0.3)))}')
        elif t == 'image':
            raw = _image_bytes(el.get('src'))
            rows = _mono_rows(raw, D(_num(el, 'w')), D(_num(el, 'h'))) if raw else None
            if rows is None:
                continue   # 没有 pillow / 非 data URI：跳过
            bpr, packed = _pack(rows, 0)
            out.append(f'BITMAP {D(x)},{D(y)},{bpr},{len(rows)},0,'.encode('ascii') + packed + b'\r\n')
    put(f'PRINT {max(1, int(copies))},1')
    return b''.join(out)

# ====================== PDF ======================


def render_pdf(tpl: dict, data_list: list[dict]) -> bytes:
    """每个数据一页，页面尺寸 = 标签尺寸；条码/线框为矢量。"""
    try:
        import fitz
    except ImportError:
        raise RuntimeError(_tr('需要 pymupdf'))
    k = 72 / 25.4
    W, H = float(tpl['width_mm']), float(tpl['height_mm'])
    doc = fitz.open()

    def segs(s, bold):
        """拆成 ASCII / 非 ASCII 段：ASCII 用 Helvetica（china-s 的西文是全角等宽，太宽），其余用 china-s。"""
        out = []
        for ch in s:
            f = ('hebo' if bold else 'helv') if ch.isascii() else 'china-s'
            if out and out[-1][0] == f:
                out[-1][1] += ch
            else:
                out.append([f, ch])
        return out

    def seg_len(f, t, fs):
        try:
            return fitz.get_text_length(t, fontname=f, fontsize=fs)
        except Exception:
            return _text_w(t, fs)

    def put_text(page, s, x, y, h, rot=0, box_w=None, align='left', bold=False):
        fs = h * k
        parts = [(f, t, seg_len(f, t, fs)) for f, t in segs(s, bold)]
        tw = sum(p[2] for p in parts) / k
        L = box_w or tw
        ux = _align_dx(align, L, tw) if box_w else 0
        dx, dy = _rot_offset(rot, L, h)
        pivot = fitz.Point((x + dx) * k, (y + dy) * k)
        morph = (pivot, fitz.Matrix(-rot)) if rot else None   # fitz 角度为逆时针，这里要顺时针
        px = (x + dx + ux) * k
        for f, t, wpt in parts:
            kw = {'render_mode': 2, 'border_width': 0.03} if (bold and f == 'china-s') else {}
            page.insert_text(fitz.Point(px, (y + dy + h * 0.8) * k), t, fontsize=fs, fontname=f,
                             color=(0, 0, 0), morph=morph, **kw)
            px += wpt

    for data in (data_list or [{}]):
        page = doc.new_page(width=W * k, height=H * k)
        for el in tpl.get('elements') or []:
            t = el.get('type')
            x, y = _num(el, 'x'), _num(el, 'y')
            if t == 'text':
                s = fill(el.get('text'), data)
                if s:
                    put_text(page, s, x, y, _num(el, 'h', 4), _rot(el), _num(el, 'w', 0) or None,
                             el.get('align') or 'left', bool(el.get('bold')))
            elif t == 'barcode':
                c = _code(el, data)
                if not c:
                    continue
                w, h = _num(el, 'w'), _num(el, 'h')
                sh = page.new_shape()
                if c['kind'] == '1d':
                    mw, bh = w / len(c['mods']), _bars_h(el)
                    for i, n in _runs(c['mods']):
                        sh.draw_rect(fitz.Rect((x + i * mw) * k, y * k, (x + (i + n) * mw) * k, (y + bh) * k))
                else:
                    mw = min(w, h) / len(c['mods'])
                    for r, row in enumerate(c['mods']):
                        for i, n in _runs(row):
                            sh.draw_rect(fitz.Rect((x + i * mw) * k, (y + r * mw) * k, (x + (i + n) * mw) * k, (y + (r + 1) * mw) * k))
                sh.finish(color=None, fill=(0, 0, 0), width=0)
                sh.commit()
                if c['kind'] == '1d' and el.get('hr'):
                    put_text(page, c['hr_text'], x, y + _bars_h(el) + 0.4, _hr_h(h), 0, w, 'center')
            elif t == 'line':
                page.draw_line(fitz.Point(x * k, y * k), fitz.Point(_num(el, 'x2', x) * k, _num(el, 'y2', y) * k),
                               color=(0, 0, 0), width=_num(el, 't', 0.3) * k)
            elif t == 'box':
                w, h, th = _num(el, 'w'), _num(el, 'h'), _num(el, 't', 0.3)
                if el.get('fill'):
                    page.draw_rect(fitz.Rect(x * k, y * k, (x + w) * k, (y + h) * k), color=None, fill=(0, 0, 0), width=0)
                else:
                    page.draw_rect(fitz.Rect((x + th / 2) * k, (y + th / 2) * k, (x + w - th / 2) * k, (y + h - th / 2) * k),
                                   color=(0, 0, 0), width=th * k)
            elif t == 'image':
                raw = _image_bytes(el.get('src'))
                if raw:
                    page.insert_image(fitz.Rect(x * k, y * k, (x + _num(el, 'w')) * k, (y + _num(el, 'h')) * k),
                                      stream=raw, keep_proportion=False)
    doc.set_metadata({})
    return doc.tobytes(deflate=True, garbage=3)
