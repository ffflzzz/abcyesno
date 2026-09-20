/* ==========================================================================
   views/shell.js —— 应用外壳（左侧导航 + 顶部栏）
   复刻自线上：aside.ant-layout-sider（宽 6.125rem）+ header（高 87.5px）
   ========================================================================== */
(function (global) {
  'use strict';
  const D = global.D, I = global.Icons;

  /** 左侧导航 */
  function sider(activeKey) {
    const items = global.NAV_ITEMS.map(function (n) {
      return '<button type="button" id="' + n.domId + '" class="nav-item' + (n.key === activeKey ? ' is-active' : '') + '"' +
        ' data-nav="' + n.key + '" data-route="' + n.route + '" title="' + n.label + '">' +
        '<span class="nav-ico">' + I[n.icon](28) + '</span>' +
        '<span class="nav-label">' + n.label + '</span>' +
        '</button>';
    }).join('');

    return '<aside class="sider">' +
      '<div class="sider-top">' +
      '<button type="button" id="dle_pavo_logo" class="sider-logo" title="Pavo Home">' + logoMark(36) + '</button>' +
      '<div class="sider-nav">' + items + '</div>' +
      '</div>' +
      '<div class="sider-bottom">' +
      '<span class="nav-item" id="dle_mobile_logo" title="移动端">' + I.wechat(28) + '</span>' +
      '<button type="button" id="dle_settings_logo" class="nav-item" data-action="settings" title="设置">' +
      '<span class="nav-ico">' + I.settings(28) + '</span></button>' +
      '</div>' +
      '</aside>';
  }

  function logoMark(size) {
    return '<span style="display:flex;align-items:center;justify-content:center;width:' + size + 'px;height:' + size +
      'px;border-radius:8px;background:#121212;color:#fff;font-weight:700;font-size:' + Math.round(size * 0.58) +
      'px;line-height:1">P</span>';
  }

  /** 顶部栏 —— 工作台模式（返回 + 标题 + 步骤条）
   *
   * ★ `opts.scope` 会盖在步骤条容器上（`data-stepper="wizard"`）——
   *   **必须有它**：`bindGlobal()` 把所有视图的委托都绑到同一个 `#app` 上、
   *   永久生效，而向导页与分镜页**都**渲染这个步骤条 ⇒ 两边都绑了 `[data-step]`，
   *   点一次跑两次。分镜视图的 `ctx.pid` 在向导页是 `null` →
   *   它那条 `'/playlet/review/' + null` 拼出 `.../null` → 后端 404
   *   （2026-09-17 实测事故，查了很久）。带上作用域后各自只响应自己的。
   */
  function topbarWorkbench(opts) {
    const steps = opts.steps || [];
    const scope = opts.scope || '';
    const stepHtml = steps.map(function (s) {
      const cls = 'step' + (s.state === 'active' ? ' is-active' : '') +
        (s.state === 'done' ? ' is-done' : '') + (s.state !== 'active' ? ' is-clickable' : '');
      const ico = s.state === 'done' ? '<span class="step-ico">' + I.check(20) + '</span>'
        : '<span class="step-num">' + s.no + '.</span>';
      return '<button type="button" class="' + cls + '" data-step="' + s.key + '"' + (s.state === 'active' ? ' aria-current="step"' : '') + '>' +
        ico + '<span>' + D.esc(s.label) + '</span></button>';
    }).join('<span class="step-sep">' + I.chevronRight(16) + '</span>');

    // ★ `topbar--pinned`：**工作台（生产页）顶栏常驻**，不参与自动隐藏。
    //   用户 2026-09-18 要求「进入生产页面后 header 应该固定、不要自动隐藏」——
    //   生产页是持续看内容/翻长页面的地方，顶栏（返回 + 步骤条 + 算力）收起会
    //   让人每次都要"把鼠标甩到屏幕顶端"才能用，成本远高于它占的那 87.5px。
    //   自动隐藏保留给列表页/占位页（`topbarPlain`）—— 那里顶栏确实没内容。
    return '<header class="topbar topbar--pinned">' +
      '<button type="button" class="back-btn" data-action="back" aria-label="返回">' + I.back(24) + '</button>' +
      '<span class="topbar-title">' + D.esc(opts.title || '') + '</span>' +
      '<div class="topbar-center">' +
      (steps.length ? '<div class="stepper"' + (scope ? ' data-stepper="' + D.esc(scope) + '"' : '') + '>' + stepHtml + '</div>' : '') +
      '</div>' +
      credits() +
      '</header>';
  }

  /** 顶部栏 —— 普通页（带侧边栏偏移） */
  function topbarPlain() {
    return '<header class="topbar with-sider">' +
      '<div class="topbar-center"></div>' + credits() + '</header>';
  }

  function credits() {
    const u = global.Store.state.user;
    return '<div class="topbar-right">' +
      '<div class="credits" title="算力点">' +
      '<span style="width:26px;height:26px;border-radius:50%;background:#e9c86a;display:inline-block"></span>' +
      '<b style="font-weight:600">' + u.credits + '</b>' +
      '<span class="sep"></span>' +
      '<span class="upgrade">升级</span>' +
      '</div>' +
      '<button type="button" class="avatar" aria-label="Account" data-action="account">' +
      (u.avatar ? '<img src="' + D.esc(u.avatar) + '" alt="avatar">' : avatarFallback()) +
      '</button>' +
      '</div>';
  }

  function avatarFallback() {
    return '<span style="display:flex;width:100%;height:100%;align-items:center;justify-content:center;background:#0ABCCF;color:#fff;font-weight:600">' +
      D.esc((global.Store.state.user.name || 'U').slice(0, 1).toUpperCase()) + '</span>';
  }

  /**
   * 自动隐藏外壳：**边缘热区 → 弹出 → 悬停保持 → 离开收纳**。
   *
   * 用户要求（2026-09-17）：顶栏与侧栏平时收起，指针靠近才弹出。
   * 理由很实在 —— 这两处目前**几乎都是占位符**（灵感/画布/作品/资产/设置/算力/头像
   * 都没接通），常驻会一直占着屏。**等接好再常驻**。
   *
   * 三条容易翻车的细节（都处理了）：
   *  ① **可见把手**：纯靠边缘热区 = 不可发现（没人知道那里有东西）。
   *     所以加了 `.shell-handle`（左中 + 上中两条细条），展开时自动藏起。
   *  ② **把手与状态挂在 `body` 上**：`App.paint()` 每次 `#app.innerHTML = ...`，
   *     放在 `#app` 里会被重渲染删掉。`<body>` 不受影响。
   *  ③ **重渲染后要重新贴类名**：`.sider` 是新元素、不带 `is-open`
   *     → 用 `MutationObserver` 在 `#app` 变化后 re-apply（不然鼠标没动就会"莫名收起"）。
   *
   * 另外照顾可访问性：键盘 `Tab` 聚焦到侧栏内任何元素时也展开（`focusin`）。
   *
   * ★ **例外：工作台（生产页）顶栏不参与隐藏**（2026-09-18 用户要求）——
   *   标记为 `topbar--pinned` 的顶栏恒常驻（见 `apply()`），并给 `body` 打
   *   `shell-top-pinned`，让 CSS 把 `--main-pt` 的顶部留白还回去。
   */
  function mountAutoHide() {
    // 留个体贴的开关：`document.body.dataset.shellAutohide = '0'` 可关掉
    if (String(document.body.dataset.shellAutohide || '') === '0') return;
    if (document.body.classList.contains('is-autohide')) return;   // 幂等
    document.body.classList.add('is-autohide');

    const HOT = 10;      // 边缘热区厚度（px）
    const KEEP = 28;     // 悬停保持区向外的宽容度（px）

    // 把手（两条细条）——挂 body，别挂 #app
    ['left', 'top'].forEach(function (edge) {
      const h = document.createElement('div');
      h.className = 'shell-handle shell-handle--' + edge;
      h.dataset.edge = edge;
      h.setAttribute('aria-hidden', 'true');
      document.body.appendChild(h);
    });

    const st = { sider: false, top: false };
    const $sider = function () { return document.querySelector('.sider'); };
    const $top = function () { return document.querySelector('.topbar'); };
    /** 当前顶栏是否「常驻」（生产页）。每次现查 DOM —— 视图会整个重渲染换节点。 */
    const pinnedTop = function () {
      const p = $top();
      return !!(p && p.classList.contains('topbar--pinned'));
    };
    const near = function (el, x, y, pad) {
      if (!el) return false;
      const r = el.getBoundingClientRect();
      return x >= r.left - pad && x <= r.right + pad && y >= r.top - pad && y <= r.bottom + pad;
    };

    function apply() {
      const s = $sider(), p = $top();
      const pin = pinnedTop();
      if (s) s.classList.toggle('is-open', st.sider);
      // 常驻 ⇒ 永远算"展开"，鼠标在哪都不收起
      if (p) p.classList.toggle('is-open', pin || st.top);
      document.body.classList.toggle('shell-top-pinned', pin);
      document.body.classList.toggle('shell-hot-left', st.sider);
      document.body.classList.toggle('shell-hot-top', !pin && st.top);
      Array.prototype.forEach.call(document.querySelectorAll('.shell-handle'), function (h) {
        // 把手只在**有对应面板**时才有意义 —— 生产页没有 `.sider`，
        // 左侧那条把手在那里是"点了没反应"的死控件，必须一并藏掉。
        const hide = h.dataset.edge === 'left'
          ? (!s || st.sider)                 // 无侧栏 / 已展开 → 藏
          : (pin || st.top);                 // 顶栏常驻 / 已展开 → 藏
        h.classList.toggle('is-hidden', hide);
      });
    }

    document.addEventListener('mousemove', function (e) {
      const x = e.clientX, y = e.clientY;
      // 贴边 → 展开；已在展开态且指针还在其附近 → 保持
      const wantSider = x <= HOT || (st.sider && near($sider(), x, y, KEEP));
      // 顶栏常驻时**不再跟鼠标**（否则会在 body 上反复切换 hot 态）
      const wantTop = pinnedTop() ? false
        : (y <= HOT || (st.top && near($top(), x, y, KEEP)));
      if (wantSider !== st.sider || wantTop !== st.top) {
        st.sider = wantSider; st.top = wantTop;
        apply();
      }
    });
    // 指针离开整个窗口（mousemove 不再触发）→ 收纳，免得停在展开态
    document.addEventListener('mouseleave', function () {
      if (st.sider || st.top) { st.sider = st.top = false; apply(); }
    });
    // 键盘可达：Tab 进侧栏也展开
    document.addEventListener('focusin', function (e) {
      const s = $sider();
      if (s && s.contains(e.target) && !st.sider) { st.sider = true; apply(); }
    });

    // 重渲染后重新贴类名（`.sider`/`.topbar` 是新节点，不带 is-open）
    const app = document.getElementById('app');
    if (app && global.MutationObserver) {
      new MutationObserver(function () { apply(); })
        .observe(app, { childList: true, subtree: false });
    }
    apply();
  }

  global.Shell = { sider, topbarWorkbench, topbarPlain, logoMark, credits, mountAutoHide };
})(window);
