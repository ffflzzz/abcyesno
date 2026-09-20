/* ==========================================================================
   views/asset-drawer.js —— 角色/场景/道具信息面板（全屏抽屉）

   复刻线上点击资产形象后打开的抽屉（实测 DOM 见 _recon/_flows/W2_state_click/）
   结构：
     左栏  名称 / 形象名称 / 声音 / 音色描述 / 应用到所有形象 / 剧集 / 描述 / [保存]
     右栏  单图 + 四视图，各带 [生成][+]
   契约：保存 → Api.saveAssetState(pid, kind, assetId, body)

   ★★ 2026-09-17 两处重要改动
   ─────────────────────────────────────────────────────────────────────────
   ① **支持"新建"模式**：`open({pid, kind, assetId: null})`。
      用户要求「点『＋ 添加』打开的就是这个抽屉」（此前是个两字段小弹窗）。
      新建时 `保存` 走 `Api.addAsset`（**真落盘**），不是本地造一条。

   ② **v5 没有语义的控件一律 `disabled` + 写明原因**，并在顶部挂一条横幅说明。
      ⛔ 不能摆一堆"点了没反应"的控件假装功能齐全 —— 那正是本项目最忌的
      「静默无效」。逐项对照：
        · 声音 / 音色描述 —— v5 **没有** per-asset 音色（音频口径在 `brief.audio_mode`，项目级）
        · 应用到所有形象 —— v5 每个资产**只有一份形象**，没有"多形象"可应用
        · 剧集         —— v5 的资产是**项目级**的，不分集
        · 四视图        —— v5 **没有**四视图概念（一张参考图）
        · 生成         —— v5 **没有单资产重生成**；这里真的调后端，但作用域是
                          「本类全部资产」（`POST /assets/batch-generate-image`）
      `state_name` 也会被送达，但 `webwrite.save_asset_state` 会如实回一张
      `ignored` 清单（前端把它显示出来）。
   ========================================================================== */
(function (global) {
  'use strict';
  const D = global.D, I = global.Icons, S = global.Store, Api = global.Api, Ov = global.Overlay,
        Vendor = global.Vendor;

  /** v5 暂无对应语义的控件 → 统一渲染成 disabled + 说明，避免"点了没反应"。 */
  const NO_V5 = {
    voice: 'v5 没有单资产音色（音频口径是项目级的 brief.audio_mode）',
    voice_desc: '同上：音色描述 v5 无落点',
    apply_all: 'v5 每个资产只有一份形象，没有"多形象"可应用',
    episode: 'v5 的资产是项目级的，不分集',
    fourview: 'v5 没有四视图概念（只产出一张参考图）',
  };

  function open(opts) {
    const o = opts || {};
    const pid = o.pid, kind = o.kind, assetId = o.assetId;
    const p = S.getProject(pid); if (!p) return;
    const bucket = kind === 'character' ? 'characters' : kind === 'scene' ? 'scenes' : 'props';
    const asset = assetId
      ? (p.assets[bucket] || []).find(function (x) { return x.id === assetId; })
      : null;
    const isNew = !asset;                     // ★ 新建模式
    if (!isNew && !asset) return;
    const state = isNew
      ? { state_name: '基础形象', image: '', ref_id: '' }
      : ((o.refId ? asset.states.find(function (s) { return s.ref_id === o.refId; }) : null)
         || asset.states[0] || { state_name: '基础形象', image: '' });
    const label = kind === 'character' ? '角色' : kind === 'scene' ? '场景' : '道具';

    const dis = function (reason) {
      return ' disabled title="' + D.esc(reason) + '（v5 暂无此语义）"';
    };
    const disField = function (name, ctrl, reason) {
      return '<div class="ad-field ad-field--na" title="' + D.esc(reason) + '（v5 暂无此语义）">' +
        '<label>' + D.esc(name) + '</label>' + ctrl + '</div>';
    };

    const draw = Ov.drawer({
      body: '<div class="ov-drawer-head">' +
        '<button type="button" class="icon-btn" data-ov="close">' + I.back(22) + '</button>' +
        '<span style="font-size:15px;font-weight:500">' + D.esc(label) + '信息' +
        (isNew ? ' · 新增' : '') + '</span>' +
        '</div>' +
        '<div class="ov-drawer-body">' +
        /* 左栏 */
        '<aside class="ad-side">' +
        '<div class="ad-tabs"><button type="button" class="ad-tab is-active">' +
        D.esc(state.state_name || '基础形象') + '</button></div>' +
        '<div class="ad-form">' +
        field('名称', '<input class="ad-input" data-f="name" value="' + D.esc(asset ? asset.name : '') +
              '" placeholder="例如：' + (kind === 'character' ? '纸扎匠' : kind === 'scene' ? '纸扎铺' : '十个纸人') + '">') +
        field('形象名称', '<input class="ad-input" data-f="state_name" value="' +
              D.esc(state.state_name || '基础形象') + '"' + dis(NO_V5.apply_all) + '>') +
        (kind === 'character'
          ? disField('声音', '<button type="button" class="ad-select" disabled>AI 自动适配 <span class="ov-menu-right">›</span></button>', NO_V5.voice)
          + disField('音色描述', '<textarea class="ad-area" rows="3" placeholder="v5 暂不支持" disabled></textarea>', NO_V5.voice_desc)
          : '') +
        '<label class="ad-check ad-field--na" title="' + D.esc(NO_V5.apply_all) + '（v5 暂无此语义）">' +
        '<input type="checkbox" data-f="apply_all" disabled> 应用到所有形象</label>' +
        disField('剧集', '<input class="ad-input" value="全部（项目级）" disabled>', NO_V5.episode) +
        field('描述 <span class="muted small">（会注入每一镜提示词）</span>',
          '<textarea class="ad-area" data-f="description" rows="9" ' +
          'placeholder="写清外观：脸型/发型/服装/材质。此文案会进每一镜提示词；' +
          '留空则模型每镜自己编长相，同一个人会长成 18 个人。">' +
          D.esc(state.identity || state.description || '') + '</textarea>' +
          '<div class="ad-count"><span data-c="desc">' +
          String((state.identity || state.description || '')).length + '</span> 字</div>') +
        '<button type="button" class="btn btn--primary btn--sm ad-save" data-act="save">' +
        (isNew ? '创建' : '保存') + '</button>' +
        '<div class="ad-note" id="ad-note"></div>' +
        '</div></aside>' +
        /* 右栏 */
        '<main class="ad-main">' +
        '<div class="ad-title">' + D.esc(asset ? asset.name : '未命名') + ' · ' +
        D.esc(state.state_name || '基础形象') + '</div>' +
        '<div class="ad-na-banner">' +
        '<span>右栏在 v5 侧只产出一张参考图；<b>四视图</b>没有对应语义（已置灰）。' +
        '该项目的资产图由「生成全部图片」统一产出 —— v5 <b>没有单资产重生成</b>。</span></div>' +
        '<div class="ad-grid">' +
        genPane('main_image', '参考图', state.image, false) +
        genPane('fourview_image', '四视图（v5 暂无）', '', true) +
        '</div></main>' +
        '</div>',
      onClose: o.onClose,
    });

    const q = function (sel) { return draw.root.querySelector(sel); };
    const g = function (s) {
      const n = q('[data-f="' + s + '"]');
      return n ? (n.type === 'checkbox' ? n.checked : n.value) : '';
    };
    const desc = q('[data-f="description"]');
    if (desc) desc.addEventListener('input', function () {
      q('[data-c="desc"]').textContent = String(desc.value.length);
    });

    // 厂商选择器（2026-09-18）：处理体在 `core/vendor.js`（与分镜页**同一套**）。
    // 选择器 `select[data-vendor]` 自带唯一作用域 —— 委托绑在共享 `#app` 上且永久生效。
    Vendor.bind(draw.root);

    /* ---------- 生成参考图：**真打后端**（作用域是本类全部资产） ---------- */
    draw.root.addEventListener('click', function (e) {
      const b = e.target.closest('[data-gen]'); if (!b) return;
      const which = b.getAttribute('data-gen');
      if (which === 'fourview_image') { S.toast(NO_V5.fourview); return; }
      if (isNew) { S.toast('先「创建」再生成图片'); return; }
      const ph = q('[data-pane="' + which + '"] .ad-ph');
      busy(ph, '提交生成任务…');
      // v5 没有"单资产重生成"：这个端点生成的是**本类全部资产**（如实告知）
      const vCode = Vendor.pick('image');
      S.toast('已提交「' + label + '」图片生成（作用于本类全部资产）'
              + (vCode ? '，厂商：' + vCode : '') + '…');
      Api.runTask(
        function () { return Api.generateAssetImages(pid, [kind], Vendor.payload('image')); },
        {
          onTick: function (st) { busy(ph, '生成中… ' + ((st && st.status) || 'running')); },
          refresh: function () {
            return Api.hydrate({ path: '/playlet/review/:pid', params: { pid: pid } });
          },
        }
      ).then(function (st) {
        global.App.render();
        if (st.status === 'ok') S.toast('生成完成，已刷新', 'ok');
        else S.toast('生成未成功（' + st.status + '）'
                     + ((st.result && st.result.reason) ? '：' + st.result.reason : ''));
      }).catch(function (err) {
        S.toast('生成失败：' + (err && err.message ? err.message : err));
      });
    });

    /* ---------- 保存 ---------- */
    draw.root.addEventListener('click', function (e) {
      if (!e.target.closest('[data-act="save"]')) return;
      const name = String(g('name') || '').trim();
      if (!name) { S.toast('请填写名称'); return; }
      const descText = String(g('description') || '').trim();

      if (isNew) {
        // ★ **先建资产**（真落盘：POST /projects/{pid}/assets），identity 一起带上
        Api.addAsset(pid, kind, name, { identity: descText }).then(function () {
          S.toast('已创建「' + name + '」', 'ok');
          draw.close();
          return Api.hydrate({ path: '/playlet/review/:pid', params: { pid: pid } })
            .then(function () { global.App.render(); });
        }).catch(function (err) { S.toast('创建失败：' + (err && err.message ? err.message : err)); });
        return;
      }

      const body = {
        name: name, description: descText,
        state_name: g('state_name') || '基础形象',
        // 下面这些 v5 没有对应语义 —— 仍送过去，让后端**如实回一张 ignored 清单**
        voice_mode: 'ai_auto', voice_description: '',
        style_id: (p.style && p.style.style_id) || '', model_code: 'agnes-image',
        fourview_image: { url: (state.fourview_image || ''), thumbnail_url: '' },
      };
      Api.saveAssetState(pid, kind, assetId, body).then(function (r) {
        const d = r || {};
        const applied = Object.keys(d.applied || {});
        const ignored = d.ignored || [];
        S.toast('已保存' + (applied.length ? '：' + applied.join('、') : '') +
                (ignored.length ? '（v5 忽略了 ' + ignored.length + ' 个字段）' : ''), 'ok');
        draw.close();
        return Api.hydrate({ path: '/playlet/review/:pid', params: { pid: pid } })
          .then(function () { global.App.render(); });
      }).catch(function (err) { S.toast('保存失败：' + (err && err.message ? err.message : err)); });
    });

    return draw;
  }

  function busy(ph, text) {
    if (!ph) return;
    ph.innerHTML = '<div class="spin" style="width:24px;height:24px;border:2px solid var(--border);' +
      'border-top-color:var(--accent);border-radius:50%;margin:0 auto 10px"></div>' + D.esc(text);
  }

  function field(k, ctrl) {
    return '<div class="ad-field"><label>' + k + '</label>' + ctrl + '</div>';
  }

  function genPane(key, label, img, na) {
    return '<section class="ad-pane' + (na ? ' ad-pane--na' : '') + '" data-pane="' + key + '">' +
      '<div class="ad-pane-head"><span class="ad-pane-title">' + D.esc(label) + '</span>' +
      (na ? '<span class="ad-na-tag">v5 暂无</span>' : '') + '</div>' +
      '<div class="ad-ph">' + (img
        ? '<img src="' + D.esc(img) + '" alt="">'
        : '<span class="muted small">' + (na ? 'v5 没有四视图概念' : '尚未生成') + '</span>') + '</div>' +
      '<div class="ad-pane-foot">' +
      /* 图片厂商选择器（2026-09-18）：摆在**生成按钮旁边** —— 它管的就是这次生成。
         资产图是图片 ⇒ 只可能用 image 厂商。控件与取值在 `core/vendor.js`（与分镜页共用）。 */
      (na ? '' : Vendor.select('image', { cls: 'ad-vendor' })) +
      '<button type="button" class="btn btn--primary btn--xs" data-gen="' + key + '"' +
      (na ? ' disabled title="v5 暂无此语义"' : '') + '>' + I.refresh(14) + ' 生成</button>' +
      (na ? '' : '<button type="button" class="icon-btn" disabled title="v5 的资产图由媒体链产出，不支持上传" >' + I.plus(18) + '</button>') +
      '</div>' +
      '<div class="ad-hist"><span class="muted small">' +
      (na ? '—' : (img ? '当前：' + (key === 'main_image' ? '参考图' : '图') + ' 1 张' : '暂无')) +
      '</span></div>' +
      '</section>';
  }

  global.AssetDrawer = { open: open };
})(window);
