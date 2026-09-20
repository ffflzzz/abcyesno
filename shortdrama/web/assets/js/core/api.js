/* ==========================================================================
   core/api.js —— 数据访问适配层（前端 ↔ 后端的唯一边界）
   --------------------------------------------------------------------------
   目的：把「前端怎么调数据」与「数据从哪来」彻底解耦，便于日后把本前端
        对齐到你自己的 new_deep_agent 后端。

   两个驱动：
     driver = 'local'  纯前端，数据落在 localStorage（当前默认，可离线跑通全流程）
     driver = 'http'   走 REST，路径与线上 /v1/pixa/short-drama/* 完全一致，
                       只需把 baseUrl 指向 new_deep_agent 的服务地址即可切换。

   约定：所有方法返回 Promise；参数/返回形状与线上接口保持一致。
   切换方式：Settings 里切换，或 localStorage.setItem('pavo.api.driver','http')
             localStorage.setItem('pavo.api.baseUrl','http://127.0.0.1:8000/api')
   --------------------------------------------------------------------------
   契约来源：对线上站点真实点击捕获（见 docs/FLOWS.md 与 _recon/_flows/）
   ========================================================================== */
(function (global) {
  'use strict';

  const LS_DRIVER = 'pavo.api.driver';
  const LS_BASE = 'pavo.api.baseUrl';

  function getDriver() { return localStorage.getItem(LS_DRIVER) === 'http' ? 'http' : 'local'; }
  function setDriver(d) { localStorage.setItem(LS_DRIVER, d === 'http' ? 'http' : 'local'); }
  function getBase() { return localStorage.getItem(LS_BASE) || ''; }
  function setBase(u) { localStorage.setItem(LS_BASE, u || ''); }

  /**
   * **同源自检**：如果本页就是由 shim 提供的（`--web-root`），自动切到 http 驱动
   * 并把 baseUrl 置空（走**相对路径**）—— 打开就能用，**零配置**。
   *
   * 判据是 `GET /health` 返回**本服务的信封**（`code === '000000'`）：
   *   · 由 shim 托管 → 命中 shim 的 `/health` ✓
   *   · 由前端静态服务（:5500）托管 → 那是静态服务，`/health` 404 → 不切
   *   · `file://` 打开 → fetch 直接失败 → 不切（仍可双击用离线模式）
   *
   * ⚠️ **不覆盖用户已显式选过的驱动**（`LS_DRIVER` 有值就尊重它）——
   *    否则想在本地模式下演示的人会被强行切走。
   *    但若同源已确认、而旧的 baseUrl 恰好指向**同一个 origin**，
   *    就把它归一成空串（这样换端口也不用重配）。
   */
  function autodetect() {
    if (typeof location === 'undefined' || location.protocol === 'file:') {
      return Promise.resolve(false);
    }
    // 已选过驱动：只在「同源 + baseUrl 指向同一 origin」时做一次归一化
    if (localStorage.getItem(LS_DRIVER)) {
      if (getDriver() !== 'http') return Promise.resolve(false);
      if (getBase().replace(/\/$/, '') === location.origin) { setBase(''); return Promise.resolve(true); }
      return Promise.resolve(false);
    }
    let tm = null;
    const timeout = new Promise(function (r) { tm = setTimeout(function () { r(null); }, 2500); });
    const probe = fetch('/health', { headers: { Accept: 'application/json' } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .catch(function () { return null; });
    return Promise.race([probe, timeout]).then(function (j) {
      if (tm) clearTimeout(tm);
      if (!j || j.code !== '000000' || !j.data) return false;
      setDriver('http');
      setBase('');                    // 同源 → 相对路径
      return true;
    });
  }

  /** 从分集标识里取集号：`<pid>-ep<N>` → N（取不到返回 0）。 */
  function _epOfEid(eid) {
    const m = /-ep(\d+)$/.exec(String(eid || ''));
    return m ? parseInt(m[1], 10) : 0;
  }

  /* ---------------------------------------------------------------- HTTP 驱动 */
  function http(method, path, body) {
    const url = getBase().replace(/\/$/, '') + path;
    const opt = { method: method, credentials: 'include', headers: {} };
    if (body !== undefined) {
      if (body instanceof FormData) { opt.body = body; }
      else { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
    }
    return fetch(url, opt).then(function (r) {
      return r.text().then(function (txt) {
        let json = null;
        try { json = txt ? JSON.parse(txt) : null; } catch (e) { json = null; }
        if (!r.ok || (json && json.code && json.code !== '000000')) {
          // ★★ **必须区分两种失败**（2026-09-17 实测踩到）：
          //   ① 后端**答复了**（有 HTTP 状态，如 404「项目不存在：null」）
          //   ② 根本**联系不上**（fetch 自身失败）
          //   原先两者都抛裸 `Error`，上层一律显示"后端未连通" ——
          //   于是后端明确回的 404 被说成"没连上"，**把排查方向带偏**。
          //   挂上 `status`/`url`/`fromBackend` 让上层能说对话。
          const err = new Error((json && json.message) || ('HTTP ' + r.status));
          err.status = r.status;
          err.url = url;
          err.fromBackend = true;
          throw err;
        }
        return json ? (json.data !== undefined ? json.data : json) : null;
      });
    }, function (e) {
      const err = new Error((e && e.message) ? e.message : 'fetch failed');
      err.isNetwork = true;            // fetch 自己失败 = 真的联系不上
      err.url = url;
      throw err;
    });
  }

  /* ---------------------------------------------------------------- Local 驱动 */
  // 直接复用 Store（Store 已实现与线上相同的字段结构）
  function local() { return global.Store; }

  /* ================================================================ API 面 */
  const Api = {
    get driver() { return getDriver(); },
    setDriver: setDriver,
    get baseUrl() { return getBase(); },
    setBaseUrl: setBase,
    /** 同源自检（零配置）：本页若由 shim 提供就自动切 http + 相对 baseUrl */
    autodetect: autodetect,

    /** 统一入口：切换驱动后无需改动任何调用方 */
    _local: function () { return local(); },

    /* ---------- 项目 ---------- */
    /**
     * 项目列表。`isDemo` 决定要哪一批 —— 线上就是靠这一个参数区分两个 tab
     * （逆向实证：`_recon/_flows/L3_featured/step.json` 记下了点击「精选项目」时
     *  **两次**请求，`is_demo=false` 与 `is_demo=true`）。
     *
     * ★ 2026-09-18 之前这里是**写死 `is_demo=false`** 的，于是「精选项目」tab
     * 永远拿不到数据（那时视图还额外用 `slice(0,0)` 直接短路，见 playlet-list.js）。
     *
     * local 驱动下 `isDemo=true` 返回**空数组**：本地模式没有服务端，
     * 也就没有"运营挑出来的精选"这回事。**返回空而不是回落成本地项目列表**
     * —— 后者会让用户以为自己收藏了精选（静默偏差）。
     */
    listProjects: function (page, pageSize, isDemo) {
      if (getDriver() === 'http') {
        return http('GET', '/v1/pixa/short-drama/projects?page=' + (page || 1)
          + '&page_size=' + (pageSize || 10)
          + '&is_demo=' + (isDemo === true ? 'true' : 'false'));
      }
      if (isDemo === true) return Promise.resolve([]);
      return Promise.resolve(local().allProjects());
    },
    getProgress: function (pid) {
      if (getDriver() === 'http') return http('GET', '/v1/pixa/short-drama/projects/' + pid + '/progress?include_content=true');
      const p = local().getProject(pid);
      return p ? Promise.resolve(p) : Promise.reject(new Error('project not found'));
    },
    createByPaste: function (name, content) {
      if (getDriver() === 'http') return http('POST', '/v1/pixa/short-drama/projects/paste', { name: name, content: content });
      const p = local().createProject({ name: name, source_type: 'paste_text', script: content });
      return Promise.resolve(p);
    },
    /** 线上三段式建项目的第三步：[剧本解析] → 创建项目并回填概要/拆资产 */
    createByParse: function (text, styleCode, styleName) {
      if (getDriver() === 'http') return http('POST', '/v1/pixa/short-drama/projects/paste', { content: text, style_code: styleCode });
      const p = local().createProject({ name: '未命名短剧', source_type: 'paste_text', script: text });
      const r = local().analyzeScript(p.id, styleCode, styleName);
      return Promise.resolve(Object.assign(p, { analyze: r }));
    },
    /** 第1步 [生成] → 「正在分析资产/识别场景元素」→ 拆出资产 */
    extractAssets: function (pid) {
      if (getDriver() === 'http') return http('POST', '/v1/pixa/short-drama/projects/' + pid + '/auto-sync-assets', {});
      return new Promise(function (res) { setTimeout(function () { res(local().extractAssets(pid)); }, 900); });
    },
    /** 资产图片批量生成（对应 [生成全部角色图片]） */
    /** 批量生成资产参考图。`extra` 可带 `image_vendor`（2026-09-18）。
     *
     * 厂商会进该次 run 的**子进程 env** ⇒ per-run 生效、不污染全局。
     * 不传 ⇒ 后端用缺省（环境变量 / agnes），行为与改造前一致。
     */
    generateAssetImages: function (pid, kindList, extra) {
      if (getDriver() === 'http') {
        return http('POST', '/v1/pixa/short-drama/projects/' + pid + '/assets/batch-generate-image',
                    Object.assign({ kinds: kindList }, extra || {}));
      }
      return new Promise(function (res) { setTimeout(function () { res({ created: local().generateAssetImages(pid, kindList) }); }, 600); });
    },
    /** 分镜脚本生成（对应分集卡 [生成分镜脚本]） */
    generateStoryboard: function (pid, eid) {
      if (getDriver() === 'http') return http('POST', '/v1/pixa/short-drama/episodes/batch/storyboard/generate', { episode_ids: [eid] });
      return new Promise(function (res) { setTimeout(function () { res(local().generateStoryboard(pid, eid)); }, 800); });
    },
    createByAI: function (payload) {
      if (getDriver() === 'http') return http('POST', '/v1/pixa/short-drama/projects/ai-generate', payload);
      const p = local().createProject({
        name: payload.name, source_type: 'ai_generate',
        style_code: payload.style_code, style_name: payload.style_name,
        script: payload.script, one_line_story: payload.idea,
      });
      return Promise.resolve(p);
    },
    renameProject: function (pid, name) {
      if (getDriver() === 'http') return http('POST', '/v1/pixa/short-drama/projects/' + pid + '/rename', { name: name });
      return Promise.resolve(local().updateProject(pid, { name: name }));
    },
    deleteProject: function (pid) {
      if (getDriver() === 'http') return http('POST', '/v1/pixa/short-drama/projects/batch-delete', { project_ids: [pid] });
      local().deleteProject(pid); return Promise.resolve(true);
    },
    updateOutline: function (pid, outline) {
      if (getDriver() === 'http') return http('POST', '/v1/pixa/short-drama/projects/' + pid + '/outline', outline);
      return Promise.resolve(local().updateProject(pid, { outline: outline }));
    },

    /* ---------- 资产 ---------- */
    getAssetRefs: function (pid) {
      if (getDriver() === 'http') return http('GET', '/v1/pixa/short-drama/projects/' + pid + '/asset-refs');
      const p = local().getProject(pid);
      return Promise.resolve(p ? p.assets : { characters: [], scenes: [], props: [] });
    },
    /** 同步资产 / 下一步：线上是 POST .../assets/finalize {"is_manual":true} */
    finalizeAssets: function (pid, isManual) {
      if (getDriver() === 'http') return http('POST', '/v1/pixa/short-drama/projects/' + pid + '/assets/finalize', { is_manual: !!isManual });
      return Promise.resolve(true);
    },
    /** 自动同步开关：线上是 PATCH .../auto-sync-assets {"auto_sync_assets":bool} */
    patchAutoSync: function (pid, val) {
      if (getDriver() === 'http') return http('PATCH', '/v1/pixa/short-drama/projects/' + pid + '/auto-sync-assets', { auto_sync_assets: !!val });
      return Promise.resolve(local().updateProject(pid, { auto_sync_assets: !!val }));
    },
    /** 生成角色/场景形象：线上 POST .../assets/{kind}/{id}/states，body 见 docs/FLOWS.md */
    saveAssetState: function (pid, kind, assetId, body) {
      const seg = kind === 'character' ? 'characters' : kind === 'scene' ? 'scenes' : 'props';
      if (getDriver() === 'http') return http('POST', '/v1/pixa/short-drama/projects/' + pid + '/assets/' + seg + '/' + assetId + '/states', body);
      const st = local().addAssetState(pid, kind, assetId, body.state_name || '基础形象', (body.main_image && body.main_image.url) || '');
      return Promise.resolve(st);
    },
    deleteAsset: function (pid, kind, assetId) {
      if (getDriver() === 'http') return http('DELETE', '/v1/pixa/short-drama/projects/' + pid + '/assets/' + kind + '/' + assetId);
      local().removeAsset(pid, kind, assetId); return Promise.resolve(true);
    },
    /** 新增资产。`opts.identity` = **会注入每一镜提示词**的外观描述（v5 的唯一身份来源）。
     *  ⚠️ 留空会导致模型每镜自己编长相 → 同一个人在 18 个镜头里长成 18 个人。 */
    addAsset: function (pid, kind, name, opts) {
      const o = opts || {};
      if (getDriver() === 'http') {
        return http('POST', '/v1/pixa/short-drama/projects/' + pid + '/assets',
                    { kind: kind, name: name,
                      identity: o.identity || '', keywords: o.keywords || undefined });
      }
      return Promise.resolve(local().addAsset(pid, kind, name));
    },

    /* ---------- D：整片出片（2026-09-19） ---------- */
    /**
     * **合成成片**：线上 `POST /v1/pixa/short-drama/episodes/{eid}/compose`。
     *
     * 它是**唯一一条会整片烧配额**的路径（静帧 → 视频 → 拼接，小时级），
     * 所以只能由人显式点「生成最终视频」触发 —— 后端为此单独开了
     * `runner.KINDS["episode"]` 这个类型（空 shots 的 video 请求仍被硬护栏拒掉）。
     * 静帧/视频已在盘上的部分按磁盘事实复用，**不重复烧**。
     */
    composeEpisode: function (pid, eid, payload) {
      if (getDriver() === 'http') {
        // 质检自愈开关（同 `generateMedia`：默认全关，值取自 core/quality.js）
        // ⚠️ 同样**一次性构造**（`const` 不能再赋值）
        const body = Object.assign({}, (global.Quality ? global.Quality.payload() : {}),
                                   payload || {});
        body.ep = body.ep || _epOfEid(eid) || 1;
        return http('POST', '/v1/pixa/short-drama/episodes/' + eid + '/compose', body);
      }
      // local 驱动：没有后端可渲 —— **如实拒绝**，不给一个假的成功
      return Promise.reject(new Error('离线版没有渲染后端：出片需要 http 驱动'));
    },

    /**
     * **生成剧本正文**（2026-09-19 两段式的第一段）：
     * 线上 `POST /v1/pixa/short-drama/episodes/{eid}/script/generate`。
     *
     * 它与「生成分镜脚本」**不是同一条**：后端只跑到 scriptwriter 就收工
     * （`runner.KINDS["script"]` → `drive_chain --until scriptwriter`），
     * 不产资产卡 / 分镜 / 评审 —— 那些等正文被人确认之后再跑（不白烧调用）。
     */
    generateScript: function (pid, eid, payload) {
      if (getDriver() === 'http') {
        const body = Object.assign({}, (global.Quality ? global.Quality.payload() : {}),
                                   payload || {});
        body.ep = body.ep || _epOfEid(eid) || 1;
        return http('POST', '/v1/pixa/short-drama/episodes/' + eid + '/script/generate', body);
      }
      return Promise.reject(new Error('离线版没有生成后端：生成剧本正文需要 http 驱动'));
    },

    /** 取消一个后台 run（`DELETE /runs/{run_id}`）—— 过场动画上的「停止」用它。 */
    cancelRun: function (runId) {
      if (getDriver() !== 'http') return Promise.reject(new Error('local 驱动没有后台任务'));
      return http('DELETE', '/v1/pixa/short-drama/runs/' + runId);
    },

    /* ---------- 生成（耗时/计费动作） ---------- */
    /** 生成前算力预估：线上 POST /v1/aigc/credits/calculate */
    estimateCredits: function (modelCode, outputs) {
      const body = { model_code: modelCode || 'agnes-image', outputs: outputs || [{ media_type: 'image', count: 1, resolution: 'sd' }] };
      if (getDriver() === 'http') return http('POST', '/v1/aigc/credits/calculate', body);
      return Promise.resolve({ total_credits: 0, model_code: body.model_code });
    },
    /** 关键帧/视频生成：线上 POST /v1/pixa/short-drama/segments/batch/{keyframe|video}/generate
     *
     * http 驱动下**一次请求渲多镜**（后端按 `segment_ids` 起一个 run）——
     * 比逐镜发 N 个请求好：一个 run 只占一次独占锁、台账也只有一条。
     */
    generateMedia: function (pid, eid, kind, payload) {
      const path = '/v1/pixa/short-drama/segments/batch/' + (kind === 'video' ? 'video' : 'keyframe') + '/generate';
      if (getDriver() === 'http') {
        // ★ 质检自愈开关随请求发（2026-09-19）：值取自 `core/quality.js`
        //   （默认全关 = 人工模式）。放在这里而不是各调用点 —— 三条生成路径
        //   （单镜/批量/出片）共用本方法，各写一遍必然漏一个。
        //   ⚠️ 一次性构造（**不能先声明再赋值**：`body` 是 `const`，
        //   赋值会抛 `Assignment to constant variable` —— 实测被渲染断言当场抓住）。
        const body = Object.assign({}, (global.Quality ? global.Quality.payload() : {}),
                                   payload || {});
        // 统一成 `segment_ids`（后端两种都收，但一次渲多镜是本方法的常态）
        if (!body.segment_ids) {
          body.segment_ids = body.segment_id ? [body.segment_id] : [];
        }
        body.pid = pid;
        // ★★ `ep` 必须从 **eid** 推出来（eid 形如 `<pid>-ep<N>`）—— 旧实现写死 `|| 1`，
        //    而 `eid` 收到了却没用 ⇒ **在第 2 集页面上点「批量生成」，实际渲的是第 1 集**
        //    （2026-09-19 实测：ep2 页面选 15 镜提交，台账记的是 `ep=1 shots=15`）。
        //    后果有两重：① 第 2 集永远渲不出来；② 白烧一轮第 1 集的生图配额。
        body.ep = body.ep || _epOfEid(eid) || 1;
        delete body.segment_id;
        return http('POST', path, body);
      }
      return new Promise(function (resolve) {
        // 本地确定性占位（见 core/gen.js）
        const seg = local().getSegment(pid, eid, payload.segment_id);
        const img = global.Gen.keyframe({
          seedKey: payload.segment_id + '|' + kind,
          title: seg ? seg.title : '', ratio: payload.ratio || '9:16',
          index: seg ? seg.order : undefined,
          prompt: seg ? seg.video_prompt : '',
        });
        local().setSegmentMedia(pid, eid, payload.segment_id, kind === 'video' ? 'video' : 'keyframe', img);
        resolve({ status: 'success', url: img });
      });
    },

    /* ---------- 长任务：轮询 ---------- */

    /** 已结束的任务状态（与后端 `runner.TERMINAL` 一致）。 */
    RUN_TERMINAL: { ok: 1, failed: 1, incomplete: 1, blocked: 1, cancelled: 1, lost: 1 },

    /**
     * 轮询一个后台 run 直到结束，返回**最后一条台账记录**。
     *
     * 设计要点：
     *  · 轮询而非长连接 —— 前端不依赖 SSE/WebSocket（后端是普通 REST）。
     *  · **带超时**：超时不是"成功"，返回 `status:'timeout'`，由调用方如实告知。
     *  · `onTick(rec)` 每次拿到状态就回调一次，供界面显示"已 N 秒"。
     *  · 单次查询失败**不当作任务失败**（网络抖一下就放弃会让用户以为白跑了）——
     *    记一次 `pollErrors` 继续轮询。
     */
    waitRun: function (runId, opts) {
      const o = opts || {};
      const interval = o.intervalMs || 3000;
      // ★ `timeoutMs` 三态（2026-09-18）：不传 = 默认 60 分钟；**`0` = 不限时**。
      //   为什么需要 0：开了「逐步人工确认」后，链路会停在每一步等人 ——
      //   耗时单位是**人**，不是分钟。用默认的 60 分钟会在人还没点的时候
      //   就报"前端等待超时"，而任务其实好端端停在那里等着。
      const tmo = (o.timeoutMs === undefined) ? 60 * 60 * 1000 : o.timeoutMs;
      const deadline = tmo > 0 ? Date.now() + tmo : 0;   // 0 = 永不超时
      const self = this;
      let pollErrors = 0;
      return new Promise(function (resolve) {
        (function tick() {
          http('GET', '/v1/pixa/short-drama/runs/' + runId).then(function (rec) {
            if (o.onTick) { try { o.onTick(rec); } catch (e) {} }
            if (rec && self.RUN_TERMINAL[rec.status]) { rec.poll_errors = pollErrors; return resolve(rec); }
            if (deadline && Date.now() > deadline) {
              return resolve(Object.assign({}, rec || {}, {
                status: 'timeout', poll_errors: pollErrors,
                note: '前端等待超时（任务可能仍在后端运行，可稍后刷新查看）',
              }));
            }
            setTimeout(tick, interval);
          }, function (e) {
            // 查询失败 ≠ 任务失败：继续轮询，但把次数记下来
            pollErrors++;
            if (deadline && Date.now() > deadline) {
              return resolve({ status: 'timeout', poll_errors: pollErrors,
                              note: '前端等待超时：' + (e && e.message ? e.message : e) });
            }
            setTimeout(tick, interval);
          });
        })();
      });
    },

    /** 重新拉某一集的分镜写进 Store（生成完刷新界面用）。local 驱动是 no-op。 */
    refreshStoryboard: function (pid, eid) {
      if (getDriver() !== 'http') return Promise.resolve({ hydrated: false, from: 'local' });
      return http('GET', '/v1/pixa/short-drama/episodes/' + eid + '/storyboard/detail')
        .then(function (sb) {
          local().upsertStoryboard(pid, eid, sb);
          return { hydrated: true, from: 'storyboard' };
        });
    },

    /** 查项目最近的运行记录（前端可显示"后台还有任务在跑"）。 */
    listRuns: function (pid, limit) {
      if (getDriver() !== 'http') return Promise.resolve([]);
      return http('GET', '/v1/pixa/short-drama/runs?pid=' + encodeURIComponent(pid || '') +
                        '&limit=' + (limit || 20)).then(function (d) {
        return (d && d.list) || [];
      });
    },

    /* ---------- 步级「逐步人工确认」（2026-09-18） ---------- */

    /**
     * 当前是否停在某一步等人（`GET /projects/{pid}/hitl`）。
     *
     * 返回 `{pending, stamp, next_role, prev_role, redo_targets, manual_steps, decision, stale_decision}`
     *   · `pending`        true = 链路正停在某一步等人给决定
     *   · `prev_role`      **你刚看到的那个产物**属于谁（= 打回的默认目标）
     *   · `next_role`      点「继续」就会跑它
     *   · `redo_targets`   允许打回的角色（**只能是本步之前已完成的**）
     *   · `manual_steps`   端口上那个 dev server 是否开着逐步确认
     *                      —— **`null` = 不知道**（那个 dev 不是本服务起的），别当成"没开"
     *   · `stale_decision` 非空 = 磁盘上有个**已失效**的决定，要提示用户重新确认
     *
     * local 驱动没有链路 ⇒ 恒返回 `{pending:false}`（**不假装可用**）。
     */
    getHitl: function (pid) {
      if (getDriver() !== 'http') {
        return Promise.resolve({ pending: false, manual_steps: null });
      }
      return http('GET', '/v1/pixa/short-drama/projects/' + pid + '/hitl');
    },

    /**
     * 提交决定：`approve`（继续）/ `redo`（打回重跑）/ `reject`（中止）。
     *
     * `stamp` 请**原样带回**从 `getHitl` 取到的那个 —— 它是"这个决定是给哪一次挂起
     * 的"凭据。不带也能用（后端会取当前挂起的戳），但带上能防"取完状态之后链路
     * 又往前走了一步"时把决定用到错误的一步上。
     */
    postHitl: function (pid, body) {
      if (getDriver() !== 'http') return Promise.reject(new Error('local 驱动没有链路'));
      return http('POST', '/v1/pixa/short-drama/projects/' + pid + '/hitl', body || {});
    },

    /**
     * 「提交长任务 → 轮询 → 刷新 → 返回终态」的通用流程（http 驱动专用）。
     *
     * 为什么抽成一个：三条路（生资产图 / 生成分镜 / 生成关键帧·视频）**形状完全一样**，
     * 各写一遍必然漂移。差异只在 `submit` 与 `refresh` 两个回调。
     *
     * 语义约定：
     *  · `submit()` 必须返回 run 记录（含 `run_id`）
     *  · **只有 `ok` / `incomplete` 才 refresh** —— 别把 blocked/failed 的失败状态
     *    当成功去刷新（那会让人误以为成功）
     *  · 返回终态记录；`refresh` 的返回值挂在 `st.refreshed`
     */
    runTask: function (submit, opts) {
      const o = opts || {};
      if (getDriver() !== 'http') return Promise.reject(new Error('runTask 只用于 http 驱动'));
      return Promise.resolve().then(submit).then(function (run) {
        if (!run || !run.run_id) throw new Error('后端未返回 run_id');
        if (o.onSubmitted) { try { o.onSubmitted(run); } catch (e) {} }
        return Api.waitRun(run.run_id, o);
      }).then(function (st) {
        if (!o.refresh || (st.status !== 'ok' && st.status !== 'incomplete')) return st;
        return Promise.resolve(o.refresh()).then(function (r) {
          st.refreshed = r;
          return st;
        }, function () { return st; });      // 刷新失败不该掩盖任务本身的结果
      });
    },

    /* ---------- 分集 / 分镜 ---------- */
    getEpisodeList: function (pid) {
      if (getDriver() === 'http') return http('GET', '/v1/pixa/short-drama/projects/' + pid + '/episodes/storyboard');
      const p = local().getProject(pid);
      return Promise.resolve(p ? p.episodes : []);
    },
    getStoryboard: function (eid) {
      if (getDriver() === 'http') return http('GET', '/v1/pixa/short-drama/episodes/' + eid + '/storyboard/detail');
      // 由 Store 反查
      for (const p of local().allProjects()) {
        const ep = p.episodes.find(function (e) { return e.id === eid; });
        if (ep && ep.storyboard) return Promise.resolve(ep.storyboard);
      }
      return Promise.reject(new Error('episode not found'));
    },
    /** 新剧集：线上 POST /v1/pixa/short-drama/projects/{pid}/episodes
     *  body {"episode_no":N,"title":"...","storyboard_manual":true} */
    createEpisode: function (pid, no, title) {
      if (getDriver() === 'http') return http('POST', '/v1/pixa/short-drama/projects/' + pid + '/episodes', { episode_no: no, title: title, storyboard_manual: true });
      return Promise.resolve(local().addEpisode(pid));
    },
    deleteEpisode: function (pid, eid) {
      if (getDriver() === 'http') return http('DELETE', '/v1/pixa/short-drama/episodes/' + eid);
      local().deleteEpisode(pid, eid); return Promise.resolve(true);
    },
    updateScript: function (pid, eid, text) {
      if (getDriver() === 'http') return http('POST', '/v1/pixa/short-drama/episodes/' + eid + '/script', { content: text });
      local().updateScript(pid, eid, text); return Promise.resolve(true);
    },
    updateSegment: function (pid, eid, sid, patch) {
      if (getDriver() === 'http') return http('POST', '/v1/pixa/short-drama/segments/' + sid, patch);
      return Promise.resolve(local().updateSegment(pid, eid, sid, patch));
    },
    deleteSegment: function (pid, eid, sid) {
      if (getDriver() === 'http') return http('DELETE', '/v1/pixa/short-drama/segments/' + sid);
      local().deleteSegment(pid, eid, sid); return Promise.resolve(true);
    },
    addSegment: function (pid, eid) {
      if (getDriver() === 'http') return http('POST', '/v1/pixa/short-drama/episodes/' + eid + '/segments', {});
      return Promise.resolve(local().addSegment(pid, eid));
    },

    /* ---------- 导出 ---------- */
    exportEpisode: function (pid, eid, format) {
      if (getDriver() === 'http') return http('POST', '/v1/pixa/short-drama/export', { episode_id: eid, format: format || 'mp4' });
      const p = local().getProject(pid);
      const ep = p && p.episodes.find(function (e) { return e.id === eid; });
      if (!ep) return Promise.reject(new Error('episode not found'));
      return Promise.resolve({
        episode_id: eid, title: ep.title,
        timeline: (ep.storyboard.segments || []).map(function (s) {
          return { order: s.order, title: s.title, duration_ms: s.duration_ms, keyframe: s.keyframe, video: s.video };
        }),
      });
    },

    /* ---------- 预设 ---------- */
    getStyles: function () {
      if (getDriver() === 'http') return http('GET', '/v1/pixa/short-drama/styles');
      return Promise.resolve(local().state.styles || []);
    },

    /** 可选**厂商档**（2026-09-18）。
     *
     * local 驱动**没有厂商**（生成是 Canvas 占位、不调模型）⇒ 返回 null，
     * 视图据此禁用选择器并说明原因 —— 不假装它可用。
     */
    getVendors: function () {
      if (getDriver() === 'http') return http('GET', '/v1/pixa/short-drama/vendors');
      return Promise.resolve(null);
    },

    /* ---------- 远端水合（http 驱动专用；local 驱动是 no-op） ----------
     * 视图有 30+ 处**直接读** Store。与其逐处改成 Api.*，不如在唯一渲染入口
     * 先按当前路由把远端数据灌进 Store —— 视图一行都不用动，离线也完全不受影响。
     * 见 `views` 与 `app.js` 的 render()。
     * ------------------------------------------------------------------ */
    /**
     * 按路由取数并写入 Store。
     * @returns Promise<{hydrated:boolean, error?:string, from?:string}>
     *   `hydrated=false` 表示"没做/没做成"，调用方照常渲染本地缓存即可。
     */
    hydrate: function (route) {
      if (getDriver() !== 'http') return Promise.resolve({ hydrated: false, from: 'local' });
      const S = local();
      const p = (route && route.params) || {};
      const path = (route && route.path) || '';
      const pattern = String((route && route.pattern) || '');

      // ★★ 路由参数是字面串 `null`/`undefined` 时**不要发请求**（2026-09-17 实测）：
      //   某处把它拼进了 URL（`'...' + null` → `'...null'`），于是后端稳定 404
      //   「项目不存在：null」，而界面把它显示成"后端未连通"，**极难定位**。
      //   这里**只在路由模板确实要 `:pid`/`:eid` 时才拦** —— 列表路由本来就没有 pid。
      const bogus = function (v) { return !v || v === 'null' || v === 'undefined'; };
      if ((pattern.indexOf(':pid') >= 0 && bogus(p.pid))
          || (pattern.indexOf(':eid') >= 0 && bogus(p.eid))) {
        console.warn('[api] 路由参数异常，跳过水合（不发明知会 404 的请求）：',
                     pattern, JSON.stringify(p));
        return Promise.resolve({ hydrated: false, from: 'bad-route-params' });
      }

      if (p.pid && p.eid) {
        // ★ 厂商档一起水合（2026-09-18）：分镜页的两个批量工具栏（批量生图 / 批量生视频）
        //   要按**后端真实厂商**列，前端不能写死一份 —— 公司机 `register("comfy")`
        //   之后要能看到它（与风格库同一条教训：硬编列表会静默拒掉新增项）。
        //   拉不到**不该**让分镜水合失败 ⇒ 照 styles 的写法兜底为 0。
        const vds = http('GET', '/v1/pixa/short-drama/vendors').then(function (v) {
          S.setVendors(v);
          return ((v && v.items) || []).length;
        }, function () { return 0; });
        return http('GET', '/v1/pixa/short-drama/episodes/' + p.eid + '/storyboard/detail')
          .then(function (sb) {
            S.upsertStoryboard(p.pid, p.eid, sb);
            return vds.then(function (n) {
              return { hydrated: true, from: 'storyboard', vendors: n };
            });
          });
      }
      if (p.pid) {
        // 资产页（`/playlet/review/:pid`）的「生成参考图」旁边也有厂商选择器
        // ⇒ 这一支同样要拉厂商列表（2026-09-18）。拉不到不该拖垮水合。
        const vds = http('GET', '/v1/pixa/short-drama/vendors').then(function (v) {
          S.setVendors(v);
          return ((v && v.items) || []).length;
        }, function () { return 0; });
        return http('GET', '/v1/pixa/short-drama/projects/' + p.pid + '/progress?include_content=true')
          .then(function (proj) {
            S.upsertProject(proj);
            return vds.then(function (n) {
              return { hydrated: true, from: 'progress', vendors: n };
            });
          });
      }
      if (path === '/playlet/list' || path === '/') {
        // 风格库也一起水合：http 驱动下必须以**后端真实类型包**为准，
        // 否则下拉里是 Pavo 的 20+ 风格名、实际却跑 v5 的某一个包（静默偏差）
        const styles = http('GET', '/v1/pixa/short-drama/styles').then(function (list) {
          S.setStyles(list);
          return (list || []).length;
        }, function () { return 0; });          // 风格拉不到不该让整个列表水合失败
        // ⚠️ 这里固定 `is_demo=false` —— 水合的是「**我的项目**」那批。
        //    精选项目**不进水合**：它是视图级的临时数据（只看一次），
        //    混进 Store 就等于让"精选"参与 localStorage 的读写周期，
        //    而它没有任何离线价值（服务端一改，本地缓存就是错的）。
        //    取值见 `views/playlet-list.js` 的 `loadFeatured()`。
        return http('GET', '/v1/pixa/short-drama/projects?page=1&page_size=100&is_demo=false')
          .then(function (d) {
            // ★ `replace=true`：列表以服务端为全集，**移除**不在返回里的项目
            //   （否则出厂种子项目会与真项目混在一起 —— 真实浏览器实测到）
            S.upsertProjects((d && d.list) || [], true);
            return styles.then(function (n) {
              return { hydrated: true, from: 'projects', styles: n };
            });
          });
      }
      return Promise.resolve({ hydrated: false, from: 'unmapped' });
    },

    /** 后端探活（设置页显示连通状态用；local 驱动恒 false）。 */
    ping: function () {
      if (getDriver() !== 'http') return Promise.resolve({ ok: true, driver: 'local' });
      return http('GET', '/health').then(function (d) {
        return Object.assign({ ok: true, driver: 'http' }, d || {});
      }).catch(function (e) {
        return { ok: false, driver: 'http', error: e.message || String(e) };
      });
    },
  };

  global.Api = Api;
})(window);
