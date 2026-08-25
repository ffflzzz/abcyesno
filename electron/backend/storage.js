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
    await fs.writeFile(this.assistantsFile, JSON.stringify(data, null, 2), 'utf-8');
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
    try {
      const raw = await fs.readFile(this.sessionsFile, 'utf-8');
      const data = JSON.parse(raw);
      const sessions = data.sessions || [];
      return assistantId ? sessions.filter((s) => s.assistantId === assistantId) : sessions;
    } catch {
      return [];
    }
  }

  async createSession(assistantId, title) {
    await fs.mkdir(this.baseDir, { recursive: true });
    let data;
    try {
      const raw = await fs.readFile(this.sessionsFile, 'utf-8');
      data = JSON.parse(raw);
    } catch {
      data = { sessions: [] };
    }
    const session = {
      id: uuidv4(),
      assistantId,
      title: title || '新会话',
      preview: '',
      updatedAt: Date.now(),
      messages: [],
    };
    data.sessions.push(session);
    await fs.writeFile(this.sessionsFile, JSON.stringify(data, null, 2), 'utf-8');
    return session;
  }

  async deleteSession(id) {
    let data;
    try {
      const raw = await fs.readFile(this.sessionsFile, 'utf-8');
      data = JSON.parse(raw);
    } catch {
      return;
    }
    data.sessions = data.sessions.filter((s) => s.id !== id);
    await fs.writeFile(this.sessionsFile, JSON.stringify(data, null, 2), 'utf-8');
  }

  async getSession(id) {
    const sessions = await this.listSessions();
    return sessions.find((s) => s.id === id) || null;
  }

  async updateSession(id, patch) {
    let data;
    try {
      const raw = await fs.readFile(this.sessionsFile, 'utf-8');
      data = JSON.parse(raw);
    } catch {
      data = { sessions: [] };
    }
    const idx = data.sessions.findIndex((s) => s.id === id);
    if (idx === -1) return null;
    data.sessions[idx] = { ...data.sessions[idx], ...patch, updatedAt: Date.now() };
    await fs.writeFile(this.sessionsFile, JSON.stringify(data, null, 2), 'utf-8');
    return data.sessions[idx];
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
    let data;
    try {
      const raw = await fs.readFile(this.sessionsFile, 'utf-8');
      data = JSON.parse(raw);
    } catch {
      return null;
    }
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
    await fs.writeFile(this.sessionsFile, JSON.stringify(data, null, 2), 'utf-8');
    return session;
  }

  async getThreadMapping(threadId) {
    if (!threadId) return null;
    try {
      const raw = await fs.readFile(this.threadsFile, 'utf-8');
      const data = JSON.parse(raw);
      return data[threadId] || null;
    } catch {
      return null;
    }
  }

  async setThreadMapping(threadId, hermesSessionId) {
    if (!threadId) return;
    await fs.mkdir(this.baseDir, { recursive: true });
    let data;
    try {
      const raw = await fs.readFile(this.threadsFile, 'utf-8');
      data = JSON.parse(raw);
    } catch {
      data = {};
    }
    if (!hermesSessionId) {
      delete data[threadId];
    } else {
      data[threadId] = hermesSessionId;
    }
    await fs.writeFile(this.threadsFile, JSON.stringify(data, null, 2), 'utf-8');
  }
}

module.exports = { Storage };
