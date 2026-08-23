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

function createWechatBridgeRunner({ onStatus } = {}) {
  let started = false;
  let unsub = null;

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
      default:
        throw new Error(`Unknown wechat-call action: ${action}`);
    }
  }

  return { start, stop, call, isStarted: () => started };
}

module.exports = { createWechatBridgeRunner };
