/* ==========================================================================
   src/lib/md.tsx —— 极小 markdown 渲染器（**只覆盖导演真的会写的那几种**）
   --------------------------------------------------------------------------
   为什么需要：右栏是 supervisor 的回答，他天然按 markdown 写（`## 标题`、
   **粗体**、`- 列表`、`| 表 |`）。第一版整段当纯文本塞进 `white-space: pre-wrap`
   ⇒ 人看到的是一堆星号和井号。

   ## 为什么不装 react-markdown

   这个前端目前只有 `react` + `react-dom` 两个依赖；为一条聊天消息拉进
   react-markdown + remark-gfm 是十几个包。而这里要渲染的**只有一个来源**
   （模型回答），语法面很窄 —— 子集够用。

   ## ⛔ 安全：绝不用 `dangerouslySetInnerHTML`

   全部走 React 元素构造 ⇒ 模型吐出来的 `<script>` 只会被**当成文字**显示，
   结构上就没有注入面（不是"记得转义"，是"没有那个接口"）。

   支持：`#`~`####` 标题 / `---` 分隔线 / ``` 代码块 / `-`·`*`·`1.` 列表 /
   `>` 引用 / `| a | b |` 表格 / 行内 `**粗**` `*斜*` `` `码` `` `[文字](url)`。
   不支持的（嵌套列表、脚注、HTML）**原样显示**，不猜。
   ========================================================================== */

import type { ReactNode } from 'react';

/* ── 行内 ─────────────────────────────────────────────────── */

const INLINE = /(\*\*[^*]+\*\*|\*[^*\n]+\*|`[^`]+`|\[[^\]]+\]\((?:https?:|\/)[^)\s]+\))/g;

function inline(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  INLINE.lastIndex = 0;
  let i = 0;
  while ((m = INLINE.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const t = m[1];
    const k = keyBase + ':' + i++;
    if (t.startsWith('**')) out.push(<b key={k}>{t.slice(2, -2)}</b>);
    else if (t.startsWith('`')) out.push(<code key={k}>{t.slice(1, -1)}</code>);
    else if (t.startsWith('[')) {
      const mm = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(t);
      if (mm) out.push(<a key={k} href={mm[2]} target="_blank" rel="noreferrer">{mm[1]}</a>);
      else out.push(t);
    } else out.push(<i key={k}>{t.slice(1, -1)}</i>);
    last = m.index + t.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/* ── 块级 ─────────────────────────────────────────────────── */

const isTableSep = (s: string) => /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(s) && s.includes('-');
const cells = (s: string) => s.replace(/^\s*\|/, '').replace(/\|\s*$/, '').split('|').map((c) => c.trim());

export function Markdown({ text }: { text: string }) {
  const lines = String(text ?? '').split('\n');
  const blocks: ReactNode[] = [];
  let i = 0;

  const push = (n: ReactNode) => blocks.push(n);

  while (i < lines.length) {
    const line = lines[i];

    // 空行
    if (!line.trim()) { i += 1; continue; }

    // ``` 代码块
    if (/^\s*```/.test(line)) {
      const buf: string[] = [];
      i += 1;
      while (i < lines.length && !/^\s*```/.test(lines[i])) buf.push(lines[i++]);
      i += 1;
      push(<pre key={'c' + i}><code>{buf.join('\n')}</code></pre>);
      continue;
    }

    // 标题
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) {
      const lv = h[1].length;
      const body = inline(h[2], 'h' + i);
      push(lv <= 2
        ? <h4 key={'h' + i}>{body}</h4>
        : <h5 key={'h' + i}>{body}</h5>);
      i += 1;
      continue;
    }

    // 分隔线
    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) { push(<hr key={'r' + i} />); i += 1; continue; }

    // 表格：本行含 |，下一行是分隔行
    if (line.includes('|') && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const head = cells(line);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) rows.push(cells(lines[i++]));
      push(
        <table key={'t' + i}>
          <thead><tr>{head.map((c, x) => <th key={x}>{inline(c, 'th' + x)}</th>)}</tr></thead>
          <tbody>
            {rows.map((r, y) => <tr key={y}>{r.map((c, x) => <td key={x}>{inline(c, 'td' + y + x)}</td>)}</tr>)}
          </tbody>
        </table>,
      );
      continue;
    }

    // 引用
    if (/^\s*>\s?/.test(line)) {
      const buf: string[] = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/, ''));
      push(<blockquote key={'q' + i}>{inline(buf.join(' '), 'q')}</blockquote>);
      continue;
    }

    // 列表（- * + 或 1.）；只认单层，嵌套原样缩进显示
    if (/^\s*([-*+]|\d+[.)])\s+/.test(line)) {
      const ordered = /^\s*\d+[.)]\s+/.test(line);
      const items: string[] = [];
      while (i < lines.length && /^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) {
        items.push(lines[i++].replace(/^\s*([-*+]|\d+[.)])\s+/, ''));
      }
      const lis = items.map((t, x) => <li key={x}>{inline(t, 'li' + x)}</li>);
      push(ordered ? <ol key={'o' + i}>{lis}</ol> : <ul key={'u' + i}>{lis}</ul>);
      continue;
    }

    // 段落：连续非空行合并
    const buf: string[] = [];
    while (i < lines.length && lines[i].trim()
      && !/^(#{1,4})\s/.test(lines[i]) && !/^\s*```/.test(lines[i])
      && !/^\s*([-*+]|\d+[.)])\s+/.test(lines[i]) && !/^\s*>\s?/.test(lines[i])
      && !(lines[i].includes('|') && i + 1 < lines.length && isTableSep(lines[i + 1]))) {
      buf.push(lines[i++]);
    }
    push(<p key={'p' + i}>{inline(buf.join(' '), 'p')}</p>);
  }

  return <div className="md">{blocks}</div>;
}
