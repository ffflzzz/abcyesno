import { join } from 'node:path';
import { readdirSync, readFileSync, mkdirSync, rmSync, existsSync } from 'node:fs';

import { loadLatestAccount, type AccountData } from './wechat/accounts.js';
import { startQrLogin, waitForQrScan } from './wechat/login.js';
import { createDaemonRuntime, type DaemonRuntime } from './main.js';
import { DATA_DIR } from './constants.js';
import { logger } from './logger.js';
import { claudeQuery } from './claude/provider.js';

export { claudeQuery };

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

export type BridgeState =
  | 'idle'          // not started
  | 'awaiting_qr'   // QR shown, waiting for scan/confirm
  | 'qr_expired'    // current QR expired, regenerating
  | 'connecting'    // account bound, daemon starting / monitor connecting
  | 'connected'     // monitor running
  | 'reconnecting'  // monitor retrying after session expired
  | 'error';        // fatal error

export interface BridgeStatus {
  state: BridgeState;
  bound: boolean;
  accountId?: string;
  detail?: string;
  ts: number;
}

type StatusListener = (status: BridgeStatus) => void;

// ---------------------------------------------------------------------------
// Module state (single bridge instance per process)
// ---------------------------------------------------------------------------

let runtime: DaemonRuntime | null = null;
let statusListeners: StatusListener[] = [];
let currentState: BridgeState = 'idle';
let currentDetail: string | undefined;
let bindingActive = false;
let currentQrUrl: string | null = null; // raw qrcode string from iLink (to render client-side)
const stateListeners: Array<(s: BridgeState) => void> = [];

function pushState(state: BridgeState, detail?: string): void {
  currentState = state;
  currentDetail = detail;
  const status: BridgeStatus = {
    state,
    bound: !!loadLatestAccount(),
    detail,
    ts: Date.now(),
  };
  try {
    const acc = loadLatestAccount();
    if (acc) status.accountId = acc.accountId;
  } catch { /* ignore */ }
  logger.info('bridge state', { state, detail: detail || '' });
  for (const cb of [...statusListeners]) {
    try { cb(status); } catch { /* listener errors must not break the bridge */ }
  }
}

/** Register a status listener (Electron main forwards these to the renderer). */
export function onBridgeStatus(cb: StatusListener): () => void {
  statusListeners.push(cb);
  return () => {
    statusListeners = statusListeners.filter((l) => l !== cb);
  };
}

export function getBridgeStatus(): BridgeStatus {
  const status: BridgeStatus = {
    state: currentState,
    bound: false,
    detail: currentDetail,
    ts: Date.now(),
  };
  try {
    const acc = loadLatestAccount();
    if (acc) {
      status.bound = true;
      status.accountId = acc.accountId;
    }
  } catch { /* ignore */ }
  return status;
}

/** Mask an ilink account id for display: keep head and tail. */
export function maskAccountId(id: string): string {
  if (!id || id.length <= 8) return id ? `${id.slice(0, 2)}****` : '';
  return `${id.slice(0, 4)}****${id.slice(-4)}`;
}

// ---------------------------------------------------------------------------
// Start / stop
// ---------------------------------------------------------------------------

/**
 * Auto-start path (called by wechat-bridge-runner when the app boots).
 * If an account is already bound, connects immediately; otherwise idles in
 * 'idle' state until the user binds via the UI.
 */
export async function startBridge(): Promise<BridgeStatus> {
  if (runtime) return getBridgeStatus();
  const account = loadLatestAccount();
  if (!account) {
    pushState('idle', '未绑定微信账号');
    return getBridgeStatus();
  }
  await connectWithAccount();
  return getBridgeStatus();
}

async function connectWithAccount(): Promise<void> {
  pushState('connecting');
  try {
    runtime = await createDaemonRuntime();
    if (!runtime) {
      pushState('error', '账号读取失败，请重新绑定');
      return;
    }
    pushState('connected', maskAccountId(runtime.accountId));
  } catch (err: any) {
    const msg = err instanceof Error ? err.message : String(err);
    logger.error('connectWithAccount failed', { error: msg });
    pushState('error', msg);
  }
}

export function stopBridge(): void {
  bindingActive = false;
  if (runtime) {
    runtime.stop();
    runtime = null;
  }
  pushState('idle');
}

export async function restartBridge(): Promise<BridgeStatus> {
  stopBridge();
  return startBridge();
}

// ---------------------------------------------------------------------------
// QR binding flow (replaces upstream CLI `setup`)
// ---------------------------------------------------------------------------

/**
 * Request a fresh QR code and start background polling for the scan.
 * The renderer renders `qrcodeUrl` into an image itself (via the `qrcode`
 * package) — we never write temp PNG files.
 */
export async function beginQrBind(): Promise<{ qrcodeUrl: string }> {
  bindingActive = true;
  const { qrcodeUrl, qrcodeId } = await startQrLogin();
  currentQrUrl = qrcodeUrl;
  pushState('awaiting_qr');
  // Background loop: wait for scan; regenerate on expiry while UI is open.
  void pollBindLoop(qrcodeId);
  return { qrcodeUrl };
}

async function pollBindLoop(qrcodeId: string): Promise<void> {
  let id = qrcodeId;
  while (bindingActive) {
    try {
      const account: AccountData = await waitForQrScan(id);
      // saveAccount() already ran inside waitForQrScan.
      bindingActive = false;
      currentQrUrl = null;
      logger.info('QR bind success', { accountId: account.accountId });
      await connectWithAccount();
      return;
    } catch (err: any) {
      const msg = err instanceof Error ? err.message : String(err);
      if (!bindingActive) return;
      if (msg.includes('expired')) {
        pushState('qr_expired');
        try {
          const { qrcodeUrl, qrcodeId: newId } = await startQrLogin();
          currentQrUrl = qrcodeUrl;
          id = newId;
          pushState('awaiting_qr', '二维码已刷新');
          continue;
        } catch (e: any) {
          pushState('error', `刷新二维码失败: ${e instanceof Error ? e.message : String(e)}`);
          return;
        }
      }
      pushState('error', msg);
      return;
    }
  }
}

/** Current raw QR content for the renderer to draw (null if none active). */
export function getCurrentQrUrl(): string | null {
  return currentQrUrl;
}

/**
 * Render the current QR content into a PNG data URL (for <img> in the
 * bind modal). Returns null when no bind flow is active.
 */
export async function getQrDataUrl(): Promise<string | null> {
  if (!currentQrUrl) return null;
  try {
    const QRCode = (await import('qrcode')).default;
    return await QRCode.toDataURL(currentQrUrl, { type: 'png', width: 320, margin: 2 });
  } catch (err: any) {
    logger.error('getQrDataUrl failed', { error: err instanceof Error ? err.message : String(err) });
    return null;
  }
}

// ---------------------------------------------------------------------------
// Unbind
// ---------------------------------------------------------------------------

export function unbindAccount(): void {
  stopBridge();
  try {
    const accountsDir = join(DATA_DIR, 'accounts');
    if (existsSync(accountsDir)) rmSync(accountsDir, { recursive: true, force: true });
    // Reset sync buffer so a future bind starts clean.
    const bufPath = join(DATA_DIR, 'get_updates_buf');
    if (existsSync(bufPath)) rmSync(bufPath, { force: true });
    logger.info('Account unbound');
  } catch (err: any) {
    logger.error('unbindAccount failed', { error: err instanceof Error ? err.message : String(err) });
    throw err;
  }
  pushState('idle', '已解绑');
}

// ---------------------------------------------------------------------------
// Logs / test message
// ---------------------------------------------------------------------------

/** Tail the most recent bridge log file (UTC+8 daily rotation). */
export function tailLogs(maxLines = 120): string[] {
  const logDir = join(DATA_DIR, 'logs');
  let files: string[] = [];
  try {
    files = readdirSync(logDir).filter((f) => f.startsWith('bridge-') && f.endsWith('.log')).sort();
  } catch {
    return [];
  }
  if (files.length === 0) return [];
  const latest = files[files.length - 1];
  try {
    const content = readFileSync(join(logDir, latest), 'utf-8');
    const lines = content.split('\n').filter(Boolean);
    return lines.slice(-maxLines);
  } catch {
    return [];
  }
}

/**
 * Send a text message to the bound user (used by the "发送测试消息" button).
 * Requires a connected runtime — the context_token comes from the last
 * inbound message stored by send.ts.
 */
export async function sendTestMessage(text: string): Promise<{ ok: boolean; error?: string }> {
  if (!runtime) return { ok: false, error: '桥接未连接' };
  try {
    const { getLastTurnInfo } = await import('./main.js');
    const turn = getLastTurnInfo();
    if (!turn.contextToken || !turn.userId) {
      return { ok: false, error: '没有可用的 context_token，请先从微信发一条消息' };
    }
    await runtime.sender.sendText(turn.userId, turn.contextToken, text);
    return { ok: true };
  } catch (err: any) {
    const msg = err instanceof Error ? err.message : String(err);
    logger.warn('sendTestMessage failed', { error: msg });
    return { ok: false, error: msg };
  }
}

// Data dir bootstrap (mirrors upstream setup, safe to call repeatedly)
try { mkdirSync(DATA_DIR, { recursive: true }); } catch { /* ignore */ }
