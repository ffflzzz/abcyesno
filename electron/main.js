const { app, BrowserWindow, Menu, ipcMain, shell, dialog, webContents, globalShortcut, protocol } = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');
const net = require('net');
const http = require('http');
const { HermesRunner } = require('./backend/hermes-runner');
const { PaperRewriterDashboardRunner } = require('./backend/paper-rewriter-dashboard-runner');
const { createWechatBridgeRunner } = require('./backend/wechat-bridge-runner');
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
// Keep it visually empty (transparent background, no text) so the React hint
// overlay in BrowserPanel is the single source of truth for the idle UI.
// This prevents ghosting/double-text when the overlay and marker page stack.
// Also exposes a `__marker` flag for any consumer that wants to detect idle.
const BROWSER_PW_MARKER =
  'data:text/html;charset=utf-8,' + encodeURIComponent(
    `<!doctype html><html><head><meta charset="utf-8"><title>browser-pw-marker</title>` +
    `<style>html,body{margin:0;height:100%;background:transparent;}</style>` +
    `</head><body><script>window.__browserPwMarker=true;</script></body></html>`
  );
const PW_CDP_PORT = parseInt(process.env.PW_CDP_PORT || '18922', 10) || 18922;
app.commandLine.appendSwitch('remote-debugging-port', String(PW_CDP_PORT));

// Register a privileged custom protocol so the renderer can load local
// workspace media (images/videos) without direct file:// access. The scheme
// must be registered before app ready.
protocol.registerSchemesAsPrivileged([
  { scheme: 'abcyesno-local', privileges: { standard: true, secure: true, supportFetchAPI: true, stream: true, bypassCSP: true } },
]);
process.env.PW_CDP_URL = `http://127.0.0.1:${PW_CDP_PORT}`;
process.env.PW_WEBVIEW_MARKER = BROWSER_PW_MARKER;

// ── Browser automation driver service (spec §5.5, route B, native) ──
// Instead of Playwright connect_over_cdp (which cannot target Electron's
// <webview> guests), the renderer reports the guest webContentsId here, and a
// tiny localhost-only HTTP service drives that *visible* webview natively via
// webContents.loadURL / executeJavaScript / capturePage. The Python side
// (pw_browser_tool.py) POSTs action requests to this service. Port is bound to
// 127.0.0.1 only; override via PW_BROWSER_DRIVER_PORT.
const PW_BROWSER_DRIVER_PORT = parseInt(process.env.PW_BROWSER_DRIVER_PORT || '18923', 10) || 18923;
process.env.PW_BROWSER_DRIVER_URL = `http://127.0.0.1:${PW_BROWSER_DRIVER_PORT}`;

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
let paperDashboardRunner = null;
let wechatBridgeRunner = null;
let gatewayClient = null;
let aguiServer = null;
let aguiPort = 0;
let gatewayReady = false; // true only after gatewayClient WS 'open' fires
// Track every BrowserWindow we own so the "window-all-closed" guard and
// clean-shutdown hooks know when the user is fully done.
const allWindows = new Set();

// ── Browser automation driver (Electron-native <webview> driver) ──
// `browserWebviewId` is the guest webContentsId reported by the renderer once
// the 浏览器 panel's <webview> is dom-ready. The driver service below uses it
// to drive the *visible* in-app browser. Cleared (self-heal) when the id is
// stale/destroyed so a reopened panel re-registers cleanly.
let browserWebviewId = null;
let browserDriverServer = null;

// Page-side selector resolver shared by /click and /type. Supports the same
// selector vocabulary the old Playwright tools accepted: css, //xpath,
// text=..., role=..., placeholder=....
const DRIVER_RESOLVER_JS = `
function __abcResolve(sel){
  if(!sel) return null;
  if(sel.startsWith('text=')){
    var t=sel.slice(5);
    var all=Array.prototype.slice.call(document.querySelectorAll('*'));
    var el=all.filter(function(e){return e.children.length===0 && (e.textContent||'').trim()===t;})[0];
    if(el) return el;
    return all.filter(function(e){return (e.textContent||'').indexOf(t)>=0;})[0] || null;
  }
  if(sel.startsWith('placeholder=')) return document.querySelector('[placeholder="'+sel.slice(11)+'"]');
  if(sel.startsWith('role=')) return document.querySelector('[role="'+sel.slice(5)+'"]');
  if(sel.startsWith('//')){ try{ return document.evaluate(sel, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null).singleNodeValue; }catch(_){ return null; } }
  return document.querySelector(sel);
}
`;

// Resolve the reported guest webContents, returning null (and forgetting the
// id) if it is missing or has been destroyed. This makes the driver self-heal
// when the 浏览器 panel is closed and later reopened with a fresh id.
function getBrowserWebviewWC() {
  if (browserWebviewId == null) return null;
  try {
    const wc = webContents.fromId(browserWebviewId);
    if (!wc || wc.isDestroyed()) {
      browserWebviewId = null;
      return null;
    }
    return wc;
  } catch (_) {
    browserWebviewId = null;
    return null;
  }
}

function sendDriverJson(res, code, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(code, {
    'Content-Type': 'application/json',
    'Access-Control-Allow-Origin': '*',
  });
  res.end(body);
}

// Native <webview> driver: a minimal JSON-over-HTTP RPC the Python browser
// tools call. All endpoints require the panel to be open (webview registered);
// otherwise they return 503 {ok:false, error:'NOT_READY'} so the tool can poll.
async function handleBrowserDriverRequest(req, res) {
  const url = req.url || '/';
  if (req.method === 'GET' && url === '/health') {
    return sendDriverJson(res, 200, {
      ok: true,
      ready: browserWebviewId != null,
      webviewId: browserWebviewId,
    });
  }
  if (req.method !== 'POST') {
    return sendDriverJson(res, 405, { ok: false, error: 'method not allowed' });
  }
  let raw = '';
  try {
    for await (const chunk of req) raw += chunk;
  } catch (_) {
    return sendDriverJson(res, 400, { ok: false, error: 'bad request body' });
  }
  let payload = {};
  try {
    if (raw) payload = JSON.parse(raw);
  } catch (_) {
    payload = {};
  }

  const wc = getBrowserWebviewWC();
  if (!wc) return sendDriverJson(res, 503, { ok: false, error: 'NOT_READY' });

  try {
    if (url === '/navigate') {
      const target = payload.url;
      if (!target) return sendDriverJson(res, 400, { ok: false, error: 'url required' });
      await wc.loadURL(String(target));
      const title = await wc.executeJavaScript('document.title').catch(() => '');
      return sendDriverJson(res, 200, { ok: true, url: wc.getURL(), title: title || '' });
    }
    if (url === '/snapshot') {
      const text = await wc.executeJavaScript('document.body ? document.body.innerText : ""');
      return sendDriverJson(res, 200, { ok: true, url: wc.getURL(), text: text || '' });
    }
    if (url === '/click') {
      const sel = payload.ref || payload.selector || '';
      if (!sel) return sendDriverJson(res, 400, { ok: false, error: 'selector required' });
      const js = `(function(){${DRIVER_RESOLVER_JS} var e=__abcResolve(${JSON.stringify(sel)}); if(!e) return false; e.click(); return true;})()`;
      const ok = await wc.executeJavaScript(js);
      if (!ok) return sendDriverJson(res, 400, { ok: false, error: 'element not found for ' + sel });
      return sendDriverJson(res, 200, { ok: true, url: wc.getURL() });
    }
    if (url === '/type') {
      const sel = payload.ref || payload.selector || '';
      const text = payload.text != null ? String(payload.text) : '';
      if (!sel) return sendDriverJson(res, 400, { ok: false, error: 'selector required' });
      const js = `(function(){${DRIVER_RESOLVER_JS} var e=__abcResolve(${JSON.stringify(sel)}); if(!e) return false; var proto=(e.tagName==='TEXTAREA')?HTMLTextAreaElement.prototype:HTMLInputElement.prototype; var setter=Object.getOwnPropertyDescriptor(proto,'value').set; setter.call(e, ${JSON.stringify(text)}); e.dispatchEvent(new Event('input',{bubbles:true})); e.dispatchEvent(new Event('change',{bubbles:true})); return true;})()`;
      const ok = await wc.executeJavaScript(js);
      if (!ok) return sendDriverJson(res, 400, { ok: false, error: 'element not found for ' + sel });
      return sendDriverJson(res, 200, { ok: true, url: wc.getURL() });
    }
    if (url === '/scroll') {
      const direction = (payload.direction || 'down').toString().trim().toLowerCase();
      const delta = direction === 'up' ? -800 : 800;
      await wc.executeJavaScript(`window.scrollBy(0, ${delta})`);
      return sendDriverJson(res, 200, { ok: true, url: wc.getURL() });
    }
    if (url === '/screenshot') {
      const img = await wc.capturePage();
      const outDir = path.join(userDataDir, 'browser-shots');
      fs.mkdirSync(outDir, { recursive: true });
      const filePath = path.join(outDir, `shot-${Date.now()}.png`);
      fs.writeFileSync(filePath, img.toPNG());
      return sendDriverJson(res, 200, { ok: true, path: filePath, url: wc.getURL() });
    }
    if (url === '/close') {
      await wc.loadURL(BROWSER_PW_MARKER);
      return sendDriverJson(res, 200, { ok: true });
    }
    return sendDriverJson(res, 404, { ok: false, error: 'unknown route ' + url });
  } catch (exc) {
    return sendDriverJson(res, 500, { ok: false, error: exc && exc.message ? exc.message : String(exc) });
  }
}

function startBrowserDriver() {
  if (browserDriverServer) return;
  browserDriverServer = http.createServer((req, res) => {
    handleBrowserDriverRequest(req, res).catch((exc) => {
      try {
        sendDriverJson(res, 500, { ok: false, error: exc && exc.message ? exc.message : String(exc) });
      } catch (_) {}
    });
  });
  browserDriverServer.on('error', (exc) => log('browser-driver', `server error: ${exc && exc.message ? exc.message : String(exc)}`));
  browserDriverServer.listen(PW_BROWSER_DRIVER_PORT, '127.0.0.1', () => {
    log('browser-driver', `listening on 127.0.0.1:${PW_BROWSER_DRIVER_PORT}`);
  });
}

function stopBrowserDriver() {
  if (browserDriverServer) {
    try { browserDriverServer.close(); } catch (_) {}
    browserDriverServer = null;
  }
}

// Toggle DevTools for whichever renderer window currently has focus (main
// window or a detached result panel). Falls back to the main window if no
// window reports focus. Used by the global F12 shortcut.
function toggleFocusedDevTools() {
  const focused = BrowserWindow.getFocusedWindow();
  if (focused && !focused.isDestroyed()) {
    const wc = focused.webContents;
    if (wc.isDevToolsOpened()) wc.closeDevTools();
    else wc.openDevTools({ mode: 'right' });
    return;
  }
  if (mainWindow && !mainWindow.isDestroyed()) {
    if (mainWindow.webContents.isDevToolsOpened()) mainWindow.webContents.closeDevTools();
    else mainWindow.webContents.openDevTools({ mode: 'right' });
  }
}

// On machines where the OS/third-party app grabs the global F12 hotkey
// (common on Lenovo laptops), globalShortcut.register('F12') fails and the
// renderer-side keydown can't reach the key when focus is inside the DevTools
// panel (DevTools is a separate renderer process). Wire the F12 capture
// directly onto the DevTools webContents so it can still be closed with F12
// from inside the panel. devToolsWebContents may be null at the exact
// devtools-opened tick, so retry briefly until it's available.
function wireDevToolsHotkey(win) {
  if (!win || win.isDestroyed()) return;
  win.webContents.on('devtools-opened', () => {
    log('shortcut', 'devtools-opened for a window');
    const attach = () => {
      const dt = win.webContents.devToolsWebContents;
      if (!dt) {
        setTimeout(attach, 50);
        return;
      }
      dt.on('before-input-event', (_event, input) => {
        if (input && input.key === 'F12') {
          _event.preventDefault();
          if (win.webContents.isDevToolsOpened()) {
            win.webContents.closeDevTools();
            log('shortcut', 'devtools F12 hit -> closed');
          }
        }
      });
      log('shortcut', 'devtools F12 hotkey wired');
    };
    attach();
  });
}

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

  wireDevToolsHotkey(mainWindow);

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
  wireDevToolsHotkey(win);

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

  // Best-effort: start the paper_rewriter dashboard local service (FastAPI
  // serving its own React SPA + running the agent). It is independent of
  // Hermes; a failure here must NOT block or crash the app. Fire-and-forget.
  try {
    paperDashboardRunner = new PaperRewriterDashboardRunner({ app });
    paperDashboardRunner.start().catch((err) => {
      log('paper-dashboard', `failed to start (non-fatal): ${err.message}`);
    });
  } catch (err) {
    log('paper-dashboard', `init failed (non-fatal): ${err.message}`);
  }

  // WeChat bridge (vendored wechat-claude-code): in-process daemon bridging
  // personal WeChat <-> the default chat agent via agui-server SSE.
  // Fire-and-forget like paper-dashboard; never blocks startup. Only auto-
  // connects if a WeChat account was previously bound — otherwise it idles
  // in 'idle' state until the user binds from Settings -> 微信绑定.
  try {
    wechatBridgeRunner = createWechatBridgeRunner({
      onStatus: (payload) => {
        log('wechat-bridge', `status: ${payload.state}${payload.detail ? ` (${payload.detail})` : ''}`);
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.webContents.send('wechat-status', payload);
        }
      },
      // Inject the same Storage instance used by ChatShell so the bridge can
      // create / append sessions that show up in the main-app sidebar. The
      // bridge module itself stays decoupled from the Storage schema — it
      // just calls runner actions and the runner talks to storage.
      getStorage: () => storage,
      // Broadcast sessions-updated after a session mutation so App.jsx can
      // re-call loadSessions() and surface the WeChat conversation.
      onSessionsUpdated: () => {
        if (mainWindow && !mainWindow.isDestroyed()) {
          mainWindow.webContents.send('sessions-updated');
        }
      },
    });
    wechatBridgeRunner.start().then((r) => {
      if (!r.ok) log('wechat-bridge', `failed to start (non-fatal): ${r.error}`);
    }).catch((err) => {
      log('wechat-bridge', `start threw (non-fatal): ${err.message}`);
    });
  } catch (err) {
    log('wechat-bridge', `init failed (non-fatal): ${err.message}`);
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
    } else if (type === 'clarify.request') {
      if (mainWindow) {
        mainWindow.webContents.send('clarify-request', params.payload || params);
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
  // Native application menu is intentionally removed. Its entries (DevTools,
  // data directory, about, quit) now live inside the in-app Settings panel,
  // so the window chrome is just a clean title bar (no 文件/编辑/帮助 bar).
  Menu.setApplicationMenu(null);

  // Custom file protocol for local workspace media. The renderer cannot load
  // file:// subresources across directories, so we expose workspace files via
  // abcyesno-local://<encoded-path>. Registered before the window is created
  // so the first document can use it.
  protocol.handle('abcyesno-local', async (request) => {
    try {
      // Renderer encodes paths as `abcyesno-local:///<drive>/<encoded-segs>`,
      // e.g. `abcyesno-local:///C/Users/foo/%E7%A5%9E%E5%A8%81%E7%8B%97.png`.
      // NOTE: with protocol.handle the request.url is still percent-encoded
      // (Chromium does NOT pre-decode it for custom scheme handlers), so we
      // must decodeURIComponent to recover Chinese/space segments before
      // touching the filesystem. Then strip the scheme prefix, restore the
      // drive-letter colon, upper-case the drive, convert to backslashes
      // (Windows fs).
      let fp = request.url.replace(/^abcyesno-local:\/+\/?/, '');
      try { fp = decodeURIComponent(fp); } catch (_) {}
      fp = fp.replace(/^([A-Za-z])\//, '$1:/');
      fp = fp.replace(/^([A-Za-z]):/, (m) => m.toUpperCase());
      fp = fp.replace(/\//g, '\\');
      let exists = null;
      try { exists = fs.existsSync(fp); } catch (_) {}
      log('main', `[abcyesno-local] request url=${request.url} -> path=${fp} exists=${exists}`);
      if (!exists) {
        return new Response('not found: ' + fp, { status: 404 });
      }
      const data = await fs.promises.readFile(fp);
      // Sniff MIME from extension (Electron's registerFileProtocol used to do
      // this; protocol.handle needs us to set it explicitly).
      const ext = (fp.match(/\.([a-zA-Z0-9]+)$/) || [, ''])[1].toLowerCase();
      const mime = ({
        png: 'image/png', jpg: 'image/jpeg', jpeg: 'image/jpeg',
        gif: 'image/gif', webp: 'image/webp', bmp: 'image/bmp',
        svg: 'image/svg+xml', ico: 'image/x-icon',
        mp4: 'video/mp4', webm: 'video/webm', mov: 'video/quicktime',
        mp3: 'audio/mpeg', wav: 'audio/wav', ogg: 'audio/ogg',
        pdf: 'application/pdf', json: 'application/json',
      })[ext] || 'application/octet-stream';
      return new Response(data, { status: 200, headers: { 'Content-Type': mime } });
    } catch (err) {
      log('main', `abcyesno-local protocol error: ${err && err.message ? err.message : String(err)}`);
      return new Response('error: ' + (err && err.message ? err.message : String(err)), { status: 500 });
    }
  });

  // Show the window immediately so the user sees a loading surface while
  // Hermes starts in the background. The frontend Bootstrap renders a
  // spinner until the backend is ready.
  createWindow();

  // Start the Electron-native browser driver service (127.0.0.1 only). The
  // 浏览器 panel reports its webview id via IPC once dom-ready; until then the
  // driver answers NOT_READY and the Python tools poll/retry.
  startBrowserDriver();

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

  // Global shortcuts for quick debugging & settings access.
  // F12 toggles DevTools on the currently focused renderer window.
  // F10 pops open the in-app Settings panel.
  const f12Registered = globalShortcut.register('F12', () => {
    log('shortcut', 'F12 pressed -> toggleFocusedDevTools');
    toggleFocusedDevTools();
  });
  const f10Registered = globalShortcut.register('F10', () => {
    log('shortcut', 'F10 pressed -> open-settings');
    if (mainWindow && !mainWindow.isDestroyed()) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      // If DevTools is open it renders as a native overlay above the main
      // window DOM and would hide the Settings modal. Close it first so the
      // modal is actually visible.
      try {
        if (mainWindow.webContents.isDevToolsOpened()) mainWindow.webContents.closeDevTools();
      } catch (_) {}
      mainWindow.focus();
      mainWindow.webContents.send('open-settings');
    }
  });
  log('shortcut', `global shortcut registration: F12=${globalShortcut.isRegistered('F12')} F10=${globalShortcut.isRegistered('F10')}`);
  if (!f12Registered) {
    // F12 is likely grabbed by an OS/third-party app (common on Lenovo laptops
    // where Vantage/Hotkeys reserve F12). Register an app-level Ctrl+Shift+I
    // DevTools toggle as a focus-independent fallback so DevTools can still be
    // closed reliably even when focus is in the DevTools panel.
    const cssi = globalShortcut.register('Control+Shift+I', () => {
      log('shortcut', 'Ctrl+Shift+I pressed -> toggleFocusedDevTools');
      toggleFocusedDevTools();
    });
    log('shortcut', `F12 unavailable; registered Ctrl+Shift+I as DevTools toggle = ${globalShortcut.isRegistered('Control+Shift+I')}`);
    if (!cssi) log('shortcut', 'WARN: both F12 and Ctrl+Shift+I failed to register. DevTools can only be toggled via the Settings panel button.');
  }
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
  if (paperDashboardRunner) paperDashboardRunner.stop();
  if (wechatBridgeRunner) wechatBridgeRunner.stop();
  stopBrowserDriver();
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
  if (paperDashboardRunner) paperDashboardRunner.stop();
  if (wechatBridgeRunner) wechatBridgeRunner.stop();
  stopBrowserDriver();
  globalShortcut.unregisterAll();
});

// IPC handlers
ipcMain.handle('get-version', () => {
  return `1.3.0`;
});

// Settings-panel entry that used to live in the native 帮助 menu: toggle the
// DevTools dock on the main window (renderer can't do this directly under
// contextIsolation, so it goes through the main process). Toggles so the same
// key/button can open and close it. When opening, bring the main window back
// to the foreground so the renderer-side F12 fallback can still receive the
// next keypress to close the dock.
ipcMain.handle('open-devtools', () => {
  if (mainWindow && !mainWindow.isDestroyed()) {
    const wc = mainWindow.webContents;
    if (wc.isDevToolsOpened()) {
      wc.closeDevTools();
    } else {
      wc.openDevTools({ mode: 'right' });
      // Return OS focus to the main window so the renderer-side F12 keydown
      // listener keeps working after the dock opens.
      try { mainWindow.focus(); } catch (_) {}
    }
  }
  return { ok: true };
});

// Settings-panel entry that used to live in the native 文件 menu: quit the app.
ipcMain.handle('quit-app', () => {
  app.quit();
  return { ok: true };
});

// Browser automation (§5.5): tell the renderer the CDP endpoint + the marker URL
// its <webview> must load so Python's pw_browser_* tools can find and drive it.
ipcMain.handle('get-browser-info', () => {
  return {
    cdpUrl: process.env.PW_CDP_URL || '',
    marker: process.env.PW_WEBVIEW_MARKER || '',
    driverUrl: process.env.PW_BROWSER_DRIVER_URL || '',
  };
});

// Browser automation (§5.5, route B): the renderer reports the guest webview's
// webContentsId so the native driver service can drive the visible <webview>.
ipcMain.on('browser-webview-ready', (_e, id) => {
  if (typeof id === 'number' && id > 0) {
    browserWebviewId = id;
    log('browser-driver', `webview registered: webContentsId=${id}`);
  }
});
ipcMain.on('browser-webview-destroyed', () => {
  browserWebviewId = null;
  log('browser-driver', 'webview unregistered');
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
const characterLibrary = require('./backend/character_library');

// WeChat bridge IPC — same {action, params} dispatch pattern as studio-call.
ipcMain.handle('wechat-call', async (_event, payload) => {
  if (!wechatBridgeRunner) {
    return { ok: false, error: 'wechat bridge not initialized' };
  }
  try {
    const action = (payload && payload.action) || '';
    const params = (payload && payload.params) || {};
    const result = await wechatBridgeRunner.call(action, params);
    // After a session-affecting action, broadcast to the renderer so the
    // main-app sidebar refreshes its session list (the WeChat bridge writes
    // to the same storage as ChatShell's createSession flow).
    if (action === 'ensureSession' || action === 'appendMessage') {
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('sessions-updated');
      }
    }
    return { ok: true, result };
  } catch (err) {
    log('wechat-bridge', `call failed: ${err.message}`);
    return { ok: false, error: err instanceof Error ? err.message : String(err) };
  }
});

// In-flight guard: card ids currently being generated (see character_library.generate).
let characterLibGenerating = null;
ipcMain.handle('studio-call', async (event, { action, params }) => {
  try {
    if (action === 'character_library.list') {
      return { ok: true, cards: characterLibrary.listCards() };
    }
    if (action === 'character_library.upsert') {
      const card = characterLibrary.upsertCard(params || {});
      return { ok: true, card };
    }
    if (action === 'character_library.touch_used') {
      const card = characterLibrary.touchUsed((params && params.id) || "");
      return { ok: true, card: card || null };
    }
    if (action === 'character_library.generate') {
      // Really generate a built-in character's reference sheet via Agnes
      // (replaces the legacy SVG placeholder art). Results are cached on disk
      // under the library images dir, so this is a one-time cost per card.
      const id = (params && params.id) || '';
      const card = characterLibrary.getCard(id);
      if (!card) return { ok: false, error: '角色不存在' };
      if (!card.prompt) return { ok: false, error: '该角色没有提示词，无法生成' };
      if (!characterLibGenerating) characterLibGenerating = new Set();
      if (characterLibGenerating.has(id)) return { ok: false, error: '该角色正在生成中' };
      characterLibGenerating.add(id);
      try {
        const prompt = `${card.prompt}, 角色设定图, character reference sheet, 全身立绘, 干净纯色背景, 高细节, 统一风格`;
        const url = await agnes.generateImage({ prompt, size: '2K', ratio: '3:4' });
        let dest = await agnes.downloadMedia(url, characterLibrary.imagesDir(), id);
        dest = characterLibrary.normalizeImageExt(dest);
        const updated = characterLibrary.setCardImage(id, dest);
        return { ok: true, card: updated };
      } finally {
        characterLibGenerating.delete(id);
      }
    }
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
      } else if (type === 'clarify.request') {
        if (mainWindow) {
          mainWindow.webContents.send('clarify-request', params.payload || params);
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

// 读便携版 ~/.hermes_portable_data/.env 的 Agnes 凭据（与 HermesRunner 同步来源）。
function readAgnesCredentials() {
  const envFile = path.join(os.homedir(), '.hermes_portable_data', '.env');
  let apiKey = '', baseUrl = '';
  try {
    const txt = fs.readFileSync(envFile, 'utf-8');
    for (const line of txt.split(/\r?\n/)) {
      const m = line.match(/^([A-Z_]+)=(.*)$/);
      if (!m) continue;
      const v = m[2].trim().replace(/^["']|["']$/g, '');
      if (m[1] === 'AGNES_API_KEY') apiKey = v || apiKey;
      else if (m[1] === 'AGNES_BASE_URL') baseUrl = v || baseUrl;
    }
  } catch {}
  if (!apiKey) apiKey = process.env.AGNES_API_KEY || '';
  if (!baseUrl) baseUrl = process.env.AGNES_BASE_URL || 'https://apihub.agnes-ai.com/v1';
  const model = process.env.AGNES_TEXT_MODEL || 'agnes-2.5-flash';
  return { apiKey, baseUrl, model };
}

// 工作台智能输入 → 自然语言解析为结构化 inputObj（调 Agnes chat completions）。
// 返回 { inputObj } 或 { error }。
ipcMain.handle('parse-workflow-intent', async (_e, nlText, _manifestId) => {
  const text = String(nlText || '').trim();
  if (!text) return { error: '请输入需求描述' };
  const creds = readAgnesCredentials();
  if (!creds.apiKey) return { error: 'AGNES_API_KEY 未配置（~/.hermes_portable_data/.env）' };

  const prompt = `你是漫剧生成助手的输入解析器。用户用自然语言描述需求，从描述中提取关键字段，严格输出 JSON（不要任何解释、注释或 markdown 代码块）：

{
  "project_name": "string, 项目名（从描述推断或取首句关键词）",
  "script": "string, 单集（single mode）漫剧脚本/剧情描述（保留用户原话的关键剧情，可适当润色）",
  "series_script": "string, 系列（series mode）整体大纲/剧情（覆盖多集走向、主要角色弧线、每集关键节拍）。single 模式下留空字符串。",
  "mode": "'single' 或 'series'",
  "total_episodes": "int, 多集时填整数，single 时填 1",
  "style": "'写实'/'二次元'/'3D' 之一",
  "characters": [{"name": "角色名", "prompt": "角色外观描述"}]
}

规则：
- mode=series → series_script 必须填，覆盖整部剧情大纲（用户原文如果是粗描述，扩写为多集走向大纲；如果是详细脚本，原样保留）
- mode=single → script 必须填；series_script 留空
- 识别"多集/系列/X集/连载"关键词时倾向 series

用户描述：${text}

输出 JSON：`;

  try {
    const resp = await fetch(`${creds.baseUrl}/chat/completions`, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${creds.apiKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        model: creds.model,
        messages: [{ role: 'user', content: prompt }],
        response_format: { type: 'json_object' },
        temperature: 0.3,
      }),
    });
    if (!resp.ok) {
      const t = await resp.text();
      return { error: `LLM 调用失败 ${resp.status}: ${t.slice(0, 200)}` };
    }
    const data = await resp.json();
    const content = data.choices?.[0]?.message?.content || '';
    let parsed;
    try { parsed = JSON.parse(content); }
    catch { return { error: 'LLM 返回非 JSON：' + content.slice(0, 200) } };
    const inputObj = {
      project_name: parsed.project_name || '',
      script: parsed.script || text,
      series_script: typeof parsed.series_script === 'string' ? parsed.series_script : '',
      mode: parsed.mode === 'series' ? 'series' : 'single',
      total_episodes: Number(parsed.total_episodes) > 0 ? Math.min(24, Number(parsed.total_episodes)) : 1,
      style: ['写实', '二次元', '3D'].includes(parsed.style) ? parsed.style : '二次元',
      characters: Array.isArray(parsed.characters)
        ? parsed.characters.slice(0, 8).map((c) => ({
            name: String(c.name || '').trim(),
            prompt: String(c.prompt || '').trim(),
          })).filter((c) => c.name && c.prompt)
        : [],
    };
    return { inputObj };
  } catch (e) {
    return { error: '解析失败：' + (e.message || String(e)) };
  }
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

// Folder picker for per-session workspace binding (see docs/SESSION_WORKSPACE_SPEC.md).
ipcMain.handle('select-directory', async () => {
  const focused = BrowserWindow.getFocusedWindow();
  const result = await dialog.showOpenDialog(focused || undefined, {
    properties: ['openDirectory', 'createDirectory'],
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

// ── Real file download: native save dialog → stream HTTP body to disk ──
// Used by the paper-rewrite "论文产物" tab so "下载 PDF" writes to disk
// instead of relying on shell.openExternal (which silently fails for
// localhost URLs in some default browsers). Returns progress-friendly result.
ipcMain.handle('download-url', async (_event, { url, filename, proxy } = {}) => {
  if (!url || typeof url !== 'string') return { success: false, error: 'missing url' };
  let suggested = filename || (() => {
    try { return decodeURIComponent(url.split('/').pop().split('?')[0]) || 'download'; }
    catch { return 'download'; }
  })();

  // Anchor the dialog to the window that actually sent the IPC request
  // (works for both the in-main-window ResultPanel and the detached panel).
  // Using _event.sender is far more reliable than getFocusedWindow(), which
  // can return null or a hidden launcher/splash window when the OS focus state
  // is ambiguous — that case made the save dialog silently not appear.
  const senderWin = _event.sender ? BrowserWindow.fromWebContents(_event.sender) : null;
  log(`[download-url] called url=${url} senderWin=${senderWin ? senderWin.id : 'null'} allWindows=${BrowserWindow.getAllWindows().length}`);
  const save = await dialog.showSaveDialog(senderWin || undefined, {
    defaultPath: suggested,
    title: '保存文件',
  });
  log(`[download-url] dialog result canceled=${save?.canceled} filePath=${save?.filePath}`);
  if (save.canceled || !save.filePath) return { success: false, canceled: true };

  const target = save.filePath;
  const reqHeaders = {};
  // Forward a proxy only if explicitly provided (keeps localhost direct).
  const doGet = () => new Promise((resolve, reject) => {
    const req = http.get(url, { headers: reqHeaders }, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        // Follow one redirect (e.g. dashboard FileResponse without inline).
        const r = http.get(res.headers.location, (r2) => {
          if (r2.statusCode !== 200) { reject(new Error('HTTP ' + r2.statusCode)); return; }
          const out = fs.createWriteStream(target);
          r2.pipe(out);
          out.on('finish', () => resolve({ success: true, path: target }));
          out.on('error', (e) => reject(e));
        });
        r.on('error', reject);
        return;
      }
      if (res.statusCode !== 200) { reject(new Error('HTTP ' + res.statusCode)); return; }
      const out = fs.createWriteStream(target);
      res.pipe(out);
      out.on('finish', () => resolve({ success: true, path: target }));
      out.on('error', (e) => reject(e));
    });
    req.on('error', reject);
  });

  try {
    const result = await doGet();
    return result;
  } catch (err) {
    // Best-effort cleanup of partial file.
    try { fs.unlinkSync(target); } catch { /* ignore */ }
    return { success: false, error: err && err.message ? err.message : String(err) };
  }
});

// ── Download-to-Downloads helper: stream an HTTP URL to a fixed file under
//    ~/Downloads and reveal it in the file manager. Shared by the
//    paper-download IPC and the webview new-window handler (below). Avoids
//    dialog.showSaveDialog which on some Windows builds (e.g. unactivated
//    Win10) silently fails to anchor the dialog. ──
async function _streamToDownloads(url, filename) {
  const safeName = String(filename || 'download').replace(/[\\/:*?"<>|]/g, '_');
  const downloadsDir = app.getPath('downloads');
  let target;
  try { fs.mkdirSync(downloadsDir, { recursive: true }); target = path.join(downloadsDir, safeName); }
  catch (err) { return { success: false, error: 'mkdir failed: ' + (err && err.message) }; }

  log(`[stream-to-downloads] url=${url} target=${target}`);
  try {
    await new Promise((resolve, reject) => {
      const req = http.get(url, (res) => {
        if (res.statusCode !== 200) { reject(new Error('HTTP ' + res.statusCode)); return; }
        const out = fs.createWriteStream(target);
        res.pipe(out);
        out.on('finish', resolve);
        out.on('error', reject);
      });
      req.on('error', reject);
    });
  } catch (err) {
    try { fs.unlinkSync(target); } catch { /* ignore */ }
    return { success: false, error: err && err.message ? err.message : String(err) };
  }

  // Reveal in file manager. shell.showItemInFolder opens Explorer/Finder with
  // the file highlighted — user gets unmistakable visual feedback even when
  // save dialogs misbehave.
  try { shell.showItemInFolder(target); } catch (err) {
    log(`[stream-to-downloads] showItemInFolder failed: ${err && err.message}`);
  }
  return { success: true, path: target };
}

// ── paper-download IPC: stream a paper PDF to ~/Downloads and reveal it. ──
ipcMain.handle('paper-download', async (_event, { runId, url } = {}) => {
  if (!runId || !url) return { success: false, error: 'missing runId/url' };
  return _streamToDownloads(url, String(runId) + '.pdf');
});

// ── Embedded <webview> new-window routing ──
// Electron <webview> silently drops target=_blank / window.open unless a
// window-open handler is attached. Attach one to every webview guest so:
//   • the paper dashboard's PDF link (Content-Disposition: attachment) becomes
//     a real disk download + reveal (doesn't rely on the system browser, which
//     can silently fail for localhost URLs);
//   • every other http/https link opens in the system browser.
// We return deny so nothing opens inside the app (no stray BrowserWindow).
// Register on web-contents-created because webview guests cannot be reached
// from the main window's own webContents.
function _paperPdfRunIdFromUrl(url) {
  const m = /^https?:\/\/(?:127\.0\.0\.1|localhost):8765\/api\/runs\/([^/?]+)\/pdf/i.exec(String(url || ''));
  return m ? decodeURIComponent(m[1]) : null;
}
app.on('web-contents-created', (_event, contents) => {
  if (contents.getType() !== 'webview') return;
  contents.setWindowOpenHandler(({ url }) => {
    const pdfRunId = _paperPdfRunIdFromUrl(url);
    if (pdfRunId) {
      log(`[webview-new-window] paper pdf run=${pdfRunId} url=${url}`);
      _streamToDownloads(url, `${pdfRunId}.pdf`).catch((err) =>
        log(`[webview-new-window] paper pdf download failed: ${err && err.message}`));
      return { action: 'deny' };
    }
    if (/^https?:/i.test(String(url || ''))) {
      log(`[webview-new-window] openExternal url=${url}`);
      shell.openExternal(url).catch((err) =>
        log(`[webview-new-window] openExternal failed: ${err && err.message}`));
    }
    return { action: 'deny' };
  });
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
