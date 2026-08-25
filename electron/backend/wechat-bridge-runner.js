/**
 * wechat-bridge-runner — in-process host for the vendored wechat-claude-code
 * bridge (electron/backend/wechat_bridge/dist/index.js).
 *
 * Unlike hermes-runner this is NOT a child process: the bridge runs inside
 * the Electron main process (it only does HTTPS long-polling to the iLink
 * API and HTTP SSE to the local agui-server, so a crash cannot take the app
 * down if callers wrap start/stop in try/catch — mirroring how agui-server
 * itself runs in-process).
 *
 * Lifecycle:
 *   createWechatBridgeRunner({ getMainWindow })  → runner with start()/stop()
 *   main.js calls start() at the tail of doStartBackend (fire-and-forget)
 *   and stop() from window-all-closed / before-quit.
 */
const bridge = require('./wechat_bridge/dist/index.js');

function maskId(id) {
  if (!id) return undefined;
  if (id.length <= 8) return `${id.slice(0, 2)}****`;
  return `${id.slice(0, 4)}****${id.slice(-4)}`;
}

function createWechatBridgeRunner({ onStatus, getStorage, onSessionsUpdated } = {}) {
  let started = false;
  let unsub = null;

  // Wire the bridge's optional abcyesno-Storage hook so WeChat turns
  // show up in the main-app session list. setAbcStorage is a no-op if
  // getStorage() returns null (e.g. in standalone test mode).
  try {
    const storage = getStorage && getStorage();
    if (storage && typeof bridge.setAbcStorage === 'function') {
      bridge.setAbcStorage(storage, () => {
        try { onSessionsUpdated && onSessionsUpdated(); } catch { /* ignore */ }
      });
    }
  } catch (err) {
    console.warn('[wechat-bridge] inject storage failed:', err?.message || err);
  }

  function emit(status) {
    try {
      const payload = {
        state: status.state,
        bound: !!status.bound,
        accountMasked: status.accountId ? maskId(status.accountId) : undefined,
        detail: status.detail || '',
        ts: status.ts || Date.now(),
      };
      onStatus?.(payload);
    } catch {
      /* status push must never crash the bridge */
    }
  }

  async function start() {
    if (started) return { ok: true, alreadyStarted: true };
    started = true;
    try {
      unsub = bridge.onBridgeStatus(emit);
      const status = await bridge.startBridge();
      emit(status);
      return { ok: true };
    } catch (err) {
      started = false;
      return { ok: false, error: err instanceof Error ? err.message : String(err) };
    }
  }

  function stop() {
    if (!started && !bridge.getBridgeStatus()) return;
    try {
      bridge.stopBridge();
    } catch (err) {
      console.warn('[wechat-bridge] stop error:', err?.message || err);
    }
    try { unsub?.(); } catch { /* ignore */ }
    unsub = null;
    started = false;
  }

  /**
   * Lookup the abcyesno sessionId previously registered for this WeChat user.
   * If none exists, call storage.createSession (injected by main.js) to make
   * one and persist the mapping. Returns {sessionId, created}.
   *
   * The bridge itself doesn't import Storage — it only owns the mapping.
   * Main.js injects the storage instance so the bridge stays decoupled
   * from the Electron-side schema.
   */
  async function ensureSession(fromUserId, fromUserMasked) {
    if (!fromUserId) throw new Error('ensureSession: fromUserId required');
    const existing = bridge.getSessionIdForFromUser(fromUserId);
    if (existing) return { sessionId: existing, created: false };
    const storage = getStorage && getStorage();
    if (!storage || typeof storage.createSession !== 'function') {
      throw new Error('ensureSession: storage not injected');
    }
    const label = fromUserMasked || fromUserId.slice(0, 8);
    const session = await storage.createSession('default', `微信 · ${label}`);
    bridge.setSessionIdForFromUser(fromUserId, session.id);
    onSessionsUpdated && onSessionsUpdated();
    return { sessionId: session.id, created: true };
  }

  async function appendMessage(sessionId, role, content) {
    if (!sessionId) throw new Error('appendMessage: sessionId required');
    const storage = getStorage && getStorage();
    if (!storage || typeof storage.appendSessionMessage !== 'function') {
      throw new Error('appendMessage: storage not injected');
    }
    await storage.appendSessionMessage(sessionId, role, String(content || ''));
    onSessionsUpdated && onSessionsUpdated();
    return { ok: true };
  }

  /**
   * IPC action dispatch — mirrors the studio-call pattern.
   * Returns plain JSON-serializable values.
   */
  async function call(action, params = {}) {
    switch (action) {
      case 'getStatus':
        return bridge.getBridgeStatus();
      case 'start':
        await start();
        return bridge.getBridgeStatus();
      case 'stop':
        stop();
        return bridge.getBridgeStatus();
      case 'restart':
        await bridge.restartBridge();
        return bridge.getBridgeStatus();
      case 'getQrCode': {
        // Begin (or reuse) the bind flow, then render the QR as a data URL.
        if (!bridge.getCurrentQrUrl()) {
          await bridge.beginQrBind();
        }
        const dataUrl = await bridge.getQrDataUrl();
        return { qrDataUrl: dataUrl, raw: bridge.getCurrentQrUrl() };
      }
      case 'refreshQrCode': {
        await bridge.beginQrBind(); // requests a fresh QR + restarts polling
        const dataUrl = await bridge.getQrDataUrl();
        return { qrDataUrl: dataUrl, raw: bridge.getCurrentQrUrl() };
      }
      case 'unbind':
        bridge.unbindAccount();
        return bridge.getBridgeStatus();
      case 'getLogs':
        return { lines: bridge.tailLogs(Number(params.lines) || 120) };
      case 'sendTestMessage':
        return bridge.sendTestMessage(String(params.text || '来自 Abcyesno 的测试消息 ✅'));
      case 'ensureSession':
        return ensureSession(params.fromUserId, params.fromUserMasked);
      case 'appendMessage':
        return appendMessage(params.sessionId, params.role, params.content);
      case 'getSessionIdByFromUser': {
        return { sessionId: bridge.getSessionIdForFromUser(params.fromUserId) };
      }
      default:
        throw new Error(`Unknown wechat-call action: ${action}`);
    }
  }

  return { start, stop, call, isStarted: () => started };
}

module.exports = { createWechatBridgeRunner };
