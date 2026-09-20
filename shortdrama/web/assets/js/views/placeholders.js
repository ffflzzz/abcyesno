/* ==========================================================================
   views/placeholders.js —— 一级导航的其余页面
   说明：本次复刻范围聚焦「短剧工作台」，其余入口保留与线上一致的信息架构，
        能离线实现的就实现，不能的给出明确说明而非伪造数据。
   ========================================================================== */
(function (global) {
  'use strict';
  const D = global.D, I = global.Icons, S = global.Store;

  function page(activeKey, title, inner) {
    return global.Shell.sider(activeKey) +
      '<section class="main">' + global.Shell.topbarPlain() +
      '<div class="content content--wide">' +
      '<h1 class="page-title" style="font-size:22px;font-weight:600;margin-top:24px">' + D.esc(title) + '</h1>' +
      inner + '</div></section>';
  }

  /* 灵感 —— 线上为服务端推荐流，离线不伪造内容 */
  function inspiration() {
    return page('inspiration', '灵感', '<div class="empty mt32">' + I.empty(48) +
      '<div>线上「灵感」为服务端推荐流（/api/v1/pixa/sd-feed/works）</div>' +
      '<div class="small">离线版不伪造推荐内容。可进入短剧工作台继续创作。</div>' +
      '<button type="button" class="btn btn--primary btn--sm mt16" data-nav="playlet">进入短剧工作台</button>' +
      '</div>');
  }

  /* 画布 —— 线上为无限画布编辑器，本次未纳入复刻范围 */
  function canvas() {
    return page('canvas', '画布', '<div class="empty mt32">' + I.empty(48) +
      '<div>线上「画布」是独立的无限画布编辑器（/canvas）</div>' +
      '<div class="small">本次复刻范围聚焦短剧工作台，该模块未纳入。</div>' +
      '</div>');
  }

  /* 作品 —— 复用项目数据展示 */
  function works() {
    const list = S.allProjects();
    return page('works', '作品',
      '<div class="project-grid mt24">' + (list.length ? list.map(function (p) {
        const segs = (p.episodes[0] && p.episodes[0].storyboard && p.episodes[0].storyboard.segments) || [];
        const cover = (segs.find(function (s) { return s.keyframe; }) || {}).keyframe || '';
        return '<div class="project-card" data-open="' + p.id + '">' +
          '<div class="project-cover">' + (cover ? '<img src="' + D.esc(cover) + '" alt="">' : '') + '</div>' +
          '<div class="project-meta"><div class="flex1"><h3>' + D.esc(p.name) + '</h3>' +
          '<p class="project-sub">' + p.episodes.length + '集<span class="divider"></span>' + D.esc(p.created_at) + '</p></div></div></div>';
      }).join('') : '<div class="empty">' + I.empty(48) + '<div>还没有作品</div></div>') + '</div>');
  }

  /* 资产 —— 聚合所有项目已生成的媒体 */
  function visuals() {
    const items = [];
    S.allProjects().forEach(function (p) {
      p.episodes.forEach(function (ep) {
        ((ep.storyboard && ep.storyboard.segments) || []).forEach(function (seg) {
          if (seg.keyframe) items.push({ kind: 'image', src: seg.keyframe, label: p.name + ' · 镜头 ' + seg.order });
          if (seg.video) items.push({ kind: 'video', src: seg.video, label: p.name + ' · 镜头 ' + seg.order });
        });
      });
      ['characters', 'scenes', 'props'].forEach(function (b) {
        p.assets[b].forEach(function (a) {
          a.states.forEach(function (st) { if (st.image) items.push({ kind: 'image', src: st.image, label: a.name + ' · ' + st.state_name }); });
        });
      });
    });
    return page('assets', '资产',
      '<div class="row gap12 mt16"><span class="muted small">共 ' + items.length + ' 项（来自本地数据层）</span></div>' +
      '<div class="project-grid mt16" style="grid-template-columns:repeat(auto-fill,minmax(200px,1fr))">' +
      (items.length ? items.map(function (it) {
        return '<div class="project-card" style="padding:12px">' +
          '<div class="project-cover" style="aspect-ratio:9/16">' +
          (it.kind === 'video' && /\.mp4$/i.test(it.src)
            ? '<video src="' + D.esc(it.src) + '" muted preload="metadata" style="width:100%;height:100%;object-fit:cover"></video>'
            : '<img src="' + D.esc(it.src) + '" alt="" loading="lazy">') +
          '</div>' +
          '<div class="muted small mt8" style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + D.esc(it.label) + '</div>' +
          '</div>';
      }).join('') : '<div class="empty">' + I.empty(48) + '<div>还没有生成任何素材</div></div>') +
      '</div>');
  }

  function notFound(path) {
    return global.Shell.sider('playlet') +
      '<section class="main">' + global.Shell.topbarPlain() +
      '<div class="content"><div class="empty mt32">' + I.empty(48) +
      '<div>未找到路由：' + D.esc(path) + '</div>' +
      '<button type="button" class="btn btn--primary btn--sm mt16" data-nav="playlet">返回短剧工作台</button>' +
      '</div></div></section>';
  }

  function bind(root) {
    D.delegate(root, 'click', '[data-nav="playlet"]', function () { global.Router.go('/playlet/list'); });
    D.delegate(root, 'click', '[data-open]', function (e, t) { global.Router.go('/playlet/review/' + t.getAttribute('data-open')); });
  }

  global.Placeholders = { inspiration: inspiration, canvas: canvas, works: works, visuals: visuals, notFound: notFound, bind: bind };
})(window);
