/* ==========================================================================
   views/playlet-list.js —— 短剧工作台首页
   对应线上 /playlet/list
   结构：AI剧本创作（上传/粘贴 · AI生成剧本）+ 我的项目 / 精选项目
   ========================================================================== */
(function (global) {
  'use strict';
  const D = global.D, I = global.Icons, S = global.Store, Api = global.Api;

  let ui = { createTab: 'upload', listTab: 'mine', menuOpen: null,
    draft: null, styleCat: '真人', styleCode: '', styleName: '',   // 三段式：已粘贴文本 → 选风格 → 剧本解析
    aiIdea: '', aiStyle: '', aiEpisodes: 1,                        // AI 生成剧本：一个输入框 + 三项设置
    /* ---------- 精选项目（2026-09-18 接通）----------
       ★ 为什么放在**视图局部**而不进 `Store`：
         它是**视图级、临时**的数据 —— 只在"精选"这个 tab 被看到时才有意义，
         关掉就作废。放进 Store 会被卷进 localStorage 的读写周期（`save()`），
         而"哪些项目被运营标成精选"是**服务端**的事，本地缓存一份错的反而有害。
       ★ `featuredState` 三态机：`idle`（还没问过）→ `loading` → `ok` / `error`。
         空态文案**必须**能区分这三态 +「本地离线」—— 见 `emptyState()`。 */
    featured: null, featuredState: 'idle', featuredError: '' };

  /* ── 过场动画（2026-09-19）：新建项目 / 解析剧本期间盖全屏 ──────────────
     为什么统一走这两个小函数：`GenOverlay` 是可选依赖（脚本顺序变了也不该崩），
     而本视图有两处调用（解析剧本 / AI 创作），各写一遍 `if (global.GenOverlay)` 必然漏一个。
     ⛔ 这里的「停止」**撤不回请求**（`createBy*` 是普通 POST，不是后台 run）——
        所以它只停止等待，并**如实说明**项目之后仍会出现。 */
  function ovShow() {
    if (!global.GenOverlay) return;
    global.GenOverlay.show({ title: '正在生成剧本大纲...', sub: '正在理解你的创作需求...',
      onStop: function () {
        global.GenOverlay.hide();
        global.Store.toast('已停止等待 —— 请求无法撤回，完成后项目仍会出现在列表里');
      } });
  }
  function ovHide() { if (global.GenOverlay) global.GenOverlay.hide(); }

  function view() {
    return global.Shell.sider('playlet') +
      '<section class="main">' +
      global.Shell.topbarPlain() +
      '<div class="content content--wide">' +
      hero() +
      projectsSection() +
      '</div></section>';
  }

  function hero() {
    const isUpload = ui.createTab === 'upload';
    return '<div class="hero">' +
      '<h1>AI剧本创作</h1>' +
      '<p>用AI创作你的下一部爆款短剧</p>' +
      '</div>' +
      '<div class="create-box">' +
      '<div class="create-tabs">' +
      '<button type="button" class="create-tab' + (isUpload ? ' is-active' : '') + '" data-create-tab="upload">' +
      I.upload(16) + ' 上传我的剧本</button>' +
      '<button type="button" class="create-tab' + (!isUpload ? ' is-active' : '') + '" data-create-tab="ai">' +
      I.inspiration(16) + ' AI生成剧本</button>' +
      '</div>' +
      '<div class="create-body">' + (ui.draft ? pastedPane() : (isUpload ? uploadPane() : aiPane())) + '</div>' +
      '</div>';
  }

  function uploadPane() {
    return '<div class="dropzone" id="dz">' +
      '<div class="dz-actions">' +
      '<button type="button" class="btn btn--primary btn--sm" data-action="upload-file">' + I.upload(16) + ' 上传我的剧本</button>' +
      '<button type="button" class="btn btn--sm" data-action="paste-script">' + I.edit(16) + ' 粘贴剧本</button>' +
      '</div>' +
      '<div class="dz-hint">支持 txt/docx 格式，剧本字数不超过10万字，可拖拽至此处上传</div>' +
      '<input type="file" id="file-input" accept=".txt,.docx,.md" class="hidden">' +
      '</div>';
  }

  /** 风格预览卡：**真实样张** + 该包会注入提示词的文案 + 样张出处。
   *
   * 为什么要有它（用户原话：「包看到了，但没有预览效果看，不直观」）：
   * 只给一个包名，用户不知道「写实电影感」和「羊毛毡故事短片」差在哪。
   * 两样东西都是**真的**：
   *   · `cover_url` —— 该类型包**真实项目**的静帧（后端从 `projects/` 里确定性挑的）
   *   · `visual_style` —— 该包**真正会注入每一镜提示词**的那段（不是宣传语）
   * 没有样张的包（如 3d-animation，一个项目都没有）**如实显示「暂无样张」**，不编。
   */
  function stylePreviewCard(code) {
    const all = S.state.styles || [];
    const s = all.find(function (x) { return x.code === code; }) || all[0] || null;
    if (!s) return '';
    const cover = s.cover_url
      ? '<img src="' + D.esc(s.cover_url) + '" alt="" loading="lazy">'
      : '<div class="empty">暂无样张<br>该包还没有项目<br>出过静帧</div>';
    let meta = '';
    if (s.cover_url) {
      // 用中文片名（`sample_topic`）而不是目录名 —— 写「样张来自《paper-crane》」不如《纸鹤》
      meta = '样张来自《' + D.esc(s.sample_topic || s.sample_project || '') + '》'
        + D.esc(s.sample_shot || '');
      if (s.sample_stills) meta += '（该包共 ' + s.sample_stills + ' 张静帧）';
    } else if (!(Api && Api.driver === 'http')) {
      meta = '离线模式：缩略图需要连上后端';
    }
    return '<div class="style-prev">' +
      '<div class="style-prev-thumb">' + cover + '</div>' +
      '<div class="style-prev-body">' +
      '<div class="style-prev-name">' + D.esc(s.name) +
      ' <span class="muted small">' + D.esc(s.code) + '</span></div>' +
      (s.visual_style ? '<div class="style-prev-desc">' + D.esc(s.visual_style) + '</div>' : '') +
      (meta ? '<div class="style-prev-meta">' + meta + '</div>' : '') +
      '</div></div>';
  }

  /** 按风格**名**反查它的包 code（预览卡要 code）。找不到返回空串。 */
  function curCodeOf(name) {
    const hit = (S.state.styles || []).find(function (s) { return s.name === name; });
    return hit ? hit.code : '';
  }

  /** 只重画风格预览卡（**不整页重渲染** —— 否则输入框会丢焦点/丢字符）。 */
  function syncStylePreview(code) {
    const wrap = D.$('#style-prev-wrap');
    if (wrap) wrap.innerHTML = stylePreviewCard(code);
  }

  /**
   * 第三段（AI 生成剧本）—— 照线上做成**一个大输入框 + 三处设置**，不填表。
   *
   * 线上结构（见 `_recon/_flows/list__tab_ai`）：
   *   大 textarea（占位「请输入你的故事创意，可包含故事背景、主角设定、剧情走向、结局等」）
   *   + 右下角 `0/10000` 计数
   *   + 一行：[风格库] [9:16] [10集] …… [开始创作]
   *
   * ⚠️ **风格库用后端真实类型包**（水合时写入 `S.state.styles`），不是前端的 20+ 风格名 ——
   * v5 只有 3 个包，两边混用就是"选了 A 实际跑 B"。
   */
  function aiPane() {
    const styles = S.state.styles || [];
    const cur = (ui.aiStyle && styles.some(function (s) { return s.code === ui.aiStyle; }))
      ? ui.aiStyle : ((styles[0] || {}).code || '');
    const idea = String(ui.aiIdea || '');
    const ratios = ['9:16'];                       // v5 目前只支持 9:16（`config.ASPECT_RATIO`）
    const eps = [1, 2, 3, 5, 10];
    return '<div class="col gap12">' +
      '<div class="dropzone" style="padding:20px">' +
      '<textarea id="ai-idea" class="paste-area" maxlength="10000" style="min-height:200px" ' +
      'placeholder="请输入你的故事创意，可包含故事背景、主角设定、剧情走向、结局等">' +
      D.esc(idea) + '</textarea>' +
      '<div class="muted small" style="text-align:right;margin-top:-8px">' +
      '<span id="ai-count">' + idea.length + '</span>/10000</div>' +
      '<div class="pasted-foot" style="margin-top:12px">' +
      '<select id="ai-style" class="btn btn--sm" title="类型包决定审美与角色契约">' +
      (styles.length ? styles.map(function (s) {
        return '<option value="' + D.esc(s.code) + '"' + (s.code === cur ? ' selected' : '') + '>' +
          '风格库 · ' + D.esc(s.name) + '</option>';
      }).join('') : '<option value="">（风格库未加载）</option>') +
      '</select>' +
      '<select id="ai-ratio" class="btn btn--sm" title="v5 目前只支持 9:16">' +
      ratios.map(function (r) { return '<option value="' + r + '">' + r + '</option>'; }).join('') +
      '</select>' +
      '<select id="ai-episodes" class="btn btn--sm" title="集数">' +
      eps.map(function (n) {
        return '<option value="' + n + '"' + (n === (ui.aiEpisodes || 1) ? ' selected' : '') +
          '>' + n + '集</option>';
      }).join('') +
      '</select>' +
      '<button type="button" class="btn btn--primary btn--sm" data-action="ai-generate"' +
      (idea.trim() ? '' : ' disabled') + '>开始创作</button>' +
      '</div>' +
      // 风格预览（缩略图 + 文案）—— 只给包名不直观，见 stylePreviewCard 的说明
      '<div id="style-prev-wrap">' + stylePreviewCard(cur) + '</div>' +
      '<div class="muted small mt8">' + (Api && Api.driver === 'http'
        ? '由 AI 生成四幕结构的 brief（约 20–40 秒）；剧本与分镜由创作链后续产出'
        : '离线模式：由本地模板生成四幕结构剧本') + '</div>' +
      '</div></div>';
  }

  /** 第三段：已粘贴文本 → 选风格 → 剧本解析（对应线上实测流程）
   *
   * ⚠️ 风格列表用 **`S.state.styles`**（http 驱动下＝后端真实类型包），
   *    不再是前端的 `STYLE_CATALOG`（线上那 20+ 个中文风格名）。
   *    两个面板必须**同一套风格库** —— 否则同一个选择在两处含义不同（静默偏差）。
   */
  function pastedPane() {
    const styles = S.state.styles || [];
    const groups = [];
    styles.forEach(function (s) {
      const g = s.group || '其他';
      if (groups.indexOf(g) < 0) groups.push(g);
    });
    const cat = groups.indexOf(ui.styleCat) >= 0 ? ui.styleCat : (groups[0] || '其他');
    const inCat = styles.filter(function (s) { return (s.group || '其他') === cat; });
    const cur = ui.styleName || '请选择风格';
    return '<div class="pasted-box">' +
      '<div class="pasted-head"><span class="badge badge--done">已粘贴文本</span>' +
      '<button type="button" class="btn btn--ghost btn--xs" data-action="clear-draft">重新粘贴</button></div>' +
      '<div class="pasted-text">' + D.esc(String(ui.draft || '').slice(0, 160)) + '…</div>' +
      '<div class="pasted-foot">' +
      '<button type="button" class="btn btn--sm" data-action="style-menu">' +
      '<span class="muted">风格库</span> ' + D.esc(cur) + '</button>' +
      '<span class="muted small">9:16</span>' +
      '<button type="button" class="btn btn--primary btn--sm" data-action="parse-script"' +
      (!ui.styleName ? ' disabled' : '') + '>剧本解析</button>' +
      '</div>' +
      '<div class="muted small mt8">' + (ui.styleName ? '' : '分析剧本前请选择风格') + '</div>' +
      '</div>' +
      '<div class="pasted-cats">' + groups.map(function (c) {
        return '<button type="button" class="asset-tab' + (cat === c ? ' is-active' : '') + '" data-stylecat="' + D.esc(c) + '">' + D.esc(c) + '</button>';
      }).join('') + '</div>' +
      '<div class="pasted-styles">' + inCat.map(function (s) {
        return '<button type="button" class="pasted-style' + (ui.styleName === s.name ? ' is-active' : '') +
          '" data-style="' + D.esc(s.name) + '" data-stylecode="' + D.esc(s.code) + '">' + D.esc(s.name) + '</button>';
      }).join('') + '</div>' +
      // 风格预览（与 AI 面板共用同一张卡 —— 两个面板必须同样的直观度）
      '<div id="style-prev-wrap">' +
      stylePreviewCard(ui.styleCode || curCodeOf(ui.styleName)) + '</div>';
  }

  function projectsSection() {
    const isMine = ui.listTab === 'mine';
    // ★ 两个 tab 是**两个数据源**，不是同一份数据的两种切法：
    //   我的项目 = Store 里水合过的（`is_demo=false`）；
    //   精选项目 = 本视图自己按需拉的（`is_demo=true`）。
    //   ⚠️ 这里曾经是 `S.allProjects().slice(0, 0)` —— `.slice(0,0)` 恒为空数组，
    //      也就是**精选 tab 从来没发过请求**，却一直显示一句归因错误的提示。
    const list = isMine ? S.allProjects() : (ui.featured || []);
    return '<section class="section-gap" style="margin-top:48px">' +
      '<div class="section-head">' +
      '<div class="section-tabs">' +
      '<button type="button" class="section-tab' + (isMine ? ' is-active' : '') + '" data-list-tab="mine">我的项目</button>' +
      '<button type="button" class="section-tab' + (!isMine ? ' is-active' : '') + '" data-list-tab="featured">精选项目</button>' +
      '</div>' +
      '<div class="row gap12">' +
      '<button type="button" class="btn btn--ghost btn--sm" data-action="import">导入工程</button>' +
      '<button type="button" class="btn btn--ghost btn--sm" data-action="export-all">全部导出</button>' +
      '<button type="button" class="btn btn--sm" data-action="manage">管理</button>' +
      '</div>' +
      '</div>' +
      '<div class="project-grid">' +
      (list.length ? list.map(projectCard).join('') : emptyState()) +
      '</div>' +
      '</section>';
  }

  function emptyState() {
    return '<div class="empty">' + I.empty(48) + '<div>' + emptyText() + '</div></div>';
  }

  /** 空态文案 —— **按真实原因分档**。
   *
   * 为什么值得单独一个函数：这里原来只有一句
   *   「精选项目需要联网获取，离线版不提供」
   * 而当时精选列表是**硬编码空**的（`slice(0, 0)`），所以这句话在**联网状态下也照显示**
   * —— 它把"这个功能还没接线"说成了"你处于离线模式"。归因错误的提示比没有提示更糟：
   * 实测它直接把人带偏到"我是不是装了两个前端版本"。
   *
   * 四种真实情况，四种说法（顺序即优先级）：
   *   ① 我的项目为空      → 提示去创建
   *   ② 本地离线模式      → 精选来自服务端，本地取不到（**只有这一档才能说"离线"**）
   *   ③ http 且拉取失败   → 说失败，并说明"我的项目不受影响"
   *   ④ http 且成功但 0 条 → 后端确实没有精选，**不是离线、也不是失败**
   */
  function emptyText() {
    if (ui.listTab === 'mine') return '还没有项目，先在上方创建一个吧';
    if (Api.driver !== 'http') {
      return '精选项目由服务端维护，本地离线模式取不到（当前数据只存在本机浏览器）';
    }
    if (ui.featuredState === 'loading') return '正在拉取精选项目…';
    if (ui.featuredState === 'error') {
      return '精选项目拉取失败：' + D.esc(ui.featuredError || '后端未响应')
        + '（我的项目不受影响，仍是服务端数据）';
    }
    if (ui.featuredState === 'ok') {
      return '后端暂无标记为精选的项目（服务端 is_demo=true 返回 0 条）';
    }
    return '精选项目尚未拉取 —— 点上方「精选项目」重新获取';
  }

  /** 拉「精选项目」（服务端 `GET /projects?is_demo=true`）。
   *
   * **每次都拉，不做长期缓存**：它是服务端的运营数据（谁被标成精选由
   * `projects/featured.json` 决定），本地存一份只会过期 —— 本项目在
   * "厂商列表 / 风格库"上已经吃过同型亏（缓存盖掉后端的新列表 = 静默偏差）。
   * 只挡**并发重复请求**。当前 tab 上再点一次 = 手动刷新。
   */
  let featuredInflight = false;
  function loadFeatured() {
    if (featuredInflight) return;
    if (Api.driver !== 'http') {
      // 本地模式没有服务端 → 不发请求，落到「本地离线取不到」那一档文案
      ui.featured = null; ui.featuredState = 'idle'; ui.featuredError = '';
      return;
    }
    featuredInflight = true;
    ui.featuredState = 'loading'; ui.featuredError = '';
    // ⚠️ 这里**不**自己 rerender：调用方（tab 点击）紧接着就会 render 一次，
    //    重复渲染只会白跑一遍全量视图。异步完成时的那次 rerender 在下面。
    Api.listProjects(1, 100, true).then(function (d) {
      ui.featured = (d && d.list) || [];          // `http()` 已解包到 data
      ui.featuredState = 'ok';
    }, function (err) {
      ui.featured = null;
      ui.featuredState = 'error';
      // 「后端答复了」与「根本联系不上」必须说不同的话（这条纪律见 core/api.js 的 http()）
      ui.featuredError = (err && err.fromBackend)
        ? ((err.status ? err.status + ' ' : '') + (err.message || ''))
        : '后端未连通';
    }).then(function () {
      featuredInflight = false;
      rerender();
    });
  }

  function projectCard(p) {
    const epCount = p.episodes.length;
    const cover = p.cover || (p.episodes[0] && p.episodes[0].storyboard && firstSegImage(p));
    return '<div class="project-card" data-project="' + p.id + '">' +
      '<div class="project-cover">' +
      (cover ? '<img src="' + D.esc(cover) + '" alt="" loading="lazy">' :
        '<span class="muted small">暂无封面</span>') +
      '</div>' +
      '<div class="project-meta">' +
      '<div class="flex1"><h3>' + D.esc(p.name) + '</h3>' +
      '<p class="project-sub">' + epCount + '集<span class="divider"></span>' + D.esc(p.created_at) + '</p>' +
      '</div>' +
      '<button type="button" class="icon-btn" data-action="card-menu" data-project="' + p.id + '" aria-label="编辑项目名称">' +
      I.ellipsis(20) + '</button>' +
      '</div>' +
      ((p.id && ui.menuOpen === p.id) ? cardMenu(p) : '') +
      '</div>';
  }

  function firstSegImage(p) {
    const segs = (p.episodes[0].storyboard && p.episodes[0].storyboard.segments) || [];
    const s = segs.find(function (x) { return x.keyframe; });
    return s ? s.keyframe : '';
  }

  function cardMenu(p) {
    return '<div class="menu" style="right:16px;top:calc(100% - 24px)">' +
      '<div class="menu-item" data-action="open" data-project="' + p.id + '">打开项目</div>' +
      '<div class="menu-item" data-action="rename" data-project="' + p.id + '">重命名</div>' +
      '<div class="menu-item" data-action="duplicate" data-project="' + p.id + '">创建副本</div>' +
      '<div class="menu-item" data-action="export" data-project="' + p.id + '">导出工程 JSON</div>' +
      '<div class="menu-item danger" data-action="delete" data-project="' + p.id + '">删除项目</div>' +
      '</div>';
  }

  /* ---------------- 事件绑定 ---------------- */
  function bind(root) {
    D.delegate(root, 'click', '[data-create-tab]', function (e, t) {
      ui.createTab = t.getAttribute('data-create-tab'); rerender();
    });
    D.delegate(root, 'click', '[data-list-tab]', function (e, t) {
      ui.listTab = t.getAttribute('data-list-tab');
      // 切到精选 → 真去服务端问一次（`is_demo=true`）；切回我的项目不发请求。
      // 在当前 tab 上再点一次 = 手动刷新（`loadFeatured` 内部挡并发）。
      if (ui.listTab === 'featured') loadFeatured();
      rerender();
    });
    // ⚠️ `[data-nav]` 的 handler **已移到 `app.js` 统一处理**（原先这里与 `wizard.js`
    //   各绑一份，而 wizard 那份把 `data-nav` 当路径用、又会覆盖正确跳转）。
    //   这里**不要再绑**，否则又把"一个点击跑两次"带回来。

    // 剧本导入
    D.delegate(root, 'click', '[data-action="upload-file"]', function () {
      D.$('#file-input').click();
    });
    root.addEventListener('change', function (e) {
      if (e.target.id !== 'file-input') return;
      const f = e.target.files[0]; if (!f) return;
      const reader = new FileReader();
      reader.onload = function () {
        promptNewProject('upload_text', String(reader.result || ''), f.name.replace(/\.[^.]+$/, ''));
      };
      reader.readAsText(f, 'utf-8');
    });
    D.delegate(root, 'click', '[data-action="paste-script"]', function () { pasteModal(); });
    // —— 三段式：已粘贴文本 → 选风格 → 剧本解析 ——
    D.delegate(root, 'click', '[data-action="clear-draft"]', function () { ui.draft = null; ui.styleName = ''; rerender(); });
    D.delegate(root, 'click', '[data-stylecat]', function (e, t) { ui.styleCat = t.getAttribute('data-stylecat'); rerender(); });
    D.delegate(root, 'click', '[data-style]', function (e, t) {
      ui.styleName = t.getAttribute('data-style');
      // 类型包的 **code** 才是后端要的（用 data-stylecode；回落用名字兼容旧按钮）
      ui.styleCode = t.getAttribute('data-stylecode') || ui.styleName;
      S.toast('已选择风格：' + ui.styleName, 'ok'); rerender();
    });
    D.delegate(root, 'click', '[data-action="parse-script"]', function () {
      if (!ui.draft || !ui.styleName) { S.toast('分析剧本前请选择风格'); return; }
      const btnText = '解析中…';
      S.toast(btnText);
      // ★ 2026-09-19：等解析（20–40 秒）期间**盖全屏过场** —— 之前只有右下角一条 toast，
      //   用户不知道在等什么、也没法停（用户反馈："应该先等加载过场动画"）。
      ovShow();
      Api.createByParse(ui.draft, ui.styleCode, ui.styleName).then(function (p) {
        ovHide();
        const a = p.analyze || {};
        // `note` 存在时说明此刻还没有资产条目（要等创作链）→ 如实说出来，
        // 不要只报 0/0/0 让人以为解析漏了东西
        S.toast(a.note
          ? ('解析完成：' + a.characters + ' 角色。' + a.note)
          : ('解析完成：' + a.characters + ' 角色 / ' + a.scenes + ' 场景 / ' + a.props + ' 道具'), 'ok');
        ui.draft = null; ui.styleName = '';
        openProject(p.id || p.pid);
      }).catch(function (err) { ovHide(); S.toast('解析失败：' + err.message); });
    });
    // 「AI 生成剧本」：一个输入框 + 三项设置（照线上；见 aiPane 的说明）
    D.delegate(root, 'click', '[data-action="ai-generate"]', function () {
      const ta = D.$('#ai-idea');
      const idea = ((ta && ta.value) || '').trim();
      const styleCode = (D.$('#ai-style') || {}).value || '';
      const ratio = (D.$('#ai-ratio') || {}).value || '9:16';
      const episodes = parseInt(((D.$('#ai-episodes') || {}).value || '1'), 10) || 1;
      if (!idea) { S.toast('请先写下你的故事创意'); return; }

      if (Api && Api.driver === 'http') {
        // ★ 真打后端：**由 AI 创作 brief**（与「粘贴剧本」的"提炼"是两种活）
        const btn = document.querySelector('[data-action="ai-generate"]');
        if (btn) { btn.disabled = true; btn.textContent = '创作中…'; }
        S.toast('正在由 AI 创作剧本，约 20–40 秒…');
        ovShow();                       // ★ 见上面「解析剧本」那条注释
        Api.createByAI({ idea: idea, style_code: styleCode, ratio: ratio,
                         episodes: episodes })
          .then(function (p) {
            ovHide();
            S.toast('已创作：《' + (p.topic || p.id) + '》', 'ok');
            ui.aiIdea = '';
            openProject(p.id || p.pid);
          })
          .catch(function (e) {
            ovHide();
            if (btn) { btn.disabled = false; btn.textContent = '开始创作'; }
            S.toast('创作失败：' + (e && e.message ? e.message : e));
          });
        return;
      }

      // 离线模式：本地模板
      const style = S.state.styles.find(function (s) { return s.code === styleCode; })
        || S.state.styles[0] || { code: styleCode, name: styleCode };
      const p = S.createProject({
        name: '', source_type: 'ai_generate',
        style_code: style.code, style_name: style.name,
        script: localScript('', idea, style.name),
        one_line_story: idea, story_type: '本地生成模板',
      });
      ui.aiIdea = '';
      S.toast('剧本已生成', 'ok');
      openProject(p.id);
    });
    // 输入框：字数计数 + 「开始创作」的可用态（**不重渲染**，直接改 DOM —— 免得丢焦点）
    D.delegate(root, 'input', '#ai-idea', D.debounce(function (e, t) {
      ui.aiIdea = t.value;
      const c = D.$('#ai-count');
      if (c) c.textContent = String(String(t.value || '').length);
      const btn = document.querySelector('[data-action="ai-generate"]');
      if (btn && !btn.dataset.busy) btn.disabled = !String(t.value || '').trim();
    }, 150));
    D.delegate(root, 'change', '#ai-style', function (e, t) {
      ui.aiStyle = t.value;
      syncStylePreview(t.value);        // 换包 → 预览卡跟着换（只重画卡片，不整页重渲染）
    });
    D.delegate(root, 'change', '#ai-episodes', function (e, t) {
      ui.aiEpisodes = parseInt(t.value, 10) || 1;
    });

    // 项目卡片菜单：线上为 antd dropdown「重命名 / 删除」（见 _flows/L8_card_menu）
    D.delegate(root, 'click', '[data-action="card-menu"]', function (e, t) {
      e.stopPropagation();
      const id = t.getAttribute('data-project');
      const p = S.getProject(id); if (!p) return;
      global.Overlay.menu(t, [
        { label: '打开项目', action: function () { openProject(id); } },
        { label: '重命名', action: function () { renameModal(p); } },
        { label: '导出工程 JSON', action: function () { S.exportProject(id); } },
        { label: '删除', danger: true, action: function () { deleteConfirm(p); } },
      ], { align: 'right' });
    });
    D.delegate(root, 'click', '[data-action="open"]', function (e, t) {
      openProject(t.getAttribute('data-project'));
    });
    D.delegate(root, 'click', '.project-card', function (e, t) {
      if (e.target.closest('[data-action]')) return;
      openProject(t.getAttribute('data-project'));
    });

    // 工具动作
    D.delegate(root, 'click', '[data-action="manage"]', function () { S.toast('管理：可直接在卡片菜单里重命名/删除'); });
    D.delegate(root, 'click', '[data-action="export-all"]', function () { S.exportAll(); });
    D.delegate(root, 'click', '[data-action="import"]', function () {
      const inp = document.createElement('input');
      inp.type = 'file'; inp.accept = '.json';
      inp.onchange = function () {
        const f = inp.files[0]; if (!f) return;
        const rd = new FileReader();
        rd.onload = function () {
          try { const r = S.importJSON(String(rd.result)); S.toast('导入成功', 'ok'); rerender(); }
          catch (err) { S.toast('导入失败：' + err.message); }
        };
        rd.readAsText(f, 'utf-8');
      };
      inp.click();
    });
    D.delegate(root, 'click', '.menu', function (e) { e.stopPropagation(); });
    D.delegate(root, 'click', '.content', function () { if (ui.menuOpen) { ui.menuOpen = null; rerender(); } });
  }

  /** 打开项目 —— **唯一入口**，空标识一律拒绝。
   *
   * ⛔ 为什么不直接 `Router.go('/playlet/review/' + id)`：
   *   JS 里 `'...' + null` 会得到**字面串** `'...null'` → hash 变成
   *   `#/playlet/review/null` → 后端稳定 404「项目不存在：null」，
   *   而界面把它显示成"后端未连通"（**这句报错本身也误导**，已在 `app.js` 修）。
   *   实测（2026-09-17）：shim 日志里每次导航都跟着两条
   *   `GET /projects/null/progress → 404`，但**新建 profile 复现不出来**，
   *   说明触发路径与用户的本地缓存/历史状态有关 ⇒ 这里做**防御性拦截**，
   *   保证无论上游怎么传都不会拼出坏 URL。
   */
  function openProject(id) {
    const bad = function (v) { return !v || v === 'null' || v === 'undefined'; };
    if (bad(id)) {
      console.warn('[playlet] 拒绝打开项目：标识为空', id);
      S.toast('项目标识缺失，无法打开（请刷新页面重试）');
      return false;
    }
    global.Router.go('/playlet/review/' + id);
    return true;
  }

  function rerender() { global.App.render(); }

  /** 粘贴剧本 —— 线上实测：弹层只有正文框（无名称框），计数 0/100000；
   *  「完成」不建项目，而是回到列表页进入「已粘贴文本」态，等选风格后点「剧本解析」。 */
  function pasteModal() {
    const MAX = 100000;
    return global.Overlay.modal({
      title: '粘贴剧本', confirmText: '完成', width: 640,
      body: '<textarea class="ov-textarea" data-pp="text" style="min-height:260px" maxlength="' + MAX + '" placeholder="在这里粘贴剧本"></textarea>' +
        '<div class="ov-count" data-pp-count>0/' + MAX + '</div>',
      onConfirm: function (root) {
        const text = (root.querySelector('[data-pp="text"]').value || '').trim();
        if (!text) return false;
        ui.draft = text; ui.styleName = ''; ui.styleCode = '';
        S.toast('已粘贴文本，请选择风格', 'ok');
        rerender();
        return true;
      },
      onReady: function (api) {
        const ta = api.root.querySelector('[data-pp="text"]');
        const cnt = api.root.querySelector('[data-pp-count]');
        const sync = function () { cnt.textContent = ta.value.length + '/' + MAX; api.setDisabled(!ta.value.trim()); };
        ta.addEventListener('input', sync); sync();
      },
    });
  }

  /** 重命名 —— 线上为带字数上限的输入弹窗（项目名 ≤ 40） */
  function renameModal(p) {
    return global.Overlay.prompt({
      title: '重命名项目', value: p.name, max: 40, placeholder: '项目名称', confirmText: '确认',
      onConfirm: function (v) {
        Api.renameProject(p.id, v).then(function () { S.toast('已重命名', 'ok'); rerender(); });
      },
    });
  }

  /** 删除 —— 线上为红色确认弹窗，需再次点击「删除」 */
  function deleteConfirm(p) {
    return global.Overlay.confirm({
      title: '删除项目',
      text: '确定删除项目「' + p.name + '」？删除后无法恢复。',
      danger: true, confirmText: '删除',
      onOk: function () { Api.deleteProject(p.id).then(function () { S.toast('已删除'); rerender(); }); },
    });
  }

  function promptNewProject(sourceType, text, name) {
    const p = S.createProject({ name: name || '未命名短剧', source_type: sourceType, script: text });
    S.toast('项目已创建', 'ok');
    openProject(p.id);
  }

  /** 离线确定性「剧本生成」：按四幕结构套用本地模板 */
  function localScript(name, idea, styleName) {
    const ideaText = idea || name;
    return '正文拍摄剧本\n' +
      '1.内景 主场景 - 日\n' +
      '【钩子】' + ideaText + '。开场用一个高信息密度的动作切入，人物在压力下做出第一个选择。\n' +
      '2.外景 街巷 - 夜\n' +
      '【发展】矛盾升级，主角为了达成目的，主动放弃了原本守住的那条底线。\n' +
      '3.内景 主场景 - 夜\n' +
      '【转折】代价显现：他此前的一个决定，反过来作用在自己身上。\n' +
      '4.外景 空地 - 黎明\n' +
      '【收尾】主角面对结果，画面定格在他回望的一个瞬间。\n' +
      '\n' +
      '（本剧本由离线版本地模板生成｜风格：' + styleName + '｜主题：' + ideaText + '）';
  }

  /* 通用模态框 */
  function openModal(opts) {
    const mask = D.el('div', { class: 'modal-mask' });
    mask.innerHTML = '<div class="modal"><h3>' + D.esc(opts.title) + '</h3>' +
      '<div class="modal-body">' + opts.body + '</div>' +
      '<div class="modal-foot">' +
      '<button type="button" class="btn btn--sm" data-m="cancel">取消</button>' +
      '<button type="button" class="btn btn--primary btn--sm" data-m="ok">' + D.esc(opts.confirmText || '确定') + '</button>' +
      '</div></div>';
    document.body.appendChild(mask);
    const close = function () { mask.remove(); };
    mask.addEventListener('click', function (e) {
      if (e.target === mask) return close();
      const b = e.target.closest('[data-m]'); if (!b) return;
      if (b.getAttribute('data-m') === 'cancel') return close();
      const ok = opts.onConfirm ? opts.onConfirm(mask) : true;
      if (ok !== false) close();
    });
    const first = mask.querySelector('input,textarea'); if (first) first.focus();
    return { close: close, root: mask };
  }

  global.PlayletList = { view: view, bind: bind, openModal: openModal, resetUi: function () { ui.menuOpen = null; } };
})(window);
