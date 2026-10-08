/* ==========================================================================
   pages/Studio.tsx —— 三栏工作室的调度中心（所有轮询都在这一个组件里）
   --------------------------------------------------------------------------
   ★ 轮询节奏按"有没有在跑"分档，⛔ 不是一刀切 3 秒：
     · 画布 3s（在跑）/ 12s（空闲）—— 后端每次请求都重读盘，空闲时白读
     · 信箱 4s —— 它决定右栏能不能点「继续」，慢了人会以为界面死了
     · 项目/进度/runs 6s —— 左栏分组用，不需要秒级
   后端**没有 SSE/WebSocket**（`api.ts:366` 明写"前端不依赖长连接"），
   所以这里就是轮询；`drive_chain` 自己 20 秒才比一次产物，快于它是白快。
   ========================================================================== */

import { useCallback, useEffect, useRef, useState } from 'react';
import { Api } from '../api';
import type { Health, Project, Segment } from '../types';
import { ProjectRail, type RailSignals } from '../components/ProjectRail';
import { CanvasPane } from '../components/CanvasPane';
import { DirectorChat } from '../components/DirectorChat';
import {
  askDirector, askRedo, approveHitl, createBlank, editShot, fetchCanvas, getChat, getInbox, isRunning,
  sendToDirector, startProduction, type CanvasDoc, type ChatState, type InboxState,
} from '../lib/studio';

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

/* ── 两栏的宽度 / 收起状态（记在本地，刷新后还在）───────────────────────
   为什么要有：中栏是画布，左右两栏是辅助 —— 辅到一定程度人就该能把它们让开。
   ⚠️ 拖拽期间要给 `body` 挂 class：否则鼠标划过 iframe 时事件被画布吃掉，
      手柄会"跟丢"（拖到一半不动了）。 */
const RAIL_MIN = 190, RAIL_MAX = 460, CHAT_MIN = 300, CHAT_MAX = 760;
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
const lsGet = (k: string, d: string) => { try { return localStorage.getItem(k) ?? d; } catch { return d; } };
const lsSet = (k: string, v: string) => { try { localStorage.setItem(k, v); } catch { /* 隐私模式 */ } };

function dragColumn(axis: 'rail' | 'chat', startX: number, startW: number,
                    apply: (w: number) => void) {
  document.body.classList.add('is-col-resizing');
  const move = (e: PointerEvent) => {
    const d = e.clientX - startX;
    apply(axis === 'rail' ? startW + d : startW - d);
  };
  const up = () => {
    document.body.classList.remove('is-col-resizing');
    window.removeEventListener('pointermove', move);
    window.removeEventListener('pointerup', up);
  };
  window.addEventListener('pointermove', move);
  window.addEventListener('pointerup', up);
}


export function Studio() {
  const [health, setHealth] = useState<Health | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [pid, setPid] = useState('');
  const [ep, setEp] = useState(1);
  const [signals, setSignals] = useState<RailSignals>({ running: {}, pending: {}, nextRole: {} });
  /** pid → 正在跑的那个 run_id（「停掉这次跑」要用） */
  const [runIds, setRunIds] = useState<Record<string, string>>({});
  const [doc, setDoc] = useState<CanvasDoc | null>(null);
  const [docError, setDocError] = useState('');
  const [segments, setSegments] = useState<Segment[]>([]);
  const [shot, setShot] = useState('');
  const [inbox, setInbox] = useState<InboxState | null>(null);
  const [inboxError, setInboxError] = useState('');
  /** 和导演本人的对话（右栏主体）。**这是主表面，轮询给最快的 3 秒一档。** */
  const [chat, setChat] = useState<ChatState | null>(null);
  const [chatError, setChatError] = useState('');
  const [railW, setRailW] = useState(() => clamp(Number(lsGet('sd.railW', '268')) || 268, RAIL_MIN, RAIL_MAX));
  const [chatW, setChatW] = useState(() => clamp(Number(lsGet('sd.chatW', '420')) || 420, CHAT_MIN, CHAT_MAX));
  const [railOpen, setRailOpen] = useState(() => lsGet('sd.railOpen', '1') === '1');
  const [chatOpen, setChatOpen] = useState(() => lsGet('sd.chatOpen', '1') === '1');
  const [busy, setBusy] = useState(false);
  const [toasts, setToasts] = useState<{ id: number; text: string; bad?: boolean }[]>([]);
  const [manualSteps, setManualSteps] = useState(true);
  /** 本次会话里**我**发出去的信箱编号 —— 右栏据此把它们排到右边。
   *  ⛔ 不用 `by` 判断：那是显示名，换个标签同一条消息就会跑到对面去。 */
  const [mineIds, setMineIds] = useState<number[]>([]);
  const tick = useRef(0);

  const toast = useCallback((text: string, bad = false) => {
    const id = ++tick.current;
    setToasts((t) => [...t.slice(-3), { id, text, bad }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 6000);
  }, []);

  useEffect(() => { void Api.getHealth().then(setHealth).catch(() => setHealth(null)); }, []);

  /* ── 左栏：项目 + 每个项目的"在跑 / 挂起"信号 ───────────────── */
  useEffect(() => {
    let stop = false;
    const pull = async () => {
      try {
        const r = await Api.listProjects(1, 200);
        const list = r.list || [];
        if (stop) return;
        setProjects(list);
        // ★ 2026-10-08 用户原话：「为什么每次打开都是自动加载历史项目？
        //   可以变成直接加载空白画布吗？」—— 可以。**不再自动钻进某个历史项目**，
        //   打开就是一张白纸；要接着做哪个，从左栏点它（或跟导演开一段新对话）。
        const running: Record<string, string> = {};
        const runIds: Record<string, string> = {};
        for (const p of list) {
          const runs = await Api.listRuns(p.id, 3).catch(() => []);
          const live = runs.find((x) => isRunning(String(x.status || '')));
          if (live) {
            running[p.id] = String(live.status);
            if (live.run_id) runIds[p.id] = String(live.run_id);
          }
        }
        if (stop) return;
        setSignals((s) => ({ ...s, running }));
        setRunIds(runIds);
      } catch (e) { if (!stop) setInboxError(errText(e)); }
    };
    void pull();
    const t = setInterval(pull, 6000);
    return () => { stop = true; clearInterval(t); };
  }, [pid]);

  /* ── 选中的项目在盘上没了 ⇒ 换一个 ──────────────────────────────
     ⛔ 不这么兜的话，界面会**一直对着一个不存在的项目轮询** ——
     2026-10-08 实测：删掉 `abc` 之后右栏每几秒刷一条「项目不存在：abc」，
     而左栏那个选中态还是它（pid 存在 localStorage 里，跨会话活着）。
     删自己（`deleteProject`）那条路会主动清 pid，但**别的窗口 / 命令行删的**
     兜不到 —— 所以按"列表里还有没有它"来判，与谁删的无关。 */
  useEffect(() => {
    if (!projects.length || !pid) return;
    if (projects.some((x) => x.id === pid)) return;
    // ⛔ 清空，**不要顺手挑一个** —— 那又变回"自动钻进历史项目"。
    //   （删掉的项目 / 别的窗口删的项目，都走这条：界面回到白纸，
    //     而不是对着一个不存在的项目每几秒刷「项目不存在」。）
    setPid(''); setEp(1); setShot('');
    setDoc(null); setSegments([]); setChat(null);
  }, [projects, pid]);

  /* ── 信箱（右栏 + 左栏的"等你确认"分组都读它）───────────────── */
  useEffect(() => {
    if (!pid) return;
    let stop = false;
    const pull = async () => {
      try {
        const s = await getInbox(pid, ep);
        if (stop) return;
        setInbox(s); setInboxError('');
        setSignals((old) => ({
          ...old,
          pending: { ...old.pending, [pid]: !!s.hitl.pending },
          nextRole: { ...old.nextRole, [pid]: s.hitl.next_role || '' },
        }));
      } catch (e) { if (!stop) setInboxError(errText(e)); }
    };
    void pull();
    const t = setInterval(pull, 4000);
    return () => { stop = true; clearInterval(t); };
  }, [pid, ep]);

  /* ── 和导演的对话（右栏主体，最快一档）───────────────────────── */
  useEffect(() => {
    if (!pid) return;
    let stop = false;
    const pull = async () => {
      try {
        const d = await getChat(pid, ep);
        if (stop) return;
        setChat(d); setChatError('');
      } catch (e) { if (!stop) setChatError(errText(e)); }
    };
    void pull();
    const t = setInterval(pull, 3000);
    return () => { stop = true; clearInterval(t); };
  }, [pid, ep]);

  /* ── 画布（中栏实时渲染）───────────────────────────────────── */
  const running = !!signals.running[pid];
  useEffect(() => {
    if (!pid) return;
    let stop = false;
    const pull = async () => {
      try {
        const d = await fetchCanvas(pid, ep);
        if (stop) return;
        setDoc(d); setDocError('');
      } catch (e) {
        if (stop) return;
        setDoc(null); setDocError(errText(e));
      }
    };
    void pull();
    const t = setInterval(pull, running ? 3000 : 12000);
    return () => { stop = true; clearInterval(t); };
  }, [pid, ep, running]);

  /* ── 分镜表（检查器的真值来源）─────────────────────────────── */
  useEffect(() => {
    if (!pid) return;
    let stop = false;
    const pull = async () => {
      try {
        const d = await Api.getStoryboard(`${pid}-ep${ep}`);
        if (stop) return;
        setSegments((d.segments as Segment[]) || []);
      } catch { if (!stop) setSegments([]); }
    };
    void pull();
    const t = setInterval(pull, running ? 5000 : 15000);
    return () => { stop = true; clearInterval(t); };
  }, [pid, ep, running]);

  /* ── 动作 ─────────────────────────────────────────────────── */
  const createProject = async (topic: string, pack: string, episodes: number, ratio: string) => {
    setBusy(true);
    try {
      const p = await Api.createByAI({ idea: topic, style_code: pack, pack,
        name: topic, episodes, ratio });
      toast(`已创建 ${(p as Project).name || topic}`);
      setProjects(await (await Api.listProjects(1, 200)).list);
      setPid((p as Project).id || topic); setEp(1);
    } catch (e) { toast('创建失败：' + errText(e), true); } finally { setBusy(false); }
  };

  const renameProject = async (target: string, name: string) => {
    try {
      await Api.renameProject(target, name);
      toast(`已改片名：${name}（目录名 ${target} 不动）`);
      const r = await Api.listProjects(1, 200);
      setProjects(r.list);
    } catch (e) { toast('改名失败：' + errText(e), true); }
  };

  const stopRun = async (target: string) => {
    const rid = runIds[target];
    if (!rid) { toast('找不到该项目正在跑的 run（可能刚结束）', true); return; }
    try {
      await Api.cancelRun(rid);
      toast('已请求停掉 ' + rid);
    } catch (e) { toast('停跑失败：' + errText(e), true); }
  };

  /** ⛔ 不自动 `force`：后端在有非终态 run 时回 409，那是**它替人拦的**
   *  （删掉一个正在跑创作链的项目，链随后会把产物写回原路径 —— 2026-09-15 实测踩过）。
   *  要删就先把跑停掉，别一键绕过。 */
  const deleteProject = async (target: string) => {
    if (!window.confirm(`删除项目 ${target}？\n\n产物会移到 .tmp/_deleted/<时间戳>/ 暂存，不是硬删，可恢复。`)) return;
    try {
      const r = await Api.deleteProject(target) as { deleted?: string[]; skipped?: unknown[] };
      toast(`已删除 ${(r.deleted || []).join('、') || target}${(r.skipped || []).length ? '；有跳过项' : ''}`);
      if (target === pid) { setPid(''); setDoc(null); setSegments([]); }
      const list = await Api.listProjects(1, 200);
      setProjects(list.list);
    } catch (e) { toast('删除失败：' + errText(e), true); }
  };

  const beginProduction = async (): Promise<string | null> => {
    setBusy(true);
    try {
      await startProduction(pid, ep, manualSteps);
      toast('开工指令已送进这段对话 —— 他每走一步的话、以及这一轮的判决，都回到右栏');
      await refreshChat();
      return null;
    } catch (e) { return errText(e); } finally { setBusy(false); }
  };

  const ask = async (text: string): Promise<string | null> => {
    try {
      const r = await askDirector(pid, text, ep);
      await refreshChat();
      // ★ 这句话本身是"开工" —— 后端已经起链了，界面只要说清楚
      if (r && r.started) toast('收到，开工指令已送出 —— 每一步的话和这一轮的判决都回到右栏');
      return null;
    } catch (e) { return errText(e); }
  };

  /** 「跟导演开一段新对话」= 建一个空白项目。对话是按项目存的（brief / 线程都在项目下），
   *  所以得先有个归属 —— 但这一步对用户应当是无感的：**你说话，系统就给你开一张白纸**。 */
  const newChat = async (): Promise<string | null> => {
    setBusy(true);
    try {
      const r = await createBlank('新项目', packOf(), 1, ratioOf());
      const list = await Api.listProjects(1, 200);
      setProjects(list.list);
      setPid(r.pid); setEp(1); setShot('');
      toast(`开了新项目 ${r.pid} —— 直接跟导演说你想拍什么`);
      return null;
    } catch (e) { return errText(e); } finally { setBusy(false); }
  };
  const packOf = () => String(health?.packs?.[0] || 'shortdrama');
  const ratioOf = () => String(health?.ratio_default || '9:16');

  const newBlank = async (name: string, pack: string, episodes: number, ratio: string) => {
    setBusy(true);
    try {
      const r = await createBlank(name, pack, episodes, ratio);
      toast(`已建空白项目 ${r.pid} —— 直接跟导演说你想拍什么`);
      const list = await Api.listProjects(1, 200);
      setProjects(list.list);
      setPid(r.pid); setEp(1); setShot('');
    } catch (e) { toast('建空白项目失败：' + errText(e), true); } finally { setBusy(false); }
  };

  const refreshChat = async () => {
    try { setChat(await getChat(pid, ep)); } catch { /* 下一轮轮询补上 */ }
  };

  const saveShot = async (s: string, patch: Record<string, unknown>): Promise<string | null> => {
    try {
      const r = await editShot(pid, ep, s, patch) as { warning?: string; note?: string };
      if (r.warning) toast(String(r.warning), true);
      const d = await fetchCanvas(pid, ep).catch(() => null);
      if (d) setDoc(d);
      return null;
    } catch (e) { return errText(e); }
  };

  const send = async (text: string, to: string): Promise<{ err: string | null; id?: number }> => {
    setBusy(true);
    try {
      const rec = await sendToDirector(pid, text, ep, to);
      if (rec?.id) setMineIds((old) => [...old.slice(-200), rec.id]);
      await refreshInbox();
      return { err: null, id: rec?.id };
    } catch (e) { return { err: errText(e) }; } finally { setBusy(false); }
  };

  const decide = async (decision: string, target: string, note: string): Promise<string | null> => {
    setBusy(true);
    try {
      await approveHitl(pid, { decision, target, note, by: 'studio',
        stamp: inbox?.hitl.stamp || '' });
      await refreshInbox();
      toast(decision === 'approve' ? '已放行，继续派发'
        : decision === 'redo' ? '已打回 ' + target + '（含下游重做）' : '链路已中止');
      return null;
    } catch (e) { return errText(e); } finally { setBusy(false); }
  };

  const redo = async (target: string, note: string): Promise<string | null> => {
    setBusy(true);
    try {
      const r = await askRedo(pid, { target, note, ep, by: 'studio' }) as
        { channel?: string; note?: string };
      await refreshInbox();
      toast('重做：' + (r.note || r.channel || '已提交'));
      return null;
    } catch (e) { return errText(e); } finally { setBusy(false); }
  };

  const refreshInbox = async () => {
    try { setInbox(await getInbox(pid, ep)); } catch { /* 下一轮轮询会补上 */ }
  };

  if (!health) {
    return (
      <div className="studio" style={{ display: 'block' }}>
        <div className="stage-empty" style={{ height: '100vh' }}>
          <strong>联系不上后端</strong>
          <span className="muted">
            工作室由 shortdrama 后端同源托管（<code>/studio</code>）。
            从启动台点「短剧工作室」会自动起后端；直接开这个地址需要先跑
            <code>python -m v5.server</code>。
          </span>
        </div>
      </div>
    );
  }

  const cls = 'studio' + (railOpen ? '' : ' studio--no-rail') + (chatOpen ? '' : ' studio--no-chat');
  const railSplit = (
    <div className="split split--right" title="拖动调整宽度"
         onPointerDown={(e) => {
           e.preventDefault();
           dragColumn('rail', e.clientX, railW, (w) => {
             const n = clamp(w, RAIL_MIN, RAIL_MAX);
             setRailW(n); lsSet('sd.railW', String(n));
           });
         }} />
  );
  const chatSplit = (
    <div className="split split--left" title="拖动调整宽度"
         onPointerDown={(e) => {
           e.preventDefault();
           dragColumn('chat', e.clientX, chatW, (w) => {
             const n = clamp(w, CHAT_MIN, CHAT_MAX);
             setChatW(n); lsSet('sd.chatW', String(n));
           });
         }} />
  );

  return (
    <div className={cls} style={{ '--rail-w': railW + 'px', '--chat-w': chatW + 'px' } as React.CSSProperties}>
      <ProjectRail projects={projects} signals={signals} pid={pid}
                   packs={health.packs || []} ratios={health.ratio_choices || []}
                   busy={busy} onPick={(v) => { setPid(v); setEp(1); setShot(''); }}
                   onNew={createProject} onBlank={newBlank}
                   runIds={runIds} onRename={renameProject}
                   onStop={stopRun} onDelete={deleteProject}
                   splitter={railSplit}
                   onCollapse={() => { setRailOpen(false); lsSet('sd.railOpen', '0'); }} />

      <CanvasPane pid={pid || '-'} name={projects.find((x) => x.id === pid)?.name}
                  ep={ep} doc={doc} docError={docError}
                  segments={segments} shot={shot} onShot={setShot}
                  onSave={saveShot} running={running}
                  toolbar={
                    <select value={ep} onChange={(e) => { setEp(Number(e.target.value)); setShot(''); }}
                            style={{ padding: '5px 9px', borderRadius: 9, border: 0, background: '#17171d', color: 'inherit', font: 'inherit' }}>
                      {(projects.find((x) => x.id === pid)?.episodes || []).map((e) => (
                        <option key={e.id} value={e.no}>第 {e.no} 集</option>
                      ))}
                    </select>
                  } />

      <DirectorChat chat={chat} chatError={chatError}
                    inbox={inbox} inboxError={inboxError} busy={busy} mine={mineIds}
                    manualSteps={manualSteps} onManualSteps={setManualSteps}
                    onAsk={ask} onStart={beginProduction}
                    onSend={send} onDecide={decide} onRedo={redo}
                    hasProject={!!pid} onStartNew={newChat}
                    splitter={chatSplit}
                    onCollapse={() => { setChatOpen(false); lsSet('sd.chatOpen', '0'); }} />

      {/* 收起后重新打开的小把手（贴对应边居中） */}
      {!railOpen ? (
        <button type="button" className="reopen reopen--left" title="展开项目栏"
                onClick={() => { setRailOpen(true); lsSet('sd.railOpen', '1'); }}>›</button>
      ) : null}
      {!chatOpen ? (
        <button type="button" className="reopen reopen--right" title="展开导演对话"
                onClick={() => { setChatOpen(true); lsSet('sd.chatOpen', '1'); }}>‹</button>
      ) : null}

      <div className="toast">
        {toasts.map((t) => <div key={t.id} data-t={t.bad ? 'bad' : 'ok'}>{t.text}</div>)}
      </div>
    </div>
  );
}
