/**
 * BackgroundProcessBar 无头截图验收
 *
 * 为什么要有这个：常驻条是这次修"后台任务在界面上隐形"的唯一出口，光过构建
 * 不代表它真能显示、更不代表窄窗口下不溢出。内置 Browser 面板在这台机器上
 * 常拿到 0×0 的视口，所以走 playwright-core + 本机 Chrome。
 *
 * 只抽 index.css 里 .bpm-* 那一段注入，不整包引 CSS —— 全量 CSS 里有字体和
 * 图片 url()，esbuild 解析它们会扯进无关依赖。
 *
 * 用法：node scripts/test-background-process-bar/run.mjs
 */
import { build } from 'esbuild';
import { chromium } from 'playwright-core';
import { mkdirSync, readFileSync, writeFileSync, rmSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, '../..');
const outDir = join(here, '.tmp');
const CHROME = 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe';
const CSS_START = '/* ── 后台进程常驻条';

const failures = [];
function check(name, cond, detail = '') {
  if (cond) console.log(`  ok   ${name}`);
  else {
    console.log(`  FAIL ${name}${detail ? ` — ${detail}` : ''}`);
    failures.push(name);
  }
}

rmSync(outDir, { recursive: true, force: true });
mkdirSync(outDir, { recursive: true });

// 1) 抽出本组件的样式
const fullCss = readFileSync(join(repoRoot, 'src/styles/index.css'), 'utf-8');
const start = fullCss.indexOf(CSS_START);
if (start === -1) {
  console.error('index.css 里找不到后台进程条的样式段，测试无法反映真实外观');
  process.exit(1);
}
const bpmCss = fullCss.slice(start);

// 2) 打包 harness
const jsOut = join(outDir, 'harness.js');
await build({
  entryPoints: [join(here, 'harness.jsx')],
  outfile: jsOut,
  bundle: true,
  format: 'iife',
  platform: 'browser',
  jsx: 'automatic',
  loader: { '.png': 'dataurl' },
  define: { 'process.env.NODE_ENV': '"development"' },
  logLevel: 'warning',
});

const html = `<!doctype html><html><head><meta charset="utf-8">
<style>body{margin:0;background:#0f1419;color:#e6edf3;font-family:system-ui,sans-serif}</style>
<style>${bpmCss}</style></head>
<body><div id="root"></div><script src="${jsOut.replace(/\\/g, '/')}"></script></body></html>`;
const htmlPath = join(outDir, 'index.html');
writeFileSync(htmlPath, html, 'utf-8');

// 3) 起本机 Chrome 截图 + 断言
const browser = await chromium.launch({ executablePath: CHROME, headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1296, height: 720 } });
  await page.goto(pathToFileURL(htmlPath).href);
  await page.waitForSelector('#case-running .bpm-bar');

  const runningText = await page.locator('#case-running .bpm-bar').innerText();
  check('在跑时显示"1 个在跑 · 已 23 分钟"', runningText.includes('1 个在跑 · 已 23 分钟'), runningText);
  check('折叠态就能看到最后一行输出', runningText.includes('reviewer OK'), runningText);
  const staleText = await page.locator('#case-stale .bpm-bar').innerText();
  check('15 分钟无输出时说清多久没动', staleText.includes('已 15 分钟无新输出'), staleText);
  check('陈旧时整条转红', (await page.locator('#case-stale .bpm-bar').getAttribute('class')).includes('is-stale'));
  check('陈旧时圆点不再闪动（还在闪会让人以为在动）', (await page.locator('#case-stale .bpm-dot-pulse').count()) === 0);
  check('全部空闲时不渲染任何条', (await page.locator('#case-empty .bpm-bar').count()) === 0);

  const finishedText = await page.locator('#case-finished .bpm-bar').innerText();
  check('刚跑完显示"后台任务已结束"', finishedText.includes('后台任务已结束'), finishedText);
  check('退出码 0 说"跑完"而不是"退出（码 0）"', finishedText.includes('跑完'), finishedText);

  // 展开明细
  await page.locator('#case-running .bpm-header').click();
  await page.waitForSelector('#case-running .bpm-list');
  const expanded = await page.locator('#case-running .bpm-list').innerText();
  check('展开后能看到 PID', expanded.includes('PID 7652'), expanded);
  check('展开后能看到命令行', expanded.includes('recover_v4.py'), expanded);
  check('展开后能看到工作目录', expanded.includes('shortdrama'), expanded);
  await page.screenshot({ path: join(outDir, 'expanded-1296.png'), fullPage: true });

  await page.locator('#case-running .bpm-header').click();
  await page.waitForSelector('#case-running .bpm-list', { state: 'detached' });
  await page.screenshot({ path: join(outDir, 'collapsed-1296.png'), fullPage: true });

  // 4) 窄窗口：常驻条不许把版面撑出横向滚动
  for (const width of [820, 520, 380]) {
    await page.setViewportSize({ width, height: 720 });
    await page.waitForTimeout(120);
    const m = await page.evaluate(() => ({
      doc: document.documentElement.scrollWidth,
      bar: document.querySelector('#case-running .bpm-bar')?.scrollWidth ?? 0,
      client: document.querySelector('#case-running .bpm-bar')?.clientWidth ?? 0,
      visible: !!document.querySelector('#case-running .bpm-bar'),
    }));
    check(`${width}px 下条仍可见`, m.visible);
    check(`${width}px 下无横向溢出`, m.doc <= width + 1 && m.bar <= m.client + 1, `doc=${m.doc} bar=${m.bar}/${m.client}`);
    if (width === 520) await page.screenshot({ path: join(outDir, 'collapsed-520.png'), fullPage: true });
  }
} finally {
  await browser.close();
}

console.log(`\n截图在 ${outDir}`);
if (failures.length) {
  console.error(`${failures.length} 项失败：${failures.join(', ')}`);
  process.exit(1);
}
console.log('全部通过');
