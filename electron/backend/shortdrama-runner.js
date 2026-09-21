// Spawns the vendored shortdrama FastAPI shim so the launcher can open its
// React SPA in an in-app browser tab.
//
// Layout: <app>/shortdrama/{v5, frontend/dist, .venv}. The shim serves the
// built SPA same-origin via --web-root, so the frontend's root-relative /v1
// and /media calls need no base URL and no CORS.
//
// Unlike paper-rewriter-dashboard-runner, this one is LAZY: nothing is spawned
// at app start. The 创作链 additionally needs a `langgraph dev` server, which
// the shim manages itself (v5/webchain.py ensure_devserver) — so the only
// process we own is the shim.
//
// Two ports are picked per launch instead of being hardcoded:
//   * the shim port (was 8787) — so a packaged copy never fights a dev
//     checkout for the same listener;
//   * the dev-server port (SHORTDRAMA_DEV_PORT, was the literal 2024) — must be
//     published to the shim together with SHORTDRAMA_V5_AGENT_URL, because
//     webchain binds the two together and a mismatch makes every subagent
//     dispatch fail silently (AGENTS.md 硬性注意事项 #5).
//
// stop() uses `taskkill /T`: the langgraph dev child spawns its own uvicorn
// grandchild, and killing only the parent leaves that grandchild holding the
// dev port (the failure recorded in the source repo's .tmp_port_owner.txt).

const { spawn, exec } = require('child_process');
const http = require('http');
const net = require('net');
const path = require('path');
const fs = require('fs');
const { log, logDir } = require('./logger');
const { unpacked } = require('./app-paths');

// unpacked(): asar 打包后 shortdrama/（含 .venv 的 python.exe）与 bin/ 必须从
// 真实磁盘目录 spawn，不能走 asar 虚拟路径（见 app-paths.js）
const SHORTDRAMA_DIR = unpacked(path.join(__dirname, '..', '..', 'shortdrama'));
const VENV_PYTHON = path.join(SHORTDRAMA_DIR, '.venv', 'Scripts', 'python.exe');
const SPA_ROOT = path.join(SHORTDRAMA_DIR, 'frontend', 'dist');
const BIN_DIR = unpacked(path.join(__dirname, '..', '..', 'bin'));

const HOST = '127.0.0.1';
const SHIM_PORT_PREFERRED = 8787;
const DEV_PORT_PREFERRED = 2024;
const MAX_PORT_PROBES = 40;
const MAX_WAIT_MS = 90000;
const POLL_MS = 500;

function listenFree(port) {
  return new Promise((resolve) => {
    const srv = net.createServer();
    srv.once('error', () => resolve(false));
    srv.once('listening', () => srv.close(() => resolve(true)));
    srv.listen(port, HOST);
  });
}

async function findFreePort(preferred) {
  for (let i = 0; i < MAX_PORT_PROBES; i += 1) {
    const port = preferred + i;
    if (await listenFree(port)) return port;
  }
  return preferred;
}

class ShortdramaRunner {
  constructor({ app }) {
    this.app = app;
    this.process = null;
    this.port = null;
    this.devPort = null;
    this._exitCode = null;
    this._startingPromise = null;
    this.hermesHome = app ? app.getPath('userData') : '';
  }

  getUrl() {
    return this.port ? `http://${HOST}:${this.port}` : '';
  }

  _readEnvKeys() {
    // shortdrama's config.py only falls back to PROJECT_ROOT/.env for keys it
    // cannot find in os.environ, and the shipped tree deliberately has no
    // .env — so the credentials come from Hermes' own env file here.
    const out = {};
    try {
      const text = fs.readFileSync(path.join(this.hermesHome, '.env'), 'utf-8');
      for (const line of text.split(/\r?\n/)) {
        const m = /^([A-Z][A-Z0-9_]*)=(.*)$/.exec(line.trim());
        if (m && /^AGNES_API_KEY/.test(m[1]) && m[2].trim()) out[m[1]] = m[2].trim();
      }
    } catch (_) {
      /* no .env yet — the shim still boots, generation just won't work */
    }
    return out;
  }

  // 出厂设置（`shortdrama/settings.env`，只含非密钥项）。
  //
  // 为什么必须有这一层：密钥走 Hermes 的 .env，所以**包内没有 `shortdrama/.env`**，
  // 而 `v5/config.py` 会去读它 —— 读不到就静默回落到代码默认值。实测差异不是
  // "偏好"而是**产物错误**：静帧回落到 3:4（视频 9:16）⇒ 画幅不一致；
  // `AGNES_VIDEO_MAX_SHOTS` 回落到 20 ⇒ 超过 20 镜的项目被截断。
  //
  // 合并顺序上放在 `...process.env` **之前** ⇒ 真实环境变量优先，临时覆盖不用改文件。
  _readSettingsEnv() {
    const out = {};
    try {
      const text = fs.readFileSync(path.join(SHORTDRAMA_DIR, 'settings.env'), 'utf-8');
      for (const line of text.split(/\r?\n/)) {
        const t = line.trim();
        if (!t || t.startsWith('#')) continue;
        const i = t.indexOf('=');
        if (i <= 0) continue;
        const k = t.slice(0, i).trim();
        const v = t.slice(i + 1).trim().replace(/^["'](.*)["']$/, '$1');
        if (/^[A-Z][A-Z0-9_]*$/.test(k) && v !== '') out[k] = v;
      }
    } catch (_) { /* 文件缺失 = 用后端默认值 */ }
    return out;
  }

  async _waitForReady() {
    const start = Date.now();
    for (;;) {
      if (this._exitCode !== null) {
        throw new Error(`shortdrama shim exited early (code ${this._exitCode})`);
      }
      if (!this.process) throw new Error('shortdrama process gone before ready');
      const ok = await new Promise((resolve) => {
        const req = http.get(`http://${HOST}:${this.port}/health`, { timeout: 2000 }, (res) => {
          resolve(res.statusCode === 200);
          res.resume();
        });
        req.on('error', () => resolve(false));
        req.on('timeout', () => { req.destroy(); resolve(false); });
      });
      if (ok) return;
      if (Date.now() - start > MAX_WAIT_MS) {
        throw new Error(`shortdrama shim not ready within ${MAX_WAIT_MS}ms`);
      }
      await new Promise((r) => setTimeout(r, POLL_MS));
    }
  }

  // Idempotent, and safe to await from concurrent clicks.
  async start() {
    if (this._startingPromise) return this._startingPromise;
    this._startingPromise = this._doStart().then(() => this.getUrl());
    try {
      return await this._startingPromise;
    } finally {
      this._startingPromise = null;
    }
  }

  async _doStart() {
    if (this.process) return;
    if (!fs.existsSync(VENV_PYTHON)) {
      throw new Error(`shortdrama venv not found: ${VENV_PYTHON}\n` +
        '先在 abcyesno 根目录跑 node scripts/setup-shortdrama-venv.mjs');
    }
    if (!fs.existsSync(SPA_ROOT)) {
      throw new Error(`shortdrama SPA build not found: ${SPA_ROOT}`);
    }

    this.port = await findFreePort(SHIM_PORT_PREFERRED);
    this.devPort = await findFreePort(DEV_PORT_PREFERRED);
    this._exitCode = null;

    const projectsDir = path.join(this.hermesHome, 'shortdrama_projects');
    const runtimeDir = path.join(this.hermesHome, 'shortdrama_runtime');
    try {
      fs.mkdirSync(projectsDir, { recursive: true });
      fs.mkdirSync(runtimeDir, { recursive: true });
    } catch (_) {}

    const env = {
      ...this._readSettingsEnv(),
      ...process.env,
      ...this._readEnvKeys(),
      // Project artifacts must not live inside the app tree (that is 5 GB of
      // media on the dev box, and unwritable under Program Files elsewhere).
      SHORTDRAMA_PROJECTS: projectsDir,
      // Same for mutable runtime state: the `langgraph dev` checkpoint store
      // (which lands in that server's cwd), its pid/port state file, logs and
      // the deleted-project trash. With these two set, the install tree is
      // read-only and the app can live under Program Files.
      SHORTDRAMA_RUNTIME: runtimeDir,
      SHORTDRAMA_DEV_PORT: String(this.devPort),
      SHORTDRAMA_V5_AGENT_URL: `http://127.0.0.1:${this.devPort}`,
      PYTHONIOENCODING: 'utf-8',
      // zh-CN Windows defaults `open()` to cp936, which crashes langgraph dev
      // on its own UTF-8 openapi resource. webchain re-sets it for the child.
      PYTHONUTF8: '1',
      // The local proxy is a single point of failure for loopback + Agnes
      // (see shortdrama/AGENTS.md); the dev child gets the same treatment in
      // v5/webchain.py.
      NO_PROXY: '127.0.0.1,localhost,agnes-ai.com,agnes-ai.space',
      no_proxy: '127.0.0.1,localhost,agnes-ai.com,agnes-ai.space',
    };
    // 媒体链 shells out to ffmpeg/ffprobe by name.
    env.PATH = [BIN_DIR, env.PATH || env.Path].filter(Boolean).join(path.delimiter);

    let logStream;
    try { logStream = fs.createWriteStream(path.join(logDir, 'shortdrama.log'), { flags: 'a' }); } catch (_) {}

    const argv = ['-m', 'v5.server', '--host', HOST, '--port', String(this.port),
      '--web-root', SPA_ROOT];
    log('shortdrama', `spawning python -m v5.server port=${this.port} devPort=${this.devPort}`);

    this.process = spawn(VENV_PYTHON, argv, {
      cwd: SHORTDRAMA_DIR,
      env,
      windowsHide: true,
      detached: false,
    });

    this.process.stdout.on('data', (d) => {
      const text = d.toString();
      log('shortdrama-stdout', text.trim());
      if (logStream) logStream.write(text);
    });
    this.process.stderr.on('data', (d) => {
      const text = d.toString();
      log('shortdrama-stderr', text.trim());
      if (logStream) logStream.write(text);
    });
    this.process.on('exit', (code) => {
      log('shortdrama', `process exited with code ${code}`);
      this._exitCode = code;
      if (logStream) { try { logStream.end(); } catch (_) {} }
      this.process = null;
    });
    this.process.on('error', (err) => {
      log('shortdrama', `process error: ${err.message}`);
    });

    await this._waitForReady();
    log('shortdrama', `ready at http://${HOST}:${this.port}/`);
  }

  // Sweep whatever still listens on `port`. Killing only the shim's process
  // tree is not enough: `langgraph.exe` hands the listening uvicorn to a child
  // that is no longer reachable via `taskkill /T` once the shim is gone, so the
  // dev port stays occupied and a ~200 MB python process survives the app.
  _killPort(port) {
    if (!port || process.platform !== 'win32') return;
    exec(`netstat -ano -p tcp`, (err, out) => {
      if (err) return;
      const pids = new Set();
      for (const line of out.split(/\r?\n/)) {
        if (!line.includes(`LISTENING`) || !line.includes(`:${port} `)) continue;
        const m = /(\d+)\s*$/.exec(line.trim());
        if (m && m[1] !== '0') pids.add(m[1]);
      }
      for (const pid of pids) {
        log('shortdrama', `sweeping leftover listener on :${port} (pid ${pid})`);
        try {
          const killer = spawn('taskkill', ['/pid', pid, '/t', '/f'],
            { windowsHide: true, detached: true });
          killer.on('error', () => {});
        } catch (_) {}
      }
    });
  }

  stop() {
    const devPort = this.devPort;
    // Sweep first: `before-quit` never awaits us, so work scheduled after the
    // shim exits may not get a chance to run.
    this._killPort(devPort);
    if (!this.process) return Promise.resolve();
    const proc = this.process;
    log('shortdrama', `stopping pid=${proc.pid}`);
    this.process = null;
    return new Promise((resolve) => {
      const cleanup = () => {
        try { proc.removeAllListeners(); } catch (_) {}
        this._killPort(devPort);
        resolve();
      };
      proc.once('exit', cleanup);
      try { proc.kill('SIGTERM'); } catch (_) {}
      if (process.platform === 'win32') {
        // /T is required: langgraph dev leaves a uvicorn grandchild on the dev
        // port otherwise.
        try {
          const killer = spawn('taskkill', ['/pid', String(proc.pid), '/t', '/f'],
            { windowsHide: true, detached: true });
          killer.on('error', () => {});
        } catch (_) {}
      }
      setTimeout(cleanup, 2500);
    });
  }
}

module.exports = { ShortdramaRunner };
