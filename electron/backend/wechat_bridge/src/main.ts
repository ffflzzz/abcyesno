import { createInterface } from 'node:readline';
import process from 'node:process';
import { spawnSync } from 'node:child_process';
import { join, basename } from 'node:path';
import { unlinkSync, writeFileSync, readFileSync, mkdirSync } from 'node:fs';
import { homedir } from 'node:os';

import { WeChatApi } from './wechat/api.js';
import { saveAccount, loadLatestAccount, type AccountData } from './wechat/accounts.js';
import { startQrLogin, waitForQrScan } from './wechat/login.js';
import { createMonitor, type MonitorCallbacks } from './wechat/monitor.js';
import { createSender } from './wechat/send.js';
import { downloadImage, extractText, extractFirstImageUrl, extractFirstFileItem, downloadFile } from './wechat/media.js';
import { createSessionStore, type Session } from './session.js';
import { routeCommand, type CommandContext, type CommandResult } from './commands/router.js';
import { claudeQuery, type QueryOptions } from './claude/provider.js';
import { TurnRouter } from './claude/turn-router.js';
import { filterToolNoise } from './claude/tool-noise-filter.js';
import { loadConfig, saveConfig } from './config.js';
import { logger } from './logger.js';
import { DATA_DIR } from './constants.js';
import { MessageType, type WeixinMessage } from './wechat/types.js';
import { loadPendingQueue, savePendingQueue, type PendingItem } from './pending-queue.js';

// ---------------------------------------------------------------------------
// Optional abcyesno-Session bridge (injected by bridge.ts via setAbcStorage)
// ---------------------------------------------------------------------------
// Lets WeChat turns show up in the main-app sidebar. Null when running
// standalone (no storage injected), which is fine — the bridge still works
// for WeChat <-> agent, the difference is only the sidebar mirror.

type AbcSessionHelper = {
  ensureSession: (fromUserId: string, fromUserMasked: string) => Promise<string | null>;
  appendMessage: (sessionId: string | null, role: 'user' | 'assistant', content: string) => Promise<void>;
};

let abcSessionHelper: AbcSessionHelper | null = null;
export function setAbcSessionHelper(h: AbcSessionHelper): void {
  abcSessionHelper = h;
}

// ---------------------------------------------------------------------------
// Inbound interceptor (injected by bridge.ts, wired from Electron main)
// ---------------------------------------------------------------------------
// 2026-08-29 微信授权「原路返回」：agent 在微信驱动的 turn 里触发工具授权时，
// 主进程把授权请求转发到微信并把待决审批挂在这里注册的表里。用户回复
// 「批准/拒绝」时，拦截器消费这条消息作为审批决策，直接回传 gateway，
// 绝不能再当普通对话起一个 agent turn。
export type InboundInterceptor = (text: string, fromUserId: string) => boolean;

let inboundInterceptor: InboundInterceptor | null = null;
export function setInboundInterceptor(fn: InboundInterceptor | null): void {
  inboundInterceptor = fn;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const MAX_MESSAGE_LENGTH = 4000;

// Extensions eligible for auto-push when detected in Claude's response
const AUTO_PUSH_EXTENSIONS = new Set([
  '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg', '.ico',
  '.pdf', '.doc', '.docx', '.ppt', '.pptx', '.rtf',
  '.txt', '.md',
  '.csv', '.xlsx', '.xls',
  '.mp3', '.wav', '.m4a', '.mp4', '.mov',
]);

/** Extract local file paths from Claude's response text. */
function extractFilePathsFromText(text: string, cwd: string): string[] {
  const paths: string[] = [];
  // Match absolute paths (macOS/Linux), tilde paths, and Windows paths with a file extension
  const regex = /(?:\/(?:Users|home|tmp|var|etc)\/[^\s`'"()\[\]{}|<>]+\.\w+|~\/[^\s`'"()\[\]{}|<>]+\.\w+|[A-Za-z]:[\\\/][^\s`'"()\[\]{}|<>]+\.\w+)/g;
  let match: RegExpExecArray | null;
  while ((match = regex.exec(text)) !== null) {
    const raw = match[0];
    const resolved = raw.startsWith('~')
      ? raw.replace(/^~/, homedir())
      : raw;
    paths.push(resolved);
  }
  return paths;
}

/** Split text into blocks at paragraph boundaries (double newlines). */
function parseBlocks(text: string): string[] {
  return text.split(/\n\n+/).filter(block => block.length > 0);
}

/** Find a safe split point that won't break markdown formatting. */
function findSafeSplitPoint(text: string, maxLen: number): number {
  // Try newline first (preserves list items, paragraphs)
  let idx = text.lastIndexOf('\n', maxLen);
  if (idx >= maxLen * 0.3) return idx;

  // Try sentence-ending punctuation
  const sentenceEnd = /[。！？.!?]$/;
  for (let i = maxLen; i >= maxLen * 0.5; i--) {
    if (sentenceEnd.test(text.slice(i - 1, i))) return i;
  }

  // Try space (won't split mid-word or mid-markdown)
  idx = text.lastIndexOf(' ', maxLen);
  if (idx >= maxLen * 0.3) return idx;

  // Last resort: hard cut
  return maxLen;
}

/** Fallback: split a single oversized block at safe boundaries. */
function splitByNewline(text: string, maxLen: number): string[] {
  const chunks: string[] = [];
  let remaining = text;
  while (remaining.length > 0) {
    if (remaining.length <= maxLen) {
      chunks.push(remaining);
      break;
    }
    const splitIdx = findSafeSplitPoint(remaining, maxLen);
    chunks.push(remaining.slice(0, splitIdx));
    remaining = remaining.slice(splitIdx).replace(/^\n+/, '');
  }
  return chunks;
}

/**
 * Card-aware message splitter.
 * Splits at paragraph boundaries (double newlines) to keep cards intact,
 * falls back to newline-based splitting for oversized single blocks.
 */
function splitMessage(text: string, maxLen: number = MAX_MESSAGE_LENGTH): string[] {
  if (text.length <= maxLen) return [text];
  const blocks = parseBlocks(text);
  const chunks: string[] = [];
  let current = '';

  for (const block of blocks) {
    // Can this block fit into the current chunk?
    if (current.length === 0) {
      if (block.length <= maxLen) {
        current = block;
      } else {
        chunks.push(...splitByNewline(block, maxLen));
      }
    } else if (current.length + 2 + block.length <= maxLen) {
      current += '\n\n' + block;
    } else {
      // Current chunk is complete, start a new one
      chunks.push(current);
      if (block.length <= maxLen) {
        current = block;
      } else {
        chunks.push(...splitByNewline(block, maxLen));
        current = '';
      }
    }
  }
  if (current) chunks.push(current);
  return chunks;
}

/**
 * Wall-clock snapshot used to anchor the model's replies. The small flash
 * model will otherwise invent dates ("206 年 8 月 5 日") because it has no
 * real time source. We render in the host's local timezone (CST for our
 * users) — `Intl.DateTimeFormat` handles DST/region correctly so the model
 * gets a concrete, defensible string to quote.
 */
function formatNowForModel(now: Date = new Date()): string {
  try {
    const fmtDate = new Intl.DateTimeFormat('zh-CN', {
      timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      weekday: 'long',
    });
    const fmtTime = new Intl.DateTimeFormat('zh-CN', {
      timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    });
    return `${fmtDate.format(now)} ${fmtTime.format(now)}`;
  } catch {
    // Fallback if Intl is unavailable for some reason
    return now.toISOString();
  }
}

function promptUser(question: string, defaultValue?: string): Promise<string> {
  return new Promise((resolve) => {
    const rl = createInterface({ input: process.stdin, output: process.stdout });
    const display = defaultValue ? `${question} [${defaultValue}]: ` : `${question}: `;
    rl.question(display, (answer) => {
      rl.close();
      resolve(answer.trim() || defaultValue || '');
    });
  });
}

/** Open a file using the platform's default application (secure: uses spawnSync) */
function openFile(filePath: string): void {
  const platform = process.platform;
  let cmd: string;
  let args: string[];

  if (platform === 'darwin') {
    cmd = 'open';
    args = [filePath];
  } else if (platform === 'win32') {
    cmd = 'cmd';
    args = ['/c', 'start', '', filePath];
  } else {
    // Linux: try xdg-open
    cmd = 'xdg-open';
    args = [filePath];
  }

  const result = spawnSync(cmd, args, { stdio: 'ignore' });
  if (result.error) {
    logger.warn('Failed to open file', { cmd, filePath, error: result.error.message });
  }
}

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

async function runSetup(): Promise<void> {
  mkdirSync(DATA_DIR, { recursive: true });
  const QR_PATH = join(DATA_DIR, 'qrcode.png');

  console.log('正在设置...\n');

  // Loop: generate QR → display → poll for scan → handle expiry → repeat
  while (true) {
    const { qrcodeUrl, qrcodeId } = await startQrLogin();

    const isHeadlessLinux = process.platform === 'linux' &&
      !process.env.DISPLAY && !process.env.WAYLAND_DISPLAY;

    if (isHeadlessLinux) {
      // Headless Linux: display QR in terminal using qrcode-terminal
      try {
        const qrcodeTerminal = await import('qrcode-terminal');
        console.log('请用微信扫描下方二维码：\n');
        qrcodeTerminal.default.generate(qrcodeUrl, { small: true });
        console.log();
        console.log('二维码链接：', qrcodeUrl);
        console.log();
      } catch {
        logger.warn('qrcode-terminal not available, falling back to URL');
        console.log('无法在终端显示二维码，请访问链接：');
        console.log(qrcodeUrl);
        console.log();
      }
    } else {
      // macOS / Windows / GUI Linux: generate QR PNG and open with system viewer
      const QRCode = await import('qrcode');
      const pngData = await QRCode.toBuffer(qrcodeUrl, { type: 'png', width: 400, margin: 2 });
      writeFileSync(QR_PATH, pngData);

      openFile(QR_PATH);
      console.log('已打开二维码图片，请用微信扫描：');
      console.log(`图片路径: ${QR_PATH}\n`);
    }

    console.log('等待扫码绑定...');

    try {
      await waitForQrScan(qrcodeId);
      console.log('✅ 绑定成功!');
      break;
    } catch (err: any) {
      if (err.message?.includes('expired')) {
        console.log('⚠️ 二维码已过期，正在刷新...\n');
        continue;
      }
      throw err;
    }
  }

  // Clean up QR image
  try { unlinkSync(QR_PATH); } catch {
    logger.warn('Failed to clean up QR image', { path: QR_PATH });
  }

  const workingDir = await promptUser('请输入工作目录', join(homedir(), 'Documents', 'ClaudeCode'));
  const config = loadConfig();
  config.workingDirectory = workingDir;
  saveConfig(config);

  console.log('运行 npm run daemon -- start 启动服务');
}

// ---------------------------------------------------------------------------
// Daemon
// ---------------------------------------------------------------------------

export interface DaemonRuntime {
  accountId: string;
  api: WeChatApi;
  sender: ReturnType<typeof createSender>;
  session: Session;
  stop(): void;
}

/** Latest inbound turn info, used for proactive sends (test message). */
export const lastTurnInfo: { contextToken: string; userId: string } = {
  contextToken: '',
  userId: '',
};

export function getLastTurnInfo(): { contextToken: string; userId: string } {
  return lastTurnInfo;
}

// ---------------------------------------------------------------------------
// Pending proactive sends — queued when the iLink passive-reply window was
// closed (no fresh context_token), flushed on the next inbound user message.
//
// Why this exists (2026-08-27): iLink bots can only REPLY. The context_token
// from each inbound message goes stale within minutes and lives only in
// memory, so a desktop-initiated "notify me when done" that fires minutes
// later reliably fails with "没有可用的 context_token". The user kept sending
// activation messages and the agent kept failing — the two events must be
// CORRELATED in time, which the notify use case can never guarantee. So
// instead of failing, we park the message and deliver it the moment the user
// sends anything to the bot. Persisted across restarts (app restarts were
// the other major token-lost trigger).
// ---------------------------------------------------------------------------

const PENDING_SENDS_FILE = join(DATA_DIR, 'pending_sends.json');
const PENDING_SENDS_MAX = 10;
const PENDING_SENDS_TTL_MS = 24 * 60 * 60 * 1000;

function loadPendingSends(): { text: string; ts: number }[] {
  try {
    const parsed = JSON.parse(readFileSync(PENDING_SENDS_FILE, 'utf8'));
    if (!Array.isArray(parsed)) return [];
    const now = Date.now();
    return parsed
      .filter((x) => x && typeof x.text === 'string' && typeof x.ts === 'number' && now - x.ts < PENDING_SENDS_TTL_MS)
      .slice(0, PENDING_SENDS_MAX);
  } catch {
    return [];
  }
}

function savePendingSends(list: { text: string; ts: number }[]): void {
  try {
    mkdirSync(DATA_DIR, { recursive: true });
    writeFileSync(PENDING_SENDS_FILE, JSON.stringify(list), 'utf8');
  } catch (err) {
    logger.warn('savePendingSends failed', { error: err instanceof Error ? err.message : String(err) });
  }
}

export const pendingProactiveSends: { text: string; ts: number }[] = loadPendingSends();

/** Park a proactive send for later flush on the next inbound user message. */
export function queueProactiveSend(text: string): void {
  pendingProactiveSends.push({ text, ts: Date.now() });
  while (pendingProactiveSends.length > PENDING_SENDS_MAX) pendingProactiveSends.shift();
  const now = Date.now();
  while (pendingProactiveSends.length && now - pendingProactiveSends[0].ts > PENDING_SENDS_TTL_MS) {
    pendingProactiveSends.shift();
  }
  savePendingSends(pendingProactiveSends);
}

/**
 * Try to flush queued proactive sends with a freshly refreshed context_token.
 * Called from onMessage after lastTurnInfo is updated. On failure the batch
 * (failed item first) is restored so a later message retries it.
 */
export async function flushPendingSends(
  sender: ReturnType<typeof createSender>,
  freshToken: string,
): Promise<void> {
  if (!pendingProactiveSends.length || !freshToken || !lastTurnInfo.userId) return;
  const batch = pendingProactiveSends.splice(0, pendingProactiveSends.length);
  for (let i = 0; i < batch.length; i++) {
    try {
      await sender.sendText(lastTurnInfo.userId, freshToken, batch[i].text);
      logger.info('pending proactive send delivered', { index: i, queuedAt: batch[i].ts });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      logger.warn('pending proactive send failed, re-queueing remainder', { error: msg, index: i });
      pendingProactiveSends.push(...batch.slice(i));
      break;
    }
  }
  savePendingSends(pendingProactiveSends);
}

/**
 * Embeddable daemon runtime (used by abcyesno's Electron main process via
 * bridge.ts). Returns null when no WeChat account is bound yet.
 */
export async function createDaemonRuntime(): Promise<DaemonRuntime | null> {
  const config = loadConfig();
  const account = loadLatestAccount();

  if (!account) {
    logger.warn('createDaemonRuntime: no bound account');
    return null;
  }

  const api = new WeChatApi(account.botToken, account.baseUrl);
  const sessionStore = createSessionStore();
  const session: Session = sessionStore.load(account.accountId);

  // Fix: backfill session workingDirectory from config if it's still the default process.cwd()
  if (config.workingDirectory && session.workingDirectory === process.cwd()) {
    session.workingDirectory = config.workingDirectory;
    sessionStore.save(account.accountId, session);
  }

  // Fix: reset stale non-idle state on startup (e.g. after crash)
  if (session.state !== 'idle') {
    logger.warn('Resetting stale session state on startup', { state: session.state });
    session.state = 'idle';
    sessionStore.save(account.accountId, session);
  }

  const sender = createSender(api, account.accountId);
  const sharedCtx = { lastContextToken: '' };
  const activeControllers = new Map<string, AbortController>();

  // -- Message queue for serial processing --
  const messageQueue: WeixinMessage[] = [];
  let processingQueue = false;

  async function drainQueue(): Promise<void> {
    if (processingQueue) return;
    processingQueue = true;
    while (messageQueue.length > 0) {
      const msg = messageQueue.shift()!;
      await handleMessage(msg, account!, session, sessionStore, sender, config, sharedCtx, activeControllers, messageQueue);
    }
    processingQueue = false;
  }

  // -- Wire the monitor callbacks --

  /** Handle priority commands (/stop, /clear) immediately, bypassing the serial queue. */
  function handlePriorityCommand(msg: WeixinMessage): boolean {
    if (msg.message_type !== MessageType.USER || !msg.item_list) return false;
    const text = extractTextFromItems(msg.item_list);
    if (!text.startsWith('/stop') && !text.startsWith('/clear')) return false;
    if (session.state !== 'processing') return false;

    const ctrl = activeControllers.get(account!.accountId);
    if (ctrl) { ctrl.abort(); activeControllers.delete(account!.accountId); }
    session.state = 'idle';
    sessionStore.save(account!.accountId, session);

    if (text.startsWith('/stop')) {
      messageQueue.length = 0;
      sender.sendText(msg.from_user_id!, msg.context_token ?? '', '⏹ 已停止当前对话，排队中的消息已清空。').catch(() => {});
    }
    return true;
  }

  const callbacks: MonitorCallbacks = {
    onMessage: async (msg: WeixinMessage) => {
      // Track the latest inbound turn info so the embeddable API (bridge.ts)
      // can push proactive messages (test message) with a valid context_token.
      if (msg.message_type === MessageType.USER) {
        lastTurnInfo.contextToken = msg.context_token ?? lastTurnInfo.contextToken;
        if (msg.from_user_id) lastTurnInfo.userId = msg.from_user_id;
        // A user message refreshes the passive-reply window — deliver any
        // proactive sends that were queued while the window was closed.
        const freshToken = msg.context_token ?? lastTurnInfo.contextToken;
        if (pendingProactiveSends.length && freshToken) {
          // fire-and-forget: never block the normal message pipeline on a
          // queued-notification flush (each send is rate-limited anyway).
          flushPendingSends(sender, freshToken).catch((err) => {
            logger.warn('flushPendingSends threw', { error: err instanceof Error ? err.message : String(err) });
          });
        }
        // 2026-08-29 微信授权原路返回：待决审批的「批准/拒绝」回复在这里被
        // 消费（主进程回传 gateway），不再进入普通对话管线。
        if (msg.item_list && inboundInterceptor) {
          const interceptText = extractTextFromItems(msg.item_list);
          if (interceptText && inboundInterceptor(interceptText, msg.from_user_id ?? '')) {
            logger.info('Inbound message consumed by approval interceptor', {
              fromUserId: msg.from_user_id,
              text: interceptText.slice(0, 40),
            });
            return;
          }
        }
      }
      if (handlePriorityCommand(msg)) return;
      messageQueue.push(msg);
      drainQueue();
    },
    onSessionExpired: () => {
      logger.warn('Session expired, will keep retrying...');
      console.error('⚠️ 微信会话已过期，请重新运行 setup 扫码绑定');
    },
  };

  const monitor = createMonitor(api, callbacks);

  let stopped = false;
  const runPromise = monitor.run().catch((err) => {
    logger.error('Monitor crashed', { error: err instanceof Error ? err.message : String(err) });
  });

  logger.info('Daemon runtime started', { accountId: account.accountId });
  console.log(`已启动 (账号: ${account.accountId})`);

  return {
    accountId: account.accountId,
    api,
    sender,
    session,
    stop(): void {
      if (stopped) return;
      stopped = true;
      logger.info('Daemon runtime stopping...');
      monitor.stop();
    },
  };
}

/** CLI mode: run the daemon in-process until SIGINT/SIGTERM. */
async function runDaemon(): Promise<void> {
  const runtime = await createDaemonRuntime();
  if (!runtime) {
    console.error('未找到账号，请先运行 node dist/main.js setup');
    process.exit(1);
  }

  function shutdown(): void {
    logger.info('Shutting down...');
    runtime.stop();
    process.exit(0);
  }

  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);

  await new Promise<void>((resolve) => {
    const check = setInterval(() => {
      // resolve when stopped externally (keeps CLI testable)
      void check;
    }, 60_000);
    runtime.stop = new Proxy(runtime.stop, {
      apply(target, thisArg, args) {
        clearInterval(check);
        resolve();
        return Reflect.apply(target as any, thisArg, args as any);
      },
    });
  });
  runtime.stop();
}

// ---------------------------------------------------------------------------
// Message handling
// ---------------------------------------------------------------------------

async function handleMessage(
  msg: WeixinMessage,
  account: AccountData,
  session: Session,
  sessionStore: ReturnType<typeof createSessionStore>,
  sender: ReturnType<typeof createSender>,
  config: ReturnType<typeof loadConfig>,
  sharedCtx: { lastContextToken: string },
  activeControllers: Map<string, AbortController>,
  messageQueue: WeixinMessage[],
): Promise<void> {
  // Filter: only user messages with required fields
  if (msg.message_type !== MessageType.USER) return;
  if (!msg.from_user_id || !msg.item_list) return;
  if (account.userId && msg.from_user_id !== account.userId) return;

  const contextToken = msg.context_token ?? '';
  const fromUserId = msg.from_user_id;
  sharedCtx.lastContextToken = contextToken;

  // Mirror to the main-app sidebar: ensure an abcyesno session exists for
  // this WeChat user (idempotent — one session per user, stable across
  // app restarts via wx_session_map.json). No-op when running standalone.
  let abcSessionId: string | null = null;
  if (abcSessionHelper && fromUserId) {
    try {
      const masked = fromUserId.length > 8
        ? `${fromUserId.slice(0, 4)}****${fromUserId.slice(-4)}`
        : fromUserId.slice(0, 2) + '****';
      abcSessionId = await abcSessionHelper.ensureSession(fromUserId, masked);
    } catch (err: any) {
      logger.warn('ensureAbcSession failed', { error: err instanceof Error ? err.message : String(err) });
    }
  }

  // Flush any pending messages from prior rate-limit windows. User's new
  // message brings a fresh context_token, which resets the iLink 11-msg quota.
  await flushPending(account.accountId, fromUserId, contextToken, sender);

  // Extract text from items
  const userText = extractTextFromItems(msg.item_list);
  const imageItem = extractFirstImageUrl(msg.item_list);
  const fileItem = extractFirstFileItem(msg.item_list);

  // Drop non-command messages while processing (priority commands already handled upstream)
  if (session.state === 'processing' && !userText.startsWith('/')) {
    return;
  }

  // -- Command routing --

  if (userText.startsWith('/')) {
    const updateSession = (partial: Partial<Session>) => {
      Object.assign(session, partial);
      sessionStore.save(account.accountId, session);
    };

    const ctx: CommandContext = {
      accountId: account.accountId,
      session,
      updateSession,
      clearSession: () => sessionStore.clear(account.accountId),
      getChatHistoryText: (limit?: number) => sessionStore.getChatHistoryText(session, limit),
      text: userText,
    };

    const result: CommandResult = routeCommand(ctx);

    if (result.handled && result.reply) {
      await sender.sendText(fromUserId, contextToken, result.reply);
      return;
    }

    if (result.handled && result.claudePrompt) {
      await sendToClaude(
        result.claudePrompt, imageItem, fileItem, fromUserId, contextToken,
        account, session, sessionStore, sender, config, activeControllers,
      );
      return;
    }

    if (result.handled && result.sendFile) {
      await sender.sendFile(fromUserId, contextToken, result.sendFile);
      return;
    }

    if (result.handled) return;

    // Not handled, treat as normal message (fall through)
  }

  // -- Normal message -> Claude --

  if (!userText && !imageItem && !fileItem) {
    await sender.sendText(fromUserId, contextToken, '暂不支持此类型消息，请发送文字、语音、图片或文件');
    return;
  }

  // Mirror the inbound turn to the abcyesno session so the sidebar reflects
  // real activity. Use a compact placeholder when the message has only
  // media so the preview line still moves.
  if (abcSessionHelper && abcSessionId) {
    const userDisplay = userText || (imageItem ? '（图片）' : (fileItem ? '（文件）' : '（语音/其他）'));
    await abcSessionHelper.appendMessage(abcSessionId, 'user', userDisplay);
  }

  await sendToClaude(
    userText, imageItem, fileItem, fromUserId, contextToken,
    account, session, sessionStore, sender, config, activeControllers,
    abcSessionHelper, abcSessionId,
  );
}

function extractTextFromItems(items: NonNullable<WeixinMessage['item_list']>): string {
  return items.map((item) => extractText(item)).filter(Boolean).join('\n');
}

/**
 * Drain the pending message queue (messages that couldn't be delivered in a
 * prior rate-limit window). Called whenever a fresh user message arrives with
 * a new context_token. Each flush attempt stops at the first failure —
 * remaining items stay queued for the next user message.
 */
async function flushPending(
  accountId: string,
  toUserId: string,
  contextToken: string,
  sender: ReturnType<typeof createSender>,
): Promise<void> {
  const queue = loadPendingQueue(accountId);
  if (queue.length === 0) return;

  logger.info('Flushing pending queue', { accountId, pending: queue.length });
  const stillPending: PendingItem[] = [];

  for (const item of queue) {
    try {
      const chunks = splitMessage(item.text);
      for (const chunk of chunks) {
        await sender.sendText(toUserId, contextToken, chunk);
      }
    } catch (err) {
      logger.warn('Flush stopped at rate-limit, keeping remaining items queued', {
        accountId,
        flushed: queue.length - stillPending.length - 1,
        remaining: stillPending.length + 1,
        error: err instanceof Error ? err.message : String(err),
      });
      stillPending.push(item);
    }
  }

  savePendingQueue(accountId, stillPending);

  if (stillPending.length > 0 && stillPending.length === queue.length) {
    // Nothing got flushed this round — nudge the user.
    await sender
      .sendText(toUserId, contextToken, `⏳ 还有 ${stillPending.length} 条暂存消息未能推送，再发任意消息我会继续补发。`)
      .catch(() => {});
  }
}

async function sendToClaude(
  userText: string,
  imageItem: ReturnType<typeof extractFirstImageUrl>,
  fileItem: ReturnType<typeof extractFirstFileItem>,
  fromUserId: string,
  contextToken: string,
  account: AccountData,
  session: Session,
  sessionStore: ReturnType<typeof createSessionStore>,
  sender: ReturnType<typeof createSender>,
  config: ReturnType<typeof loadConfig>,
  activeControllers: Map<string, AbortController>,
  abcSessionHelper: AbcSessionHelper | null = null,
  abcSessionId: string | null = null,
): Promise<void> {
  // Set state to processing
  session.state = 'processing';
  sessionStore.save(account.accountId, session);

  // Create abort controller for this query so it can be cancelled by new messages
  const abortController = new AbortController();
  activeControllers.set(account.accountId, abortController);

  // Flush timer for streaming text to WeChat during query (declared here for finally cleanup)
  let flushTimer: ReturnType<typeof setInterval> | undefined;

  // Record user message in chat history
  sessionStore.addChatMessage(session, 'user', userText || '(图片)');

  // Start typing indicator (keepalive until stopTyping is called)
  const stopTyping = sender.startTyping(fromUserId, contextToken);

  try {
    // Download image if present
    let images: QueryOptions['images'];
    if (imageItem) {
      const base64DataUri = await downloadImage(imageItem);
      if (base64DataUri) {
        const matches = base64DataUri.match(/^data:([^;]+);base64,(.+)$/);
        if (matches) {
          images = [
            {
              type: 'image',
              source: {
                type: 'base64',
                media_type: matches[1],
                data: matches[2],
              },
            },
          ];
        }
      }
    }

    // Download file if present
    let prompt = userText || '请分析这张图片';
    if (fileItem) {
      const filePath = await downloadFile(fileItem);
      if (filePath) {
        const fileName = fileItem.file_item?.file_name || basename(filePath);
        prompt = userText
          ? `${userText}\n\n用户发送了文件: ${fileName}\n文件已保存到: ${filePath}\n请先读取这个文件再回答。`
          : `用户发送了文件: ${fileName}\n文件已保存到: ${filePath}\n请读取这个文件并总结其内容。`;
      }
    }

    let anySent = false;
    let lastSentTime = Date.now();
    let pendingRetry: { text: string; role: 'interstitial' | 'final' } | null = null;

    // Serial promise chain — each emit appends to the chain, no flags needed
    let flushChain: Promise<void> = Promise.resolve();

    // ── Self-correction dedup ────────────────────────────────────────
    // When the small model (Agnes 2.5-flash) gets confused it loops on
    // "哦我理解错了" / "刚才那个回答确实..." and ends up sending the same
    // answer twice with cosmetic edits. Filter out an emit that overlaps
    // heavily with the very recent prior emit — but only inside a short
    // window so we don't drop legit re-statements hours later.
    const DEDUP_WINDOW_MS = 1500;
    const DEDUP_SIMILARITY = 0.8;
    let lastEmitRecord: { text: string; ts: number } | null = null;

    function normalizeForSim(s: string): string {
      return s.replace(/\s+/g, '').toLowerCase();
    }

    function bigramJaccard(a: string, b: string): number {
      if (a === b) return 1;
      if (a.length < 2 || b.length < 2) return 0;
      const aCounts = new Map<string, number>();
      for (let i = 0; i < a.length - 1; i++) {
        const k = a.slice(i, i + 2);
        aCounts.set(k, (aCounts.get(k) || 0) + 1);
      }
      let inter = 0;
      for (let i = 0; i < b.length - 1; i++) {
        const k = b.slice(i, i + 2);
        const c = aCounts.get(k);
        if (c && c > 0) {
          inter++;
          if (c === 1) aCounts.delete(k);
          else aCounts.set(k, c - 1);
        }
      }
      const aSet = a.length - 1;
      const bSet = b.length - 1;
      const denom = aSet + bSet - inter;
      return denom > 0 ? inter / denom : 0;
    }

    function isSelfCorrectionDuplicate(text: string): boolean {
      if (!lastEmitRecord) return false;
      const now = Date.now();
      if (now - lastEmitRecord.ts > DEDUP_WINDOW_MS) return false;
      const a = normalizeForSim(lastEmitRecord.text);
      const b = normalizeForSim(text);
      if (!a || !b) return false;
      if (a === b) return true;
      // Strong prefix match: new text is a near-cousin of prior (model
      // self-corrected and rewrote the head with a minor meta-comment).
      const minLen = Math.min(a.length, b.length);
      const maxLen = Math.max(a.length, b.length);
      if (minLen >= 16 && maxLen / minLen < 1.4) {
        const headLen = Math.floor(minLen * 0.7);
        if (headLen >= 16) {
          const aHead = a.slice(0, headLen);
          const bHead = b.slice(0, headLen);
          if (bigramJaccard(aHead, bHead) >= DEDUP_SIMILARITY) return true;
        }
      }
      // Generic fallback: high overall bigram overlap
      return bigramJaccard(a, b) >= DEDUP_SIMILARITY;
    }

    function emitText(text: string, role: 'interstitial' | 'final'): void {
      if (!text.trim()) return;

      // Drop self-correction duplicates BEFORE we commit to flushing.
      // Logged so it's visible in bridge-*.log when investigating.
      if (isSelfCorrectionDuplicate(text)) {
        logger.info('dropped self-correction duplicate', {
          role,
          length: text.length,
          preview: text.slice(0, 60),
        });
        return;
      }
      lastEmitRecord = { text, ts: Date.now() };

      // 若上一次发送失败留下了 pendingRetry，先用它原本的 role 单独补发，
      // 不要和当前 role 的文本合并（避免 interstitial 内容混进 final 答案）。
      if (pendingRetry) {
        const stuck = pendingRetry;
        pendingRetry = null;
        scheduleSend(stuck.text, stuck.role);
      }

      scheduleSend(text, role);
    }

    function scheduleSend(text: string, role: 'interstitial' | 'final'): void {
      if (!text.trim()) return;
      flushChain = flushChain.then(async () => {
        const chunks = splitMessage(text);
        for (let i = 0; i < chunks.length; i++) {
          try {
            await sender.sendText(fromUserId, contextToken, chunks[i]);
          } catch (err) {
            pendingRetry = { text: chunks.slice(i).join('\n\n'), role };
            logger.warn('emitText send failed, content retained for retry', {
              role,
              error: err instanceof Error ? err.message : String(err),
              retainedChunks: chunks.length - i,
            });
            return;
          }
        }
        anySent = true;
        lastSentTime = Date.now();
      });
    }

    const router = new TurnRouter((msg) => emitText(filterToolNoise(msg.text), msg.role));

    // Safety net: send keepalive if nothing was sent for 5 minutes
    const SILENCE_WARNING_MS = 5 * 60 * 1000;
    // 2026-08-31 文案诚实化：去掉"马上就好/一分钟搞定"式承诺（曾出现任务
    // 26 分钟无进展仍每 5 分钟说"马上出结果"），全部改为不承诺时长的中性
    // 表述；超过 15 分钟的静默追加告知式文案，用户可自行决定是否打断。
    const SILENCE_MESSAGES = [
      '我还在处理中，这个问题有点复杂，请再稍等一下',
      '还在后台全力跑着，任务量比较大，完成后立刻发你',
      '任务比想象的复杂一些，还在处理中，请再等等',
      '正在处理中，还没结束，好了会第一时间发你',
      '我在认真思考这个问题，请再稍等一会儿',
      '还在跑，这部分确实需要一些时间',
      '仍在处理中，目前还没有最终结果，请稍候',
    ];
    const SILENCE_LONG_MESSAGES = [
      '任务已经跑了挺久（超过15分钟），还在继续处理；如果你着急，可以直接发"停止"中断当前任务',
      '还在后台处理中，已经超过15分钟了；不想等的话发"停止"可以中断',
    ];
    let silenceCount = 0;
    flushTimer = setInterval(() => {
      if (Date.now() - lastSentTime > SILENCE_WARNING_MS) {
        silenceCount += 1;
        const pool = silenceCount >= 3 ? SILENCE_LONG_MESSAGES : SILENCE_MESSAGES;
        const msg = pool[Math.floor(Math.random() * pool.length)];
        sender.sendText(fromUserId, contextToken, msg).catch(() => {});
        lastSentTime = Date.now();
      }
    }, 2000);

    const queryOptions: QueryOptions = {
      prompt,
      cwd: (session.workingDirectory || config.workingDirectory).replace(/^~/, homedir()),
      // One stable Hermes thread per WeChat user, so each user gets their
      // own conversation history (and the abcyesno sidebar can group them).
      threadId: `wx-${fromUserId}`,
      resume: session.sdkSessionId,
      model: session.model,
      systemPrompt: [
        // Inject the real wall clock so the model can answer date/time
        // questions honestly. Without this it invents dates like "206 年
        // 8 月 5 日" or "8 月 25 号" because it has no real source.
        `当前时间：${formatNowForModel()}`,
        '你正在通过微信与用户对话，不是在终端里。不要让用户去终端操作。如果用户需要文件，直接输出文件地址就行，会自动识别解析推送文件到用户的微信中。',
        // Anti self-correction loop: the small model likes to reply, then
        // criticize its own reply, then reply again. Tell it to send the
        // final answer only — no meta-commentary on the previous draft.
        '回复必须一次性给到最终答案，不要"我理解错了"式的自我反思重写，不要在答案前后追加"刚才那个回答确实..."的元评论。',
        '日期、时间、星期、电话号码、身份证号、版本号、引用的数字等一切需要精确性的内容，必须严格基于上文提供的"当前时间"或用户给出的真实数据；不知道就说不知道，不要凭印象编造。',
        config.systemPrompt,
      ].filter(Boolean).join('\n'),
      abortController,
      images,
      onText: (delta: string) => {
        router.onText(delta);
      },
      onTurnEnd: (stopReason: string) => {
        router.onTurnEnd(stopReason);
      },
    };

    let result = await claudeQuery(queryOptions);

    // If resume failed (e.g. corrupted session), retry without resume
    if (result.error && queryOptions.resume) {
      logger.warn('Resume failed, retrying without resume', { error: result.error, sessionId: queryOptions.resume });
      queryOptions.resume = undefined;
      session.sdkSessionId = undefined;
      sessionStore.save(account.accountId, session);
      const retryResult = await claudeQuery(queryOptions);
      Object.assign(result, retryResult);
    }

    // Stop periodic flush, drain router (final 先于 interstitial), wait for queued sends
    clearInterval(flushTimer);
    router.drain();
    await flushChain;

    // 兜底重试：drain() 的最后一次发送若失败，pendingRetry 会卡住没有下一个 emit 接力。
    // 这里做有上限的终态重试，避免静默丢内容（commit d6d7d62 的 "never silently drop" 保证）。
    const MAX_TERMINAL_ATTEMPTS = 3;
    let terminalAttempt = 0;
    while (pendingRetry && terminalAttempt < MAX_TERMINAL_ATTEMPTS) {
      const stuck: { text: string; role: 'interstitial' | 'final' } = pendingRetry;
      pendingRetry = null;
      terminalAttempt++;
      const delayMs = terminalAttempt * 5_000;  // 5s, 10s, 15s
      logger.warn(`terminal retry ${terminalAttempt}/${MAX_TERMINAL_ATTEMPTS} for stranded content`, {
        role: stuck.role,
        delayMs,
        textLength: stuck.text.length,
      });
      await new Promise(r => setTimeout(r, delayMs));

      const chunks = splitMessage(stuck.text);
      let failed = false;
      for (let i = 0; i < chunks.length; i++) {
        try {
          await sender.sendText(fromUserId, contextToken, chunks[i]);
          anySent = true;
          lastSentTime = Date.now();
        } catch (err) {
          pendingRetry = { text: chunks.slice(i).join('\n\n'), role: stuck.role };
          logger.warn('terminal retry failed', {
            attempt: terminalAttempt,
            error: err instanceof Error ? err.message : String(err),
          });
          failed = true;
          break;
        }
      }
      if (!failed) break;
    }

    if (pendingRetry) {
      // Park the stranded content to the pending queue. It will be flushed
      // automatically when the user's next message brings a fresh context_token
      // (which resets the iLink 11-msg quota).
      const queue = loadPendingQueue(account.accountId);
      queue.push({
        text: pendingRetry.text,
        role: pendingRetry.role,
        queuedAt: Date.now(),
      });
      savePendingQueue(account.accountId, queue);
      logger.warn('content parked to pending queue', {
        role: pendingRetry.role,
        textLength: pendingRetry.text.length,
        queueSize: queue.length,
      });
      await sender
        .sendText(fromUserId, contextToken, '⏳ 部分内容因微信单次推送上限暂存，下次你回复任意消息时自动补发。')
        .catch(() => {});
      pendingRetry = null;
    }

    // Send result back to WeChat
    if (result.text) {
      if (result.error) {
        logger.warn('Claude query had error but returned text, using text', { error: result.error });
      }
      sessionStore.addChatMessage(session, 'assistant', result.text);
      // Mirror the assistant turn to the abcyesno session. Use the full
      // result.text (not just the streamed chunks) so the sidebar preview
      // reflects the complete answer. This is the same content that was
      // either streamed as deltas or sent verbatim in the !anySent branch.
      if (abcSessionHelper && abcSessionId) {
        await abcSessionHelper.appendMessage(abcSessionId, 'assistant', result.text);
      }
      // If nothing was streamed at all (e.g. streaming not supported), send full text now
      if (!anySent) {
        const chunks = splitMessage(result.text);
        for (const chunk of chunks) {
          await sender.sendText(fromUserId, contextToken, chunk);
        }
      }
    } else if (result.error) {
      logger.error('Claude query error', { error: result.error });
      await sender.sendText(fromUserId, contextToken, 'Claude 处理请求时出错，请稍后重试。');
    } else if (!anySent) {
      await sender.sendText(fromUserId, contextToken, 'Claude 无返回内容（可能因权限被拒而终止）');
    }

    // Update session with new SDK session ID
    session.sdkSessionId = result.sessionId || undefined;
    session.state = 'idle';
    sessionStore.save(account.accountId, session);

    // Auto-push deliverable files mentioned in Claude's response
    if (result.text) {
      const cwd = (session.workingDirectory || config.workingDirectory).replace(/^~/, homedir());
      const detectedPaths = extractFilePathsFromText(result.text, cwd);
      const { existsSync } = await import('node:fs');
      const { extname } = await import('node:path');
      const pushable = detectedPaths.filter(f => {
        const ext = extname(f).toLowerCase();
        return AUTO_PUSH_EXTENSIONS.has(ext) && existsSync(f);
      });
      if (pushable.length > 0) {
        const failedFiles: string[] = [];
        for (const filePath of pushable) {
          try {
            await sender.sendFile(fromUserId, contextToken, filePath);
          } catch {
            failedFiles.push(filePath);
          }
        }
        if (failedFiles.length > 0) {
          // Server-side rate limit requires longer cooldown (observed ret:-2 even after 9s backoff)
          for (let attempt = 0; attempt < 3; attempt++) {
            const delay = (attempt + 1) * 15_000;
            logger.warn(`Rate-limited, retrying ${failedFiles.length} file(s) in ${delay / 1000}s (attempt ${attempt + 1}/3)`);
            await new Promise(r => setTimeout(r, delay));
            const stillFailed: string[] = [];
            for (const filePath of failedFiles) {
              try {
                await sender.sendFile(fromUserId, contextToken, filePath);
              } catch {
                stillFailed.push(filePath);
              }
            }
            if (stillFailed.length === 0) break;
            failedFiles.length = 0;
            failedFiles.push(...stillFailed);
          }
          if (failedFiles.length > 0) {
            logger.error('File delivery failed after all retries', { files: failedFiles });
            await sender.sendText(fromUserId, contextToken, `文件推送失败（服务端限频），请稍后重试。`).catch(() => {});
          }
        }
      }
    }
  } catch (err) {
    const isAbort = err instanceof Error && (err.name === 'AbortError' || err.message.includes('abort'));
    if (isAbort) {
      // Query was cancelled by a new incoming message — exit silently
      logger.info('Claude query aborted by new message');
    } else {
      const errorMsg = err instanceof Error ? err.message : String(err);
      logger.error('Error in sendToClaude', { error: errorMsg });
      await sender.sendText(fromUserId, contextToken, '处理消息时出错，请稍后重试。');
    }
    session.state = 'idle';
    sessionStore.save(account.accountId, session);
  } finally {
    clearInterval(flushTimer);
    stopTyping();
    // Clean up the abort controller if it's still ours
    if (activeControllers.get(account.accountId) === abortController) {
      activeControllers.delete(account.accountId);
    }
  }
}

// ---------------------------------------------------------------------------
// CLI entry — only active in standalone CLI mode. When bundled into
// abcyesno (entry = bridge.ts), this file is imported as a library and the
// footer must not auto-start anything.
// ---------------------------------------------------------------------------

if (process.env.WCC_CLI_MODE === '1') {
  const command = process.argv[2];

  if (command === 'setup') {
    runSetup().catch((err) => {
      logger.error('Setup failed', { error: err instanceof Error ? err.message : String(err) });
      console.error('设置失败:', err);
      process.exit(1);
    });
  } else {
    // 'start' or no argument
    runDaemon().catch((err) => {
      logger.error('Daemon start failed', { error: err instanceof Error ? err.message : String(err) });
      console.error('启动失败:', err);
      process.exit(1);
    });
  }
}
