/* ==========================================================================
   core/vendor.js —— 模型厂商选择器（2026-09-18）

   为什么抽成共享模块：**分镜页与资产页需要完全同一套逻辑**
   （选项来自后端 / 无厂商可选时禁用并说明 / 选择随生成请求发出）。
   复制两份必然漂移 —— 本项目最贵的一类 bug（「同一判据绝不写两份」）。

   为什么选项必须**来自后端**：厂商由 `v5/media/vendors.py` 的 `register()` 决定，
   清单走 `GET /v1/pixa/short-drama/vendors` 水合进 `Store.state.vendors`。
   前端写死一份列表，公司机 `register("comfy", …)` 之后前端就**看不到它**——
   与类型包同一条教训（v5 加第 4 个包时，硬编列表把它静默拒了）。

   为什么选择**不落 localStorage**：厂商列表是派生数据（见 `store.setVendors`），
   落盘的选择在换后端 / 换机器后会指向一个不存在的厂商 ⇒ 静默偏差。
   ========================================================================== */
(function (global) {
  'use strict';
  const D = global.D, S = global.Store, Api = global.Api;

  /** 本会话里用户选过的厂商，按能力分开。空 = 还没选 ⇒ 用后端缺省。 */
  const chosen = { image: '', video: '' };

  function label(kind) { return kind === 'video' ? '视频' : '图片'; }

  /** 该能力的厂商信息（列表 / 后端缺省 / 缺省是否合法）。 */
  function info(kind) {
    const v = (S.state && S.state.vendors) || null;
    return {
      kind: kind,
      items: (v && v.items) || [],
      current: (v && v.current && v.current[kind]) || '',
      ok: !!(v && v.current_ok && v.current_ok[kind]),
    };
  }

  function has(kind) { return info(kind).items.length > 0; }

  /** 当前生效的厂商：用户选的 > 后端缺省 > 第一项；无可选项 ⇒ ''。 */
  function pick(kind) {
    const cs = info(kind).items.map(function (x) { return x.code; });
    if (chosen[kind] && cs.indexOf(chosen[kind]) >= 0) return chosen[kind];
    const cur = info(kind).current;
    if (cur && cs.indexOf(cur) >= 0) return cur;
    return cs.length ? cs[0] : '';
  }

  /** 请求体里该带的厂商字段。无可选项 ⇒ **不带**，让后端用缺省。 */
  function payload(kind) {
    const c = pick(kind);
    if (!c) return {};
    const o = {};
    o[kind + '_vendor'] = c;
    return o;
  }

  /** 渲染选择器。**没有可选项时禁用并说明**，不假装它能用。 */
  function select(kind, opts) {
    const o = opts || {};
    const cls = o.cls || 'vendor-sel';
    const isHttp = !!(Api && Api.driver === 'http');
    const inf = info(kind);
    if (!inf.items.length) {
      const why = isHttp ? '厂商列表未取到' : '离线模式（不调模型）';
      return '<span class="' + cls + ' is-disabled" title="' +
        D.esc(why + ' ⇒ 厂商选择不可用') + '">' + D.esc(why) + ' ▾</span>';
    }
    const cur = pick(kind);
    const tip = (o.tip || ('本次生成使用的' + label(kind) + '厂商'))
      + (inf.ok ? '' : '（⚠️ 后端缺省「' + inf.current + '」未注册，生成会返回 400）');
    return '<select class="' + cls + '" data-vendor="' + kind + '"'
      + ' title="' + D.esc(tip) + '"' + (inf.ok ? '' : ' data-stale="1"') + '>'
      + inf.items.map(function (x) {
        return '<option value="' + D.esc(x.code) + '"' + (x.code === cur ? ' selected' : '') + '>'
          + D.esc(x.short || x.name || x.code) + '</option>';
      }).join('') + '</select>';
  }

  /** `change` 的处理体：**只写内存状态**（`<select>` 自己持有选中值，不必重渲染）。 */
  function onChange(target) {
    const kind = target.getAttribute('data-vendor') === 'video' ? 'video' : 'image';
    chosen[kind] = target.value || '';
    // 明确回显，避免"选了但不知道生效没"（本项目的「生效/失败必须可见」）
    if (S && S.toast) {
      const hit = info(kind).items.filter(function (x) { return x.code === chosen[kind]; })[0];
      S.toast('本次' + label(kind) + '生成将使用：'
              + ((hit && (hit.short || hit.name)) || chosen[kind]));
    }
    return chosen[kind];
  }

  /** 各视图在 `bind()` 里调一次即可。
   *  选择器 `select[data-vendor]` **自带唯一作用域** —— 委托是绑在共享 `#app` 上
   *  且永久生效的，选择器不唯一会"点一次两个视图都跑"（有实测事故）。 */
  function bind(root) {
    D.delegate(root, 'change', 'select[data-vendor]', function (e, t) { onChange(t); });
  }

  function reset() { chosen.image = ''; chosen.video = ''; }

  global.Vendor = {
    label: label, info: info, has: has, pick: pick, payload: payload,
    select: select, onChange: onChange, bind: bind, reset: reset,
  };
})(window);
