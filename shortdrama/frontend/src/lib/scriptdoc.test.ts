/* ==========================================================================
   src/lib/scriptdoc.test.ts —— 移植的**等价性**测试（旧实现当标准答案）
   --------------------------------------------------------------------------
   为什么不手写期望值：手写期望只能证明"我写的时候想的是什么"，证明不了
   **移植没走样**。这里把旧 `web/assets/js/core/scriptdoc.js` 直接加载起来当
   对照实现，同一份输入喂两边，逐字节比对 html / stats ——
   这是把 200 行解析逻辑搬语言时唯一靠得住的判据。

   语料用**真实产物**（`projects/<项目>/scriptwriter/scriptwriter_ep1.md`），不用手写样本：
   本项目实测过 —— 手写样本 39 项全绿之后，拿 24 份真实产物一跑又抓出 3 个 bug
   （模型用了手写样本里根本没出现过的写法）。手写样本只用来补**边界**。
   ========================================================================== */

import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { render as mine, parse, stats } from './scriptdoc';

/* ---------- 把旧实现加载进来当"标准答案" ---------- */

const origFile = path.resolve(__dirname, '../../../web/assets/js/core/scriptdoc.js');
const origSrc = fs.readFileSync(origFile, 'utf8');
const win: Record<string, unknown> = {};
new Function('window', origSrc)(win);
const orig = win.ScriptDoc as {
  render: (md: unknown) => { html: string; stats: Record<string, number>; blocks: unknown[] };
};

/* ---------- 语料：真实产物 ---------- */

const repoRoot = path.resolve(__dirname, '../../..');
const projectsDir = path.join(repoRoot, 'projects');

function realScripts(): { name: string; text: string }[] {
  const out: { name: string; text: string }[] = [];
  for (const pid of fs.readdirSync(projectsDir)) {
    const f = path.join(projectsDir, pid, 'scriptwriter', 'scriptwriter_ep1.md');
    if (fs.existsSync(f)) out.push({ name: pid, text: fs.readFileSync(f, 'utf8') });
  }
  return out;
}

/**
 * 边界样本 —— 只补真实语料里**可能没有**的形态。
 * 每一条都注明它防的是什么，不是随便凑数。
 */
const EDGE: { name: string; text: string }[] = [
  // `## 场次列表` 曾经被宽泛的"标题里含『场』"误判成场次行
  { name: '场次列表标题不许被当成场次行', text: '## 场次列表\n\n### 第三场 教堂 - 夜\n' },
  // `<画面>` 短标签：下一行是描述，不是台词
  { name: '短标签后的行是描述不是台词', text: '<画面>\n石砌拱顶压得很低。\n' },
  // 整行尖括号包裹 ⇒ 去掉括号、当描述
  { name: '整行包裹去掉尖括号', text: '<石砌拱顶压得很低，泛着冷光。>\n' },
  // 脚本注入不许被"包裹"规则吞掉首尾（内层含 <> 时必须不匹配）
  { name: '尖括号内层含尖括号时不吞首尾', text: '<script>alert(1)</script>\n' },
  // 说话人 + 台词（AI 形态：带语气括注）
  { name: '说话人带语气', text: '巴赫（严肃、不紧不慢）\n把谱子放下。\n' },
  // 粘贴形态：短行无标点 ⇒ 说话人，下一行是台词
  { name: '无标点短行当说话人', text: '老人\n灯还亮着。\n' },
  // 画面/动作：连续行并成一段
  { name: '连续画面行并段', text: '画面：门开了。\n画面：风灌进来。\n' },
  // 元信息 / 列表 / 分割线 / 围栏
  { name: '元信息与列表与围栏', text: '> 系列：测试\n> 时长：60s\n\n- 一\n- 二\n\n---\n\n```\n噪声\n```\n' },
  // 行内粗体与代码
  { name: '行内标记', text: '**场景：** 教堂\n这里 `code` 与 **bold**。\n' },
  // 空输入
  { name: '空输入', text: '' },
];

describe('scriptdoc 移植等价性（旧实现 = 标准答案）', () => {
  const real = realScripts();

  it('能找到真实产物（否则这条测试会变成空跑）', () => {
    expect(real.length).toBeGreaterThanOrEqual(10);
  });

  it('真实产物：html 与 stats 与旧实现**完全一致**', () => {
    const diffs: string[] = [];
    for (const f of real) {
      const a = mine(f.text);
      const b = orig.render(f.text);
      if (a.html !== b.html) diffs.push(f.name + '（html 不一致）');
      else if (JSON.stringify(a.stats) !== JSON.stringify(b.stats)) diffs.push(f.name + '（stats 不一致）');
    }
    expect(diffs).toEqual([]);
  });

  it('真实产物：正文非空就必须渲染出东西（结构上不可能渲染成空）', () => {
    const empty: string[] = [];
    for (const f of real) {
      if (f.text.trim() && !mine(f.text).html.trim()) empty.push(f.name);
    }
    expect(empty).toEqual([]);
  });

  it('真实产物：markdown 源码标记不泄漏到 html（`**` 与 `#` 标题）', () => {
    const leak: string[] = [];
    for (const f of real) {
      const html = mine(f.text).html;
      // ⚠️ 只查"源码标记泄漏"。**不要**用 `&gt;` 出现与否当判据 ——
      //    正文里的数学（5 > 8）本来就该保留 `&gt;`，我为此误报过一次。
      if (/\*\*/.test(html) || /^#{1,6}\s/m.test(html)) leak.push(f.name);
    }
    expect(leak).toEqual([]);
  });

  it('边界样本：与旧实现逐条一致', () => {
    const diffs: string[] = [];
    for (const c of EDGE) {
      const a = mine(c.text);
      const b = orig.render(c.text);
      if (a.html !== b.html) diffs.push(c.name + '\n  我: ' + a.html + '\n  旧: ' + b.html);
      else if (JSON.stringify(a.stats) !== JSON.stringify(b.stats)) diffs.push(c.name + '（stats）');
    }
    expect(diffs).toEqual([]);
  });

  it('具体判据（这两条是会"语义全错"的地方，值得显式钉住）', () => {
    // `<画面>` 之后那行必须是 action（描述），不能被当成台词
    const b = parse('<画面>\n石砌拱顶压得很低。\n');
    expect(b[0].t).toBe('tag');
    expect(b[1].t).toBe('action');

    // `## 场次列表` 是普通标题，不是场次行
    const c = parse('## 场次列表\n');
    expect(c[0].t).toBe('h');
    expect((c[0] as { role: string }).role).toBe('plain');
    expect(stats(c).scenes).toBe(0);
  });
});
