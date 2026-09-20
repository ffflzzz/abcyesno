/* ==========================================================================
   src/lib/rich.ts —— 富文本 token → HTML（移植 `core/dom.js` 的 richHtml/parseRich）
   与线上语法一致：`@[名称](sd-asset://kind/id)` 会被解析成引用胶囊。
   ========================================================================== */

export interface RichRef { type: 'ref'; name: string; kind: string; id: string }
export interface RichText { type: 'text'; value: string }
export type RichToken = RichRef | RichText;

export function esc(s: unknown): string {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

export function richHtml(tokens: RichToken[] | undefined | null): string {
  if (!tokens || !tokens.length) return '';
  return tokens.map((t) => {
    if (t.type === 'ref') {
      return '<span class="ref-chip" data-ref-kind="' + esc(t.kind)
        + '" data-ref-id="' + esc(t.id) + '">@' + esc(t.name) + '</span>';
    }
    return esc(t.value).replace(/\n/g, '<br>');
  }).join('');
}

/** 把纯文本按 `@[名称](sd-asset://kind/id)` 解析成 token（与线上语法一致）。 */
const RE_TOKEN = /@\[([^\]]+)\]\(sd-asset:\/\/([a-zA-Z]+)\/(\d+)\)/g;

export function parseRich(text: string): RichToken[] {
  const out: RichToken[] = [];
  let last = 0;
  const s = text || '';
  let m: RegExpExecArray | null;
  RE_TOKEN.lastIndex = 0;
  while ((m = RE_TOKEN.exec(s)) !== null) {
    if (m.index > last) out.push({ type: 'text', value: s.slice(last, m.index) });
    out.push({ type: 'ref', name: m[1], kind: m[2], id: m[3] });
    last = m.index + m[0].length;
  }
  if (last < s.length) out.push({ type: 'text', value: s.slice(last) });
  return out;
}

/** 简易防抖 */
export function debounce<A extends unknown[]>(fn: (...a: A) => void, wait: number) {
  let t: number | null = null;
  return (...a: A) => {
    if (t) window.clearTimeout(t);
    t = window.setTimeout(() => fn(...a), wait);
  };
}
