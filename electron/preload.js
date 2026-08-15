const { contextBridge, ipcRenderer } = require('electron');

const listenerMap = new Map();

function on(channel, cb) {
  const wrapped = (_e, ...args) => cb(...args);
  listenerMap.set(cb, wrapped);
  ipcRenderer.on(channel, wrapped);
}

function off(channel, cb) {
  const wrapped = listenerMap.get(cb);
  if (wrapped) {
    ipcRenderer.off(channel, wrapped);
    listenerMap.delete(cb);
  }
}

contextBridge.exposeInMainWorld('hermes', {
  // Lifecycle & runtime
  getVersion: () => ipcRenderer.invoke('get-version'),
  getBrowserInfo: () => ipcRenderer.invoke('get-browser-info'),
  // Browser automation (§5.5, route B): the renderer reports the <webview>'s
  // guest webContentsId to the main process so the native driver service can
  // drive the *visible* in-app browser. Sent once the webview is dom-ready.
  reportBrowserWebview: (id) => ipcRenderer.send('browser-webview-ready', id),
  clearBrowserWebview: () => ipcRenderer.send('browser-webview-destroyed'),
  getStatus: () => ipcRenderer.invoke('get-status'),
  getAguiPort: () => ipcRenderer.invoke('get-agui-port'),
  getApiKeyStatus: () => ipcRenderer.invoke('get-api-key-status'),
  validateApiKey: (key) => ipcRenderer.invoke('validate-api-key', key),
  setApiKey: (key) => ipcRenderer.invoke('set-api-key', key),
  logError: (msg) => ipcRenderer.invoke('log-error', msg),
  openDataDir: () => ipcRenderer.invoke('open-data-dir'),

  // Settings-panel equivalents of the old native menu entries.
  openDevTools: () => ipcRenderer.invoke('open-devtools'),
  quitApp: () => ipcRenderer.invoke('quit-app'),

  // Workflow intent parser (smart input → structured inputObj)
  parseWorkflowIntent: (nlText, manifestId) => ipcRenderer.invoke('parse-workflow-intent', nlText, manifestId),

  // Assistants
  listAssistants: () => ipcRenderer.invoke('list-assistants'),
  createAssistant: (data) => ipcRenderer.invoke('create-assistant', data),
  updateAssistant: (id, data) => ipcRenderer.invoke('update-assistant', id, data),
  deleteAssistant: (id) => ipcRenderer.invoke('delete-assistant', id),
  listSkills: () => ipcRenderer.invoke('list-skills'),

  // Sessions
  listSessions: (assistantId) => ipcRenderer.invoke('list-sessions', assistantId),
  createSession: (assistantId, title) => ipcRenderer.invoke('create-session', assistantId, title),
  deleteSession: (id) => ipcRenderer.invoke('delete-session', id),
  getSession: (id) => ipcRenderer.invoke('get-session', id),
  updateSession: (id, data) => ipcRenderer.invoke('update-session', id, data),

  // Approval & gateway passthrough
  respondApproval: (id, choice) => ipcRenderer.invoke('respond-approval', id, choice),
  // Workflow (LangGraph HITL) brake: POST the decision to the agui-server
  // control-channel endpoint; the paused graph polls the resulting file.
  sendWorkflowInterrupt: async (payload) => {
    const port = await ipcRenderer.invoke('get-agui-port');
    if (!port) throw new Error('agui port unknown');
    const res = await fetch(`http://localhost:${port}/api/ag-ui/interrupt`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    return res.json();
  },
  gatewayRequest: (method, params, timeout) => ipcRenderer.invoke('gateway-request', method, params, timeout),

  // Voice STT: forward recorded audio (base64) to the agui-server proxy,
  // which calls Agnes /audio/transcriptions server-side (key stays in backend).
  transcribeAudio: async (audioBase64, mime) => {
    const port = await ipcRenderer.invoke('get-agui-port');
    if (!port) throw new Error('agui port unknown');
    const res = await fetch(`http://localhost:${port}/api/transcribe`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ audio: audioBase64, mime: mime || 'audio/webm' }),
    });
    return res.json();
  },

  // File upload
  selectFile: (options) => ipcRenderer.invoke('select-file', options),
  uploadFile: (sessionId, filePath) => ipcRenderer.invoke('upload-file', sessionId, filePath),

  // Result panel: workspace file tree + read-file + open external (spec §5/§7.1)
  listWorkspace: (opts) => ipcRenderer.invoke('list-workspace', opts),
  readFile: (opts) => ipcRenderer.invoke('read-file', opts),
  openExternal: (url) => ipcRenderer.invoke('open-external', url),

  // Read a local image file as a base64 data URL. Used by ApprovalBubble to
  // render workspace artifacts: the file:// renderer cannot load cross-directory
  // file:// images, so the main process reads + encodes them.
  readLocalImage: (p) => ipcRenderer.invoke('read-local-image', p),

  // Pop the result panel into a standalone Electron window. The new window
  // shares the same backend (AG-UI/Hermes) — only the surrounding chrome
  // (Sidebar, ChatLayout) is omitted. Mirrors Chrome's "move tab to a new
  // window" gesture.
  detachResultPanel: (opts) => ipcRenderer.invoke('detach-result-panel', opts || {}),

  // Event subscriptions
  on,
  off,
  removeAllListeners: (channel) => ipcRenderer.removeAllListeners(channel),

  // Backend lifecycle
  onAguiReady: (cb) => on('agui-ready', cb),
  offAguiReady: (cb) => off('agui-ready', cb),

  // Global shortcuts pushed from the main process.
  onOpenSettings: (cb) => on('open-settings', cb),
  offOpenSettings: (cb) => off('open-settings', cb),

  // Direct interrupt fallback (used when CopilotKit's stopGeneration cannot
  // reach the AG-UI runtime in time).
  interruptSession: (sessionId) => ipcRenderer.invoke('interrupt-session', sessionId),

  // Permission mode: push default/yolo to the backend for the current session.
  setPermissionMode: (mode, sessionId) => ipcRenderer.invoke('set-permission-mode', mode, sessionId),

  // Studio workbench: call Agnes image/video generation via IPC (main process)
  studioCall: (action, params) => ipcRenderer.invoke('studio-call', { action, params }),
});
