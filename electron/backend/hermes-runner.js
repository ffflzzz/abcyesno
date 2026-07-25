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
const MAX_WAIT_MS = 120000;
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
    // Hermes resolves skills under HERMES_HOME/skills. The bundled
    // langgraph-agents skill lives in hermes-fork/skills; mirror it into the
    // writable home dir so preloading via HERMES_TUI_SKILLS actually finds it.
    const srcDir = path.join(HERMES_FORK, 'skills', 'langgraph_agents');
    const destDir = path.join(this.hermesHome, 'skills', 'langgraph_agents');
    if (!fs.existsSync(srcDir)) {
      log('hermes-runner', `builtin skill source not found: ${srcDir}`);
      return;
    }
    try {
      fs.mkdirSync(path.dirname(destDir), { recursive: true });
      this._copyDirRecursive(srcDir, destDir);
      log('hermes-runner', `synced builtin skill to ${destDir}`);
    } catch (err) {
      log('hermes-runner', `failed to sync builtin skills: ${err.message}`);
    }
  }

  _copyDirRecursive(src, dest) {
    fs.mkdirSync(dest, { recursive: true });
    for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
      const srcPath = path.join(src, entry.name);
      const destPath = path.join(dest, entry.name);
      if (entry.isDirectory()) {
        this._copyDirRecursive(srcPath, destPath);
      } else {
        fs.copyFileSync(srcPath, destPath);
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
    while (Date.now() - start < MAX_WAIT_MS) {
      const ok = await new Promise((resolve) => {
        const req = http.get(this._statusUrl(), { timeout: 2500 }, (res) => {
          resolve(res.statusCode === 200);
          res.resume();
        });
        req.on('error', () => resolve(false));
        req.on('timeout', () => { req.destroy(); resolve(false); });
      });
      if (ok) return;
      await new Promise((r) => setTimeout(r, POLL_MS));
    }
    throw new Error(`Hermes backend did not become ready on port ${this.port} within ${MAX_WAIT_MS}ms`);
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
    this.port = await this._findAvailablePort();
    if (this.port !== PORT) {
      log('hermes-runner', `port ${PORT} busy, using ${this.port}`);
    }
    const hermesCommand = fs.existsSync(HERMES_PYTHON)
      ? { exe: HERMES_PYTHON, args: ['-m', 'hermes_cli.main', 'serve', '--port', String(this.port), '--host', this.host, '--skip-build'] }
      : { exe: HERMES_EXE, args: ['serve', '--port', String(this.port), '--host', this.host, '--skip-build'] };
    if (!fs.existsSync(hermesCommand.exe)) {
      throw new Error(`Hermes runtime not found: ${hermesCommand.exe}`);
    }

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
      MANJU_CRAFT_MOCK: process.env.MANJU_CRAFT_MOCK || '',
      // Port the agui-server bridge listens on. The Python langgraph runtime
      // POSTs HITL workflow events here so they can reach the frontend SSE.
      AGUI_PORT: process.env.AGUI_PORT || '9121',
      // Cap tool-calling iterations so a confused model doesn't spin forever.
      HERMES_TUI_MAX_TURNS: process.env.HERMES_TUI_MAX_TURNS || '15',
      // Enable the Hermes toolset that carries langgraph_agent and shell/file tools.
      HERMES_TUI_TOOLSETS: process.env.HERMES_TUI_TOOLSETS || 'hermes-cli',
      // Preload the LangGraph agents skill so manju_craft / hello_agent are available.
      HERMES_TUI_SKILLS: process.env.HERMES_TUI_SKILLS || 'langgraph-agents',
      PYTHONPATH: HERMES_FORK + (process.env.PYTHONPATH ? path.delimiter + process.env.PYTHONPATH : ''),
    };

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
