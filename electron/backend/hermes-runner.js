const { spawn } = require('child_process');
const http = require('http');
const net = require('net');
const path = require('path');
const fs = require('fs');
const crypto = require('crypto');
const { log, logDir } = require('./logger');

const HERMES_FORK = path.join(__dirname, '..', '..', 'hermes-fork');
const HERMES_VENV = path.join(HERMES_FORK, '.venv');
const HERMES_PYTHON = path.join(HERMES_VENV, 'Scripts', 'python.exe');
const HERMES_EXE = path.join(HERMES_VENV, 'Scripts', 'hermes.exe');
const PORT = 9120;
const HOST = '127.0.0.1';
// Cold first launch of a freshly-unpacked build triggers a full Windows
// Defender real-time scan of the bundled Python venv (~70k .py/.dll/.pyc
// files) + an Agnes connection-retry window, which can push backend startup
// past 110s. 120s was too tight and caused a false "backend 有问题" failure.
// Bump to 300s and, more importantly, bail early only on a real crash (the
// process exited), not on a slow-but-alive startup.
const MAX_WAIT_MS = 300000;
const POLL_MS = 600;

class HermesRunner {
  constructor({ app }) {
    this.app = app;
    this.process = null;
    this.port = PORT;
    this.host = HOST;
    this.sessionToken = crypto.randomBytes(32).toString('base64url');
    this.hermesHome = '';
    this.apiKey = '';
    this._exitCode = null; // set by the 'exit' handler; signals a crash during startup
    this._computePaths();
  }

  _computePaths() {
    // Always use Electron's userData directory as HERMES_HOME. main.js sets
    // userData to %USERPROFILE%/.hermes_portable_data, so sessions, config,
    // API keys and skills persist across portable launches (including the
    // single-file extractor, which unpacks to a temporary directory).
    const home = this.app.getPath('userData');
    this.hermesHome = home;
    // Surface HERMES_HOME to this (backend) process too, so sibling services
    // like agui-server resolve the same data dir as the Hermes child when
    // writing the human-in-the-loop control-file channel.
    process.env.HERMES_HOME = home;
    try {
      fs.mkdirSync(home, { recursive: true });
    } catch (_) {}
    this._ensureConfig();
  }

  _envFile() {
    return path.join(this.hermesHome, '.env');
  }

  _configFile() {
    return path.join(this.hermesHome, 'config.yaml');
  }

  _ensureConfig() {
    const configFile = this._configFile();
    const defaultConfig = path.join(__dirname, 'default-config.yaml');
    try {
      if (!fs.existsSync(configFile)) {
        fs.copyFileSync(defaultConfig, configFile);
        log('hermes-runner', `wrote default config to ${configFile}`);
      }
    } catch (err) {
      log('hermes-runner', `failed to copy default config: ${err.message}`);
    }
  }

  _syncBuiltinSkills() {
    // Hermes resolves skills under HERMES_HOME/skills by their canonical
    // skill id (kebab-case). The bundled sources live under hermes-fork/skills
    // using snake_case directory names, so map source dir -> target skill id.
    const mappings = [
      { source: 'langgraph_agents', target: 'langgraph-agents' },
      { source: 'browser_pw', target: 'browser-pw' },
    ];
    for (const { source, target } of mappings) {
      const srcDir = path.join(HERMES_FORK, 'skills', source);
      const destDir = path.join(this.hermesHome, 'skills', target);
      if (!fs.existsSync(srcDir)) {
        log('hermes-runner', `builtin skill source not found: ${srcDir}`);
        continue;
      }
      try {
        fs.mkdirSync(path.dirname(destDir), { recursive: true });
        const copied = this._copyDirRecursive(srcDir, destDir);
        log('hermes-runner', `synced builtin skill ${target} (${copied} file(s) updated)`);
      } catch (err) {
        log('hermes-runner', `failed to sync builtin skill ${target}: ${err.message}`);
      }
    }
  }

  _copyDirRecursive(src, dest) {
    fs.mkdirSync(dest, { recursive: true });
    let updated = 0;
    for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
      const srcPath = path.join(src, entry.name);
      const destPath = path.join(dest, entry.name);
      if (entry.isDirectory()) {
        updated += this._copyDirRecursive(srcPath, destPath);
      } else {
        // Incremental sync: skip files whose destination copy is already
        // up-to-date (source mtime <= dest mtime). Avoids re-copying the
        // entire skill tree on every cold start.
        try {
          const dstStat = fs.statSync(destPath);
          const srcStat = fs.statSync(srcPath);
          if (dstStat.mtimeMs >= srcStat.mtimeMs) continue;
        } catch (_) {
          // Destination missing → always copy.
        }
        fs.copyFileSync(srcPath, destPath);
        updated++;
      }
    }
    return updated;
  }

  // ── Portable Python: strip machine-specific absolute paths ──────────────
  // The venv was built on the dev machine, so two things are baked with
  // absolute paths that DON'T exist on a fresh target machine:
  //   1. pyvenv.cfg `home` / `executable` → the dev Python
  //      (C:\Users\Administrator\.workbuddy\...). The venv launcher can't
  //      find its base interpreter → Hermes never starts (port 9120 error).
  //   2. hermes_agent is `pip install -e`'d, so the generated
  //      __editable__*_finder.py hardcodes the dev source dir. Imports fail.
  // Both are rewritten at first run to point at (a) the portable Python we
  // bundle inside the app and (b) the app's actual hermes-fork location.
  // Idempotent + safe to run on every cold start.
  _resolveBasePython() {
    const candidates = [
      // 1) Portable Python shipped with the app (resources/runtime/python).
      path.join(process.resourcesPath || '', 'runtime', 'python', 'python.exe'),
      // 2) The base Python the venv currently records (works on the build
      //    machine / any machine that happens to have that path).
      (() => {
        try {
          const cfg = fs.readFileSync(path.join(HERMES_VENV, 'pyvenv.cfg'), 'utf-8');
          const m = cfg.match(/^\s*home\s*=\s*(.+?)\s*$/m);
          const home = m ? m[1].trim() : '';
          const exe = path.join(home, 'python.exe');
          return fs.existsSync(exe) ? exe : '';
        } catch (_) {
          return '';
        }
      })(),
      // 3) System Python on PATH (last resort).
      'python.exe',
    ];
    for (const c of candidates) {
      if (c && fs.existsSync(c)) return c;
    }
    return '';
  }

  _longestCommonPrefix(strs) {
    if (!strs.length) return '';
    let prefix = strs[0];
    for (const s of strs) {
      let i = 0;
      const max = Math.min(prefix.length, s.length);
      while (i < max && prefix[i] === s[i]) i++;
      prefix = prefix.slice(0, i);
      if (!prefix) break;
    }
    // Trim trailing separators so the prefix is a clean directory name and the
    // remaining suffix keeps its own leading separator (avoids a missing or
    // doubled separator in the replacement result).
    while (prefix.length && /[\\/]/.test(prefix[prefix.length - 1])) prefix = prefix.slice(0, -1);
    return prefix;
  }

  _portablizeVenv() {
    // (1) pyvenv.cfg → point at the resolved base Python.
    const baseExe = this._resolveBasePython();
    const cfgPath = path.join(HERMES_VENV, 'pyvenv.cfg');
    if (baseExe && fs.existsSync(cfgPath)) {
      try {
        const baseDir = path.dirname(baseExe);
        let txt = fs.readFileSync(cfgPath, 'utf-8');
        const wantHome = `home = ${baseDir}`;
        const wantExe = `executable = ${baseExe}`;
        let changed = false;
        txt = txt.replace(/^home\s*=\s*.+$/m, (line) => {
          if (line.trim() !== wantHome) { changed = true; return wantHome; }
          return line;
        });
        txt = txt.replace(/^executable\s*=\s*.+$/m, (line) => {
          if (line.trim() !== wantExe) { changed = true; return wantExe; }
          return line;
        });
        if (changed) {
          fs.writeFileSync(cfgPath, txt, 'utf-8');
          log('hermes-runner', `portablized venv: home -> ${baseDir}`);
        }
      } catch (err) {
        log('hermes-runner', `failed to rewrite pyvenv.cfg: ${err.message}`);
      }
    }

    // (2) editable-install finder → rewrite absolute source prefix to HERMES_FORK.
    const sp = path.join(HERMES_VENV, 'Lib', 'site-packages');
    if (!fs.existsSync(sp)) return;
    const finderFiles = fs.readdirSync(sp).filter((f) => f.startsWith('__editable__') && f.endsWith('_finder.py'));
    // The finder .py stores paths with escaped backslashes (Python string
    // literals), whereas HERMES_FORK (from path.join) uses single backslashes.
    // Match the escaped form so the replacement keeps a consistent separator
    // style in the generated file.
    const HERMES_FORK_ESCAPED = HERMES_FORK.replace(/\\/g, '\\\\');
    for (const f of finderFiles) {
      const fp = path.join(sp, f);
      try {
        let txt = fs.readFileSync(fp, 'utf-8');
        const paths = [...txt.matchAll(/'([^']+)'/g)].map((m) => m[1]).filter((p) => /^[A-Za-z]:[\\/]/.test(p));
        if (!paths.length) continue;
        const prefix = this._longestCommonPrefix(paths);
        // Only rewrite when the shared root is our hermes-fork source tree — a
        // stray path on another drive would yield a tiny prefix we must not use.
        if (!prefix || !/hermes-fork$/i.test(prefix) || prefix === HERMES_FORK_ESCAPED) continue;
        if (txt.includes(prefix)) {
          txt = txt.split(prefix).join(HERMES_FORK_ESCAPED);
          fs.writeFileSync(fp, txt, 'utf-8');
          log('hermes-runner', `portablized editable finder ${f}: ${prefix} -> ${HERMES_FORK_ESCAPED}`);
        }
      } catch (err) {
        log('hermes-runner', `failed to rewrite ${f}: ${err.message}`);
      }
    }
  }

  _updateConfigApiKey(key) {
    const configFile = this._configFile();
    let text = '';
    try {
      text = fs.readFileSync(configFile, 'utf-8');
    } catch (_) {
      return;
    }
    // Update all api_key fields (default config only contains Agnes provider fields).
    const updated = text.replace(/^(\s*)api_key:\s*[^\n]*$/gm, `$1api_key: ${key}`);
    if (updated !== text) {
      fs.writeFileSync(configFile, updated, 'utf-8');
    }
  }

  getApiKeyStatus() {
    try {
      const text = fs.readFileSync(this._envFile(), 'utf-8');
      const m = text.match(/^AGNES_API_KEY=(.+)$/m);
      return !!(m && m[1].trim());
    } catch (_) {
      return false;
    }
  }

  _readFallbackKey() {
    try {
      const text = fs.readFileSync(this._envFile(), 'utf-8');
      const m = text.match(/^AGNES_FALLBACK_API_KEY=(.+)$/m);
      return m ? m[1].trim() : '';
    } catch (_) {
      return '';
    }
  }

  setApiKey(key) {
    this.apiKey = (key || '').trim();
    const file = this._envFile();
    let text = '';
    try {
      text = fs.readFileSync(file, 'utf-8');
    } catch (_) {}
    const lines = text.split(/\r?\n/).filter((l) => !l.startsWith('AGNES_API_KEY='));
    if (this.apiKey) {
      lines.push(`AGNES_API_KEY=${this.apiKey}`);
    }
    fs.writeFileSync(file, lines.join('\n') + '\n', 'utf-8');

    // Keep Hermes config.yaml in sync so the Agnes provider uses the new key.
    this._ensureConfig();
    if (this.apiKey) {
      this._updateConfigApiKey(this.apiKey);
    }
  }

  getSessionToken() {
    return this.sessionToken;
  }

  getPort() {
    return this.port;
  }

  getHermesHome() {
    return this.hermesHome;
  }

  _statusUrl() {
    return `http://${this.host}:${this.port}/api/status`;
  }

  async _portAvailable(port) {
    return new Promise((resolve) => {
      // Fast TCP check first: if we can bind, the port is free.
      const tcp = net.createServer();
      tcp.once('error', (err) => {
        if (err.code === 'EADDRINUSE') {
          resolve(false);
        } else {
          // Fall back to HTTP probe on unexpected errors.
          const req = http.get(`http://${this.host}:${port}/api/status`, { timeout: 800 }, (res) => {
            res.resume();
            resolve(false); // someone is already listening
          });
          req.on('error', () => resolve(true));
          req.on('timeout', () => { req.destroy(); resolve(true); });
        }
      });
      tcp.once('listening', () => {
        const { port: boundPort } = tcp.address();
        tcp.close(() => resolve(true));
      });
      tcp.listen(port, this.host);
    });
  }

  async _findAvailablePort() {
    for (let offset = 0; offset < 20; offset++) {
      const port = PORT + offset;
      if (await this._portAvailable(port)) return port;
    }
    return PORT;
  }

  async _waitForReady() {
    const start = Date.now();
    // Wait as long as the backend process is ALIVE. A freshly-unpacked build
    // can take 100s+ on the first launch while Windows Defender scans the
    // 70k-file venv; that is NOT a failure — we must keep polling until the
    // process actually answers /api/status. Only a genuine crash (process
    // exited) is a real error and is surfaced immediately.
    // SAFETY_MS is an absolute ceiling that only catches a true hang where the
    // process is alive but never becomes ready (e.g. deadlock) — it should
    // essentially never be hit in normal operation.
    const SAFETY_MS = 600000;
    while (true) {
      // A crashed backend is a real failure — surface it immediately.
      if (this._exitCode !== null) {
        throw new Error(`Hermes backend process exited early (code ${this._exitCode}) before becoming ready`);
      }
      if (this.process === null) {
        throw new Error('Hermes backend process is no longer running before becoming ready');
      }
      const ok = await new Promise((resolve) => {
        const req = http.get(this._statusUrl(), { timeout: 2500 }, (res) => {
          resolve(res.statusCode === 200);
          res.resume();
        });
        req.on('error', () => resolve(false));
        req.on('timeout', () => { req.destroy(); resolve(false); });
      });
      if (ok) return;
      if (Date.now() - start > SAFETY_MS) {
        throw new Error(`Hermes backend did not become ready on port ${this.port} within ${SAFETY_MS}ms (process still alive — possible deadlock)`);
      }
      await new Promise((r) => setTimeout(r, POLL_MS));
    }
  }

  async start() {
    if (this._startingPromise) return this._startingPromise;
    this._startingPromise = this._doStart();
    try {
      await this._startingPromise;
    } finally {
      this._startingPromise = null;
    }
  }

  async _doStart() {
    if (this.process) return;
    // Neutralize machine-specific absolute paths in the venv + editable
    // install BEFORE we try to launch Python (new-machine portability).
    this._portablizeVenv();
    this.port = await this._findAvailablePort();
    if (this.port !== PORT) {
      log('hermes-runner', `port ${PORT} busy, using ${this.port}`);
    }
    // Portable builds rely on the bundled venv Python. If it is missing, the
    // most common cause on a fresh Windows machine is antivirus quarantine
    // (Windows Defender / 360 / Huorong) deleting python.exe right after
    // install. Report that explicitly so the user does not chase hermes.exe.
    log('hermes-runner', `runtime check: python=${fs.existsSync(HERMES_PYTHON)} hermes=${fs.existsSync(HERMES_EXE)} venv=${HERMES_VENV}`);
    if (!fs.existsSync(HERMES_PYTHON)) {
      throw new Error(
        `Hermes Python runtime not found: ${HERMES_PYTHON}\n\n` +
          `The bundled Python interpreter is missing. This usually means an antivirus program ` +
          `(Windows Defender / 360 / Huorong) quarantined or deleted it during/after installation.\n\n` +
          `To fix:\n` +
          `1. Check your antivirus quarantine / protection history.\n` +
          `2. Restore python.exe and add this folder to the whitelist:\n` +
          `   ${path.join(HERMES_VENV, 'Scripts')}\n` +
          `3. Re-run Abcyesno. If the file is still missing, reinstall from the installer/ZIP.`
      );
    }
    const hermesCommand = {
      exe: HERMES_PYTHON,
      args: ['-m', 'hermes_cli.main', 'serve', '--port', String(this.port), '--host', this.host, '--skip-build'],
    };

    // Refresh API key from disk in case it was set before start.
    this._syncBuiltinSkills();

    this.apiKey = '';
    if (this.getApiKeyStatus()) {
      try {
        const text = fs.readFileSync(this._envFile(), 'utf-8');
        const m = text.match(/^AGNES_API_KEY=(.+)$/m);
        this.apiKey = m ? m[1].trim() : '';
      } catch (_) {}
    }

    const env = {
      ...process.env,
      HERMES_HOME: this.hermesHome,
      HERMES_DASHBOARD_SESSION_TOKEN: this.sessionToken,
      AGNES_API_KEY: this.apiKey || process.env.AGNES_API_KEY || '',
      AGNES_BASE_URL: process.env.AGNES_BASE_URL || 'https://apihub.agnes-ai.com/v1',
      // Public/default fallback key for video generation: used when the Token
      // Plan daily video-second quota (500s) is exhausted. Unlimited seconds
      // but 1 RPM, so the agent serializes fallback video calls.
      AGNES_FALLBACK_API_KEY: process.env.AGNES_FALLBACK_API_KEY || this._readFallbackKey() || '',
      // Port the agui-server bridge listens on. The Python langgraph runtime
      // POSTs HITL workflow events here so they can reach the frontend SSE.
      AGUI_PORT: process.env.AGUI_PORT || '9121',
      // Cap tool-calling iterations so a confused model doesn't spin forever.
      // 15 turns is too low for deep research / multi-step tasks; raise to 100
      // so the agent can finish long reports without pausing for "好了吗" prompts.
      HERMES_TUI_MAX_TURNS: process.env.HERMES_TUI_MAX_TURNS || '100',
      // 2026-08-29「怎么老是未收到模型输出」：gateway 默认 busy_input_mode=
      // interrupt——同一会话任何新消息（包括 goal 续跑的续篇）都会打断正在
      // 跑的 turn，留下「未收到模型输出」空占位气泡。桌面端 composer 本身
      // 就承诺「Enter 排队」，切到 queue 模式：忙时消息走 FIFO 按到达顺序
      // 逐条执行，绝不打断；/stop 仍走显式中断路径不受影响。可用 env 覆盖。
      HERMES_GATEWAY_BUSY_INPUT_MODE: process.env.HERMES_GATEWAY_BUSY_INPUT_MODE || 'queue',
      // 2026-08-28 "Chaos 卡住": video-generation batches legitimately run
      // >10 min; the terminal tool's 600s foreground cap killed them
      // mid-flight twice (LN01-05 / LN05-09) and the tool then returned a
      // timeout error the UI never saw. Raise to 1h; still overridable.
      TERMINAL_MAX_FOREGROUND_TIMEOUT: process.env.TERMINAL_MAX_FOREGROUND_TIMEOUT || '3600',
      // Enable the Hermes toolsets. `hermes-cli` carries langgraph_agent and
      // shell/file tools; `browser-pw` adds the 7 pw_browser_* native-driver tools
      // (Path B browser automation). Override via HERMES_TUI_TOOLSETS to roll back.
      HERMES_TUI_TOOLSETS: process.env.HERMES_TUI_TOOLSETS || 'hermes-cli,browser-pw',
      // Endpoint of the Electron-native browser driver service (main process,
      // 127.0.0.1:18923). pw_browser_tool.py POSTs navigate/snapshot/click/...
      // here to drive the visible in-app <webview>. Set by the main process; we
      // forward it verbatim into the backend env.
      PW_BROWSER_DRIVER_URL: process.env.PW_BROWSER_DRIVER_URL || 'http://127.0.0.1:18923',
      // Point Playwright at the Chromium build bundled with the portable app.
      // In a packaged build process.resourcesPath -> release/win-unpacked/resources,
      // so browsers live in resources/playwright-browsers (copied via extraResources).
      // In dev (no such dir) this path is harmless: _chromium_installed() falls back
      // to the default cache and ~/.hermes_portable_data/playwright-browsers.
      PLAYWRIGHT_BROWSERS_PATH: process.env.PLAYWRIGHT_BROWSERS_PATH || path.join(process.resourcesPath || '', 'playwright-browsers'),
      // Preload skills for the agent. `langgraph-agents` carries the
      // manjucraft_agent workflow; `browser-pw` injects the strong "use
      // pw_browser_* for web tasks, NOT computer_use" guidance into the system
      // prompt.
      HERMES_TUI_SKILLS: process.env.HERMES_TUI_SKILLS || 'langgraph-agents,browser-pw',
      PYTHONPATH: HERMES_FORK + (process.env.PYTHONPATH ? path.delimiter + process.env.PYTHONPATH : ''),
      // Make the bundled venv Python reachable from terminal/file tools.
      // Windows has NO global `python`/`python3` (the App Execution Alias
      // errors "Python was not found" when the agent runs `python3 ...`), so
      // prepending the venv Scripts/bin dir lets the agent execute `python` /
      // `pip` directly instead of guessing a path. The venv is hermetic —
      // shadowing the system PATH here only affects tools the agent spawns.
      PATH:
        path.join(HERMES_FORK, '.venv', process.platform === 'win32' ? 'Scripts' : 'bin') +
        path.delimiter +
        (process.env.PATH || ''),
    };

    // Network proxy — configurable, defaults to DIRECT (no proxy).
    //
    // On some networks apihub.agnes-ai.com is DNS-poisoned (a direct lookup returns
    // a fake IP, e.g. 2001::/31.13.x), so the connection times out. A proxy that
    // resolves DNS through its own tunnel reaches the real server. This is a network
    // property, NOT an Agnes requirement — on a clean network the call works direct.
    //
    // Proxy is resolved in order of precedence:
    //   1. HTTPS_PROXY / HTTP_PROXY in the launching environment (highest priority)
    //   2. `network.proxy_url` in HERMES_HOME/config.yaml
    // If neither is set, we go DIRECT: any inherited proxy env is stripped so Hermes
    // connects straight out. To force direct on a clean network, leave both unset or
    // remove `network.proxy_url` from config.yaml.
    const proxyEnv =
      process.env.HTTPS_PROXY ||
      process.env.HTTP_PROXY ||
      process.env.https_proxy ||
      process.env.http_proxy;
    let proxyFromConfig = '';
    try {
      const cfgText = fs.readFileSync(path.join(this.hermesHome, 'config.yaml'), 'utf-8');
      const m = cfgText.match(/^\s*proxy_url:\s*(.+?)\s*$/m);
      if (m) {
        const v = m[1].trim().replace(/^["']|["']$/g, '');
        // Empty / "direct" / unset → no proxy. Only a real http(s):// URL is used.
        if (v && v !== 'direct' && /^https?:\/\//i.test(v)) proxyFromConfig = v;
      }
    } catch (_) {}
    const proxyUrl = proxyEnv || proxyFromConfig;
    if (proxyUrl) {
      env.HTTPS_PROXY = proxyUrl;
      env.HTTP_PROXY = proxyUrl;
      if (!env.NO_PROXY && !env.no_proxy) {
        env.NO_PROXY = 'localhost,127.0.0.1,::1';
      }
    } else {
      // Explicit direct: drop any inherited proxy so Hermes connects directly.
      delete env.HTTPS_PROXY;
      delete env.HTTP_PROXY;
      delete env.https_proxy;
      delete env.http_proxy;
    }

    const hermesLogFile = path.join(logDir, 'hermes.log');
    let hermesLogStream;
    try {
      hermesLogStream = fs.createWriteStream(hermesLogFile, { flags: 'a' });
    } catch (err) {
      log('hermes-runner', `failed to create hermes log stream: ${err.message}`);
    }

    log('hermes-runner', `spawning ${hermesCommand.exe} ${hermesCommand.args.join(' ')}`);
    log('hermes-runner', `HERMES_HOME=${this.hermesHome}`);

    this.process = spawn(hermesCommand.exe, hermesCommand.args, {
      // Run from the writable home dir so Hermes doesn't pick up
      // hermes-fork/AGENTS.md / SOUL.md as project context files.
      cwd: this.hermesHome,
      env,
      windowsHide: true,
      detached: false,
    });

    this.process.stdout.on('data', (d) => {
      const text = d.toString();
      log('hermes-stdout', text.trim());
      if (hermesLogStream) hermesLogStream.write(text);
    });
    this.process.stderr.on('data', (d) => {
      const text = d.toString();
      log('hermes-stderr', text.trim());
      if (hermesLogStream) hermesLogStream.write(text);
    });
    this.process.on('exit', (code) => {
      log('hermes-runner', `Hermes process exited with code ${code}`);
      this._exitCode = code;
      if (hermesLogStream) {
        try { hermesLogStream.end(); } catch (_) {}
      }
      this.process = null;
    });
    this.process.on('error', (err) => {
      log('hermes-runner', `Hermes process error: ${err.message}`);
    });

    await this._waitForReady();
    log('hermes-runner', `Hermes backend ready at ${this._statusUrl()}`);
  }

  stop() {
    if (!this.process) return Promise.resolve();
    const proc = this.process;
    log('hermes-runner', `stopping Hermes process pid=${proc.pid}`);
    this.process = null;

    return new Promise((resolve) => {
      const cleanup = () => {
        try { proc.removeAllListeners(); } catch (_) {}
        resolve();
      };

      proc.once('exit', cleanup);

      try {
        proc.kill('SIGTERM');
      } catch (_) {}

      // Windows: make sure the whole tree is gone.
      if (process.platform === 'win32') {
        try {
          const killer = spawn('taskkill', ['/pid', String(proc.pid), '/t', '/f'], { windowsHide: true, detached: true });
          killer.on('error', () => {});
        } catch (_) {}
      }

      // Fallback: don't wait forever.
      setTimeout(cleanup, 2500);
    });
  }

  async restart() {
    await this.stop();
    // Give the OS a moment to release the port.
    await new Promise((r) => setTimeout(r, 500));
    return this.start();
  }
}

module.exports = { HermesRunner };
