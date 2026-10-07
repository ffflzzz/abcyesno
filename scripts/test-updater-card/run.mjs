/**
 * 更新提醒卡（左下角那张）的无头浏览器验收：真组件 + 真 index.css + 本机 Chrome。
 *
 * 为什么必须真开浏览器：这次改的是**一张卡随状态换内容**——发现新版 → 点了才下载 →
 * 进度 → 已就绪 → 失败可重试。这些没有一个是从类型检查或代码读得出来的；
 * 而且卡是 380×236 的固定小窗，文案一长就会顶掉「立即安装」那个按钮，只能量。
 * 内置 Browser 面板在这台机器上常拿到 0×0 视口，所以走 playwright-core + 本机 Chrome
 * （与 `scripts/test-local-media/run.mjs` 同一条路子）。
 *
 * 用法：node scripts/test-updater-card/run.mjs
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

// 与 electron/updater.js 里 POPUP_W / POPUP_H 一致（那边改了这边要跟着改，
// 否则量出来的「没溢出」是假的）。
const CARD_W = 380;
const CARD_H = 236;

const failures = [];
const shots = [];
let gridPath = '';
function check(name, cond, detail = '') {
  if (cond) console.log(`  ok   ${name}`);
  else {
    console.log(`  FAIL ${name}${detail ? ` — ${String(detail).slice(0, 240)}` : ''}`);
    failures.push(name);
  }
}

rmSync(outDir, { recursive: true, force: true });
mkdirSync(outDir, { recursive: true });

await build({
  entryPoints: [join(here, 'harness.jsx'), join(here, 'settings-harness.jsx')],
  outdir: outDir,
  bundle: true,
  format: 'iife',
  platform: 'browser',
  jsx: 'automatic',
  loader: { '.png': 'dataurl' },
  define: { 'process.env.NODE_ENV': '"development"' },
  logLevel: 'warning',
});

// 真 index.css 整段注入：卡片用的是设计系统令牌，只截一段量出来的排版不是生产那套。
const css = readFileSync(join(repoRoot, 'src/styles/index.css'), 'utf-8');
const page = (script) => `<!doctype html><html><head><meta charset="utf-8"><style>${css}</style></head>
<body><div id="root"></div><script src="${script}"></script></body></html>`;
writeFileSync(join(outDir, 'index.html'), page('harness.js'), 'utf-8');
writeFileSync(join(outDir, 'settings.html'), page('settings-harness.js'), 'utf-8');

const browser = await chromium.launch({ executablePath: CHROME, headless: true });
const pageErrors = [];
try {
  const page = await browser.newPage({ viewport: { width: CARD_W, height: CARD_H } });
  page.on('console', (m) => { if (m.type() === 'error') pageErrors.push(m.text()); });
  page.on('pageerror', (e) => pageErrors.push(String(e && e.message ? e.message : e)));

  const card = page.locator('.up-card');
  const bodyText = () => page.locator('.up-body').innerText();
  const primaryText = () => page.locator('.up-install').innerText();
  async function overflow() {
    return page.evaluate(() => document.documentElement.scrollHeight - window.innerHeight);
  }
  async function shot(name) {
    const p = join(outDir, `${name}.png`);
    await page.screenshot({ path: p });
    shots.push(p);
  }

  // ── ① 首帧（URL 参数）：发现新版，等你点下载 ──────────────────────────────
  const boot = `${pathToFileURL(join(outDir, 'index.html')).href}?from=1.5.8&to=1.5.9&status=available`;
  await page.goto(boot);
  try {
    await page.waitForSelector('.up-card', { timeout: 8000 });
  } catch (err) {
    console.error('卡片没渲染：' + err.message);
    console.error('页面报错：\n' + (pageErrors.join('\n') || '（控制台没抓到，可能整包没执行）'));
    console.error('root 内容：' + (await page.locator('#root').innerHTML()).slice(0, 400));
    process.exit(1);
  }
  check('① 标题是「发现新版本」', (await page.locator('.up-title').innerText()) === '发现新版本',
    await page.locator('.up-title').innerText());
  check('① 两个版本号都在卡上', (await bodyText()).includes('1.5.8') && (await bodyText()).includes('1.5.9'),
    await bodyText());
  check('① 主按钮是「下载并更新」', await primaryText() === '下载并更新', await primaryText());
  check('① 写明下完还要再点一下', (await bodyText()).includes('再点一下'), await bodyText());
  check('① 这个状态没有进度条', await page.locator('.up-progress').count() === 0);
  check('① 380×236 里不溢出（按钮没被顶掉）', await overflow() <= 1, `溢出 ${await overflow()}px`);
  await shot('1-available');

  // ── ② 点「下载并更新」→ 状态由主进程推进来，卡原地变进度 ─────────────────
  await page.locator('.up-install').click();
  const acts1 = await page.evaluate(() => window.__ctl.actions.slice());
  check('② 点击发的是 download', acts1.includes('download'), JSON.stringify(acts1));
  await page.evaluate(() => window.__ctl.set({
    status: 'downloading',
    info: { version: '1.5.9' },
    progress: { percent: 43, transferred: 52400000, total: 122000000, bytesPerSecond: 3100000 },
  }));
  check('② 标题变「正在下载」', (await page.locator('.up-title').innerText()) === '正在下载',
    await page.locator('.up-title').innerText());
  check('② 下载中版本号还在卡上（不许变成 v?）',
    (await bodyText()).includes('1.5.9') && !(await bodyText()).includes('?'), await bodyText());
  check('② 出现进度条且宽度按百分比',
    await page.locator('.up-progress-fill').getAttribute('style') === 'width: 43%;',
    await page.locator('.up-progress-fill').getAttribute('style'));
  check('② 主按钮显示进度并禁用（点不动第二下）',
    await primaryText() === '正在下载… 43%' && await page.locator('.up-install').isDisabled(),
    `${await primaryText()} disabled=${await page.locator('.up-install').isDisabled()}`);
  check('② 写了已下/总量多少 MB', (await bodyText()).includes('50.0 MB / 116.3 MB'), await bodyText());
  check('② 下载中不溢出', await overflow() <= 1, `溢出 ${await overflow()}px`);
  await shot('2-downloading');

  // ── ③ 下完：卡原地变「已就绪」，仍要人点 ─────────────────────────────────
  await page.evaluate(() => window.__ctl.set({ status: 'downloaded', info: { version: '1.5.9' }, progress: null }));
  check('③ 标题变「下载完成」', (await page.locator('.up-title').innerText()) === '下载完成',
    await page.locator('.up-title').innerText());
  check('③ 主按钮变「立即安装并重启」', await primaryText() === '立即安装并重启', await primaryText());
  check('③ 进度条收掉了', await page.locator('.up-progress').count() === 0);
  check('③ 写明正在跑的任务会中断', (await bodyText()).includes('会中断'), await bodyText());
  check('③ 已就绪不溢出', await overflow() <= 1, `溢出 ${await overflow()}px`);
  await shot('3-downloaded');

  await page.locator('.up-install').click();
  const acts2 = await page.evaluate(() => window.__ctl.actions.slice());
  check('③ 点击发的是 install', acts2.includes('install'), JSON.stringify(acts2));
  check('③ 点完按钮变成「正在安装…」',
    (await primaryText()).includes('正在安装'), await primaryText());

  // ── ④ 失败态：给原因 + 重试出口 ──────────────────────────────────────────
  await page.goto(`${pathToFileURL(join(outDir, 'index.html')).href}?from=1.5.8&to=1.5.9&status=error`);
  await page.waitForSelector('.up-card');
  await page.evaluate(() => window.__ctl.set({
    status: 'error', info: { version: '1.5.9' }, progress: null,
    error: 'Cannot download update file: ECONNRESET 连接被重置',
  }));
  check('④ 标题变「更新失败」', (await page.locator('.up-title').innerText()) === '更新失败',
    await page.locator('.up-title').innerText());
  check('④ 卡上带出错误原因', (await bodyText()).includes('ECONNRESET'), await bodyText());
  check('④ 主按钮变「重试」', await primaryText() === '重试', await primaryText());
  await page.locator('.up-install').click();
  const acts3 = await page.evaluate(() => window.__ctl.actions.slice());
  check('④ 点击发的是 retry', acts3.includes('retry'), JSON.stringify(acts3));
  check('④ 长错误串也没把按钮顶出窗口', await overflow() <= 1, `溢出 ${await overflow()}px`);
  await shot('4-error');

  // ── ⑤ 键盘：回车=当前该做的事，Esc=稍后 ──────────────────────────────────
  await page.goto(`${pathToFileURL(join(outDir, 'index.html')).href}?from=1.5.8&to=1.5.9&status=available`);
  await page.waitForSelector('.up-card');
  await page.evaluate(() => { window.__ctl.actions.length = 0; });
  await page.keyboard.press('Enter');
  check('⑤ 发现新版时回车=下载', (await page.evaluate(() => window.__ctl.actions.slice())).includes('download'),
    await page.evaluate(() => JSON.stringify(window.__ctl.actions)));
  await page.keyboard.press('Escape');
  check('⑤ 回车后 Esc=稍后', (await page.evaluate(() => window.__ctl.actions.slice())).includes('later'),
    await page.evaluate(() => JSON.stringify(window.__ctl.actions)));

  await page.goto(`${pathToFileURL(join(outDir, 'index.html')).href}?from=1.5.8&to=1.5.9&status=downloaded`);
  await page.waitForSelector('.up-card');
  await page.evaluate(() => { window.__ctl.actions.length = 0; });
  await page.keyboard.press('Enter');
  check('⑤ 已就绪时回车=安装', (await page.evaluate(() => window.__ctl.actions.slice())).includes('install'),
    await page.evaluate(() => JSON.stringify(window.__ctl.actions)));

  // ── ⑦ 首帧看 URL 参数，之后只听 known 状态的推送 ─────────────────────────
  await page.goto(`${pathToFileURL(join(outDir, 'index.html')).href}?from=1.5.8&to=1.5.9&status=downloaded`);
  await page.waitForSelector('.up-card');
  check('⑦ 只靠 URL 首帧也能显示「下载完成」',
    (await page.locator('.up-title').innerText()) === '下载完成',
    await page.locator('.up-title').innerText());
  await page.evaluate(() => window.__ctl.set({ status: 'uptodate', info: null, progress: null }));
  check('⑦ 「已是最新」这类推送不把已就绪的卡改空',
    (await page.locator('.up-title').innerText()) === '下载完成',
    await page.locator('.up-title').innerText());
  await page.evaluate(() => window.__ctl.set({ status: 'available', info: { version: '1.6.0' } }));
  check('⑦ 推送能把卡换成新版本号',
    (await page.locator('.up-title').innerText()) === '发现新版本'
    && (await bodyText()).includes('1.6.0'), await bodyText());

  // ── ⑧ 设置面板那一行：文案与按钮跟着新顺序走 ──────────────────────────────
  const spage = await browser.newPage({ viewport: { width: 1296, height: 900 } });
  spage.on('pageerror', (e) => pageErrors.push('SETTINGS ' + String(e && e.message ? e.message : e)));
  await spage.goto(pathToFileURL(join(outDir, 'settings.html')).href);
  await spage.waitForSelector('.settings-modal');
  await spage.locator('.settings-search input').fill('更新');
  const row = spage.locator('.settings-card', { hasText: '关于 Abcyesno' });
  const rowDesc = () => row.locator('.settings-card-desc').innerText();
  const rowBtn = () => row.locator('.settings-card-control button');
  check('⑧ 搜「更新」找得到这一行', await row.count() === 1, await row.count());
  check('⑧ 没查过之前不谎称状态', (await rowDesc()).includes('便携桌面'), await rowDesc());
  check('⑧ 默认按钮是「检查更新」', await rowBtn().innerText() === '检查更新', await rowBtn().innerText());

  await spage.evaluate(() => window.__ctl.set({ status: 'available', info: { version: '1.5.9' } }));
  check('⑧ 查到新版：文案说等你点下载',
    (await rowDesc()).includes('发现 v1.5.9') && (await rowDesc()).includes('等你点'), await rowDesc());
  check('⑧ 查到新版：按钮变「下载新版本」（卡被关掉时的出口）',
    await rowBtn().innerText() === '下载新版本', await rowBtn().innerText());
  await rowBtn().click();
  check('⑧ 点它真的发起下载',
    (await spage.evaluate(() => window.__ctl.calls.slice())).includes('download'),
    await spage.evaluate(() => JSON.stringify(window.__ctl.calls)));

  await spage.evaluate(() => window.__ctl.set({
    status: 'downloading', progress: { percent: 43, transferred: 52400000, total: 122000000 },
  }));
  check('⑧ 下载中这一行出现进度条', await row.locator('.settings-progress').count() === 1);
  check('⑧ 下载中按钮禁用，不让人再点一次检查', await rowBtn().isDisabled());
  check('⑧ 下载中不出现「重启更新」', await rowBtn().innerText() !== '重启更新', await rowBtn().innerText());

  await spage.evaluate(() => window.__ctl.set({ status: 'downloaded', progress: null }));
  check('⑧ 下完按钮才变「重启更新」', await rowBtn().innerText() === '重启更新', await rowBtn().innerText());
  await rowBtn().click();
  check('⑧ 点「重启更新」发的是 install',
    (await spage.evaluate(() => window.__ctl.calls.slice())).includes('install'),
    await spage.evaluate(() => JSON.stringify(window.__ctl.calls)));

  // 绿色解压版 / dev：supported=false ⇒ 不许出现应用内下载按钮，点了只打开发布页
  await spage.evaluate(() => window.__ctl.set({
    supported: false, status: 'idle', info: null, progress: null,
  }));
  check('⑧ 绿色版这一行不提下载，只报版本',
    (await rowDesc()).includes('便携桌面') && !(await rowDesc()).includes('下载'), await rowDesc());
  await rowBtn().click();
  check('⑧ 绿色版点「检查更新」打开的是发布页',
    (await spage.evaluate(() => window.__ctl.calls.slice())).some((c) => String(c).startsWith('open:')),
    await spage.evaluate(() => JSON.stringify(window.__ctl.calls.slice(-2))));
  await spage.close();

  // ── ⑨ 四张状态拼一张图给人眼看 ───────────────────────────────────────────
  const grid = `<!doctype html><html><head><meta charset="utf-8"><style>
  body{margin:0;padding:18px;background:#0d1117;font-family:sans-serif;color:#c9d1d9}
  h1{font-size:14px;margin:0 0 14px}figure{margin:0 0 18px}
  img{border:1px solid #30363d;display:block}
  figcaption{font-size:12px;margin-top:6px;color:#8b949e}</style></head><body>
  <h1>更新提醒卡（左下角 · ${CARD_W}×${CARD_H}）</h1>
  ${shots.map((p, i) => `<figure><img src="${p.replace(/^.*[\\/]/, '')}"><figcaption>${['① 发现新版本，等你点下载（没下载前不自己下）', '② 点了才下载：进度条 + 按钮禁用', '③ 已就绪：仍要人点「立即安装并重启」', '④ 失败：带原因 + 重试'][i] || p}</figcaption></figure>`).join('\n')}
  </body></html>`;
  writeFileSync(join(outDir, 'grid.html'), grid, 'utf-8');
  const gpage = await browser.newPage({ viewport: { width: CARD_W + 60, height: 1200 } });
  await gpage.goto(pathToFileURL(join(outDir, 'grid.html')).href);
  gridPath = join(outDir, 'update-card-4-states.png');
  await gpage.screenshot({ path: gridPath, fullPage: true });
  await gpage.close();
  console.log(`\n拼图 ${gridPath}`);
} finally {
  await browser.close();
}

// 视觉产物用 Chrome 打开（这台机器上 `start` 对 .png 也会假成功，所以点名看图器）。
execFile(CHROME, ['--app=file:///' + gridPath.replace(/\\/g, '/')], () => {});
console.log(`  图  ${gridPath}`);

if (failures.length) {
  console.log(`\nFAILED ${failures.length} 项：${failures.join(' / ')}`);
  process.exit(1);
}
console.log('\n卡片全部通过');
