/* ==========================================================================
   src/pages/Placeholders.tsx —— 一级导航的其余页面
   说明：复刻范围聚焦「短剧工作台」，其余入口保留与线上一致的信息架构，
        能离线实现的就实现，不能的给出明确说明而非伪造数据。
   ========================================================================== */

import { useEffect, useState } from 'react';
import { Router } from '../router';
import { Api } from '../api';
import { Store, fmtDuration, useStore } from '../store';
import type { ApiError, AssetItem, Episode, Project, Segment } from '../types';
import { Icon } from '../components/Icons';
import { canvasOpenUrl } from '../lib/canvasApp';

function Page({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="content content--wide">
      <h1 className="page-title" style={{ fontSize: 22, fontWeight: 600, marginTop: 24 }}>{title}</h1>
      {children}
    </div>
  );
}

/**
 * ★ 两类失败说两句不同的话（与 `App.tsx` 的水合失败同一个口径）：
 *   「后端答复了 404」与「根本联系不上」是两件事，一律显示"加载失败"
 *   会把排查方向带偏（旧版 2026-09-17 实测踩到，判据在 `api.ts` 的 `http()` 里）。
 */
function errText(e: unknown): string {
  const err = e as ApiError;
  const msg = (err && err.message) ? err.message : String(e);
  if (err && err.isNetwork) return '后端未连通：' + msg;
  if (err && err.status) return '后端拒绝了这次请求（HTTP ' + err.status + '）：' + msg;
  return '加载失败：' + msg;
}

/** 一格的载荷：图或视频 + 说明。 */
interface MediaCell {
  kind: 'image' | 'video';
  src: string;
  label: string;
  sub?: string;
}

/**
 * 一集的读数（`Works` 的角标 title 与 `Visuals` 的集按钮 title 共用一份）。
 *
 * ★ 三个数字都是后端的**便利字段**，不是前端数出来的：
 *   `shots` = 分镜表镜数、`duration_s` = 本集**预计**秒数、`has_final` = 成片在不在盘上。
 */
function epReadout(e: Episode): string {
  return '分镜表 ' + (e.shots || 0) + ' 镜 · 预计 ' + fmtDuration((e.duration_s || 0) * 1000)
    + ' · ' + (e.has_final ? '成片已在盘上' : '还没出片');
}

/** 一集的短标：`第N集 · X镜 · mm:ss`，✓ 表示成片已在盘上。 */
function epShort(e: Episode): string {
  return '第' + e.no + '集 · ' + (e.shots || 0) + '镜 · ' + fmtDuration((e.duration_s || 0) * 1000)
    + (e.has_final ? ' ✓' : '');
}

/** 素材网格（视频/静帧共用一套壳）。 */
function MediaGrid({ items }: { items: MediaCell[] }) {
  return (
    <div className="project-grid" style={{ gridTemplateColumns: 'repeat(auto-fill,minmax(200px,1fr))' }}>
      {items.map((it, i) => (
        <div className="project-card" key={it.src + '-' + i} style={{ padding: 12 }}>
          <div className="project-cover" style={{ aspectRatio: '9/16' }}>
            {it.kind === 'video' && /\.mp4($|\?)/i.test(it.src)
              ? (
                <video src={it.src} controls muted preload="metadata"
                  style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
              )
              : <img src={it.src} alt="" loading="lazy" />}
          </div>
          <div className="mt8" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 13 }}>
            {it.label}
          </div>
          {it.sub ? <div className="muted small">{it.sub}</div> : null}
        </div>
      ))}
    </div>
  );
}

/** 灵感 —— 线上为服务端推荐流，离线不伪造内容 */
export function Inspiration() {
  return (
    <Page title="灵感">
      <div className="empty mt32">
        {Icon.empty(48)}
        <div>线上「灵感」为服务端推荐流（/api/v1/pixa/sd-feed/works）</div>
        <div className="small">离线版不伪造推荐内容。可进入短剧工作台继续创作。</div>
        <button type="button" className="btn btn--primary btn--sm mt16"
          onClick={() => Router.go('/playlet/list')}>进入短剧工作台</button>
      </div>
    </Page>
  );
}

/**
 * 画布 —— 把某一集**已跑完的产物**摆成无限画布。
 *
 * 画布是独立应用（Infinite Atelier），不在本 SPA 的路由里 ⇒ 跳转是整页导航，
 * 不能用 Router.go。地址收在 `lib/canvasApp.ts` 一处。
 * 内容全由后端按盘上事实生成（资产定妆照 → 逐镜静帧 → 片段组 → 成片），
 * 不调模型、不烧配额。
 *
 * 卡片上的"N镜"是**分镜表的镜数**（后端 `shots`），不是已生成的静帧数 ——
 * 两者可以差很多（只跑了创作链的项目有镜数、没静帧）。
 */
export function Canvas() {
  const { projects } = useStore();
  const withBoard = projects.filter((p) => (p.episodes || []).some((e) => (e.shots || 0) > 0));
  return (
    <Page title="画布">
      <div className="small mt16">
        每集的画布由后端按盘上已有产物生成：资产定妆照 → 逐镜静帧 → 片段组 → 成片。
        不调用模型、不消耗配额；点开的是独立应用，节点里可直接播放片段。
      </div>
      {withBoard.length ? (
        <div className="project-grid mt24">
          {withBoard.map((p) => (
            <div className="project-card" key={p.id}>
              <div className="project-cover">
                {p.cover ? <img src={p.cover} alt="" loading="lazy" /> : null}
              </div>
              <div className="project-meta">
                <div className="flex1">
                  <h3>{p.name}</h3>
                  <p className="project-sub">
                    {p.episodes.length}集<span className="divider" />{p.created_at}
                  </p>
                </div>
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 10 }}>
                {(p.episodes || []).map((e) => {
                  const n = e.shots || 0;
                  return (
                    <button key={e.id} type="button" className="btn btn--sm" disabled={!n}
                      title={n ? `${n} 镜${e.has_final ? ' · 已出片' : ' · 未出片'}` : '这一集还没有分镜表'}
                      onClick={() => window.open(canvasOpenUrl(p.id, e.no), '_blank', 'noopener')}>
                      第{e.no} 集{n ? ` · ${n}镜` : ''}
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="empty mt32">
          {Icon.empty(48)}
          <div>还没有可画的项目 —— 至少需要一集有分镜表</div>
          <div className="small">分镜出来后，这里会按集列出入口。</div>
          <button type="button" className="btn btn--primary btn--sm mt16"
            onClick={() => Router.go('/playlet/list')}>去短剧工作台</button>
        </div>
      )}
    </Page>
  );
}

/**
 * 作品 —— 复用项目数据展示（挂在路由 `/chat` 上）。
 *
 * ★ 封面这一项以前是**恒空**的（2026-10-02 契约审计查实）：旧实现从
 *   `p.episodes[0].storyboard.segments[].keyframe` 里挑第一张，而后端列表端点
 *   给的 `storyboard` 是**刻意给的轻量壳** —— `v5/webmap.py` 的 `_episode_row()`
 *   写死 `"segments": []`（注释原话："完整分镜走 `GET /episodes/{eid}/storyboard/detail`，
 *   列表接口不该变重"）。⇒ 空数组是合法值，**不报错**，卡片就一直没封面。
 *   现在读后端直接给的便利字段：`p.cover` = 第一张静帧的**绝对 URL**
 *   （`_cover()`；绝对化是有意的，见 `webmap.MEDIA_BASE` 上那段实测注释）。
 * ★ 镜数 / 时长 / 是否出片同样走便利字段（`ep.shots` / `ep.duration_s` / `ep.has_final`），
 *   **不在前端数 segments** —— 数出 0 也算"成功"，这正是上一个缺陷的形状。
 *   注意 `shots` 是**分镜表的镜数**，不等于已生成的静帧数（同 `Canvas` 的说明）。
 */
export function Works() {
  const { projects } = useStore();
  return (
    <Page title="作品">
      <div className="project-grid mt24">
        {projects.length ? projects.map((p) => {
          const eps = p.episodes || [];
          const shots = eps.reduce((a, e) => a + (e.shots || 0), 0);
          const secs = eps.reduce((a, e) => a + (e.duration_s || 0), 0);
          const done = eps.filter((e) => e.has_final).length;
          return (
            <div className="project-card" key={p.id} onClick={() => Router.go('/playlet/review/' + p.id)}>
              <div className="project-cover">
                {p.cover
                  ? <img src={p.cover} alt="" loading="lazy" />
                  : <span className="muted small">还没有静帧</span>}
                {done
                  ? <span className="badge badge--done" style={{ position: 'absolute', right: 8, top: 8 }}>
                      已出片 {done}/{eps.length}
                    </span>
                  : null}
              </div>
              <div className="project-meta">
                <div className="flex1">
                  <h3>{p.name}</h3>
                  <p className="project-sub">
                    {eps.length}集<span className="divider" />{p.created_at}
                  </p>
                </div>
              </div>
              <div className="row gap8 mt8" style={{ flexWrap: 'wrap' }}>
                <span className="meta-chip">共 {shots} 镜</span>
                <span className="meta-chip">预计 {fmtDuration(secs * 1000)}</span>
              </div>
              {/* 逐集角标：镜数 / 预计时长 / 成片在不在盘上（都是后端按盘上事实算的） */}
              <div className="row gap8 mt8" style={{ flexWrap: 'wrap' }}>
                {eps.map((e) => (
                  <span key={e.id} className="badge badge--done" title={epReadout(e)}>
                    {epShort(e)}
                  </span>
                ))}
              </div>
            </div>
          );
        }) : (
          <div className="empty">
            {Icon.empty(48)}
            <div>还没有作品</div>
            <div className="small">后端返回的项目列表是空的（这条页直接读 Store 里的列表数据）。</div>
            <button type="button" className="btn btn--primary btn--sm mt16"
              onClick={() => Router.go('/playlet/list')}>去短剧工作台创建</button>
          </div>
        )}
      </div>
    </Page>
  );
}

/**
 * 项目的定妆照条目 —— 走 `p.assets`（`webmap.asset_refs()`）。
 *
 * ★ 这份数据**本来就是真的**：列表端点直接给 `states[].image`（可显示的绝对 URL），
 *   所以定妆照不需要另发请求。（`states[].image` 这个**别名键**是必须的 ——
 *   后端原先只给 `thumbnail_url`/`images`，前端读 `state.image` ⇒
 *   盘上真有图也一律显示「未生成」，2026-09-17 实测事故，注释还在那函数里。）
 */
function assetCells(p: Project): MediaCell[] {
  const out: MediaCell[] = [];
  const a = p.assets || { characters: [], scenes: [], props: [] };
  [a.characters, a.scenes, a.props].forEach((bucket) => {
    (bucket || []).forEach((asset: AssetItem) => {
      (asset.states || []).forEach((stx) => {
        if (stx.image) {
          out.push({
            kind: 'image', src: stx.image,
            label: asset.name + ' · ' + (stx.state_name || '基础形象'),
          });
        }
      });
    });
  });
  return out;
}

/**
 * 资产 —— **先选项目、再选集**，按需拉真实逐镜产物。
 *
 * ★ 这一页旧实现和 `Works` 同一条缺陷：遍历 `ep.storyboard.segments`
 *   取 `keyframe`/`video` ⇒ 恒空（后端列表端点给的是轻量壳，见 `Works` 上那段 ★），
 *   于是显示「还没有生成任何素材」，而后端盘上 37 个项目的素材齐全。
 *
 * ★ 为什么改成"选中哪一集才发那一条请求"，而不是把列表接口做重 / 逐项目预拉：
 *   完整逐镜产物只在 `GET /episodes/{eid}/storyboard/detail` 里给，
 *   而它在后端要**重新装配每镜提示词**（`webmap._prompt_ctx` 是 pipeline 注入链的镜像）。
 *   对每个项目都预拉 = 几十条重请求，换来的是一屏根本看不完的图。
 *   ⇒ 一次只发**一个**请求（切集时旧请求的答复直接丢弃，不许盖到新集上）。
 *
 * ★ 定妆照不待发：`p.assets` 在列表数据里就是真数据，不另发请求，
 *   但同样**按项目分开展示**（未选项目时只给"每个项目各有多少张"的索引，
 *   不把几十个项目摊成一面墙）。
 */
export function Visuals() {
  const { projects } = useStore();
  const [pid, setPid] = useState('');
  /** 0 = 未指定（回落到"该集列表里第一集有分镜表的"）；换项目时归零。 */
  const [epNo, setEpNo] = useState(0);
  const [board, setBoard] = useState<{ loading: boolean; error: string; segs: Segment[] }>(
    { loading: false, error: '', segs: [] });

  const project = projects.find((p) => p.id === pid) || null;
  const eps = project ? (project.episodes || []) : [];
  // 选中的集：`epNo` 认不到（刚换项目 / 列表刚更新）时按"有分镜表的优先"回落。
  const ep = eps.find((e) => e.no === epNo)
    || eps.find((e) => (e.shots || 0) > 0)
    || eps[0] || null;
  const eid = ep ? ep.id : '';

  useEffect(() => {
    if (!pid || !eid || Api.driver !== 'http') {
      setBoard({ loading: false, error: '', segs: [] });
      return;
    }
    let cancelled = false;
    setBoard({ loading: true, error: '', segs: [] });
    Api.getStoryboard(eid).then(
      (sb) => {
        if (cancelled) return;                 // ★ 切集后旧答复不许写进来（串集）
        setBoard({ loading: false, error: '', segs: (sb && sb.segments) || [] });
      },
      (e: unknown) => {
        if (cancelled) return;
        setBoard({ loading: false, error: errText(e), segs: [] });
      },
    );
    return () => { cancelled = true; };
  }, [pid, eid]);

  const segs = board.segs;
  const cells: MediaCell[] = [];
  segs.forEach((s) => {
    const name = (s.v5 && s.v5.shot_name) || ('#' + s.order);
    const sub = [s.scene, (s.duration_ms || 0) ? fmtDuration(s.duration_ms) : '', s.status]
      .filter(Boolean).join(' · ');
    if (s.keyframe) cells.push({ kind: 'image', src: s.keyframe, label: '静帧 ' + name, sub });
    if (s.video) cells.push({ kind: 'video', src: s.video, label: '片段 ' + name, sub });
  });
  const stills = segs.filter((s) => s.keyframe).length;
  const clips = segs.filter((s) => s.video).length;
  const assets = project ? assetCells(project) : [];

  return (
    <Page title="资产">
      {/* ── 选择器：先项目、再集 ── */}
      <div className="row gap12 mt16" style={{ flexWrap: 'wrap' }}>
        <select
          className="btn btn--sm"
          value={pid}
          title="完整分镜按集单独取（列表接口只给轻量壳），所以选中哪一集才发那一条请求"
          onChange={(ev) => { setPid(ev.target.value); setEpNo(0); }}
        >
          <option value="">— 选择项目 —</option>
          {projects.map((p) => (
            <option key={p.id} value={p.id}>{p.name}（{(p.episodes || []).length} 集）</option>
          ))}
        </select>
        {project ? (
          <div className="row gap8" style={{ flexWrap: 'wrap' }}>
            {eps.map((e) => (
              <button key={e.id} type="button"
                className={'btn btn--xs' + (ep && e.no === ep.no ? ' btn--primary' : '')}
                title={epReadout(e)}
                onClick={() => setEpNo(e.no)}>
                第{e.no}集{e.shots ? ` · ${e.shots}镜` : ''}{e.has_final ? ' · ✓已出片' : ''}
              </button>
            ))}
          </div>
        ) : null}
      </div>

      {/* ── 引导（三种空态各说一件事，别一律"没有素材"）── */}
      {!projects.length ? (
        <div className="empty mt32">
          {Icon.empty(48)}
          <div>后端还没返回任何项目</div>
          <div className="small">这一页读的是项目列表 + 单集分镜详情；先建一个项目再回来。</div>
          <button type="button" className="btn btn--primary btn--sm mt16"
            onClick={() => Router.go('/playlet/list')}>去短剧工作台</button>
        </div>
      ) : !project ? (
        <div className="empty mt32">
          {Icon.empty(48)}
          <div>先在上方选一个项目</div>
          <div className="small">
            后端有 {projects.length} 个项目。逐镜静帧/片段要按集单独取
            （列表接口只给轻量壳、不带 segments），所以这里不做"一次摊开所有项目"。
          </div>
        </div>
      ) : null}

      {/* ── 逐镜产物 ── */}
      {project ? (
        <section className="mt32">
          <h2 className="page-title" style={{ fontSize: 17, fontWeight: 600 }}>
            {project.name} · 第{ep ? ep.no : '?'}集镜头产物
          </h2>
          <div className="small muted mt8">
            共 {segs.length} 镜 · 静帧 {stills} 张 · 片段 {clips} 条
            （数字取自分镜详情接口逐镜的 keyframe / video，不是估的）
          </div>
          {board.loading ? <div className="loading">正在取这一集的逐镜产物…</div> : null}
          {board.error ? (
            <div className="small mt16" style={{ color: 'var(--danger)' }}>
              {board.error}
              {/* ★ 这里**不写**"分镜还没出也会报这条、属正常"——那是假安抚：
                  `storyboard_detail` 在没有分镜时返回的是 200 + `segments: []`
                  （空态由下面的"有分镜、没产物"分支如实显示）。
                  ⇒ 报到这里的一定是真问题（项目/集号解析不了、后端出错），别替它圆。 */}
              <div className="muted">这一集的逐镜产物取不到 —— 空分镜不会报错（它给的是空列表），所以这条一定是真问题。</div>
            </div>
          ) : null}
          {!board.loading && !board.error ? (
            cells.length
              ? <MediaGrid items={cells} />
              : (
                <div className="empty mt16">
                  {Icon.empty(48)}
                  {segs.length
                    ? <div>这一集有 {segs.length} 镜，但还没有静帧 / 片段落盘</div>
                    : <div>这一集还没有分镜表（后端返回的 segments 是空的）</div>}
                  <div className="small">
                    {segs.length
                      ? '有分镜、没产物 = 媒体链还没跑（或被门拦下）；去工作台对该集出片后即出现。'
                      : '创作链跑到 scenedesigner 才会有分镜；去工作台推进这一集。'}
                  </div>
                </div>
              )
          ) : null}
        </section>
      ) : null}

      {/* ── 资产定妆照（按项目分开；数据在列表里就是真的，不另发请求）── */}
      {project ? (
        <section className="mt32">
          <h2 className="page-title" style={{ fontSize: 17, fontWeight: 600 }}>
            {project.name} · 资产定妆照
          </h2>
          <div className="small muted mt8">共 {assets.length} 张（取自资产注册表的 states[].image，列表数据里就有）</div>
          {assets.length
            ? <MediaGrid items={assets} />
            : (
              <div className="empty mt16">
                {Icon.empty(48)}
                <div>这个项目还没有资产参考图</div>
                <div className="small">在工作台的「资产库」步骤里生成角色/场景/道具定妆照后即出现。</div>
              </div>
            )}
        </section>
      ) : projects.length ? (
        <section className="mt32">
          <h2 className="page-title" style={{ fontSize: 17, fontWeight: 600 }}>各项目资产一览</h2>
          <div className="small muted mt8">
            选中一个项目后，它的定妆照与逐镜产物会摊在下面（一次只取一集，不并发）。
          </div>
          <div className="row gap12 mt16" style={{ flexWrap: 'wrap' }}>
            {projects.map((p) => {
              const n = assetCells(p).length;
              return (
                <button key={p.id} type="button" className="btn btn--sm"
                  title={p.name}
                  onClick={() => { setPid(p.id); setEpNo(0); }}>
                  {p.name} · {n} 张定妆照
                </button>
              );
            })}
          </div>
        </section>
      ) : null}
    </Page>
  );
}

export function NotFound({ path }: { path: string }) {
  return (
    <Page title="未找到路由">
      <div className="empty mt32">
        {Icon.empty(48)}
        <div>未找到路由：{path}</div>
        <button type="button" className="btn btn--primary btn--sm mt16"
          onClick={() => Router.go('/playlet/list')}>返回短剧工作台</button>
      </div>
    </Page>
  );
}

/** 供外部按 key 取（App 的路由表用） */
export const Placeholders = {
  inspiration: Inspiration,
  canvas: Canvas,
  works: Works,
  visuals: Visuals,
  notFound: NotFound,
  allProjects: () => Store.allProjects(),
};
