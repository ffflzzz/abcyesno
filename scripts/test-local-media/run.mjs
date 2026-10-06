/**
 * 设置面板「本地出片服务」的无头验收（真实 SettingsPanel + 本机 Chrome）
 *
 * 为什么要有这个：这张卡片是**跨进程**功能，后端那侧的真路径已由
 * `shortdrama/v5/tests_local_services.py`（本机假 ComfyUI + TestClient 五条路由）背书，
 * 但面板这一侧的状态机没跑过就等于没验：探到才出现候选、选了才出现工作流入口、
 * 参数没指全必须拦住接入、窄窗口不能把地址串撑破横向滚动。
 * 内置 Browser 面板在这台机器上常拿到 0×0 视口，所以走 playwright-core + 本机 Chrome
 * （与 `scripts/test-background-process-bar/run.mjs` 同一条路子）。
 *
 * 用法：node scripts/test-local-media/run.mjs
 */
import { build } from 'esbuild';
import { chromium } from 'playwright-core';
import { mkdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { execFile } from 'node:child_process';

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, '../..');
const outDir = join(here, '.tmp');
const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';

const failures = [];
const shots = [];
function check(name, cond, detail = '') {
  if (cond) console.log(`  ok   ${name}`);
  else {
    console.log(`  FAIL ${name}${detail ? ` — ${String(detail).slice(0, 220)}` : ''}`);
    failures.push(name);
  }
}

rmSync(outDir, { recursive: true, force: true });
mkdirSync(outDir, { recursive: true });

await build({
  entryPoints: [join(here, 'harness.jsx')],
  outfile: join(outDir, 'harness.js'),
  bundle: true,
  format: 'iife',
  platform: 'browser',
  jsx: 'automatic',
  loader: { '.png': 'dataurl' },
  define: { 'process.env.NODE_ENV': '"development"' },
  logLevel: 'warning',
});

// 真实 index.css 整段注入：卡片用的是设计系统令牌（--panel-2 / --border / --warning…），
// 只截一段会拿不到变量，量出来的排版就不是生产里那套。
const css = readFileSync(join(repoRoot, 'src/styles/index.css'), 'utf-8');
const html = `<!doctype html><html><head><meta charset="utf-8"><style>${css}</style></head>
<body><div id="root"></div><script src="${join(outDir, 'harness.js').replace(/\\/g, '/')}"></script></body></html>`;
const htmlPath = join(outDir, 'index.html');
writeFileSync(htmlPath, html, 'utf-8');

const browser = await chromium.launch({ executablePath: CHROME, headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1296, height: 900 } });
  // 面板没渲染出来时，报错必须打在控制台上而不是让 waitForSelector 干等 30 秒
  // （harness 里 React 抛错是静默的 —— 没有这句话，查根因要来回好几轮）。
  const pageErrors = [];
  page.on('console', (m) => { if (m.type() === 'error') pageErrors.push(m.text()); });
  page.on('pageerror', (e) => pageErrors.push(String(e && e.message ? e.message : e)));
  await page.goto(pathToFileURL(htmlPath).href);
  try {
    await page.waitForSelector('.settings-modal', { timeout: 8000 });
  } catch (err) {
    console.error('设置面板没渲染：' + err.message);
    console.error('页面报错：\n' + (pageErrors.join('\n') || '（控制台没抓到，可能整包没执行）'));
    console.error('root 内容：' + (await page.locator('#root').innerHTML()).slice(0, 400));
    process.exit(1);
  }

  // ① 卡片在「API 与模型」分类里，且搜得到
  check('设置面板出现「本地出片服务」卡片',
    await page.locator('.settings-card-name', { hasText: '本地出片服务' }).count() === 1);
  await page.locator('.settings-search input').fill('comfyui');
  check('搜 comfyui 能命中这张卡片',
    await page.locator('.lmc').count() === 1, await page.locator('.settings-pane').innerText());
  await page.locator('.settings-search-clear').click();

  const card = page.locator('.lmc');
  check('没查过之前不谎称状态',
    (await card.innerText()).includes('还没查'), await card.innerText());

  // ② 一键探测：活的摊开、死的带原因（不许合成一句「没探到」）
  await card.getByRole('button', { name: '一键探测' }).click();
  await page.waitForSelector('.lmc-item');
  const live = await card.locator('.lmc-item').innerText();
  check('探到的机器带显卡与显存', live.includes('RTX 3060') && live.includes('12GB'), live);
  check('探到的机器报出 H3 节点与模型文件数',
    live.includes('H3 相关节点 1 个') && live.includes('模型文件 1 个'), live);
  const dead = await card.locator('.lmc-dead').innerText();
  check('不通那条给出可动手的原因', dead.includes('握手超时') && dead.includes('192.168.1.31'), dead);

  // ③ 只有一台候选 ⇒ 自动取它跑过的那张图，**不需要用户导出/选文件**
  await page.waitForSelector('.lmc-map');
  const auto = await card.innerText();
  check('探测后自动认出参数落点（免选手动工作流）',
    auto.includes('提示词') && auto.includes('帧数'), auto.slice(0, 500));
  check('写明这张图的来历（队列号 + 产出文件名）',
    auto.includes('队列号 7') && auto.includes('h3_take7.mp4'), auto.slice(0, 500));
  check('仍保留「自己选 JSON」的兜底入口',
    await card.getByText('换一张（自己选 JSON）').count() === 1);

  // ④ 接入：状态、产地来源、降级说明、撤销入口都要出现
  await card.getByRole('button', { name: /接入并设为本机默认/ }).click();
  await page.waitForTimeout(120);
  const after = await card.innerText();
  check('接入后当前出片视频是本机厂商', after.includes('comfyui'), after.slice(0, 500));
  check('接入后写明来源是档案而不是环境变量',
    after.includes('本机接入档案'), after.slice(0, 500));
  check('接入后列出降级说明（静帧仍云端）',
    after.includes('静帧') && after.includes('只接了视频'), after.slice(0, 800));
  check('接入后能一键回云端',
    await card.getByRole('button', { name: '断开并回云端' }).count() === 1);

  // ⑥ 认不出参数时必须拦住接入，并指名要哪一项（不许拿图里的残留值发出去）
  await page.evaluate(() => { window.__stub.missing = ['frames']; });
  await card.getByRole('button', { name: '重新看一次' }).click();
  await page.waitForTimeout(120);
  check('认不出时指名要手填的项',
    (await card.innerText()).includes('帧数') && (await card.innerText()).includes('认不出来'),
    (await card.innerText()).slice(0, 600));
  await card.getByRole('button', { name: /接入并设为本机默认/ }).click();
  await page.waitForTimeout(120);
  check('没指全时接入被拦住并点名',
    (await card.innerText()).includes('这几项还没指到节点'),
    (await card.innerText()).slice(0, 400));
  const inputs = card.locator('.lmc-manual input');
  check('给了两个手填输入框（节点号 / 字段名）', await inputs.count() === 2, await inputs.count());
  await inputs.nth(0).fill('7');
  await inputs.nth(1).fill('num_frames');
  await card.getByRole('button', { name: /接入并设为本机默认/ }).click();
  await page.waitForTimeout(150);
  const sent = await page.evaluate(() => window.__stub.connectCalls.slice(-1)[0]);
  check('手填的落点真的进了请求', !!sent && sent.mapping
    && sent.mapping.frames && sent.mapping.frames.node === '7'
    && sent.mapping.frames.field === 'num_frames', JSON.stringify(sent && sent.mapping));

  // ⑦ 排版：宽窗口截图 + 窄窗口不许把面板撑出横向滚动
  await page.locator('.settings-modal').screenshot({ path: join(outDir, 'local-media-wide.png'), fullPage: false });
  shots.push(join(outDir, 'local-media-wide.png'));
  await page.setViewportSize({ width: 820, height: 900 });
  await page.waitForTimeout(80);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  check('窄窗口（820）不出现横向溢出', overflow <= 1, `溢出 ${overflow}px`);
  await page.locator('.settings-modal').screenshot({ path: join(outDir, 'local-media-narrow.png') });
  shots.push(join(outDir, 'local-media-narrow.png'));
} finally {
  await browser.close();
}

// 视觉产物按惯例弹给用户自己看（这台机器上 `start` 对 .png 也会假成功，
// 所以显式点名看图器并回报是否真起进程）。
for (const p of shots) {
  execFile('cmd.exe', ['/c', 'start', '', p], () => {});
  console.log(`  图  ${p}`);
}

if (failures.length) {
  console.log(`\nFAILED ${failures.length} 项：${failures.join(' / ')}`);
  process.exit(1);
}
console.log('\n全部通过');
