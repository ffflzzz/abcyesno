/* ==========================================================================
   src/pages/Wizard.tsx —— 短剧工作台三步向导（对应线上 /playlet/review/:pid）
     1. 剧本大纲   2. 资产库   3. 分集视频
   --------------------------------------------------------------------------
   从 `web/assets/js/views/wizard.js` 移植。**行为逐条对齐**，凡是旧版注释里
   带「★ / ⚠️ / ⛔」的判据都保留了 —— 那些都是用事故换来的。

   ⚠️ **一处刻意的不假装可用**（本项目纪律：不摆点了没反应的控件）：
     ① 资产抽屉（角色信息 / 新增角色）尚未移植 ⇒ 相关按钮打开的是**如实说明**，
        不是一个点开是空白的抽屉。

   创作链默认**一口气跑完 7 个角色**；要逐步停由第 3 步工具条上的「逐步确认」
   开关打开（见 `lib/chainMode.ts` —— 它是编译期生效，拨动后后端会重启 dev）。
   ========================================================================== */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Api } from '../api';
import { Router } from '../router';
import { episodeStats, fmtDuration, Store, useStore } from '../store';
import type { AssetItem, Episode, Project, RunRecord, Segment } from '../types';
import { Icon } from '../components/Icons';
import { ConfirmModal, Menu, Modal, PromptModal } from '../components/Overlay';
import { AssetDrawer } from '../components/AssetDrawer';
import { WorkbenchShell } from '../components/Shell';
import { ChainMode, MANUAL_STEPS_LABEL } from '../lib/chainMode';
import { roleLabel } from '../components/HitlBar';
import { GenOverlay } from '../lib/genOverlay';
import type { StepInfo } from '../components/Shell';
import { esc, render as renderScriptDoc } from '../lib/scriptdoc';

const STEPS = [
  { key: 'script', no: 1, label: '剧本大纲' },
  { key: 'assets', no: 2, label: '资产库' },
  { key: 'episodes', no: 3, label: '分集视频' },
] as const;

type StepKey = (typeof STEPS)[number]['key'];
type AssetKind = 'character' | 'scene' | 'prop';

const ASSET_TABS: { key: AssetKind; label: string }[] = [
  { key: 'character', label: '角色列表' },
  { key: 'scene', label: '场景列表' },
  { key: 'prop', label: '道具列表' },
];

const bucketOf = (p: Project, kind: AssetKind) => {
  const a = p.assets || { characters: [], scenes: [], props: [] };
  return (kind === 'character' ? a.characters : kind === 'scene' ? a.scenes : a.props) || [];
};
const kindLabel = (k: AssetKind) => (k === 'character' ? '角色' : k === 'scene' ? '场景' : '道具');

/** `str()` —— 只用于"是否为空"和只读展示。**正文不许过它**（见 scriptEditArea 注释）。 */
const str = (v: unknown) => String(v == null ? '' : v).trim();

/** 文本 → 触发下载。旧版用 Blob + `a.click()`，原样照搬。 */
function downloadText(text: string, name: string): void {
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

/**
 * 统一的「提交创作链 → 轮询 → 刷新」，把旧版那套**人工确认 + 过场让位**的处理收在一处。
 *
 * ## 为什么必须处理"人工确认条"
 * 逐步人工确认（前端起的 dev server **自带** `SHORTDRAMA_APPROVE_EACH_ROLE=1`）
 * 会让链**每次派发角色之前**停下来等人。那一刻若过场还盖着，用户看到的是一个
 * 永远转的圈，而链在等他点确认 —— 两边互等，界面还不说（本项目最忌的"静默"）。
 * ⇒ 判据：**确认条在场 = 人在等** ⇒ 过场让位；人点完（条消失）⇒ 过场回来。
 *    用"确认条在不在场"而不另开一个轮询：那个人机等待信号**已经有唯一来源**
 *    （`components/HitlBar.tsx`），再造一份必然漂移。
 *
 * ★ 挂起时要**重拉产物**：用户就是要"看过产物再决定"；若只在 run 结束时才刷新，
 *   挂起那一刻界面还是旧内容 ⇒ 那条确认条就只是**盲批**。
 * ★ 挂起提示**按 stamp 去重**（每 3 秒一次 tick 会刷屏）。
 */
function makeChainRunner(pid: string, eid: string | null, title: string, noun: string) {
  let runId = '';
  let lastStamp = '';
  let warnedNoSteps = false;
  // ★ 旧版实测：点「停止」后过场**又冒出来**（tick 见"没确认条且过场不在"就重新 show），
  //   用户以为没停成功 ⇒ 停止之后 tick 不许再把它拉起来。
  let stopping = false;
  const t0 = Date.now();
  const subOf = (secs: number) => '正在' + noun + '…'
    + (secs >= 15 ? ' 已 ' + secs + 's' : '')
    + '（每个角色开工前都会停下来等你确认）';

  const show = (secs: number) => {
    GenOverlay.show({ title, sub: subOf(secs), onStop: stop });
  };
  function stop() {
    stopping = true;
    GenOverlay.hide();
    if (runId) {
      Api.cancelRun(runId).then(
        () => Store.toast('已请求停止（人工结束，不算失败）'),
        (e: Error) => Store.toast('停止失败：' + e.message, 'error'),
      );
    } else {
      Store.toast('已停止等待（还没拿到任务号，后端可能仍在跑）');
    }
  }

  const refresh = async () => {
    const proj = await Api.getProgress(pid);
    Store.upsertProject(proj);
    if (eid) {
      try {
        const sb = await Api.getStoryboard(eid);
        Store.upsertStoryboard(pid, eid, sb);
      } catch { /* 分镜还没写到时会失败，不该拖垮刷新 */ }
    }
    return true;
  };

  return {
    /** 交给 `Api.runTask` 的 onTick */
    onTick(st: RunRecord) {
      const secs = Math.round((Date.now() - t0) / 1000);
      const h = (st && (st as { hitl?: { pending?: boolean; stamp?: string; prev_role?: string; manual_steps?: boolean | null } }).hitl) || {};
      if (stopping) {
        if (GenOverlay.visible()) GenOverlay.hide();
        return;
      }
      if (h.pending) {
        // 让位给人：确认条在场时不盖全屏
        if (GenOverlay.visible()) GenOverlay.hide();
        if (h.stamp === lastStamp) return;              // 同一步只提示一次
        lastStamp = String(h.stamp || '');
        Store.toast('链路停在「' + roleLabel(h.prev_role) + '」—— 请在底部条上点「继续」或「打回重做」');
        // 重拉本项目的产物，让用户**看得到**刚做完的东西
        void Api.getProgress(pid).then(
          (proj) => { Store.upsertProject(proj); },
          () => { /* 拉不到也别打断轮询 */ },
        );
        return;
      }
      // 用户开了「逐步确认」却仍在自动跑 ⇒ 值没生效，**必须说出来**（否则他会一直
      // 等那条永远不会出现的确认条）。自动模式本身是预期行为，不打扰。
      // 残留场景：dev server 是从别人手里**接管**的（后端不知道它的值 ⇒ `null`），
      // 那种 server 不会被我方重启，所以开关对它无效。
      if (!warnedNoSteps && st && st.status === 'running'
          && ChainMode.on() && h.manual_steps !== true) {
        warnedNoSteps = true;
        Store.toast('注意：你开了「逐步确认」，但当前 dev server 不会逐步停下'
          + '（它是接管来的、或值尚未生效）。可点底部「停止」后重新生成');
      }
      if (GenOverlay.visible()) GenOverlay.update({ sub: subOf(secs) });
      else show(secs);
    },
    onSubmitted(run: RunRecord) { runId = String((run && run.run_id) || ''); },
    refresh,
    seed(secs: number) { show(secs); },
    finish() { stopping = false; GenOverlay.hide(); },
  };
}

/**
 * 常驻顶栏上的「逐步确认」开关。
 *
 * 为什么在顶栏而不是某一步的内容区里：创作链从**第 1 步**「生成剧本正文」就会
 * 启动，摆到第 3 步等于"链都跑起来了才让你设"；而第 3 步同时还是媒体链的地盘，
 * 一个写着「创作链」的控件放那儿，读起来像在管分镜/出片。
 */
function ChainModeSwitch() {
  const [, bump] = useState(0);   // 值在模块里，不在 Store ⇒ 拨完自己触发重渲染
  const on = ChainMode.on();
  return (
    <span className="qc-switches">
      <span className="qc-label" title={MANUAL_STEPS_LABEL.hint}>创作链</span>
      <button type="button" className={'qc-chip' + (on ? ' is-on' : '')}
        aria-pressed={on} title={MANUAL_STEPS_LABEL.hint}
        onClick={() => {
          const now = ChainMode.toggle();
          Store.toast(now
            ? '逐步确认已打开：每个角色开工前会停下等你点「继续 / 打回重做」（下一次生成起生效）'
            : '逐步确认已关闭：创作链一口气跑完 7 个角色（下一次生成起生效）');
          bump((n) => n + 1);
        }}>{MANUAL_STEPS_LABEL.label}</button>
    </span>
  );
}

/**
 * 画幅选择器。
 *
 * 候选**来自后端**（`/health` 的 `ratio_choices`，单一来源是
 * `v5/config.py:RATIO_CHOICES`）—— agnes 没有公开的比例白名单，前端自己列值
 * 等于拿用户的项目去赌接口不报错。拉不到候选就退回只读展示（本项目纪律：
 * 不摆点了没反应的控件）。
 *
 * 值写进 `brief.json` 的 `ratio`，媒体链子进程按它注入 `SHORTDRAMA_STILL_RATIO`
 * 与 `SHORTDRAMA_ASPECT`（**两个一起改**，否则静帧与成片画幅不一致）。
 */
function RatioPicker({ project, pid }: { project: Project; pid: string }) {
  const [choices, setChoices] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const cur = project.ratio || '9:16';

  useEffect(() => {
    let alive = true;
    void Api.ping().then((h) => {
      if (alive) {
        setChoices((h as { ratio_choices?: string[] }).ratio_choices || []);
      }
    }).catch(() => { /* 拉不到 ⇒ 只读 */ });
    return () => { alive = false; };
  }, []);

  if (!choices.length) {
    return <span className="meta-chip">{Icon.canvas(16)} 视频比例：{cur}</span>;
  }

  const pick = async (r: string) => {
    if (r === cur || busy) return;
    setBusy(true);
    try {
      await Api.updateOutline(pid, { ratio: r });
      Store.upsertProject(await Api.getProgress(pid));
      Store.toast('画幅已设为 ' + r
        + '：对**之后生成**的静帧与视频生效，已出过的不会自动重做');
    } catch (e) {
      Store.toast('画幅设置失败：' + (e as Error).message, 'error');
    } finally {
      setBusy(false);
    }
  };

  return (
    <span className="qc-switches">
      <span className="qc-label" title="画幅对之后生成的静帧与视频生效；已出过的产物不会自动重做">
        视频比例
      </span>
      {choices.map((r) => (
        <button key={r} type="button" disabled={busy}
          className={'qc-chip' + (r === cur ? ' is-on' : '')}
          aria-pressed={r === cur}
          onClick={() => { void pick(r); }}>{r}</button>
      ))}
    </span>
  );
}

export function Wizard({ pid, step }: { pid: string; step?: string }) {
  const { styles } = useStore();   // 订阅一次，保证 store 变更能重渲染
  void styles;
  const project = Store.getProject(pid);

  const [assetTab, setAssetTab] = useState<AssetKind>('character');
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [scriptEdit, setScriptEdit] = useState<string | null>(null);
  const [outlineConfirmed, setOutlineConfirmed] = useState(false);
  /** 只用来**禁用按钮**（不再渲染弹窗）；长任务的进度由全屏过场负责。 */
  const [busy, setBusy] = useState<string | null>(null);
  const [menu, setMenu] = useState<{ rect: DOMRect; el: HTMLElement; items: MenuItemLike[] } | null>(null);
  const [modal, setModal] = useState<ModalState | null>(null);

  const cur: StepKey = (STEPS.some((s) => s.key === step) ? step : 'script') as StepKey;
  const idx = STEPS.findIndex((s) => s.key === cur);
  const steps: StepInfo[] = STEPS.map((s, i) => ({
    key: s.key, no: s.no, label: s.label,
    state: i < idx ? 'done' : (i === idx ? 'active' : 'todo'),
  }));

  const goStep = useCallback((k: string) => {
    Router.go('/playlet/review/' + pid + '?step=' + k, true);   // replace：切步骤不塞历史记录
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }, [pid]);

  const openMenu = (e: React.MouseEvent, items: MenuItemLike[]) => {
    e.stopPropagation();
    const el = e.currentTarget as HTMLElement;
    setMenu({ rect: el.getBoundingClientRect(), el, items });
  };

  if (!project) {
    return (
      <WorkbenchShell title="项目不存在" steps={[]}>
        <div className="content">
          <div className="empty">
            {Icon.empty(48)}
            <div>项目不存在或已被删除</div>
            <button type="button" className="btn btn--sm mt16" onClick={() => Router.go('/playlet/list')}>
              返回短剧列表
            </button>
          </div>
        </div>
      </WorkbenchShell>
    );
  }

  return (
    <WorkbenchShell title={project.name} steps={steps} onStep={goStep}
                    tools={<ChainModeSwitch />}>
      <div className="content">
        <h1 className="page-title">{project.name}</h1>
        <div className="meta-row">
          <span className="meta-chip">{Icon.film(16)} 视频风格：{project.style?.name || '未设置'}</span>
          <RatioPicker project={project} pid={pid} />
        </div>

        {cur === 'script' ? (
          <StepScript
            p={project}
            pid={pid}
            collapsed={collapsed} setCollapsed={setCollapsed}
            scriptEdit={scriptEdit} setScriptEdit={setScriptEdit}
            outlineConfirmed={outlineConfirmed} setOutlineConfirmed={setOutlineConfirmed}
            busy={busy} setBusy={setBusy}
            openMenu={openMenu}
            setModal={setModal}
            goStep={goStep}
          />
        ) : null}

        {cur === 'assets' ? (
          <StepAssets
            p={project} pid={pid}
            assetTab={assetTab} setAssetTab={setAssetTab}
            openMenu={openMenu} setModal={setModal} setBusy={setBusy} busy={busy}
            goStep={goStep}
          />
        ) : null}

        {cur === 'episodes' ? (
          <StepEpisodes
            p={project} pid={pid}
            openMenu={openMenu} setModal={setModal} setBusy={setBusy} busy={busy}
          />
        ) : null}
      </div>

      {menu ? (
        <Menu
          anchor={menu.rect}
          anchorEl={menu.el}
          onClose={() => setMenu(null)}
          items={menu.items.map((it) => ({
            label: it.label, danger: it.danger,
            action: () => { setMenu(null); it.action(); },
          }))}
        />
      ) : null}

      {modal ? <WizardModals m={modal} onClose={() => setModal(null)} p={project} pid={pid} /> : null}
    </WorkbenchShell>
  );
}

/* ------------------------------------------------------------------ 类型 */

interface MenuItemLike { label: string; danger?: boolean; action: () => void }

type ModalState =
  | { kind: 'outline' }
  | { kind: 'asset-rename'; kindOf: AssetKind; id: string; name: string }
  | { kind: 'asset-delete'; kindOf: AssetKind; id: string; name: string }
  | { kind: 'asset-edit'; kindOf: AssetKind; id: string; refId?: string | null }
  | { kind: 'asset-new'; kindOf: AssetKind }
  | { kind: 'new-episode'; no: number }
  | { kind: 'delete-episode'; eid: string; title: string }
  | { kind: 'export'; eid: string };

/* ---------------------------------------------------------------- 忙碌状态

   ⛔ 这里**故意没有"忙碌弹窗"**。曾经有一个：标题「正在处理…」、两个按钮
      （取消 / 知道了），而它们的处理函数是**空的**（当时的想法是"关掉也不该
      取消请求"，于是干脆什么都不做）。
      后果是**标准的"死按钮"**：用户点了没反应，以为界面坏了 ——
      正是本项目最忌的「摆了点了没反应的控件」，而且是我自己犯的。

      为什么现在整个删掉而不是把按钮接上：
        · 长任务（生成剧本正文 / 分镜脚本 / 资产图）已经有**全屏过场**
          （`GenOverlay`）在显示进度，而且那个「停止」是**真能用的**；
        · 两个层同时存在会互相打架（弹窗 1500 盖着过场 1200），
          用户看到的是一堆叠在一起的东西，不知道该点哪个。
      ⇒ `busy` 现在**只用来禁用按钮**，不再渲染任何弹窗。

   ★ 纪律（写下来避免再犯）：**永远不要 ship 一个什么都不做的处理函数。**
      若某个动作不该关闭界面，就让它关闭并**如实说明**（toast），
      或者干脆不放这个按钮。 */

/* ---------------------------------------------------------------- 第 1 步 */

function StepScript({ p, pid, collapsed, setCollapsed, scriptEdit, setScriptEdit, outlineConfirmed, setOutlineConfirmed, setBusy, openMenu, setModal, goStep }: {
  p: Project; pid: string;
  collapsed: Record<string, boolean>; setCollapsed: (v: Record<string, boolean>) => void;
  scriptEdit: string | null; setScriptEdit: (v: string | null) => void;
  outlineConfirmed: boolean; setOutlineConfirmed: (v: boolean) => void;
  busy: string | null; setBusy: (v: string | null) => void;
  openMenu: (e: React.MouseEvent, items: MenuItemLike[]) => void;
  setModal: (m: ModalState | null) => void;
  goStep: (k: string) => void;
}) {
  const o = (p.outline || {}) as Record<string, unknown>;

  const anyMissing = p.episodes.some((ep) => !str(ep.script));
  const firstMissing = p.episodes.filter((ep) => !str(ep.script))[0] || null;

  /**
   * 两段式：**正文还没生成**时这一步的语义是"确认简介 → 生成剧本正文"；
   * 正文已存在（粘贴剧本建的项目）则只是确认。
   */
  const onConfirmOutline = async () => {
    if (!firstMissing) {
      setOutlineConfirmed(true);
      Store.toast('概要已确认', 'ok');
      window.scrollTo({ top: 0, behavior: 'smooth' });
      return;
    }
    const ep = firstMissing;
    const run = makeChainRunner(pid, ep.id, '正在生成剧本内容...', '写第 ' + ep.no + ' 集的剧本正文');
    setBusy('生成剧本正文');
    Store.toast('已提交「生成剧本正文」…');
    run.seed(0);
    try {
      // ★ `timeoutMs: 0` = **不限时**：开了逐步确认后，耗时单位是**人**不是分钟。
      //   用默认 60 分钟会在人还没点的时候就报"前端等待超时"，而任务好端端停着等人。
      const st = await Api.runTask(
        () => Api.generateScript(ep.id, {}),
        {
          timeoutMs: 0,
          onTick: run.onTick,
          onSubmitted: run.onSubmitted,
          refresh: run.refresh,
        },
      );
      if (st.status === 'ok') {
        setOutlineConfirmed(true);
        Store.toast('剧本正文已生成，请过目', 'ok');
        window.scrollTo({ top: 0, behavior: 'smooth' });
      } else if (st.status === 'cancelled') {
        Store.toast('已停止（人工结束）—— 正文可能只完成了一部分');
      } else {
        Store.toast('生成剧本正文未成功（' + st.status + '）', 'error');
      }
    } catch (e) {
      Store.toast('生成剧本正文失败：' + (e as Error).message, 'error');
    } finally {
      run.finish();
      setBusy(null);
    }
  };

  const onRunAnalysis = async () => {
    setBusy('正在分析资产… 正在识别剧本中的场景元素…');
    try {
      await Api.extractAssets(pid);
      const proj = await Api.getProgress(pid);
      Store.upsertProject(proj);
      Store.toast('资产分析完成', 'ok');
      goStep('assets');
    } catch (e) {
      Store.toast('分析失败：' + (e as Error).message, 'error');
    } finally { setBusy(null); }
  };

  return (
    <div className="section-gap split-2">
      <InfoCard o={o} p={p} onEdit={() => setModal({ kind: 'outline' })} />

      <div className="doc-section">
        <div className="script-head">
          <h2>剧本内容</h2>
          <button type="button" className="icon-btn" title="剧本操作"
            onClick={(e) => {
              const first = p.episodes[0];
              openMenu(e, [{
                label: '下载剧本',
                action: () => {
                  if (!first) return;
                  downloadText(str(first.script), p.name + '-第' + first.no + '集.txt');
                  Store.toast('剧本已下载', 'ok');
                },
              }]);
            }}>{Icon.ellipsis(20)}</button>
        </div>
        {p.episodes.map((ep) => (
          <EpisodeBlock
            key={ep.id} ep={ep} pid={pid}
            collapsed={!!collapsed[p.id + ':' + ep.id]}
            editing={scriptEdit === p.id + ':' + ep.id}
            onToggleCollapse={() => {
              const k = p.id + ':' + ep.id;
              setCollapsed(Object.assign({}, collapsed, { [k]: !collapsed[k] }));
            }}
            onToggleEdit={() => {
              const k = p.id + ':' + ep.id;
              // 只允许一个分集处于编辑态 —— 多开 textarea 会让"完成"按钮指代不明
              setScriptEdit(scriptEdit === k ? null : k);
            }}
          />
        ))}
      </div>

      <div className="sticky-bar">
        <span className="tip">
          {Icon.check(20)}{' '}
          {outlineConfirmed
            ? '概要已确认，开始分析资产'
            : (anyMissing ? '确认简介后生成剧本正文' : '请确认脚本概要以供生成')}
        </span>
        <button
          type="button" className="btn btn--primary btn--sm"
          onClick={() => (outlineConfirmed ? onRunAnalysis() : onConfirmOutline())}
        >
          {outlineConfirmed ? '生成' : '下一步'}
        </button>
      </div>
    </div>
  );
}

/** 简介卡：字段**按序、跳过空值**（满屏 `—` 只会把真内容淹没）。 */
function InfoCard({ o, p, onEdit }: { o: Record<string, unknown>; p: Project; onEdit: () => void }) {
  const rows: React.ReactNode[] = [];
  rows.push(<Field key="eps" k="剧集" v={String(p.episodes.length)} />);
  if (str(o.story_type)) rows.push(<Field key="st" k="故事类型" v={String(o.story_type)} />);
  if (str(o.target_audience)) rows.push(<Field key="ta" k="目标受众" v={String(o.target_audience)} />);

  /**
   * 剧情概要：**有独立概要就是一段话；只有 must_have 时才分条**。
   * 怎么知道当前是哪一种：后端把 must_have 的**原样列表**放在 `story_summary_items`，
   * 而 `story_summary` 是它的 `" / "` join（同一个来源）。
   * 两者相等 ⇒ 这段"概要"其实就是 must_have ⇒ 该分条。等号写法让判据只有一处。
   */
  const items = (o.story_summary_items as string[]) || [];
  const text = str(o.story_summary);
  const isMustHave = items.length > 1 && text === items.join(' / ');
  if (isMustHave) {
    rows.push(
      <div className="info-field" key="sum">
        <span className="k">剧情概要</span>
        <div className="hint">
          本片没有独立概要，以下是 brief 的 must_have 硬要求（共 {items.length} 条）
        </div>
        <ol className="summary-list">
          {items.map((x, i) => <li key={i} dangerouslySetInnerHTML={{ __html: boldLead(x) }} />)}
        </ol>
      </div>,
    );
  } else {
    rows.push(<Field key="sum" k="剧情概要" v={text || str(o.one_line_story) || '—'} />);
  }

  if (str(o.world_setting)) rows.push(<Field key="ws" k="世界观设定" v={String(o.world_setting)} />);

  const bios = ((o.character_biographies as { name?: string; bio?: string }[]) || [])
    .filter((b) => b && str(b.name));
  if (bios.length) {
    rows.push(
      <div className="info-field" key="bio">
        <span className="k">角色设定</span>
        <div className="bio-list">
          {bios.map((b, i) => {
            const name = str(b.name);
            const bio = str(b.bio);
            // 手工添加的资产 `identity` 会等于名字本身 ⇒ 再显示一遍就是重复
            return (
              <div className="bio" key={i}>
                <span className="bio-name">{name}</span>
                {bio && bio !== name ? <p className="bio-text">{bio}</p> : null}
              </div>
            );
          })}
        </div>
      </div>,
    );
  }

  return (
    <div className="info-card">
      <div className="info-card-head">
        <h2>简介</h2>
        <button type="button" className="icon-btn" title="编辑简介" onClick={onEdit}>{Icon.edit(18)}</button>
      </div>
      {rows}
    </div>
  );
}

/** `第一幕(钩子)：清早……` → **粗体幕名**：正文。没有前导标签就原样返回。 */
function boldLead(s: string): string {
  const m = /^([^：:]{1,14})[：:]\s*(.+)$/.exec(str(s));
  return m ? '<b>' + esc(m[1]) + '</b>：' + esc(m[2]) : esc(str(s));
}

function Field({ k, v }: { k: string; v: string }) {
  const s = str(v);
  return (
    <div className="info-field">
      <span className="k">{k}</span>
      <span className={'v' + (s.length > 120 ? '' : ' small')}>{s}</span>
    </div>
  );
}

function EpisodeBlock({ ep, pid, collapsed, editing, onToggleCollapse, onToggleEdit }: {
  ep: Episode; pid: string;
  collapsed: boolean; editing: boolean;
  onToggleCollapse: () => void; onToggleEdit: () => void;
}) {
  return (
    <div className="episode-block">
      <div className="episode-row">
        <button
          type="button" className={'episode-caret' + (collapsed ? ' collapsed' : '')}
          aria-label="收起分集" onClick={onToggleCollapse}
        >{Icon.chevronDown(20)}</button>
        <span className="episode-title">第{ep.no}集</span>
        <input
          className="episode-name-input" defaultValue={ep.title}
          title="改标题只改本地显示：后端没有分集改名接口（旧版也是这样）"
          style={{ width: Math.max(3, ep.title.length + 1) + 'ch' }}
          onBlur={(e) => {
            const v = e.target.value.trim() || '未命名';
            if (v !== ep.title) Store.patchEpisode(pid, ep.id, { title: v });
          }}
          onKeyDown={(e) => { if (e.key === 'Enter') (e.target as HTMLInputElement).blur(); }}
        />
        <span className={'badge ' + (ep.script_status === 'completed' ? 'badge--done' : 'badge--warn')}>
          {ep.script_status === 'completed' ? '已完成' : '待完善'}
        </span>
        <button type="button" className="ep-edit" onClick={onToggleEdit}>
          {editing ? '完成' : '编辑剧本'}
        </button>
      </div>
      {collapsed ? null : (editing ? <ScriptEditArea ep={ep} pid={pid} /> : <ScriptDocView ep={ep} />)}
    </div>
  );
}

/** 编辑态。
 * ⚠️ **正文用原样字符串，不能过 `str()`**（实测：`str()` 的 `trim()` 会吃掉结尾换行，
 *   于是编辑框比磁盘少 1 个字符 —— 用户只要在框里动一下再点「完成」，回写就把文件改短了）。
 */
function ScriptEditArea({ ep, pid }: { ep: Episode; pid: string }) {
  const raw = ep.script == null ? '' : String(ep.script);
  const [val, setVal] = useState(raw);
  const timer = useRef<number | null>(null);

  useEffect(() => () => { if (timer.current) window.clearTimeout(timer.current); }, []);

  const onChange = (v: string) => {
    setVal(v);
    // 本地先落，界面立刻跟上；**不重新拉取**（否则 textarea 会被重建、焦点丢失）
    Store.patchEpisode(pid, ep.id, { script: v });
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      Api.updateScript(pid, ep.id, v).then(
        () => Store.toast('剧本已保存', 'ok'),
        (e: Error) => Store.toast('剧本保存失败：' + e.message, 'error'),
      );
    }, 800);
  };

  return (
    <>
      <textarea
        className="script-textarea" placeholder="在此填写本集剧本正文…"
        value={val} onChange={(e) => onChange(e.target.value)}
      />
      <div className="hint">
        编辑中。停止输入约 1 秒后自动保存。可用 markdown 式标记
        （`# 小节`、`&gt; 元信息`、`**场景：** …`），点「完成」后按上面的版式显示。
      </div>
    </>
  );
}

/** 只读富文本剧本。空正文要**说清下一步**，不要留一片空白。 */
function ScriptDocView({ ep }: { ep: Episode }) {
  const src = str(ep.script);
  if (!src) {
    return (
      <div className="sd sd--empty">
        <div className="sd-empty-t">本集还没有剧本正文</div>
        <div>
          点下方「<b>下一步</b>」：先确认左边的简介，然后由创作链写剧本正文
          （只跑到 scriptwriter —— 资产卡 / 分镜等正文确认之后再跑）。
        </div>
        <div className="small">也可以点右上「编辑剧本」自己写。</div>
      </div>
    );
  }
  const r = useMemo(() => renderScriptDoc(src), [src]);
  return <div className="sd" dangerouslySetInnerHTML={{ __html: r.html }} />;
}

/* ---------------------------------------------------------------- 第 2 步 */

function StepAssets({ p, pid, assetTab, setAssetTab, openMenu, setModal, setBusy, busy, goStep }: {
  p: Project; pid: string;
  assetTab: AssetKind; setAssetTab: (k: AssetKind) => void;
  openMenu: (e: React.MouseEvent, items: MenuItemLike[]) => void;
  setModal: (m: ModalState | null) => void;
  setBusy: (v: string | null) => void; busy: string | null;
  goStep: (k: string) => void;
}) {
  const bucket = bucketOf(p, assetTab);
  return (
    <div className="section-gap">
      <div>
        <div className="asset-toolbar">
          <div className="asset-tabs">
            {ASSET_TABS.map((t) => {
              const list = bucketOf(p, t.key);
              const pending = list.filter((a) => !(a.states || []).length).length;
              return (
                <button key={t.key} type="button"
                  className={'asset-tab' + (assetTab === t.key ? ' is-active' : '')}
                  onClick={() => setAssetTab(t.key)}>
                  {t.label}
                  {pending ? <span className="dot" title={pending + ' 项未生成'} /> : null}
                </button>
              );
            })}
          </div>
          <div className="asset-tools">
            <button type="button" className="tool-link" onClick={async () => {
              try {
                // `extractAssets` = POST /auto-sync-assets（把 images/ 里未登记的图
                // 收编进注册表）。**不是** `assets/finalize` —— 那是线上 SaaS 的
                // 「资产定稿」，v5 没有这个语义，调它只会吃 501。
                await Api.extractAssets(pid);
                const proj = await Api.getProgress(pid);
                Store.upsertProject(proj);
                Store.toast('资产同步成功', 'ok');
              } catch (e) { Store.toast('同步失败：' + (e as Error).message, 'error'); }
            }}>{Icon.sync(16)} 同步资产</button>
            <span className="tool-link">自动同步</span>
            <span
              className={'switch' + (p.auto_sync_assets ? ' on' : '')}
              role="switch" aria-checked={!!p.auto_sync_assets}
              title="自动同步开关"
              onClick={async () => {
                const next = !p.auto_sync_assets;
                try {
                  await Api.patchAutoSync(pid, next);
                  Store.patchProject(pid, { auto_sync_assets: next });
                  Store.toast(next ? '已开启自动同步' : '已关闭自动同步', 'ok');
                } catch (e) { Store.toast('切换失败：' + (e as Error).message, 'error'); }
              }}
            />
          </div>
        </div>

        <div className="asset-banner">
          <div>
            <div className="t">新增{kindLabel(assetTab)}</div>
            <div className="s">创建空白{kindLabel(assetTab)}或从素材添加</div>
          </div>
          <div className="row gap8">
            {[...(p.assets?.characters || []), ...(p.assets?.scenes || []), ...(p.assets?.props || [])]
              .some((a) => !(a.states || []).length) ? (
              <button type="button" className="btn btn--sm" disabled={!!busy} onClick={async () => {
                setBusy('正在生成资产图片…（分钟级任务）');
                try {
                  const st = await Api.runTask(
                    () => Api.generateAssetImages(pid, ['character', 'scene', 'prop']) as Promise<{ run_id?: string }>,
                    { refresh: () => Api.getProgress(pid).then((proj) => { Store.upsertProject(proj); return true; }) },
                  );
                  Store.toast(st.status === 'ok' ? '资产图片已生成' : '资产图任务未成功（' + st.status + '）',
                    st.status === 'ok' ? 'ok' : 'error');
                } catch (e) {
                  Store.toast('生成失败：' + (e as Error).message, 'error');
                } finally { setBusy(null); }
              }}>生成全部图片</button>
            ) : null}
            <button type="button" className="btn btn--primary btn--sm"
              onClick={() => setModal({ kind: 'asset-new', kindOf: assetTab })}>
              {Icon.plus(16)} 添加
            </button>
          </div>
        </div>

        {/* ⛔ `.asset-grid` 必须是 banner 的**兄弟**，不能嵌进去：
            `.asset-banner` 是 `display:flex; justify-content:space-between`，
            网格嵌进去就成了 flex 的第 3 项，被挤成右侧一条窄列 ⇒ 左边半块空白。 */}
        <div className="asset-grid">
          {bucket.length
            ? bucket.map((a) => (
              <AssetCard key={a.id} a={a} kind={assetTab}
                openMenu={openMenu} setModal={setModal} />
            ))
            : <div className="empty">{Icon.empty(48)}<div>暂无资产，点击右上角「添加」创建</div></div>}
        </div>
      </div>

      <div className="sticky-bar">
        <span className="tip">{Icon.check(20)} 本页资产将应用于整个项目</span>
        <button type="button" className="btn btn--primary btn--sm" onClick={() => goStep('episodes')}>下一步</button>
      </div>
    </div>
  );
}

function AssetCard({ a, kind, openMenu, setModal }: {
  a: AssetItem; kind: AssetKind;
  openMenu: (e: React.MouseEvent, items: MenuItemLike[]) => void;
  setModal: (m: ModalState | null) => void;
}) {
  const states = a.states || [];
  const def = states.find((s) => s.is_default) || states[0];
  /**
   * ★★ 场景**按设计不生参考图**（`cast.ensure`：location 只登记、不生图）——
   * 场景图自带固定机位，绑进分镜会**覆盖分镜的景别/机位**。
   * 旧实现却给它渲染「未生成 + 再次生成」，用户点了永远没用、列表看起来永远空白。
   * ⇒ 这里**如实说明**，并把那个死控件拿掉（纪律：不摆点了没反应的控件）。
   */
  const noImg = !(def && def.image);
  const sceneNoRef = kind === 'scene' && noImg;
  const withImg = states.filter((s) => s.image).length;

  return (
    <div className="asset-card">
      <div className="asset-thumb">
        {def && def.image
          ? <img src={def.image} alt="" />
          : (sceneNoRef
            ? <span className="placeholder placeholder--scene">场景不生图<br /><span className="small">固定机位会覆盖分镜</span></span>
            : <span className="placeholder">未生成</span>)}
        {def ? <span className="tag">{states.length > 1 ? states.length + ' 形象' : '基础形象'}</span> : null}
        {sceneNoRef ? null : (
          <button type="button" className="asset-refresh"
            onClick={() => setModal({ kind: 'asset-edit', kindOf: kind, id: a.id })}>
            {Icon.refresh(14)} 再次生成
          </button>
        )}
      </div>
      <div className="asset-info">
        <div className="name-row">
          <span className="name">{a.name}</span>
          <button type="button" className="icon-btn" onClick={(e) => openMenu(e, [
            { label: '编辑形象', action: () => setModal({ kind: 'asset-edit', kindOf: kind, id: a.id }) },
            { label: '重命名', action: () => setModal({ kind: 'asset-rename', kindOf: kind, id: a.id, name: a.name }) },
            { label: '删除', danger: true, action: () => setModal({ kind: 'asset-delete', kindOf: kind, id: a.id, name: a.name }) },
          ])}>{Icon.ellipsis(18)}</button>
        </div>
        <div className="states">
          {sceneNoRef
            ? <span className="label">场景不进参考图 · 分镜里按文字锚点注入</span>
            : (
              <>
                {/* ⚠️ 原文案「已添加形象 N/1」**误导**：它数的是**形象槽位**
                    （v5 每个资产恒为 1 个「基础形象」），不是"有几张图"。
                    于是"未生成"的资产也写着「已添加形象 1/1」，用户以为图已经有了。
                    改成如实说：槽位数 + **是否已有图**。 */}
                <span className="label">
                  形象 {states.length}/1 · {withImg ? '已生成 ' + withImg : '待生成'}
                </span>
                {states.map((s, i) => (
                  <span key={i} className="state-thumb" title={s.display_name || ''}
                    onClick={() => setModal({ kind: 'asset-edit', kindOf: kind, id: a.id, refId: s.ref_id })}>
                    {s.image ? <img src={s.image} alt="" /> : null}
                  </span>
                ))}
                <button type="button" className="add-state" title="添加形象"
                  onClick={() => setModal({ kind: 'asset-edit', kindOf: kind, id: a.id })}>
                  {Icon.plus(16)}
                </button>
              </>
            )}
        </div>
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- 第 3 步 */

function StepEpisodes({ p, pid, openMenu, setModal, setBusy, busy }: {
  p: Project; pid: string;
  openMenu: (e: React.MouseEvent, items: MenuItemLike[]) => void;
  setModal: (m: ModalState | null) => void;
  setBusy: (v: string | null) => void; busy: string | null;
}) {
  const genStoryboard = async (ep: Episode) => {
    const run = makeChainRunner(pid, ep.id, '正在生成分镜脚本...', '跑创作链（7 个角色）');
    setBusy('创作链运行中');
    Store.toast('已提交创作链（7 个角色）—— 可以先去别的页面');
    run.seed(0);
    try {
      // `timeoutMs` 6 小时：**开了逐步确认时耗时单位是人**，默认 60 分钟不够
      const st = await Api.runTask(
        () => Api.generateStoryboard(pid, ep.id) as Promise<RunRecord>,
        {
          timeoutMs: 6 * 60 * 60 * 1000,
          onTick: run.onTick,
          onSubmitted: run.onSubmitted,
          refresh: run.refresh,
        },
      );
      if (st.status === 'ok') Store.toast('分镜脚本已生成', 'ok');
      else if (st.status === 'failed') {
        const r = (st.result || {}) as { reason?: string };
        Store.toast('创作链未完成：' + (r.reason || st.note || ''), 'error');
      } else Store.toast('创作链未成功（' + st.status + '）', 'error');
    } catch (e) {
      Store.toast('生成失败：' + (e as Error).message, 'error');
    } finally {
      run.finish();
      setBusy(null);
    }
  };

  return (
    <div className="section-gap">
      <div>
        <div className="ep-toolbar">
          <div className="left">{p.episodes.length}集</div>
          <div className="right">
            <button type="button" className="btn btn--sm"
              onClick={() => setModal({
                kind: 'new-episode',
                no: p.episodes.reduce((a, e) => Math.max(a, e.no), 0) + 1,
              })}>
              {Icon.plus(16)} 新剧集
            </button>
            <button type="button" className="btn btn--primary btn--sm"
              onClick={(e) => openMenu(e, [
                { label: '全选', action: () => Store.toast('已全选（演示）') },
                { label: '批量删除', danger: true, action: () => Store.toast('批量删除（演示）') },
              ])}>批量选择</button>
          </div>
        </div>
        <div className="ep-list">
          {p.episodes.map((ep) => {
            const st = episodeStats(ep);
            const segs: Segment[] = (ep.storyboard?.segments) || [];
            const poster = (segs.find((s) => s.keyframe) || {}).keyframe || '';
            const hasSb = segs.length > 0;
            const assets = p.assets || { characters: [], scenes: [], props: [] };
            return (
              <article className="ep-card" key={ep.id}
                onClick={(e) => {
                  if ((e.target as HTMLElement).closest('[data-action]')) return;
                  // 线上：无分镜脚本不进编辑器
                  if (!hasSb) { Store.toast('请先生成分镜脚本'); return; }
                  Router.go('/playlet/review/' + pid + '/episode/' + ep.id);
                }}>
                <span className="ep-index">{ep.no}</span>
                <div className="ep-poster">
                  {poster ? <img src={poster} alt="" loading="lazy" /> : null}
                  <span className="dur">{fmtDuration(st.durationMs)}</span>
                  <span className="play">{Icon.play(28)}</span>
                </div>
                <div className="ep-body">
                  <div className="name">{ep.title}</div>
                  <div className="stats">
                    {assets.characters.length}个角色<span className="muted">·</span>
                    {assets.scenes.length}个场景<span className="muted">·</span>
                    {st.shots}个镜头
                  </div>
                </div>
                <div className="ep-actions">
                  {!hasSb ? (
                    <button type="button" data-action="gen-sb" className="btn btn--primary btn--sm"
                      disabled={!!busy} onClick={() => genStoryboard(ep)}>生成分镜脚本</button>
                  ) : null}
                  <button type="button" data-action="export" className="btn btn--sm"
                    onClick={(e) => { e.stopPropagation(); setModal({ kind: 'export', eid: ep.id }); }}>导出</button>
                  <button type="button" data-action="ep-menu" className="icon-btn" aria-label="剧集操作"
                    onClick={(e) => openMenu(e, [
                      {
                        label: '打开分镜',
                        action: () => Router.go('/playlet/review/' + pid + '/episode/' + ep.id),
                      },
                      { label: '导出', action: () => setModal({ kind: 'export', eid: ep.id }) },
                      {
                        label: '删除剧集', danger: true,
                        action: () => setModal({ kind: 'delete-episode', eid: ep.id, title: ep.title }),
                      },
                    ])}>{Icon.ellipsis(20)}</button>
                </div>
              </article>
            );
          })}
        </div>
      </div>

      <div className="sticky-bar">
        <span className="tip">{Icon.check(20)} 资产已同步，可进入分镜编辑</span>
        <button type="button" className="btn btn--primary btn--sm"
          onClick={() => Store.toast('流程已完成，可进入分镜编辑', 'ok')}>完成</button>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ 弹层 */

function WizardModals({ m, onClose, p, pid }: { m: ModalState; onClose: () => void; p: Project; pid: string }) {
  if (m.kind === 'outline') {
    return <OutlineModal p={p} pid={pid} onClose={onClose} />;
  }

  if (m.kind === 'asset-edit' || m.kind === 'asset-new') {
    return (
      <AssetDrawer
        pid={pid}
        kind={m.kindOf}
        assetId={m.kind === 'asset-edit' ? m.id : null}
        refId={m.kind === 'asset-edit' ? (m.refId || null) : null}
        onClose={onClose}
        // 保存/生成之后重拉项目：抽屉自己不持有列表，页面才是唯一渲染方
        onSaved={() => {
          void Api.getProgress(pid).then(
            (proj) => { Store.upsertProject(proj); },
            (e: Error) => Store.toast('刷新失败：' + e.message, 'error'),
          );
        }}
      />
    );
  }

  if (m.kind === 'asset-rename') {
    return (
      <PromptModal
        title="重命名" value={m.name} max={40} placeholder="资产名称" confirmText="确认"
        onClose={onClose}
        onConfirm={async (v) => {
          onClose();
          Store.toast('重命名只改本地显示：后端暂无该接口（旧版同样是本地改名）');
          const proj = Store.getProject(pid);
          if (!proj || !proj.assets) return;
          const key = m.kindOf === 'character' ? 'characters' : m.kindOf === 'scene' ? 'scenes' : 'props';
          const list = (proj.assets as unknown as Record<string, { id: string; name: string }[]>)[key]
            .map((a) => (a.id === m.id ? Object.assign({}, a, { name: v }) : a));
          Store.patchProject(pid, {
            assets: Object.assign({}, proj.assets, { [key]: list }),
          } as Partial<Project>);
        }}
      />
    );
  }

  if (m.kind === 'asset-delete') {
    return (
      <ConfirmModal
        title="删除资产" danger confirmText="删除"
        text="删除后该资产在分镜中的引用将失效，确定删除？"
        onClose={onClose}
        onOk={async () => {
          onClose();
          try {
            await Api.deleteAsset(pid, m.kindOf, m.id);
            const proj = await Api.getProgress(pid);
            Store.upsertProject(proj);
            Store.toast('已删除');
          } catch (e) { Store.toast('删除失败：' + (e as Error).message, 'error'); }
        }}
      />
    );
  }

  if (m.kind === 'new-episode') {
    return (
      <PromptModal
        title="新剧集" max={20} placeholder="输入剧集名称" confirmText="确认"
        onClose={onClose}
        onConfirm={async (title) => {
          onClose();
          try {
            await Api.createEpisode(pid, m.no, title);
            const proj = await Api.getProgress(pid);
            Store.upsertProject(proj);
            Store.toast('已新增剧集', 'ok');
          } catch (e) { Store.toast('新增失败：' + (e as Error).message, 'error'); }
        }}
      />
    );
  }

  if (m.kind === 'delete-episode') {
    return (
      <ConfirmModal
        title="删除剧集" danger confirmText="删除"
        text={'确定删除「' + m.title + '」？分镜与生成素材将一并删除。'}
        onClose={onClose}
        onOk={async () => {
          onClose();
          try {
            await Api.deleteEpisode(pid, m.eid);
            const proj = await Api.getProgress(pid);
            Store.upsertProject(proj);
            Store.toast('已删除');
          } catch (e) { Store.toast('删除失败：' + (e as Error).message, 'error'); }
        }}
      />
    );
  }

  if (m.kind === 'export') {
    return <ExportModal eid={m.eid} p={p} onClose={onClose} />;
  }

  return null;
}

function OutlineModal({ p, pid, onClose }: { p: Project; pid: string; onClose: () => void }) {
  const o = (p.outline || {}) as Record<string, string>;
  const [f, setF] = useState({
    story_type: o.story_type || '',
    target_audience: o.target_audience || '',
    one_line_story: o.one_line_story || '',
    story_summary: o.story_summary || '',
    world_setting: o.world_setting || '',
  });
  const field = (label: string, key: keyof typeof f, textarea = false) => (
    <div className="field">
      <label>{label}</label>
      {textarea
        ? <textarea value={f[key]} onChange={(e) => setF(Object.assign({}, f, { [key]: e.target.value }))} />
        : <input value={f[key]} onChange={(e) => setF(Object.assign({}, f, { [key]: e.target.value }))} />}
    </div>
  );
  return (
    <Modal
      title="编辑简介" width={560} confirmText="保存"
      onClose={onClose}
      onConfirm={async () => {
        onClose();
        try {
          await Api.updateOutline(pid, Object.assign({}, o, f));
          const proj = await Api.getProgress(pid);
          Store.upsertProject(proj);
          Store.toast('已保存', 'ok');
        } catch (e) { Store.toast('保存失败：' + (e as Error).message, 'error'); }
      }}
      body={
        <>
          {field('故事类型', 'story_type')}
          {field('目标受众', 'target_audience')}
          {field('一句话故事', 'one_line_story', true)}
          {field('剧情概要', 'story_summary', true)}
          {field('世界观设定', 'world_setting', true)}
        </>
      }
    />
  );
}

/** 导出面板：对齐线上「导出」抽屉（可选格式 → **真的产出文件**）。 */
function ExportModal({ eid, p, onClose }: { eid: string; p: Project; onClose: () => void }) {
  const ep = p.episodes.find((x) => x.id === eid);
  if (!ep) return null;
  const st = episodeStats(ep);
  const segs: Segment[] = (ep.storyboard?.segments) || [];
  const base = p.name + '-第' + ep.no + '集';
  const doDl = (text: string, name: string) => { downloadText(text, name); Store.toast('已导出 ' + name, 'ok'); onClose(); };

  const items: { label: string; danger?: boolean; act: () => void }[] = [
    { label: '导出工程 JSON', act: () => doDl(JSON.stringify(ep, null, 2), base + '.json') },
    {
      label: '导出剪辑 EDL',
      act: () => doDl(segs.map((s, i) =>
        (i + 1) + '  AX  V  C  00:00:00:00 00:00:00:00 00:00:00:00 00:00:00:00\n'
        + '* FROM CLIP NAME: ' + s.title).join('\n'), base + '.edl'),
    },
    { label: '导出剧本文本', act: () => doDl(str(ep.script), base + '.txt') },
    {
      label: '导出成片时间线',
      act: () => doDl(JSON.stringify(segs.map((s) => ({
        order: s.order, title: s.title, duration_ms: s.duration_ms,
        keyframe: s.keyframe, video: s.video,
      })), null, 2), base + '-timeline.json'),
    },
  ];

  return (
    <Modal
      title={'导出 · ' + ep.title} width={440} cancelText="关闭" confirmText="关闭"
      onClose={onClose} onConfirm={onClose}
      body={
        <>
          <div className="muted small">
            共 {st.segments} 镜 · {st.shots} 个镜次 · 时长 {fmtDuration(st.durationMs)} · 关键帧 {st.keyframes}/{st.segments} · 视频 {st.videos}/{st.segments}
          </div>
          <div className="col gap8 mt16" style={{ alignItems: 'stretch' }}>
            {items.map((it) => (
              <button key={it.label} type="button" className="btn btn--sm" onClick={it.act}>{it.label}</button>
            ))}
          </div>
          <div className="muted small mt16">
            离线版不执行视频编码；时间线可直接交给 ffmpeg / 剪辑软件合成。
          </div>
        </>
      }
    />
  );
}
