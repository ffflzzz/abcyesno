/* ==========================================================================
   ui/hitl-bar.js —— 步级「逐步人工确认」的常驻确认条（2026-09-18）
   --------------------------------------------------------------------------
   链路（`scripts/drive_chain.py`）每派一个角色**之前**会挂起，等人给决定。
   人在等的时候很可能翻到别的页面去了 —— 所以这条**必须挂在 `<body>` 上**：

     `App.paint()` 每次都会 `#app.innerHTML = ...`，挂在里面的元素会被删掉。
     这正是 `views/shell.js` 那条注释记过的坑（"常驻 UI 挂 body，别挂 #app"）。
     挂在 `#app` 里的后果是：你一翻页，按钮就没了，而任务还在等 —— 死等。

   两个按钮（**刻意只有两个**）：
     · 继续  → `POST {decision:'approve'}` → 链路 resume，跑 `next_role`
     · 打回  → `Overlay.prompt` 收一句原因 → `POST {decision:'redo', target, note}`
               → 链路把该角色**及其下游**的旧产物移入 `.rerun_backup/` 后重跑

   为什么不给「中止」按钮：`reject` 是运维动作（要中止走 CLI 的 `--hitl-reject`）。
   把"中止整条链"摆在前端，和"重做这一步"只差一次误点 —— 而代价是整条链没了。
   ========================================================================== */
(function (global) {
  'use strict';
  const D = global.D;

  const ID = 'hitl-bar';

  /** 角色 → 中文产物名（给用户看的；后端只认角色名） */
  const ROLE_CN = {
    // ★ 2026-09-19：`director` 也进来了 —— 它**不是被派发的角色**，
    //   但它有一份产物（`director/director.md` = 制作规格：片长/画幅/音频模式/
    //   视觉基准），而且是**第一停**时人唯一能审的东西。现在它也能被打回
    //   （见 `v5/hitl.redo_targets_of` 的说明）。
    director: '制作规格',
    worldbuilder: '世界观与角色卡',
    assetdesigner: '资产卡',
    plotdesigner: '剧情大纲',
    scriptwriter: '剧本',
    dialogue: '台词清单',
    scenedesigner: '分镜表',
    reviewer: '审片报告',
  };

  let lastKey = '';        // 状态指纹：内容没变就不重建 DOM（重建会打断鼠标悬停）
  let current = null;      // {state, pid}

  function label(role) {
    return role ? (ROLE_CN[role] ? ROLE_CN[role] + '（' + role + '）' : role) : '—';
  }

  function node() { return document.getElementById(ID); }

  /**
   * 给**内容**预留底部空间（否则固定底栏必然盖住落在底部的控件）。
   *
   * ★ 2026-09-19 真机实测（420×800）：条子 203×163、占 y 613~776，
   *   而那一屏的**主按钮「下一步」（confirm-outline）在 y 741~773** —— **被压住**。
   *   桌面宽（1584）量下来一个控件都不挡 ⇒ 这个问题只在窄屏暴露。
   *
   * ## 为什么预留量是**写死在 CSS 里**，而不是量出来的（这点很重要）
   *
   * 我先写的是"量条子高度 → 写进 `--hitl-bar-h`"，**结果自相矛盾**：量到 145px，
   * 而条子实际 163px。补了 `requestAnimationFrame` 与 `ResizeObserver` 都修不好。
   *
   * 根因是**反馈环**（不是量错时机）：
   *   设 `padding-bottom` → 文档变长 → **出现纵向滚动条** → `100vw` 变窄
   *   → 条子换行方式变 → **高度从 145 变 163** → 预留量又变 → ……
   * 于是"条子高度"本身**没有稳定值**，量它就是在追一个动靶。
   *
   * ⇒ 正确做法：**不量**。按实测的两档高度在 CSS 里写死预留（桌面 112px / 窄屏 220px），
   *   于是没有反馈环、不会振荡。这里只负责切 `body.has-hitl-bar` 这个类。
   */
  function syncSpace() {
    if (!document.body) return;
    document.body.classList.toggle('has-hitl-bar', !!node());
  }

  function removeBar() {
    const e = node();
    if (e && e.parentElement) e.parentElement.removeChild(e);
    lastKey = '';
    current = null;
    syncSpace();          // 收起来就把预留空间还回去（此时 node() 已为 null ⇒ 类被摘掉）
  }

  /** 状态指纹：只要这几项没变，就不动 DOM */
  function keyOf(st, pid) {
    return [pid, st.stamp || '', st.next_role || '', st.prev_role || '',
            st.stale_decision || '', st.decision || ''].join('|');
  }

  function busy(on, text) {
    const e = node();
    if (!e) return;
    e.classList.toggle('is-busy', !!on);
    const b = e.querySelector('.hitl-busy');
    if (b) b.textContent = on ? (text || '提交中…') : '';
  }

  function send(body) {
    const cur = current;
    if (!cur) return;
    const e = node();
    Array.prototype.forEach.call(e ? e.querySelectorAll('button') : [], function (b) {
      b.disabled = true;
    });
    busy(true, body.decision === 'redo' ? '正在打回…' : '正在继续…');
    global.Api.postHitl(cur.pid, body).then(function (st) {
      busy(false);
      global.Store.toast(body.decision === 'redo'
        ? '已打回：' + label(body.target) + ' 及其下游将重跑'
        : '已继续：链路接着往下跑', 'ok');
      // 用返回的最新状态重画（pending 变 false 就自动收起）
      update(st, cur.pid);
    }, function (err) {
      busy(false);
      global.Store.toast('提交失败：' + ((err && err.message) || err));
      // 失败要**把按钮放回来**，否则用户就卡在一个点不动的条上
      Array.prototype.forEach.call(e ? e.querySelectorAll('button') : [], function (b) {
        b.disabled = false;
      });
    });
  }

  function onRedo() {
    const cur = current;
    if (!cur) return;
    const st = cur.state || {};
    const targets = st.redo_targets || [];
    if (!targets.length) {
      global.Store.toast('没有可打回的目标（还没有任何角色完成）');
      return;
    }
    // 默认打回"你刚看到的那个"；要回到更上游的角色，提示里给清单
    const def = st.prev_role && targets.indexOf(st.prev_role) >= 0 ? st.prev_role : targets[targets.length - 1];
    global.Overlay.prompt({
      title: '打回重做',
      multiline: true,
      max: 300,
      value: '',
      placeholder: '哪里不满意？（会原样交给导演，越具体越好）',
      hint: '会重跑「' + label(def) + '」及其下游（上游不动）。'
          + '可打回的角色：' + targets.map(label).join('、'),
      confirmText: '打回重做',
      onConfirm: function (note) {
        send({ decision: 'redo', target: def, note: note, stamp: st.stamp || '' });
      },
    });
  }

  function render(st, pid) {
    const e = node();
    const prev = label(st.prev_role);
    const next = label(st.next_role);
    e.innerHTML =
      '<span class="hitl-dot" aria-hidden="true"></span>' +
      '<div class="hitl-text">' +
      '<b>等你确认</b>' +
      '<span>刚产出：<b>' + D.esc(prev) + '</b>　→　下一步：<b>' + D.esc(next) + '</b></span>' +
      (st.stale_decision
        ? '<span class="hitl-warn">⚠️ 你上一次的点击已失效（那一步已经过去）—— 请重新确认</span>'
        : '') +
      (st.decision
        ? '<span class="hitl-warn">已提交「' + D.esc(st.decision) + '」，等待链路响应…</span>'
        : '') +
      '</div>' +
      '<span class="hitl-busy"></span>' +
      '<button type="button" class="btn btn--sm" data-hitl="redo">打回重做</button>' +
      '<button type="button" class="btn btn--sm btn--primary" data-hitl="approve">继续</button>';

    e.onclick = function (ev) {
      const b = ev.target.closest('[data-hitl]');
      if (!b) return;
      if (b.getAttribute('data-hitl') === 'redo') { onRedo(); return; }
      send({ decision: 'approve', stamp: (current.state || {}).stamp || '' });
    };
    if (st.decision) busy(true, '已提交，等待链路…');
    syncSpace();          // 只切类；预留量走 CSS 固定值（见 syncSpace 的说明：**不能量**）
  }

  /**
   * 更新确认条。**幂等** —— 每次轮询都调它，内容没变就不会动 DOM。
   *
   * @param {object} st   `Api.getHitl` 的返回
   * @param {string} pid  当前项目
   */
  function update(st, pid) {
    const s = st || {};
    if (!s.pending) { removeBar(); return; }
    const k = keyOf(s, pid);
    if (k === lastKey && node()) return;         // 没变化 → 不动
    lastKey = k;
    current = { state: s, pid: pid };
    let e = node();
    if (!e) {
      e = D.el('div', { id: ID, class: 'hitl-bar' });
      e.setAttribute('role', 'status');
      document.body.appendChild(e);
    }
    render(s, pid);
  }

  // 视口变化会改变条子的高度（窄屏换行 ⇒ 从 81px 变成 163px，实测）⇒ 重新量一次预留空间。
  // 用 `try` 兜住：本文件要被 Node 的无浏览器渲染断言 import，那里没有真实 window。
  try {
    window.addEventListener('resize', function () { if (node()) syncSpace(); });
  } catch (e) { /* 无 window.addEventListener 的测试环境 */ }

  global.HitlBar = { update: update, remove: removeBar, label: label, _syncSpace: syncSpace };
})(window);
