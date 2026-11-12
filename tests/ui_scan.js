// 界面扫描工具（从虚拟工厂 tests/ui_scan.js 移植，pages_ui.js / tour_ui.js 用）：在页面里装一个 window.__hmScan。
//   __hmScan.clipped(root) 文字被容器截断（没有省略号、也不是滚动区）的元素
//   __hmScan.hscroll()     主区 / 页面出现横向滚动
//   __vfScan.poppers()    看得见的弹出层（Element Plus 下拉/选择/日期/提示/菜单、ECharts 提示框、我们自己的右键菜单），
//                         每个给出：是不是被父容器（浮窗、面板）裁掉了一块、是不是被别的东西盖住、在不在视口里
//   __vfScan.overlaps()   看得见的文字互相压在一起的地方（同一块区域两段字都露在外面）
//   __vfScan.contrast()   按钮悬停 / 按下时字和底色太接近（用 CDP 强制 :hover / :active 后调用）
// 用法：await ctx.addInitScript({ content: require('./ui_scan').SCAN })
const SCAN = String.raw`
(() => {
  const CLIPS = /(auto|scroll|hidden|clip)/;
  function shown(el) {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) return null;
    let x0 = Math.max(0, r.left), y0 = Math.max(0, r.top), x1 = Math.min(innerWidth, r.right), y1 = Math.min(innerHeight, r.bottom);
    let vx0 = r.left, vy0 = r.top, vx1 = r.right, vy1 = r.bottom;          // 只算父容器裁剪（不算视口）
    for (let p = el.parentElement; p && p !== document.body && p !== document.documentElement; p = p.parentElement) {
      const cs = getComputedStyle(p);
      if (cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity === 0) return null;
      if (CLIPS.test(cs.overflowX + ' ' + cs.overflowY)) {
        const q = p.getBoundingClientRect();
        x0 = Math.max(x0, q.left); y0 = Math.max(y0, q.top); x1 = Math.min(x1, q.right); y1 = Math.min(y1, q.bottom);
        vx0 = Math.max(vx0, q.left); vy0 = Math.max(vy0, q.top); vx1 = Math.min(vx1, q.right); vy1 = Math.min(vy1, q.bottom);
      }
      if (cs.position === 'fixed') break;
    }
    return { full: r, x0, y0, x1, y1, area: Math.max(0, x1 - x0) * Math.max(0, y1 - y0), parentArea: Math.max(0, vx1 - vx0) * Math.max(0, vy1 - vy0) };
  }
  const visible = (e) => { if (!e || !e.getClientRects().length) return false; const cs = getComputedStyle(e); return cs.display !== 'none' && cs.visibility !== 'hidden' && +cs.opacity > 0.05; };
  const label = (e) => (e.id ? '#' + e.id : '') + '.' + String(e.className?.baseVal ?? e.className ?? '').trim().split(/\s+/).slice(0, 3).join('.') + ' "' + (e.innerText || '').trim().replace(/\s+/g, ' ').slice(0, 40) + '"';
  const where = (e) => e.closest('.hm-nav') ? 'nav' : e.closest('.hm-top') ? 'top' : e.closest('.dock') ? 'dock' : e.closest('.el-drawer') ? 'drawer' : e.closest('.el-dialog') ? 'dialog' : 'page';

  /** ECharts 提示框（没有类名：position absolute、z-index 9999999、pointer-events none 的 div）。 */
  function echartsTips() {
    return [...document.querySelectorAll('div[style*="z-index: 9999999"]')].filter((d) => visible(d) && d.innerText.trim());
  }
  function poppers() {
    const sel = '.el-popper, .el-select-dropdown, .el-picker__popper, .el-dropdown__popper, .el-cascader__dropdown, .el-color-dropdown, .xm, .ctx, .el-autocomplete-suggestion';
    const leaving = (e) => /-leave-(active|from|to)|-enter-(active|from)/.test(String(e.className));     // 正在淡入 / 淡出的不算
    const els = [...new Set([...document.querySelectorAll(sel)].map((e) => e.closest('.el-popper') || e))].filter((e) => visible(e) && !leaving(e));
    const out = [];
    for (const e of [...els, ...echartsTips()]) {
      const s = shown(e);
      if (!s) continue;
      const fa = s.full.width * s.full.height;
      const inBody = e.parentElement === document.body || !!e.closest('body > div:not(#app)');
      // 被父容器裁掉：父容器裁剪后剩下的面积 < 97%
      const clipped = s.parentArea < fa * 0.97;
      const outOfView = s.full.left < -1 || s.full.top < -1 || s.full.right > innerWidth + 1 || s.full.bottom > innerHeight + 1;
      // 被盖住：中心点最上面的东西不在弹出层里（ECharts 提示框 pointer-events: none，不查）
      let covered = false, by = '';
      if (!e.matches('div[style*="z-index: 9999999"]') && getComputedStyle(e).pointerEvents !== 'none') {
        const cx = (s.x0 + s.x1) / 2, cy = (s.y0 + s.y1) / 2;
        const top = document.elementFromPoint(cx, cy);
        covered = !!top && !e.contains(top) && !top.closest('#hm-tour-host') && !top.closest('.el-popper');   // 被另一个弹出层（刚悬停出来的提示）盖住不算
        if (covered) by = label(top);
      }
      out.push({ what: e.matches('div[style*="z-index: 9999999"]') ? 'echarts-tooltip' : label(e), where: where(e), inBody, clipped, outOfView, covered, by,
        frac: +(s.parentArea / fa).toFixed(3), rect: [Math.round(s.full.left), Math.round(s.full.top), Math.round(s.full.width), Math.round(s.full.height)] });
    }
    return out;
  }

  /** 文字压在一起：每个文字节点按行取矩形（去掉被父容器裁掉的部分），两两比较；相交面积大、而且交叉处两段字都没被别的东西盖住才算。 */
  const SKIP = '.scene-host, .labels, .vue-flow, .pv-svg, .av-svg, .lde, svg, canvas, .vt-root, #hm-tour-host, .hm-chart, .ai-menu, .ai-bar, .el-tooltip__trigger:empty, .el-table__fixed, .el-table__fixed-right, .el-table__header-wrapper .hidden-columns, .hidden-columns, .el-popper, .xm, [aria-hidden="true"], .el-loading-mask, .tl-track, .tl, .v3-legend, script, style, .gantt, .ec';
  function textBoxes(root) {
    const out = [];
    const w = document.createTreeWalker(root || document.body, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => (n.nodeValue.trim().length && n.parentElement && !n.parentElement.closest(SKIP) ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT),
    });
    const rng = document.createRange();
    let n;
    while ((n = w.nextNode())) {
      const el = n.parentElement;
      if (!visible(el)) continue;
      const cs = getComputedStyle(el);
      if (cs.color === 'transparent' || /rgba\(.*, 0\)$/.test(cs.color)) continue;
      const s = shown(el);
      if (!s || s.area < 4) continue;
      rng.selectNodeContents(n);
      for (const r of rng.getClientRects()) {
        const x0 = Math.max(r.left, s.x0), y0 = Math.max(r.top, s.y0), x1 = Math.min(r.right, s.x1), y1 = Math.min(r.bottom, s.y1);
        if (x1 - x0 < 2 || y1 - y0 < 3) continue;
        out.push({ n, el, x0, y0, x1, y1 });
      }
    }
    return out;
  }
  const bgOpaque = (e) => { const cs = getComputedStyle(e); if (cs.backgroundImage && cs.backgroundImage !== 'none') return true; const c = cs.backgroundColor; return !(c === 'transparent' || /rgba\(.*,\s*0\)$/.test(c)); };
  /** (x, y) 上这个元素的字是不是被上面别的东西挡住了。 */
  function hiddenAt(el, x, y) {
    const st = document.elementsFromPoint(x, y);
    const i = st.findIndex((e) => e === el || el.contains(e));
    if (i < 0) return true;                       // 不在命中栈里（pointer-events: none 或被完全挡住）：不算
    for (let j = 0; j < i; j++) if (!st[j].contains(el) && bgOpaque(st[j])) return true;
    return false;
  }
  function overlaps(root) {
    const bs = textBoxes(root);
    const G = 64, grid = new Map(), out = [], seen = new Set();
    bs.forEach((b, i) => {
      for (let gx = Math.floor(b.x0 / G); gx <= Math.floor(b.x1 / G); gx++) for (let gy = Math.floor(b.y0 / G); gy <= Math.floor(b.y1 / G); gy++) {
        const k = gx + ',' + gy; if (!grid.has(k)) grid.set(k, []); grid.get(k).push(i);
      }
    });
    for (const ids of grid.values()) for (let a = 0; a < ids.length; a++) for (let c = a + 1; c < ids.length; c++) {
      const A = bs[ids[a]], C = bs[ids[c]];
      if (A.n === C.n) continue;
      const key = ids[a] < ids[c] ? ids[a] + ':' + ids[c] : ids[c] + ':' + ids[a];
      if (seen.has(key)) continue; seen.add(key);
      const x0 = Math.max(A.x0, C.x0), y0 = Math.max(A.y0, C.y0), x1 = Math.min(A.x1, C.x1), y1 = Math.min(A.y1, C.y1);
      if (x1 - x0 < 3 || y1 - y0 < 4) continue;
      const ia = (x1 - x0) * (y1 - y0), sm = Math.min((A.x1 - A.x0) * (A.y1 - A.y0), (C.x1 - C.x0) * (C.y1 - C.y0));
      if (ia < sm * 0.25) continue;
      const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
      if (hiddenAt(A.el, cx, cy) || hiddenAt(C.el, cx, cy)) continue;
      out.push({ a: A.n.nodeValue.trim().slice(0, 40), b: C.n.nodeValue.trim().slice(0, 40), ea: label(A.el), eb: label(C.el), where: where(A.el),
        rect: [Math.round(x0), Math.round(y0), Math.round(x1 - x0), Math.round(y1 - y0)] });
    }
    // 同一对元素只报一次
    const uniq = new Map();
    for (const o of out) { const k = o.ea + '|' + o.eb; if (!uniq.has(k)) uniq.set(k, o); }
    return [...uniq.values()];
  }

  /** 颜色对比度（WCAG）。 */
  function rgb(c) { const m = String(c).match(/rgba?\(([^)]+)\)/); if (!m) return null; const v = m[1].split(',').map((x) => parseFloat(x)); return { r: v[0], g: v[1], b: v[2], a: v.length > 3 ? v[3] : 1 }; }
  function lum(c) { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c.r) + 0.7152 * f(c.g) + 0.0722 * f(c.b); }
  function mix(top, bot) { const a = top.a; return { r: top.r * a + bot.r * (1 - a), g: top.g * a + bot.g * (1 - a), b: top.b * a + bot.b * (1 - a), a: 1 }; }
  /** 元素实际的底色：从自己往上叠，直到不透明。 */
  function bgOf(e) {
    const layers = [];
    for (let p = e; p; p = p.parentElement) {
      if (p.classList?.contains('labels')) break;
      const cs = getComputedStyle(p);
      const c = rgb(cs.backgroundColor);
      // 渐变底：有不透明底色时用底色（画布网格、装饰性渐变），否则取第一个颜色（带它自己的透明度）
      const gi = cs.backgroundImage && /gradient/.test(cs.backgroundImage) && !(c && c.a >= 0.99) ? rgb(cs.backgroundImage) : null;
      if (gi) { layers.push(gi); if (gi.a >= 0.99) break; continue; }
      if (c && c.a > 0) { layers.push(c); if (c.a >= 0.99) break; }
    }
    let base = { r: 255, g: 255, b: 255, a: 1 };
    if (document.documentElement.classList.contains('dark')) base = { r: 21, g: 24, b: 28, a: 1 };
    if (e.closest('.labels')) base = { r: 223, g: 229, b: 234, a: 1 };      // 三维标签底下是场景（固定的浅灰背景 / 地面），不是页面底色
    for (let i = layers.length - 1; i >= 0; i--) base = mix(layers[i], base);
    return base;
  }
  function ratio(e) {
    const fg = rgb(getComputedStyle(e).color); if (!fg) return 99;
    const bg = bgOf(e), f = fg.a < 1 ? mix(fg, bg) : fg;
    const L1 = lum(f), L2 = lum(bg);
    return (Math.max(L1, L2) + 0.05) / (Math.min(L1, L2) + 0.05);
  }
  /** 按钮里真正显示字的元素（按钮自己或里面的 span），字的颜色和它实际底色的对比度。 */
  function contrastOf(btn) {
    const tx = [...btn.querySelectorAll('*')].filter((x) => [...x.childNodes].some((n) => n.nodeType === 3 && n.nodeValue.trim()) && visible(x));
    if ([...btn.childNodes].some((n) => n.nodeType === 3 && n.nodeValue.trim())) tx.unshift(btn);
    let worst = 99, who = null;
    for (const x of tx) { const r = ratio(x); if (r < worst) { worst = r; who = x; } }
    return { ratio: +worst.toFixed(2), text: (btn.innerText || '').trim().slice(0, 30), color: who ? getComputedStyle(who).color : '', bg: who ? JSON.stringify(bgOf(who)) : '' };
  }
  /** 三维设备标签（CSS2D .labels .lbl）：字和底色的对比度、带底色的标签互相压住。 */
  function labels() {
    const chips = [...document.querySelectorAll('.labels .lbl')].filter((e) => visible(e) && e.style.display !== 'none' && (e.innerText || '').trim());
    const low = [], rects = [];
    for (const e of chips) {
      const r = e.getBoundingClientRect();
      if (r.right < 0 || r.bottom < 0 || r.left > innerWidth || r.top > innerHeight) continue;
      for (const t of [e, ...e.querySelectorAll('b, small, em, span, div')]) {
        if (![...t.childNodes].some((n) => n.nodeType === 3 && n.nodeValue.trim())) continue;
        const k = ratio(t);
        if (k < 3) low.push({ text: t.textContent.trim().slice(0, 30), ratio: +k.toFixed(2), color: getComputedStyle(t).color, bg: JSON.stringify(bgOf(t)) });
      }
      if (bgOpaque(e)) rects.push({ e, r });
    }
    const over = [];
    for (let i = 0; i < rects.length; i++) for (let j = i + 1; j < rects.length; j++) {
      const A = rects[i].r, C = rects[j].r;
      const w = Math.min(A.right, C.right) - Math.max(A.left, C.left), h = Math.min(A.bottom, C.bottom) - Math.max(A.top, C.top);
      if (w > 2 && h > 2 && w * h > 0.3 * Math.min(A.width * A.height, C.width * C.height)) over.push([rects[i].e.innerText.trim().slice(0, 20), rects[j].e.innerText.trim().slice(0, 20)]);
    }
    return { n: chips.length, low, over };
  }
  /** 文字被截断：overflow 不可见、内容比盒子宽/高、又没有省略号（有省略号算有意的截断，鼠标悬停能看全） */
  function clipped(root) {
    const out = [];
    const els = (root || document.body).querySelectorAll('h1, h2, h3, h4, b, strong, button, label, dt, dd, th, .el-button, .hm-tag, .hm-kpi .v, .kl, .t, .sub, .cell, span, small, em, a');
    for (const e of els) {
      if (!visible(e) || e.closest(SKIP) || e.closest('.el-select, .el-input, .el-table__body-wrapper .cell, .el-scrollbar__wrap')) continue;
      const tx = [...e.childNodes].some((n) => n.nodeType === 3 && n.nodeValue.trim());
      if (!tx) continue;
      const cs = getComputedStyle(e);
      const hid = /(hidden|clip)/.test(cs.overflowX + cs.overflowY);
      if (!hid || cs.textOverflow === 'ellipsis' || /-webkit-box/.test(cs.display)) continue;
      if (e.scrollWidth > e.clientWidth + 2 || e.scrollHeight > e.clientHeight + 3) {
        // 父元素有省略号 / 是滚动区 → 不算
        const p = e.parentElement; const pcs = p ? getComputedStyle(p) : null;
        if (pcs && pcs.textOverflow === 'ellipsis') continue;
        out.push({ el: label(e), where: where(e), sw: e.scrollWidth, cw: e.clientWidth, sh: e.scrollHeight, ch: e.clientHeight });
      }
    }
    return out;
  }
  /** 横向滚动：页面本身、外壳主区 */
  function hscroll() {
    const m = document.querySelector('.hm-main');
    return { doc: document.documentElement.scrollWidth - innerWidth, main: m ? m.scrollWidth - m.clientWidth : 0 };
  }
  /** 主区里看得见的文字，对比度低于 min 的 */
  function lowContrast(root, min = 3) {
    const out = [], seen = new Set();
    for (const b of textBoxes(root)) {
      if (seen.has(b.el)) continue; seen.add(b.el);
      if (b.el.closest('.el-segmented, .is-transparent, .el-select__placeholder, .is-disabled, [disabled], .el-pagination .is-disabled')) continue;   // 分段控件的选中底不是祖先元素；占位符、禁用项按规范就是浅色
      const r = ratio(b.el);
      if (r < min) out.push({ text: (b.el.innerText || '').trim().slice(0, 30), ratio: +r.toFixed(2), color: getComputedStyle(b.el).color, el: label(b.el) });
    }
    return out;
  }
  window.__hmScan = { shown, poppers, overlaps, textBoxes, contrastOf, ratio, label, where, labels, clipped, hscroll, lowContrast };
})();
`;
module.exports = { SCAN };
