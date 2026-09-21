// electron/updater.js
// 应用内自动更新（electron-updater + GitHub Releases provider）。
//
// 工作方式（2026-09-21 接线）：
//   1. 仅 NSIS 安装版可用：打包时 publish 配置会往 resources/ 写 app-update.yml，
//      它存在 = 更新器可用；dev / 绿色解压版没有 → supported=false，前端
//      SettingsPanel 自动降级为原来的"打开 Releases 页"行为。
//   2. 启动 30 秒后静默检查一次（不弹窗）；有新版自动后台下载（blockmap 差分）。
//   3. 下载完成后**不自动重启**——正在跑的任务（创作链/微信桥）不能被杀，
//      由用户在设置面板点「重启更新」才 quitAndInstall。
//   4. autoInstallOnAppQuit = true：下载完成后用户正常退出时也会装上。
//
// 状态机（推送给渲染层的 state 快照，与 wechat-status 同模式）：
//   idle → checking → downloading → downloaded
//                     ↘ error（可重试）     ↘ uptodate（已是最新）
const { app, ipcMain } = require('electron');
const path = require('path');
const fs = require('fs');
const { log } = require('./backend/logger');

// 启动后延迟检查：避开冷启动的 Definder 扫描窗口（hermes-runner MAX_WAIT
// 的教训——开机后 30s 内磁盘最忙），也让首屏加载不抢网络。
const AUTO_CHECK_DELAY_MS = 30 * 1000;

let autoUpdater = null;       // lazy require：dev/绿色版永不加载
let getWindow = () => null;
let autoCheckTimer = null;

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
  // 先把状态广播出去（按钮反馈），再延迟退出——给渲染层一个绘制窗口。
  setTimeout(() => {
    try {
      au.quitAndInstall(false, true);
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
  ipcMain.handle('updater-check', () => checkForUpdates());
  ipcMain.handle('updater-install', () => installUpdate());

  if (state.supported) {
    // 静默自动检查一次；失败仅记日志（不打扰用户），下次启动再试。
    autoCheckTimer = setTimeout(() => {
      autoCheckTimer = null;
      checkForUpdates();
    }, AUTO_CHECK_DELAY_MS);
  }
}

module.exports = { init, checkForUpdates, installUpdate };
