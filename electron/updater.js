// electron/updater.js
// 应用内自动更新（electron-updater + GitHub Releases provider）。
//
// 工作方式（2026-09-21 接线）：
//   1. 仅 NSIS 安装版可用：打包时 publish 配置会往 resources/ 写 app-update.yml，
//      它存在 = 更新器可用；dev / 绿色解压版没有 → supported=false，前端
//      SettingsPanel 自动降级为原来的"打开 Releases 页"行为。
//   2. 启动 30 秒后静默检查一次；有新版自动后台下载（blockmap 差分）。
//   3. 下载完成后**不自动重启**——正在跑的任务（创作链/微信桥）不能被杀。
//      改为在屏幕右上角弹一张提醒卡（UpdatePopup，?panel=update），用户点
//      「立即安装」才装；点 × 则本次运行不再为该版本重复弹。
//   4. 用户确认后走**静默安装**：quitAndInstall(true, true) → NSIS 带 /S
//      跑，不再出现需要手动点下一步的安装向导；--force-run 让装完自动重开。
//      （已核实：assisted installer 支持 /S，安装目录从注册表回读；
//        --updated 会让 CHECK_APP_RUNNING 跳过「应用正在运行」对话框。）
//   5. autoInstallOnAppQuit = true：下载完成后用户正常退出时也会静默装上。
//
// 状态机（推送给渲染层的 state 快照，与 wechat-status 同模式）：
//   idle → checking → downloading → downloaded
//                     ↘ error（可重试）     ↘ uptodate（已是最新）
const { app, ipcMain, BrowserWindow, screen, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const { log } = require('./backend/logger');

// 启动后延迟检查：避开冷启动的 Definder 扫描窗口（hermes-runner MAX_WAIT
// 的教训——开机后 30s 内磁盘最忙），也让首屏加载不抢网络。
const AUTO_CHECK_DELAY_MS = 30 * 1000;

const RELEASES_URL = 'https://github.com/ffflzzz/abcyesno/releases';

// 提醒卡尺寸/位置（屏幕右上角，不抢焦点所在的应用）
const POPUP_W = 380;
const POPUP_H = 214;
const POPUP_MARGIN = 16;

let autoUpdater = null;       // lazy require：dev/绿色版永不加载
let getWindow = () => null;
let autoCheckTimer = null;
let popupWin = null;
// 本次运行内已弹过提醒卡的版本：一个版本只弹一次，点了「稍后」也不再弹。
let popupShownFor = null;

const state = {
  supported: false,   // app-update.yml 存在（NSIS 安装版）
  status: 'idle',     // idle | checking | downloading | downloaded | uptodate | error
  info: null,         // { version } 发现的新版本
  progress: null,     // { percent, transferred, total, bytesPerSecond }
  error: null,        // 最近一次错误消息（截断后）
};

function isSupported() {
  try {
    if (!app.isPackaged) return false;
    return fs.existsSync(path.join(process.resourcesPath || '', 'app-update.yml'));
  } catch (_) {
    return false;
  }
}

function broadcast() {
  const win = getWindow();
  if (win && !win.isDestroyed()) {
    try { win.webContents.send('updater-state', { ...state }); } catch (_) {}
  }
}

function setState(patch) {
  Object.assign(state, patch);
  log('updater', `state=${state.status}${state.info ? ` v${state.info.version}` : ''}${state.error ? ` err=${state.error}` : ''}`);
  broadcast();
}

function truncateErr(err) {
  const msg = String((err && err.message) || err || 'unknown');
  return msg.length > 160 ? `${msg.slice(0, 160)}…` : msg;
}

// ── 下载完成提醒卡（右上角无边框小窗）────────────────────────────────────
// 复用主窗口同一份 dist 打包，靠 ?panel=update 让 main.jsx 只渲染
// UpdatePopup（不加载 App、不等后端）。数据全部走 URL 参数，窗口是无状态的。
function isDev() {
  return process.env.NODE_ENV === 'development' || process.argv.includes('--dev');
}

function popupUrl(toVersion) {
  const params = new URLSearchParams({
    panel: 'update',
    from: app.getVersion(),
    to: toVersion || '',
  });
  if (isDev()) return `http://localhost:5173/?${params.toString()}`;
  const file = `file:///${path.join(__dirname, '..', 'dist', 'index.html').replace(/\\/g, '/')}`;
  return `${file}?${params.toString()}`;
}

function popupBounds() {
  const { workArea } = screen.getPrimaryDisplay();
  return {
    x: Math.round(workArea.x + workArea.width - POPUP_W - POPUP_MARGIN),
    y: Math.round(workArea.y + POPUP_MARGIN),
    width: POPUP_W,
    height: POPUP_H,
  };
}

function closePopup() {
  if (popupWin && !popupWin.isDestroyed()) {
    try { popupWin.close(); } catch (_) {}
  }
  popupWin = null;
}

function focusPopup() {
  if (!popupWin || popupWin.isDestroyed()) return;
  if (popupWin.isMinimized()) popupWin.restore();
  popupWin.show();
  popupWin.focus();
}

function showUpdatePopup(toVersion) {
  const version = toVersion || (state.info && state.info.version) || '';
  if (!version) return;
  if (popupShownFor === version) {
    focusPopup();
    return;
  }
  popupShownFor = version;
  const b = popupBounds();
  popupWin = new BrowserWindow({
    ...b,
    show: false,
    frame: false,
    resizable: false,
    movable: true,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    hasShadow: true,
    backgroundColor: '#1c2128',
    title: 'Abcyesno · 更新提醒',
    icon: path.join(__dirname, 'bach-icon.png'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      backgroundThrottling: false,
    },
  });
  // Windows 上无边框窗口的置顶要在创建后再钉一层才稳。
  popupWin.setAlwaysOnTop(true, 'floating');
  popupWin.once('ready-to-show', () => {
    if (!popupWin || popupWin.isDestroyed()) return;
    popupWin.show();
    popupWin.focus();
  });
  popupWin.on('closed', () => { popupWin = null; });
  popupWin.webContents.on('render-process-gone', (_e, details) => {
    log('updater', `popup render-process-gone: reason=${details.reason} exitCode=${details.exitCode}`);
    popupWin = null;
  });
  // 卡片里不放任何外链可点区域之外的导航；兜底拦一次。
  popupWin.webContents.on('will-navigate', (e) => e.preventDefault());
  popupWin.loadURL(popupUrl(version));
  log('updater', `popup shown for v${version}`);
}

function ensureAutoUpdater() {
  if (autoUpdater) return autoUpdater;
  // electron-updater 只在打包环境可用；dev 下 require 也无妨（不触发网络）
  const { autoUpdater: au } = require('electron-updater');
  autoUpdater = au;
  au.autoDownload = true;          // 有新版直接后台下载
  au.autoInstallOnAppQuit = true;  // 下载完成后正常退出也会安装
  au.logger = {
    info: (...a) => log('updater', ...a),
    warn: (...a) => log('updater', ...a),
    error: (...a) => log('updater', ...a),
  };
  au.on('checking-for-update', () => setState({ status: 'checking', error: null }));
  au.on('update-available', (info) => {
    setState({ status: 'downloading', info: { version: info && info.version }, error: null });
  });
  au.on('update-not-available', () => {
    setState({ status: 'uptodate', error: null, progress: null });
  });
  au.on('download-progress', (p) => {
    setState({
      status: 'downloading',
      progress: {
        percent: Math.round(p.percent || 0),
        transferred: p.transferred,
        total: p.total,
        bytesPerSecond: p.bytesPerSecond,
      },
    });
  });
  au.on('update-downloaded', (info) => {
    setState({ status: 'downloaded', info: { version: info && info.version }, progress: null });
    showUpdatePopup(info && info.version);
  });
  au.on('error', (err) => {
    // 差分下载失败时 electron-updater 会内部回退全量下载，不触发 error；
    // 走到这里说明 check/下载彻底失败。已下载完成的状态不被覆盖。
    if (state.status === 'downloaded') return;
    setState({ status: 'error', error: truncateErr(err), progress: null });
  });
  return autoUpdater;
}

async function checkForUpdates() {
  if (!state.supported) return { ...state };
  try {
    await ensureAutoUpdater().checkForUpdates();
  } catch (err) {
    if (state.status !== 'downloaded') {
      setState({ status: 'error', error: truncateErr(err), progress: null });
    }
  }
  return { ...state };
}

function installUpdate() {
  if (state.status !== 'downloaded') return false;
  const au = ensureAutoUpdater();
  closePopup();
  // 先把状态广播出去（按钮反馈），再延迟退出——给渲染层一个绘制窗口。
  setTimeout(() => {
    try {
      // isSilent=true → NSIS 带 /S 静默装，不再弹需要手动点下一步的安装向导；
      // isForceRunAfter=true → 装完自动重开新版。
      au.quitAndInstall(true, true);
    } catch (err) {
      log('updater', `quitAndInstall failed: ${err && err.message}`);
      app.quit();
    }
  }, 300);
  return true;
}

/**
 * @param {() => Electron.BrowserWindow|null} getWindowFn 主窗口 getter（惰性求值）
 */
function init(getWindowFn) {
  if (typeof getWindowFn === 'function') getWindow = getWindowFn;
  state.supported = isSupported();
  log('updater', `init: supported=${state.supported} packaged=${app.isPackaged}`);

  ipcMain.handle('updater-get-state', () => ({ ...state }));
  ipcMain.handle('updater-check', () => {
    // 手动检查 = 用户在问更新的事，允许提醒卡为该版本再弹一次。
    popupShownFor = null;
    return checkForUpdates();
  });
  ipcMain.handle('updater-install', () => installUpdate());
  ipcMain.handle('updater-popup-action', (_event, action) => {
    switch (action) {
      case 'install':
        return installUpdate();
      case 'details':
        shell.openExternal(RELEASES_URL).catch((err) => {
          log('updater', `openExternal failed: ${err && err.message}`);
        });
        return true;
      case 'later':
        closePopup();
        return true;
      default:
        return false;
    }
  });

  if (state.supported) {
    // 静默自动检查一次；失败仅记日志（不打扰用户），下次启动再试。
    autoCheckTimer = setTimeout(() => {
      autoCheckTimer = null;
      checkForUpdates();
    }, AUTO_CHECK_DELAY_MS);
  }
}

module.exports = { init, checkForUpdates, installUpdate };
