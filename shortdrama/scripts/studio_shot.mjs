/**
 * 三栏工作室的浏览器验收（无头截图 + 控制台/网络错误 + 画布桥握手）。
 *
 * 为什么必须真开浏览器：这一版的三个卖点（三栏形状、画布实时渲染、画布选中
 * → 检查器切镜）**没有一个是类型检查能证明的**。
 *
 * 跑法：后端在 8787（`python -m v5.server --port 8787 --web-root frontend/dist`），
 *      仓库根 `node shortdrama/scripts/studio_shot.mjs [项目名]`
 */
import { chromium } from 'playwright-core';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import fs from 'node:fs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.join(HERE, '..', '.tmp-studio-shot');
fs.mkdirSync(OUT, { recursive: true });

const PID = process.argv[2] || 'bumengshi-0922';
const URL = 'http://127.0.0.1:8787/studio/';

const browser = await chromium.launch({
  executablePath: process.env.PW_CHROMIUM || undefined,
  args: ['--no-sandbox'],
});
const page = await browser.newPage({ viewport: { width: 1600, height: 900 } });
/** ★ 8 秒动作上限。默认 30 秒 × 十几个探测性点击 = 脚本看起来"卡死"
 *  （实测第一版就在画布节点循环里挂过 5 分钟）。这里每一步都允许失败，
 *  失败由下面的 PASS/FAIL 行说清楚，不该靠超时兜。 */
page.setDefaultTimeout(8000);

const errors = [];
const badResponses = [];
page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)); });
page.on('pageerror', (e) => errors.push('PAGEERROR ' + String(e.message).slice(0, 200)));
page.on('response', (r) => { if (r.status() >= 400) badResponses.push(r.status() + ' ' + r.url()); });

console.log('打开', URL);
await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(6000);   // 轮询型页面没有 networkidle，只能定点等
await page.screenshot({ path: path.join(OUT, '01-boot.png') });

const railItems = await page.locator('.rail-item').count();
console.log('左栏项目数 =', railItems);

// 选一个指定项目
const target = page.locator('.rail-item', { hasText: PID }).first();
if (await target.count()) {
  await target.click();
  console.log('已点选', PID);
} else {
  console.log('!! 左栏里找不到', PID, '（用当前默认项目继续）');
}
await page.waitForTimeout(4000);
await page.screenshot({ path: path.join(OUT, '02-project.png') });

// 桥握手：画布 iframe 加载后应发 pixa:hello，宿主去掉「画布未连接」
const stageMeta = await page.locator('.stage-meta').first().innerText().catch(() => '');
console.log('中栏状态文字 =', JSON.stringify(stageMeta));
const disconnected = stageMeta.includes('画布未连接');
console.log(disconnected ? '!! 桥未握手（仍显示"画布未连接"）' : 'PASS 桥已握手');

const frames = page.frames();
console.log('iframe 数 =', frames.length, frames.map((f) => f.url()).join(' | '));

// ── 实时渲染的**直接证明**：宿主推一份多一格的节点集，画布应回报格数涨了 ──
// 比"点一下看看"强：这条测的就是"出一个资产就显示一个"这件事本身。
let liveWorks = false;
try {
  await page.evaluate(() => {
    window.__pixa = [];
    window.addEventListener('message', (ev) => {
      const d = ev.data;
      if (d && d.source === 'pixa-canvas') window.__pixa.push({ kind: d.kind, payload: d.payload });
    });
  });
  const before = await page.evaluate(() => {
    const last = (window.__pixa || []).filter((x) => x.kind === 'pixa:nodes').pop();
    return last ? last.payload.count : -1;
  });
  await page.evaluate(() => {
    const f = document.querySelector('.stage-canvas iframe');
    const nodes = [];
    for (let i = 0; i < 3; i += 1) {
      nodes.push({ id: 's:LN9' + i, type: 'image', title: '冒烟节点 ' + i,
        position: { x: 40 + i * 60, y: 40 }, width: 200, height: 300,
        metadata: { content: '', prompt: 'probe', status: 'success' } });
    }
    f.contentWindow.postMessage({ source: 'pixa-host', kind: 'pixa:merge',
      payload: { nodes, connections: [] } }, '*');
  });
  await page.waitForTimeout(2500);
  const after = await page.evaluate(() => {
    const last = (window.__pixa || []).filter((x) => x.kind === 'pixa:nodes').pop();
    return last ? last.payload.count : -1;
  });
  console.log(`实时渲染：握手后回报格数 ${before} → 推 3 格后 ${after}`);
  liveWorks = after > 0 && (before < 0 || after !== before);
  console.log(liveWorks ? 'PASS 宿主推送改变了画布节点集（桥双向通）' : '!! 推送后画布没回报变化');
} catch (e) { console.log('!! 实时渲染探测失败：', e.message); }

// 在画布里点一格静帧节点 → 检查器应切到那一镜
const canvasFrame = frames.find((f) => f.url().includes('/atelier/canvas'));
let selectWorks = false;
if (canvasFrame) {
  await canvasFrame.waitForLoadState('domcontentloaded').catch(() => {});
  await page.waitForTimeout(3000);
  // ★ 只认 `[data-node-id]`（`canvas-node.tsx:286` 打在节点容器上）。
  //   第一版用 `text=/^LN\d+ ·/` 找，结果 41 个命中**全是画布左侧那份节点清单**，
  //   点它们不会选中画布里的格子 —— 假阴性，白判成"功能坏了"。
  const nodes = canvasFrame.locator('[data-node-id]');
  const n = await nodes.count();
  console.log('画布内 [data-node-id] 节点 =', n);
  for (let i = 0; i < Math.min(n, 8); i += 1) {
    const el = nodes.nth(i);
    const box = await el.boundingBox().catch(() => null);
    if (!box || box.width < 20 || box.height < 20) continue;
    await el.click({ force: true, position: { x: 12, y: 12 } }).catch(() => {});
    await page.waitForTimeout(700);
    const chip = await page.locator('.inspector h4 .chip').first().innerText().catch(() => '');
    if (/^LN\d+$/.test(chip.trim())) {
      console.log('PASS 点画布节点 → 检查器切到', chip.trim());
      selectWorks = true;
      break;
    }
  }
  if (!selectWorks) console.log('!! 没从画布点出镜号（下面截图里人眼看）');
}
await page.screenshot({ path: path.join(OUT, '03-inspector.png') });

// 右栏发一条信箱（真发，验完由 smoke 的清理或手动删）
const ta = page.locator('.chat-input textarea').first();
if (await ta.count()) {
  await ta.fill('浏览器验收：这条来自 studio_shot.mjs');
  await page.locator('.chat-input button', { hasText: '发送' }).first().click();
  await page.waitForTimeout(6000);   // 轮询型页面没有 networkidle，只能定点等
  const msgs = await page.locator('.msg--me').count();
  console.log('右栏自己的消息条数 =', msgs, msgs ? 'PASS 发送回路通' : '!! 发送没生效');
  const last = await page.locator('.msg--me').last().innerText().catch(() => '');
  console.log('最后一条 =', JSON.stringify(last));
  await page.screenshot({ path: path.join(OUT, '04-chat.png') });
}

// 收尾：把这条验收消息从真项目上抹掉 —— 验收不该在别人的信箱里留话，
// 那会在下一次真跑链路时被注入给角色。
try {
  const inboxDir = path.join(HERE, '..', 'projects', PID, '.tmp', 'inbox');
  if (fs.existsSync(inboxDir)) {
    fs.rmSync(inboxDir, { recursive: true, force: true });
    console.log('已清掉', PID, '的验收信箱');
  }
} catch (e) { console.log('!! 清理信箱失败：', e.message); }

console.log('\n控制台 error 数 =', errors.length);
errors.slice(0, 12).forEach((e) => console.log('  ·', e));
console.log('HTTP>=400 数 =', badResponses.length);
[...new Set(badResponses)].slice(0, 12).forEach((e) => console.log('  ·', e));
console.log('\n截图目录：', OUT);

await browser.close();
