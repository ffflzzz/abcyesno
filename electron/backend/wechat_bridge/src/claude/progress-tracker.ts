/**
 * 进度观察器 —— 把 AG-UI 的 CUSTOM 事件翻译成微信里能读的短句。
 *
 * 背景（2026-09-01 修）：微信桥的 `handleAgUiEvent` 只认 TEXT_MESSAGE_* 和
 * RUN_*，`CUSTOM` 一律 `default: break` 丢掉。于是只要 agent 调 langgraph_agent
 * 跑长任务，外层的文本流就是空的 —— 微信侧除了一句「稍后再等」的保活文案
 * 什么都看不到。本模块负责把那条被丢掉的 CUSTOM 通道接回来。
 *
 * 职责边界：
 *  - 只做「事件 → 短句」的翻译 + 去重 / 节流 / 累计，**不做任何 I/O**。
 *  - 推送由调用方（main.ts）拿到 ProgressLine 后走既有的 emitText 链路。
 *  - 送达结果由调用方回写（markDelivered / noteSendFailure），用于构造
 *    「你不在的时候发生了什么」的补发摘要。
 *
 * 纯逻辑、无依赖，可被 node:test 直接单测（见 src/tests/progress-tracker.test.ts）。
 */

export type ProgressKind =
  | 'started'
  | 'progress'
  | 'artifact'
  | 'approval'
  | 'error'
  | 'done';

export interface ProgressEntry {
  ts: number;
  kind: ProgressKind;
  /** 去重键：同一节点同一状态在一个 TTL 内只推送一次 */
  key: string;
  text: string;
  /** 是否绕过节流立刻推送（审批 / 错误 / 完成 / 产物） */
  urgent: boolean;
  /** 是否被选中推送（false = 被去重/节流挡下，仍记在账上供 /进度 与补发摘要用） */
  emitted: boolean;
  /** 是否已成功送达微信 */
  delivered: boolean;
  /** 是否尝试过推送但失败（回复窗口关闭的判据） */
  failed: boolean;
}

export interface ProgressLine {
  text: string;
  entry: ProgressEntry;
}

interface Humanized {
  kind: ProgressKind;
  key: string;
  text: string;
  urgent: boolean;
  /** 只更新「当前步骤」、不进聊天流的事件（如 workflow.trace / 完成态进度） */
  trackStep?: string;
  /** trace 事件带的原始节点名，仅作 currentStep 的兜底来源 */
  trackNode?: string;
  /** 只记账、永不推送的事件（工具调用行：/进度 与 keepalive 看得到，聊天流不出现） */
  silent?: boolean;
  /** reasoning delta 文本：追加进思考缓冲区，攒够或到边界再整句推送 */
  trackThink?: string;
  /** 一整块现成的思考文本（reasoning.snapshot）：直接取尾部推送 */
  flushThink?: string;
  /** 阶段切换点：把思考缓冲区里攒的内容整句 flush 出去 */
  flushBuf?: boolean;
}

// ---------------------------------------------------------------------------
// 调参
// ---------------------------------------------------------------------------

const MAX_ENTRIES = 80;
/** 同一 key 多久内不重复推送 */
const DEDUP_TTL_MS = 45_000;
/** 非紧急进度两条之间的最短间隔 */
const THROTTLE_MS = 20_000;
/**
 * 单轮最多推送多少条进度。iLink 每个 context_token 只有 ~11 条被动回复额度，
 * 进度刷太猛会把最终答案挤掉，所以这里刻意压得很紧；被挡下的进度仍然记在
 * 账上，由 /进度 和「下一条消息时的补发摘要」兜住。
 */
const MAX_EMITS_PER_RUN = 6;
const DIGEST_LIMIT = 8;
const REPORT_LIMIT = 10;
/** 补发摘要只回看这么久以内的进度 */
const DIGEST_LOOKBACK_MS = 6 * 60 * 60 * 1000;

/**
 * 工具参数摘要时优先取哪些键 —— 覆盖常见的搜索/读写/执行类参数名。
 * 找不到命中时退回「第一个像人话的字符串值」，不做任何 per-tool 硬编码表。
 */
const ARG_KEY_PRIORITY = [
  'query', 'q', 'keyword', 'search', 'topic', 'question',
  'url', 'command', 'cmd', 'script',
  'path', 'file', 'filename', 'filepath',
  'prompt', 'task', 'input', 'text', 'content', 'code', 'pattern', 'message',
];

/** Hermes 的 tool.start 有时把整个 payload 当 args 发，真正参数藏在这类包装键里 */
const ARG_WRAPPER_KEYS = ['arguments', 'args', 'input', 'parameters', 'kwargs', 'params'];
/** 回执类键/值（tool_id、call_xxx…）没有任何人话价值，摘要时一律跳过 */
const ARG_SKIP_KEYS = new Set(['id', 'tool_id', 'call_id', 'name', 'type', 'role', 'session_id', 'status']);
const ARG_JUNK_VALUE_RE = /^(call_|tool_|msg-|tc-|sess-)/i;

/** 💭 思考推送：一句话最多带多少字符（取尾部，最新的想法最值钱） */
const THINK_LINE_CHARS = 110;
/** 思考缓冲区攒到这么多字符还没遇到边界，就先 flush 一次（防单次思考过长无推送） */
const THINK_OVERFLOW_CHARS = 600;

// ---------------------------------------------------------------------------
// 小工具
// ---------------------------------------------------------------------------

function truncate(s: string, max: number): string {
  const t = s.replace(/\s+/g, ' ').trim();
  return t.length <= max ? t : `${t.slice(0, max - 1)}…`;
}

/** 取尾部：思考内容「最新的想法」最值钱，超长时只留最后 N 个字符。 */
function truncateTail(s: string, max: number): string {
  const t = s.replace(/\s+/g, ' ').trim();
  return t.length <= max ? t : `…${t.slice(-(max - 1))}`;
}

/** base64 / dataURL / 超长无空格串 —— 摘要里出现这种就是乱码，直接跳过 */
function looksLikeBinary(s: string): boolean {
  return /^data:/i.test(s) || /^[A-Za-z0-9+/=\s]{120,}$/.test(s);
}

/**
 * 把工具调用的 args JSON 缩成一行人话：优先按 ARG_KEY_PRIORITY 取键，
 * 否则取第一个「像人话」的字符串/数值；解析失败就截原始文本。
 * Hermes 的 tool.start 偶尔把整个 payload 当 args（此时真参数在
 * arguments/args 包装键里，且混着 call_id 这类回执），这里统一剥掉。
 */
function summarizeArgs(argsText?: unknown): string {
  const raw = String(argsText || '').trim();
  if (!raw) return '';
  try {
    let obj: unknown = JSON.parse(raw);
    if (obj === null || typeof obj !== 'object') return truncate(String(obj), 48);
    if (Array.isArray(obj)) {
      const first = obj[0];
      if (typeof first === 'string' && !looksLikeBinary(first)) return truncate(first, 48);
      return truncate(JSON.stringify(obj), 48);
    }
    let rec = obj as Record<string, unknown>;
    // 剥包装：真参数藏在 arguments/args/... 里时下钻一层
    for (const k of ARG_WRAPPER_KEYS) {
      const inner = rec[k];
      if (inner && typeof inner === 'object' && !Array.isArray(inner)) {
        rec = inner as Record<string, unknown>;
        break;
      }
    }
    for (const k of ARG_KEY_PRIORITY) {
      const val = rec[k];
      if (typeof val === 'string' && val.trim() && !looksLikeBinary(val) && !ARG_JUNK_VALUE_RE.test(val)) {
        return truncate(val, 48);
      }
    }
    for (const [k, val] of Object.entries(rec)) {
      if (ARG_SKIP_KEYS.has(k)) continue;
      if (typeof val === 'string' && val.trim() && !looksLikeBinary(val) && !ARG_JUNK_VALUE_RE.test(val)) {
        return truncate(val, 48);
      }
      if (typeof val === 'number' || typeof val === 'boolean') return truncate(String(val), 48);
    }
    return '';
  } catch {
    return truncate(raw, 48);
  }
}

function shortPath(p?: string): string {
  if (!p) return '';
  const norm = String(p).replace(/\\/g, '/');
  const idx = norm.lastIndexOf('/');
  return idx >= 0 ? norm.slice(idx + 1) : norm;
}

function formatDuration(ms: number): string {
  const totalSec = Math.max(0, Math.round(ms / 1000));
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  if (min <= 0) return `${sec} 秒`;
  return `${min} 分 ${sec} 秒`;
}

// ---------------------------------------------------------------------------
// 事件 → 短句（纯函数，单测主入口）
// ---------------------------------------------------------------------------

/**
 * 把一个 AG-UI `CUSTOM` 事件翻译成一条人话。返回 null 表示「这条不用管」
 * （拓扑、浅层 thinking 指示器、工具原始输出等噪音）；返回 text='' 的事件
 * 走 track/flush 旁路（trace 节点、思考缓冲、阶段切换点、silent 记账）。
 */
export function humanizeEvent(
  ev: { name?: string; value?: unknown } | null | undefined,
): Humanized | null {
  const name = String(ev?.name || '');
  if (!name) return null;
  const v = (ev?.value && typeof ev.value === 'object' ? ev.value : {}) as Record<string, any>;

  if (name.startsWith('workflow.')) {
    switch (name) {
      // 图拓扑，给前端画 DAG 用的，进聊天流纯属噪音
      case 'workflow.graph':
        return null;

      case 'workflow.started':
        return { kind: 'started', key: 'wf:started', text: '🚀 任务已开始', urgent: false };

      // 节点级 trace 是**不去重**的（series 模式会重复跑同一节点），只拿来
      // 更新「当前节点」，绝不逐条推送。
      case 'workflow.trace': {
        const node = String(v.node || '');
        if (!node) return null;
        return { kind: 'progress', key: `trace:${node}`, text: '', urgent: false, trackNode: node };
      }

      case 'workflow.progress': {
        const status = String(v.status || '');
        const stepId = String(v.step_id || v.node || '');
        const label = String(v.stage || stepId || '处理中');
        const completed = Number(v.completed || 0);
        const total = Number(v.total || 0);
        const counter = completed > 0 && total > 0 ? `${completed}/${total} ` : '';
        if (status === 'done') {
          return {
            kind: 'progress',
            key: `done:${stepId}`,
            text: `✅ ${counter}${label} 完成`,
            urgent: false,
            trackStep: label,
          };
        }
        return {
          kind: 'progress',
          key: `run:${stepId}`,
          text: `⏳ ${counter}${label}`,
          urgent: false,
          trackStep: `${counter}${label}`.trim(),
        };
      }

      case 'workflow.artifact': {
        const label = String(v.label || v.id || '产物');
        const file = shortPath(v.path);
        return {
          kind: 'artifact',
          key: `artifact:${String(v.id || label)}`,
          text: file ? `📦 ${label}：${file}` : `📦 ${label} 已生成`,
          urgent: true,
        };
      }

      case 'workflow.approval': {
        const label = truncate(String(v.label || v.message || '需要确认'), 120);
        return {
          kind: 'approval',
          key: `approval:${String(v.gate_id || v.node || label)}`,
          text: `🙋 需要你确认：${label}\n回复「批准」继续，回复「拒绝」中止。`,
          urgent: true,
        };
      }

      case 'workflow.error': {
        const msg = truncate(String(v.message || '未知错误'), 200);
        return {
          kind: 'error',
          key: `error:${String(v.node || '')}:${msg.slice(0, 40)}`,
          text: `⚠️ ${msg}`,
          urgent: true,
        };
      }

      case 'workflow.done': {
        const status = String(v.status || 'done');
        if (status === 'rejected') {
          return { kind: 'done', key: 'wf:done', text: '⏹ 已按你的决定中止', urgent: true };
        }
        if (status === 'timeout') {
          return { kind: 'done', key: 'wf:done', text: '⌛ 审批等待超时，任务已中止', urgent: true };
        }
        if (status === 'done') {
          return { kind: 'done', key: 'wf:done', text: '✅ 任务完成', urgent: true };
        }
        return {
          kind: 'done',
          key: 'wf:done',
          text: `❌ 任务失败${v.error ? `：${truncate(String(v.error), 160)}` : ''}`,
          urgent: true,
        };
      }

      default:
        return null;
    }
  }

  // ── 思考内容（2026-09-03 二次反馈）──────────────────────────────
  // 用户反馈：「工具调用本身也是无意义的，应该返回 agent 拿到工具结果之后
  // 的思考内容，让用户知道他在想啥」。所以：
  //  - reasoning.delta（reasoning 模型的真实思考 token，前端 ReasoningBlock
  //    同源）→ 追加进缓冲区，攒到阶段切换点整句 flush 成 💭。
  //  - thinking.delta 是浅层指示器（可能和 reasoning 重复），不进缓冲。
  //  - reasoning.snapshot 是一次性兜底快照，直接取尾部推。
  if (name === 'reasoning.delta') {
    const text = typeof v.text === 'string' ? v.text : '';
    if (!text.trim()) return null;
    return { kind: 'progress', key: 'think:buf', text: '', urgent: false, trackThink: text };
  }

  if (name === 'reasoning.snapshot') {
    const text = typeof v.text === 'string' ? v.text : '';
    if (!text.trim()) return null;
    return { kind: 'progress', key: 'think:buf', text: '', urgent: false, flushThink: text };
  }

  // stream.phase 现在唯一的用处：标记「思考结束了」（切到 tool_executing /
  // text_generating），把缓冲区里攒的思考整句放出去。它本身永远不推送。
  if (name === 'stream.phase') {
    const phase = String(v.phase || '');
    if (phase === 'thinking') return null; // 继续攒
    return { kind: 'progress', key: 'phase', text: '', urgent: false, flushBuf: true };
  }

  // ── 工具调用：只记账、不推送 ──────────────────────────────────────
  // 用户反馈工具调用行本身没信息量（真正想看的是思考），所以降级为 silent：
  // /进度 与 keepalive 仍能看到当前在跑什么，但聊天流里不再出现。
  if (name === 'tool.call') {
    const toolName = String(v.toolName || 'tool');
    const excerpt = summarizeArgs(v.argsText);
    return {
      kind: 'progress',
      key: `tool:${toolName}:${excerpt}`,
      text: excerpt ? `🔧 ${toolName}：${excerpt}` : `🔧 ${toolName}`,
      urgent: false,
      silent: true,
      trackStep: excerpt ? `${toolName}（${excerpt}）` : toolName,
    };
  }

  if (name === 'tool.error') {
    const toolName = String(v.toolName || 'tool');
    return {
      kind: 'error',
      key: `toolerr:${toolName}`,
      text: `⚠️ ${toolName} 执行失败`,
      urgent: true,
    };
  }

  return null;
}

// ---------------------------------------------------------------------------
// Per-thread 累加器
// ---------------------------------------------------------------------------

export class ProgressTracker {
  readonly threadId: string;

  private entries: ProgressEntry[] = [];
  private lastKeyAt = new Map<string, number>();
  private lastEmitAt = 0;
  private emitCount = 0;
  private sendFailed = false;

  /** 最近一次 workflow.progress 报的「N/M 步骤名」——最像「现在在干嘛」的东西 */
  private currentStep = '';
  /** workflow.trace 给的原始节点名，仅当 currentStep 为空时兜底 */
  private currentNode = '';
  /** reasoning.delta 攒的思考缓冲，阶段切换点整句 flush 成 💭 */
  private thinkBuf = '';
  private startedAt = Date.now();
  lastActivityAt = Date.now();

  constructor(threadId: string) {
    this.threadId = threadId;
  }

  /** 新一轮对话开始时调用，清掉上一轮的状态。 */
  reset(): void {
    this.entries = [];
    this.lastKeyAt.clear();
    this.lastEmitAt = 0;
    this.emitCount = 0;
    this.sendFailed = false;
    this.currentStep = '';
    this.currentNode = '';
    this.thinkBuf = '';
    this.startedAt = Date.now();
    this.lastActivityAt = Date.now();
  }

  /** 当前步骤的可读文案；没有则用 trace 节点名兜底。 */
  currentStepText(): string {
    return this.currentStep || this.currentNode || '';
  }

  get elapsedMs(): number {
    return Date.now() - this.startedAt;
  }

  /**
   * 吃进一个 CUSTOM 事件。返回非 null 表示「这条值得推给微信」，调用方负责推。
   * 无论返回什么都已记账（/进度 与补发摘要看得到）。
   */
  ingest(ev: { name?: string; value?: unknown } | null | undefined): ProgressLine | null {
    this.lastActivityAt = Date.now();

    const h = humanizeEvent(ev);
    if (!h) return null;

    // reasoning delta：追加进思考缓冲区，攒够 THINK_OVERFLOW_CHARS 先推一波
    if (h.trackThink !== undefined) {
      this.thinkBuf += h.trackThink;
      if (this.thinkBuf.length >= THINK_OVERFLOW_CHARS) return this.flushThinkingBuffer();
      return null;
    }
    if (h.flushThink !== undefined) return this.emitThought(h.flushThink);
    if (h.flushBuf) return this.flushThinkingBuffer();

    // silent（工具调用行）：只记账 + 更新当前步骤，不占推送通道。
    // 用户明确说工具调用行没信息量；/进度 和 keepalive 仍看得到全貌。
    // 工具开跑前先把攒下的思考放出去——「拿到结果→思考→决定调工具」，
    // 这句思考就该在这条工具调用前面说。
    if (h.silent) {
      const flushed = this.flushThinkingBuffer();
      if (h.trackStep) this.currentStep = h.trackStep;
      const entry: ProgressEntry = {
        ts: Date.now(),
        kind: h.kind,
        key: h.key,
        text: h.text,
        urgent: false,
        emitted: false,
        delivered: false,
        failed: false,
      };
      this.entries.push(entry);
      if (this.entries.length > MAX_ENTRIES) this.entries.shift();
      return flushed;
    }

    // trace 之类只有 track 信息、没有文案的事件：只更新当前节点。
    if (!h.text) {
      if (h.trackNode) this.currentNode = h.trackNode;
      return null;
    }
    if (h.trackStep) this.currentStep = h.trackStep;

    return this.recordAndGate(h);
  }

  /** 记账 + 去重/节流/上限门控，ingest 与思考 flush 共用。 */
  private recordAndGate(h: { kind: ProgressKind; key: string; text: string; urgent: boolean }): ProgressLine | null {
    const now = Date.now();
    const entry: ProgressEntry = {
      ts: now,
      kind: h.kind,
      key: h.key,
      text: h.text,
      urgent: h.urgent,
      emitted: false,
      delivered: false,
      failed: false,
    };
    this.entries.push(entry);
    if (this.entries.length > MAX_ENTRIES) this.entries.shift();

    const lastAt = this.lastKeyAt.get(h.key) || 0;
    if (now - lastAt < DEDUP_TTL_MS) return null;

    if (!h.urgent) {
      if (this.emitCount >= MAX_EMITS_PER_RUN) return null;
      if (now - this.lastEmitAt < THROTTLE_MS) return null;
    }

    this.lastKeyAt.set(h.key, now);
    this.lastEmitAt = now;
    this.emitCount += 1;
    entry.emitted = true;
    return { text: h.text, entry };
  }

  /** 缓冲区有货就取尾部整句推出去；没货返回 null。 */
  private flushThinkingBuffer(): ProgressLine | null {
    const buf = this.thinkBuf;
    this.thinkBuf = '';
    if (!buf.trim()) return null;
    return this.emitThought(buf);
  }

  /** 一句思考 → 💭 短句。取尾部（最新的想法最值钱），并喂给 keepalive 当步骤。 */
  private emitThought(text: string): ProgressLine | null {
    const tail = truncateTail(text, THINK_LINE_CHARS);
    if (!tail) return null;
    this.currentStep = `思考：${truncate(tail, 40)}`;
    return this.recordAndGate({ kind: 'progress', key: `think:${tail}`, text: `💭 ${tail}`, urgent: false });
  }

  markDelivered(entry: ProgressEntry): void {
    entry.delivered = true;
    entry.failed = false;
  }

  noteSendFailure(entry?: ProgressEntry): void {
    this.sendFailed = true;
    if (entry) entry.failed = true;
  }

  /** 上一轮推送是否失败过 —— 决定要不要在下一条用户消息前补一段进展摘要。 */
  get hadSendFailure(): boolean {
    return this.sendFailed;
  }

  hasDigest(): boolean {
    if (!this.sendFailed) return false;
    const cutoff = Date.now() - DIGEST_LOOKBACK_MS;
    return this.entries.some((e) => e.ts >= cutoff);
  }

  /** 记账里所有条目（供 /进度 渲染）。 */
  allEntries(): readonly ProgressEntry[] {
    return this.entries;
  }

  /**
   * 生成「你不在的时候发生了什么」的补发摘要，并把它标记为已消费。
   * 只在至少一次推送失败过（= 回复窗口被关掉过）时才有内容。
   */
  takeDigest(): string | null {
    if (!this.hasDigest()) return null;
    const lines = this.pickDigestLines();
    // 消费掉：摘要一旦给出就不再重复
    this.sendFailed = false;
    for (const e of this.entries) e.failed = false;
    if (!lines.length) return null;
    return [
      '📌 上一条任务的部分进展没能在当时送达微信，补给你：',
      ...lines.map((l) => `· ${l}`),
    ].join('\n');
  }

  private pickDigestLines(): string[] {
    const cutoff = Date.now() - DIGEST_LOOKBACK_MS;
    const byKey = new Map<string, ProgressEntry>();
    for (const e of this.entries) {
      if (e.ts < cutoff) continue;
      byKey.set(e.key, e); // 同 key 保留最新一条
    }
    return [...byKey.values()]
      .sort((a, b) => a.ts - b.ts)
      .slice(-DIGEST_LIMIT)
      .map((e) => e.text.split('\n')[0]);
  }

  /** /进度 用：当前步骤 + 最近 N 条。 */
  renderReport(): string {
    const step = this.currentStepText();
    if (!step && this.entries.length === 0) {
      return '📊 当前没有进行中的任务。';
    }

    const byKey = new Map<string, ProgressEntry>();
    for (const e of this.entries) byKey.set(e.key, e);
    const recent = [...byKey.values()]
      .sort((a, b) => a.ts - b.ts)
      .slice(-REPORT_LIMIT)
      .map((e) => `· ${e.text.split('\n')[0]}`);

    const lines: string[] = ['📊 当前进展'];
    lines.push(step ? `正在做：${step}` : '正在做：—');
    if (recent.length) {
      lines.push('', '最近动态：', ...recent);
    }
    lines.push('', `已运行 ${formatDuration(this.elapsedMs)}`);
    return lines.join('\n');
  }
}

// ---------------------------------------------------------------------------
// Registry（main.ts / handlers.ts 共用）
// ---------------------------------------------------------------------------

const REGISTRY_MAX = 64;
const registry = new Map<string, ProgressTracker>();

export function getProgressTracker(threadId: string): ProgressTracker {
  let t = registry.get(threadId);
  if (!t) {
    // 简单的 FIFO 淘汰，防止长期运行下无限增长
    if (registry.size >= REGISTRY_MAX) {
      const oldest = registry.keys().next().value;
      if (oldest !== undefined) registry.delete(oldest);
    }
    t = new ProgressTracker(threadId);
    registry.set(threadId, t);
  }
  return t;
}

/** 新一轮对话：换一个干净的 tracker。 */
export function resetProgressTracker(threadId: string): ProgressTracker {
  registry.delete(threadId);
  const t = new ProgressTracker(threadId);
  registry.set(threadId, t);
  return t;
}

/** 「wx-<userId>」这种 threadId 约定集中在这里，避免各处拼字符串。 */
export function wechatThreadId(fromUserId: string): string {
  return `wx-${fromUserId}`;
}
