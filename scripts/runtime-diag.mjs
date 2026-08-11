// Abcyesno 运行时自诊断 harness
// ────────────────────────────────────────────────────────────────────────
// 解决痛点：很多 bug 只表现在 GUI（页面 JS 异常 / console.error / ErrorBoundary
// 兜底界面 / 渲染进程崩溃），用户没法一个个贴。本脚本自动：
//   1. 启动打包好的 Abcyesno（或 attach 到已在跑的实例）
//   2. 通过 CDP（playwright-core connectOverCDP）驱动 GUI、监听报错
//   3. 读取主进程统一落盘的 electron.log（含 renderer console / 崩溃 / ErrorBoundary）
//   4. 输出结构化报告（JSON + Markdown）+ 截图到 selfdiag-shots/
//
// 用法：
//   node scripts/runtime-diag.mjs                 # 全新启动并观察基线
//   node scripts/runtime-diag.mjs --attach        # 连已在跑的实例，观察 --observe-ms
//   ABEXE=path/to/Abcyesno.exe node scripts/runtime-diag.mjs
//   PW_CDP_PORT=18922 node scripts/runtime-diag.mjs
//
// 注意：本项目在 WorkBuddy（也是 Electron）内运行，必须剥离 ELECTRON_RUN_AS_NODE /
// NODE_OPTIONS，否则目标 app 会被当成 Node 启动即退出。

import { chromium } from 'playwright-core';
import { spawn, execSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const ROOT = process.cwd();
const args = process.argv.slice(2);
const ATTACH = args.includes('--attach');
const observeMs = Number(args.find((a) => a.startsWith('--observe-ms='))?.split('=')[1] || (ATTACH ? 20000 : 12000));
const CDP_PORT = Number(process.env.PW_CDP_PORT || '18922');
const EXE = process.env.ABEXE || path.join(ROOT, 'release', 'win-unpacked', 'Abcyesno.exe');
const LOG_FILE = path.join(os.homedir(), '.hermes_portable_data', 'logs', 'electron.log');
const SHOTS = path.join(ROOT, 'selfdiag-shots');
const REPORT_JSON = path.join(ROOT, 'selfdiag-report.json');
const REPORT_MD = path.join(ROOT, 'selfdiag-report.md');

fs.mkdirSync(SHOTS, { recursive: true });
const ts0 = Date.now();

// ── 收集器 ──────────────────────────────────────────────────────────────
const findings = []; // {source, type, message, stack?, url?, ts}
const logLines = [];  // 来自 electron.log 的过滤行

function add(source, type, message, extra = {}) {
  const entry = { source, type, message: String(message).slice(0, 4000), ts: new Date().toISOString(), ...extra };
  findings.push(entry);
  const tag = type === 'error' || type === 'crash' ? '❌' : type === 'warning' ? '⚠️' : 'ℹ️';
  console.log(`${tag} [${source}/${type}] ${entry.message.slice(0, 160)}`);
  return entry;
}

// ── 日志读取：取本次诊断起点之后的 error/warning/崩溃/后端关键行 ───────────
function tailElectronLog(sinceMs) {
  try {
    if (!fs.existsSync(LOG_FILE)) return;
    const text = fs.readFileSync(LOG_FILE, 'utf8');
    const lines = text.split(/\r?\n/);
    for (const ln of lines) {
      if (!ln.trim()) continue;
      const m = ln.match(/^\[(\d{4}-\d{2}-\d{2}T[\d:.]+[+-]\d{2}:?\d{2})\]/);
      const t = m ? Date.parse(m[1]) : NaN;
      if (!Number.isNaN(t) && t < sinceMs - 2000) continue; // 只看本次启动后的
      const low = ln.toLowerCase();
      if (/(error|warn|fail|exception|traceback|render-process-gone|errorboundary|crash|gateway|agui|hermes-runner)/.test(low)) {
        logLines.push(ln);
      }
    }
  } catch (e) {
    add('harness', 'warning', `读取 electron.log 失败: ${e.message}`);
  }
}

// ── 进程管理 ──────────────────────────────────────────────────────────────
function killApp() {
  try { execSync('taskkill /F /IM Abcyesno.exe', { stdio: 'ignore' }); } catch {}
}
function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

async function waitCdp(timeoutMs) {
  const url = `http://127.0.0.1:${CDP_PORT}/json/version`;
  const t = Date.now();
  for (;;) {
    try {
      const res = await fetch(url);
      if (res.ok) return true;
    } catch {}
    if (Date.now() - t > timeoutMs) return false;
    await sleep(400);
  }
}

let appProc = null;
async function startApp() {
  if (!fs.existsSync(EXE)) {
    add('harness', 'error', `找不到打包产物: ${EXE}（先 npm run electron:build 或 --dir）`);
    process.exit(1);
  }
  killApp();
  await sleep(800);
  const env = { ...process.env };
  delete env.ELECTRON_RUN_AS_NODE;
  delete env.NODE_OPTIONS;
  env.PW_CDP_PORT = String(CDP_PORT);
  console.log(`▶ 启动 ${EXE} (CDP=${CDP_PORT})`);
  appProc = spawn(EXE, [], { env, detached: true, stdio: 'ignore' });
  appProc.on('error', (e) => add('harness', 'error', `spawn 失败: ${e.message}`));
  const ok = await waitCdp(45000);
  if (!ok) { add('harness', 'error', 'CDP 端口未在 45s 内就绪（app 可能启动失败/无显示器）'); }
  return ok;
}

// ── 主流程 ────────────────────────────────────────────────────────────────
async function main() {
  console.log('════ Abcyesno 运行时自诊断 ════');
  console.log(`模式: ${ATTACH ? 'attach(连已在跑实例)' : 'fresh(全新启动)'} | 观察窗口: ${observeMs}ms | CDP: ${CDP_PORT}`);
  console.log(`exe: ${EXE}`);
  console.log(`log: ${LOG_FILE}`);

  if (!ATTACH) {
    const ok = await startApp();
    if (!ok) { tailElectronLog(ts0); finalize(); process.exit(1); }
  } else {
    const ok = await waitCdp(8000);
    if (!ok) { add('harness', 'error', `attach 模式但 CDP(${CDP_PORT}) 不可达——请先启动 Abcyesno`); process.exit(1); }
  }

  let browser, page;
  try {
    browser = await chromium.connectOverCDP(`http://127.0.0.1:${CDP_PORT}`);
    const ctx = browser.contexts()[0];
    page = ctx.pages().find((p) => !/devtools/.test(p.url())) || ctx.pages()[0];
    if (!page) throw new Error('找不到主窗口 page');
    console.log(`✓ 已连接主窗口: ${page.url()}`);

    // 监听页面级运行时错误
    page.on('pageerror', (err) => add('pageerror', 'error', err.message, { stack: err.stack }));
    page.on('crash', () => add('page', 'crash', '渲染进程崩溃 (page crash)'));
    page.on('console', (msg) => {
      const type = msg.type(); // error | warning | log | info | debug
      if (type === 'error' || type === 'warning') {
        const txt = msg.text();
        const loc = msg.location();
        add('console', type, txt, { url: loc?.url, line: loc?.lineNumber });
      }
    });
    page.on('requestfailed', (req) => {
      const f = req.failure();
      // 只关心应用自身的请求失败（过滤掉外部资源偶发失败噪声）
      const u = req.url();
      if (/127\.0\.0\.1|file:\/\/|\/api\/|\/assets\//.test(u)) {
        add('network', 'warning', `${u} → ${f ? f.errorText : 'failed'}`);
      }
    });
  } catch (e) {
    add('harness', 'error', `CDP 连接/监听失败: ${e.message}`);
    tailElectronLog(ts0);
    finalize();
    cleanup();
    process.exit(1);
  }

  // 等后端就绪（window.hermes.getAguiPort 返回真值端口）
  let backendPort = 0;
  try {
    for (let i = 0; i < 50; i++) {
      backendPort = await page.evaluate(async () => {
        try { return await window.hermes.getAguiPort(); } catch { return 0; }
      }).catch(() => 0);
      if (backendPort) break;
      await sleep(600);
    }
  } catch (e) {
    add('harness', 'warning', `轮询后端就绪异常: ${e.message}`);
  }
  add('backend', backendPort ? 'info' : 'warning',
    backendPort ? `AG-UI 后端就绪 (port=${backendPort})` : 'AG-UI 后端在观察期内未就绪（可能 Hermes 启动失败/无 API Key）');

  // React 是否挂载
  let reactMounted = false;
  try {
    reactMounted = await page.evaluate(() => {
      const root = document.getElementById('root');
      return !!(root && root.children && root.children.length > 0);
    }).catch(() => false);
  } catch {}
  add('frontend', reactMounted ? 'info' : 'error',
    reactMounted ? 'React 根节点已挂载' : 'React 根节点为空（白屏/未渲染）');

  // ErrorBoundary 兜底界面是否触发
  let errorBoundary = null;
  try {
    errorBoundary = await page.evaluate(() => {
      const fb = document.querySelector('.error-fallback');
      if (!fb) return null;
      const msg = fb.querySelector('.error-msg-block pre')?.textContent || '';
      const stack = fb.querySelector('.error-stack-pre')?.textContent || '';
      return { msg, stack };
    }).catch(() => null);
  } catch {}
  if (errorBoundary) add('errorboundary', 'error', `GUI 兜底界面已触发: ${errorBoundary.msg}`, { stack: errorBoundary.stack });

  // 截图（基线）
  try {
    const shot = path.join(SHOTS, `baseline-${Date.now()}.png`);
    await page.screenshot({ path: shot, fullPage: false });
    console.log(`📸 截图: ${shot}`);
  } catch (e) { add('harness', 'warning', `截图失败: ${e.message}`); }

  // 观察窗口：这段时间内的 console/pageerror 已在监听器中收集
  console.log(`\n… 观察 ${observeMs}ms（此期间 GUI 产生的报错会被自动捕获）…`);
  await sleep(observeMs);

  // 观察结束再截一张，对比异常
  try {
    const shot2 = path.join(SHOTS, `after-${Date.now()}.png`);
    await page.screenshot({ path: shot2, fullPage: false });
  } catch {}

  tailElectronLog(ts0);
  finalize();
  cleanup();
  process.exit(0);
}

function finalize() {
  const errors = findings.filter((f) => f.type === 'error' || f.type === 'crash');
  const warnings = findings.filter((f) => f.type === 'warning');
  const summary = {
    generatedAt: new Date().toISOString(),
    mode: ATTACH ? 'attach' : 'fresh',
    cdpPort: CDP_PORT,
    totalFindings: findings.length,
    errorCount: errors.length,
    warningCount: warnings.length,
    electronLogLines: logLines.length,
    verdict: errors.length ? '有运行时错误，见 details' : (warnings.length ? '仅有警告，未见致命错误' : '未见运行时错误'),
  };
  const report = { summary, findings, electronLogTail: logLines.slice(-120) };
  fs.writeFileSync(REPORT_JSON, JSON.stringify(report, null, 2));

  const md = [];
  md.push('# Abcyesno 运行时自诊断报告');
  md.push(`- 生成时间: ${summary.generatedAt}`);
  md.push(`- 模式: ${summary.mode} | CDP: ${summary.cdpPort}`);
  md.push(`- 结论: **${summary.verdict}**`);
  md.push(`- 错误 ${summary.errorCount} / 警告 ${summary.warningCount} / 日志命中 ${summary.electronLogLines}`);
  md.push('');
  md.push('## 抓到的报错（按出现顺序）');
  if (findings.length === 0) md.push('_无_');
  for (const f of findings) {
    md.push(`### [${f.type}] ${f.source}`);
    md.push(`- 时间: ${f.ts}`);
    md.push(`- 信息: ${f.message}`);
    if (f.url) md.push(`- 位置: ${f.url}${f.line ? ':' + f.line : ''}`);
    if (f.stack) md.push(`- 堆栈:\n\`\`\`\n${f.stack.slice(0, 2000)}\n\`\`\``);
    md.push('');
  }
  md.push('## electron.log 关键行（本次运行）');
  md.push('```');
  md.push(logLines.slice(-120).join('\n') || '(无)');
  md.push('```');
  fs.writeFileSync(REPORT_MD, md.join('\n'));

  console.log('\n════ 诊断完成 ════');
  console.log(`结论: ${summary.verdict}`);
  console.log(`报告: ${REPORT_JSON}`);
  console.log(`报告(MD): ${REPORT_MD}`);
}

function cleanup() {
  try { if (browser && !ATTACH) browser.close().catch(() => {}); } catch {}
  if (!ATTACH) killApp();
}

process.on('unhandledRejection', (e) => { add('harness', 'error', `unhandledRejection: ${e && e.message || e}`); });
main().catch((e) => { add('harness', 'error', `FATAL: ${e && e.message || e}`); tailElectronLog(ts0); finalize(); cleanup(); process.exit(1); });
