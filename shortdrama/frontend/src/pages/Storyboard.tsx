/* ==========================================================================
   src/pages/Storyboard.tsx —— 分镜编辑器（/playlet/review/:pid/episode/:eid）
   布局：左侧镜头缩略轨 + 主区逐镜面板（分镜文本 + 关键帧/视频生成卡）
   --------------------------------------------------------------------------
   从 `web/assets/js/views/storyboard.js`（711 行）移植。
   ★ 凡是带「★ / ⚠️ / ⛔」的判据都保留了 —— 那些都是实测事故换来的。

   移植手法上的一处**刻意的改进**（不是遗漏）：
     旧版在 `onTick` 里用 `document.querySelector('.sb-gen-empty')` 找节点再
     `innerHTML` 写进度，找不到就**静默跳过**（旧注释自己写了"否则会打断整个
     批量生成链（实测踩坑）"）。React 里进度就是 state，由卡片自己渲染 ——
     "找不到节点"这个失败模式**不存在**。

   2026-10-02 契约对账（本轮改动的前提，全部按 `v5/webwrite.py` / `v5/webmap.py` 现码核对）：
     · 编辑一镜的**可写列只有** `EDITABLE_COLS` 那十个键（visual/dialogue/seconds/
       shot_type/angle/camera/scene/visual_style/tail/sfx）。旧表单提交的
       `title / duration_ms / summary / video_prompt` **四个键后端一个都不认**
       ⇒ 点保存每次都是 400「没有可应用的字段」，界面上一个字都改不了。
     · `job_state` / `job_error` 后端一直在给（`video_jobs.json`），前端此前**完全不显示**
       ⇒ 哪镜上次渲染失败只能靠再点一次生成去撞。
     · `unresolved_tokens` 同理：`@点名` 落在注册表外 ⇒ 参考图静默不绑 = 静默穿帮。
   ========================================================================== */

import { useMemo, useState } from 'react';
import { Api } from '../api';
import { Router } from '../router';
import { Store, useStore } from '../store';
import type { Episode, Project, RunRecord, Segment } from '../types';
import { Icon } from '../components/Icons';
import { ConfirmModal, Menu, Modal } from '../components/Overlay';
import { WorkbenchShell } from '../components/Shell';
import type { StepInfo } from '../components/Shell';
import { Vendor, VendorSelect } from '../components/VendorSelect';
import { qcPayload, qcToggle, QC_LABELS, type QcKey } from '../lib/quality';
import { richHtml, type RichToken } from '../lib/rich';

const SB_STEPS: StepInfo[] = [
  { key: 'script', no: 1, label: '剧本大纲', state: 'done' },
  { key: 'assets', no: 2, label: '资产库', state: 'done' },
  { key: 'episodes', no: 3, label: '分集视频', state: 'active' },
];

type Which = 'keyframe' | 'video';
type Bar = 'img' | 'vid';

/**
 * ⚠️ **历史文案**，不是正在执行的约束（2026-10-02 对账）：
 * 旧版每镜提示词末尾会统一追加「视频全程不要字幕、不要屏幕文字；必须保留…BGM…」。
 * 现在后端**不再注入**这句话：
 *   · 有无台词由 `brief.json` 的 `audio_mode` 决定（`validate.audio_mode_of`）
 *   · BGM 由类型包禁忌 + `SHORTDRAMA_VIDEO_BGM` 决定
 *   · 画内文字默认**只禁烧录型**（成行字幕条），场景固有文字（门牌/面板读数）放行
 * ⇒ 所以本正则**只用于把老项目分镜里已经写死的这句话从展示里剥掉**（免得和下面的
 *   脚注重复），**不要**理解成"前端在这里执行一条约束"。
 */
const FOOT_RE = /视频全程不要字幕[^。]*。?/;

function stripFoot(rich: RichToken[] | undefined): RichToken[] {
  return (rich || [])
    .map((t) => (t.type === 'text'
      ? { type: 'text' as const, value: String(t.value || '').replace(FOOT_RE, '') }
      : t))
    .filter((t) => !(t.type === 'text' && !t.value.trim()));
}

/* ------------------------------------------------- 后端字段的本地读形 */
/* ⚠️ 刻意**不**依赖 `types.ts` 的收窄声明：那份文件正由并行任务改，
 *    本页要读的字段比它多（tail / text_shot / unresolved_tokens …）。
 *    本地 cast 一处收口，字段缺失时回落空值，不猜。 */

interface ShotRaw {
  shot_id?: string;
  title?: string;
  duration_sec?: number;
  duration_ms?: number;
  rich?: RichToken[];
  content_rich?: string;
  content_plain?: string;
  content?: string;
}
interface SceneRaw {
  scene_id?: string;
  title?: string;
  content_plain?: string;
  shots?: ShotRaw[];
}
interface V5Info {
  shot_name?: string;
  col_index?: number | string | null;
  shot_type?: string;
  angle?: string;
  camera?: string;
  tail?: string;
  dialogue?: string;
  sfx?: string;
  text_shot?: string;
  join_note?: string;
  heading?: string;
  still_prompt?: string;
  job_state?: string | null;
  job_error?: string;
  unresolved_tokens?: string[];
}
interface SegRaw {
  summary?: string;
  scene?: string;
  visual_style?: string;
  status?: string;
  video_prompt_text?: string;
  scenes?: SceneRaw[];
  shots?: ShotRaw[];
  v5?: V5Info;
}
interface EditResult {
  applied?: Record<string, string>;
  skipped?: string[];
  invalidated?: { still?: boolean; clip?: boolean; job?: boolean; clip_error?: string };
  note?: string;
}

const raw = (seg: Segment): SegRaw => seg as unknown as SegRaw;
const v5Of = (seg: Segment): V5Info => raw(seg).v5 || {};

/** 一镜只有一个 scene / 一个 shot（`webmap.storyboard_detail` 的三层结构）。 */
function firstShot(seg: Segment): ShotRaw {
  const r = raw(seg);
  const sc = (r.scenes || [])[0];
  return (sc && sc.shots && sc.shots[0]) || (r.shots && r.shots[0]) || {};
}

/** 画面描述**纯文本**（`content_plain`）：token 已被换成「资产名 - 基础形象」的显示名。 */
function visualPlain(seg: Segment): string {
  const sh = firstShot(seg);
  return String(sh.content_plain || sh.content || raw(seg).summary || '');
}

/**
 * ★ 编辑框初值必须**还原 `@资产名`**，不能用 `content_plain`。
 *
 * 为什么（2026-10-02 读 `_to_rich` / `rich_to_plain` 查实）：
 *   分镜表「画面描述」列里存的是 `@老陈`；后端为了渲染胶囊把它升级成
 *   token `@[老陈 - 基础形象](sd-asset://character/老陈)`，而 `content_plain`
 *   = token 换回**显示名** `老陈 - 基础形象`（**没有 `@`**）。
 *   ⇒ 拿 `content_plain` 当编辑初值原样存回去，`@` 就全丢了 ——
 *     而 `assets.hits_for_shot` 的参考图绑定**正是按 `@名` 命中**的：
 *     绑定静默失效、人脸每镜自己编，日志却全绿（本项目最忌的那一类）。
 *   `tokens` 是原文的**无损切分**（未命中的文字原样保留），
 *   所以 `text.value + '@' + ref.name` 拼回去 === 分镜表原文。
 */
function visualRaw(seg: Segment): string {
  const toks = firstShot(seg).rich;
  if (toks && toks.length) {
    const s = toks.map((t) => (t.type === 'ref'
      ? '@' + String(t.name || t.id || '')
      : String(t.value || ''))).join('');
    if (s.trim()) return s;
  }
  return visualPlain(seg);
}

/** `v5/webwrite.py` 的 `PLACEHOLDER_VISUAL` 自解释占位标记。 */
const PH_MARK = '[[待补]]';

/**
 * ⚠️ `webmap.storyboard_detail` 在「场景」列取空时给的是**展示用**的 `未标注场景`
 *   （`scene_title = ... or "未标注场景"`）—— 盘上那一格其实是空的。
 *   ⇒ 编辑框初值必须还原成空，否则"打开看一眼再保存"就会把这四个字真写进分镜表，
 *     从此 `assets.hits_for_shot` 拿它去匹配场景资产（永不命中）。
 */
const SCENE_DISPLAY_EMPTY = '未标注场景';

/**
 * 占位镜判据：画面描述里还留着 `[[待补]]`。
 *
 * ⛔ 这类镜**一律不许送去生成**（静帧与视频都不许）：
 *   `storyboard.parse` 的门槛是「画面描述 ≥15 字」，而那句占位本身就有 27 字
 *   ⇒ 它会**照常进渲染清单**，模型收到「请填写不少于 15 字的具体内容后再生成」
 *   这种中间指令文字，画出来必然是烧字的废图 —— 白烧一次图片配额 + 一次视频配额。
 */
const isPlaceholder = (seg: Segment): boolean => visualRaw(seg).includes(PH_MARK);

/** 供应商硬约束：单镜 `seconds ∈ [4,12]`（下限 4 固定，见 AGENTS.md 环境变量表）。 */
const SEC_MIN = 4;
const SEC_MAX = 12;

/** 渲染台账（`video_jobs.json`）状态 → 色点 + 人话。 */
const JOB_META: Record<string, { text: string; fg: string }> = {
  pending: { text: '排队中（未提交）', fg: 'var(--text-tertiary)' },
  submitted: { text: '已提交，等后端回执', fg: 'var(--warn)' },
  completed: { text: '已出片', fg: 'var(--success)' },
  failed: { text: '上次提交失败', fg: 'var(--danger)' },
  expired: { text: '任务已过期', fg: 'var(--danger)' },
};

/* ========================================================================== */

export function Storyboard({ pid, eid }: { pid: string; eid: string }) {
  useStore();                       // 订阅 store 变更
  const p = Store.getProject(pid) as Project | null;
  const ep = p ? ((p.episodes || []).find((x) => x.id === eid) as Episode | undefined) : undefined;

  const segs: Segment[] = useMemo(() => (ep?.storyboard?.segments) || [], [ep]);
  /** 可参与批量生成的镜（占位镜被排除，见 `isPlaceholder`）。 */
  const eligible = useMemo(() => segs.filter((s) => !isPlaceholder(s)), [segs]);
  const phSegs = useMemo(() => segs.filter(isPlaceholder), [segs]);
  /**
   * ★ 渲染进度**只能自己数 segments**（2026-10-02 定）。
   * ⛔ 别改成读 `p.v5.render`：那是**按集**的汇总，而 `Api.getProgress` 的集号参数
   *   正由并行任务改；且它是后端推导出来的步骤，不是"本集这一镜有没有图"。
   *   `keyframe` / `video` 两个字段来自 `stills.json` + `clips/*.mp4` 的**磁盘事实**，
   *   数出来的就是本集真实值。
   * ⚠️ pack 档的已知偏差：产物是**组级** `clips/packNN.mp4`，逐镜 `video` 会空 ⇒
   *   这里的"视频 N/总"偏保守（显示 0 不代表没素材，代表没有**逐镜**文件）。
   */
  const doneKF = useMemo(() => segs.filter((s) => s.keyframe).length, [segs]);
  const doneVid = useMemo(() => segs.filter((s) => s.video).length, [segs]);
  const nFailed = useMemo(() => segs.filter((s) => {
    const st = String(v5Of(s).job_state || '');
    return st === 'failed' || st === 'expired';
  }).length, [segs]);

  const [active, setActive] = useState<string | null>(null);
  const [busy, setBusy] = useState<{ sids?: Record<string, boolean>; sid?: string | null; which: string } | null>(null);
  const [runNote, setRunNote] = useState('');
  // 批量选择：图片栏与视频栏各自独立（线上实测：两个工具栏各有自己的「全选」）
  const [sel, setSel] = useState<{ img: Record<string, boolean>; vid: Record<string, boolean> }>({ img: {}, vid: {} });
  const [editSeg, setEditSeg] = useState<Segment | null>(null);
  const [delSeg, setDelSeg] = useState<Segment | null>(null);
  const [history, setHistory] = useState<{ rect: DOMRect; el: HTMLElement; seg: Segment; which: Which } | null>(null);
  const [previewSrc, setPreviewSrc] = useState<string | null>(null);
  const [finalUrl, setFinalUrl] = useState<{ url: string; warnings: string[] } | null>(null);

  const curActive = active && segs.some((s) => s.id === active) ? active : (segs[0]?.id || null);

  if (!p || !ep) {
    return (
      <WorkbenchShell title="剧集不存在" steps={[]}>
        <div className="content">
          <div className="empty">
            {Icon.empty(48)}
            <div>剧集不存在</div>
            <button type="button" className="btn btn--sm mt16" onClick={() => Router.go('/playlet/list')}>
              返回列表
            </button>
          </div>
        </div>
      </WorkbenchShell>
    );
  }

  /* ------------------------------------------------------------ 生成动作 */

  /**
   * ★ http 驱动：**真打后端**生成。
   *
   * 一次请求渲多镜（后端按 `segment_ids` 起一个 run）→ 轮询台账 → 重拉分镜 → 重画。
   *
   * 为什么必须这么做（旧版实测）：原本 `generate()`/`batch()` **完全走本地**
   * （画占位图 + 本地写状态），`Api.generateMedia` 是**死代码** ——
   * 点「生成」只会在浏览器里画一张占位图，后端一无所知。
   *
   * ⚠️ 结果**如实播报**：`blocked`（被媒体门拦）与 `incomplete`（缺镜）都不是"成功"，
   * 也不能算"失败"——它们是有信息量的正常结果，必须把原因显示出来。
   */
  const generateRemote = async (ids: string[], which: Which) => {
    // ★ 占位镜在**唯一出口**这里再拦一次（按钮禁用是提示，这里是保证）：
    //   绕过这条 = 白烧配额，且产物是带中间指令文字的废图。
    const blocked = ids.filter((i) => {
      const s = segs.find((x) => x.id === i);
      return !!s && isPlaceholder(s);
    });
    const goIds = ids.filter((i) => !blocked.includes(i));
    if (blocked.length) {
      Store.toast('已挡下 ' + blocked.length + ' 镜：画面描述仍是 ' + PH_MARK
        + ' 占位，生成只会白烧配额 —— 先点镜头右上角的笔补内容', 'error');
    }
    if (!goIds.length) return;

    const sids: Record<string, boolean> = {};
    goIds.forEach((i) => { sids[i] = true; });
    setBusy({ sid: goIds[0], sids, which });
    setRunNote('已提交…');

    const label = which === 'keyframe' ? '关键帧' : '视频';
    // ★ 厂商随请求发出：`which` 是 'keyframe'/'video'，而工具栏栏位是 'img'/'vid'
    //   —— 这里做映射，**单镜与批量共用同一份选择**。
    const cap = which === 'video' ? 'video' : 'image';
    const body = Object.assign({ segment_ids: goIds }, Vendor.payload(cap));
    const vCode = Vendor.pick(cap);
    Store.toast('已提交 ' + goIds.length + ' 个' + label + '到后端'
      + (vCode ? '（厂商：' + vCode + '）' : '') + '…');

    const t0 = Date.now();
    try {
      const st = await Api.runTask(
        () => Api.generateMedia(pid, eid, which, body) as Promise<RunRecord>,
        {
          onTick: (s) => {
            const secs = Math.round((Date.now() - t0) / 1000);
            setRunNote('正在生成… 已 ' + secs + 's（' + ((s && s.status) || 'running') + '）');
          },
          // 刷新分镜：`segments` 在 storyboard/detail 里
          refresh: async () => {
            const sb = await Api.getStoryboard(eid);
            Store.upsertStoryboard(pid, eid, sb);
            return true;
          },
        },
      );
      Store.toast(runOutcomeText(st, label), st.status === 'ok' ? 'ok' : 'error');
    } catch (e) {
      Store.toast('生成请求失败：' + (e as Error).message, 'error');
    } finally {
      setBusy(null);
      setRunNote('');
    }
  };

  const generate = (sid: string, which: Which) => { void generateRemote([sid], which); };

  const batch = (which: Which, onlyIds: string[]) => {
    const targets = segs.filter((s) => {
      if (isPlaceholder(s)) return false;                 // ★ 占位镜不参与批量（见 `isPlaceholder`）
      if (onlyIds.length && !onlyIds.includes(s.id)) return false;
      return which === 'keyframe' ? !s.keyframe : !s.video;
    });
    if (!targets.length) {
      // ★ 别让这里变成"死胡同"（旧版实测：镜头都生成完之后再点什么都不发生，
      //   用户会以为按钮坏了）。如实说明"没有要生成的"，并**指出下一步在哪**。
      const phNote = phSegs.length
        ? '（其中 ' + phSegs.length + ' 镜是 ' + PH_MARK + ' 占位，不参与生成）' : '';
      Store.toast(which === 'video'
        ? '所选镜头都已有视频' + phNote + ' → 下一步：右上角「生成最终视频」出片'
        : '所选镜头都已有图片' + phNote);
      return;
    }
    void generateRemote(targets.map((s) => s.id), which);
  };

  /**
   * 「生成最终视频」= **整片出片**。
   * `POST /episodes/{eid}/compose` → run → 轮询 → 给出**可播的成片**。
   * 后端那条路径是 `pipeline.run`（**不带 only** = 整片）：静帧缺的补画、
   * 视频缺的补渲、已在盘上的按磁盘事实复用（**不重复烧配额**）。
   * `timeoutMs: 0` = 不限时 —— 整片是小时级，用默认 60 分钟会在片子还在渲时报"超时"。
   */
  const compose = async () => {
    const ready = segs.filter((s) => s.video);
    if (!ready.length) { Store.toast('请先生成镜头视频'); return; }
    // ★ 出片**不吃镜号**（整片），所以占位镜会被连着一起烧 —— 比单镜更贵，必须先挡。
    if (phSegs.length) {
      Store.toast('本集有 ' + phSegs.length + ' 镜（'
        + phSegs.map((s) => v5Of(s).shot_name || s.order).join('、')
        + '）画面描述仍是 ' + PH_MARK + ' 占位。出片是整片、不挑镜 ⇒ 这些镜会一起烧配额，'
        + '且画出来是带中间指令文字的废镜。先补齐或删除该镜。', 'error');
      return;
    }
    if (busy) { Store.toast('还有生成任务在跑，等它结束再出片'); return; }
    if (Api.driver !== 'http') {
      Store.toast('离线模式没有渲染后端：出片需要连接后端', 'error');
      return;
    }
    setBusy({ sid: null, which: 'compose' });
    setRunNote('已提交整片出片…');
    Store.toast('已提交整片出片（静帧→视频→拼接，可能要很久）…');
    const t0 = Date.now();
    try {
      const st = await Api.runTask(
        () => Api.composeEpisode(eid, {}) as Promise<RunRecord>,
        {
          timeoutMs: 0,
          onTick: (s) => setRunNote('正在出片… 已 ' + Math.round((Date.now() - t0) / 60000)
            + ' 分（' + ((s && s.status) || 'running') + '）'),
          refresh: async () => {
            const sb = await Api.getStoryboard(eid);
            Store.upsertStoryboard(pid, eid, sb);
            return true;
          },
        },
      );
      const res = (st.result || {}) as { final_url?: string; warnings?: string[] };
      if (st.status === 'ok') {
        // ★ 出片成功必须给人**看得见的东西**（一句 toast 不足以验收）
        if (res.final_url) setFinalUrl({ url: res.final_url, warnings: res.warnings || [] });
        else Store.toast('出片完成，但没拿到成片地址 —— 请看后端日志', 'ok');
      } else {
        Store.toast(runOutcomeText(st, '出片'), 'error');
      }
      if ((res.warnings || []).length) {
        console.warn('[分镜契约]', res.warnings);
        Store.toast('分镜契约有 ' + res.warnings!.length + ' 条警告（不拦你）——详情见分镜页顶部条');
      }
    } catch (e) {
      Store.toast('出片请求失败：' + (e as Error).message, 'error');
    } finally {
      setBusy(null);
      setRunNote('');
    }
  };

  const addSegment = async () => {
    try {
      const res = await Api.addSegment(pid, eid) as { visible?: boolean; note?: string } | null;
      const sb = await Api.getStoryboard(eid);
      Store.upsertStoryboard(pid, eid, sb);
      // ★ 如实回报：`add_segment` 会返回 `visible`（新行有没有被 `parse` 认作镜头）。
      //   旧写法不管结果直接说"已新增镜头" —— 后端「插了等于没插」时界面在说谎。
      if (res && res.visible === false) {
        Store.toast('已插入分镜行，但后端没把它认作镜头（`shots_after` 未增加）——'
          + '多半是本集分镜表缺列，请看后端日志', 'error');
      } else {
        Store.toast('已新增镜头：画面描述是 ' + PH_MARK + ' 占位 ⇒ 补齐前不能生成', 'ok');
      }
    } catch (e) { Store.toast('新增失败：' + (e as Error).message, 'error'); }
  };

  /**
   * ★ 保存 = 写**分镜表的列**，并按后端的答复如实播报。
   *
   * 为什么不能像旧版那样只弹"已保存"：`webwrite.update_segment` 一定会返回
   *   · `applied{}`  真正落盘的列
   *   · `skipped[]`  **没落盘**的键（不在 `EDITABLE_COLS`，或本集分镜表根本没这一列）
   *   · `invalidated{}` 作废了静帧 / clip / job 没有
   *   · `note`「该镜的静帧与成片已作废，需重新生成」
   * ⇒ 只说"已保存"= 假报成功（用户会以为图还能用）。
   */
  const saveSegment = async (seg: Segment, patch: Record<string, unknown>) => {
    try {
      const res = await Api.updateSegment(seg.id, patch) as EditResult | null;
      const sb = await Api.getStoryboard(eid);
      Store.upsertStoryboard(pid, eid, sb);

      const applied = Object.keys(res?.applied || {});
      const skipped = (res?.skipped || []).filter(Boolean);
      const inv = res?.invalidated || {};
      const gone = [inv.still ? '静帧' : '', inv.clip ? '成片片段' : '', inv.job ? '渲染任务' : '']
        .filter(Boolean).join(' / ');
      let msg = '已写入 ' + applied.length + ' 列：' + (applied.join('、') || '（无）');
      if (gone) msg += ' · 已作废：' + gone + '，需重新生成';
      else msg += ' · 后端未报作废（该镜本来就没有产物）';
      if (res?.note) msg += '（' + res.note + '）';
      Store.toast(msg, 'ok');

      // ⚠️ `skipped` 非空 = **有字段没落盘**，这是部分失败，必须单独说、并且留得够久
      //   （`error` 类 toast 存 6 秒）——否则用户在框里改了「落幅」却以为改成功了。
      if (skipped.length) {
        Store.toast('这些字段后端没写进分镜表：' + skipped.join('、')
          + '（不在可编辑列名单，或本集分镜表缺这一列）', 'error');
      }
      if (inv.clip_error) Store.toast('暂存旧片段时后端报错：' + inv.clip_error, 'error');
    } catch (e) { Store.toast('保存失败：' + (e as Error).message, 'error'); }
  };

  const onBatchGen = (bar: Bar) => {
    const which: Which = bar === 'vid' ? 'video' : 'keyframe';
    const bucket = bar === 'vid' ? sel.vid : sel.img;
    const ids = Object.keys(bucket).filter((k) => bucket[k]);
    if (!ids.length) { Store.toast('请选择需要生成的片段'); return; }   // 与线上 aria 一致
    batch(which, ids);
  };

  const batchDownload = () => {
    const manifest = segs.map((s) => ({
      order: s.order, title: s.title, duration_ms: s.duration_ms,
      keyframe: s.keyframe, video: s.video,
    }));
    const blob = new Blob([JSON.stringify(manifest, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = ep.title + '-素材清单.json';
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
    Store.toast('已导出素材清单（不打包二进制）');
  };

  const onPreview = () => {
    const first = segs.find((s) => s.video && /\.mp4$/i.test(s.video));
    if (!first) { Store.toast('还没有生成视频，先执行批量生成'); return; }
    setPreviewSrc(first.video || null);
  };

  /* ---------------------------------------------------------------- 渲染 */

  const nImg = Object.keys(sel.img).filter((k) => sel.img[k]).length;
  const nVid = Object.keys(sel.vid).filter((k) => sel.vid[k]).length;
  /**
   * ★ 「生成最终视频」是否可点，判据是**这一集有没有已生成的视频**，
   *   不是"选中了几镜"（旧版真浏览器实测抓到的误导性控件）：
   *   旧写法用选中数 ⇒ 19 镜全部有视频、但一镜未选时按钮是**灰的**，
   *   而点了它其实出的是**整片**（后端 `compose` 不吃镜号）—— 看起来像坏了。
   *   这属于本项目最忌的"控件与它描述的事不符"。
   * ★ 追加的 `phSegs.length` 不削弱上面那条：它只多拦一种情况 ——
   *   本集还有 '[[待补]]' 占位镜（整片出片会把它们一起烧掉，见 `compose`）。
   */
  const nReadyVid = segs.filter((s) => s.video).length;
  const composeTip = phSegs.length
    ? '本集有 ' + phSegs.length + ' 镜画面描述仍是 ' + PH_MARK + ' 占位：先补齐或删除再出片'
    : (nReadyVid ? '按已生成的镜头出整片（不挑镜）' : '先给镜头生成视频，再出片');

  const batchBar = (bar: Bar) => {
    const n = bar === 'img' ? nImg : nVid;
    const dis = n === 0;
    const label = bar === 'img' ? '批量生成图片' : '批量生成视频';
    return (
      <div className={'sb-tool ' + (bar === 'img' ? 'batch-images' : 'batch-videos')}>
        <VendorSelect kind={bar === 'vid' ? 'video' : 'image'} cls="sb-model" />
        <button
          type="button" className={'sb-selchip' + (n ? ' is-active' : '')}
          title={phSegs.length
            ? '全选只选「画面描述已补齐」的镜：本集 ' + phSegs.length + ' 镜是 ' + PH_MARK + ' 占位，不参与'
            : undefined}
          onClick={() => {
            // ★ 全选范围 = `eligible`（**排除占位镜**）：全选一个不能生成的镜，
            //   结果必然是"点了批量 ⇒ 白烧配额"或"批量啥也没干"，两头都说不通。
            const on = n < eligible.length;          // 未全选 → 全选；已全选 → 清空
            const next: Record<string, boolean> = {};
            if (on) eligible.forEach((sg) => { next[sg.id] = true; });
            setSel(bar === 'img' ? { img: next, vid: sel.vid } : { img: sel.img, vid: next });
          }}
        >
          {n >= eligible.length && eligible.length > 0 ? '取消全选' : '全选'}
          <span className="muted">已选择 {n}{phSegs.length ? ' · 占位不计 ' + phSegs.length : ''}</span>
        </button>
        <button
          type="button" className={'btn btn--sm sb-genbtn' + (dis ? ' is-disabled' : '')}
          disabled={dis}
          aria-disabled={dis}
          aria-label={dis ? '请选择需要生成的片段' : '需要0积分'}
          onClick={() => onBatchGen(bar)}
        >{label}</button>
      </div>
    );
  };

  const gates = (ep.storyboard as unknown as { gates?: { fatal?: string[]; tips?: string[]; blocked?: boolean; at?: string } })?.gates || {};
  const fatal = gates.fatal || [];
  const tips = gates.tips || [];

  return (
    <WorkbenchShell
      title={p.name}
      steps={SB_STEPS}
      scope="storyboard"
      onStep={(k) => Router.go('/playlet/review/' + pid + '?step=' + k, true)}
    >
      <div className="content" style={{ maxWidth: 1600 }}>
        {/* 动作条 */}
        <div className="row between gap16" style={{ marginTop: 8 }}>
          <div className="row gap12">
            <span className="page-title">{ep.title}</span>
            <span className="muted small">
              第 {ep.no} 集 · {ep.storyboard?.ratio || p.ratio} · 共 {segs.length} 镜
            </span>
          </div>
          <div className="sb-batch">
            {batchBar('img')}
            <span className="sb-div" />
            {batchBar('vid')}
            <span className="sb-div" />
            <button type="button" className="btn btn--sm" onClick={batchDownload}>批量下载</button>
            <button type="button" className="btn btn--sm" onClick={onPreview}>预览</button>
            <button
              type="button" className="btn btn--primary btn--sm" title={composeTip}
              disabled={!nReadyVid || !!busy || phSegs.length > 0} onClick={compose}
            >生成最终视频</button>
          </div>
        </div>

        {/* 渲染进度：数的是**磁盘事实**（有 `keyframe` / 有 `video` 的镜数），
            不是后端的步骤推导，也不是 `p.v5.render`（那是按集的，理由见上方注释）。 */}
        <div className="row gap16 mt8" style={{ alignItems: 'center', flexWrap: 'wrap' }}>
          <MiniBar label="静帧" done={doneKF} total={segs.length} color="var(--accent)"
            tip="已生成关键帧的镜数 / 本集镜数（按磁盘上的 stills 与 clips 数）" />
          <MiniBar label="视频" done={doneVid} total={segs.length} color="var(--success)"
            tip="已生成片段的镜数 / 本集镜数。⚠️ pack 档产物是组级 packNN.mp4，逐镜计数会偏少" />
          {phSegs.length ? (
            <span className="small" style={{ color: 'var(--warn)' }}
              title="这些镜的画面描述还是占位，不能送去生成（见卡片上的说明）">
              {PH_MARK} 占位 {phSegs.length} 镜
            </span>
          ) : null}
          {nFailed ? (
            <span className="small" style={{ color: 'var(--danger)' }}
              title="渲染台账里本集有这些镜上次是 failed / expired —— 不必再点生成去撞">
              上次渲染失败 {nFailed} 镜
            </span>
          ) : null}
        </div>

        {/* ⚠️ 「自动质检」刻意单开一行、右对齐：动作条 `row.between` 已经很挤
            （两个厂商胶囊 + 两个全选 + 三四个按钮），再塞 170px 会把**页面标题
            挤成两行**（旧版真浏览器量到 `.page-title` 高度 52px = 两行；单行 26px）。 */}
        <div className="row mt8" style={{ justifyContent: 'flex-end' }}>
          <span className="qc-switches">
            <span className="qc-label" title="默认全关：判断权在人。打开就是允许机器自己判不合格并重画/重拍">
              自动质检
            </span>
            {QC_LABELS.map((c) => {
              const on = qcPayload()[c.key === 'still' ? 'still_qc' : 'clip_qc'] === 1;
              return (
                <button
                  key={c.key} type="button"
                  className={'qc-chip' + (on ? ' is-on' : '')}
                  aria-pressed={on} title={c.hint}
                  onClick={() => {
                    const now = qcToggle(c.key as QcKey);
                    Store.toast((c.key === 'still' ? '静帧' : '成片') + '自动质检已'
                      + (now ? '打开（不合格会自动重画/重拍，会烧配额）' : '关闭'));
                    // 靠 useStore 的订阅触发重渲染（qc 状态不在 store 里，这里手动 set 一次）
                    setSel({ img: Object.assign({}, sel.img), vid: sel.vid });
                  }}
                >{c.label}</button>
              );
            })}
          </span>
        </div>

        {/* 分镜契约门的判决条：人工模式下门**只报不拦**（判断权在人），
            所以这份判决必须出现在页面上 —— 否则「把门降级为警告」在界面上
            与「把门删掉」没有区别。没有报告时**什么都不显示**（绝不假报「通过」）。 */}
        {fatal.length || tips.length ? (
          <div className={'gate-bar' + (gates.blocked ? ' gate-bar--block' : '')}>
            <div className="gate-bar-head">
              {gates.blocked ? '媒体链被分镜契约门挡住' : '分镜契约警告 · 不拦你（人工模式）'}
              {gates.at ? <span className="muted small"> · {gates.at}</span> : null}
            </div>
            <ul className="gate-bar-list">
              {fatal.map((x, i) => <li key={'f' + i}>{x}</li>)}
              {tips.slice(0, 4).map((x, i) => <li className="muted" key={'t' + i}>{x}</li>)}
            </ul>
          </div>
        ) : null}

        {segs.length ? (
          <div className="sb-layout mt16">
            {/* 缩略轨 */}
            <div className="sb-rail">
              {segs.map((s) => (
                <div
                  key={s.id}
                  className={'sb-rail-item' + (s.id === curActive ? ' is-active' : '')}
                  title={s.title}
                  onClick={() => {
                    setActive(s.id);
                    document.querySelector('[data-seg="' + s.id + '"]')
                      ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
                  }}
                >
                  {s.keyframe ? <img src={s.keyframe} alt="" /> : null}
                  <span className="no">{String(s.order).padStart(2, '0')}</span>
                </div>
              ))}
              <button type="button" className="sb-rail-add" title="新增镜头" onClick={addSegment}>
                {Icon.plus(20)}
              </button>
            </div>

            <div className="sb-main">
              {segs.map((s) => (
                <SegmentPanel
                  key={s.id}
                  seg={s}
                  busyWhich={busy && (busy.sids ? !!busy.sids[s.id] : busy.sid === s.id) ? busy.which : null}
                  runNote={runNote}
                  placeholder={isPlaceholder(s)}
                  unresolved={(v5Of(s).unresolved_tokens || []).filter(Boolean)}
                  selected={!!(sel.img[s.id] || sel.vid[s.id])}
                  onSelectSeg={() => {
                    if (isPlaceholder(s)) {
                      // ★ 占位镜不参与批量：让它进选择集只会让「已选择 N」骗人
                      //   （批量时又被排除 ⇒ 计数与结果不符）。直接说明为什么不能选。
                      Store.toast('这镜的画面描述还是 ' + PH_MARK + ' 占位，不参与批量生成 —— 先点笔补齐', 'error');
                      return;
                    }
                    const v = !sel.img[s.id];
                    setSel({
                      img: Object.assign({}, sel.img, { [s.id]: v }),
                      vid: Object.assign({}, sel.vid, { [s.id]: v }),
                    });
                  }}
                  onEdit={() => setEditSeg(s)}
                  onDelete={() => setDelSeg(s)}
                  onGen={(which) => generate(s.id, which)}
                  onHistory={(rect, el, which) => setHistory({ rect, el, seg: s, which })}
                />
              ))}
            </div>
          </div>
        ) : (
          <div className="empty mt32">
            {Icon.empty(48)}
            <div>本集还没有分镜</div>
            <button type="button" className="btn btn--primary btn--sm mt16" onClick={addSegment}>
              {Icon.plus(16)} 新增镜头
            </button>
          </div>
        )}
      </div>

      {history ? (
        <Menu
          anchor={history.rect}
          anchorEl={history.el}
          onClose={() => setHistory(null)}
          items={[{
            label: (() => {
              const cur = history.which === 'keyframe' ? history.seg.keyframe : history.seg.video;
              return cur ? '当前选用 1 项（历史回切尚未移植）' : '暂无历史记录';
            })(),
            action: () => Store.toast('历史记录回切尚未移植'),
          }]}
        />
      ) : null}

      {editSeg ? (
        <EditSegmentModal
          seg={editSeg}
          onClose={() => setEditSeg(null)}
          onSave={(patch) => { const s = editSeg; setEditSeg(null); void saveSegment(s, patch); }}
        />
      ) : null}

      {delSeg ? (
        <ConfirmModal
          title="删除镜头" danger confirmText="删除"
          text={'确定删除「' + (delSeg.title || '该镜头') + '」？已生成的关键帧与视频将一并移除。'}
          onClose={() => setDelSeg(null)}
          onOk={async () => {
            const sid = delSeg.id;
            setDelSeg(null);
            try {
              // 线上是 DELETE /segments/{sid}
              await Api.deleteSegment(pid, eid, sid);
              const sb = await Api.getStoryboard(eid);
              Store.upsertStoryboard(pid, eid, sb);
              Store.toast('已删除');
            } catch (e) { Store.toast('删除失败：' + (e as Error).message, 'error'); }
          }}
        />
      ) : null}

      {previewSrc ? (
        <Modal title={ep.title + ' · 预览'} width={480} confirmText="关闭" cancelText="关闭"
          onClose={() => setPreviewSrc(null)} onConfirm={() => setPreviewSrc(null)}
          body={
            <>
              <video src={previewSrc} controls autoPlay style={{ width: '100%', borderRadius: 8 }} />
              <div className="muted small">共 {segs.length} 镜 · 已出视频 {doneVid} 镜</div>
            </>
          }
        />
      ) : null}

      {finalUrl ? (
        <Modal title={'成片 · ' + ep.title} width={640} confirmText="关闭" cancelText="关闭"
          onClose={() => setFinalUrl(null)} onConfirm={() => setFinalUrl(null)}
          body={
            <>
              <video src={finalUrl.url} controls playsInline
                style={{ width: '100%', borderRadius: 10, background: '#000' }} />
              <div className="row between gap12 mt16">
                <a className="btn btn--sm" href={finalUrl.url} target="_blank" rel="noopener">
                  在新窗口打开 / 下载
                </a>
                <span className="muted small">{finalUrl.url}</span>
              </div>
              {finalUrl.warnings.length ? (
                <div className="gate-bar mt16">
                  <div className="gate-bar-head">分镜契约警告（不拦你）</div>
                  <ul className="gate-bar-list">
                    {finalUrl.warnings.slice(0, 6).map((x, i) => <li key={i}>{x}</li>)}
                  </ul>
                </div>
              ) : null}
            </>
          }
        />
      ) : null}
    </WorkbenchShell>
  );
}

/* ------------------------------------------------------------------ 子件 */

/** 一行迷你进度条（动作条下面的渲染进度用）。 */
function MiniBar({ label, done, total, color, tip }: {
  label: string; done: number; total: number; color: string; tip: string;
}) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  return (
    <span className="row gap8" style={{ alignItems: 'center' }} title={tip}>
      <span className="muted small">{label}</span>
      <span style={{
        display: 'inline-block', width: 120, height: 6, borderRadius: 'var(--r-pill)',
        background: 'var(--bg-subtle)', overflow: 'hidden',
      }}>
        <span style={{
          display: 'block', height: '100%', width: pct + '%',
          background: color, borderRadius: 'var(--r-pill)',
        }} />
      </span>
      <span className="small">{done}/{total}</span>
    </span>
  );
}

/**
 * 渲染台账状态条（关键帧卡 + 视频卡各一条）。
 *
 * ⚠️ 口径必须说准（2026-10-02 读 `webmap.storyboard_detail` 与 `webwrite._invalidate_shot`）：
 *   `v5.job_state` / `v5.job_error` 来自 **`video_jobs.json`（视频任务台账）**，
 *   后端**没有**给静帧单独的状态文件字段 —— `stills.json` 只贡献了 `still_prompt`。
 *   ⇒ 视频卡：直接播台账状态。
 *   ⇒ 关键帧卡：播**磁盘事实**（有没有 `keyframe` 图），台账只在"作废/失败"时借来解释。
 *   把视频台账的状态当成静帧状态 = 另一种形式的假报，所以这里**分开判**。
 * ⚠️ `job_state` 为空 = **台账里没有这一镜的记录**，不等于"没开始"：
 *   pack 档的 job 是按组记的（`pack01`…），逐镜查不到 ⇒ 必须显示"台账无记录"，
 *   不能替它编一个 `pending`（`_seg_status()` 那个回落是三色态用的，不是台账事实）。
 */
function JobStatus({ seg, which }: { seg: Segment; which: Which }) {
  const [open, setOpen] = useState(false);
  const v5 = v5Of(seg);
  const isKF = which === 'keyframe';
  const err = String(v5.job_error || '');
  const state = String(v5.job_state || '');

  let fg = 'var(--text-tertiary)';
  let text = '';
  if (isKF) {
    const voidedByEdit = /分镜被编辑/.test(err);
    if (seg.keyframe) {
      text = '静帧已在盘';
      fg = 'var(--success)';
    } else if (voidedByEdit) {
      text = '静帧已作废（分镜被编辑过）';
      fg = 'var(--warn)';
    } else {
      text = '尚无静帧';
    }
    // 台账有记录、图不在盘上 = 静帧记录与磁盘不一致（重画失败的典型形状）
    if (!seg.keyframe && v5.still_prompt) text += ' · stills.json 有记录但图不在盘上';
  } else if (!state) {
    text = '渲染台账无本镜记录';
  } else {
    const m = JOB_META[state];
    text = m ? m.text : '台账状态：' + state;
    fg = m ? m.fg : 'var(--text-tertiary)';
  }

  const short = err.length > 46 ? err.slice(0, 46) + '…' : err;
  return (
    <div className="row gap8 mt8" style={{ alignItems: 'flex-start', fontSize: 12, flexWrap: 'wrap' }}>
      <span style={{
        width: 8, height: 8, borderRadius: '50%', background: fg,
        flexShrink: 0, marginTop: 4,
      }} />
      <span style={{ color: fg }}>{text}</span>
      {err ? (
        <>
          <button
            type="button" className="tool-link" title={'完整信息：' + err}
            onClick={() => setOpen(!open)}
          >
            {open ? '收起' : '看详情'}
          </button>
          {open
            ? (
              <div className="muted" style={{
                width: '100%', fontSize: 12, lineHeight: 1.7, wordBreak: 'break-all',
                background: 'var(--bg-subtle)', borderRadius: 'var(--r-sm)', padding: '6px 8px',
              }}>{err}</div>
            )
            : <span className="muted" style={{ wordBreak: 'break-all' }}>{short}</span>}
        </>
      ) : null}
    </div>
  );
}

function SegmentPanel({ seg, busyWhich, runNote, placeholder, unresolved, selected, onSelectSeg, onEdit, onDelete, onGen, onHistory }: {
  seg: Segment;
  busyWhich: string | null;
  runNote: string;
  placeholder: boolean;
  unresolved: string[];
  selected: boolean;
  onSelectSeg: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onGen: (which: Which) => void;
  onHistory: (rect: DOMRect, el: HTMLElement, which: Which) => void;
}) {
  const scenes = raw(seg).scenes || [];
  const totalShots = scenes.reduce((a, c) => a + ((c.shots || []).length), 0);
  const v5 = v5Of(seg);
  const secs = (seg.duration_ms || 0) / 1000;
  const sceneTitle = (scenes[0] && scenes[0].title) || raw(seg).scene || '';
  return (
    <div className="sb-panel" data-seg={seg.id}>
      <div className="sb-panel-head">
        <div className="row gap12">
          <span className="sb-shot-no" title={v5.shot_name ? '镜名（与 CLI --rerender ' + v5.shot_name + ' 同一个标识）' : undefined}>
            镜头 {seg.order}{v5.shot_name ? ' · ' + v5.shot_name : ''}
          </span>
          <span className="muted small">
            {totalShots} 个镜次 · {secs}s
            {v5.shot_type ? ' · ' + v5.shot_type : ''}
            {v5.text_shot ? ' · 文字镜=' + v5.text_shot : ''}
          </span>
        </div>
        <div className="sb-head-actions">
          <button type="button"
            className={'sb-selchip' + (selected ? ' is-active' : '')}
            disabled={placeholder}
            style={placeholder ? { opacity: .45, cursor: 'not-allowed' } : undefined}
            title={placeholder ? '画面描述仍是 ' + PH_MARK + ' 占位，不参与批量生成' : '选择该镜头'}
            onClick={onSelectSeg}>{selected ? '已选' : '选择'}</button>
          <button type="button" className="icon-btn" title="编辑（写分镜表的列）" onClick={onEdit}>{Icon.edit(18)}</button>
          <button type="button" className="icon-btn" title="删除" onClick={onDelete}>{Icon.trash(18)}</button>
        </div>
      </div>

      {placeholder ? (
        <div className="gate-bar gate-bar--block" style={{ marginTop: 12 }}>
          <div className="gate-bar-head">画面描述还是 {PH_MARK} 占位 —— 本镜不能送去生成</div>
          <ul className="gate-bar-list">
            <li>
              占位句本身就有 27 字，而 `storyboard.parse` 的入镜门槛是「≥15 字」⇒
              它会照常进渲染清单，模型收到的是「请填写不少于 15 字的具体内容后再生成」这种中间指令文字，
              画出来是烧字的废镜。所以现在点生成 = 白烧一次图片 + 一次视频配额 ⇒ 静帧与视频按钮已禁用。
            </li>
            <li>右上角的笔 → 把「画面描述」改成真实内容（≥15 字）后本镜自动恢复可生成。</li>
          </ul>
        </div>
      ) : null}

      {unresolved.length ? (
        <div className="gate-bar" style={{ marginTop: 8 }}>
          <div className="gate-bar-head">
            {unresolved.length} 个 @点名 在资产注册表里找不到：{unresolved.join('、')}
          </div>
          <ul className="gate-bar-list">
            <li className="muted">
              这个名字没有身份锚点 ⇒ 参考图绑不上，该人物/道具的长相由模型每镜自己编 = 跨镜穿帮
              （由 `assets.resolve_mentions` 报出，随分镜一起给）。
              处理：去资产库补卡，或把分镜里的 `@名` 改成注册表里逐字一致的写法。
            </li>
          </ul>
        </div>
      ) : null}

      <div className="row gap16 mt16" style={{ alignItems: 'flex-start' }}>
        <div className="flex1">
          <div className="sb-section-title">根据以下分镜生成视频</div>
          <div className="sb-hint">
            单镜时长 {SEC_MIN}-{SEC_MAX}s（供应商硬约束；pack 档一组超 12s 时媒体层会等比压缩每镜秒数）·
            输入 @ 可引用角色、场景 · 下方正文即分镜表「画面描述」列，
            仅剥掉了「无人像」标记与历史收尾句（这两样都不进提示词）
          </div>
          {scenes.length ? (
            <div className="sb-scene-chip">
              {Icon.image(14)} 本镜头场景设定：{sceneTitle || '未指定'}
            </div>
          ) : null}
          <div className="sb-shots">
            {scenes.map((sc, si) => (sc.shots || []).map((sh, hi) => (
              <div className="sb-shot" key={si + '-' + hi}>
                <div className="st">
                  {sh.title} <span className="muted small">{sh.duration_sec}s</span>
                </div>
                <div className="sd" dangerouslySetInnerHTML={{ __html: richHtml(stripFoot(sh.rich)) }} />
                {v5.dialogue ? (
                  <div className="sb-hint">对白／声音：{v5.dialogue}</div>
                ) : null}
              </div>
            )))}
          </div>
          {/* 说明性文字（**不是约束**）：旧版这里抄的是上一站的一句硬编码文案，
              后端并不按它执行 —— 换成如实描述这条链怎么产提示词。 */}
          <div className="sb-foot">
            每镜的送生成提示词由后端按「分镜表各列 + 类型包风格块（style-block.md）+
            资产参考图声明」组装；上面展示的就是实际送生成的那份内容（提示词全文见
            编辑弹窗底部的只读区）。有无台词由项目 brief 的 `audio_mode` 决定、
            背景音乐由类型包禁忌与 SHORTDRAMA_VIDEO_BGM 决定、画内文字默认只禁烧录型
            字幕（场景固有的门牌 / 面板读数 / 标签放行）—— 本页不替你改这些策略。
          </div>
        </div>
        <div className="sb-side">
          {(['keyframe', 'video'] as Which[]).map((w) => (
            <GenCard
              key={w} seg={seg} which={w}
              busy={busyWhich === w} runNote={runNote}
              placeholder={placeholder}
              onGen={() => onGen(w)}
              onHistory={onHistory}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function GenCard({ seg, which, busy, runNote, placeholder, onGen, onHistory }: {
  seg: Segment; which: Which; busy: boolean; runNote: string; placeholder: boolean;
  onGen: () => void;
  onHistory: (rect: DOMRect, el: HTMLElement, which: Which) => void;
}) {
  const isKF = which === 'keyframe';
  const media = (isKF ? seg.keyframe : seg.video) || '';
  const hint = isKF ? '关键帧封面画面内容，可选择剧作参考，或跳过生成' : '视频片段，由关键帧驱动生成';
  // ★ 占位镜的生成按钮**禁用并写明理由**（判据与 `generateRemote` 里那道拦截同源）。
  const dis = busy || placeholder;
  const tip = placeholder
    ? '画面描述仍是 ' + PH_MARK + ' 占位：现在生成 = 白烧配额（补齐后即可用）'
    : (busy ? '本镜正在生成…' : '生成' + (isKF ? '关键帧' : '视频片段'));
  return (
    <div className="sb-gen-card">
      <div className="sb-gen-head">
        <span className="lbl"><span className="ic">{isKF ? '▣' : '▶'}</span>{isKF ? '关键帧封面' : '视频'}</span>
        <div className="row gap8">
          <button type="button" className="icon-btn" title="选择本地文件"
            onClick={() => Store.toast('导入本地素材尚未移植（v5 的素材由媒体链产出）')}>
            {Icon.plus(18)}
          </button>
          {/* `.btn[disabled]` 自带 0.45 透明 + not-allowed，不需再加修饰类 */}
          <button type="button" className="btn btn--primary btn--xs"
            disabled={dis} aria-disabled={dis} title={tip} onClick={onGen}>
            {Icon.refresh(14)} 生成
          </button>
        </div>
      </div>
      <div className="sb-gen-body">
        {busy
          ? (
            <div className="sb-gen-empty">
              <div className="spin" style={{
                width: 26, height: 26, border: '2px solid var(--border)',
                borderTopColor: 'var(--accent)', borderRadius: '50%', margin: '0 auto 12px',
              }} />
              {runNote || '正在生成…'}
            </div>
          )
          : (!media
            ? (
              <div className="sb-gen-empty">
                {isKF ? '尚未生成关键帧' : '尚未生成视频'}
                <br /><span className="small">{placeholder ? '先补齐画面描述' : '点击右上角「生成」'}</span>
              </div>
            )
            : (isKF || !/\.mp4$/i.test(media)
              ? <img src={media} alt="" />
              : <video src={media} controls preload="metadata" playsInline />))}
      </div>
      <JobStatus seg={seg} which={which} />
      <div className="sb-history">
        <button type="button" className="tool-link"
          onClick={(e) => onHistory((e.currentTarget as HTMLElement).getBoundingClientRect(),
            e.currentTarget as HTMLElement, which)}>
          历史记录{media ? ' · 1 项' : ' · 空'}
        </button>
      </div>
      <div className="sb-hint">{hint}</div>
    </div>
  );
}

/* -------------------------------------------------- 编辑弹窗（写分镜列） */

/** `EDITABLE_COLS` 的十列（与 `v5/webwrite.py` 同一份名单，**多一个键后端就 400**）。 */
interface EditFields {
  visual: string;
  dialogue: string;
  seconds: string;
  shot_type: string;
  angle: string;
  camera: string;
  scene: string;
  visual_style: string;
  tail: string;
  sfx: string;
}

const FIELD_STYLE: React.CSSProperties = {
  display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 14,
};
const LABEL_STYLE: React.CSSProperties = { fontSize: 13, color: 'var(--text-secondary)' };
const GRID_STYLE: React.CSSProperties = {
  display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '0 12px',
};

function Field({ label, hint, children }: {
  label: string; hint?: string; children: React.ReactNode;
}) {
  return (
    <div className="field" style={FIELD_STYLE}>
      <label style={LABEL_STYLE}>{label}</label>
      {children}
      {hint ? <span className="hint">{hint}</span> : null}
    </div>
  );
}

/**
 * 编辑一镜 = 改**分镜表的列**。
 *
 * 为什么字段长这样（`v5/webwrite.py` 的 `EDITABLE_COLS`，按列名改写单元格）：
 *   visual / dialogue / seconds / shot_type / angle / camera /
 *   scene / visual_style / tail / sfx
 *   —— 名单外的键一律进 `skipped[]`；**全部**在名单外时后端直接 400。
 *
 * 三处**刻意不再提供**的旧输入框：
 *   · 「镜头标题」——分镜表**没有标题列**，标题是 `webmap._seg_title()` 按
 *     「景别·场景」现合成的 ⇒ 留个能输入而后端不认的框 = 骗人。改成只读展示 +
 *     指明"改景别/场景才能改标题"。
 *   · 「时长（毫秒）」——后端写的是**秒**（`storyboard.parse` 用
 *     `int(float(时长列))`）。旧框是毫秒且 `Math.max(4000, Math.min(15000, …))`，
 *     注释还写「后端也有」：**后端没有任何毫秒处理**，那句话不属实。
 *   · 「视频提示词」——它是后端**装配**出来的（`prompt.build_video_prompt`），
 *     不是分镜列 ⇒ 换成只读全文展示。
 */
function EditSegmentModal({ seg, onClose, onSave }: {
  seg: Segment; onClose: () => void; onSave: (patch: Record<string, unknown>) => void;
}) {
  const v5 = v5Of(seg);
  const secsInit = (seg.duration_ms || 0) / 1000;
  const noSeconds = !(secsInit > 0);
  const [f, setF] = useState<EditFields>({
    // ★ 初值取**还原了 `@资产名`** 的原文，不是 `content_plain` —— 理由见 `visualRaw`。
    visual: visualRaw(seg),
    dialogue: String(v5.dialogue || ''),
    seconds: noSeconds ? '6' : String(secsInit),
    shot_type: String(v5.shot_type || ''),
    angle: String(v5.angle || ''),
    camera: String(v5.camera || ''),
    // ★ `scene` 有两个来源：segment 级 `scene` 与 `scenes[0].title`（`webmap` 同一份数据，
    //   取到哪个都等价）。两个都要**还原成空**：后端在空格子上填的是展示用的
    //   `未标注场景`，原样存回去 = 把四个字写进分镜表的「场景」列。
    scene: (() => {
      const sc0 = String(raw(seg).scene || (raw(seg).scenes || [])[0]?.title || '');
      return sc0 === SCENE_DISPLAY_EMPTY ? '' : sc0;
    })(),
    visual_style: String(raw(seg).visual_style || ''),
    tail: String(v5.tail || ''),
    sfx: String(v5.sfx || ''),
  });
  const set = (k: keyof EditFields) => (
    e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>,
  ) => setF(Object.assign({}, f, { [k]: e.target.value }));

  const visLen = f.visual.trim().length;
  const visBad = visLen < 15;
  const n = Number(String(f.seconds).trim());
  const secBad = !(Number.isFinite(n) && n >= SEC_MIN && n <= SEC_MAX);
  const prompt = seg.video_prompt || raw(seg).video_prompt_text || '';

  return (
    <Modal
      title={'编辑镜头 · 第 ' + seg.order + ' 镜' + (v5.shot_name ? '（' + v5.shot_name + '）' : '')}
      width={680} confirmText="保存到分镜表"
      confirmDisabled={visBad || secBad}
      onClose={onClose}
      hint={isPlaceholder(seg)
        ? '本镜现在是 ' + PH_MARK + ' 占位：补齐画面描述（≥15 字真实内容）后才能送去生成。'
        : '保存会作废本镜的静帧与成片片段（提示词含这些列），需重新生成。'}
      onConfirm={() => onSave({
        visual: f.visual.trim(),
        dialogue: f.dialogue,
        // ★ 单位是**秒**：后端按秒写进「时长」列（`EDITABLE_COLS.seconds → ("时长","秒")`）
        seconds: String(f.seconds).trim(),
        shot_type: f.shot_type,
        angle: f.angle,
        camera: f.camera,
        scene: f.scene,
        visual_style: f.visual_style,
        tail: f.tail,
        sfx: f.sfx,
      })}
      body={
        <>
          <Field
            label="画面描述（列：画面）"
            hint={
              '不少于 15 字（当前 ' + visLen + ' 字）—— `storyboard.parse` 会把更短的行**当占位整行跳过**，'
              + '于是这镜从分镜里消失。' + (visBad ? ' 现在不能保存。' : '')
            }
          >
            <textarea className="ov-textarea" style={{ minHeight: 130 }} value={f.visual}
              onChange={set('visual')} />
          </Field>
          <div className="hint" style={{ marginTop: -8, marginBottom: 14 }}>
            ⚠️ 文中的 `@资产名` 是参考图绑定的锚点：删掉 `@` 或改成注册表外的名字 ⇒ 该人物/道具
            不再绑设定表（后端会把它报进 `unresolved_tokens`，镜头卡片上会显出来）。
            另外：若本镜原文写过「无人像」标记，后端给的是剥掉标记后的正文，
            保存会把该标记从分镜表里抹掉（它不进 v5 字段，所以本页无法替你先留着）。
          </div>

          <div style={GRID_STYLE}>
            <Field
              label="时长（秒）"
              hint={'供应商硬约束 ' + SEC_MIN + '–' + SEC_MAX + ' 秒；请填整数——'
                + '`storyboard.parse` 用 int(float(...)) 读这一列，6.5 会当 6 用。'
                + (noSeconds ? ' 本镜「时长」列原先解析不出数字，已给 6。' : '')}
            >
              <input className="ov-input" type="number" min={SEC_MIN} max={SEC_MAX} step={1}
                value={f.seconds} onChange={set('seconds')}
                style={secBad ? { outline: '1px solid var(--danger)' } : undefined} />
            </Field>
            <Field label="景别（列：景别）" hint="镜头标题 = 「景别·场景」，改这两格就改了标题">
              <input className="ov-input" value={f.shot_type} onChange={set('shot_type')}
                placeholder="如：中近景 / 大全景" />
            </Field>
            <Field label="角度（列：角度）">
              <input className="ov-input" value={f.angle} onChange={set('angle')}
                placeholder="如：平视 / 俯拍" />
            </Field>
            <Field label="运镜（列：运镜）">
              <input className="ov-input" value={f.camera} onChange={set('camera')}
                placeholder="如：缓推 / 手持跟拍" />
            </Field>
            <Field label="场景（列：场景）"
              hint="写注册表里的场景名、不要加 @：宽景镜按「本列 strip 后查注册表」绑场景图，带 @ 会查不到">
              <input className="ov-input" value={f.scene} onChange={set('scene')} />
            </Field>
            <Field label="视觉风格（列：视觉风格）">
              <input className="ov-input" value={f.visual_style} onChange={set('visual_style')} />
            </Field>
            <Field label="落幅（列：落幅）" hint="本镜结束的定格，供下一镜接续">
              <input className="ov-input" value={f.tail} onChange={set('tail')} />
            </Field>
            <Field label="音效（列：音效）" hint="旁白式画外音也写在这里">
              <input className="ov-input" value={f.sfx} onChange={set('sfx')} />
            </Field>
          </div>

          <Field label="对白（列：对白）"
            hint="无声镜写「（无声，环境音）」。brief 的 audio_mode 会真的被 reviewer 校验">
            <textarea className="ov-textarea" style={{ minHeight: 64 }} value={f.dialogue}
              onChange={set('dialogue')} />
          </Field>

          <div className="field" style={{ ...FIELD_STYLE, marginBottom: 8 }}>
            <label style={LABEL_STYLE}>镜头标题（后端合成，改不了）</label>
            <input className="ov-input" readOnly value={seg.title || ''} />
            <span className="hint">分镜表没有「标题」列：标题由后端按「景别·场景」拼出来 ⇒ 想改标题就改上面那两格。</span>
          </div>

          <div className="field" style={FIELD_STYLE}>
            <label style={LABEL_STYLE}>视频提示词（后端装配，只读）</label>
            <textarea className="ov-textarea" readOnly
              value={prompt || '（后端没组出提示词：多半是缺类型包 / 分镜未被认作镜头）'}
              style={{ minHeight: 120, whiteSpace: 'pre-wrap' }} />
            <span className="hint">
              这句不是分镜列，改了不会落盘。它就是实际送去生成的那份（分镜各列 +
              类型包风格块 + 参考图声明装配出来的）。要改它，改上面那些列。
            </span>
          </div>
        </>
      }
    />
  );
}

/** 把 run 的终态翻成一句**如实**的话（不把 blocked/incomplete 说成成功）。 */
function runOutcomeText(st: RunRecord, label: string): string {
  const s = String((st && st.status) || '?');
  const res = (st.result || {}) as { reason?: string; error?: string; missing?: string[] };
  const why = res.reason || res.error || st.note || '';
  if (s === 'ok') return label + '已生成';
  if (s === 'blocked') return '被媒体门拦住：' + (why || '前置条件未满足');
  if (s === 'incomplete') {
    const miss = res.missing || [];
    return '部分完成（缺 ' + miss.length + ' 镜' + (miss.length ? '：' + miss.slice(0, 4).join(',') : '') + '）';
  }
  if (s === 'cancelled') return '已取消';
  if (s === 'timeout') return '前端等待超时（任务可能仍在后端跑，稍后刷新看看）';
  if (s === 'lost') return '执行进程已消失（被回收/崩溃）—— 看后端日志';
  return label + '生成未成功（' + s + '）' + (why ? '：' + why : '');
}
