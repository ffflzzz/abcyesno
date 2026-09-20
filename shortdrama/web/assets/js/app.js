/* ==========================================================================
   app.js —— 应用入口：路由注册 + 渲染调度 + 全局交互
   ========================================================================== */
(function (global) {
  'use strict';
  const D = global.D, I = global.Icons, S = global.Store, R = global.Router;

  let bound = false;
  let renderSeq = 0;      // 并发保护：连续两次 render 时，只有最新那次允许落 DOM

  /**
   * 渲染调度。
   *
   * ★ `http` 驱动下先做一次**水合**（把远端数据灌进 Store）再画；`local` 驱动直接画。
   *   这样各视图**一行都不用改** —— 它们继续读 `S.getProject()`，只是数据已经是后端的。
   *
   * ⚠️ **水合失败绝不阻断渲染**：回落本地缓存 + 弹提示。
   *   否则后端一挂整个界面白屏，连"哪里错了"都看不见（本项目「失败必须可见」）。
   */
  function render() {
    const cur = R.current || { path: '/playlet/list', params: {}, query: {} };
    const A = global.Api;
    const seq = ++renderSeq;

    if (A && A.hydrate && A.driver === 'http') {
      return A.hydrate(cur).then(function (r) {
        if (seq === renderSeq) paint(cur);
        return r;
      }, function (err) {
        // ★★ 报错要**说对话**（2026-09-17 实测踩到）：
        //   原先一律显示"后端未连通"，而后端明确回的 404「项目不存在：null」
        //   也被这么说 —— **把排查方向带偏**（人以为服务没起，其实服务好得很）。
        const msg = (err && err.message) ? err.message : String(err);
        if (err && err.isNetwork) {
          S.toast('后端未连通，显示本地缓存：' + msg);
        } else if (err && err.status) {
          S.toast('后端拒绝了这次请求（HTTP ' + err.status + '）：' + msg);
        } else {
          S.toast('水合失败，显示本地缓存：' + msg);
        }
        if (seq === renderSeq) paint(cur);
        return { hydrated: false, error: msg };
      });
    }
    paint(cur);
    return Promise.resolve({ hydrated: false, from: 'local' });
  }

  /** 真正的 DOM 绘制（读的都是 Store，与水合解耦）。 */
  function paint(cur) {
    const app = document.getElementById('app');
    let html;
    try {
      if (cur.path === '/playlet/list') html = global.PlayletList.view();
      else if (cur.params && cur.params.pid && cur.params.eid) html = global.Storyboard.view(cur.params, cur.query);
      else if (cur.params && cur.params.pid) html = global.Wizard.view(cur.params, cur.query);
      else if (cur.path === '/inspiration' || cur.path === '/') html = global.Placeholders.inspiration();
      else if (cur.path === '/canvas') html = global.Placeholders.canvas();
      else if (cur.path === '/chat') html = global.Placeholders.works();
      else if (cur.path === '/visuals') html = global.Placeholders.visuals();
      else html = global.Placeholders.notFound(cur.path);
    } catch (e) {
      // ★★ 视图渲染失败必须**可见**（本项目铁律：静默失败是最贵的一类 bug）。
      //   原先异常会穿透 `paint()`，`innerHTML` 得不到赋值 → DOM 保持上一次的内容
      //   → 现象是"点了路由没反应/页面没换"，而**真正的错因完全看不到**
      //   （2026-09-15 真实浏览器实测：分镜页崩了，界面却仍显示向导页）。
      html = '<div style="padding:32px">' +
        '<h2 style="font-size:15px;margin:0 0 8px">页面渲染失败</h2>' +
        '<pre style="white-space:pre-wrap;color:var(--danger);font-size:12px;margin:0 0 12px">' +
        D.esc(String((e && (e.stack || e.message)) || e)) + '</pre>' +
        '<button type="button" class="btn btn--sm" onclick="location.reload()">重新加载</button>' +
        '</div>';
      window.__APPERR.push('render: ' + (e && e.message));
    }
    app.innerHTML = html;
  }

  function renderNotFound(path) { global.Placeholders.notFound(path); }

  function bindGlobal() {
    if (bound) return;
    bound = true;
    const app = document.getElementById('app');

    // ⚠️ **别以为"选择器互不冲突"**（2026-09-17 实测打脸）：
    //   所有视图的委托都绑在**同一个 `#app`** 上且**永久生效**，所以
    //   **同一个选择器被两个视图绑定 = 点一次跑两次**。真实事故：
    //   `[data-step]` 被 `wizard.js` 与 `storyboard.js` 同时绑定，而分镜视图的
    //   `ctx.pid` 在向导页是 `null` → `'/playlet/review/' + null` 拼出 `.../null`
    //   → 后端 404「项目不存在：null」，界面报"后端未连通"，查了很久。
    //   ⇒ 视图自己的委托要带**作用域**：`'[data-stepper="wizard"] [data-step]'`。
    //   ⇒ `_tools/check-delegate-conflicts.js` 会扫这类冲突（含"有没有加作用域"）。
    //   （下面四行的旧注释是"选择器互不冲突，重复项结果收敛"—— 那句话是错的。）
    global.PlayletList.bind(app);
    global.Wizard.bind(app);
    global.Storyboard.bind(app);
    global.Placeholders.bind(app);

    // ★ `[data-nav]` 收成**唯一一个** handler（此前 playlet-list 与 wizard 各绑一份，
    //   而 wizard 那份把 `data-nav`（键名）**当路径用** → 侧栏点"灵感"跳到 `#inspiration`
    //   落在 notFound；且它的绑定顺序在后，会**覆盖**正确的那次跳转）。
    //   解析顺序：`data-route`（显式路径）→ `NAV_ITEMS` 键名 → 特例 `list` → 否则忽略。
    D.delegate(app, 'click', '[data-nav]', function (e, t) {
      const route = t.getAttribute('data-route');
      if (route) { R.go(route); return; }
      const key = t.getAttribute('data-nav');
      if (key === 'list') { R.go('/playlet/list'); return; }
      const hit = (global.NAV_ITEMS || []).find(function (n) { return n.key === key; });
      if (hit && hit.route) { R.go(hit.route); return; }
      console.warn('[app] 未知的 data-nav：' + key + '（既无 data-route 也不是已知导航键）');
    });

    // 顶部栏右侧 & 设置
    D.delegate(app, 'click', '[data-action="account"]', function () { accountMenu(); });
    D.delegate(app, 'click', '[data-action="settings"]', function () { settingsModal(); });

    // 路由切换后复位滚动
    document.addEventListener('route:change', function () {
      if (window.scrollY > 0) window.scrollTo({ top: 0 });
    });

    // 快捷键：Esc 关闭弹层
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        D.$$('.modal-mask').forEach(function (m) { m.remove(); });
        D.$$('.menu').forEach(function (m) { m.remove(); });
      }
    });
  }

  /**
   * 步级「逐步人工确认」的**全局轮询**（2026-09-18）。
   *
   * 为什么是全局轮询、而不是"点生成分镜时才开始看"：链路在**后台**跑，
   * 人很可能已经翻到别的页面去了 —— 挂起时**必须**还能看见按钮。
   * 否则界面只会显示"跑了很久还没完"，而真相是**它在等人点东西**（死等）。
   *
   * 三条细节：
   *  · 只在 `http` 驱动下请求；`local` 驱动没有链路，顺带清掉可能残留的条
   *  · **单飞**：上一次查询没回来就跳过这一轮，避免请求叠加
   *  · 查询**失败不动**现有条 —— 网络抖一下不等于"链路不等了"
   */
  function mountHitlWatch() {
    if (!global.HitlBar || !global.Api) return;
    let inFlight = false;
    let gonePid = '';     // 服务端已明确说过"这个项目不存在"的 pid（见下面的 404 分支）
    function tick() {
      if (inFlight) return;
      const A = global.Api;
      if (A.driver !== 'http') { global.HitlBar.remove(); return; }
      const pid = ((R.current || {}).params || {}).pid;
      if (!pid) { global.HitlBar.remove(); return; }   // 列表/占位页没有项目上下文
      if (pid === gonePid) { global.HitlBar.remove(); return; }
      inFlight = true;
      A.getHitl(pid).then(function (st) {
        inFlight = false;
        global.HitlBar.update(st, pid);
      }, function (err) {
        inFlight = false;
        // ★★ 404 = 服务端**明确**说"这个项目不存在"（被删 / 被移走）⇒ **停手**。
        //
        //   实测 bug（2026-09-19，控制台抓到）：项目被移走后，这个 5 秒轮询
        //   **永久刷 404**（实测连续 11 条、每 5 秒一条，一直不停），而且
        //   **完全静默** —— 用户既不知道项目没了，也不知道有人在后台空转。
        //   既违反"失败必须可见"，也是白耗。
        //   ⇒ 记下这个 pid 不再打，并**说一句**让人知道。
        if (err && err.status === 404) {
          gonePid = pid;
          global.HitlBar.remove();
          try {
            global.Store.toast('这个项目已不存在（可能被删除或移走），已停止轮询确认状态');
          } catch (e) { /* toast 不可用也不该再抛 */ }
          return;
        }
        // 其它失败（网络抖一下）**不动**现有条：那不等于"链路不等了"，下轮再试
      });
    }
    // 路由一变立刻对一次，别让上一个项目的条挂满 5 秒
    document.addEventListener('route:change', function () { setTimeout(tick, 0); });
    // ★ **回到标签页时立刻对一次**（2026-09-18 验收实测）：
    //   后台标签页会被 Chrome 强力节流 —— 实测把 `setInterval` 压到"很久才跑一次"，
    //   于是条子的出现/消失会**延迟很久**（看着像坏了，其实只是没轮到）。
    //   这里补一个可见性回调，切回来即刷新，不必等下一次 interval。
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'visible') setTimeout(tick, 0);
    });
    tick();
    setInterval(tick, 5000);
  }

  function accountMenu() {
    const u = S.state.user;
    const mask = D.el('div', { class: 'modal-mask' });
    mask.innerHTML = '<div class="modal" style="width:420px"><h3>账号</h3>' +
      '<div class="modal-body">' +
      '<div class="info-field"><span class="k">用户</span><span class="v small">' + D.esc(u.name) + '</span></div>' +
      '<div class="info-field"><span class="k">算力点</span><span class="v small">' + u.credits + '</span></div>' +
      '<div class="info-field"><span class="k">数据位置</span><span class="v small">浏览器 localStorage（key: pavo-offline:v1）</span></div>' +
      '<div class="muted small">离线版没有真实账号体系；所有数据仅存于本机浏览器。</div>' +
      '</div>' +
      '<div class="modal-foot">' +
      '<button type="button" class="btn btn--sm" data-m="export">导出全部数据</button>' +
      '<button type="button" class="btn btn--primary btn--sm" data-m="close">关闭</button></div></div>';
    document.body.appendChild(mask);
    mask.addEventListener('click', function (e) {
      const b = e.target.closest('[data-m]');
      if (e.target === mask || (b && b.getAttribute('data-m') === 'close')) return mask.remove();
      if (b && b.getAttribute('data-m') === 'export') S.exportAll();
    });
  }

  function settingsModal() {
    const A = global.Api;
    const mask = D.el('div', { class: 'modal-mask' });
    const isHttp = A && A.driver === 'http';
    mask.innerHTML = '<div class="modal" style="width:520px"><h3>设置</h3>' +
      '<div class="modal-body">' +

      /* ---------- 后端连接（http 驱动；见 core/api.js 与 core/store.js 的水合） ---------- */
      '<div class="info-field"><span class="k">数据来源</span>' +
      '<span class="v small">' + (isHttp
        ? '后端（' + D.esc(A.baseUrl || '(未填地址)') + '）'
        : '浏览器本地 localStorage') + '</span></div>' +
      '<div class="row gap12">' +
      '<button type="button" class="btn btn--sm' + (isHttp ? '' : ' btn--primary') +
      '" data-m="driver-local">本地离线</button>' +
      '<button type="button" class="btn btn--sm' + (isHttp ? ' btn--primary' : '') +
      '" data-m="driver-http">连后端</button>' +
      '</div>' +
      '<div class="row gap12" style="align-items:center">' +
      '<input class="episode-name-input" data-m="base" style="flex:1" ' +
      'placeholder="http://127.0.0.1:8787" value="' + D.esc(A ? (A.baseUrl || '') : '') + '">' +
      '<button type="button" class="btn btn--sm" data-m="ping">测试连接</button>' +
      '</div>' +
      '<div class="muted small" data-m="ping-out">' + (isHttp
        ? '点「测试连接」验证后端是否可达。'
        : '离线模式：数据仅存本机浏览器。') + '</div>' +

      '<div class="info-field" style="margin-top:16px"><span class="k">数据操作</span>' +
      '<span class="v small">导出备份可以让整份工作台状态随文件迁移；重置会恢复为出厂种子数据。</span></div>' +
      '<div class="row gap12">' +
      '<button type="button" class="btn btn--sm" data-m="export">导出全部数据</button>' +
      '<button type="button" class="btn btn--sm" data-m="import">导入数据</button>' +
      '</div>' +
      '<div class="row gap12">' +
      '<button type="button" class="btn btn--sm" style="color:var(--danger);border-color:var(--danger)" data-m="reset">重置为出厂数据</button>' +
      '</div>' +
      '</div>' +
      '<div class="modal-foot"><button type="button" class="btn btn--primary btn--sm" data-m="close">关闭</button></div></div>';
    document.body.appendChild(mask);
    mask.addEventListener('click', function (e) {
      const b = e.target.closest('[data-m]'); if (!b) { if (e.target === mask) mask.remove(); return; }
      const k = b.getAttribute('data-m');

      if (k === 'close') return mask.remove();
      if (k === 'driver-local' || k === 'driver-http') {
        if (!A) return;
        A.setDriver(k === 'driver-http' ? 'http' : 'local');
        const inp = mask.querySelector('[data-m="base"]');
        if (inp && inp.value.trim()) A.setBaseUrl(inp.value.trim());
        S.toast(k === 'driver-http' ? '已切换到后端数据' : '已切换到本地数据', 'ok');
        mask.remove();
        render();                       // 切驱动要重画（render 内部会按需水合）
        return;
      }
      if (k === 'ping') {
        if (!A) return;
        const inp = mask.querySelector('[data-m="base"]');
        if (inp && inp.value.trim()) A.setBaseUrl(inp.value.trim());
        const out = mask.querySelector('[data-m="ping-out"]');
        if (out) out.textContent = '测试中…';
        A.ping().then(function (r) {
          if (!out) return;
          out.textContent = r.ok
            ? '✓ 已连通：项目 ' + (r.projects != null ? r.projects : '?') +
              ' 个 · 类型包 ' + ((r.packs || []).join(' / ') || '—')
            : '✗ 连不上：' + (r.error || '未知错误');
        });
        return;
      }
      if (k === 'export') return S.exportAll();
      if (k === 'import') {
        const inp = document.createElement('input'); inp.type = 'file'; inp.accept = '.json';
        inp.onchange = function () {
          const f = inp.files[0]; if (!f) return;
          const rd = new FileReader();
          rd.onload = function () {
            try { S.importJSON(String(rd.result)); S.toast('导入成功', 'ok'); mask.remove(); render(); }
            catch (err) { S.toast('导入失败：' + err.message); }
          };
          rd.readAsText(f, 'utf-8');
        };
        return inp.click();
      }
      if (k === 'reset') {
        if (confirm('确定重置？当前所有本地改动都会丢失。')) { S.reset(); mask.remove(); R.go('/playlet/list'); render(); S.toast('已重置'); }
      }
    });
  }

  // 全局错误捕获（诊断用）：window.__APPERR
  window.__APPERR = [];
  window.addEventListener('error', function (e) { window.__APPERR.push('error: ' + e.message + ' @' + (e.filename || '').split('/').pop() + ':' + e.lineno); });
  window.addEventListener('unhandledrejection', function (e) { window.__APPERR.push('rejection: ' + ((e.reason && e.reason.message) || e.reason)); });

  function boot() {
    S.load();
    R.add('/playlet/list', function (params, query) { render(); });
    R.add('/playlet/review/:pid', function (params, query) { global.Wizard.resetUi(); render(); });
    R.add('/playlet/review/:pid/episode/:eid', function (params, query) { global.Storyboard.reset(); render(); });
    R.add('/inspiration', render);
    R.add('/canvas', render);
    R.add('/chat', render);
    R.add('/visuals', render);
    R.setNotFound(function (path) { render(); });

    bindGlobal();

    // ★ 外壳自动隐藏（用户要求：顶栏/侧栏平时收起，指针靠近才弹出）——
    //   那两处目前几乎都是占位符，常驻一直占屏。见 `Shell.mountAutoHide` 的说明。
    if (global.Shell && global.Shell.mountAutoHide) global.Shell.mountAutoHide();

    // ★ 步级「逐步人工确认」：全局看着"链路是不是正停在某一步等人"，
    //   是就挂一条常驻确认条（挂 body，不随视图重渲染消失）。
    mountHitlWatch();

    // ★ **同源自检**（`Api.autodetect`）：本页若就是 shim 端出来的（`--web-root`），
    //   自动切 http + 相对 baseUrl —— 打开就能用，**不用再手配 driver/baseUrl**。
    //   ★ 必须放在 `R.start()` **之前**：否则会先用本地种子数据画一帧再切，
    //     用户会看到一闪而过的**假项目**。
    const go = function () {
      R.start();
      global.__pavo = { render: render, store: S, router: R };
    };
    if (global.Api && global.Api.autodetect) {
      global.Api.autodetect().then(go, go);         // 自检失败也照常启动
    } else {
      go();
    }
  }

  global.App = { render: render, renderNotFound: renderNotFound, boot: boot };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})(window);
