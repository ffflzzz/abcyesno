/* ==========================================================================
   views/wizard.js —— 短剧工作台三步向导
   对应线上 /playlet/review/:pid
     1. 剧本大纲   2. 资产库   3. 分集视频
   结构、文案与交互对齐线上实测（见 _recon/ 下的 DOM 与截图）
   ========================================================================== */
(function (global) {
  'use strict';
  const D = global.D, I = global.Icons, S = global.Store, Gen = global.Gen, Api = global.Api, Ov = global.Overlay;

  const STEPS = [
    { key: 'script', no: 1, label: '剧本大纲' },
    { key: 'assets', no: 2, label: '资产库' },
    { key: 'episodes', no: 3, label: '分集视频' },
  ];

  let ui = { assetTab: 'character', collapsed: {}, busy: null, progress: 0, outlineConfirmed: false, analyzing: false, genScript: false,
    // 正在编辑剧本的分集 key（`pid:epid`）；null = 全部只读展示。
    // 只应有一个分集处于编辑态 —— 多开 textarea 会让"完成"按钮指代不明。
    scriptEdit: null };
  let ctx = { pid: null, step: 'script' };

  /* ---------------- 外壳 ---------------- */
  function view(params, query) {
    const p = S.getProject(params.pid);
    if (!p) return notFoundProject();
    ctx.pid = p.id;
    ctx.step = (query && query.step) || 'script';

    const idx = STEPS.findIndex(function (s) { return s.key === ctx.step; });
    const steps = STEPS.map(function (s, i) {
      return { key: s.key, no: s.no, label: s.label, state: i < idx ? 'done' : (i === idx ? 'active' : 'todo') };
    });

    return global.Shell.topbarWorkbench({ title: p.name, steps: steps, scope: 'wizard' }) +
      '<section class="main" style="margin-left:0">' +
      '<div class="content">' +
      '<h1 class="page-title">' + D.esc(p.name) + '</h1>' +
      metaRow(p) +
      (ctx.step === 'script' ? stepScript(p) : ctx.step === 'assets' ? stepAssets(p) : stepEpisodes(p)) +
      '</div></section>';
  }

  function notFoundProject() {
    return global.Shell.topbarWorkbench({ title: '项目不存在', steps: [] }) +
      '<section class="main" style="margin-left:0"><div class="content">' +
      '<div class="empty">' + I.empty(48) + '<div>项目不存在或已被删除</div>' +
      '<button type="button" class="btn btn--sm mt16" data-nav="list">返回短剧列表</button></div>' +
      '</div></section>';
  }

  function metaRow(p) {
    return '<div class="meta-row">' +
      '<span class="meta-chip">' + I.film(16) + ' 视频风格：' + D.esc(p.style && p.style.name || '未设置') + '</span>' +
      '<span class="meta-chip">' + I.canvas(16) + ' 视频比例：' + D.esc(p.ratio || '9:16') + '</span>' +
      '</div>';
  }

  function stickyBar(tipText, btnText, action) {
    return '<div class="sticky-bar">' +
      '<span class="tip">' + I.check(20) + ' ' + D.esc(tipText) + '</span>' +
      '<button type="button" class="btn btn--primary btn--sm" data-action="' + action + '">' + D.esc(btnText) + '</button>' +
      '</div>';
  }

  /* ---------------- 第 1 步：剧本大纲 ----------------
     结构对齐线上（依据 `_recon/_flows/W1_ep_caret`、`W1_script_menu` 的真实截图）：
       大标题 → 风格/比例 chips → 「简介」卡片 → 「剧本内容」分集列表 → 底部 sticky bar

     ★ 三条直接针对"可读性"的决定：
       ① **空字段不渲染**。线上就是这样：`借脸` 没有世界观设定就不显示那一节，
          `橘子汽水` 没有"目标受众"也不显示。满屏 `—` 只会把真内容淹没。
       ② 「剧情概要」在没有独立概要、只有 must_have 时**分条列出**，
          而不是用 `" / "` 拼成一整段 —— 2026-09-18 用户原话「可读性很差」，
          指的正是这一坨（实测 7 条 must_have 被糊成 6 行密文）。
       ③ 剧本正文渲染成**结构化只读文档**（`core/scriptdoc.js`），
          不再把 markdown 源码（`> 元信息`、`**场景：**`、`#### 镜头`）丢进 textarea 给人看。

     ⚠️ **与线上唯一的有意差异**：线上剧本不可编辑（「剧本操作」菜单里只有「下载剧本」），
        我们保留了编辑能力 —— 收在分集行右侧的「编辑剧本 / 完成」里，默认不占视觉。
  */
  function stepScript(p) {
    const o = p.outline || {};
    // ★ 2026-09-19：`split-2` = **书页式左右对照**（左侧「简介」/ 右侧「剧本内容」）。
    //   用户要求："像书的左右页这样，比较好对照"。
    //   两块**等高 + 各自内部滚动**（样式见 app.css 的 `.split-2`）；
    //   窄屏（<1080px）自动回到上下排列 —— 两栏塞不下剧本正文。
    return '<div class="section-gap split-2">' +
      infoCard(p, o) +
      // ★ 2026-09-19：这块原来是**裸 `<div>`**（没有底色、没有边）⇒ 整片区域
      //   只有文字、与页面糊在一起（用户反馈："整页都是文字，没有分块"）。
      //   现在与「简介」对称，也是一张卡（`.doc-section`，样式见 app.css）。
      '<div class="doc-section">' +
      '<div class="script-head"><h2>剧本内容</h2>' +
      '<button type="button" class="icon-btn" data-action="episode-menu-1" title="剧本操作">' + I.ellipsis(20) + '</button>' +
      '</div>' +
      p.episodes.map(function (ep) { return episodeBlock(p, ep); }).join('') +
      '</div>' +
      '</div>' +
      (ui.outlineConfirmed
        ? stickyBar('概要已确认，开始分析资产', '生成', 'run-analysis')
        // ★ 2026-09-19 两段式：**正文还没生成**时，这一步的语义是"确认简介 → 生成剧本正文"
        //   （用户要求：没内容就别让人进工作台看到空白页）。正文已存在（粘贴剧本建的项目）
        //   则只是确认，文案保持原样。
        : (anyScriptMissing(p)
            ? stickyBar('确认简介后生成剧本正文', '下一步', 'confirm-outline')
            : stickyBar('请确认脚本概要以供生成', '下一步', 'confirm-outline')));
  }

  /** 简介卡片：字段**按序、跳过空值**。 */
  function infoCard(p, o) {
    const rows = [field('剧集', String(p.episodes.length))];
    if (str(o.story_type)) rows.push(field('故事类型', o.story_type));
    if (str(o.target_audience)) rows.push(field('目标受众', o.target_audience));
    rows.push(summaryField(o));
    if (str(o.world_setting)) rows.push(field('世界观设定', o.world_setting));
    const bios = (o.character_biographies || []).filter(function (b) { return b && str(b.name); });
    if (bios.length) rows.push(bioField(bios));
    return '<div class="info-card">' +
      '<div class="info-card-head"><h2>简介</h2>' +
      '<button type="button" class="icon-btn" data-action="edit-outline" title="编辑简介">' + I.edit(18) + '</button>' +
      '</div>' + rows.join('') + '</div>';
  }

  function str(v) { return String(v == null ? '' : v).trim(); }

  /**
   * 剧情概要：**有独立概要就是一段话；只有 must_have 时才分条**。
   *
   * 怎么知道当前是哪一种：后端把 must_have 的**原样列表**放在
   * `story_summary_items`，而 `story_summary` 是它的 `" / "` join（一个来源）。
   * 两者相等 ⇒ 这段"概要"其实就是 must_have ⇒ 该分条。等号写法让判据只有一处。
   */
  function summaryField(o) {
    const items = o.story_summary_items || [];
    const text = str(o.story_summary);
    const isMustHave = items.length > 1 && text === items.join(' / ');
    if (!isMustHave) return field('剧情概要', text || str(o.one_line_story) || '—');
    return '<div class="info-field"><span class="k">剧情概要</span>' +
      '<div class="hint">本片没有独立概要，以下是 brief 的 must_have 硬要求（共 ' + items.length + ' 条）</div>' +
      '<ol class="summary-list">' + items.map(function (x) {
        return '<li>' + boldLead(x) + '</li>';
      }).join('') + '</ol></div>';
  }

  /** `第一幕(钩子)：清早……` → **粗体幕名**：正文。没有前导标签就原样返回。 */
  function boldLead(s) {
    const m = /^([^：:]{1,14})[：:]\s*(.+)$/.exec(str(s));
    return m ? '<b>' + D.esc(m[1]) + '</b>：' + D.esc(m[2]) : D.esc(str(s));
  }

  /** 角色设定：**名字一行、描述一段**（对齐线上 `橘子汽水` 的排版）。 */
  function bioField(bios) {
    return '<div class="info-field"><span class="k">角色设定</span>' +
      '<div class="bio-list">' + bios.map(function (b) {
        const name = str(b.name);
        const bio = str(b.bio);
        // 手工添加的资产 `identity` 会等于名字本身 ⇒ 再显示一遍就是重复
        return '<div class="bio"><span class="bio-name">' + D.esc(name) + '</span>' +
          (bio && bio !== name ? '<p class="bio-text">' + D.esc(bio) + '</p>' : '') + '</div>';
      }).join('') + '</div></div>';
  }

  function field(k, v) {
    const s = str(v);
    return '<div class="info-field"><span class="k">' + D.esc(k) + '</span>' +
      '<span class="v' + (s.length > 120 ? '' : ' small') + '">' + D.esc(s) + '</span></div>';
  }

  function episodeBlock(p, ep) {
    const key = p.id + ':' + ep.id;
    const collapsed = ui.collapsed[key];
    const editing = ui.scriptEdit === key;
    return '<div class="episode-block">' +
      '<div class="episode-row">' +
      '<button type="button" class="episode-caret' + (collapsed ? ' collapsed' : '') + '" data-action="toggle-ep" data-ep="' + ep.id + '" aria-label="收起分集">' +
      I.chevronDown(20) + '</button>' +
      '<span class="episode-title">第' + ep.no + '集</span>' +
      '<input class="episode-name-input" value="' + D.esc(ep.title) + '" data-action="rename-ep" data-ep="' + ep.id + '" style="width:' +
      Math.max(3, ep.title.length + 1) + 'ch">' +
      '<span class="badge ' + (ep.script_status === 'completed' ? 'badge--done' : 'badge--warn') + '">' +
      (ep.script_status === 'completed' ? '已完成' : '待完善') + '</span>' +
      '<button type="button" class="ep-edit" data-action="toggle-script-edit" data-ep="' + ep.id + '">' +
      (editing ? '完成' : '编辑剧本') + '</button>' +
      '</div>' +
      (collapsed ? '' : (editing ? scriptEditArea(ep) : scriptDoc(ep))) +
      '</div>';
  }

  /** 本片**有没有分集还没正文**（决定底部按钮的语义，见 `stepScript`）。 */
  function anyScriptMissing(p) {
    return (p.episodes || []).some(function (ep) { return !str(ep.script).trim(); });
  }

  /**
   * **第一个还没有正文的分集** —— 「生成剧本正文」的目标。
   *
   * ★ 2026-09-19 真浏览器实测抓到的 bug：最初这里用的是 `ctx.eid`，而
   *   **剧本大纲页的路由是 `/playlet/review/:pid`，根本没有 `:eid`**
   *   ⇒ `ctx.eid` 是 `null` ⇒ 请求打成 `script/generate` 且 `ep=null`（后端查不到集）。
   *   ⇒ 改成"第一个缺正文的分集"：多集项目连点几次就能一集一集往下推，
   *     也不会反复重生成同一集（那会变成点击不前进）。
   */
  function firstMissingScriptEp(p) {
    return (p.episodes || []).filter(function (ep) {
      return !str(ep.script).trim();
    })[0] || null;
  }

  /** 只读富文本剧本。空正文要**说清下一步**，不要留一片空白。 */
  function scriptDoc(ep) {
    const src = str(ep.script);
    if (!src) {
      // ★ 2026-09-19：空态要**说清下一步**（旧文案只说"还没有"，用户不知道怎么办）
      return '<div class="sd sd--empty">' +
        '<div class="sd-empty-t">本集还没有剧本正文</div>' +
        '<div>点下方「<b>下一步</b>」：先确认左边的简介，然后由创作链写剧本正文' +
        '（只跑到 scriptwriter —— 资产卡 / 分镜等正文确认之后再跑）。</div>' +
        '<div class="small">也可以点右上「编辑剧本」自己写。</div>' +
        '</div>';
    }
    return '<div class="sd">' + global.ScriptDoc.render(src).html + '</div>';
  }

  /** 编辑态：保留原来的 textarea（存盘路径不变，仍是 `data-action="edit-script"`）。
   *
   * ⚠️ **正文用原样字符串，不能过 `str()`**（实测：`str()` 的 `trim()` 会吃掉结尾换行，
   *   于是编辑框比磁盘少 1 个字符 —— 用户只要在框里动一下再点「完成」，
   *   回写就把文件改短了）。`str()` 只用于"是否为空"的判断与只读展示。
   */
  function scriptEditArea(ep) {
    const raw = ep.script == null ? '' : String(ep.script);
    return '<textarea class="script-textarea" data-action="edit-script" data-ep="' + ep.id + '" ' +
      'placeholder="在此填写本集剧本正文…">' + D.esc(raw) + '</textarea>' +
      '<div class="hint">编辑中。可用 markdown 式标记（`# 小节`、`&gt; 元信息`、`**场景：** …`），' +
      '点「完成」后按上面的版式显示。</div>';
  }

  /* ---------------- 第 2 步：资产库 ---------------- */
  const ASSET_TABS = [
    { key: 'character', label: '角色列表' },
    { key: 'scene', label: '场景列表' },
    { key: 'prop', label: '道具列表' },
  ];

  function stepAssets(p) {
    const bucket = p.assets[ui.assetTab === 'character' ? 'characters' : ui.assetTab === 'scene' ? 'scenes' : 'props'] || [];
    return '<div class="section-gap">' +
      '<div>' +
      '<div class="asset-toolbar">' +
      '<div class="asset-tabs">' +
      ASSET_TABS.map(function (t) {
        const list = p.assets[t.key === 'character' ? 'characters' : t.key === 'scene' ? 'scenes' : 'props'] || [];
        const pending = list.filter(function (a) { return !a.states.length; }).length;
        return '<button type="button" class="asset-tab' + (ui.assetTab === t.key ? ' is-active' : '') + '" data-asset-tab="' + t.key + '">' +
          D.esc(t.label) + (pending ? '<span class="dot" title="' + pending + ' 项未生成"></span>' : '') + '</button>';
      }).join('') +
      '</div>' +
      '<div class="asset-tools">' +
      '<button type="button" class="tool-link" data-action="sync-assets">' + I.sync(16) + ' 同步资产</button>' +
      '<span class="tool-link">自动同步</span>' +
      '<span class="switch' + (p.auto_sync_assets ? ' on' : '') + '" data-action="toggle-sync" role="switch" aria-checked="' + (!!p.auto_sync_assets) + '"></span>' +
      '</div>' +
      '</div>' +
      '<div class="asset-banner">' +
      '<div><div class="t">新增' + (ui.assetTab === 'character' ? '角色' : ui.assetTab === 'scene' ? '场景' : '道具') + '</div>' +
      '<div class="s">创建空白' + (ui.assetTab === 'character' ? '角色' : ui.assetTab === 'scene' ? '场景' : '道具') + '或从素材添加</div></div>' +
      '<div class="row gap8">' +
      ((p.assets.characters.concat(p.assets.scenes, p.assets.props).some(function (a) { return !a.states.length; }))
        ? '<button type="button" class="btn btn--sm" data-action="gen-all-images">生成全部图片</button>' : '') +
      '<button type="button" class="btn btn--primary btn--sm" data-action="add-asset">' + I.plus(16) + ' 添加</button>' +
      '</div>' +
      '</div>' +
      // ⛔ **`.asset-grid` 必须是 banner 的兄弟，不能嵌在里面**（2026-09-17 实测）：
      //   `.asset-banner` 是 `display:flex; justify-content:space-between`，
      //   本来只给"文字块 + 按钮组"两个子项用；网格嵌进去就成了 flex 的第 3 项，
      //   被挤成右侧一条窄列 → 左边那半块**空白**（用户截图就是这个）。
      '<div class="asset-grid">' +
      (bucket.length ? bucket.map(function (a) { return assetCard(p, a); }).join('') : emptyAsset()) +
      '</div>' +
      '</div>' +
      stickyBar('本页资产将应用于整个项目', '下一步', 'to-episodes');
  }

  function emptyAsset() {
    return '<div class="empty">' + I.empty(48) + '<div>暂无资产，点击右上角「添加」创建</div></div>';
  }

  function assetCard(p, a) {
    const kind = ui.assetTab;
    const def = a.states.find(function (s) { return s.is_default; }) || a.states[0];
    // ★★ 场景**按设计不生参考图**（`cast.ensure`：location 只登记、不生图）——
    //    原因是场景图自带固定机位，绑进分镜会**覆盖分镜的景别/机位**。
    //    ⇒ 旧实现却给它渲染「未生成 + 再次生成」，用户点了**永远没用**、
    //      列表看起来永远空白（用户 2026-09-19 实测反馈：「场景列表还是空白的」）。
    //    这里改成**如实说明**，并把那个死控件拿掉（本项目纪律：不摆点了没反应的控件）。
    const noImg = !(def && def.image);
    const sceneNoRef = (kind === 'scene') && noImg;
    return '<div class="asset-card">' +
      '<div class="asset-thumb">' +
      (def && def.image ? '<img src="' + D.esc(def.image) + '" alt="">' :
        (sceneNoRef
          ? '<span class="placeholder placeholder--scene">场景不生图<br><span class="small">固定机位会覆盖分镜</span></span>'
          : '<span class="placeholder">未生成</span>')) +
      (def ? '<span class="tag">' + (a.states.length > 1 ? a.states.length + ' 形象' : '基础形象') + '</span>' : '') +
      (sceneNoRef ? '' :
        '<button type="button" class="asset-refresh" data-action="regen-asset" data-id="' + a.id + '">' + I.refresh(14) + ' 再次生成</button>') +
      '</div>' +
      '<div class="asset-info">' +
      '<div class="name-row"><span class="name">' + D.esc(a.name) + '</span>' +
      '<button type="button" class="icon-btn" data-action="asset-menu" data-id="' + a.id + '">' + I.ellipsis(18) + '</button>' +
      '</div>' +
      '<div class="states">' +
      // ⚠️ 原文案「已添加形象 N/1」**误导**（2026-09-17 用户实测反馈）：
      //   它数的是**形象槽位**（v5 每个资产恒为 1 个「基础形象」），不是"有几张图"。
      //   于是"未生成"的资产也写着「已添加形象 1/1」，用户以为图已经有了。
      //   改成如实说：槽位数 + **是否已有图**。
      (sceneNoRef
        // 场景：不摆空的 state-thumb（那就是用户看到的"一片空白"），改一句实话。
        ? '<span class="label">场景不进参考图 · 分镜里按文字锚点注入</span>'
        : (function () {
            const withImg = a.states.filter(function (s) { return s.image; }).length;
            return '<span class="label">形象 ' + a.states.length + '/1 · ' +
              (withImg ? '已生成 ' + withImg : '待生成') + '</span>';
          })() +
          a.states.map(function (s) {
            return '<span class="state-thumb" data-asset="' + a.id + '" data-ref="' + D.esc(s.ref_id) + '" title="' + D.esc(s.display_name) + '">' +
              (s.image ? '<img src="' + D.esc(s.image) + '" alt="">' : '') + '</span>';
          }).join('') +
          '<button type="button" class="add-state" data-action="add-state" data-id="' + a.id + '" title="添加形象">' + I.plus(16) + '</button>') +
      '</div>' +
      '</div>' +
      '</div>';
  }

  /* ---------------- 第 3 步：分集视频 ---------------- */
  function stepEpisodes(p) {
    return '<div class="section-gap">' +
      '<div>' +
      '<div class="ep-toolbar">' +
      '<div class="left">' + p.episodes.length + '集</div>' +
      '<div class="right">' +
      '<button type="button" class="btn btn--sm" data-action="add-episode">' + I.plus(16) + ' 新剧集</button>' +
      '<button type="button" class="btn btn--primary btn--sm" data-action="batch-select">批量选择</button>' +
      '</div></div>' +
      '<div class="ep-list">' +
      p.episodes.map(function (ep) { return episodeCard(p, ep); }).join('') +
      '</div>' +
      '</div>' +
      '</div>' +
      stickyBar('资产已同步，可进入分镜编辑', '完成', 'finish');
  }

  function episodeCard(p, ep) {
    const st = S.episodeStats(ep);
    const segs = (ep.storyboard && ep.storyboard.segments) || [];
    const poster = (segs.find(function (s) { return s.keyframe; }) || {}).keyframe || '';
    const assets = p.assets;
    const hasSb = !!(ep.storyboard && ep.storyboard.segments && ep.storyboard.segments.length);
    return '<article class="ep-card" data-episode="' + ep.id + '">' +
      '<span class="ep-index">' + ep.no + '</span>' +
      '<div class="ep-poster">' +
      (poster ? '<img src="' + D.esc(poster) + '" alt="" loading="lazy">' : '') +
      '<span class="dur">' + D.fmtDuration(st.durationMs) + '</span>' +
      '<span class="play">' + I.play(28) + '</span>' +
      '</div>' +
      '<div class="ep-body">' +
      '<div class="name">' + D.esc(ep.title) + '</div>' +
      '<div class="stats">' +
      assets.characters.length + '个角色<span class="muted">·</span>' +
      assets.scenes.length + '个场景<span class="muted">·</span>' +
      st.shots + '个镜头' +
      '</div>' +
      '</div>' +
      '<div class="ep-actions">' +
      (!hasSb ? '<button type="button" class="btn btn--primary btn--sm" data-action="gen-storyboard" data-ep="' + ep.id + '">生成分镜脚本</button>' : '') +
      '<button type="button" class="btn btn--sm" data-action="export-episode" data-ep="' + ep.id + '">导出</button>' +
      '<button type="button" class="icon-btn" data-action="episode-menu" data-ep="' + ep.id + '" aria-label="剧集操作">' + I.ellipsis(20) + '</button>' +
      '</div>' +
      '</article>';
  }

  /* ---------------- 事件 ---------------- */
  function bind(root) {
    D.delegate(root, 'click', '[data-stepper="wizard"] [data-step]', function (e, t) {
      const key = t.getAttribute('data-step');
      if (t.classList.contains('is-active')) return;
      // 只在**向导页**响应。（分镜页也渲染同名步骤条，两边都绑会跑两次 —— 见 shell.js 说明）
      if (!ctx.pid) return;
      global.Router.go('/playlet/review/' + ctx.pid + '?step=' + key, true);
      global.App.render();
    });
    D.delegate(root, 'click', '[data-action="back"]', function () { global.Router.go('/playlet/list'); });

    // 第1步两段式：下一步(确认概要) → 生成(分析资产)   对应线上实测
    // ★ 2026-09-19：**正文还没生成**时，"下一步"= 确认简介 + **生成剧本正文**，
    //   全程盖全屏过场（用户要求："剧本没有生成出来之前，不允许进入工作台"）。
    D.delegate(root, 'click', '[data-action="confirm-outline"]', function () {
      const p = S.getProject(ctx.pid) || {};
      const miss = firstMissingScriptEp(p);      // 大纲页没有 :eid ⇒ 不能读 ctx.eid
      if (Api.driver === 'http' && miss) return genScriptThenConfirm(miss);
      ui.outlineConfirmed = true;
      S.toast('概要已确认', 'ok');
      global.App.render();
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });

    /**
     * 「确认简介 → 生成剧本正文」：过场动画 + 轮询，等正文落盘。
     *
     * ## 为什么必须处理"人工确认条"
     * 逐步人工确认（前端起的 dev server **自带** `SHORTDRAMA_APPROVE_EACH_ROLE=1`）
     * 会让链**每次派发角色之前**停下来等人。那一刻若过场还盖着，用户看到的是一个
     * 永远转的圈，而链在等他点确认 —— 两边互等，界面还不说（本项目最忌的"静默"）。
     * ⇒ 判据：**确认条在场 = 人在等** ⇒ 过场让位；人点完（条消失）⇒ 过场回来。
     *    用"确认条在不在场"而不另开一个轮询：那个人机等待信号**已经有唯一来源**
     *    （`ui/hitl-bar.js` + app.js 的全局轮询），再造一份必然漂移。
     */
    function genScriptThenConfirm(targetEp) {
      if (ui.genScript) { S.toast('正在生成剧本正文，请稍候'); return; }
      ui.genScript = true;
      let runId = '';
      // ★ 2026-09-19 真浏览器实测抓到的 bug：点「停止」后过场**又冒出来** ——
      //   因为 tick 看到"没有确认条 + 过场不在场"就 `show()` 重新盖上了。
      //   用户会以为没停成功。⇒ 停止之后 tick 不许再把它拉起来。
      let stopping = false;
      const t0 = Date.now();
      const subOf = function (secs) {
        return '正在写第 ' + targetEp.no + ' 集的剧本正文…'
          + (secs >= 15 ? ' 已 ' + secs + 's' : '')
          + '（每个角色开工前都会停下来等你确认）';
      };
      function stop() {
        stopping = true;                 // 见上面那条：停完不许被 tick 重新拉起
        global.GenOverlay.hide();
        if (runId && Api.cancelRun) {
          Api.cancelRun(runId).then(function () {
            S.toast('已请求停止（人工结束，不算失败）');
          }, function (e) { S.toast('停止失败：' + (e && e.message ? e.message : e)); });
        } else {
          S.toast('已停止等待（还没拿到任务号，后端可能仍在跑）');
        }
      }
      function show(secs) {
        global.GenOverlay.show({ title: '正在生成剧本内容...', sub: subOf(secs),
                                 onStop: stop });
      }
      S.toast('已提交「生成剧本正文」…');
      show(0);
      Api.runTask(
        function () { return Api.generateScript(ctx.pid, targetEp.id, {}); },
        {
          timeoutMs: 0,                       // 全手动：人可能在确认条上想很久
          onSubmitted: function (run) { runId = (run && run.run_id) || ''; },
          onTick: function () {
            const secs = Math.round((Date.now() - t0) / 1000);
            if (stopping) {                                   // 已请求停止 ⇒ 只收不起
              if (global.GenOverlay.visible()) global.GenOverlay.hide();
              return;
            }
            if (document.getElementById('hitl-bar')) {
              if (global.GenOverlay.visible()) global.GenOverlay.hide();   // 让位给人
            } else if (global.GenOverlay.visible()) {
              global.GenOverlay.update({ sub: subOf(secs) });
            } else {
              show(secs);
            }
          },
          // 刷新要**拉回项目**：`ep.script` 在项目/分集里，不在分镜详情里
          refresh: function () {
            return Api.hydrate({ path: '/playlet/review/:pid', params: { pid: ctx.pid } });
          },
        }
      ).then(function (st) {
        ui.genScript = false;
        stopping = false;
        global.GenOverlay.hide();
        if (st.status === 'ok') {
          ui.outlineConfirmed = true;         // 简介就是用户刚点确认的那一步
          S.toast('剧本正文已生成，请过目', 'ok');
          window.scrollTo({ top: 0, behavior: 'smooth' });
        } else if (st.status === 'cancelled') {
          S.toast('已停止（人工结束）—— 正文可能只完成了一部分');
        } else {
          S.toast('生成剧本正文未成功（' + st.status + '）'
                  + ((st.result && (st.result.reason || st.result.error))
                     ? '：' + (st.result.reason || st.result.error) : ''));
        }
        global.App.render();
      }).catch(function (e) {
        ui.genScript = false;
        global.GenOverlay.hide();
        global.App.render();
        S.toast('生成剧本正文失败：' + (e && e.message ? e.message : e));
      });
    }
    D.delegate(root, 'click', '[data-action="run-analysis"]', function () {
      if (ui.analyzing) return;
      ui.analyzing = true;
      S.toast('正在分析资产… 正在识别剧本中的场景元素…');
      Api.extractAssets(ctx.pid).then(function (r) {
        ui.analyzing = false;
        S.toast('已识别 ' + r.characters + ' 角色 / ' + r.scenes + ' 场景 / ' + r.props + ' 道具', 'ok');
        goStep('assets');
      }).catch(function (e) { ui.analyzing = false; S.toast('分析失败：' + e.message); });
    });
    // 资产图片批量生成（线上 [生成全部角色图片]）
    D.delegate(root, 'click', '[data-action="gen-all-images"]', function () {
      const kinds = ['character', 'scene', 'prop'];
      if (Api.driver === 'http') {
        // ★ 真打后端：生图是可续跑的长任务（分钟级）→ 提交 → 轮询 → 重拉进度
        S.toast('已提交资产图任务到后端（正在生成，可继续操作）…');
        Api.runTask(
          function () { return Api.generateAssetImages(ctx.pid, kinds); },
          { refresh: function () {
              return Api.hydrate({ path: '/playlet/review/:pid', params: { pid: ctx.pid } });
            } }
        ).then(function (st) {
          const p = S.getProject(ctx.pid);
          const a = (p && p.assets) || { characters: [], scenes: [], props: [] };
          const n = (a.characters || []).length + (a.scenes || []).length + (a.props || []).length;
          global.App.render();
          if (st.status === 'ok') S.toast('资产图片已生成（当前 ' + n + ' 个资产）', 'ok');
          else S.toast('资产图任务未成功（' + st.status + '）'
                       + ((st.result && st.result.reason) ? '：' + st.result.reason : ''));
        }).catch(function (e) { S.toast('生成失败：' + (e && e.message ? e.message : e)); });
        return;
      }
      S.toast('正在生成资产图片…');
      Api.generateAssetImages(ctx.pid, kinds).then(function (r) {
        S.toast('已生成 ' + r.created + ' 张资产图片', 'ok'); global.App.render();
      });
    });
    // 分集卡 [生成分镜脚本]
    D.delegate(root, 'click', '[data-action="gen-storyboard"]', function (e, t) {
      const eid = t.getAttribute('data-ep');
      if (Api.driver === 'http') {
        // ★ 真打后端：这是**创作链**（supervisor 7 角色），十几分钟量级 → 后台跑 + 轮询
        S.toast('已提交创作链（7 个角色）—— 可以先去别的页面');
        // ★ 开了「逐步人工确认」时，耗时单位是**人**：链路每做完一个角色就停下等人点
        //   「继续」。三件事据此而来（2026-09-18）：
        //
        //   ① `timeoutMs` 改成 6 小时（默认 60 分钟**不够** —— 人还没点就先报
        //      "前端等待超时"，而任务好端端停在那里等着）。
        //   ② 挂起时要**重拉产物**。用户就是要"看过产物再决定"；若只在 run 结束时
        //      才 refresh，挂起那一刻界面还是旧内容 ⇒ 那条确认条就只是**盲批**。
        //   ③ 挂起提示**按 stamp 去重**（每 3 秒一次 tick 会刷屏）。
        let lastStamp = '';
        let warnedNoSteps = false;
        // 只在用户**还停在这个项目**上时才强制重画（否则会把人家正在填的表单冲掉）
        const repaintIfHere = function () {
          const cur = global.Router.current || {};
          if (((cur.params || {}).pid) === ctx.pid) global.App.render();
        };
        Api.runTask(
          function () { return Api.generateStoryboard(ctx.pid, eid); },
          {
            timeoutMs: 6 * 60 * 60 * 1000,
            onTick: function (st) {
              const h = (st && st.hitl) || {};
              if (h.pending) {
                if (h.stamp === lastStamp) return;      // 同一步只提示一次
                lastStamp = h.stamp;
                const cn = global.HitlBar ? global.HitlBar.label(h.prev_role) : (h.prev_role || '—');
                S.toast('链路停在「' + cn + '」—— 请在底部条上点「继续」或「打回重做」');
                // 重拉本项目的产物，让用户**看得到**刚做完的东西
                Api.hydrate({ path: '/playlet/review/:pid', params: { pid: ctx.pid } })
                  .then(function () { return Api.refreshStoryboard(ctx.pid, eid); })
                  .then(repaintIfHere, repaintIfHere);   // 分镜还没写到时会失败，同样要重画
                return;
              }
              // 没停、也没开逐步确认 ⇒ **说出来**（否则用户以为"是不是坏了"）
              if (!warnedNoSteps && st && st.status === 'running' && h.manual_steps !== true) {
                warnedNoSteps = true;
                S.toast('提示：本次运行不会逐步停下（这个 dev server 未开启逐步确认 —— '
                        + '需重启 dev 后新起的 run 才生效）');
              }
            },
            refresh: function () { return Api.refreshStoryboard(ctx.pid, eid); }
          }
        ).then(function (st) {
          const p = S.getProject(ctx.pid);
          const ep = p && p.episodes.find(function (x) { return x.id === eid; });
          const n = ep && ep.storyboard ? (ep.storyboard.segments || []).length : 0;
          global.App.render();
          if (st.status === 'ok') S.toast('分镜脚本已生成：' + n + ' 个镜头', 'ok');
          else if (st.status === 'failed') S.toast('创作链未完成：' + ((st.result && st.result.reason) || st.note || ''));
          else S.toast('创作链未成功（' + st.status + '）'
                       + ((st.result && st.result.reason) ? '：' + st.result.reason : ''));
        }).catch(function (err) { S.toast('生成失败：' + (err && err.message ? err.message : err)); });
        return;
      }
      S.toast('正在生成分镜脚本…');
      Api.generateStoryboard(ctx.pid, eid).then(function (sb) {
        S.toast('分镜脚本已生成：' + (sb.segments || []).length + ' 个镜头', 'ok');
        global.App.render();
      }).catch(function (err) { S.toast('生成失败：' + err.message); });
    });
    // 步骤流转
    D.delegate(root, 'click', '[data-action="to-assets"]', function () { goStep('assets'); });
    D.delegate(root, 'click', '[data-action="to-episodes"]', function () { goStep('episodes'); });
    D.delegate(root, 'click', '[data-action="finish"]', function () {
      S.toast('流程已完成，可进入分镜编辑', 'ok'); goStep('episodes');
    });

    // 剧本
    D.delegate(root, 'click', '[data-action="toggle-ep"]', function (e, t) {
      const k = ctx.pid + ':' + t.getAttribute('data-ep');
      ui.collapsed[k] = !ui.collapsed[k]; global.App.render();
    });
    D.delegate(root, 'input', '[data-action="edit-script"]', D.debounce(function (e, t) {
      S.updateScript(ctx.pid, t.getAttribute('data-ep'), t.value);
    }, 400));
    D.delegate(root, 'change', '[data-action="rename-ep"]', function (e, t) {
      S.renameEpisode(ctx.pid, t.getAttribute('data-ep'), t.value.trim() || '未命名');
      global.App.render();
    });
    D.delegate(root, 'click', '[data-action="edit-outline"]', function () { editOutline(); });

    // 剧本操作 ··· 菜单：线上只有「下载剧本」（见 _flows/W1_script_menu）
    D.delegate(root, 'click', '[data-action="toggle-script-edit"]', function (e, t) {
      e.stopPropagation();
      const p = S.getProject(ctx.pid);
      if (!p) return;
      const key = p.id + ':' + t.getAttribute('data-ep');
      // 只允许一个分集处于编辑态（见 ui.scriptEdit 的说明）
      ui.scriptEdit = (ui.scriptEdit === key) ? null : key;
      global.App.render();
    });

    D.delegate(root, 'click', '[data-action="episode-menu-1"]', function (e, t) {
      const p = S.getProject(ctx.pid);
      const epId = (p.episodes[0] || {}).id;
      global.Overlay.menu(t, [
        { label: '下载剧本', right: 'TXT', action: function () { downloadScript(ctx.pid, epId); } },
      ]);
    });

    // 资产
    D.delegate(root, 'click', '[data-asset-tab]', function (e, t) {
      ui.assetTab = t.getAttribute('data-asset-tab'); global.App.render();
    });
    // 自动同步：线上是 PATCH /projects/{pid}/auto-sync-assets {"auto_sync_assets":bool}
    D.delegate(root, 'click', '[data-action="toggle-sync"]', function () {
      const p = S.getProject(ctx.pid);
      Api.patchAutoSync(ctx.pid, !p.auto_sync_assets).then(function () {
        S.toast(p.auto_sync_assets ? '已关闭自动同步' : '已开启自动同步', 'ok');
        global.App.render();
      });
    });
    // 同步资产：线上是 POST /projects/{pid}/assets/finalize {"is_manual":true}
    D.delegate(root, 'click', '[data-action="sync-assets"]', function () {
      const p = S.getProject(ctx.pid);
      if (p.auto_sync_assets) fillMissingAssets(p);
      Api.finalizeAssets(ctx.pid, true).then(function () {
        S.toast('资产同步成功', 'ok'); global.App.render();
      });
    });
    // 「＋ 添加」→ 打开**完整的「角色信息」抽屉**（新建模式）。
    // 用户要求（2026-09-17）：「新增角色的弹出生成窗口应该是这样的」——
    // 此前是个只有「名称 + 形象名称」的两字段小弹窗，与线上不一致。
    D.delegate(root, 'click', '[data-action="add-asset"]', function () {
      openAssetDrawer(null, null);
    });
    // 点击形象 → 打开「角色信息」抽屉（线上为全屏 drawer，见 _flows/W2_state_click）
    D.delegate(root, 'click', '[data-action="add-state"]', function (e, t) {
      openAssetDrawer(t.getAttribute('data-id'), null);
    });
    D.delegate(root, 'click', '.state-thumb', function (e, t) {
      const id = t.getAttribute('data-asset');
      openAssetDrawer(id, t.getAttribute('data-ref'));
    });
    D.delegate(root, 'click', '[data-action="regen-asset"]', function (e, t) {
      openAssetDrawer(t.getAttribute('data-id'), null);
    });
    // 资产 ··· 菜单：线上为「删除」（见 _flows/W2_asset_menu）
    D.delegate(root, 'click', '[data-action="asset-menu"]', function (e, t) {
      const id = t.getAttribute('data-id');
      const kind = ui.assetTab;
      global.Overlay.menu(t, [
        { label: '编辑形象', action: function () { openAssetDrawer(id, null); } },
        { label: '重命名', action: function () {
            const p = S.getProject(ctx.pid);
            const a = findAsset(p, kind, id); if (!a) return;
            global.Overlay.prompt({
              title: '重命名', value: a.name, max: 40, confirmText: '确认',
              onConfirm: function (v) { S.renameAsset(ctx.pid, kind, id, v); global.App.render(); },
            });
          } },
        { label: '删除', danger: true, action: function () {
            global.Overlay.confirm({
              title: '删除资产', text: '删除后该资产在分镜中的引用将失效，确定删除？',
              danger: true, confirmText: '删除',
              onOk: function () { Api.deleteAsset(ctx.pid, kind, id).then(function () { S.toast('已删除'); global.App.render(); }); },
            });
          } },
      ]);
    });

    // 分集
    // 新剧集：线上为模态「新剧集 + 输入剧集名称(0/20) + 取消/确认」，空名禁用确认
    // （见 _flows/W3_new_episode；契约 POST /projects/{pid}/episodes
    //   {"episode_no":N,"title":"...","storyboard_manual":true}）
    D.delegate(root, 'click', '[data-action="add-episode"]', function () {
      const p = S.getProject(ctx.pid);
      const no = p.episodes.reduce(function (a, e) { return Math.max(a, e.no); }, 0) + 1;
      global.Overlay.prompt({
        title: '新剧集', max: 20, placeholder: '输入剧集名称', confirmText: '确认',
        onConfirm: function (title) {
          Api.createEpisode(ctx.pid, no, title).then(function () {
            S.toast('已新增剧集', 'ok'); global.App.render();
          });
        },
      });
    });
    D.delegate(root, 'click', '[data-action="batch-select"]', function (e, t) {
      global.Overlay.menu(t, [
        { label: '全选', action: function () { S.toast('已全选（演示）'); } },
        { label: '批量删除', danger: true, action: function () { S.toast('批量删除（演示）'); } },
      ]);
    });
    // 导出：线上为右侧抽屉，可选格式；此处以面板呈现并产出真实文件
    D.delegate(root, 'click', '[data-action="export-episode"]', function (e, t) {
      e.stopPropagation();
      exportPanel(t.getAttribute('data-ep'));
    });
    // 剧集 ··· 菜单：线上只有「删除剧集」（见 _flows/W3_ep_menu）
    D.delegate(root, 'click', '[data-action="episode-menu"]', function (e, t) {
      e.stopPropagation();
      const eid = t.getAttribute('data-ep');
      const p = S.getProject(ctx.pid);
      const ep = p.episodes.find(function (x) { return x.id === eid; });
      global.Overlay.menu(t, [
        { label: '打开分镜', action: function () { global.Router.go('/playlet/review/' + ctx.pid + '/episode/' + eid); } },
        { label: '导出', action: function () { exportPanel(eid); } },
        { label: '删除剧集', danger: true, action: function () {
            global.Overlay.confirm({
              title: '删除剧集',
              text: '确定删除「' + (ep ? ep.title : '该剧集') + '」？分镜与生成素材将一并删除。',
              danger: true, confirmText: '删除',
              onOk: function () { Api.deleteEpisode(ctx.pid, eid).then(function () { S.toast('已删除'); global.App.render(); }); },
            });
          } },
      ]);
    });
    D.delegate(root, 'click', '.ep-card', function (e, t) {
      if (e.target.closest('[data-action]')) return;
      const epId = t.getAttribute('data-episode');
      const ep = S.getEpisode(ctx.pid, epId);
      const hasSb = !!(ep && ep.storyboard && ep.storyboard.segments && ep.storyboard.segments.length);
      if (!hasSb) { S.toast('请先生成分镜脚本'); return; }   // 线上：无分镜脚本不进编辑器
      global.Router.go('/playlet/review/' + ctx.pid + '/episode/' + epId);
    });
  }

  function goStep(step) {
    ctx.step = step;
    global.Router.go('/playlet/review/' + ctx.pid + '?step=' + step, true);
    global.App.render();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  /* ---------------- 本地生成 ---------------- */
  function generateAssetState(assetId, stateName) {
    const p = S.getProject(ctx.pid);
    const kind = ui.assetTab;
    const bucket = p.assets[kind === 'character' ? 'characters' : kind === 'scene' ? 'scenes' : 'props'];
    const a = bucket.find(function (x) { return x.id === assetId; }); if (!a) return;
    S.toast('正在生成形象…');
    Gen.simulate({ ms: 700 }).then(function () {
      const img = Gen.keyframe({
        seedKey: ctx.pid + '|' + kind + '|' + a.name + '|' + stateName,
        title: a.name + ' · ' + stateName, ratio: '1:1', prompt: a.name,
      });
      const existing = a.states.find(function (s) { return s.state_name === stateName; });
      if (existing) S.addAssetRef(ctx.pid, kind, assetId, existing.ref_id, img);
      else S.addAssetState(ctx.pid, kind, assetId, stateName, img);
      S.toast('形象已生成', 'ok');
      global.App.render();
    });
  }

  function fillMissingAssets(p) {
    const jobs = [];
    ['character', 'scene', 'prop'].forEach(function (kind) {
      const bucket = p.assets[kind === 'character' ? 'characters' : kind === 'scene' ? 'scenes' : 'props'];
      bucket.forEach(function (a) {
        if (!a.states.length) {
          const img = Gen.keyframe({ seedKey: p.id + '|' + kind + '|' + a.name + '|基础形象', title: a.name, ratio: '1:1' });
          jobs.push({ kind: kind, id: a.id, name: a.name, img: img });
        }
      });
    });
    jobs.forEach(function (j) { S.addAssetState(p.id, j.kind, j.id, '基础形象', j.img); });
  }

  function editOutline() {
    const p = S.getProject(ctx.pid);
    const o = p.outline || {};
    global.PlayletList.openModal({
      title: '编辑简介',
      body: '<div class="field"><label>故事类型</label><input id="ol-type" value="' + D.esc(o.story_type || '') + '"></div>' +
        '<div class="field"><label>目标受众</label><input id="ol-aud" value="' + D.esc(o.target_audience || '') + '"></div>' +
        '<div class="field"><label>一句话故事</label><textarea id="ol-one">' + D.esc(o.one_line_story || '') + '</textarea></div>' +
        '<div class="field"><label>剧情概要</label><textarea id="ol-sum">' + D.esc(o.story_summary || '') + '</textarea></div>' +
        '<div class="field"><label>世界观设定</label><textarea id="ol-world">' + D.esc(o.world_setting || '') + '</textarea></div>',
      onConfirm: function (root) {
        const outline = Object.assign({}, o, {
          story_type: root.querySelector('#ol-type').value,
          target_audience: root.querySelector('#ol-aud').value,
          one_line_story: root.querySelector('#ol-one').value,
          story_summary: root.querySelector('#ol-sum').value,
          world_setting: root.querySelector('#ol-world').value,
        });
        S.updateProject(ctx.pid, { outline: outline });
        S.toast('已保存', 'ok'); global.App.render(); return true;
      },
    });
  }

  global.Wizard = {
    view: view, bind: bind,
    resetUi: function () { ui.collapsed = {}; ui.assetTab = 'character'; ui.outlineConfirmed = false; ui.analyzing = false; ui.genScript = false; },
  };

/* ---------------- 辅助 ---------------- */
function findAsset(p, kind, id) {
  const bucket = p.assets[kind === 'character' ? 'characters' : kind === 'scene' ? 'scenes' : 'props'] || [];
  return bucket.find(function (x) { return x.id === id; }) || null;
}

/** 打开「角色信息」抽屉（线上为全屏 drawer） */
function openAssetDrawer(assetId, refId) {
  if (!global.AssetDrawer) { S.toast('抽屉模块未加载'); return; }
  global.AssetDrawer.open({
    pid: ctx.pid, kind: ui.assetTab, assetId: assetId, refId: refId,
    onSaved: function () { global.App.render(); },
  });
}

/** 下载剧本（线上「剧本操作 → 下载剧本」） */
function downloadScript(pid, epId) {
  const ep = S.getEpisode(pid, epId); if (!ep) return;
  const blob = new Blob([ep.script || ''], { type: 'text/plain;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = (S.getProject(pid).name || 'script') + '-第' + ep.no + '集.txt';
  a.click();
  setTimeout(function () { URL.revokeObjectURL(a.href); }, 2000);
  S.toast('剧本已下载', 'ok');
}


/** 导出面板：对齐线上「导出」抽屉（可选格式 → 产出文件） */
function exportPanel(eid) {
  const p = S.getProject(ctx.pid);
  const ep = p.episodes.find(function (x) { return x.id === eid; });
  if (!ep) return;
  const st = S.episodeStats(ep);
  const body = '<div class="ov-drawer-head" style="position:absolute;right:0;top:0;width:420px;height:auto;flex-direction:column;align-items:stretch;gap:0;padding:0">' +
    '<div class="ov-mask" style="position:fixed">' +
    '<div class="ov-modal" style="width:440px">' +
    '<div class="ov-modal-head"><h3>导出 · ' + D.esc(ep.title) + '</h3>' +
    '<button type="button" class="ov-close" data-x="1">✕</button></div>' +
    '<div class="ov-modal-body">' +
    '<div class="muted small">共 ' + st.segments + ' 镜 · ' + st.shots + ' 个镜次 · 时长 ' + D.fmtDuration(st.durationMs) + ' · 关键帧 ' + st.keyframes + '/' + st.segments + ' · 视频 ' + st.videos + '/' + st.segments + '</div>' +
    '<div class="col gap8 mt16" style="align-items:stretch">' +
    '<button type="button" class="btn btn--sm" data-x="json">导出工程 JSON</button>' +
    '<button type="button" class="btn btn--sm" data-x="edl">导出剪辑 EDL</button>' +
    '<button type="button" class="btn btn--sm" data-x="script">导出剧本文本</button>' +
    '<button type="button" class="btn btn--primary btn--sm" data-x="timeline">导出成片时间线</button>' +
    '</div>' +
    '<div class="muted small mt16">离线版不执行视频编码；时间线可直接交给 ffmpeg / 剪辑软件合成。</div>' +
    '</div></div></div></div>';
  const mask = D.el('div', { class: 'ov-mask' });
  mask.innerHTML = '<div class="ov-modal" style="width:440px">' +
    '<div class="ov-modal-head"><h3>导出 · ' + D.esc(ep.title) + '</h3>' +
    '<button type="button" class="ov-close" data-x="1">✕</button></div>' +
    '<div class="ov-modal-body">' +
    '<div class="muted small">共 ' + st.segments + ' 镜 · ' + st.shots + ' 个镜次 · 时长 ' + D.fmtDuration(st.durationMs) + '</div>' +
    '<div class="col gap8 mt16" style="align-items:stretch">' +
    '<button type="button" class="btn btn--sm" data-x="json">导出工程 JSON</button>' +
    '<button type="button" class="btn btn--sm" data-x="edl">导出剪辑 EDL</button>' +
    '<button type="button" class="btn btn--sm" data-x="script">导出剧本文本</button>' +
    '<button type="button" class="btn btn--primary btn--sm" data-x="timeline">导出成片时间线</button>' +
    '</div>' +
    '<div class="muted small mt16">离线版不执行视频编码；时间线可直接交给 ffmpeg / 剪辑软件合成。</div>' +
    '</div></div>';
  document.body.appendChild(mask);
  const close = function () { mask.remove(); };
  mask.addEventListener('click', function (e) {
    if (e.target === mask || e.target.closest('[data-x="1"]')) return close();
    const b = e.target.closest('[data-x]'); if (!b) return;
    const k = b.getAttribute('data-x');
    const segs = (ep.storyboard && ep.storyboard.segments) || [];
    if (k === 'json') return dl2(JSON.stringify(ep, null, 2), p.name + '-第' + ep.no + '集.json', close);
    if (k === 'script') return dl2(ep.script || '', p.name + '-第' + ep.no + '集.txt', close);
    if (k === 'timeline') return dl2(JSON.stringify(segs.map(function (s) {
      return { order: s.order, title: s.title, duration_ms: s.duration_ms, keyframe: s.keyframe, video: s.video };
    }), null, 2), p.name + '-第' + ep.no + '集-timeline.json', close);
    if (k === 'edl') return dl2(segs.map(function (s, i) {
      return (i + 1) + '  AX  V  C  00:00:00:00 00:00:00:00 00:00:00:00 00:00:00:00\n* FROM CLIP NAME: ' + s.title;
    }).join('\n'), p.name + '-第' + ep.no + '集.edl', close);
  });
}
function dl2(text, name, close) {
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob); a.download = name; a.click();
  setTimeout(function () { URL.revokeObjectURL(a.href); }, 2000);
  S.toast('已导出 ' + name, 'ok');
  if (close) close();
}

})(window);

