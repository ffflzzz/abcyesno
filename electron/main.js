const { app, BrowserWindow, Menu, ipcMain, shell, dialog } = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');
const net = require('net');
const { HermesRunner } = require('./backend/hermes-runner');
const { GatewayClient } = require('./backend/gateway-client');
const { createAgUIServer } = require('./backend/agui-server');
const { Storage } = require('./backend/storage');
const { log } = require('./backend/logger');

// Hermes Python backend location (relative to electron/main.js -> project root)
const HERMES_FORK = path.join(__dirname, '..', 'hermes-fork');

// Improve compatibility on some Windows GPUs / sandbox configs.
// GPU acceleration can be re-enabled via env ABC_GPU=1 (e.g. low-end machines
// that stutter on CPU-composited scrolling/animations with the virtualized
// message list). Defaults to disabled for maximum compatibility.
if (process.env.ABC_GPU !== '1' && process.env.ABC_GPU !== 'true') {
  app.commandLine.appendSwitch('disable-gpu');
}
app.commandLine.appendSwitch('no-sandbox');
// Keep SSE / timers alive when window loses focus (agent must keep running in background)
app.commandLine.appendSwitch('disable-background-timer-throttling');

// ── Browser automation (spec §5.5, route B): embedded Electron Chromium ──
// Expose Electron's own Chromium to Playwright via the DevTools Protocol so the
// agent can drive the built-in <webview> "浏览器" panel. Python's pw_browser_*
// tools connect_over_cdp(PW_CDP_URL) and select the webview by its marker URL.
// Port is localhost-only; override via PW_CDP_PORT. Must be set before app ready.
// Browser marker: tiny self-contained page Playwright can target by URL.
// Includes visible body so users see a real (idle) page in the panel, not
// a blank white canvas. Also exposes a `__marker` flag for any consumer that
// wants to detect the idle state.
const BROWSER_PW_MARKER =
  'data:text/html;charset=utf-8,' + encodeURIComponent(
    `<!doctype html><html><head><meta charset="utf-8"><title>browser-pw-marker</title>` +
    `<style>html,body{margin:0;height:100%;background:#0d1117;color:#c9d1d9;` +
    `font-family:-apple-system,Segoe UI,sans-serif;display:flex;align-items:center;` +
    `justify-content:center;flex-direction:column;gap:14px;user-select:none;}` +
    `.dot{width:14px;height:14px;border-radius:50%;background:#3fb950;` +
    `box-shadow:0 0 18px #3fb95080;animation:pulse 1.6s ease-in-out infinite;}` +
    `h1{margin:0;font-size:18px;font-weight:600;letter-spacing:0.5px;}` +
    `p{margin:0;font-size:13px;color:#8b949e;text-align:center;line-height:1.6;` +
    `max-width:340px;}` +
    `@keyframes pulse{0%,100%{opacity:0.55}50%{opacity:1}}</style>` +
    `</head><body><div class="dot"></div>` +
    `<h1>内置浏览器已就绪</h1>` +
    `<p>可在上方地址栏手动浏览，或等 Agent 调用浏览器工具。<br>` +
    `（空闲页面 — Agent 一旦驱动将自动切换）</p>` +
    `<script>window.__browserPwMarker=true;</script></body></html>`
  );
const PW_CDP_PORT = parseInt(process.env.PW_CDP_PORT || '18922', 10) || 18922;
app.commandLine.appendSwitch('remote-debugging-port', String(PW_CDP_PORT));
process.env.PW_CDP_URL = `http://127.0.0.1:${PW_CDP_PORT}`;
process.env.PW_WEBVIEW_MARKER = BROWSER_PW_MARKER;

// Use a stable writable user-data dir under the user home to avoid temp/permission issues
const userDataDir = path.join(os.homedir(), '.hermes_portable_data');
try {
  fs.mkdirSync(userDataDir, { recursive: true });
} catch (_) {}
app.setPath('userData', userDataDir);
// Surface HERMES_HOME in the main process too (agnes.js reads .env from here)
process.env.HERMES_HOME = userDataDir;

const storage = new Storage(userDataDir);

let mainWindow = null;
let hermesRunner = null;
let gatewayClient = null;
let aguiServer = null;
let aguiPort = 0;
let gatewayReady = false; // true only after gatewayClient WS 'open' fires
// Track every BrowserWindow we own so the "window-all-closed" guard and
// clean-shutdown hooks know when the user is fully done.
const allWindows = new Set();

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 640,
    minHeight: 480,
    backgroundColor: '#0f1419',
    show: false,
    icon: path.join(__dirname, 'bach-icon.png'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      webviewTag: true,
      backgroundThrottling: false,
    },
  });

  mainWindow.once('ready-to-show', () => {
    mainWindow.center();
    mainWindow.show();
    mainWindow.focus();
  });

  mainWindow.webContents.on('render-process-gone', (_event, details) => {
    log('main', `render-process-gone: reason=${details.reason} exitCode=${details.exitCode}`);
  });
  mainWindow.webContents.on('console-message', (_event, level, message, line, sourceId) => {
    const levelName = ['verbose', 'info', 'warning', 'error'][level] || level;
    log('renderer', `[${levelName}] ${message} (${sourceId}:${line})`);
  });

  if (process.env.NODE_ENV === 'development' || process.argv.includes('--dev')) {
    mainWindow.loadURL('http://localhost:5173');
    mainWindow.webContents.openDevTools();
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
    allWindows.delete(mainWindow);
  });
  allWindows.add(mainWindow);
}

// ── Detached result panel — same dist, narrower URL ───────────────────────
// Mirrors Chrome's "move tab to a new window" gesture. The new window loads
// dist/index.html with ?panel=result so App.jsx skips the Sidebar/ChatLayout
// and renders the ResultPanel standalone. Both windows share the same AG-UI
// bridge (HTTP) and Hermes gateway (WS in main), so live workflow runs and
// tabs stay in sync.
function createDetachedPanelWindow(opts = {}) {
  const { workflowId = '', tab = 'overview', sessionId = '', collapsed = 'false' } = opts;
  const params = new URLSearchParams({
    panel: 'result',
    workflowId,
    tab,
    sessionId,
    collapsed,
  });
  const url = `file:///${path.join(__dirname, '..', 'dist', 'index.html').replace(/\\/g, '/')}?${params.toString()}#/${encodeURIComponent(workflowId || 'result')}`;
  const win = new BrowserWindow({
    width: 960,
    height: 720,
    minWidth: 480,
    minHeight: 360,
    backgroundColor: '#0f1419',
    title: 'Abcyesno · 结果面板',
    show: false,
    icon: path.join(__dirname, 'bach-icon.png'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      webviewTag: true,
      backgroundThrottling: false,
    },
  });
  win.once('ready-to-show', () => {
    win.center();
    win.show();
  });
  win.webContents.on('render-process-gone', (_e, details) => {
    log('main', `detached-panel render-process-gone: reason=${details.reason} exitCode=${details.exitCode}`);
  });
  win.webContents.on('console-message', (_e, level, message, line, sourceId) => {
    const levelName = ['verbose', 'info', 'warning', 'error'][level] || level;
    log('detached-panel', `[${levelName}] ${message} (${sourceId}:${line})`);
  });
  win.on('closed', () => allWindows.delete(win));
  allWindows.add(win);
  win.loadURL(url);
  return win;
}

ipcMain.handle('detach-result-panel', (_event, opts) => {
  // Don't allow duplicate windows for the exact same workflow/tab combo —
  // focus the existing one instead. (Same UX as Chrome re-opening a tab.)
  const target = `wf=${opts?.workflowId || ''}|tab=${opts?.tab || 'overview'}|s=${opts?.sessionId || ''}`;
  for (const w of allWindows) {
    if (w.__detachKey === target) {
      if (w.isMinimized()) w.restore();
      w.focus();
      return { success: true, reused: true };
    }
  }
  const win = createDetachedPanelWindow(opts || {});
  win.__detachKey = target;
  return { success: true, reused: false };
});

function findAvailablePort(host, startPort) {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', (err) => {
      if (err.code === 'EADDRINUSE') {
        findAvailablePort(host, startPort + 1).then(resolve, reject);
      } else {
        reject(err);
      }
    });
    server.once('listening', () => {
      const { port } = server.address();
      server.close(() => resolve(port));
    });
    server.listen(startPort, host);
  });
}

async function startAgUIServer() {
  aguiPort = await findAvailablePort('127.0.0.1', 9121);
  return new Promise((resolve, reject) => {
    const agentsDir = [
      path.join(HERMES_FORK, 'skills', 'langgraph_agents', 'agents'),
      process.env.ABC_LANGGRAPH_AGENTS_DIR,
    ].filter(Boolean);
    const aguiApp = createAgUIServer(() => gatewayClient, storage, { agentsDir });
    const server = aguiApp.listen(aguiPort, '127.0.0.1', (err) => {
      if (err) return reject(err);
      log('agui-server', `AG-UI bridge listening on http://127.0.0.1:${aguiPort}`);
      resolve(server);
    });
    server.on('error', reject);
  });
}

let backendStarting = null;

async function startBackend() {
  if (backendStarting) return backendStarting;
  backendStarting = doStartBackend();
  try {
    await backendStarting;
  } catch (err) {
    backendStarting = null;
    throw err;
  }
}

async function doStartBackend() {
  // Start the AG-UI bridge first so the port is bound and exported to env
  // before Hermes (and its Python tools) are spawned. The Python langgraph
  // runtime needs AGUI_PORT to relay HITL workflow events back to the bridge.
  aguiServer = await startAgUIServer();
  process.env.AGUI_PORT = String(aguiPort);

  hermesRunner = new HermesRunner({ app });
  await hermesRunner.start();

  const wsUrl = `ws://127.0.0.1:${hermesRunner.getPort()}/api/ws`;
  gatewayClient = new GatewayClient({ url: wsUrl, token: hermesRunner.getSessionToken() });

  gatewayClient.on('event', (type, params) => {
    if (type === 'approval.request') {
      if (mainWindow) {
        mainWindow.webContents.send('approval-request', params.payload || params);
      }
    } else if (type === 'sudo.request') {
      if (mainWindow) {
        mainWindow.webContents.send('sudo-request', params.payload || params);
      }
    } else if (type === 'secret.request') {
      if (mainWindow) {
        mainWindow.webContents.send('secret-request', params.payload || params);
      }
    } else if (type === 'terminal.read.request') {
      if (mainWindow) {
        mainWindow.webContents.send('terminal-read-request', params.payload || params);
      }
    }
  });

  gatewayClient.on('close', () => {
    gatewayReady = false;
    if (mainWindow) mainWindow.webContents.send('gateway-status', { connected: false });
  });

  gatewayClient.on('open', () => {
    gatewayReady = true;
    if (mainWindow) {
      mainWindow.webContents.send('gateway-status', { connected: true });
      // Inform the frontend that it can re-read the AG-UI port and start
      // the CopilotKit runtime.
      mainWindow.webContents.send('agui-ready', { port: aguiPort });
    }
  });

  // Give Hermes web server a moment to fully mount the /api/ws endpoint.
  await new Promise((r) => setTimeout(r, 500));
  await gatewayClient.connect();
}

app.whenReady().then(async () => {
  // Native application menu (Help carries the DevTools entry).
  const template = [
    {
      label: '文件',
      submenu: [{ role: 'quit', label: '退出' }],
    },
    {
      label: '编辑',
      submenu: [
        { role: 'undo', label: '撤销' },
        { role: 'redo', label: '重做' },
        { type: 'separator' },
        { role: 'cut', label: '剪切' },
        { role: 'copy', label: '复制' },
        { role: 'paste', label: '粘贴' },
        { role: 'selectall', label: '全选' },
      ],
    },
    {
      label: '帮助',
      submenu: [
        {
          label: '开发控制台 (DevTools)',
          accelerator: 'F12',
          click: () => {
            if (mainWindow) mainWindow.webContents.openDevTools({ mode: 'right' });
          },
        },
        {
          label: '打开数据目录',
          click: async () => {
            try { await shell.openPath(userDataDir); } catch (_) {}
          },
        },
        {
          label: '关于 Abcyesno',
          click: () => {
            dialog.showMessageBox(mainWindow || undefined, {
              title: '关于 Abcyesno',
              message: 'Abcyesno v1.3.0\n便携桌面 Agent 平台',
            });
          },
        },
      ],
    },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));

  // Show the window immediately so the user sees a loading surface while
  // Hermes starts in the background. The frontend Bootstrap renders a
  // spinner until the backend is ready.
  createWindow();

  try {
    await startBackend();
  } catch (err) {
    log('main', `backend start failed: ${err && err.message ? err.message : String(err)}`);
    dialog.showErrorBox('Abcyesno 启动失败', err && err.message ? err.message : String(err));
    app.quit();
  }

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    }
  });
});

app.on('window-all-closed', () => {
  if (aguiServer) {
    try { aguiServer.close(); } catch (_) {}
    aguiServer = null;
  }
  if (gatewayClient) {
    gatewayClient.close();
    gatewayClient = null;
  }
  if (hermesRunner) hermesRunner.stop();
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', () => {
  if (aguiServer) {
    try { aguiServer.close(); } catch (_) {}
    aguiServer = null;
  }
  if (gatewayClient) {
    gatewayClient.close();
    gatewayClient = null;
  }
  if (hermesRunner) hermesRunner.stop();
});

// IPC handlers
ipcMain.handle('get-version', () => {
  return `1.3.0`;
});

// Browser automation (§5.5): tell the renderer the CDP endpoint + the marker URL
// its <webview> must load so Python's pw_browser_* tools can find and drive it.
ipcMain.handle('get-browser-info', () => {
  return {
    cdpUrl: process.env.PW_CDP_URL || '',
    marker: process.env.PW_WEBVIEW_MARKER || '',
  };
});

ipcMain.handle('get-agui-port', () => {
  // Only expose the port to the renderer after the gateway WebSocket is
  // truly connected.  Without this guard Bootstrap reads aguiPort as soon
  // as the Express bridge binds (step 1 of startup) and mounts App while
  // Hermes is still starting — causing the first message to hit
  // "Hermes gateway not connected".
  return gatewayReady ? aguiPort : 0;
});

// Studio workbench: proxy Agnes calls through IPC (avoids renderer fetch/CSP issues)
const agnes = require('./backend/agnes');
ipcMain.handle('studio-call', async (event, { action, params }) => {
  try {
    if (action === 'generate-image') {
      const url = await agnes.generateImage(params);
      return { ok: true, url };
    }
    if (action === 'generate-video') {
      const url = await agnes.generateVideo(params);
      return { ok: true, url };
    }
    if (action === 'prepare-export') {
      const project = params.project || {};
      const timeline = Array.isArray(params.timeline) ? params.timeline : [];
      const shotCfg = params.shotCfg || {};
      const shots = Array.isArray(params.shots) ? params.shots : [];
      if (!timeline.length) return { ok: false, error: '时间轴为空' };

      const [w, h] = String(project.res || '1080×1920').split('×').map(Number);
      const fps = Number(project.fps || 30) || 30;
      const safeName = String(project.name || 'short_drama').replace(/[^\w一-龥-]/g, '_');

      const home = process.env.HERMES_HOME || path.join(os.homedir(), '.hermes_portable_data');
      const draftDir = path.join(home, 'studio_exports', `${safeName}.draft`);
      const matsDir = path.join(draftDir, 'materials');
      fs.mkdirSync(matsDir, { recursive: true });

      const videos = [];
      const images = [];
      const segments = [];
      let start = 0;
      let idx = 0;
      for (const k of timeline) {
        const cfg = shotCfg[k] || { dur: 4, trans: 'none' };
        const dur = Math.max(1, cfg.dur) * 1000000; // microseconds
        const sh = shots.find((x) => x.key === k) || {};
        idx += 1;
        const id = `m${idx}`;
        let materialId = null;
        if (sh.videoUrl) {
          const dest = await agnes.downloadMedia(sh.videoUrl, matsDir, `shot_${k}`);
          videos.push({ id, path: path.relative(draftDir, dest).replace(/\\/g, '/'), duration: dur });
          materialId = id;
        } else if (sh.imgUrl) {
          const dest = await agnes.downloadMedia(sh.imgUrl, matsDir, `shot_${k}`);
          images.push({ id, path: path.relative(draftDir, dest).replace(/\\/g, '/'), duration: dur });
          materialId = id;
        } else {
          continue;
        }
        const seg = { material_id: materialId, target_timerange: { start, duration: dur } };
        if (cfg.trans && cfg.trans !== 'none') seg.transition = { type: cfg.trans };
        segments.push(seg);
        start += dur;
      }
      if (!segments.length) {
        return { ok: false, error: '没有可导出的素材（请先在「分镜」页生成图/视频）' };
      }

      const draft = {
        app_version: '5.0.0', fps, width: w || 1080, height: h || 1920,
        version: '1.0.0',
        materials: { videos, images, audios: [], texts: [], transitions: [] },
        tracks: [{ type: 'video', id: 't1', segments }],
      };
      fs.writeFileSync(path.join(draftDir, 'draft_content.json'), JSON.stringify(draft, null, 2), 'utf-8');
      fs.writeFileSync(
        path.join(draftDir, 'draft_meta.json'),
        JSON.stringify({ app_version: '5.0.0', platform: 'pc', project: 'draft', tm: Date.now() }, null, 2),
        'utf-8'
      );
      const totalSec = start / 1000000;
      return { ok: true, json: draft, totalSec, count: segments.length, draftDir };
    }
    return { ok: false, error: `未知 action: ${action}` };
  } catch (e) {
    return { ok: false, error: e.message };
  }
});

ipcMain.handle('get-api-key-status', () => {
  return hermesRunner ? hermesRunner.getApiKeyStatus() : false;
});

let apiKeyRestartPromise = null;

ipcMain.handle('validate-api-key', async (_event, key) => {
  try {
    const res = await fetch('https://apihub.agnes-ai.com/v1/models', {
      method: 'GET',
      headers: { Authorization: `Bearer ${key}` },
    });
    if (res.status === 200) {
      return { valid: true };
    }
    const text = await res.text().catch(() => '');
    return { valid: false, error: `HTTP ${res.status}${text ? `: ${text.slice(0, 200)}` : ''}` };
  } catch (err) {
    return { valid: false, error: err && err.message ? err.message : String(err) };
  }
});

ipcMain.handle('set-api-key', async (_event, key) => {
  if (!hermesRunner) return { success: false, error: 'runner not ready' };

  // Serialize API-key restarts so two rapid saves don't spawn two Hermes processes.
  if (apiKeyRestartPromise) {
    try { await apiKeyRestartPromise; } catch (_) {}
  }

  apiKeyRestartPromise = (async () => {
    hermesRunner.setApiKey(key);
    await hermesRunner.restart();

    if (gatewayClient) {
      gatewayClient.removeAllListeners();
      gatewayClient.close();
    }

    const wsUrl = `ws://127.0.0.1:${hermesRunner.getPort()}/api/ws`;
    gatewayClient = new GatewayClient({ url: wsUrl, token: hermesRunner.getSessionToken() });

    gatewayClient.on('event', (type, params) => {
      if (type === 'approval.request') {
        if (mainWindow) {
          mainWindow.webContents.send('approval-request', params.payload || params);
        }
      } else if (type === 'sudo.request') {
        if (mainWindow) {
          mainWindow.webContents.send('sudo-request', params.payload || params);
        }
      } else if (type === 'secret.request') {
        if (mainWindow) {
          mainWindow.webContents.send('secret-request', params.payload || params);
        }
      } else if (type === 'terminal.read.request') {
        if (mainWindow) {
          mainWindow.webContents.send('terminal-read-request', params.payload || params);
        }
      }
    });
    gatewayClient.on('close', () => {
      gatewayReady = false;
      if (mainWindow) mainWindow.webContents.send('gateway-status', { connected: false });
    });
    gatewayClient.on('open', () => {
      gatewayReady = true;
      if (mainWindow) {
        mainWindow.webContents.send('gateway-status', { connected: true });
        mainWindow.webContents.send('agui-ready', { port: aguiPort });
      }
    });

    await gatewayClient.connect();
    return { success: true };
  })();

  try {
    return await apiKeyRestartPromise;
  } catch (err) {
    log('main', `restart after api key change failed: ${err.message}`);
    return { success: false, error: err.message };
  } finally {
    apiKeyRestartPromise = null;
  }
});

ipcMain.handle('get-status', async () => {
  return {
    gatewayConnected: gatewayClient ? gatewayClient.ready : false,
    hermesReady: hermesRunner !== null,
  };
});

ipcMain.handle('list-assistants', async () => {
  return storage.listAssistants();
});

ipcMain.handle('create-assistant', async (_event, data) => {
  return storage.createAssistant(data);
});

ipcMain.handle('update-assistant', async (_event, id, data) => {
  return storage.updateAssistant(id, data);
});

ipcMain.handle('delete-assistant', async (_event, id) => {
  return storage.deleteAssistant(id);
});

ipcMain.handle('list-skills', async () => {
  if (!gatewayClient || !gatewayClient.ready) {
    return [];
  }
  try {
    const res = await gatewayClient.request('skills.manage', {}, 15000);
    const byCategory = (res && res.skills) || {};
    // Flatten to a simple list of { id, name, category } objects.
    const list = [];
    for (const [category, names] of Object.entries(byCategory)) {
      for (const name of names) {
        list.push({ id: name, name, category });
      }
    }
    return list;
  } catch (err) {
    log('main', `list-skills failed: ${err.message}`);
    return [];
  }
});

ipcMain.handle('list-sessions', async (_event, assistantId) => {
  return storage.listSessions(assistantId);
});

ipcMain.handle('create-session', async (_event, assistantId, title) => {
  return storage.createSession(assistantId, title);
});

ipcMain.handle('delete-session', async (_event, id) => {
  return storage.deleteSession(id);
});

ipcMain.handle('get-session', async (_event, id) => {
  return storage.getSession(id);
});

ipcMain.handle('update-session', async (_event, id, data) => {
  return storage.updateSession(id, data);
});

ipcMain.handle('respond-approval', async (_event, id, choice) => {
  if (!gatewayClient || !gatewayClient.ready) {
    throw new Error('gateway not connected');
  }
  return gatewayClient.request('approval.respond', { id, choice }, 30000);
});

ipcMain.handle('gateway-request', async (_event, method, params, timeout) => {
  if (!gatewayClient || !gatewayClient.ready) {
    throw new Error('gateway not connected');
  }
  return gatewayClient.request(method, params || {}, timeout || 60000);
});

ipcMain.handle('interrupt-session', async (_event, sessionId) => {
  if (!gatewayClient || !gatewayClient.ready || !sessionId) {
    return { success: false, error: 'not ready' };
  }
  try {
    const hermesSessionId = await storage.getThreadMapping(sessionId);
    if (hermesSessionId) {
      await gatewayClient.request('session.interrupt', { session_id: hermesSessionId }, 10000);
    }
    return { success: true };
  } catch (err) {
    log('main', `interrupt-session failed: ${err.message}`);
    return { success: false, error: err.message };
  }
});

ipcMain.handle('set-permission-mode', async (_event, mode, sessionId) => {
  if (!gatewayClient || !gatewayClient.ready || !sessionId) {
    return { success: false, error: 'not ready' };
  }
  try {
    const hermesSessionId = await storage.getThreadMapping(sessionId);
    if (!hermesSessionId) {
      return { success: false, error: 'session not mapped' };
    }
    const enabled = mode === 'yolo';
    await gatewayClient.request('session.set_yolo', { session_id: hermesSessionId, enabled }, 10000);
    return { success: true, mode, enabled };
  } catch (err) {
    log('main', `set-permission-mode failed: ${err.message}`);
    return { success: false, error: err.message };
  }
});

ipcMain.handle('log-error', (_event, msg) => {
  log('renderer', `ErrorBoundary: ${msg}`);
});

ipcMain.handle('open-data-dir', async () => {
  if (!userDataDir) return { success: false, error: 'user data dir not set' };
  const result = await shell.openPath(userDataDir);
  // shell.openPath returns an empty string on success, otherwise an error message.
  return { success: result === '', error: result || undefined };
});

ipcMain.handle('select-file', async (_event, options = {}) => {
  const focused = BrowserWindow.getFocusedWindow();
  const result = await dialog.showOpenDialog(focused || undefined, {
    properties: ['openFile'],
    filters: options.filters || [
      { name: '所有文件', extensions: ['*'] },
      { name: '文本', extensions: ['txt', 'md', 'json', 'py', 'js'] },
      { name: '图片', extensions: ['png', 'jpg', 'jpeg', 'gif', 'webp'] },
    ],
  });
  if (result.canceled || !result.filePaths || result.filePaths.length === 0) {
    return null;
  }
  return result.filePaths[0];
});

ipcMain.handle('upload-file', async (_event, sessionId, filePath) => {
  if (!hermesRunner || !sessionId || !filePath) {
    throw new Error('runner, sessionId or filePath missing');
  }
  const home = hermesRunner.getHermesHome();
  const destDir = path.join(home, 'uploads', sessionId);
  fs.mkdirSync(destDir, { recursive: true });
  const fileName = path.basename(filePath);
  const destPath = path.join(destDir, fileName);
  fs.copyFileSync(filePath, destPath);
  return {
    originalPath: filePath,
    localPath: destPath,
    fileName,
    sessionId,
  };
});

// ── Result panel: workspace file tree + read-file (spec §5 / §7.1) ──
// Allowed roots: HERMES_HOME (userData) and the project dir. Anything else is
// rejected to keep the read-only <webview> / file browser from escaping.
const WS_ROOTS = {
  home: userDataDir,
  project: path.join(__dirname, '..'),
};
const TEXT_EXTS = new Set([
  '.txt', '.md', '.markdown', '.json', '.js', '.jsx', '.ts', '.tsx', '.mjs',
  '.py', '.css', '.scss', '.less', '.html', '.htm', '.csv', '.yml', '.yaml',
  '.xml', '.log', '.env', '.toml', '.ini', '.cfg', '.sh', '.bat', '.ps1',
]);
const SKIP_DIRS = new Set(['node_modules', '.git', 'dist', 'release', 'build', '__pycache__', '.venv']);

function resolveWsPath(rootKey, sub) {
  const base = WS_ROOTS[rootKey] || userDataDir;
  const realBase = path.resolve(base);
  let target = realBase;
  if (sub) target = path.resolve(realBase, sub);
  if (target !== realBase && !target.startsWith(realBase + path.sep)) {
    throw new Error('path escapes allowed root');
  }
  return target;
}

async function buildTree(dir, root, depth, maxDepth, maxFiles) {
  if (depth > maxDepth) return [];
  let entries;
  try {
    entries = await fs.promises.readdir(dir, { withFileTypes: true });
  } catch (_) {
    return [];
  }
  const children = [];
  for (const e of entries) {
    if (e.name.startsWith('.')) continue;
    if (e.isDirectory() && SKIP_DIRS.has(e.name)) continue;
    const full = path.join(dir, e.name);
    const rel = path.relative(root, full);
    if (e.isDirectory()) {
      children.push({
        name: e.name,
        path: rel,
        type: 'dir',
        children: await buildTree(full, root, depth + 1, maxDepth, maxFiles),
      });
    } else {
      let stat = null;
      try { stat = await fs.promises.stat(full); } catch (_) {}
      children.push({
        name: e.name,
        path: rel,
        type: 'file',
        mtime: stat ? stat.mtimeMs : 0,
        size: stat ? stat.size : 0,
      });
    }
    if (children.length >= maxFiles) break;
  }
  return children;
}

ipcMain.handle('list-workspace', async (_event, opts = {}) => {
  const rootKey = opts.root === 'project' ? 'project' : 'home';
  const sub = opts.path || '';
  const base = WS_ROOTS[rootKey];
  const target = resolveWsPath(rootKey, sub);
  let isDir = false;
  try { isDir = (await fs.promises.stat(target)).isDirectory(); } catch (_) {}
  const rootName = rootKey === 'project' ? 'abcyesno (项目)' : 'HERMES_HOME';
  if (!isDir) {
    return { root: rootKey, path: sub, name: rootName, type: 'dir', error: 'not a directory', children: [] };
  }
  let children;
  try {
    children = await buildTree(target, target, 0, 5, 500);
  } catch (err) {
    children = [];
  }
  return { root: rootKey, path: sub, name: rootName, type: 'dir', children };
});

ipcMain.handle('read-file', async (_event, opts = {}) => {
  const rootKey = opts.root === 'project' ? 'project' : 'home';
  const rel = opts.path || '';
  const full = resolveWsPath(rootKey, rel);
  let stat;
  try { stat = await fs.promises.stat(full); } catch (err) {
    return { path: rel, error: 'file not found' };
  }
  if (stat.isDirectory()) return { path: rel, error: 'is a directory' };
  const ext = path.extname(full).toLowerCase();
  if (stat.size > 2 * 1024 * 1024) {
    return { path: rel, ext, size: stat.size, tooLarge: true };
  }
  if (!TEXT_EXTS.has(ext)) {
    return { path: rel, ext, size: stat.size, binary: true };
  }
  let content = '';
  try { content = await fs.promises.readFile(full, 'utf8'); } catch (err) {
    return { path: rel, ext, size: stat.size, error: 'read failed' };
  }
  return { path: rel, ext, size: stat.size, content };
});

ipcMain.handle('open-external', async (_event, url) => {
  if (!url || typeof url !== 'string') return { success: false, error: 'missing url' };
  try {
    await shell.openExternal(url);
    return { success: true };
  } catch (err) {
    return { success: false, error: err && err.message ? err.message : String(err) };
  }
});

// ── Read a local image file as a base64 data URL ──
// The renderer is loaded via file:// and Chromium blocks cross-directory
// file:// subresource loads (opaque origin). So the sandboxed renderer cannot
// <img src="file:///C:/..."> a workspace artifact. We read it in the main
// process (full FS access) and return a data: URL — bypassing the restriction.
const IMG_EXTS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg', '.ico']);
ipcMain.handle('read-local-image', async (_event, p) => {
  if (!p || typeof p !== 'string') return { dataUrl: null, error: 'missing path' };
  let fp = p.trim();
  // Accept file:// URLs (strip scheme; file:///C:/... -> C:/...)
  if (/^file:\/\//i.test(fp)) {
    fp = fp.replace(/^file:\/\//i, '');
    if (/^\/[A-Za-z]:/.test(fp)) fp = fp.slice(1);
  }
  let stat;
  try {
    stat = await fs.promises.stat(fp);
  } catch (err) {
    return { dataUrl: null, error: 'not found: ' + fp };
  }
  if (!stat.isFile()) return { dataUrl: null, error: 'not a file' };
  if (stat.size > 16 * 1024 * 1024) return { dataUrl: null, error: 'too large' };
  const ext = path.extname(fp).toLowerCase();
  if (!IMG_EXTS.has(ext)) return { dataUrl: null, error: 'unsupported type: ' + ext };
  const mime =
    ext === '.jpg' ? 'image/jpeg' :
    ext === '.svg' ? 'image/svg+xml' :
    'image/' + ext.replace('.', '');
  try {
    const buf = await fs.promises.readFile(fp);
    const b64 = buf.toString('base64');
    return { dataUrl: `data:${mime};base64,${b64}` };
  } catch (err) {
    return { dataUrl: null, error: String(err && err.message ? err.message : err) };
  }
});

process.on('uncaughtException', (err) => {
  log('main', `uncaughtException: ${err && err.message ? err.message : String(err)}`);
});
