import { randomUUID } from 'node:crypto';
import { logger } from '../logger.js';

// ---------------------------------------------------------------------------
// Public types (shape-compatible with the upstream Claude CLI adapter so
// main.ts / turn-router keep working unchanged)
// ---------------------------------------------------------------------------

export interface QueryOptions {
  /** User message text (already includes any file-path annotations). */
  prompt: string;
  /** Ignored by the abcyesno adapter (kept for upstream compatibility). */
  cwd?: string;
  /**
   * Upstream used this for `claude --resume`. In abcyesno the conversation
   * identity is the AG-UI threadId (agui-server maps it to a persistent
   * Hermes session), so callers should pass the wx thread here instead.
   */
  resume?: string;
  threadId?: string;
  model?: string;
  /**
   * Bridge-level system prompt (time injection, anti self-correction, number
   * honesty). AG-UI does not carry a system role that survives agui-server —
   * handleAgentRun only forwards the *user* message text to Hermes and drops
   * any system role. So we prepend this to the user content so the model
   * actually receives it. Without this the model has no real time source and
   * invents dates like "206 年 8 月 5 日".
   */
  systemPrompt?: string;
  images?: Array<{
    type: "image";
    source: { type: "base64"; media_type: string; data: string };
  }>;
  /** Called each time an assistant text chunk is produced. */
  onText?: (text: string) => Promise<void> | void;
  /** Called when the run finishes ('end_turn' | 'error' | ...). */
  onTurnEnd?: (stopReason: string) => Promise<void> | void;
  /** Optional abort controller to cancel the query. */
  abortController?: AbortController;
}

export interface QueryResult {
  text: string;
  sessionId: string;
  error?: string;
}

// ---------------------------------------------------------------------------
// AG-UI SSE parsing (extracted for testability)
// ---------------------------------------------------------------------------

export interface AgUiParserState {
  messageId: string | null;
  textParts: string[];
  errorMessage?: string;
  finished: boolean;
}

/**
 * Consume one parsed AG-UI event. Returns true when the run reached a
 * terminal state (RUN_FINISHED / RUN_ERROR).
 */
export function handleAgUiEvent(
  ev: any,
  state: AgUiParserState,
  callbacks: { onText?: (t: string) => void; onTurnEnd?: (reason: string) => void },
): boolean {
  switch (ev?.type) {
    case 'TEXT_MESSAGE_START': {
      state.messageId = ev.messageId || state.messageId;
      break;
    }
    case 'TEXT_MESSAGE_CONTENT': {
      const delta: string = ev.delta || '';
      // Gateway echoes this literal when a run was interrupted mid-stream.
      if (/^Operation interrupted:/.test(delta)) break;
      if (delta) {
        state.textParts.push(delta);
        callbacks.onText?.(delta);
      }
      break;
    }
    case 'TEXT_MESSAGE_END': {
      break;
    }
    case 'RUN_ERROR': {
      state.errorMessage = ev.message || ev.detail || 'RUN_ERROR';
      logger.error('AG-UI run error', { message: state.errorMessage });
      state.finished = true;
      callbacks.onTurnEnd?.('error');
      return true;
    }
    case 'RUN_FINISHED': {
      state.finished = true;
      callbacks.onTurnEnd?.('end_turn');
      return true;
    }
    default:
      break;
  }
  return false;
}

/** Split an SSE byte stream into `data:` JSON frames (incremental buffer). */
export class SseFrameReader {
  private buffer = '';

  push(chunk: string): any[] {
    this.buffer += chunk;
    const events: any[] = [];
    const frames = this.buffer.split('\n\n');
    this.buffer = frames.pop() || '';
    for (const frame of frames) {
      const line = frame.trim();
      if (!line.startsWith('data:')) continue;
      const json = line.slice(5).trim();
      if (!json) continue;
      try {
        events.push(JSON.parse(json));
      } catch {
        // ignore single-frame parse errors
      }
    }
    return events;
  }
}

// ---------------------------------------------------------------------------
// Core: one WeChat turn -> one abcyesno agent run over AG-UI SSE
// ---------------------------------------------------------------------------

function resolveAguiPort(): number {
  const p = Number(process.env.AGUI_PORT || 0);
  return Number.isFinite(p) && p > 0 ? p : 0;
}

export async function claudeQuery(options: QueryOptions): Promise<QueryResult> {
  const {
    prompt,
    threadId,
    model,
    images,
    onText,
    onTurnEnd,
    abortController,
    systemPrompt,
  } = options;

  const port = resolveAguiPort();
  if (!port) {
    return { text: '', sessionId: '', error: 'AGUI_PORT not available (backend not started)' };
  }

  // Stable per-WeChat-user thread so agui-server maps it to one persistent
  // Hermes session (server-side history). Callers MUST pass `threadId`; the
  // historical `'wx-bridge'` fallback was removed because it merged all
  // WeChat users into a single shared session (causing context confusion
  // and the early "206 年 8 月 5 日 vs 2026 年 8 月 25 日" date hallucination
  // — the model was replying to a different user's prior turn).
  if (!threadId) {
    return { text: '', sessionId: '', error: 'claudeQuery: threadId is required (one Hermes thread per WeChat user)' };
  }
  const agThreadId = threadId;
  const runId = randomUUID();

  // Convert anthropic-style image blocks to the wire format the agui-server
  // expects ({ alt?, dataUrl, filename }) — same shape useAgentStream ships.
  const wireImages = (images || []).map((img, i) => ({
    alt: '',
    dataUrl: `data:${img.source.media_type};base64,${img.source.data}`,
    filename: `wechat_image_${i + 1}.${img.source.media_type.split('/')[1] || 'png'}`,
  }));

  // agui-server's handleAgentRun only forwards the *user* message text to
  // Hermes (it filters `role === 'user'`), so a real system role would be
  // dropped. Prepend the bridge systemPrompt to the user content so the model
  // actually sees the real clock time + anti self-correction + number-honesty
  // instructions. This is the actual fix for the "206 年" date hallucination.
  const userContent = systemPrompt ? `${systemPrompt}\n\n---\n\n${prompt}` : prompt;

  const body = {
    method: 'agent/run',
    threadId: agThreadId,
    runId,
    messages: [{ id: randomUUID(), role: 'user', content: userContent }],
    forwardedProps: {
      assistantId: process.env.WECHAT_BRIDGE_ASSISTANT_ID || 'default',
      ...(model ? { model } : {}),
    },
    ...(wireImages.length > 0 ? { images: wireImages } : {}),
  };

  logger.info('Starting AG-UI run', {
    port,
    threadId: agThreadId,
    runId,
    textLength: prompt.length,
    hasImages: wireImages.length > 0,
  });

  const controller = new AbortController();
  const onAbort = () => controller.abort();
  abortController?.signal.addEventListener('abort', onAbort, { once: true });

  let fetchRes: Response;
  try {
    fetchRes = await fetch(`http://127.0.0.1:${port}/api/ag-ui/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
      body: JSON.stringify(body),
      signal: controller.signal,
    });
  } catch (err: any) {
    abortController?.signal.removeEventListener('abort', onAbort);
    const msg = err instanceof Error ? err.message : String(err);
    return { text: '', sessionId: agThreadId, error: `Failed to reach agui-server: ${msg}` };
  }

  if (!fetchRes.ok || !fetchRes.body) {
    abortController?.signal.removeEventListener('abort', onAbort);
    return { text: '', sessionId: agThreadId, error: `agui-server HTTP ${fetchRes.status}` };
  }

  const QUERY_TIMEOUT_MS = 60 * 60 * 1000;
  // fetch() returns a Web ReadableStream (not a Node stream), so we must use
  // getReader()/TextDecoder — never setEncoding() or .on('data').
  const streamReader = fetchRes.body.getReader();
  const timeoutId = setTimeout(() => {
    logger.warn('AG-UI run timed out, aborting');
    try { streamReader.cancel().catch(() => {}); } catch { /* ignore */ }
  }, QUERY_TIMEOUT_MS);

  const state: AgUiParserState = { messageId: null, textParts: [], finished: false };
  const frameReader = new SseFrameReader();
  const decoder = new TextDecoder();

  return new Promise<QueryResult>((resolve) => {
    let settled = false;
    const finish = (result: QueryResult) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeoutId);
      abortController?.signal.removeEventListener('abort', onAbort);
      resolve(result);
    };

    async function cancelStream() {
      try { await streamReader.cancel(); } catch { /* ignore */ }
    }

    function settleNormal() {
      const fullText = state.textParts.join('').trim();
      if (!fullText && !state.errorMessage) {
        state.errorMessage = 'Agent returned an empty response.';
      }
      logger.info('AG-UI run completed', {
        threadId: agThreadId,
        textLength: fullText.length,
        hasError: !!state.errorMessage,
      });
      finish({ text: fullText, sessionId: agThreadId, error: state.errorMessage });
    }

    async function pump(): Promise<void> {
      try {
        while (true) {
          if (abortController?.signal.aborted) {
            await cancelStream();
            settleNormal();
            return;
          }
          const { done, value } = await streamReader.read();
          if (done) {
            settleNormal();
            return;
          }
          const textChunk = decoder.decode(value as Uint8Array, { stream: true });
          for (const ev of frameReader.push(textChunk)) {
            handleAgUiEvent(ev, state, {
              onText: (t) => { try { onText?.(t); } catch { /* never kill stream on emit errors */ } },
              onTurnEnd: (r) => { try { onTurnEnd?.(r); } catch { /* ignore */ } },
            });
            if (state.finished) {
              await cancelStream();
              settleNormal();
              return;
            }
          }
        }
      } catch (err: any) {
        const msg = err instanceof Error ? err.message : String(err);
        if (!state.finished && !state.textParts.length && !state.errorMessage) {
          finish({ text: '', sessionId: agThreadId, error: `SSE stream error: ${msg}` });
        } else {
          settleNormal();
        }
      }
    }

    pump();
  });
}
