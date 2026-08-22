// Spawns the paper_rewriter FastAPI dashboard as a local service so the
// launcher can open it inside an in-app browser tab. This is Option A from
// the integration plan: the agent's own FastAPI server (server/agent_app.py)
// serves the built React SPA (frontend/dist) AND runs the agent in a worker
// thread, streaming progress over SSE. It is fully independent of the Hermes
// backend — the @-invocation path uses the separately-vendored
// paper_rewriter_agent (pr_graph) inside Hermes, so the two surfaces coexist
// with their own HITL flows.
//
// Mirrors electron/backend/hermes-runner.js but is best-effort: a failure to
// start the dashboard must never block or crash the main app.

const { spawn } = require('child_process');
const http = require('http');
const path = require('path');
const fs = require('fs');
const { log, logDir } = require('./logger');

// dashboard lives beside the agent package, under hermes-fork/skills/...
const DASHBOARD_DIR = path.join(
  __dirname, '..', '..', 'hermes-fork', 'skills', 'langgraph_agents',
  'agents', 'paper_rewriter_agent', 'dashboard'
);
const HERMES_FORK = path.join(__dirname, '..', '..', 'hermes-fork');
const HERMES_VENV = path.join(HERMES_FORK, '.venv');
const HERMES_PYTHON = path.join(HERMES_VENV, 'Scripts', 'python.exe');

const PORT = 8765;
const HOST = '127.0.0.1';
const MAX_WAIT_MS = 120000;
const POLL_MS = 600;

class PaperRewriterDashboardRunner {
  constructor({ app }) {
    this.app = app;
    this.process = null;
    this.port = PORT;
    this.host = HOST;
    this._exitCode = null;
    this._startingPromise = null;
    this.hermesHome = app ? app.getPath('userData') : '';
  }

  getPort() { return this.port; }
  getUrl() { return `http://${this.host}:${this.port}`; }

  _readAgnesKey() {
    try {
      const text = fs.readFileSync(path.join(this.hermesHome, '.env'), 'utf-8');
      const m = text.match(/^AGNES_API_KEY=(.+)$/m);
      return m ? m[1].trim() : '';
    } catch (_) {
      return '';
    }
  }

  _statusUrl() {
    return `http://${this.host}:${this.port}/api/status`;
  }

  async _waitForReady() {
    const start = Date.now();
    while (true) {
      if (this._exitCode !== null) {
        throw new Error(`paper_rewriter dashboard exited early (code ${this._exitCode})`);
      }
      if (!this.process) {
        throw new Error('paper_rewriter dashboard process gone before ready');
      }
      const ok = await new Promise((resolve) => {
        const req = http.get(this._statusUrl(), { timeout: 2000 }, (res) => {
          resolve(res.statusCode === 200);
          res.resume();
        });
        req.on('error', () => resolve(false));
        req.on('timeout', () => { req.destroy(); resolve(false); });
      });
      if (ok) return;
      if (Date.now() - start > MAX_WAIT_MS) {
        throw new Error(`paper_rewriter dashboard not ready within ${MAX_WAIT_MS}ms`);
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
    if (!fs.existsSync(DASHBOARD_DIR)) {
      throw new Error(`paper_rewriter dashboard dir not found: ${DASHBOARD_DIR}`);
    }
    if (!fs.existsSync(HERMES_PYTHON)) {
      throw new Error(`Python runtime not found: ${HERMES_PYTHON}`);
    }

    const apiKey = this._readAgnesKey();
    const outputDir = path.join(DASHBOARD_DIR, 'runs');
    try { fs.mkdirSync(outputDir, { recursive: true }); } catch (_) {}

    const env = {
      ...process.env,
      HERMES_HOME: this.hermesHome,
      // Agnes credentials override the hardcoded key in pipeline/config.py
      // (it reads LLM_API_KEY from env first).
      LLM_API_KEY: apiKey || process.env.LLM_API_KEY || '',
      LLM_BASE_URL: process.env.LLM_BASE_URL || 'https://apihub.agnes-ai.com/v1',
      OUTPUT_DIR: outputDir,
      SERVER_HOST: this.host,
      SERVER_PORT: String(this.port),
      // Make the dashboard package importable as top-level `agent` / `pipeline`.
      PYTHONPATH: DASHBOARD_DIR + (process.env.PYTHONPATH ? path.delimiter + process.env.PYTHONPATH : ''),
    };

    const logFile = path.join(logDir, 'paper-rewriter-dashboard.log');
    let logStream;
    try { logStream = fs.createWriteStream(logFile, { flags: 'a' }); } catch (_) {}

    log('paper-dashboard', `spawning ${HERMES_PYTHON} -m server.agent_app (cwd=${DASHBOARD_DIR})`);

    this.process = spawn(HERMES_PYTHON, ['-m', 'server.agent_app'], {
      cwd: DASHBOARD_DIR,
      env,
      windowsHide: true,
      detached: false,
    });

    this.process.stdout.on('data', (d) => {
      const text = d.toString();
      log('paper-dashboard-stdout', text.trim());
      if (logStream) logStream.write(text);
    });
    this.process.stderr.on('data', (d) => {
      const text = d.toString();
      log('paper-dashboard-stderr', text.trim());
      if (logStream) logStream.write(text);
    });
    this.process.on('exit', (code) => {
      log('paper-dashboard', `process exited with code ${code}`);
      this._exitCode = code;
      if (logStream) { try { logStream.end(); } catch (_) {} }
      this.process = null;
    });
    this.process.on('error', (err) => {
      log('paper-dashboard', `process error: ${err.message}`);
    });

    await this._waitForReady();
    log('paper-dashboard', `ready at ${this._statusUrl()}`);
  }

  stop() {
    if (!this.process) return Promise.resolve();
    const proc = this.process;
    log('paper-dashboard', `stopping pid=${proc.pid}`);
    this.process = null;
    return new Promise((resolve) => {
      const cleanup = () => {
        try { proc.removeAllListeners(); } catch (_) {}
        resolve();
      };
      proc.once('exit', cleanup);
      try { proc.kill('SIGTERM'); } catch (_) {}
      if (process.platform === 'win32') {
        try {
          const killer = spawn('taskkill', ['/pid', String(proc.pid), '/t', '/f'], { windowsHide: true, detached: true });
          killer.on('error', () => {});
        } catch (_) {}
      }
      setTimeout(cleanup, 2500);
    });
  }
}

module.exports = { PaperRewriterDashboardRunner };
