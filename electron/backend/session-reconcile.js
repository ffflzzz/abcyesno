/**
 * 自主唤醒回合回写桌面存档（session reconcile）
 *
 * 病根（2026-10-01 实测）：后台进程完成通知触发的 agent 回合由 tui_gateway
 * 的 notification poller 直接起（`_run_prompt_submit`），**不经过桌面端的
 * AG-UI SSE**——那条流只在用户主动 `prompt.submit` 时开着。于是这类回合的
 * 产出只进引擎侧 `state.db`，桌面存档 `abcyesno_sessions.json` 停在用户最后
 * 一次主动交互。当天实测：界面停在 15:47，同一会话在引擎侧已到 16:50，中间
 * 三份汇报（含"两集都出片了"的终版验收）在界面上怎么点都看不到。
 *
 * 做法：网关每完成一个回合（`message.complete`）就把该网关会话的
 * `session.history` 与桌面存档对一遍，缺的 user/assistant 文本补进存档，
 * 然后广播 `sessions-updated`。渲染端已有的 loadSessions → hydrateSession
 * 链路会把它们显示出来，前端不用改。
 *
 * 幂等与竞态：按 (role, 归一化文本) 去重，重复对账不会追加两遍。渲染端在
 * 切走/卸载时会把内存快照 flush 回 storage（App.jsx 的 flush-on-switch），
 * 若那一刻快照是旧的，可能把刚补的消息冲掉——下一次事件或对账会重新补齐，
 * 属于自愈而不是丢数据。不在此处做合并语义，是为了不去碰 2026-08-26 那次
 * 存档撕裂事故涉及的写入路径。
 */

/** 取一条网关消息的纯文本。content 可能是字符串、也可能是多模态 parts 数组。 */
function textOf(m) {
  if (!m || typeof m !== 'object') return '';
  let raw = m.content;
  if (raw == null) raw = m.text;
  if (Array.isArray(raw)) {
    raw = raw
      .map((p) => (typeof p === 'string' ? p : p && (p.text || p.content) || ''))
      .join('\n');
  }
  return typeof raw === 'string' ? raw : '';
}

/** 归一化 + 截断：只为去重比对，不改变落盘内容。 */
function norm(s) {
  return String(s || '').replace(/\s+/g, ' ').trim().slice(0, 400);
}

function keyOf(role, content) {
  return `${role} ${norm(content)}`;
}

function createSessionReconciler({ getGatewayClient, storage, onAppended, log }) {
  const say = typeof log === 'function' ? log : () => {};
  // 同一网关会话的并发对账合并成一次：回合密集时 message.complete 会连着来。
  const inFlight = new Map();

  async function findAppSessionId(hermesSessionId) {
    const map = await storage.listThreadMappings();
    for (const [threadId, sid] of Object.entries(map)) {
      if (sid === hermesSessionId) return threadId;
    }
    return null;
  }

  async function reconcileHermesSession(hermesSessionId) {
    if (!hermesSessionId) return { appended: 0, reason: 'no-session' };
    if (inFlight.has(hermesSessionId)) return inFlight.get(hermesSessionId);
    const run = doReconcile(hermesSessionId).finally(() => inFlight.delete(hermesSessionId));
    inFlight.set(hermesSessionId, run);
    return run;
  }

  async function doReconcile(hermesSessionId) {
    const client = typeof getGatewayClient === 'function' ? getGatewayClient() : getGatewayClient;
    if (!client || !client.ready) return { appended: 0, reason: 'gateway-off' };

    const appSessionId = await findAppSessionId(hermesSessionId);
    if (!appSessionId) return { appended: 0, reason: 'unmapped' };

    let history;
    try {
      history = await client.request('session.history', { session_id: hermesSessionId }, 10000);
    } catch (err) {
      // 会话尚未在本进程 resume（例如刚重启）——静默跳过，下个触发点再补。
      say('reconcile', `session.history failed for ${hermesSessionId}: ${err.message}`);
      return { appended: 0, reason: 'history-failed', error: err.message };
    }

    const backend = (history && history.messages) || [];
    const stored = await storage.getSession(appSessionId);
    if (!stored) return { appended: 0, reason: 'session-gone', appSessionId };

    const local = Array.isArray(stored.messages) ? stored.messages : [];
    const seen = new Set(local.map((m) => keyOf(m.role, textOf(m))));
    const missing = backend.filter(
      (m) =>
        (m.role === 'user' || m.role === 'assistant') &&
        norm(textOf(m)).length > 0 &&
        !seen.has(keyOf(m.role, textOf(m)))
    );
    if (missing.length === 0) {
      return { appended: 0, reason: 'in-sync', appSessionId, localCount: local.length };
    }

    const now = Date.now();
    const added = missing.map((m, i) => ({
      id: `reconciled-${now}-${i}`,
      role: m.role,
      content: textOf(m),
      createdAt: now + i,
      reasoning: typeof m.reasoning === 'string' ? m.reasoning : '',
    }));
    await storage.updateSession(appSessionId, { messages: [...local, ...added] });
    say(
      'reconcile',
      `appended ${added.length} missed turn(s) to ${appSessionId.slice(0, 8)} ` +
        `(archive ${local.length} -> ${local.length + added.length})`
    );
    if (typeof onAppended === 'function') onAppended({ appSessionId, appended: added.length });
    return { appended: added.length, appSessionId };
  }

  /** 启动/重连时全量对账一遍：把上次退出后引擎侧新增的回合捞回来。 */
  async function reconcileAll() {
    const map = await storage.listThreadMappings();
    const ids = [...new Set(Object.values(map).filter(Boolean))];
    let appended = 0;
    let checked = 0;
    for (const sid of ids) {
      const r = await reconcileHermesSession(sid);
      checked += 1;
      appended += r.appended || 0;
    }
    return { checked, appended };
  }

  return { reconcileHermesSession, reconcileAll };
}

module.exports = { createSessionReconciler, __internal: { textOf, keyOf, norm } };
