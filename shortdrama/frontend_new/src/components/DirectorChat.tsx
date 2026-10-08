/* ==========================================================================
   components/DirectorChat.tsx —— 右栏：**和导演本人对话**，聊清楚了再开工
   --------------------------------------------------------------------------
   这是真对话，不是投递槽：右栏说的话发给 **supervisor 本人**，他回答你；
   你满意了点「开工」，完整创作链就跑在**同一段对话**上 —— 他对每一步的说明
   （"规格已落盘" "派 scenedesigner" "创作链已跑完"）落在同一条时间线上，
   中间画布同时开始长格子。

   ## 三块东西，别混

   | 块 | 是什么 | 什么时候有用 |
   |---|---|---|
   | **对话**（主体） | 跟导演本人一来一回 | 随时。这是这栏的主业 |
   | **投递**（折叠在下面） | 把一句话塞给**下一个被派发的角色** | 链在跑、你想微调某个角色时 |
   | **步级确认**（挂起时才出现） | 继续 / 打回 / 中止 | 开了「逐步确认」、链停在派活前时 |

   ⛔ 投递那条**不是**聊天（链没跑时它永远"排队中"，因为没人来收）—— 所以它被
     折进一行，默认不展开，免得再被当成对话框用。

   ## 为什么对话阶段他不动手

   导演的提示词是「你是监制，按依赖序派活」，没有"只回答不做事"的模式。
   按住他的办法写在 `v5/director_chat.py` 的消息前缀里（实测：盘上新增文件 0 个），
   并且**每次都会把项目 brief + 盘上现状附上去** —— 因为他不能用工具，不喂就只能空谈。
   ========================================================================== */

import { useEffect, useMemo, useRef, useState } from 'react';
import type { ChatState, InboxState } from '../lib/studio';
import { ROLE_ZH, hhmm } from '../lib/studio';
import { Markdown } from '../lib/md';

type Props = {
  chat: ChatState | null;
  chatError: string;
  inbox: InboxState | null;
  inboxError: string;
  busy: boolean;
  mine: number[];
  manualSteps: boolean;
  onManualSteps: (v: boolean) => void;
  onAsk: (text: string) => Promise<string | null>;
  onStart: () => Promise<string | null>;
  onSend: (text: string, to: string) => Promise<{ err: string | null; id?: number }>;
  onDecide: (decision: string, target: string, note: string) => Promise<string | null>;
  onRedo: (target: string, note: string) => Promise<string | null>;
  splitter?: React.ReactNode;
  onCollapse?: () => void;
};

export function DirectorChat(p: Props) {
  const { splitter, onCollapse } = p;
  const [text, setText] = useState('');
  const [msg, setMsg] = useState<string | null>(null);
  const scroller = useRef<HTMLDivElement | null>(null);

  const turns = p.chat?.turns || [];
  const h = p.inbox?.hitl;
  const pending = !!h?.pending;
  const gateTargets = useMemo(() => (h?.redo_targets || []).filter(Boolean), [h]);
  const idleTargets = useMemo(() => (p.inbox?.idle_targets || []).filter(Boolean), [p.inbox]);
  const [gateTarget, setGateTarget] = useState('');
  const [note, setNote] = useState('');
  const [to, setTo] = useState('');
  const [redoTo, setRedoTo] = useState('');

  useEffect(() => {
    if (pending && !gateTargets.includes(gateTarget)) setGateTarget(String(gateTargets[0] || ''));
  }, [pending, gateTargets, gateTarget]);

  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns.length, p.chat?.busy]);

  const ask = async () => {
    const t = text.trim();
    if (!t || p.chat?.busy) return;
    setMsg(null);
    const err = await p.onAsk(t);
    if (err) { setMsg(err); return; }
    setText('');
  };

  const queued = p.inbox?.stats?.pending || 0;

  return (
    <aside className="chat">
      {splitter}
      <div className="chat-head">
        <span className="rail-title">导演</span>
        <span className="chat-state">
          {p.chat?.busy ? (p.chat.status === 'interrupted' ? '等你确认' : '在想 / 干活中')
            : turns.length ? '可以说话' : ''}
        </span>
        <span className="stage-spacer" />
        <label className="chat-state" title="每派一个角色前停一次，等人点继续 / 打回。翻了要重启 dev server、只对下一次生成生效">
          <input type="checkbox" checked={p.manualSteps}
                 onChange={(e) => p.onManualSteps(e.target.checked)} /> 逐步确认
        </label>
        {onCollapse ? (
          <button type="button" className="panel-x" title="收起对话栏" onClick={onCollapse}>›</button>
        ) : null}
      </div>

      <div className="chat-body" ref={scroller}>
        {!turns.length ? (
          <div className="chat-hero">
            <h3>先跟导演聊聊</h3>
            <p>聊清楚了再点下面的「开工」</p>
          </div>
        ) : null}

        {turns.map((t, i) => (
          <div key={i} className={'msg ' + (t.role === 'user' ? 'msg--me' : 'msg--sys')}>
            {t.role === 'user' ? t.text : <Markdown text={t.text} />}
            <span className="msg-meta">{hhmm(t.at)}</span>
          </div>
        ))}

        {p.chat?.busy ? (
          <div className="msg msg--sys"><span className="spin" /> {p.chat.status === 'interrupted' ? '停下来等你确认' : '……'}</div>
        ) : null}
        {p.chatError ? <div className="msg msg--sys err">{p.chatError}</div> : null}
      </div>

      {pending ? (
        <div className="chat-gate">
          <div>
            下一步 <b>{ROLE_ZH[h?.next_role || ''] || h?.next_role || '?'}</b>
            {' · '}刚出 <b>{ROLE_ZH[h?.prev_role || ''] || h?.prev_role || '?'}</b>
          </div>
          {h?.stale_decision ? <div className="err">⚠️ 你的决定已失效，请重新确认</div> : null}
          <div className="field" style={{ marginTop: 10 }}>
            <textarea rows={2} placeholder="理由（可选）" value={note}
                      onChange={(e) => setNote(e.target.value)} />
          </div>
          <div className="chat-gate-row">
            <button type="button" className="btn btn--sm btn--primary" disabled={p.busy}
                    onClick={async () => setMsg(await p.onDecide('approve', '', note))}>继续</button>
            <select value={gateTarget} onChange={(e) => setGateTarget(e.target.value)}>
              {gateTargets.map((t) => <option key={t} value={t}>{ROLE_ZH[t] || t}</option>)}
            </select>
            <button type="button" className="btn btn--sm" disabled={p.busy} data-danger="1"
                    title="重跑该角色及其全部下游"
                    onClick={async () => setMsg(await p.onDecide('redo', gateTarget, note))}>打回</button>
            <button type="button" className="btn btn--sm btn--danger" disabled={p.busy} data-danger="1"
                    onClick={async () => setMsg(await p.onDecide('reject', '', note))}>中止</button>
          </div>
        </div>
      ) : null}

      {/* 投递：折叠成一行。⛔ 它不是聊天（链没跑时永远排着队，没人来收） */}
      <details className="chat-inbox">
        <summary>投递给角色{queued ? ` · ${queued} 条排队中` : ''}</summary>
        <div className="chat-inbox-body">
          <div className="chat-input-row">
            <select value={to} onChange={(e) => setTo(e.target.value)}>
              <option value="">任意角色（下一个被派发的收）</option>
              {(p.inbox?.targets || []).filter(Boolean).map((t) => (
                <option key={t} value={t}>{ROLE_ZH[t] || t}</option>
              ))}
            </select>
            <select value={redoTo} onChange={(e) => setRedoTo(e.target.value)} title="重做哪个角色（含下游）">
              <option value="">重做…</option>
              {idleTargets.map((t) => <option key={t} value={t}>{ROLE_ZH[t] || t}</option>)}
            </select>
            <button type="button" className="btn btn--sm" disabled={p.busy || !redoTo} data-danger="1"
                    title="回退该角色及其全部下游并重跑 —— 整表重派实测约 95 分钟"
                    onClick={async () => setMsg(await p.onRedo(redoTo, text.trim() || note.trim()))}>
              让导演重做
            </button>
          </div>
          {(p.inbox?.messages || []).slice(-6).map((m) => (
            <div key={m.id} className="chat-inbox-row">
              <span className="muted">{hhmm(m.at)}</span>
              <span className={'chip' + (m.delivered_to ? ' chip--good' : '')}>
                {m.delivered_to ? '已送达 ' + (ROLE_ZH[m.delivered_to] || m.delivered_to) : '排队中'}
                {m.to ? ' → ' + (ROLE_ZH[m.to] || m.to) : ''}
              </span>
              {m.text}
            </div>
          ))}
          {!p.inbox?.messages?.length ? <div className="chat-inbox-row muted">还没投递过</div> : null}
          {p.inboxError ? <div className="chat-inbox-row err">{p.inboxError}</div> : null}
          <div className="chat-inbox-row muted">
            链没在跑时"排队中"不会变成"已送达" —— 要立刻说上话，用上面的对话。
          </div>
        </div>
      </details>

      <div className="chat-input">
        <textarea value={text} placeholder={p.chat?.busy ? '他还在忙…' : '对导演说…'}
                  onChange={(e) => setText(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) void ask(); }} />
        <div className="chat-input-row">
          <button type="button" className="btn btn--sm btn--primary" disabled={p.busy || !text.trim() || !!p.chat?.busy}
                  title="Ctrl / ⌘ + Enter" onClick={() => void ask()}>发送</button>
          <span className="stage-spacer" />
          <button type="button" className="btn btn--sm btn--primary"
                  data-danger="1"
                  title="接着这段对话跑完整条创作链（7 个角色 → 分镜）。跑起来后每一步都会出现在上面这段对话里。"
                  disabled={p.busy || !!p.chat?.busy || turns.length === 0}
                  onClick={async () => setMsg(await p.onStart())}>
            {p.chat?.status === 'running' ? '开工中…' : '开工'}
          </button>
        </div>
        {msg ? <div className="inspector-log err">{msg}</div> : null}
      </div>
    </aside>
  );
}
