// Lightweight Markdown rendering (for Ask Data answers): headings, lists, tables, bold, inline code; HTML is escaped first to prevent injection
const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]!));
const inline = (s: string) => esc(s).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/`([^`]+)`/g, '<code>$1</code>');

export function md(text: string): string {
  const lines = (text || '').replace(/\r/g, '').split('\n');
  const out: string[] = [];
  let i = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (/^\s*\|.*\|\s*$/.test(l) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      const cells = (x: string) => x.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim());
      const head = cells(l), aligns = cells(lines[i + 1]).map((c) => (c.endsWith(':') ? 'right' : 'left'));
      let html = '<table class="mdt"><tr>' + head.map((h, k) => `<th style="text-align:${aligns[k]}">${inline(h)}</th>`).join('') + '</tr>';
      i += 2;
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
        html += '<tr>' + cells(lines[i]).map((c, k) => `<td style="text-align:${aligns[k] || 'left'}">${inline(c)}</td>`).join('') + '</tr>';
        i++;
      }
      out.push(html + '</table>');
      continue;
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(l);
    if (h) { out.push(`<h${h[1].length + 2}>${inline(h[2])}</h${h[1].length + 2}>`); i++; continue; }
    if (/^\s*([-*]|\d+\.)\s+/.test(l)) {
      const ordered = /^\s*\d+\./.test(l);
      let html = ordered ? '<ol>' : '<ul>';
      while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) { html += '<li>' + inline(lines[i].replace(/^\s*([-*]|\d+\.)\s+/, '')) + '</li>'; i++; }
      out.push(html + (ordered ? '</ol>' : '</ul>'));
      continue;
    }
    out.push(l.trim() ? `<p>${inline(l)}</p>` : '');
    i++;
  }
  return out.join('');
}
