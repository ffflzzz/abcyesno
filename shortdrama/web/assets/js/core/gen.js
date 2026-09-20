/* ==========================================================================
   core/gen.js —— 本地确定性生成层（离线替代线上 AI 出图/出片）
   设计原则：
     1. 同一输入必须得到同一输出（纯函数式散列，无随机）——「离线可复现」。
     2. 不依赖网络与后端，仅用 Canvas 2D 合成，产出 dataURL 直接落入本地数据层。
     3. 输出带可辨识的视觉分区与文字标注，便于人眼核对「哪一镜对应哪张图」。
   ========================================================================== */
(function (global) {
  'use strict';

  /** FNV-1a 32 位散列 —— 稳定、快、无依赖 */
  function hash(str) {
    let h = 0x811c9dc5;
    const s = String(str || '');
    for (let i = 0; i < s.length; i++) {
      h ^= s.charCodeAt(i);
      h = (h + ((h << 1) + (h << 4) + (h << 7) + (h << 8) + (h << 24))) >>> 0;
    }
    return h >>> 0;
  }

  /** 由散列派生一个伪随机序列（线性同余，确定性） */
  function rng(seed) {
    let x = seed || 1;
    return function () {
      x = (x * 1664525 + 1013904223) >>> 0;
      return x / 4294967296;
    };
  }

  /** HSL 色相：根据种子在受控区间取值，避免刺眼配色 */
  function palette(seed) {
    const r = rng(seed);
    const base = Math.floor(r() * 360);
    return {
      h1: base,
      h2: (base + 28 + Math.floor(r() * 40)) % 360,
      h3: (base + 200 + Math.floor(r() * 60)) % 360,
      sat: 22 + Math.floor(r() * 26),
      light: 12 + Math.floor(r() * 14),
      accent: 40 + Math.floor(r() * 25),
    };
  }

  function ratioSize(ratio) {
    if (ratio === '16:9') return [960, 540];
    if (ratio === '4:3') return [880, 660];
    if (ratio === '1:1') return [768, 768];
    return [576, 1024]; // 9:16 默认
  }

  /**
   * 生成一张确定性占位关键帧
   * @param {{prompt?:string,title?:string,ratio?:string,index?:number,seedKey?:string}} o
   * @returns {string} dataURL (image/jpeg)
   */
  function keyframe(o) {
    const opt = o || {};
    const seed = hash((opt.seedKey || '') + '|' + (opt.prompt || '') + '|' + (opt.title || ''));
    const [W, H] = ratioSize(opt.ratio);
    const cv = document.createElement('canvas');
    cv.width = W; cv.height = H;
    const ctx = cv.getContext('2d');
    const p = palette(seed);
    const r = rng(seed);

    // 1) 背景渐变
    const g = ctx.createLinearGradient(0, 0, W * (0.4 + r() * 0.6), H);
    g.addColorStop(0, 'hsl(' + p.h1 + ',' + p.sat + '%,' + (p.light + 8) + '%)');
    g.addColorStop(0.55, 'hsl(' + p.h2 + ',' + p.sat + '%,' + p.light + '%)');
    g.addColorStop(1, 'hsl(' + p.h3 + ',' + (p.sat - 6) + '%,' + Math.max(6, p.light - 6) + '%)');
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, W, H);

    // 2) 光斑
    for (let i = 0; i < 4; i++) {
      const cx = r() * W, cy = r() * H * 0.8, rad = W * (0.18 + r() * 0.3);
      const rg = ctx.createRadialGradient(cx, cy, 0, cx, cy, rad);
      rg.addColorStop(0, 'hsla(' + p.h3 + ',70%,70%,' + (0.06 + r() * 0.1).toFixed(3) + ')');
      rg.addColorStop(1, 'hsla(' + p.h3 + ',70%,70%,0)');
      ctx.fillStyle = rg;
      ctx.fillRect(0, 0, W, H);
    }

    // 3) 垂直构图块：模拟主体剪影
    const blockCount = 3 + Math.floor(r() * 4);
    for (let i = 0; i < blockCount; i++) {
      const bw = W * (0.08 + r() * 0.2);
      const bh = H * (0.22 + r() * 0.5);
      const bx = r() * (W - bw);
      const by = H - bh - r() * H * 0.18;
      ctx.fillStyle = 'hsla(' + p.h1 + ',' + (p.sat + 8) + '%,' + Math.max(4, p.light - 8) + '%,' + (0.55 + r() * 0.3).toFixed(2) + ')';
      ctx.fillRect(bx, by, bw, bh);
      if (r() > 0.55) {
        ctx.beginPath();
        ctx.arc(bx + bw / 2, by - bw * 0.22, bw * 0.22, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    // 4) 地面反射
    const fg = ctx.createLinearGradient(0, H * 0.72, 0, H);
    fg.addColorStop(0, 'hsla(' + p.h2 + ',40%,6%,0)');
    fg.addColorStop(1, 'hsla(' + p.h2 + ',40%,4%,0.75)');
    ctx.fillStyle = fg;
    ctx.fillRect(0, H * 0.72, W, H * 0.28);

    // 5) 颗粒感（确定性）
    const grain = ctx.getImageData(0, 0, W, H);
    const data = grain.data;
    const gr = rng(seed ^ 0x9e3779b9);
    for (let i = 0; i < data.length; i += 4) {
      const n = (gr() - 0.5) * 16;
      data[i] = Math.max(0, Math.min(255, data[i] + n));
      data[i + 1] = Math.max(0, Math.min(255, data[i + 1] + n));
      data[i + 2] = Math.max(0, Math.min(255, data[i + 2] + n));
    }
    ctx.putImageData(grain, 0, 0);

    // 6) 暗角
    const vg = ctx.createRadialGradient(W / 2, H / 2, Math.min(W, H) * 0.28, W / 2, H / 2, Math.max(W, H) * 0.75);
    vg.addColorStop(0, 'rgba(0,0,0,0)');
    vg.addColorStop(1, 'rgba(0,0,0,0.45)');
    ctx.fillStyle = vg;
    ctx.fillRect(0, 0, W, H);

    // 7) 标注层（说明这是本地占位产物，便于人工核对）
    const pad = Math.round(W * 0.045);
    ctx.fillStyle = 'rgba(0,0,0,0.42)';
    ctx.fillRect(0, H - pad * 3.4, W, pad * 3.4);
    ctx.fillStyle = 'rgba(255,255,255,0.94)';
    ctx.font = '600 ' + Math.round(W * 0.042) + 'px ' + '-apple-system, "PingFang SC", "Microsoft YaHei", sans-serif';
    ctx.textBaseline = 'middle';
    const label = (opt.index != null ? '镜头 ' + String(opt.index).padStart(2, '0') + '　' : '') + (opt.title || '未命名');
    ctx.fillText(label.length > 18 ? label.slice(0, 18) + '…' : label, pad, H - pad * 2.4);
    ctx.fillStyle = 'rgba(255,255,255,0.62)';
    ctx.font = '400 ' + Math.round(W * 0.028) + 'px ' + '-apple-system, "PingFang SC", sans-serif';
    ctx.fillText('LOCAL PLACEHOLDER · ' + opt.ratio + ' · #' + seed.toString(16).slice(0, 6), pad, H - pad * 1.05);

    return cv.toDataURL('image/jpeg', 0.82);
  }

  /**
   * 模拟一次带进度的异步生成（离线：纯延时 + 本地合成，无网络）
   * @param {{onProgress?:function, ms?:number}} opts
   * @returns {Promise<void>}
   */
  function simulate(opts) {
    // 离线 mock 不依赖真实时间：
    //  1) requestAnimationFrame 在后台标签页不触发，Promise 永不 resolve（实测踩坑）；
    //  2) setTimeout 在后台标签页被 Chrome 强力节流（隐藏>5min 后约 1 次/分钟），
    //     逐项循环的批量生成会被拖到几分钟（实测踩坑）。
    //  → 用微任务立即完成；「生成中」的观感由 UI 的 busy 状态（spinner）承担。
    const o = opts || {};
    return Promise.resolve().then(function () { if (o.onProgress) o.onProgress(1); });
  }

  global.Gen = { hash, keyframe, simulate, ratioSize };
})(window);
