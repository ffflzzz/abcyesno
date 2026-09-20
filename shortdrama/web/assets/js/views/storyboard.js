/* ==========================================================================
   views/storyboard.js —— 分镜编辑器
   对应线上 /playlet/review/:pid/episode/:eid
   布局：左侧镜头缩略轨 + 主区逐镜面板（分镜文本 + 关键帧/视频生成卡）
   ========================================================================== */
(function (global) {
  'use strict';
  const D = global.D, I = global.Icons, S = global.Store, Gen = global.Gen, Api = global.Api,
        Ov = global.Overlay, Vendor = global.Vendor;

  let ctx = { pid: null, eid: null, active: null };
  let busy = null;      // { sid, which } 正在生成的项
  // 批量选择：图片栏与视频栏各自独立（线上实测：两个工具栏各有自己的「全选」）
  let sel = { img: {}, vid: {} };
  // 厂商选择（2026-09-18）：状态与渲染都在 `core/vendor.js`（与资产页共用一套）。
  // 选择器摆在批量工具栏里，但**单镜生成也吃它** —— 否则同一页上
  // 单镜与批量会跑不同厂商，行为不一致。

  function view(params) {
    const p = S.getProject(params.pid);
    const ep = p && S.getEpisode(params.pid, params.eid);
    if (!p || !ep) return notFound();

    ctx.pid = p.id; ctx.eid = ep.id;
    const segs = (ep.storyboard && ep.storyboard.segments) || [];
    if (!ctx.active || !segs.some(function (s) { return s.id === ctx.active; })) {
      ctx.active = segs.length ? segs[0].id : null;
    }

    const steps = [
      { key: 'script', no: 1, label: '剧本大纲', state: 'done' },
      { key: 'assets', no: 2, label: '资产库', state: 'done' },
      { key: 'episodes', no: 3, label: '分集视频', state: 'active' },
    ];

    return global.Shell.topbarWorkbench({ title: p.name, steps: steps, scope: 'storyboard' }) +
      '<section class="main" style="margin-left:0">' +
      '<div class="content" style="max-width:1600px">' +
      actionBar(p, ep) +
      qcRow() +
      gatesBanner(ep) +
      (segs.length ? '<div class="sb-layout mt16">' + rail(segs) +
        '<div class="sb-main">' + segs.map(function (s) { return segmentPanel(p, ep, s); }).join('') + '</div>' +
        '</div>' : emptyStoryboard()) +
      '</div></section>';
  }

  function notFound() {
    return global.Shell.topbarWorkbench({ title: '剧集不存在', steps: [] }) +
      '<section class="main" style="margin-left:0"><div class="content">' +
      '<div class="empty">' + I.empty(48) + '<div>剧集不存在</div>' +
      '<button type="button" class="btn btn--sm mt16" data-action="back-list">返回列表</button></div>' +
      '</div></section>';
  }

  /**
   * 分镜契约门的判决条（C，2026-09-19）。
   *
   * 人工模式下门**只报不拦**（判断权在人），所以这份判决必须出现在页面上 ——
   * 否则「把门降级为警告」在界面上与「把门删掉」没有区别。
   *   · `fatal` 非空且 `blocked:false` → 黄条：「这些硬伤不拦你，但你得知道」
   *   · `blocked:true`（全自动模式下判的）→ 红条：「媒体链被它挡住了」
   *   · 没有报告（`{}`）→ **什么都不显示**（**绝不假报「通过」**）
   */
  function gatesBanner(ep) {
    const g = (ep.storyboard && ep.storyboard.gates) || {};
    const fatal = g.fatal || [], tips = g.tips || [];
    if (!fatal.length && !tips.length) return '';
    const blocked = !!g.blocked;
    return '<div class="gate-bar' + (blocked ? ' gate-bar--block' : '') + '">' +
      '<div class="gate-bar-head">' +
      (blocked ? '媒体链被分镜契约门挡住' : '分镜契约警告 · 不拦你（人工模式）') +
      (g.at ? '<span class="muted small"> · ' + D.esc(g.at) + '</span>' : '') +
      '</div><ul class="gate-bar-list">' +
      fatal.map(function (x) { return '<li>' + D.esc(x) + '</li>'; }).join('') +
      tips.slice(0, 4).map(function (x) {
        return '<li class="muted">' + D.esc(x) + '</li>';
      }).join('') +
      '</ul></div>';
  }

  /**
   * 「自动质检」开关那一行（2026-09-19）。
   *
   * ⚠️ **刻意不放进 `actionBar`**：那一条 `row.between` 已经很挤（两个厂商胶囊 +
   * 两个全选 + 三四个按钮），再塞 170px 会把**页面标题挤成两行**
   * （真浏览器量到 `.page-title` 高度 52px = 两行；放回单行是 26px）。
   * 单开一行、右对齐：既不挤标题，也让"这两个开关管的是生成阶段"更显眼。
   */
  function qcRow() {
    if (!global.Quality) return '';
    // 右对齐用行内样式（本前端没有 `.end` 工具类；为一次使用去加全局类不划算，
    // 而 `.between` 在这里没有第二个子元素可用）
    return '<div class="row mt8" style="justify-content:flex-end">' +
      global.Quality.chips() + '</div>';
  }

  function actionBar(p, ep) {
    const segs = (ep.storyboard && ep.storyboard.segments) || [];
    const nImg = Object.keys(sel.img).filter(function (k) { return sel.img[k]; }).length;
    const nVid = Object.keys(sel.vid).filter(function (k) { return sel.vid[k]; }).length;
    // ★ 「生成最终视频」是否可点，判据是**这一集有没有已生成的视频**，
    //   不是"选中了几镜"（2026-09-19 真实浏览器实测抓到的误导性控件）：
    //   旧写法用 `nVid`（选中数）⇒ 19 镜全部有视频、但一镜未选时按钮是**灰的**，
    //   而点了它其实出的是**整片**（后端 `compose` 不吃镜号）—— 看起来像坏了。
    //   这属于本项目最忌的"控件与它描述的事不符"。
    const nReadyVid = segs.filter(function (s) { return s.video; }).length;
    const composeTip = nReadyVid ? '按已生成的镜头出整片（不挑镜）'
                                 : '先给镜头生成视频，再出片';
    return '<div class="row between gap16" style="margin-top:8px">' +
      '<div class="row gap12">' +
      '<span class="page-title">' + D.esc(ep.title) + '</span>' +
      '<span class="muted small">第 ' + ep.no + ' 集 · ' + D.esc((ep.storyboard && ep.storyboard.ratio) || p.ratio) + '</span>' +
      '</div>' +
      '<div class="sb-batch">' +
      batchBar('img', nImg, segs.length, '批量生成图片') +
      '<span class="sb-div"></span>' +
      batchBar('vid', nVid, segs.length, '批量生成视频') +
      '<span class="sb-div"></span>' +
      '<button type="button" class="btn btn--sm" data-action="batch-download">批量下载</button>' +
      '<button type="button" class="btn btn--sm" data-action="preview">预览</button>' +
      '<button type="button" class="btn btn--primary btn--sm" data-action="compose" title="' +
      composeTip + '"' + (nReadyVid ? '' : ' disabled') + '>生成最终视频</button>' +
      '</div></div>';
  }

  /* ---------------- 厂商选择（2026-09-18） ----------------
   *
   * 线上这里是一个「模型」下拉（`sb-model ▾`）。我们原来渲染的是**写死的
   * `'Agnes Image 2.5 Flash'` 文本 + 一个纯装饰的 ▾**：点了没反应，
   * 而且**换厂商后它会说谎**（显示 Agnes，实际跑别家）。
   *
   * 现在：控件与取值逻辑在 **`core/vendor.js`**（与资产页**共用一套** ——
   * 复制两份必然漂移）。本视图只负责把它摆进批量工具栏，
   * 并在提交时把选中的厂商随请求带上。
   */

  /** 本页栏位（'img' / 'vid'）→ 能力名（'image' / 'video'）。 */
  function vendorCap(which) { return which === 'vid' ? 'video' : 'image'; }

  /** 单条批量工具栏：厂商 + 全选 + 批量按钮（未选择时 disabled，aria 与线上一致） */
  function batchBar(which, n, total, label) {
    const dis = n === 0;
    const aria = dis ? '请选择需要生成的片段' : '需要0积分';
    return '<div class="sb-tool ' + (which === 'img' ? 'batch-images' : 'batch-videos') + '">' +
      Vendor.select(vendorCap(which), { cls: 'sb-model' }) +
      '<button type="button" class="sb-selchip' + (n ? ' is-active' : '') + '" data-action="sel-all" data-which="' + which + '">' +
      (n >= total && total > 0 ? '取消全选' : '全选') +
      '<span class="muted">已选择 ' + n + '</span></button>' +
      '<button type="button" class="btn btn--sm sb-genbtn' + (dis ? ' is-disabled' : '') + '" data-action="batch-gen" data-which="' + which + '"' +
      (dis ? ' disabled aria-disabled="true" aria-label="' + aria + '"' : ' aria-label="' + aria + '"') + '>' + label + '</button>' +
      '</div>';
  }

  function emptyStoryboard() {
    return '<div class="empty mt32">' + I.empty(48) +
      '<div>本集还没有分镜</div>' +
      '<button type="button" class="btn btn--primary btn--sm mt16" data-action="add-segment">' + I.plus(16) + ' 新增镜头</button>' +
      '</div>';
  }

  function rail(segs) {
    return '<div class="sb-rail">' +
      segs.map(function (s) {
        const img = s.keyframe || '';
        return '<div class="sb-rail-item' + (s.id === ctx.active ? ' is-active' : '') + '" data-rail="' + s.id + '" title="' + D.esc(s.title) + '">' +
          (img ? '<img src="' + D.esc(img) + '" alt="">' : '') +
          '<span class="no">' + String(s.order).padStart(2, '0') + '</span>' +
          '</div>';
      }).join('') +
      '<button type="button" class="sb-rail-add" data-action="add-segment" title="新增镜头">' + I.plus(20) + '</button>' +
      '</div>';
  }

  /** 线上分镜的每个镜次末尾会附一句统一的收尾约束；原站把它单独作为灰色脚注展示，
   *  这里做同样处理：从正文里剥离，避免重复。 */
  const FOOT_RE = /视频全程不要字幕[^。]*。?/;
  function stripFoot(rich) {
    return (rich || []).map(function (t) {
      return t.type === 'text' ? { type: 'text', value: String(t.value || '').replace(FOOT_RE, '') } : t;
    }).filter(function (t) { return !(t.type === 'text' && !t.value.trim()); });
  }

  function segmentPanel(p, ep, seg) {
    const totalShots = seg.scenes.reduce(function (a, c) { return a + c.shots.length; }, 0);
    return '<div class="sb-panel" data-seg="' + seg.id + '">' +
      '<div class="sb-panel-head">' +
      '<div class="row gap12">' +
      '<span class="sb-shot-no">镜头 ' + seg.order + '</span>' +
      '<span class="muted small">' + totalShots + ' 个镜次 · ' + (seg.duration_ms / 1000).toFixed(0) + 's</span>' +
      '</div>' +
      '<div class="sb-head-actions">' +
      '<button type="button" class="sb-selchip' + ((sel.img[seg.id] || sel.vid[seg.id]) ? ' is-active' : '') + '" data-action="sel-seg" data-sid="' + seg.id + '" title="选择该镜头">' +
      ((sel.img[seg.id] || sel.vid[seg.id]) ? '已选' : '选择') + '</button>' +
      '<button type="button" class="icon-btn" data-action="edit-seg" data-sid="' + seg.id + '" title="编辑">' + I.edit(18) + '</button>' +
      '<button type="button" class="icon-btn" data-action="del-seg" data-sid="' + seg.id + '" title="删除">' + I.trash(18) + '</button>' +
      '</div>' +
      '</div>' +

      // 左右两栏：左=分镜文本，右=生成卡
      '<div class="row gap16 mt16" style="align-items:flex-start">' +
      '<div class="flex1">' +
      '<div class="sb-section-title">根据以下分镜生成视频</div>' +
      '<div class="sb-hint">片段时长限制 4-15s，输入 @ 可引用角色、场景</div>' +
      (seg.scenes.length ? '<div class="sb-scene-chip">' + I.image(14) + ' 本镜头场景设定：' + D.esc(seg.scenes[0].title || '未指定') + '</div>' : '') +
      '<div class="sb-shots">' +
      seg.scenes.map(function (sc) {
        return sc.shots.map(function (sh) {
          return '<div class="sb-shot">' +
            '<div class="st">' + D.esc(sh.title) + ' <span class="muted small">' + sh.duration_sec + 's</span></div>' +
            '<div class="sd">' + D.richHtml(stripFoot(sh.rich)) + '</div>' +
            '</div>';
        }).join('');
      }).join('') +
      '</div>' +
      '<div class="sb-foot">视频全程不要字幕、不要屏幕文字；必须保留协调统一的全局BGM和必要环境音，禁止静音段。</div>' +
      '</div>' +
      '<div class="sb-side">' + genCard(seg, 'keyframe') + genCard(seg, 'video') + '</div>' +
      '</div>' +
      '</div>';
  }

  function genCard(seg, which) {
    const isKF = which === 'keyframe';
    const media = isKF ? seg.keyframe : seg.video;
    const label = isKF ? '关键帧封面' : '视频';
    const hint = isKF ? '关键帧封面画面内容，可选择剧作参考，或跳过生成' : '视频片段，由关键帧驱动生成';
    const isBusy = isBusySeg(seg, which);

    let body;
    if (isBusy) {
      body = '<div class="sb-gen-empty"><div class="spin" style="width:26px;height:26px;border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;margin:0 auto 12px"></div>' +
        '正在生成… ' + Math.round(busy.progress * 100) + '%</div>';
    } else if (!media) {
      body = '<div class="sb-gen-empty">' + (isKF ? '尚未生成关键帧' : '尚未生成视频') + '<br><span class="small">点击右上角「生成」</span></div>';
    } else if (isKF) {
      body = '<img src="' + D.esc(media) + '" alt="">';
    } else if (/\.mp4$/i.test(media)) {
      body = '<video src="' + D.esc(media) + '" controls preload="metadata" playsinline></video>';
    } else {
      body = '<img src="' + D.esc(media) + '" alt="">';
    }

    return '<div class="sb-gen-card">' +
      '<div class="sb-gen-head">' +
      '<span class="lbl"><span class="ic">' + (isKF ? '▣' : '▶') + '</span>' + label + '</span>' +
      '<div class="row gap8">' +
      '<button type="button" class="icon-btn" data-action="add-media" data-sid="' + seg.id + '" data-which="' + which + '" title="选择本地文件">' + I.plus(18) + '</button>' +
      '<button type="button" class="btn btn--primary btn--xs" data-action="gen-media" data-sid="' + seg.id + '" data-which="' + which + '"' + (isBusy ? ' disabled' : '') + '>' +
      I.refresh(14) + ' 生成</button>' +
      '</div></div>' +
      '<div class="sb-gen-body">' + body + '</div>' +
      '<div class="sb-history"><button type="button" class="tool-link" data-action="history" data-sid="' + seg.id + '" data-which="' + which + '">历史记录' + (media ? ' · 1 项' : ' · 空') + '</button></div>' +
      '<div class="sb-hint">' + D.esc(hint) + '</div>' +
      '</div>';
  }

  /* ---------------- 事件 ---------------- */
  function bind(root) {
    D.delegate(root, 'click', '[data-action="back"]', function () { global.Router.go('/playlet/list'); });
    D.delegate(root, 'click', '[data-action="back-list"]', function () { global.Router.go('/playlet/list'); });

    D.delegate(root, 'click', '[data-stepper="storyboard"] [data-step]', function (e, t) {
      // ⛔ **必须带作用域**：向导页也渲染同一个步骤条，而所有视图的委托都绑在
      //    同一个 `#app` 上、永久生效 ⇒ 不带作用域时点一次两边都跑，
      //    本视图的 `ctx.pid` 在向导页是 `null` → `'/playlet/review/' + null`
      //    拼出 `.../null` → 后端 404（2026-09-17 实测事故）。
      if (t.classList.contains('is-active') || !ctx.pid || !ctx.eid) return;
      global.Router.go('/playlet/review/' + ctx.pid + '?step=' + t.getAttribute('data-step'));
    });

    D.delegate(root, 'click', '[data-rail]', function (e, t) {
      const sid = t.getAttribute('data-rail');
      ctx.active = sid;
      const el = root.querySelector('[data-seg="' + sid + '"]');
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
      global.App.render();
    });

    D.delegate(root, 'click', '[data-action="add-segment"]', function () {
      const seg = S.addSegment(ctx.pid, ctx.eid);
      if (seg) { ctx.active = seg.id; S.toast('已新增镜头', 'ok'); global.App.render(); }
    });
    D.delegate(root, 'click', '[data-action="del-seg"]', function (e, t) {
      const sid = t.getAttribute('data-sid');
      const seg = S.getSegment(ctx.pid, ctx.eid, sid);
      global.Overlay.confirm({
        title: '删除镜头', text: '确定删除「' + (seg ? seg.title : '该镜头') + '」？已生成的关键帧与视频将一并移除。',
        danger: true, confirmText: '删除',
        onOk: function () { Api.deleteSegment(ctx.pid, ctx.eid, sid).then(function () { S.toast('已删除'); global.App.render(); }); },
      });
    });
    D.delegate(root, 'click', '[data-action="edit-seg"]', function (e, t) { editSegment(t.getAttribute('data-sid')); });

    D.delegate(root, 'click', '[data-action="gen-media"]', function (e, t) {
      generate(t.getAttribute('data-sid'), t.getAttribute('data-which'));
    });
    // 自动质检开关（2026-09-19）：翻转一格，重画工具栏（胶囊的 is-on 态要跟着变）。
    // 选择器带 `data-qc`，只在本页命中 —— 本前端的委托全挂在同一个 `#app` 上、
    // 永久生效，选择器不唯一就会"点一次两个视图都跑"（实测事故）。
    D.delegate(root, 'click', '[data-qc]', function (e, t) {
      const k = t.getAttribute('data-qc');
      const now = global.Quality ? global.Quality.toggle(k) : false;
      S.toast((k === 'still' ? '静帧' : '成片') + '自动质检已'
              + (now ? '打开（不合格会自动重画/重拍，会烧配额）' : '关闭'));
      global.App.render();
    });
    D.delegate(root, 'click', '[data-action="add-media"]', function (e, t) {
      pickMedia(t.getAttribute('data-sid'), t.getAttribute('data-which'));
    });

    // —— 批量选择门控（线上：未选择时按钮 disabled，aria=请选择需要生成的片段） ——
    D.delegate(root, 'click', '[data-action="sel-all"]', function (e, t) {
      const which = t.getAttribute('data-which');
      const ep = S.getEpisode(ctx.pid, ctx.eid);
      const segs = (ep.storyboard && ep.storyboard.segments) || [];
      const n = Object.keys(sel[which]).filter(function (k) { return sel[which][k]; }).length;
      const on = n < segs.length;                       // 未全选 → 全选；已全选 → 清空
      sel[which] = {};
      if (on) segs.forEach(function (sg) { sel[which][sg.id] = true; });
      global.App.render();
    });
    D.delegate(root, 'click', '[data-action="sel-seg"]', function (e, t) {
      const sid = t.getAttribute('data-sid');
      sel.img[sid] = !sel.img[sid]; sel.vid[sid] = sel.img[sid];
      global.App.render();
    });
    // 厂商选择（2026-09-18）：处理体在 `core/vendor.js`（与资产页共用一套）。
    // 选择器 `select[data-vendor]` **自带唯一作用域** —— 委托绑在共享 `#app` 上
    // 且永久生效，选择器不唯一会"点一次两个视图都跑"（有实测事故）。
    Vendor.bind(root);

    D.delegate(root, 'click', '[data-action="batch-gen"]', function (e, t) {
      const which = t.getAttribute('data-which') === 'vid' ? 'video' : 'keyframe';
      const bucket = which === 'video' ? sel.vid : sel.img;
      const ids = Object.keys(bucket).filter(function (k) { return bucket[k]; });
      if (!ids.length) { S.toast('请选择需要生成的片段'); return; }   // 与线上 aria 一致
      batch(which, ids);
    });
    D.delegate(root, 'click', '[data-action="batch-download"]', function () { batchDownload(); });
    D.delegate(root, 'click', '[data-action="preview"]', function () { preview(); });
    // 历史记录气泡：展示该镜该类素材的当前选用项 + 可一键回切
    D.delegate(root, 'click', '[data-action="history"]', function (e, t) {
      const sid = t.getAttribute('data-sid'), which = t.getAttribute('data-which');
      const seg = S.getSegment(ctx.pid, ctx.eid, sid); if (!seg) return;
      const cur = which === 'keyframe' ? seg.keyframe : seg.video;
      const html = cur
        ? '<div style="display:flex;gap:8px;flex-wrap:wrap">' +
          '<div style="width:72px"><div style="width:72px;aspect-ratio:9/16;border-radius:6px;overflow:hidden;border:2px solid var(--accent)">' +
          (which === 'keyframe' || !/\.mp4$/i.test(cur)
            ? '<img src="' + D.esc(cur) + '" style="width:100%;height:100%;object-fit:cover">'
            : '<video src="' + D.esc(cur) + '" style="width:100%;height:100%;object-fit:cover" muted></video>') +
          '</div><div class="muted" style="font-size:11px;margin-top:4px">当前选用</div></div></div>'
        : '<div class="muted small">暂无历史记录</div>';
      global.Overlay.popover(t, html, { wide: true });
    });
    D.delegate(root, 'click', '[data-action="compose"]', function () { compose(); });
  }

  /* ---------------- 本地生成 ---------------- */
  function generate(sid, which) {
    const seg = S.getSegment(ctx.pid, ctx.eid, sid);
    if (!seg) return;
    if (Api.driver === 'http') return generateRemote([sid], which);
    busy = { sid: sid, which: which, progress: 0 };
    global.App.render();
    Gen.simulate({
      ms: which === 'keyframe' ? 900 : 1200,
      onProgress: function (t) { busy.progress = t; updateBusyUi(t); },
    }).then(function () {
      const p = S.getProject(ctx.pid);
      const img = Gen.keyframe({
        seedKey: seg.id + '|' + which + '|' + (seg.scenes[0] ? seg.scenes[0].title : ''),
        title: seg.title, ratio: p.ratio, index: seg.order,
        prompt: seg.video_prompt || (seg.scenes[0] && seg.scenes[0].shots[0] ? seg.scenes[0].shots[0].plain : ''),
      });
      S.setSegmentMedia(ctx.pid, ctx.eid, sid, which, img);
      seg.status = which === 'video' ? 'composed' : seg.status;
      busy = null;
      S.toast((which === 'keyframe' ? '关键帧' : '视频') + '已生成', 'ok');
      global.App.render();
    });
  }

  function updateBusyUi(t) {
    // 守卫：目标镜头面板可能不在当前 DOM（已滚出/已重渲染），找不到就静默跳过，
    // 否则会打断整个批量生成链（实测踩坑）
    const el = document.querySelector('.sb-gen-empty');
    if (!el || !busy) return;
    el.innerHTML = '<div class="spin" style="width:26px;height:26px;border:2px solid var(--border);border-top-color:var(--accent);border-radius:50%;margin:0 auto 12px"></div>正在生成… ' + Math.round(t * 100) + '%';
  }

  function pickMedia(sid, which) {
    const inp = document.createElement('input');
    inp.type = 'file';
    inp.accept = which === 'keyframe' ? 'image/*' : 'video/*,image/*';
    inp.onchange = function () {
      const f = inp.files[0]; if (!f) return;
      const rd = new FileReader();
      rd.onload = function () {
        S.setSegmentMedia(ctx.pid, ctx.eid, sid, which, String(rd.result));
        S.toast('已导入本地素材', 'ok'); global.App.render();
      };
      rd.readAsDataURL(f);
    };
    inp.click();
  }

  /** 该镜是否正在生成（单镜时看 sid；批量时看 sids 集合）。 */
  function isBusySeg(seg, which) {
    if (!busy || busy.which !== which) return false;
    if (busy.sids) return !!busy.sids[seg.id];
    return busy.sid === seg.id;
  }

  /**
   * ★ http 驱动：**真打后端**生成。
   *
   * 一次请求渲多镜（后端按 `segment_ids` 起一个 run）→ 轮询台账 → 重拉分镜 → 重画。
   *
   * 为什么必须这么做（2026-09-15 实测）：原本 `generate()`/`batch()` **完全走本地**
   * （`Gen.simulate` + canvas 画占位图 + `S.setSegmentMedia`），
   * `Api.generateMedia` 是**死代码** —— 点「生成」只会在浏览器里画一张占位图，
   * 后端一无所知。
   *
   * ⚠️ 结果**如实播报**：`blocked`（被媒体门拦）与 `incomplete`（缺镜）都不是"成功"，
   * 也不能算"失败"——它们是**有信息量的正常结果**，必须把原因显示出来。
   */
  function generateRemote(ids, which) {
    const pid = ctx.pid, eid = ctx.eid;
    const sids = {};
    ids.forEach(function (i) { sids[i] = true; });
    busy = { sid: ids[0], sids: sids, which: which };
    global.App.render();

    const label = which === 'keyframe' ? '关键帧' : '视频';
    // ★ 厂商随请求发出（2026-09-18）：`which` 是 'keyframe'/'video'，
    //   而工具栏栏位是 'img'/'vid' —— 这里做映射，**单镜与批量共用同一份选择**。
    const cap = vendorCap(which === 'video' ? 'vid' : 'img');
    const body = Object.assign({ segment_ids: ids }, Vendor.payload(cap));
    const vCode = Vendor.pick(cap);
    S.toast('已提交 ' + ids.length + ' 个' + label + '到后端'
            + (vCode ? '（厂商：' + vCode + '）' : '') + '…');

    const t0 = Date.now();
    // 统一的「提交 → 轮询 → 刷新」流程（见 core/api.js 的 runTask）
    Api.runTask(
      function () { return Api.generateMedia(pid, eid, which, body); },
      {
        onTick: function (st) {
          const el = document.querySelector('.sb-gen-empty');
          if (!el) return;
          const secs = Math.round((Date.now() - t0) / 1000);
          el.innerHTML = '<div class="spin" style="width:26px;height:26px;border:2px solid var(--border);' +
            'border-top-color:var(--accent);border-radius:50%;margin:0 auto 12px"></div>' +
            '正在生成… 已 ' + secs + 's<br><span class="small">' +
            D.esc((st && st.status) || 'running') + '</span>';
        },
        refresh: function () { return Api.refreshStoryboard(pid, eid); },
      }
    ).then(function (st) {
      busy = null;
      global.App.render();
      S.toast(runOutcomeText(st, label), st.status === 'ok' ? 'ok' : undefined);
    }).catch(function (e) {
      busy = null;
      global.App.render();
      S.toast('生成请求失败：' + (e && e.message ? e.message : e));
    });
  }

  /** 把 run 的终态翻成一句**如实**的话（不把 blocked/incomplete 说成成功）。 */
  function runOutcomeText(st, label) {
    const s = (st && st.status) || '?';
    const why = (st && st.result && (st.result.reason || st.result.error)) || (st && st.note) || '';
    if (s === 'ok') return label + '已生成';
    if (s === 'blocked') return '被媒体门拦住：' + (why || '前置条件未满足');
    if (s === 'incomplete') {
      const miss = (st.result && st.result.missing) || [];
      return '部分完成（缺 ' + miss.length + ' 镜' + (miss.length ? '：' + miss.slice(0, 4).join(',') : '') + '）';
    }
    if (s === 'cancelled') return '已取消';
    if (s === 'timeout') return '前端等待超时（任务可能仍在后端跑，稍后刷新看看）';
    if (s === 'lost') return '执行进程已消失（被回收/崩溃）—— 看后端日志';
    return label + '生成未成功（' + s + '）' + (why ? '：' + why : '');
  }

  function batch(which, onlyIds) {
    const ep = S.getEpisode(ctx.pid, ctx.eid);
    const segs = ep.storyboard.segments.slice();
    const targets = segs.filter(function (s) {
      if (onlyIds && onlyIds.indexOf(s.id) < 0) return false;
      return which === 'keyframe' ? !s.keyframe : !s.video;
    });
    if (!targets.length) {
      // ★ 别让这里变成"死胡同"（2026-09-19 实测：镜头都生成完之后再点，
      //   什么都不发生 —— 用户会以为按钮坏了）。如实说明"没有要生成的"，
      //   并**指出下一步在哪**。
      S.toast(which === 'video'
        ? '所选镜头都已有视频 → 下一步：右上角「生成最终视频」出片'
        : '所选镜头都已有图片');
      return;
    }
    if (Api.driver === 'http') return generateRemote(targets.map(function (s) { return s.id; }), which);
    S.toast('开始批量生成 ' + targets.length + ' 项…');
    let i = 0;
    (function next() {
      if (i >= targets.length) { S.toast('批量生成完成', 'ok'); global.App.render(); return; }
      const s = targets[i++];
      busy = { sid: s.id, which: which, progress: 0 };
      global.App.render();
      Gen.simulate({ ms: 420, onProgress: function (t) { busy.progress = t; updateBusyUi(t); } }).then(function () {
        const p = S.getProject(ctx.pid);
        const img = Gen.keyframe({
          seedKey: s.id + '|' + which + '|' + (s.scenes[0] ? s.scenes[0].title : ''),
          title: s.title, ratio: p.ratio, index: s.order,
          prompt: s.video_prompt || '',
        });
        S.setSegmentMedia(ctx.pid, ctx.eid, s.id, which, img);
        busy = null;
        next();
      });
    })();
  }

  function batchDownload() {
    const ep = S.getEpisode(ctx.pid, ctx.eid);
    const manifest = ep.storyboard.segments.map(function (s) {
      return { order: s.order, title: s.title, duration_ms: s.duration_ms, keyframe: s.keyframe, video: s.video };
    });
    const blob = new Blob([JSON.stringify(manifest, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = ep.title + '-素材清单.json'; a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 2000);
    S.toast('已导出素材清单（离线版不打包二进制）');
  }

  function preview() {
    const ep = S.getEpisode(ctx.pid, ctx.eid);
    const segs = ep.storyboard.segments;
    const first = segs.find(function (s) { return s.video && /\.mp4$/i.test(s.video); });
    if (!first) { S.toast('还没有生成视频，先执行批量生成'); return; }
    const mask = D.el('div', { class: 'modal-mask' });
    mask.innerHTML = '<div class="modal" style="width:420px">' +
      '<h3>' + D.esc(ep.title) + ' · 预览</h3>' +
      '<div class="modal-body"><video src="' + D.esc(first.video) + '" controls autoplay style="width:100%;border-radius:8px"></video>' +
      '<div class="muted small">共 ' + segs.length + ' 镜，当前预览第 ' + first.order + ' 镜</div></div>' +
      '<div class="modal-foot"><button type="button" class="btn btn--sm" data-m="close">关闭</button></div>' +
      '</div>';
    document.body.appendChild(mask);
    mask.addEventListener('click', function (e) {
      if (e.target === mask || (e.target.closest('[data-m]'))) mask.remove();
    });
  }

  /**
   * 「生成最终视频」= **整片出片**（D，2026-09-19）。
   *
   * 改造前它是**演示**：`Gen.simulate` + 一个弹窗写着「离线版不执行真实编码」并展示
   * 一条时间线 —— 也就是说**前端根本出不了片**，成片只能靠命令行
   * （`python -m v5.series <项目> --resume-media`）出。
   * ⇒ 那样「把判断权交回给人」只是纸面成立：人得有"点一下就出片"的能力。
   *
   * 现在（http 驱动）：`POST /episodes/{eid}/compose` → run → 轮询 → 给出**可播的成片**。
   * 后端那条路径是 `runner.KINDS["episode"]` → `pipeline.run`（**不带 only** = 整片）：
   * 静帧缺的补画、视频缺的补渲、已在盘上的按磁盘事实复用（**不重复烧配额**）。
   * `timeoutMs: 0` = 不限时 —— 整片是小时级，用默认 60 分钟会在片子还在渲时报"超时"。
   */
  function compose() {
    const ep = S.getEpisode(ctx.pid, ctx.eid);
    const segs = (ep && ep.storyboard && ep.storyboard.segments) || [];
    const ready = segs.filter(function (s) { return s.video; });
    if (!ready.length) { S.toast('请先生成镜头视频'); return; }
    // local 驱动（离线演示）：保留原样，**如实**说明不做真实编码，不给假的成功
    if (Api.driver !== 'http') return composeOfflinePreview(ep, segs, ready);
    if (busy) { S.toast('还有生成任务在跑，等它结束再出片'); return; }
    busy = { sid: null, which: 'compose' };
    S.toast('已提交整片出片（静帧→视频→拼接，可能要很久）…');
    global.App.render();

    const t0 = Date.now();
    Api.runTask(
      function () { return Api.composeEpisode(ctx.pid, ctx.eid, {}); },
      {
        timeoutMs: 0,
        onTick: function (st) {
          const el = document.querySelector('.sb-gen-empty');
          if (!el) return;
          el.innerHTML = '<div class="spin" style="width:26px;height:26px;border:2px solid var(--border);' +
            'border-top-color:var(--accent);border-radius:50%;margin:0 auto 12px"></div>' +
            '正在出片… 已 ' + Math.round((Date.now() - t0) / 60000) + ' 分<br><span class="small">' +
            D.esc((st && st.status) || 'running') + '</span>';
        },
        refresh: function () { return Api.refreshStoryboard(ctx.pid, ctx.eid); },
      }
    ).then(function (st) {
      busy = null;
      global.App.render();
      const url = (st && st.result && st.result.final_url) || '';
      if (st.status === 'ok') {
        // ★ 出片成功必须给人**看得见的东西**（一句 toast 不足以验收）
        if (url) showFinal(url, ep, st);
        else S.toast('出片完成，但没拿到成片地址 —— 请看后端日志', 'ok');
      } else {
        S.toast(runOutcomeText(st, '出片'));
      }
      showWarnings(st);
    }).catch(function (e) {
      busy = null;
      global.App.render();
      S.toast('出片请求失败：' + (e && e.message ? e.message : e));
    });
  }

  /** 出片成功后把片子直接摆出来（人验收要**看得见画面**，不是只看一句话）。 */
  function showFinal(url, ep, st) {
    const warns = ((st && st.result && st.result.warnings) || []);
    const mask = D.el('div', { class: 'modal-mask' });
    mask.innerHTML = '<div class="modal" style="width:620px">' +
      '<h3>成片 · ' + D.esc(ep.title) + '</h3><div class="modal-body">' +
      '<video src="' + D.esc(url) + '" controls playsinline ' +
      'style="width:100%;border-radius:10px;background:#000"></video>' +
      '<div class="row between gap12 mt16"><a class="btn btn--sm" href="' + D.esc(url) +
      '" target="_blank" rel="noopener">在新窗口打开 / 下载</a>' +
      '<span class="muted small">' + D.esc(url) + '</span></div>' +
      (warns.length ? '<div class="gate-bar mt16"><div class="gate-bar-head">' +
        '分镜契约警告（不拦你）</div><ul class="gate-bar-list">' +
        warns.slice(0, 6).map(function (x) { return '<li>' + D.esc(x) + '</li>'; }).join('') +
        '</ul></div>' : '') +
      '</div><div class="modal-foot"><button type="button" class="btn btn--primary btn--sm" ' +
      'data-m="close">关闭</button></div></div>';
    document.body.appendChild(mask);
    mask.addEventListener('click', function (e) {
      if (e.target === mask || e.target.closest('[data-m="close"]')) mask.remove();
    });
  }

  /** run 结果里带回的分镜契约判决 → 提示一次（C：判决必须到达人眼前）。 */
  function showWarnings(st) {
    const w = (st && st.result && st.result.warnings) || [];
    if (!w.length) return;
    try { console.warn('[分镜契约]', w); } catch (e) {}
    S.toast('分镜契约有 ' + w.length + ' 条警告（不拦你）——详情见分镜页顶部红条');
  }

  /** 离线演示版（local 驱动）：**如实**说明不做真实编码，不给假的成功。 */
  function composeOfflinePreview(ep, segs, ready) {
    S.toast('正在合成最终视频…');
    Gen.simulate({ ms: 1400 }).then(function () {
      const timeline = segs.map(function (s) {
        return { order: s.order, title: s.title, src: s.video, duration_ms: s.duration_ms };
      });
      const mask = D.el('div', { class: 'modal-mask' });
      mask.innerHTML = '<div class="modal" style="width:560px">' +
        '<h3>生成最终视频</h3><div class="modal-body">' +
        '<div class="muted small">离线版不执行真实编码，此处展示可交付的成片时间线（' + ready.length + '/' + segs.length + ' 镜已就绪，总时长 ' +
        D.fmtDuration(segs.reduce(function (a, s) { return a + s.duration_ms; }, 0)) + '）。</div>' +
        '<pre style="max-height:260px;overflow:auto;background:var(--bg-page);padding:12px;border-radius:8px;font-size:12px;line-height:1.6">' +
        D.esc(timeline.map(function (t) { return String(t.order).padStart(2, '0') + '  ' + D.fmtDuration(t.duration_ms) + '  ' + t.title; }).join('\n')) +
        '</pre>' +
        '<div class="row gap12"><button type="button" class="btn btn--sm" data-m="json">导出时间线 JSON</button>' +
        '<button type="button" class="btn btn--sm" data-m="list">导出 EDL</button></div>' +
        '</div><div class="modal-foot"><button type="button" class="btn btn--primary btn--sm" data-m="close">完成</button></div></div>';
      document.body.appendChild(mask);
      mask.addEventListener('click', function (e) {
        const b = e.target.closest('[data-m]');
        if (e.target === mask) return mask.remove();
        if (!b) return;
        const k = b.getAttribute('data-m');
        if (k === 'close') return mask.remove();
        if (k === 'json') return dl(JSON.stringify(timeline, null, 2), ep.title + '-timeline.json');
        if (k === 'list') {
          const edl = timeline.map(function (t, i) {
            return (i + 1) + '  ' + 'AX' + '  V  C  ' + '00:00:00:00 00:00:00:00 00:00:00:00 00:00:00:00\n* FROM CLIP NAME: ' + t.title + '  (' + t.src.slice(0, 48) + '...)';
          }).join('\n');
          dl(edl, ep.title + '-timeline.edl');
        }
      });
    });
  }

  function dl(text, name) {
    const blob = new Blob([text], { type: 'text/plain' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = name; a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 2000);
  }

  function editSegment(sid) {
    const seg = S.getSegment(ctx.pid, ctx.eid, sid);
    if (!seg) return;
    global.PlayletList.openModal({
      title: '编辑镜头',
      body: '<div class="field"><label>镜头标题</label><input id="sg-title" value="' + D.esc(seg.title) + '"></div>' +
        '<div class="field"><label>时长（毫秒，4000-15000）</label><input id="sg-dur" type="number" value="' + seg.duration_ms + '" min="4000" max="15000"></div>' +
        '<div class="field"><label>镜头摘要</label><textarea id="sg-sum">' + D.esc(seg.summary || '') + '</textarea></div>' +
        '<div class="field"><label>视频提示词</label><textarea id="sg-prompt" style="min-height:160px">' + D.esc(seg.video_prompt || '') + '</textarea></div>',
      onConfirm: function (root) {
        S.updateSegment(ctx.pid, ctx.eid, sid, {
          title: root.querySelector('#sg-title').value.trim() || seg.title,
          duration_ms: Math.max(4000, Math.min(15000, Number(root.querySelector('#sg-dur').value) || seg.duration_ms)),
          summary: root.querySelector('#sg-sum').value,
          video_prompt: root.querySelector('#sg-prompt').value,
        });
        S.toast('已保存', 'ok'); global.App.render(); return true;
      },
    });
  }

  global.Storyboard = { view: view, bind: bind, reset: function () { ctx.active = null; busy = null; sel = { img: {}, vid: {} }; } };
})(window);
