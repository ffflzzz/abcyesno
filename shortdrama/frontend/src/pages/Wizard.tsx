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
import type { AssetItem, Episode, Project, RunRecord, Segment, StylePack } from '../types';
import { Icon } from '../components/Icons';
import { ConfirmModal, Menu, Modal, PromptModal } from '../components/Overlay';
import { AssetDrawer } from '../components/AssetDrawer';
import { WorkbenchShell } from '../components/Shell';
import { ChainMode, MANUAL_STEPS_LABEL } from '../lib/chainMode';
import { roleLabel } from '../components/HitlBar';
import { GenOverlay } from '../lib/genOverlay';
import type { StepInfo } from '../components/Shell';
import { esc, render as renderScriptDoc } from '../lib/scriptdoc';
import { packCaps } from '../lib/packcaps';

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

/**
 * 数组字段 → 逐条字符串（去空项）。
 * 后端给 `taboos` / `key_props` 都是 `list`，但形状不对时（旧项目 / 手工改过的 brief）
 * 也不能抛异常把整页打崩 —— 当成空数组，界面按"没有"渲染（**不假报**）。
 */
const arrOf = (v: unknown): string[] =>
  (Array.isArray(v) ? v : []).map((x) => String(x == null ? '' : x).trim()).filter(Boolean);

/**
 * 去掉文案里的 `**强调**` 星号，**文字一字不改**。
 * 为什么需要：`v5/webmap.py` 的 `_registry_warnings()`、brief 的 `禁忌` 条目都写成
 * 给人读的纯文本（`像**源照片伪资产**`），而这条通道上**没有 markdown 渲染器**
 * ⇒ 原样显示会露出一串星号，读起来像坏了。
 */
const stripMd = (s: string) => s.replace(/\*\*/g, '');

/**
 * `progress.v5` 的**唯一**取值口。
 * ⚠️ 这个块只有 `GET /projects/{pid}/progress` 有（`_project_row` 那份列表行**没有**）
 * ⇒ 用 `has` 区分"后端说没有警告"（空数组）与"这份数据压根没带 v5 块"（未知），
 *    后者不能显示成"一切正常"（本项目最忌静默失配）。
 */
const v5Block = (p: Project): { has: boolean; warnings?: string[] } => {
  const v = p.v5 as { warnings?: string[] } | undefined;
  return v && typeof v === 'object' ? Object.assign({ has: true }, v) : { has: false };
};

/**
 * 类型包能力的中文标签与"没声明时说什么"收在 `lib/packcaps.ts` **一处** ——
 * 原先这里有一份 `AUDIO_MODE_ZH` + 三条判空文案，与新建项目页那份是同话两写，
 * 以后改一条必然漏另一条（本项目最忌"同一件事两个口径"）。
 */

/**
 * `audio_modes` / `still_refs` / `has_style_block` 已写进 `types.ts` 的 `StylePack`
 * （后端 `webmap.styles()` 一直在给）—— 原先这里做一次局部转型 `StyleCaps`，
 * 是因为并行任务正在改 `types.ts`；现在不必了，两个页面共用同一份声明。
 */

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

/**
 * 类型包（风格）的**能力声明**。
 *
 * ★ 为什么必须摆在这里：`brief.pack` 决定审美、音频模式与参考图开关，而旧界面只有
 *   一个 `视频风格：{name}` ⇒ 用户**选完包、跑完链**才知道它是无声还是有台词。
 *   `Api.getStyles()` 本来就把 `audio_modes` / `still_refs` / `visual_style` / 样张出处
 *   一起给了（`v5/webmap.py` 的 `styles()`），只是前端一处都没消费 —— 白给。
 *
 * ⚠️ **这一块是只读的，本页不能换包**：`pack` 不在 `update_outline` 的白名单里
 *   （`v5/webwrite.py` 的 `update_outline()`，白名单外的键后端直接 400），做一个下拉就是"点了必然失败"。
 *   要换风格请回短剧列表新建项目。
 */
function StyleInfo({ p, styles }: { p: Project; styles: StylePack[] }) {
  const code = str(p.style?.code);
  const hit = styles.find((x) => str(x.code) === code) || null;

  if (!styles.length) {
    return (
      <div className="hint mt8">
        类型包能力未加载（风格库没拉到）—— 音频模式 / 参考图 / 样张都未知，不猜。
      </div>
    );
  }
  if (!hit) {
    return (
      <div className="hint mt8">
        当前包 {code || '（未设置）'} 不在后端返回的风格库里 ⇒ 能力声明取不到。
        注意：brief.pack 写错时后端不报错，会静默用默认包 shortdrama ——
        如果你预期的是别的包，成片风格会和你想要的无关，这条得人去核。
      </div>
    );
  }

  const s = hit;
  /** 中文标签与"没声明时说什么"来自 `lib/packcaps.ts`（**判据只留一份**，见文件头）。 */
  const caps = packCaps(s);
  const srcName = str(s.sample_topic) || str(s.sample_project);
  const sampleLine = s.cover_url
    ? '样张来自' + (srcName ? '《' + srcName + '》' : '该包的一个真实项目（后端没给项目名）')
      + (s.sample_shot ? ' 的 ' + s.sample_shot : '')
      + (s.sample_stills ? '（该包已出过 ' + s.sample_stills + ' 张静帧，这里取的第一张）' : '')
    : '暂无样张：该包还没有项目出过静帧（后端没样张就给空，不编）';

  return (
    <div className="hint mt8">
      <div>
        {caps.audio}<span className="muted"> · </span>{caps.refs}
      </div>
      {caps.block ? <div style={{ color: 'var(--warn)' }}>{caps.block}</div> : null}
      {s.visual_style ? <div className="mt8">{s.visual_style}</div> : null}
      <div className="mt8">{sampleLine}</div>
      <div className="mt8">
        类型包只读：本页不能换包 —— pack 不在后端可编辑白名单里，要换风格请回短剧列表新建项目。
      </div>
    </div>
  );
}

export function Wizard({ pid, step }: { pid: string; step?: string }) {
  const { styles } = useStore();   // 第 1 步的「类型包能力」要从这里取（见 StyleInfo）
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
        <StyleInfo p={project} styles={styles} />

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
  if (str(o.target_duration)) rows.push(<Field key="td" k="目标时长" v={String(o.target_duration)} />);

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

  /**
   * ★ 下面这几项是后端**专门从 brief 里产出、而全仓此前一处都没消费**的字段
   *   （`v5/webmap.py` 的 `_outline()` 尾部）：`tone` / `ending` / `taboos` / `key_props`。
   *   不显示 = 人在这一步看不到自己写的禁忌与道具，也就无从发现 brief 写漏了。
   */
  if (str(o.tone)) rows.push(<Field key="tone" k="基调" v={String(o.tone)} />);
  if (str(o.ending)) rows.push(<Field key="end" k="结局" v={String(o.ending)} />);
  rows.push(
    <ListField key="tab" k="禁忌" items={arrOf(o.taboos)}
      note="静帧 QC 的 P0 判据就来自这里 —— 少一条就少一道保护" />,
  );
  rows.push(
    <ListField key="kp" k="关键道具" items={arrOf(o.key_props)} raw
      note="道具名必须逐字一致：近义词漂移 = 参考图绑不上 = 穿帮，所以原样显示、不截断" />,
  );

  /**
   * ⚠️ 下面两项**只读**（本项目纪律：后端这条写通道到不了的字段不许做成能输入的框）。
   * 它们都不在 `brief.json` 里 —— 概要接口（`Api.updateOutline`）改不到它们，
   * 所以这里**明说去哪儿改**，省得人在「编辑简介」里改完以为存进去了。
   */
  if (str(o.world_setting)) {
    rows.push(
      <div className="info-field" key="ws">
        <span className="k">世界观设定<span className="muted small"> 只读</span></span>
        <span className="v small">{String(o.world_setting)}</span>
        <div className="hint">
          取自 worldbuilder/worldbuilder.md（全剧级，只在第 1 集生成）—— 要改那个产物文件，改这里存不进去
        </div>
      </div>,
    );
  }

  const bios = ((o.character_biographies as { name?: string; bio?: string }[]) || [])
    .filter((b) => b && str(b.name));
  if (bios.length) {
    rows.push(
      <div className="info-field" key="bio">
        <span className="k">角色设定<span className="muted small"> 只读 · 来自资产注册表</span></span>
        <div className="hint">
          每一行就是那张角色卡的 identity（会进每一镜提示词）—— 要改请去第 2 步的资产库改那张卡，
          或改 assets.json；在这里改存不进去，而**跨集改名会让分镜与参考图错位**
        </div>
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

/**
 * 数组字段 → **分条**展示（`禁忌` / `关键道具` 都是 list，糊成一段就读不下去，
 * 与「剧情概要」必须分条是同一条理由）。
 * 空数组 ⇒ 整块不渲染（后端约定：没有就不假报，前端也不许造一条"（无）"出来吓人）。
 * `raw=true` 时**一个字都不动** —— 道具名是跨镜一致性的锚点，
 * 连去星号这种"美化"都不做（万一人家名字里真带星号）。
 */
function ListField({ k, items, note, raw }: {
  k: string; items: string[]; note?: string; raw?: boolean;
}) {
  if (!items.length) return null;
  return (
    <div className="info-field">
      <span className="k">{k}<span className="muted small"> 共 {items.length} 条</span></span>
      {note ? <div className="hint">{note}</div> : null}
      <ul className="summary-list">
        {items.map((x, i) => <li key={i}>{raw ? x : stripMd(x)}</li>)}
      </ul>
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
      <RegistryWarnings p={p} />
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

/**
 * 资产注册表的**数据质量警告**（`progress.v5.warnings`）。
 *
 * ★ 为什么必须显示、而且显示在**第 2 步顶部**：
 *   `v5/webmap.py` 的 `_registry_warnings()` 注释里明说了它的纪律是
 *   「只报告，绝不过滤」——「那会把 auto_sync 的规则复制一份，而且会掩盖真实的
 *   数据问题：前端看不到、人也就不会去清」，而 `progress` 是这些警告**唯一的可见落点**。
 *   前端一处都不消费 = 人永远不知道自己项目有**串脸风险**（角色缺 identity 时
 *   同一个人会在多个镜头里长成多个人，那是成片级事故，不是洁癖）。
 *   摆在资产库这一页顶部：这里就是那些条目的现场 —— 第 1 步看不到，第 3 步配额已烧完。
 *
 * ⚠️ 用 `.gate-bar`（`--warn` 配色），**不用** `gate-bar--block`（`--danger`）：
 *   后端什么都没拦，把警告显示成错误会让人以为项目坏了、进而忽略真错误。
 */
function RegistryWarnings({ p }: { p: Project }) {
  const blk = v5Block(p);
  const list = arrOf(blk.warnings).map(stripMd);     // arrOf：形状不对也只当"没有"，不抛
  if (list.length) {
    return (
      <div className="gate-bar">
        <div className="gate-bar-head">
          资产数据质量警告 共 {list.length} 条
          <span className="muted small"> 只提示，不拦你，也不会自动改动任何数据</span>
        </div>
        <ul className="gate-bar-list">
          {list.map((w, i) => <li key={i}>{w}</li>)}
        </ul>
      </div>
    );
  }
  /**
   * ⚠️ 「没拿到 v5 块」与「后端说没有警告」是**两件事**：
   *   列表接口（`_project_row`）压根不带 `v5`，那种情况下断言"没有警告"就是**假报成功**。
   *   所以如实说未知。（直接进本页走的是 `GET /progress`，正常都有。）
   */
  if (!blk.has) {
    return (
      <div className="hint">
        数据质量警告未知：这份项目数据没带 v5 块（只有 progress 接口给）—— 重新进入本页可取。
      </div>
    );
  }
  return null;                 // 后端给了空数组 = 确实没有警告（不假报，也不占位）
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

/**
 * 一集的「有没有分镜 / 多少镜 / 多长 / 出没出片」判据 —— **只写这一份**
 * （第 3 步的卡片和导出面板都读它，两处各写一遍必然漂移）。
 *
 * ⚠️ 为什么**不能**再只读 `ep.storyboard.segments`（2026-10-02 修）：
 *   `GET /progress` 的 `_episode_row`（`v5/webmap.py` 里那段「轻量列表不回传 segments」）
 *   给的是**轻量壳** ——
 *   注释里明写「轻量列表不回传 segments（前端只为判断"有没有分镜"）」，恒 `[]`；
 *   完整分镜走 `/episodes/{eid}/storyboard/detail`。
 *   旧实现拿 `segs.length > 0` 判"有没有分镜" ⇒ **每一个已经有分镜的项目都被显示成
 *   "还没有分镜"**、海报空、统计全 0、导出的 EDL 与时间线恒空
 *   （只有进过分镜页、`Store.upsertStoryboard` 把真 segments 写回来之后才碰巧对）。
 *
 * 现在按后端的**便利字段**判（三个都同源于 `len(shots(root, ep))`，与 segments 不打架）：
 *   · `shots`      —— 本集分镜表的镜数 ⇒ "有没有分镜"、多少镜
 *   · `duration_s` —— 各镜秒数之和 = **预计**时长（后端刻意不叫 final_seconds：
 *                    真时长要 ffprobe，静态端点不起子进程）
 *   · `has_final`  —— `media/ep{N}/episode_final.mp4` 在不在盘（**按集**的磁盘事实）
 *
 * ⛔ 出片与否**别改用 `v5.render.final`**：`GET /progress` 不带 `?ep=N` 时**恒为第 1 集**，
 *   拿它去标第 2、3 集会**集体标错**。
 */
function episodeCounts(ep: Episode) {
  const segs: Segment[] = (ep.storyboard?.segments) || [];
  const st = episodeStats(ep);
  // 后端给了 `shots` 就以它为准；字段缺失（旧形状 / 离线档）才回落 segments 计数
  const shotsN = typeof ep.shots === 'number' ? ep.shots : (st.shots || segs.length);
  return {
    segs,
    st,
    shotsN,
    hasSb: shotsN > 0,
    // 水合过的集用 segments 的秒数合计（更细），没水合的用后端预计值 —— 两者同源
    durMs: st.durationMs || Math.round((ep.duration_s || 0) * 1000),
    poster: (segs.find((s) => s.keyframe) || {}).keyframe || '',
  };
}

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
            const { shotsN, hasSb, durMs, poster } = episodeCounts(ep);
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
                  {poster
                    ? <img src={poster} alt="" loading="lazy" />
                    : (
                      /* 海报要逐镜数据（第一镜静帧），而列表接口按设计不下发它 ——
                         这里**如实说明**，不是"这一集没静帧"。打开分镜页就有了。 */
                      <span className="small muted"
                        style={{ display: 'flex', alignItems: 'center', justifyContent: 'center',
                          height: '100%', padding: '0 8px', textAlign: 'center', lineHeight: 1.45 }}>
                        海报需逐镜数据<br />打开分镜页即加载
                      </span>
                    )}
                  <span className="dur" title="各镜秒数之和（分镜表上的预计时长，不是成片实测时长）">
                    {durMs ? fmtDuration(durMs) : '--:--'}
                  </span>
                  <span className="play">{Icon.play(28)}</span>
                </div>
                <div className="ep-body">
                  <div className="name">{ep.title}</div>
                  <div className="stats">
                    {assets.characters.length}个角色<span className="muted">·</span>
                    {assets.scenes.length}个场景<span className="muted">·</span>
                    {shotsN}个镜头
                    <span className={'badge ' + (ep.has_final ? 'badge--done' : 'badge--warn')}
                      title={ep.has_final
                        ? 'media/ep' + ep.no + '/episode_final.mp4 已在盘上'
                        : '成片还没拼出来（或未出片）'}>
                      {ep.has_final ? '已出片' : '未出片'}
                    </span>
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

/**
 * 界面字段 ↔ `brief.json` 键的**唯一映射表**（可编辑档）。
 *
 * ⚠️ 为什么必须一张表、且**只提交表里的键**（2026-10-02 修，此前是真缺陷）：
 *   `v5/webwrite.py` 的 `update_outline()` 对 patch 里**每一个键**去查白名单
 *   （`genre` / `target_duration` / `tone` / `结局` / `story_summary` / `ratio`），
 *   命中不了就 `raise EditError` —— shim 转 **HTTP 400**，**不是"忽略多余键"**。
 *   旧实现在这里写 `Object.assign({}, o, f)`：把 `outline` 的 14 个键
 *   （`story_type` / `one_line_story` / `world_setting` / `character_biographies` /
 *   `taboos` / `outline_status` …）原样 POST 出去
 *   ⇒ **每一次点「保存」都必然失败**，而界面只回一句"保存失败"。
 *   界面键与 brief 键**不同名**（`story_type` 其实来自 `brief.genre`、
 *   `ending` 来自 `brief.结局`），所以反向映射必须显式写、只能写一处。
 */
const OUTLINE_EDITABLE: {
  label: string; ui: string; briefKey: string; area?: boolean; hint?: string;
}[] = [
  { label: '故事类型', ui: 'story_type', briefKey: 'genre' },
  {
    label: '目标时长', ui: 'target_duration', briefKey: 'target_duration',
    hint: '总时长目标 + 镜数指引；单镜秒数由分镜按剧情节拍分配（4-12s），不在这里写死',
  },
  {
    label: '剧情概要', ui: 'story_summary', briefKey: 'story_summary', area: true,
    hint: '写入 brief 的 story_summary。硬要求 must_have 不在可编辑白名单里 —— 保真门（FIDELITY）仍按 must_have 判，不会因这里改了而跟着变。',
  },
  { label: '基调', ui: 'tone', briefKey: 'tone', area: true, hint: '渲染风格 + 光线色彩叙事 + 情绪弧' },
  { label: '结局', ui: 'ending', briefKey: '结局', area: true, hint: '定格画面描述（写画面，不写主题）' },
];

/**
 * **只读**字段：值在盘上、看得见，但这条写通道到不了它。
 * ⛔ 不许做成"能输入、保存必失败"的样子（本项目最忌静默失配）——
 * 一律 disabled + 明说"改它要去改哪个产物"。
 */
const OUTLINE_READONLY: { label: string; ui: string; why: string }[] = [
  {
    label: '目标受众', ui: 'target_audience',
    why: '后端概要接口只认那 6 个键，target_audience 不在其中 —— 要改请直接编辑 projects/<项目名>/brief.json',
  },
  {
    label: '一句话故事', ui: 'one_line_story',
    why: '它就是 brief.topic，也就是项目名 —— 要改请在短剧列表里「重命名项目」',
  },
  {
    label: '世界观设定', ui: 'world_setting',
    why: '取自 worldbuilder/worldbuilder.md（全剧级产物，只在第 1 集生成）—— 改那个文件，改这里保存不进去',
  },
];

function OutlineModal({ p, pid, onClose }: { p: Project; pid: string; onClose: () => void }) {
  const o = (p.outline || {}) as Record<string, unknown>;
  const seed = (ui: string) => str(o[ui]);
  const [draft, setDraft] = useState<Record<string, string>>(() => {
    const d: Record<string, string> = {};
    OUTLINE_EDITABLE.forEach((x) => { d[x.ui] = seed(x.ui); });
    return d;
  });
  const [saving, setSaving] = useState(false);

  /** 只提交**真改过**的键：没改也照发会把同值写一遍、无谓地动盘（且 brief 会多出空键）。 */
  const changed = OUTLINE_EDITABLE.filter((x) => draft[x.ui] !== seed(x.ui));

  const save = async () => {
    if (!changed.length) {
      onClose();
      Store.toast('没有改动要保存（界面与 brief 现值一致）');
      return;
    }
    const patch: Record<string, string> = {};
    // 提交前 trim：与 `seed()` 的口径一致（那边也 trim 过），否则"只输了个空格"
    // 会被当成改动写进 brief —— 后端 `str(v or "")` 是照收的。
    changed.forEach((x) => { patch[x.briefKey] = draft[x.ui].trim(); });
    setSaving(true);
    try {
      await Api.updateOutline(pid, patch);
      Store.upsertProject(await Api.getProgress(pid));
      onClose();
      // 报**具体落到 brief 的哪个键** —— 只说"已保存"就又是一次假报成功
      Store.toast('已写入 brief.json：' + changed.map((x) => x.briefKey).join('、'), 'ok');
    } catch (e) {
      // 失败**不关弹窗**：用户写的东西还在，直接重试（旧实现先 onClose() 再发请求 = 草稿没了）
      Store.toast('保存失败：' + (e as Error).message, 'error');
    } finally {
      setSaving(false);
    }
  };

  const set = (ui: string, v: string) => setDraft(Object.assign({}, draft, { [ui]: v }));

  return (
    <Modal
      title="编辑简介" width={560} confirmText="保存" cancelText="关闭"
      confirmDisabled={saving}
      onClose={onClose} onConfirm={() => { void save(); }}
      hint="保存只改 brief.json 白名单里的键（genre / target_duration / tone / 结局 / story_summary / ratio）；其余字段只读 —— 它们不在 brief 里，要去对应的产物文件改。"
      body={
        <div className="col gap12">
          {OUTLINE_EDITABLE.map((x) => (
            <div className="ad-field" key={x.ui}>
              <label>
                {x.label} <span className="muted small">写入 brief 的 {x.briefKey}</span>
              </label>
              {x.area
                ? <textarea className="ad-area" rows={3} value={draft[x.ui]}
                  onChange={(e) => set(x.ui, e.target.value)} />
                : <input className="ad-input" value={draft[x.ui]}
                  onChange={(e) => set(x.ui, e.target.value)} />}
              {x.hint ? <span className="hint">{x.hint}</span> : null}
            </div>
          ))}

          {/* 只读档（含 `禁忌` / `关键道具`：它们有 brief 落点，但不在这条写通道的白名单里） */}
          <div className="hint">
            下面几项看得到、改不了 —— 后端概要接口不认识它们，硬发过去整个请求会被拒（400）。
          </div>
          {OUTLINE_READONLY.filter((x) => seed(x.ui)).map((x) => (
            <div className="ad-field ad-field--na" key={x.ui} title={x.why}>
              <label>{x.label} <span className="muted small">只读</span></label>
              <textarea className="ad-area" rows={2} value={seed(x.ui)} readOnly disabled />
              <span className="hint">{x.why}</span>
            </div>
          ))}
          <ListField k="禁忌（只读）" items={arrOf(o.taboos)}
            note="要改请直接编辑 brief.json 的「禁忌」（它同时是静帧 QC 的 P0 判据来源）" />
          <ListField k="关键道具（只读）" items={arrOf(o.key_props)} raw
            note="要改请直接编辑 brief.json 的 key_props；名字必须与分镜、资产卡逐字一致" />
        </div>
      }
    />
  );
}

/**
 * 导出面板：对齐线上「导出」抽屉（可选格式 → **真的产出文件**）。
 *
 * ⚠️ 逐镜数据**按需拉一次**（2026-10-02）：`GET /progress` 里 `storyboard.segments`
 * 恒为 `[]`（列表接口只给轻量壳，见 `episodeCounts` 的注释）。旧实现直接拿它拼
 * EDL 与时间线 ⇒ **导出的文件恒为空**，却照样弹一句「已导出 xxx.edl」——
 * 那是本项目最忌的**假报成功**。现在：没数据先拉 → 拉到才生成文件；
 * 拉到 0 镜 / 请求失败就**如实报错、不下载、不关窗**。
 * 拉回来的同时写回 Store ⇒ 第 3 步这张卡的海报与统计跟着就有了，同一 eid 不重复请求。
 */
function ExportModal({ eid, p, onClose }: { eid: string; p: Project; onClose: () => void }) {
  const ep = p.episodes.find((x) => x.id === eid) || null;
  // ★ hooks 必须排在 `if (!ep) return null` **之前**：早退改变 hook 调用次数会让 React 直接抛错
  const [segs, setSegs] = useState<Segment[]>(
    () => (ep && ep.storyboard && ep.storyboard.segments) || [],
  );
  const [pulling, setPulling] = useState(false);
  if (!ep) return null;

  const { shotsN, durMs, st } = episodeCounts(ep);
  const base = p.name + '-第' + ep.no + '集';
  const kf = segs.filter((s) => s.keyframe).length;
  const vd = segs.filter((s) => s.video).length;

  const doDl = (text: string, name: string) => {
    downloadText(text, name);
    Store.toast('已导出 ' + name, 'ok');
    onClose();
  };

  /** 拿逐镜数据（内存里已有就用，否则**一次**请求）。拿不到就返回 null 并且已经 toast 说明。 */
  const needSegs = async (): Promise<Segment[] | null> => {
    if (segs.length) return segs;
    if (pulling) return null;                      // 正在拉，别发第二次
    setPulling(true);
    try {
      const sb = await Api.getStoryboard(eid);
      const list = ((sb as { segments?: Segment[] } | null)?.segments) || [];
      if (!list.length) {
        Store.toast('后端这一集没解析出任何镜（分镜表可能是空的）—— 没有生成文件', 'error');
        return null;
      }
      setSegs(list);
      Store.upsertStoryboard(p.id, eid, sb);       // 顺手把海报/统计喂回第 3 步
      return list;
    } catch (e) {
      Store.toast('拉分镜失败：' + (e as Error).message + ' —— 没有生成文件', 'error');
      return null;
    } finally {
      setPulling(false);
    }
  };

  /** 包一层：需要逐镜的导出项先 await `needSegs()`，拿不到就**什么都不做**。 */
  const withSegs = (fn: (list: Segment[]) => void) => async () => {
    const list = await needSegs();
    if (list) fn(list);
  };

  const items: { label: string; act: () => void }[] = [
    {
      label: '导出工程 JSON',
      act: withSegs((list) => doDl(JSON.stringify(
        Object.assign({}, ep, { storyboard: Object.assign({}, ep.storyboard, { segments: list }) }),
        null, 2,
      ), base + '.json')),
    },
    {
      label: '导出剪辑 EDL',
      act: withSegs((list) => doDl(list.map((s, i) =>
        (i + 1) + '  AX  V  C  00:00:00:00 00:00:00:00 00:00:00:00 00:00:00:00\n'
        + '* FROM CLIP NAME: ' + (s.title || s.id)).join('\n'), base + '.edl')),
    },
    { label: '导出剧本文本', act: () => doDl(str(ep.script), base + '.txt') },
    {
      label: '导出成片时间线',
      act: withSegs((list) => doDl(JSON.stringify(list.map((s) => ({
        order: s.order, title: s.title, duration_ms: s.duration_ms,
        keyframe: s.keyframe, video: s.video,
      })), null, 2), base + '-timeline.json')),
    },
  ];

  return (
    <Modal
      title={'导出 · ' + ep.title} width={440} cancelText="关闭" confirmText="关闭"
      onClose={onClose} onConfirm={onClose}
      body={
        <>
          <div className="muted small">
            共 {shotsN} 镜 · 预计时长 {durMs ? fmtDuration(durMs) : '未知'}
            · {ep.has_final ? '成片已在盘' : '未出片'}
          </div>
          <div className="hint mt8">
            {segs.length
              ? '逐镜：关键帧 ' + kf + '/' + segs.length + ' · 视频 ' + vd + '/' + segs.length
                + ' · 镜次 ' + st.shots
              : '逐镜数据没随项目列表下发（后端把列表接口做成轻量壳）—— 点下面需要逐镜的导出项时会自动拉一次；拉不到就不生成文件。'}
          </div>
          <div className="col gap8 mt16" style={{ alignItems: 'stretch' }}>
            {items.map((it) => (
              <button key={it.label} type="button" className="btn btn--sm"
                disabled={pulling} onClick={it.act}>{it.label}</button>
            ))}
          </div>
          {pulling ? <div className="hint mt8">正在拉本集分镜…</div> : null}
          <div className="muted small mt16">
            离线版不执行视频编码；时间线可直接交给 ffmpeg / 剪辑软件合成。
          </div>
        </>
      }
    />
  );
}
