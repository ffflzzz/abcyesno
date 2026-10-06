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
  askRedo, approveHitl, editShot, fetchCanvas, getInbox, isRunning, sendToDirector,
  startChain, type CanvasDoc, type InboxState,
} from '../lib/studio';

const errText = (e: unknown) => (e instanceof Error ? e.message : String(e));

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
        if (!pid && list.length) setPid(list[0].id);
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

  const beginProduction = async () => {
    setBusy(true);
    try {
      await startChain(`${pid}-ep${ep}`, manualSteps);
      toast(manualSteps ? '已起创作链（逐步确认开）' : '已起创作链（一路跑到底，留言排队）');
    } catch (e) { toast('起链失败：' + errText(e), true); } finally { setBusy(false); }
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
          <span className="inspector-hint">
            工作室由 shortdrama 后端同源托管（<code>/studio</code>）。
            从启动台点「短剧工作室」会自动起后端；直接开这个地址需要先跑
            <code>python -m v5.server</code>。
          </span>
        </div>
      </div>
    );
  }

  return (
    <div className="studio">
      <ProjectRail projects={projects} signals={signals} pid={pid}
                   packs={health.packs || []} ratios={health.ratio_choices || []}
                   busy={busy} onPick={(v) => { setPid(v); setEp(1); setShot(''); }}
                   onNew={createProject}
                   runIds={runIds} onRename={renameProject}
                   onStop={stopRun} onDelete={deleteProject} />

      <CanvasPane pid={pid || '-'} ep={ep} doc={doc} docError={docError}
                  segments={segments} shot={shot} onShot={setShot}
                  onSave={saveShot} running={running}
                  nodeCount={(inbox as unknown as { canvas_nodes?: number })?.canvas_nodes || 0}
                  toolbar={
                    <>
                      <select value={ep} onChange={(e) => { setEp(Number(e.target.value)); setShot(''); }}>
                        {(projects.find((x) => x.id === pid)?.episodes || []).map((e) => (
                          <option key={e.id} value={e.no}>第 {e.no} 集</option>
                        ))}
                      </select>
                      <button type="button" className="btn btn--sm btn--primary" disabled={busy || !pid}
                              onClick={() => void beginProduction()}>开始生产</button>
                      <label style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 12, color: '#8b8b98' }}>
                        <input type="checkbox" checked={manualSteps}
                               onChange={(e) => setManualSteps(e.target.checked)} />
                        逐步确认
                      </label>
                    </>
                  } />

      <div style={{ display: 'contents' }}>
        <DirectorChat inbox={inbox} error={inboxError} busy={busy} mine={mineIds}
                      onSend={send} onDecide={decide} onRedo={redo} />
      </div>

      <div className="toast">
        {toasts.map((t) => <div key={t.id} data-t={t.bad ? 'bad' : 'ok'}>{t.text}</div>)}
      </div>
    </div>
  );
}
