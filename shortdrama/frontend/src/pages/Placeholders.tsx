/* ==========================================================================
   src/pages/Placeholders.tsx —— 一级导航的其余页面
   说明：复刻范围聚焦「短剧工作台」，其余入口保留与线上一致的信息架构，
        能离线实现的就实现，不能的给出明确说明而非伪造数据。
   ========================================================================== */

import { Router } from '../router';
import { Store, useStore } from '../store';
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

/** 作品 —— 复用项目数据展示 */
export function Works() {
  const { projects } = useStore();
  return (
    <Page title="作品">
      <div className="project-grid mt24">
        {projects.length ? projects.map((p) => {
          const segs = p.episodes?.[0]?.storyboard?.segments || [];
          const cover = segs.find((s) => s.keyframe)?.keyframe || '';
          return (
            <div className="project-card" key={p.id} onClick={() => Router.go('/playlet/review/' + p.id)}>
              <div className="project-cover">
                {cover ? <img src={cover} alt="" loading="lazy" /> : null}
              </div>
              <div className="project-meta">
                <div className="flex1">
                  <h3>{p.name}</h3>
                  <p className="project-sub">
                    {p.episodes.length}集<span className="divider" />{p.created_at}
                  </p>
                </div>
              </div>
            </div>
          );
        }) : <div className="empty">{Icon.empty(48)}<div>还没有作品</div></div>}
      </div>
    </Page>
  );
}

/** 资产 —— 聚合所有项目已生成的媒体 */
export function Visuals() {
  const { projects } = useStore();
  const items: { kind: string; src: string; label: string }[] = [];
  projects.forEach((p) => {
    (p.episodes || []).forEach((ep) => {
      (ep.storyboard?.segments || []).forEach((seg) => {
        if (seg.keyframe) items.push({ kind: 'image', src: seg.keyframe, label: p.name + ' · 镜头 ' + seg.order });
        if (seg.video) items.push({ kind: 'video', src: seg.video, label: p.name + ' · 镜头 ' + seg.order });
      });
    });
    const a = p.assets || { characters: [], scenes: [], props: [] };
    [a.characters, a.scenes, a.props].forEach((bucket) => {
      (bucket || []).forEach((asset) => {
        (asset.states || []).forEach((stx) => {
          if (stx.image) items.push({ kind: 'image', src: stx.image, label: asset.name + ' · ' + (stx.state_name || '') });
        });
      });
    });
  });
  return (
    <Page title="资产">
      <div className="row gap12 mt16">
        <span className="muted small">共 {items.length} 项（来自后端数据）</span>
      </div>
      <div className="project-grid mt16" style={{ gridTemplateColumns: 'repeat(auto-fill,minmax(200px,1fr))' }}>
        {items.length ? items.map((it, i) => (
          <div className="project-card" key={i} style={{ padding: 12 }}>
            <div className="project-cover" style={{ aspectRatio: '9/16' }}>
              {it.kind === 'video' && /\.mp4$/i.test(it.src)
                ? <video src={it.src} muted preload="metadata" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
                : <img src={it.src} alt="" loading="lazy" />}
            </div>
            <div className="muted small mt8" style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {it.label}
            </div>
          </div>
        )) : <div className="empty">{Icon.empty(48)}<div>还没有生成任何素材</div></div>}
      </div>
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
