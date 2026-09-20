/* ==========================================================================
   src/pages/Placeholders.tsx —— 一级导航的其余页面
   说明：复刻范围聚焦「短剧工作台」，其余入口保留与线上一致的信息架构，
        能离线实现的就实现，不能的给出明确说明而非伪造数据。
   ========================================================================== */

import { Router } from '../router';
import { Store, useStore } from '../store';
import { Icon } from '../components/Icons';

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

/** 画布 —— 线上为无限画布编辑器，未纳入复刻范围 */
export function Canvas() {
  return (
    <Page title="画布">
      <div className="empty mt32">
        {Icon.empty(48)}
        <div>线上「画布」是独立的无限画布编辑器（/canvas）</div>
        <div className="small">本次复刻范围聚焦短剧工作台，该模块未纳入。</div>
      </div>
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
