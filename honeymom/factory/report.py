"""联调验收报告（接入中心 → 生成验收报告）：Word（python-docx）和 PDF（Pillow 排版成页面图像再存 PDF，服务器不需要 Office）。

内容：项目概况、数据源与连接、点位覆盖率（已确认 / 总数）、每台设备的覆盖率与问题、未通过的点（读不到 / 超范围 / 值质量问题）、
待确认队列、工厂模型版本、签字栏（实施方 / 集成商 / 客户）。版式按正式文档要求：全黑字、宋体正文、黑体标题、（1）编号。
"""
from __future__ import annotations

from ..i18n import _tr
import io
import os
from datetime import datetime

from .. import db
from ..db import clock


def collect(c, user=None) -> dict:
    from . import live
    from ..schema_factory import factory_version_t as vt
    import sqlalchemy as sa
    chk = live.check(c, user=user)
    pl = db.rows(c, 'plant', limit=1)
    company = db.setting(c, 'company', '')
    ver = c.execute(sa.select(vt.c.version, vt.c.published_at, vt.c.note).order_by(vt.c.version.desc()).limit(1)).mappings().first()
    srcs = live.collector_status(c)
    fails = [r for r in chk['rows'] if r['color'] in ('red', 'orange') or r['flags']]
    return {'title': _tr('数据接入联调验收报告'), 'plant': pl[0]['name'] if pl else '', 'company': company, 'date': clock_wall().strftime('%Y-%m-%d %H:%M'),
            'by': (user or {}).get('name') or (user or {}).get('username') or '', 'total': chk['total'], 'confirmed': chk['confirmed'],
            'coverage': chk['coverage'], 'counts': chk['counts'], 'devices': chk['devices'], 'sources': srcs,
            'failures': [{k: r[k] for k in ('code', 'device', 'meaning', 'color', 'flags_cn', 'value', 'address')} for r in fails][:400],
            'n_failures': len(fails), 'yellow': [{k: y[k] for k in ('datasource', 'address', 'sample', 'count')} for y in chk['yellow']][:200],
            'version': {'version': ver['version'], 'at': db.jsonable(ver['published_at']), 'note': ver['note']} if ver else None,
            'observed_s': chk['since']}


def clock_wall():
    return datetime.now()


def record(c, data, fmt, blob, user):
    from ..schema_factory import onboard_report_t as rt
    d = os.path.join(db.DATA, 'factory', 'reports')
    os.makedirs(d, exist_ok=True)
    rid = c.execute(rt.insert().values(title=data['title'], coverage=data['coverage'], summary={k: data[k] for k in ('total', 'confirmed', 'counts', 'n_failures')},
                                       file='', created_by=user, created_at=clock.now())).inserted_primary_key[0]
    fn = f'{rid}.{fmt}'
    with open(os.path.join(d, fn), 'wb') as f:
        f.write(blob)
    c.execute(rt.update().where(rt.c.id == rid).values(file=fn))
    from .. import audit
    audit.audit(c, 'onboard_report', 'onboard_report', rid, detail={'coverage': data['coverage'], 'fmt': fmt}, target=_tr('联调验收报告'))
    return rid


ROLE_CN = {'red': '读不到', 'orange': '超范围 / 异常', 'yellow': '点表外', 'green': '正常', 'grey': '已忽略'}


def sections(d) -> list:
    """报告结构：[(类型, 内容)]，Word 和 PDF 共用。类型 h1 / h2 / p / table / sign。"""
    pct = f"{d['coverage'] * 100:.1f}%"
    out = [('title', d['title']), ('p', _tr('项目：{plant}', plant=d['plant']) + (_tr('（{company}）', company=d['company']) if d['company'] else '')),
           ('p', _tr('生成时间：{date}；生成人：{by}；本次连续观测约 {round} 分钟。', date=d['date'], by=d['by'], round=round(d['observed_s'] / 60))),
           ('h1', _tr('一、结论')),
           ('p', _tr('（1）采集点共 {total} 个，已确认 {confirmed} 个，覆盖率 {pct}。', total=d['total'], confirmed=d['confirmed'], pct=pct)),
           ('p', _tr('（2）读不到 {get} 个，超范围或值异常 {get2} 个，点表外的地址 {get3} 个，已忽略 {get4} 个。', get=d['counts'].get('red', 0), get2=d['counts'].get('orange', 0), get3=d['counts'].get('yellow', 0), get4=d['counts'].get('grey', 0))),
           ('p', _tr('（3）') + (_tr('全部点位已确认，可以签字验收。') if d['coverage'] >= 0.999 and not d['counts'].get('red') else
                           _tr('仍有未确认或未通过的点位，处理完成后重新生成本报告再签字。'))),
           ('h1', _tr('二、数据源')),
           ('table', [[_tr('编码'), _tr('协议'), _tr('地址'), _tr('状态'), _tr('收到值')]] + [[s['code'], s['protocol'], s['endpoint'] or '', s['status'] or '', str(s['values'])]
                                                                 for s in d['sources']]),
           ('h1', _tr('三、各设备覆盖率')),
           ('table', [[_tr('设备'), _tr('名称'), _tr('点数'), _tr('已确认'), _tr('覆盖率'), _tr('读不到'), _tr('异常')]] +
            [[x['code'] or '—', x['name'], str(x['total']), str(x['confirmed']), f"{x['confirmed'] / x['total'] * 100:.0f}%" if x['total'] else '—', str(x['red']),
              str(x['orange'] + x['flags'])] for x in d['devices']]),
           ('h1', _tr('四、未通过的点位')),
           ]
    if d['failures']:
        out.append(('table', [[_tr('点位编码'), _tr('设备'), _tr('含义'), _tr('问题'), _tr('最后值')]] +
                    [[f['code'] or '', f['device'] or '', f['meaning'] or '', _tr('、').join([ROLE_CN.get(f['color'], '')] + f['flags_cn']).strip('、'),
                      '' if f['value'] is None else str(f['value'])[:20]] for f in d['failures']]))
        if d['n_failures'] > len(d['failures']):
            out.append(('p', _tr('（仅列前 {len} 个，共 {n_failures} 个。）', len=len(d['failures']), n_failures=d['n_failures'])))
    else:
        out.append(('p', _tr('无。')))
    out.append(('h1', _tr('五、点表外的地址（待确认）')))
    out.append(('table', [[_tr('数据源'), _tr('地址'), _tr('样本值'), _tr('次数')]] + [[y['datasource'] or '', y['address'], str(y['sample'] or '')[:24], str(y['count'] or 0)]
                                                             for y in d['yellow']]) if d['yellow'] else ('p', _tr('无。')))
    out.append(('h1', _tr('六、工厂模型版本')))
    v = d['version']
    out.append(('p', _tr('当前已发布版本 V{version}（{at}）：{v}', version=v['version'], at=v['at'], v=v['note'] or '') if v else _tr('尚未发布工厂模型版本。')))
    out.append(('h1', _tr('七、签字')))
    out.append(('sign', [[_tr('单位'), _tr('姓名'), _tr('签字'), _tr('日期')], [_tr('实施方（蜂元）'), '', '', ''], [_tr('系统集成商'), '', '', ''], [_tr('客户（设备 / 信息部门）'), '', '', '']]))
    return out


def docx(d) -> bytes:
    from docx import Document
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Pt, RGBColor
    doc = Document()

    def font(run, name=_tr('宋体'), size=10.5, bold=False):
        run.font.name = name
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = RGBColor(0, 0, 0)
        rpr = run._element.get_or_add_rPr()
        rf = rpr.find(qn('w:rFonts'))
        if rf is None:
            rf = rpr.makeelement(qn('w:rFonts'), {})
            rpr.append(rf)
        for k in ('w:eastAsia', 'w:ascii', 'w:hAnsi'):
            rf.set(qn(k), name)
    st = doc.styles['Normal']
    st.font.name = _tr('宋体')
    st.font.size = Pt(10.5)
    st.element.rPr.rFonts.set(qn('w:eastAsia'), _tr('宋体'))
    for kind, val in sections(d):
        if kind == 'title':
            p = doc.add_paragraph()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            font(p.add_run(val), _tr('黑体'), 18, True)
        elif kind == 'h1':
            p = doc.add_paragraph()
            p.paragraph_format.space_before = Pt(10)
            font(p.add_run(val), _tr('黑体'), 14, True)
        elif kind == 'p':
            p = doc.add_paragraph()
            font(p.add_run(val))
        elif kind in ('table', 'sign'):
            t = doc.add_table(rows=len(val), cols=len(val[0]))
            t.style = 'Table Grid'
            t.alignment = WD_TABLE_ALIGNMENT.CENTER
            for i, row in enumerate(val):
                for j, x in enumerate(row):
                    cell = t.cell(i, j)
                    cell.text = ''
                    font(cell.paragraphs[0].add_run(str(x)), _tr('黑体') if i == 0 else _tr('宋体'), 9, i == 0)
                    if kind == 'sign' and i > 0:
                        cell.paragraphs[0].paragraph_format.space_after = Pt(18)
            doc.add_paragraph()
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


FONT_CANDIDATES = [r'C:\Windows\Fonts\simsun.ttc', r'C:\Windows\Fonts\msyh.ttc', r'C:\Windows\Fonts\simhei.ttf',
                   '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc', '/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc',
                   '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc', '/usr/share/fonts/wqy-microhei/wqy-microhei.ttc']
BOLD_CANDIDATES = [r'C:\Windows\Fonts\simhei.ttf', r'C:\Windows\Fonts\msyhbd.ttc', '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc']


def _font(cands, size):
    from PIL import ImageFont
    for f in cands + FONT_CANDIDATES:
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    raise RuntimeError(_tr('服务器上没有中文字体（Windows 字体或 Noto CJK / 文泉驿），生成不了 PDF；请下载 Word 版'))


def pdf(d) -> bytes:
    """A4 竖版，150 dpi，按 sections 排版成页面图像再合成 PDF。"""
    from PIL import Image, ImageDraw
    W, H, M = 1240, 1754, 110
    f_t, f_h, f_p, f_c, f_cb = _font(BOLD_CANDIDATES, 38), _font(BOLD_CANDIDATES, 28), _font([], 21), _font([], 17), _font(BOLD_CANDIDATES, 17)
    pages = []
    page = draw = None
    y = 0

    def new_page():
        nonlocal page, draw, y
        page = Image.new('RGB', (W, H), 'white')
        draw = ImageDraw.Draw(page)
        pages.append(page)
        y = M
        draw.text((W - M, H - 60), _tr('第 {len} 页', len=len(pages)), fill='black', font=f_c, anchor='rs')

    def need(h):
        if page is None or y + h > H - M:
            new_page()

    def wrap(text, f, width):
        lines, cur = [], ''
        for ch in str(text):
            if draw.textlength(cur + ch, font=f) > width:
                lines.append(cur)
                cur = ch
            else:
                cur += ch
        lines.append(cur)
        return lines
    new_page()
    for kind, val in sections(d):
        if kind == 'title':
            need(80)
            draw.text((W / 2, y), val, fill='black', font=f_t, anchor='ma')
            y += 80
        elif kind == 'h1':
            need(70)
            y += 18
            draw.text((M, y), val, fill='black', font=f_h)
            y += 48
        elif kind == 'p':
            for ln in wrap(val, f_p, W - 2 * M):
                need(34)
                draw.text((M, y), ln, fill='black', font=f_p)
                y += 34
        else:
            ncol = len(val[0])
            avail = W - 2 * M
            wts = [max(1.0, max(min(len(str(r[j])), 30) for r in val)) for j in range(ncol)]
            cw = [avail * w / sum(wts) for w in wts]
            for i, row in enumerate(val):
                cells = [wrap(x, f_cb if i == 0 else f_c, cw[j] - 12) for j, x in enumerate(row)]
                rh = max(len(c) for c in cells) * 24 + 14 + (34 if kind == 'sign' and i > 0 else 0)
                need(rh)
                x = M
                for j, lines in enumerate(cells):
                    draw.rectangle([x, y, x + cw[j], y + rh], outline='black', width=1)
                    for k, ln in enumerate(lines):
                        draw.text((x + 6, y + 7 + k * 24), ln, fill='black', font=f_cb if i == 0 else f_c)
                    x += cw[j]
                y += rh
            y += 16
    buf = io.BytesIO()
    pages[0].save(buf, 'PDF', resolution=150.0, save_all=True, append_images=pages[1:])
    return buf.getvalue()
