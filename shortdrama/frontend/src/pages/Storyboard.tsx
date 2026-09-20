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

/** 线上分镜每个镜次末尾附一句统一收尾约束；原站把它单独作为灰色脚注展示，
 *  这里同样从正文里剥离，避免重复。 */
const FOOT_RE = /视频全程不要字幕[^。]*。?/;

function stripFoot(rich: RichToken[] | undefined): RichToken[] {
  return (rich || [])
    .map((t) => (t.type === 'text'
      ? { type: 'text' as const, value: String(t.value || '').replace(FOOT_RE, '') }
      : t))
    .filter((t) => !(t.type === 'text' && !t.value.trim()));
}

interface Shot {
  id?: string;
  title?: string;
  duration_sec?: number;
  plain?: string;
  rich?: RichToken[];
}
interface Scene { id?: string; title?: string; shots?: Shot[] }

export function Storyboard({ pid, eid }: { pid: string; eid: string }) {
  useStore();                       // 订阅 store 变更
  const p = Store.getProject(pid) as Project | null;
  const ep = p ? ((p.episodes || []).find((x) => x.id === eid) as Episode | undefined) : undefined;

  const segs: Segment[] = useMemo(() => (ep?.storyboard?.segments) || [], [ep]);
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
    const sids: Record<string, boolean> = {};
    ids.forEach((i) => { sids[i] = true; });
    setBusy({ sid: ids[0], sids, which });
    setRunNote('已提交…');

    const label = which === 'keyframe' ? '关键帧' : '视频';
    // ★ 厂商随请求发出：`which` 是 'keyframe'/'video'，而工具栏栏位是 'img'/'vid'
    //   —— 这里做映射，**单镜与批量共用同一份选择**。
    const cap = which === 'video' ? 'video' : 'image';
    const body = Object.assign({ segment_ids: ids }, Vendor.payload(cap));
    const vCode = Vendor.pick(cap);
    Store.toast('已提交 ' + ids.length + ' 个' + label + '到后端'
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
      if (onlyIds.length && !onlyIds.includes(s.id)) return false;
      return which === 'keyframe' ? !s.keyframe : !s.video;
    });
    if (!targets.length) {
      // ★ 别让这里变成"死胡同"（旧版实测：镜头都生成完之后再点什么都不发生，
      //   用户会以为按钮坏了）。如实说明"没有要生成的"，并**指出下一步在哪**。
      Store.toast(which === 'video'
        ? '所选镜头都已有视频 → 下一步：右上角「生成最终视频」出片'
        : '所选镜头都已有图片');
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
      await Api.addSegment(pid, eid);
      const sb = await Api.getStoryboard(eid);
      Store.upsertStoryboard(pid, eid, sb);
      Store.toast('已新增镜头', 'ok');
    } catch (e) { Store.toast('新增失败：' + (e as Error).message, 'error'); }
  };

  const saveSegment = async (seg: Segment, patch: Record<string, unknown>) => {
    try {
      await Api.updateSegment(seg.id, patch);
      const sb = await Api.getStoryboard(eid);
      Store.upsertStoryboard(pid, eid, sb);
      Store.toast('已保存', 'ok');
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
   */
  const nReadyVid = segs.filter((s) => s.video).length;
  const composeTip = nReadyVid ? '按已生成的镜头出整片（不挑镜）' : '先给镜头生成视频，再出片';

  const batchBar = (bar: Bar) => {
    const n = bar === 'img' ? nImg : nVid;
    const dis = n === 0;
    const label = bar === 'img' ? '批量生成图片' : '批量生成视频';
    return (
      <div className={'sb-tool ' + (bar === 'img' ? 'batch-images' : 'batch-videos')}>
        <VendorSelect kind={bar === 'vid' ? 'video' : 'image'} cls="sb-model" />
        <button
          type="button" className={'sb-selchip' + (n ? ' is-active' : '')}
          onClick={() => {
            const on = n < segs.length;          // 未全选 → 全选；已全选 → 清空
            const next: Record<string, boolean> = {};
            if (on) segs.forEach((sg) => { next[sg.id] = true; });
            setSel(bar === 'img' ? { img: next, vid: sel.vid } : { img: sel.img, vid: next });
          }}
        >
          {n >= segs.length && segs.length > 0 ? '取消全选' : '全选'}
          <span className="muted">已选择 {n}</span>
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
              第 {ep.no} 集 · {ep.storyboard?.ratio || p.ratio}
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
              disabled={!nReadyVid || !!busy} onClick={compose}
            >生成最终视频</button>
          </div>
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
                  selected={!!(sel.img[s.id] || sel.vid[s.id])}
                  onSelectSeg={() => {
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
              <div className="muted small">共 {segs.length} 镜</div>
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

function SegmentPanel({ seg, busyWhich, runNote, selected, onSelectSeg, onEdit, onDelete, onGen, onHistory }: {
  seg: Segment;
  busyWhich: string | null;
  runNote: string;
  selected: boolean;
  onSelectSeg: () => void;
  onEdit: () => void;
  onDelete: () => void;
  onGen: (which: Which) => void;
  onHistory: (rect: DOMRect, el: HTMLElement, which: Which) => void;
}) {
  const scenes = ((seg as unknown as { scenes?: Scene[] }).scenes) || [];
  const totalShots = scenes.reduce((a, c) => a + ((c.shots || []).length), 0);
  return (
    <div className="sb-panel" data-seg={seg.id}>
      <div className="sb-panel-head">
        <div className="row gap12">
          <span className="sb-shot-no">镜头 {seg.order}</span>
          <span className="muted small">
            {totalShots} 个镜次 · {(Math.round((seg.duration_ms || 0) / 100) / 10).toFixed(0)}s
          </span>
        </div>
        <div className="sb-head-actions">
          <button type="button" className={'sb-selchip' + (selected ? ' is-active' : '')}
            title="选择该镜头" onClick={onSelectSeg}>{selected ? '已选' : '选择'}</button>
          <button type="button" className="icon-btn" title="编辑" onClick={onEdit}>{Icon.edit(18)}</button>
          <button type="button" className="icon-btn" title="删除" onClick={onDelete}>{Icon.trash(18)}</button>
        </div>
      </div>

      <div className="row gap16 mt16" style={{ alignItems: 'flex-start' }}>
        <div className="flex1">
          <div className="sb-section-title">根据以下分镜生成视频</div>
          <div className="sb-hint">片段时长限制 4-15s，输入 @ 可引用角色、场景</div>
          {scenes.length ? (
            <div className="sb-scene-chip">
              {Icon.image(14)} 本镜头场景设定：{scenes[0].title || '未指定'}
            </div>
          ) : null}
          <div className="sb-shots">
            {scenes.map((sc, si) => (sc.shots || []).map((sh, hi) => (
              <div className="sb-shot" key={si + '-' + hi}>
                <div className="st">
                  {sh.title} <span className="muted small">{sh.duration_sec}s</span>
                </div>
                <div className="sd" dangerouslySetInnerHTML={{ __html: richHtml(stripFoot(sh.rich)) }} />
              </div>
            )))}
          </div>
          <div className="sb-foot">
            视频全程不要字幕、不要屏幕文字；必须保留协调统一的全局BGM和必要环境音，禁止静音段。
          </div>
        </div>
        <div className="sb-side">
          {(['keyframe', 'video'] as Which[]).map((w) => (
            <GenCard
              key={w} seg={seg} which={w}
              busy={busyWhich === w} runNote={runNote}
              onGen={() => onGen(w)}
              onHistory={onHistory}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function GenCard({ seg, which, busy, runNote, onGen, onHistory }: {
  seg: Segment; which: Which; busy: boolean; runNote: string;
  onGen: () => void;
  onHistory: (rect: DOMRect, el: HTMLElement, which: Which) => void;
}) {
  const isKF = which === 'keyframe';
  const media = (isKF ? seg.keyframe : seg.video) || '';
  const hint = isKF ? '关键帧封面画面内容，可选择剧作参考，或跳过生成' : '视频片段，由关键帧驱动生成';
  return (
    <div className="sb-gen-card">
      <div className="sb-gen-head">
        <span className="lbl"><span className="ic">{isKF ? '▣' : '▶'}</span>{isKF ? '关键帧封面' : '视频'}</span>
        <div className="row gap8">
          <button type="button" className="icon-btn" title="选择本地文件"
            onClick={() => Store.toast('导入本地素材尚未移植（v5 的素材由媒体链产出）')}>
            {Icon.plus(18)}
          </button>
          <button type="button" className="btn btn--primary btn--xs" disabled={busy} onClick={onGen}>
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
                <br /><span className="small">点击右上角「生成」</span>
              </div>
            )
            : (isKF || !/\.mp4$/i.test(media)
              ? <img src={media} alt="" />
              : <video src={media} controls preload="metadata" playsInline />))}
      </div>
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

function EditSegmentModal({ seg, onClose, onSave }: {
  seg: Segment; onClose: () => void; onSave: (patch: Record<string, unknown>) => void;
}) {
  const [title, setTitle] = useState(seg.title || '');
  const [dur, setDur] = useState(String(seg.duration_ms || 4000));
  const [summary, setSummary] = useState(String((seg as unknown as { summary?: string }).summary || ''));
  const [prompt, setPrompt] = useState(seg.video_prompt || '');
  return (
    <Modal
      title="编辑镜头" width={560} confirmText="保存"
      onClose={onClose}
      onConfirm={() => onSave({
        title: title.trim() || seg.title,
        // 4-12s 的单镜钳制（后端也有，这里按旧版同样钳 4000-15000ms）
        duration_ms: Math.max(4000, Math.min(15000, Number(dur) || seg.duration_ms || 4000)),
        summary,
        video_prompt: prompt,
      })}
      body={
        <>
          <div className="field"><label>镜头标题</label>
            <input value={title} onChange={(e) => setTitle(e.target.value)} /></div>
          <div className="field"><label>时长（毫秒，4000-15000）</label>
            <input type="number" min={4000} max={15000} value={dur}
              onChange={(e) => setDur(e.target.value)} /></div>
          <div className="field"><label>镜头摘要</label>
            <textarea value={summary} onChange={(e) => setSummary(e.target.value)} /></div>
          <div className="field"><label>视频提示词</label>
            <textarea style={{ minHeight: 160 }} value={prompt}
              onChange={(e) => setPrompt(e.target.value)} /></div>
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
