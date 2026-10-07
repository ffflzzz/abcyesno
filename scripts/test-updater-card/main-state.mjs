/**
 * electron/updater.js 状态机的离线验收（假 electron + 假 electron-updater）。
 *
 * 为什么要有这个：这次改的是**顺序**——查到新版不许自己开始下载，必须先让用户在
 * 左下角那张卡上点一下。这个顺序光看代码不算数，得拿盘上事实证明：
 *   ① 收到 update-available 时 downloadUpdate() 被调了 **0 次**；
 *   ② 卡片窗口开在**左下角**（断言的是 x/y 与 workArea 的关系，不写死 1920×1080）；
 *   ③ 用户点「下载并更新」之后才 downloadUpdate()，进度靠 updater-state 推给卡片；
 *   ④ 「已就绪」之前点安装必须被拦住（不许把正在跑的链杀掉）；
 *   ⑤ 卡片还开着时不会为同一版本重复开窗，但下载完成后会把关掉的卡弹回来。
 *
 * 用法：node scripts/test-updater-card/main-state.mjs
 */
import Module from 'node:module';
import { createRequire } from 'node:module';
import { mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const repoRoot = resolve(here, '../..');
const require = createRequire(import.meta.url);

const WORK = { x: 40, y: 60, width: 1600, height: 900 };
const POPUP_W = 380;
const POPUP_H = 236;
const MARGIN = 16;

const failures = [];
function check(name, cond, detail = '') {
  if (cond) console.log(`  ok   ${name}`);
  else {
    console.log(`  FAIL ${name}${detail ? ` — ${String(detail).slice(0, 240)}` : ''}`);
    failures.push(name);
  }
}

// ── 假件 ────────────────────────────────────────────────────────────────────
const created = [];      // 开过的提醒卡
const logs = [];
const handlers = {};     // ipcMain.handle
const sends = [];        // 所有 webContents.send('updater-state', …)

class FakeWin {
  constructor(opts) {
    this.opts = opts;
    this.destroyed = false;
    this.listeners = {};
    this.webContents = {
      send: (ch, payload) => { sends.push({ to: this, ch, payload }); },
      on: () => {},
    };
    created.push(this);
  }
  setAlwaysOnTop() {}
  once(ev, cb) { (this.listeners[ev] = this.listeners[ev] || []).push(cb); }
  on() {}
  loadURL(url) { this.url = url; }
  show() {}
  focus() {}
  restore() {}
  isMinimized() { return false; }
  isDestroyed() { return this.destroyed; }
  close() {
    this.destroyed = true;
    (this.listeners.closed || []).forEach((cb) => cb());
  }
  emitReady() { (this.listeners['ready-to-show'] || []).forEach((cb) => cb()); }
  get lastState() {
    for (let i = sends.length - 1; i >= 0; i -= 1) {
      if (sends[i].to === this && sends[i].ch === 'updater-state') return sends[i].payload;
    }
    return null;
  }
}

let mainWin = null;
// 主窗也是 FakeWin，别把它算进「开过几张卡」。
const popups = () => created.filter((w) => !w.opts || !w.opts.isMain);
const lastPopup = () => popups()[popups().length - 1];

const auHandlers = {};
const auCalls = { check: 0, download: 0, quitAndInstall: [] };
const fakeAu = {
  autoDownload: null,
  autoInstallOnAppQuit: null,
  logger: null,
  on(ev, cb) { auHandlers[ev] = cb; },
  async checkForUpdates() {
    auCalls.check += 1;
    auHandlers['checking-for-update']();
    auHandlers['update-available']({ version: '1.5.9' });
  },
  async downloadUpdate() {
    auCalls.download += 1;
    // 真 electron-updater 在这里发进度；harness 手动推，好断言卡片收到没
    return { on: () => {} };
  },
  quitAndInstall(silent, forceRun) { auCalls.quitAndInstall.push({ silent, forceRun }); },
};

const fakeElectron = {
  app: {
    isPackaged: true,
    getVersion: () => '1.5.8',
    quit() {},
  },
  ipcMain: { handle: (name, fn) => { handlers[name] = fn; } },
  BrowserWindow: FakeWin,
  screen: { getPrimaryDisplay: () => ({ workArea: { ...WORK } }) },
  shell: { openExternal: async () => {} },
};

const resDir = join(here, '.tmp-res');
rmSync(resDir, { recursive: true, force: true });
mkdirSync(resDir, { recursive: true });
writeFileSync(join(resDir, 'app-update.yml'), 'provider: github\n');
process.resourcesPath = resDir;

const origLoad = Module._load;
Module._load = function patched(request, ...rest) {
  if (request === 'electron') return fakeElectron;
  if (request === './backend/logger') return { log: (...a) => logs.push(a.join(' ')) };
  if (request === 'electron-updater') return { autoUpdater: fakeAu };
  return origLoad.call(this, request, ...rest);
};

const updater = require(join(repoRoot, 'electron/updater.js'));
// ⛔ 不还原 Module._load：ensureAutoUpdater() 里的 require('electron-updater')
// 是**惰性**的，要等第一次检查更新才发生，还原了就会去加载真包。

mainWin = new FakeWin({ isMain: true });
updater.init(() => mainWin);

// ── ① supported / 不自动下载 ────────────────────────────────────────────────
let st = await handlers['updater-get-state']();
check('resources/app-update.yml 在 ⇒ supported', st.supported === true, JSON.stringify(st));
check('查过之前 status=idle', st.status === 'idle', st.status);

await handlers['updater-check']();
st = await handlers['updater-get-state']();
check('查到新版后 status=available（不是 downloading）', st.status === 'available', st.status);
check('autoDownload=false（不许偷偷下）', fakeAu.autoDownload === false, String(fakeAu.autoDownload));
check('发现新版时 downloadUpdate 被调 0 次', auCalls.download === 0, String(auCalls.download));
check('autoInstallOnAppQuit 仍为 true', fakeAu.autoInstallOnAppQuit === true);

// ── ② 卡片开在左下角 ────────────────────────────────────────────────────────
check('弹了一张卡', popups().length === 1, `开窗 ${popups().length} 张`);
const b = popups()[0].opts;
check('卡片贴左下角（x=workArea.x+边距）',
  b.x === WORK.x + MARGIN, `x=${b.x} 应为 ${WORK.x + MARGIN}`);
check('卡片底边离 workArea 底边 = 边距',
  b.y + b.height === WORK.y + WORK.height - MARGIN,
  `y=${b.y} h=${b.height} 底边和 ${b.y + b.height} 应为 ${WORK.y + WORK.height - MARGIN}`);
check('卡 URL 带首帧 status=available 与两个版本号',
  /panel=update/.test(popups()[0].url) && /status=available/.test(popups()[0].url)
  && /from=1\.5\.8/.test(popups()[0].url) && /to=1\.5\.9/.test(popups()[0].url), popups()[0].url);

// 同一版本重复触发不另开窗（卡还在，只带回前台）
auHandlers['update-available']({ version: '1.5.9' });
check('同一版本不重复开窗', popups().length === 1, `开窗 ${popups().length} 张`);

// ── ③ 用户点「下载并更新」才下 ───────────────────────────────────────────────
await handlers['updater-popup-action'](null, 'download');
st = await handlers['updater-get-state']();
check('点了才 downloadUpdate（1 次）', auCalls.download === 1, String(auCalls.download));
check('点完 status=downloading', st.status === 'downloading', st.status);
auHandlers['download-progress']({ percent: 42.6, transferred: 52e6, total: 122e6, bytesPerSecond: 3e6 });
check('进度事件推给了卡片', popups()[0].lastState && popups()[0].lastState.status === 'downloading',
  JSON.stringify(popups()[0].lastState));
check('进度百分比已取整并进了快照',
  popups()[0].lastState.progress.percent === 43, JSON.stringify(popups()[0].lastState.progress));
const toMain = sends.filter((s) => s.to === mainWin && s.ch === 'updater-state').length;
check('主窗（设置面板那行小字）同步收到推送', toMain > 0, `推送 ${toMain} 次`);

// ── ④ 没下完不许装 ──────────────────────────────────────────────────────────
const installedEarly = await handlers['updater-popup-action'](null, 'install');
check('下载中就点「安装」会被拦住', installedEarly === false && auCalls.quitAndInstall.length === 0,
  JSON.stringify({ installedEarly, calls: auCalls.quitAndInstall }));

// ── ⑤ 下载失败要有出口 ──────────────────────────────────────────────────────
auHandlers['error'](new Error('ECONNRESET 断线'));
st = await handlers['updater-get-state']();
check('下载失败落到 error 并带上原因', st.status === 'error' && !!st.error, JSON.stringify(st));
const beforeRetry = auCalls.download;
await handlers['updater-popup-action'](null, 'retry');
check('已知道有新版本时，「重试」直接重试下载', auCalls.download === beforeRetry + 1,
  `${beforeRetry} → ${auCalls.download}`);
check('重试用完状态回到 downloading',
  (await handlers['updater-get-state']()).status === 'downloading');

// ── ⑥ 下完了：只报「已就绪」，不自己重启 ────────────────────────────────────
auHandlers['update-downloaded']({ version: '1.5.9' });
st = await handlers['updater-get-state']();
check('下完 status=downloaded', st.status === 'downloaded', st.status);
check('下完没自动重启（正在跑的链不能被杀）', auCalls.quitAndInstall.length === 0,
  JSON.stringify(auCalls.quitAndInstall));

// ── ⑦ 卡被关了也要把「已就绪」弹回来，且不倒退 ──────────────────────────────
await handlers['updater-popup-action'](null, 'later');
check('点 × 后卡片关掉', lastPopup().destroyed === true);
const dlBeforeRecheck = auCalls.download;
await handlers['updater-check']();
st = await handlers['updater-get-state']();
check('重新查一次会把关掉的卡弹回来', popups().length === 2, `开窗 ${popups().length} 张`);
check('重新查一次不把「已就绪」倒回「等你下载」', st.status === 'downloaded', st.status);
check('重新查一次不重复下载', auCalls.download === dlBeforeRecheck,
  `${dlBeforeRecheck} → ${auCalls.download}`);
check('重弹的卡首帧就是 downloaded',
  /status=downloaded/.test(popups()[1].url), popups()[1].url);
auHandlers['error'](new Error('再查一次网络不通'));
check('包已在盘上时，一次失败的检查不抹掉「已就绪」',
  (await handlers['updater-get-state']()).status === 'downloaded');

// ── ⑧ 用户点安装 → 静默装 + 自动重开 ────────────────────────────────────────
const ok = await handlers['updater-popup-action'](null, 'install');
check('已就绪时点安装放行', ok === true);
await new Promise((r) => setTimeout(r, 360));
check('走的是静默安装 + 装完自动重开',
  auCalls.quitAndInstall.length === 1
  && auCalls.quitAndInstall[0].silent === true && auCalls.quitAndInstall[0].forceRun === true,
  JSON.stringify(auCalls.quitAndInstall));

rmSync(resDir, { recursive: true, force: true });
console.log(`\n${failures.length ? `FAILED ${failures.length} 项：${failures.join(' / ')}` : '主进程状态机全部通过'}`);
process.exit(failures.length ? 1 : 0);
