const fs = require('fs').promises;
const path = require('path');
const { v4: uuidv4 } = require('uuid');

class Storage {
  constructor(baseDir) {
    this.baseDir = baseDir;
    this.assistantsFile = path.join(baseDir, 'abcyesno_assistants.json');
    this.sessionsFile = path.join(baseDir, 'abcyesno_sessions.json');
    this.threadsFile = path.join(baseDir, 'abcyesno_threads.json');
    this._cache = null;
    // Per-file promise chains serializing read-modify-write cycles.
    // 2026-08-26 corruption incident: concurrent fs.writeFile calls on
    // abcyesno_sessions.json interleaved (flush-on-unmount + onSettled
    // persist + title updates during a tab-switch unmount storm) and tore
    // the file — a shorter doc overwrote the head of a longer one, leaving
    // trailing garbage that broke JSON.parse and blanked the session list.
    this._locks = new Map();
  }

  /** Serialize async read-modify-write operations per file. */
  _withLock(file, fn) {
    const prev = this._locks.get(file) || Promise.resolve();
    const run = prev.then(() => fn());
    this._locks.set(file, run.catch(() => {}));
    return run;
  }

  /**
   * Atomic JSON write: write to a temp file, then rename over the target.
   * A crash mid-write can never leave a torn target file, and the rename
   * is a single filesystem op so concurrent readers see old or new, never
   * a mix. (libuv uses MOVEFILE_REPLACE_EXISTING on Windows.)
   */
  async _atomicWriteJson(file, data) {
    const tmp = `${file}.tmp-${process.pid}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    await fs.writeFile(tmp, JSON.stringify(data, null, 2), 'utf-8');
    await fs.rename(tmp, file);
  }

  /**
   * Read + parse JSON, tolerating legacy torn files: if the raw content has
   * extra data after a complete document (the 2026-08-26 corruption shape),
   * recover by parsing just the first complete document instead of
   * returning the fallback and losing everything.
   */
  async _readJsonSafe(file, fallback) {
    let raw;
    try {
      raw = await fs.readFile(file, 'utf-8');
    } catch {
      return fallback;
    }
    try {
      return JSON.parse(raw);
    } catch (err) {
      const m = /position (\d+)/.exec(err && err.message || '');
      if (m) {
        try {
          const recovered = JSON.parse(raw.slice(0, Number(m[1])));
          console.warn(`[storage] recovered torn JSON file ${path.basename(file)} at position ${m[1]}`);
          return recovered;
        } catch { /* fall through */ }
      }
      return fallback;
    }
  }

  async _ensure() {
    if (this._cache) return this._cache;
    await fs.mkdir(this.baseDir, { recursive: true });
    try {
      const data = await fs.readFile(this.assistantsFile, 'utf-8');
      this._cache = JSON.parse(data);
    } catch {
      this._cache = { assistants: [], version: 1 };
    }
    return this._cache;
  }

  async _save() {
    const data = await this._ensure();
    await this._atomicWriteJson(this.assistantsFile, data);
  }

  async listAssistants() {
    const data = await this._ensure();
    if (!data.assistants || data.assistants.length === 0) {
      return this._defaultAssistants();
    }
    // Migrate the legacy default-assistant name to the current abcyesno persona.
    // The chain goes "通用助手" -> "ABC" (older) -> "Chaos" (current). Only the
    // built-in default assistant (id='default') is touched — any assistant the
    // user has since renamed to something else (or created themselves) is left
    // alone, even if its name happens to collide with one of these.
    let migrated = false;
    for (const a of data.assistants) {
      if (a.id !== 'default') continue;
      if (a.name === '通用助手' || a.name === 'ABC') {
        a.name = 'Chaos';
        migrated = true;
      }
    }
    if (migrated) await this._save();
    return data.assistants;
  }

  async getAssistant(id) {
    const list = await this.listAssistants();
    return list.find((a) => a.id === id) || this._defaultAssistants()[0];
  }

  async createAssistant({ name, skillId, description, avatar, defaultModel, config }) {
    const data = await this._ensure();
    const assistant = {
      id: uuidv4(),
      name: name || '新助手',
      skillId: skillId || 'default',
      description: description || '',
      avatar: avatar || '',
      defaultModel: defaultModel || 'agnes-2.5-flash',
      capabilities: [],
      config: config || {},
      createdAt: Date.now(),
    };
    data.assistants.push(assistant);
    await this._save();
    return assistant;
  }

  async deleteAssistant(id) {
    const data = await this._ensure();
    data.assistants = data.assistants.filter((a) => a.id !== id);
    await this._save();
  }

  async updateAssistant(id, patch) {
    const data = await this._ensure();
    const idx = data.assistants.findIndex((a) => a.id === id);
    if (idx === -1) return null;
    data.assistants[idx] = { ...data.assistants[idx], ...patch };
    await this._save();
    return data.assistants[idx];
  }

  _defaultAssistants() {
    // Only the default Chaos assistant lives here. The legacy Manju Craft
    // entry was removed when the bundled manju-craft LangGraph agent was
    // deleted (2026-08-15 cleanup) — its skillId pointed at a skill that
    // no longer exists. Workflow assistants (e.g. 短剧制片工作台) flow
    // through the manifest/codegen path, not this hardcoded list.
    //
    // 2026-08-24: renamed "ABC" → "Chaos" to match the new abcyesno agent
    // persona (see hermes_cli/default_soul.py). The description is kept for
    // the marketplace / settings views but the welcome screen in
    // ChatLayout.jsx no longer renders it (cleaned up in the same pass).
    return [
      {
        id: 'default',
        name: 'Chaos',
        skillId: 'default',
        description: '默认助手，支持终端、文件、浏览器等工具',
        avatar: '',
        defaultModel: 'agnes-2.5-flash',
        capabilities: ['chat', 'tools'],
        config: {},
        createdAt: 0,
      },
    ];
  }

  async listSessions(assistantId) {
    const data = await this._readJsonSafe(this.sessionsFile, null);
    if (!data) return [];
    const sessions = data.sessions || [];
    return assistantId ? sessions.filter((s) => s.assistantId === assistantId) : sessions;
  }

  async createSession(assistantId, title, extra) {
    return this._withLock(this.sessionsFile, async () => {
      await fs.mkdir(this.baseDir, { recursive: true });
      const data = (await this._readJsonSafe(this.sessionsFile, null)) || { sessions: [] };
      if (!Array.isArray(data.sessions)) data.sessions = [];
      const session = {
        id: uuidv4(),
        assistantId,
        title: title || '新会话',
        preview: '',
        updatedAt: Date.now(),
        messages: [],
        ...(extra && typeof extra === 'object' ? extra : {}),
      };
      data.sessions.push(session);
      await this._atomicWriteJson(this.sessionsFile, data);
      return session;
    });
  }

  async deleteSession(id) {
    return this._withLock(this.sessionsFile, async () => {
      const data = await this._readJsonSafe(this.sessionsFile, null);
      if (!data) return;
      data.sessions = (data.sessions || []).filter((s) => s.id !== id);
      await this._atomicWriteJson(this.sessionsFile, data);
    });
  }

  async getSession(id) {
    const sessions = await this.listSessions();
    return sessions.find((s) => s.id === id) || null;
  }

  async updateSession(id, patch) {
    return this._withLock(this.sessionsFile, async () => {
      const data = (await this._readJsonSafe(this.sessionsFile, null)) || { sessions: [] };
      if (!Array.isArray(data.sessions)) data.sessions = [];
      const idx = data.sessions.findIndex((s) => s.id === id);
      if (idx === -1) return null;
      data.sessions[idx] = { ...data.sessions[idx], ...patch, updatedAt: Date.now() };
      await this._atomicWriteJson(this.sessionsFile, data);
      return data.sessions[idx];
    });
  }

  /**
   * Append a single message to a session in a read-modify-write pass and
   * refresh preview/updatedAt. Used by the WeChat bridge so each inbound/
   * outbound turn shows up in the main-program session list without going
   * through the full ChatShell create-flow. Caps the stored messages at
   * MAX_MESSAGES_PER_SESSION to keep the JSON file bounded.
   */
  async appendSessionMessage(id, role, content) {
    const MAX_MESSAGES_PER_SESSION = 200;
    return this._withLock(this.sessionsFile, async () => {
      const data = await this._readJsonSafe(this.sessionsFile, null);
      if (!data) return null;
      const idx = (data.sessions || []).findIndex((s) => s.id === id);
      if (idx === -1) return null;
      const session = data.sessions[idx];
      session.messages = Array.isArray(session.messages) ? session.messages : [];
      session.messages.push({ role, content, ts: Date.now() });
      if (session.messages.length > MAX_MESSAGES_PER_SESSION) {
        session.messages = session.messages.slice(-MAX_MESSAGES_PER_SESSION);
      }
      session.preview = String(content || '').slice(0, 100).replace(/\s+/g, ' ').trim();
      session.updatedAt = Date.now();
      await this._atomicWriteJson(this.sessionsFile, data);
      return session;
    });
  }

  async getThreadMapping(threadId) {
    if (!threadId) return null;
    const data = await this._readJsonSafe(this.threadsFile, null);
    return data ? data[threadId] || null : null;
  }

  async setThreadMapping(threadId, hermesSessionId) {
    if (!threadId) return;
    return this._withLock(this.threadsFile, async () => {
      await fs.mkdir(this.baseDir, { recursive: true });
      const data = (await this._readJsonSafe(this.threadsFile, null)) || {};
      if (!hermesSessionId) {
        delete data[threadId];
      } else {
        data[threadId] = hermesSessionId;
      }
      await this._atomicWriteJson(this.threadsFile, data);
    });
  }
}

module.exports = { Storage };
