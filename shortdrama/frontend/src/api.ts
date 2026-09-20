/* ==========================================================================
   src/api.ts —— 数据访问适配层（前端 ↔ 后端的唯一边界）
   --------------------------------------------------------------------------
   这是从 web/assets/js/core/api.js **逐条移植**的，不是重写。
   为什么强调这一点：`api.js` 里 30+ 处注释记录的都是**实测踩出来的坑**，
   重写一遍等于把这些坑重新踩一次。凡是带着「★ / ⚠️」的段落，都是
   有事故依据的判据，移植时**一条都没敢省**。
   ========================================================================== */

import type {
  ApiError, HitlState, HydrateResult, Project, RouteInfo, RunRecord, StylePack, Vendors,
} from './types';
import { qcPayload } from './lib/quality';
import { chainModePayload } from './lib/chainMode';

const LS_DRIVER = 'pavo.api.driver';
const LS_BASE = 'pavo.api.baseUrl';

export type Driver = 'http' | 'local';

/**
 * 后端默认地址。
 *
 * ⚠️ **默认空串 = 走相对路径（同源）**，由 `vite.config.ts` 的 `server.proxy`
 * 把 `/v1` `/media` `/health` 转发到 8787。这样做的原因见那个配置里的注释：
 * 后端返回的封面是**相对路径**，若前端跑在不同 origin 上又用绝对 base 去取数据，
 * 相对路径的图片会全部裂掉（第一次真浏览器实测就是这样）。
 * 相对路径 + 代理 = 与生产的同源形态一致，两边行为不会分叉。
 *
 * 顺序：
 *   ① localStorage 里显式设过的 —— 尊重用户（否则想在本地模式演示的人会被强行切走）
 *   ② 其余 → 空串（相对路径）
 */
export function getBase(): string {
  const explicit = localStorage.getItem(LS_BASE);
  if (explicit !== null && explicit !== '') return explicit;
  return '';
}
export function setBase(u: string): void {
  localStorage.setItem(LS_BASE, u || '');
}

export function getDriver(): Driver {
  return localStorage.getItem(LS_DRIVER) === 'local' ? 'local' : 'http';
}
export function setDriver(d: Driver): void {
  localStorage.setItem(LS_DRIVER, d === 'local' ? 'local' : 'http');
}

/** 从分集标识里取集号：`<pid>-ep<N>` → N（取不到返回 0）。 */
function epOfEid(eid: string | undefined): number {
  const m = /-ep(\d+)$/.exec(String(eid || ''));
  return m ? parseInt(m[1], 10) : 0;
}

/* ------------------------------------------------------------------ HTTP 驱动 */

export function http<T = unknown>(method: string, path: string, body?: unknown): Promise<T> {
  const url = getBase().replace(/\/$/, '') + path;
  const opt: RequestInit = { method, credentials: 'include', headers: {} };
  if (body !== undefined) {
    if (body instanceof FormData) {
      opt.body = body;
    } else {
      (opt.headers as Record<string, string>)['Content-Type'] = 'application/json';
      opt.body = JSON.stringify(body);
    }
  }
  return fetch(url, opt).then(
    (r) =>
      r.text().then((txt) => {
        let json: Record<string, unknown> | null = null;
        try { json = txt ? JSON.parse(txt) : null; } catch { json = null; }
        const code = json && (json as { code?: string }).code;
        if (!r.ok || (code && code !== '000000')) {
          // ★★ **必须区分两种失败**（旧 web 版 2026-09-17 实测踩到）：
          //   ① 后端**答复了**（有 HTTP 状态，如 404「项目不存在：null」）
          //   ② 根本**联系不上**（fetch 自身失败）
          //   两类都抛裸 Error 时，上层一律显示"后端未连通" ——
          //   于是后端明确回的 404 被说成"没连上"，**把排查方向带偏**。
          //   挂上 status/url/fromBackend 让上层能说对话。
          const msg = (json && (json as { message?: string }).message) || 'HTTP ' + r.status;
          const err = new Error(msg) as ApiError;
          err.status = r.status;
          err.url = url;
          err.fromBackend = true;
          throw err;
        }
        if (!json) return null as T;
        return ((json as { data?: unknown }).data !== undefined
          ? (json as { data: unknown }).data
          : json) as T;
      }),
    (e: unknown) => {
      const err = new Error(
        e && typeof e === 'object' && 'message' in e
          ? String((e as { message: unknown }).message)
          : 'fetch failed',
      ) as ApiError;
      err.isNetwork = true;          // fetch 自己失败 = 真的联系不上
      err.url = url;
      throw err;
    },
  );
}

/* ----------------------------------------------------------------- Local 驱动 */

export function getHitl(pid: string): Promise<HitlState> {
  if (getDriver() !== 'http') return Promise.resolve({ pending: false, manual_steps: null });
  return http<HitlState>('GET', '/v1/pixa/short-drama/projects/' + pid + '/hitl');
}

/* ================================================================ API 面 */

export const Api = {
  get driver(): Driver { return getDriver(); },
  setDriver,
  get baseUrl(): string { return getBase(); },
  setBaseUrl: setBase,

  /* ---------- 项目 ---------- */

  /**
   * 项目列表。`isDemo` 决定要哪一批 —— 线上就是靠这一个参数区分两个 tab。
   * ⚠️ 旧版这里**写死过 `is_demo=false`**，于是「精选项目」tab 永远拿不到数据。
   */
  listProjects(page?: number, pageSize?: number, isDemo?: boolean): Promise<{ list: Project[] }> {
    if (getDriver() !== 'http') return Promise.resolve({ list: [] });
    return http<{ list: Project[] }>(
      'GET',
      '/v1/pixa/short-drama/projects?page=' + (page || 1)
        + '&page_size=' + (pageSize || 10)
        + '&is_demo=' + (isDemo === true ? 'true' : 'false'),
    );
  },

  getProgress(pid: string): Promise<Project> {
    return http<Project>('GET', '/v1/pixa/short-drama/projects/' + pid + '/progress?include_content=true');
  },

  /** 线上三段式建项目的第三步：[剧本解析] → 创建项目并回填概要/拆资产 */
  createByParse(text: string, styleCode: string): Promise<Project> {
    return http<Project>('POST', '/v1/pixa/short-drama/projects/paste', {
      content: text, style_code: styleCode,
    });
  },

  createByAI(payload: Record<string, unknown>): Promise<Project> {
    return http<Project>('POST', '/v1/pixa/short-drama/projects/ai-generate', payload);
  },

  createByPaste(name: string, content: string): Promise<Project> {
    return http<Project>('POST', '/v1/pixa/short-drama/projects/paste', {
      name, content,
    });
  },

  renameProject(pid: string, name: string): Promise<unknown> {
    return http('POST', '/v1/pixa/short-drama/projects/' + pid + '/rename', { name });
  },

  deleteProject(pid: string): Promise<unknown> {
    return http('POST', '/v1/pixa/short-drama/projects/batch-delete', { project_ids: [pid] });
  },

  extractAssets(pid: string): Promise<unknown> {
    return http('POST', '/v1/pixa/short-drama/projects/' + pid + '/auto-sync-assets', {});
  },

  /** ⚠️ `_pid` 刻意不用：线上这条接口是按**分集**走的（旧版同样只用了 eid）。 */
  generateStoryboard(_pid: string, eid: string): Promise<unknown> {
    return http('POST', '/v1/pixa/short-drama/episodes/batch/storyboard/generate', {
      episode_ids: [eid],
      ...chainModePayload(),
    });
  },

  /** 批量生成资产参考图。`extra` 可带 `image_vendor` —— 进该次 run 的**子进程 env**。 */
  generateAssetImages(pid: string, kindList: string[], extra?: Record<string, unknown>): Promise<unknown> {
    return http(
      'POST',
      '/v1/pixa/short-drama/projects/' + pid + '/assets/batch-generate-image',
      Object.assign({ kinds: kindList }, extra || {}),
    );
  },

  getAssetRefs(pid: string): Promise<unknown> {
    return http('GET', '/v1/pixa/short-drama/projects/' + pid + '/asset-refs');
  },

  /* ---------- 分集 / 分镜 ---------- */

  getEpisodeList(pid: string): Promise<unknown> {
    return http('GET', '/v1/pixa/short-drama/projects/' + pid + '/episodes/storyboard');
  },

  getStoryboard(eid: string): Promise<unknown> {
    return http('GET', '/v1/pixa/short-drama/episodes/' + eid + '/storyboard/detail');
  },

  createEpisode(pid: string, no: number, title: string): Promise<unknown> {
    return http('POST', '/v1/pixa/short-drama/projects/' + pid + '/episodes', {
      episode_no: no, title, storyboard_manual: true,
    });
  },

  deleteEpisode(_pid: string, eid: string): Promise<unknown> {
    return http('DELETE', '/v1/pixa/short-drama/episodes/' + eid);
  },

  updateOutline(pid: string, outline: Record<string, unknown>): Promise<unknown> {
    return http('POST', '/v1/pixa/short-drama/projects/' + pid + '/outline', outline);
  },

  /**
   * 新增资产。`opts.identity` = **会注入每一镜提示词**的外观描述（v5 的唯一身份来源）。
   * ⚠️ 留空会导致模型每镜自己编长相 → 同一个人在 18 个镜头里长成 18 个人。
   */
  addAsset(pid: string, kind: string, name: string, opts?: {
    identity?: string; keywords?: string[];
  }): Promise<unknown> {
    const o = opts || {};
    return http('POST', '/v1/pixa/short-drama/projects/' + pid + '/assets', {
      kind, name, identity: o.identity || '', keywords: o.keywords || undefined,
    });
  },

  /** 生成角色/场景形象：线上 POST .../assets/{kind}/{id}/states */
  saveAssetState(pid: string, kind: string, assetId: string, body: Record<string, unknown>): Promise<unknown> {
    const seg = kind === 'character' ? 'characters' : kind === 'scene' ? 'scenes' : 'props';
    return http('POST', '/v1/pixa/short-drama/projects/' + pid + '/assets/' + seg + '/' + assetId + '/states', body);
  },

  deleteAsset(pid: string, kind: string, assetId: string): Promise<unknown> {
    return http('DELETE', '/v1/pixa/short-drama/projects/' + pid + '/assets/' + kind + '/' + assetId);
  },

  /** 自动同步开关：线上是 PATCH .../auto-sync-assets {"auto_sync_assets":bool} */
  patchAutoSync(pid: string, val: boolean): Promise<unknown> {
    return http('PATCH', '/v1/pixa/short-drama/projects/' + pid + '/auto-sync-assets', {
      auto_sync_assets: !!val,
    });
  },

  /** 步级「逐步人工确认」的挂起状态（见 `types.HitlState`）。 */
  getHitl(pid: string): Promise<HitlState> {
    if (getDriver() !== 'http') return Promise.resolve({ pending: false, manual_steps: null });
    return http<HitlState>('GET', '/v1/pixa/short-drama/projects/' + pid + '/hitl');
  },

  updateScript(_pid: string, eid: string, text: string): Promise<unknown> {
    return http('POST', '/v1/pixa/short-drama/episodes/' + eid + '/script', { content: text });
  },

  addSegment(_pid: string, eid: string): Promise<unknown> {
    return http('POST', '/v1/pixa/short-drama/episodes/' + eid + '/segments', {});
  },

  deleteSegment(_pid: string, _eid: string, sid: string): Promise<unknown> {
    return http('DELETE', '/v1/pixa/short-drama/segments/' + sid);
  },

  updateSegment(sid: string, patch: Record<string, unknown>): Promise<unknown> {
    return http('POST', '/v1/pixa/short-drama/segments/' + sid, patch);
  },

  /* ---------- 预设 ---------- */

  getStyles(): Promise<StylePack[]> {
    return http<StylePack[]>('GET', '/v1/pixa/short-drama/styles');
  },

  /** 可选**厂商档**。local 驱动没有厂商（不调模型）⇒ null，视图据此禁用并说明原因。 */
  getVendors(): Promise<Vendors> {
    return http<Vendors>('GET', '/v1/pixa/short-drama/vendors');
  },

  /** 后端探活（设置页显示连通状态用）。 */
  async ping(): Promise<{ ok: boolean; driver: Driver; error?: string }> {
    if (getDriver() !== 'http') return { ok: true, driver: 'local' };
    try {
      const d = await http<Record<string, unknown>>('GET', '/health');
      return Object.assign({ ok: true, driver: 'http' as Driver }, d || {});
    } catch (e) {
      return { ok: false, driver: 'http', error: (e as Error).message || String(e) };
    }
  },

  /* ---------- 长任务：轮询 ---------- */

  /** 已结束的任务状态（与后端 `runner.TERMINAL` 一致）。 */
  RUN_TERMINAL: {
    ok: 1, failed: 1, incomplete: 1, blocked: 1, cancelled: 1, lost: 1,
  } as Record<string, number>,

  /**
   * 轮询一个后台 run 直到结束，返回**最后一条台账记录**。
   *
   * 设计要点（沿用旧版，逐条有据）：
   *  · 轮询而非长连接 —— 前端不依赖 SSE/WebSocket。
   *  · **带超时**：超时不是"成功"，返回 `status:'timeout'`，由调用方如实告知。
   *  · `onTick(rec)` 每次拿到状态就回调一次（界面显示"已 N 秒"）。
   *  · 单次查询失败**不当作任务失败** —— 记一次 `pollErrors` 继续轮询。
   *  · ★ `timeoutMs` 三态：不传 = 默认 60 分钟；**`0` = 不限时**。
   *    需要 0 的原因：开了「逐步人工确认」后链路会停在每一步等人，耗时单位是**人**。
   */
  waitRun(runId: string, opts?: WaitOpts): Promise<RunRecord> {
    const o = opts || {};
    const interval = o.intervalMs || 3000;
    const tmo = o.timeoutMs === undefined ? 60 * 60 * 1000 : o.timeoutMs;
    const deadline = tmo > 0 ? Date.now() + tmo : 0;
    let pollErrors = 0;
    return new Promise<RunRecord>((resolve) => {
      (function tick() {
        http<RunRecord>('GET', '/v1/pixa/short-drama/runs/' + runId).then(
          (rec) => {
            if (o.onTick) { try { o.onTick(rec); } catch { /* 回调失败不该影响轮询 */ } }
            if (rec && Api.RUN_TERMINAL[String(rec.status)]) {
              rec.poll_errors = pollErrors;
              return resolve(rec);
            }
            if (deadline && Date.now() > deadline) {
              return resolve(Object.assign({}, rec || {}, {
                status: 'timeout', poll_errors: pollErrors,
                note: '前端等待超时（任务可能仍在后端运行，可稍后刷新查看）',
              }));
            }
            setTimeout(tick, interval);
          },
          (e: Error) => {
            pollErrors++;
            if (deadline && Date.now() > deadline) {
              return resolve({
                status: 'timeout', poll_errors: pollErrors,
                note: '前端等待超时：' + (e && e.message ? e.message : String(e)),
              });
            }
            setTimeout(tick, interval);
          },
        );
      })();
    });
  },

  /** 重新拉某一集的分镜写进 Store（生成完刷新界面用）。 */
  async refreshStoryboard(): Promise<HydrateResult> {
    return { hydrated: false, from: 'not-ported' };
  },

  /** 查项目最近的运行记录（前端可显示"后台还有任务在跑"）。 */
  async listRuns(pid: string, limit?: number): Promise<RunRecord[]> {
    if (getDriver() !== 'http') return [];
    const d = await http<{ list?: RunRecord[] }>(
      'GET',
      '/v1/pixa/short-drama/runs?pid=' + encodeURIComponent(pid || '') + '&limit=' + (limit || 20),
    );
    return (d && d.list) || [];
  },

  /**
   * 提交决定：`approve`（继续）/ `redo`（打回重跑）/ `reject`（中止）。
   * `stamp` 请**原样带回**从 getHitl 取到的那个 —— 它是"这个决定是给哪一次挂起"的凭据。
   */
  postHitl(pid: string, body: Record<string, unknown>): Promise<unknown> {
    return http('POST', '/v1/pixa/short-drama/projects/' + pid + '/hitl', body || {});
  },

  /**
   * 「提交长任务 → 轮询 → 刷新 → 返回终态」的通用流程。
   * 语义约定：**只有 `ok` / `incomplete` 才 refresh** ——
   * 别把 blocked/failed 当成功去刷新（那会让人误以为成功）。
   */
  runTask(
    submit: () => Promise<RunRecord>,
    opts?: WaitOpts & { refresh?: () => Promise<unknown> },
  ): Promise<RunRecord> {
    const o = opts || {};
    return Promise.resolve()
      .then(submit)
      .then((run) => {
        if (!run || !run.run_id) throw new Error('后端未返回 run_id');
        return Api.waitRun(String(run.run_id), o);
      })
      .then((st) => {
        if (!o.refresh || (st.status !== 'ok' && st.status !== 'incomplete')) return st;
        return Promise.resolve(o.refresh()).then(
          (r) => { st.refreshed = r; return st; },
          () => st,                       // 刷新失败不该掩盖任务本身的结果
        );
      });
  },

  /* ---------- 生成（耗时/计费动作） ---------- */

  /**
   * 关键帧 / 视频生成。http 驱动下**一次请求渲多镜**（后端按 `segment_ids` 起一个 run）——
   * 比逐镜发 N 个请求好：一个 run 只占一次独占锁、台账也只有一条。
   *
   * ⚠️ `ep` 必须从 **eid** 推出来：旧实现写死 `|| 1`，于是**在第 2 集页面上
   * 点「批量生成」实际渲的是第 1 集**（2026-09-19 实测：ep2 页面选 15 镜提交，
   * 台账记的是 `ep=1 shots=15`），后果是第 2 集永远渲不出来 + 白烧一轮第 1 集配额。
   */
  generateMedia(
    pid: string, eid: string, kind: 'keyframe' | 'video', payload: Record<string, unknown>,
  ): Promise<unknown> {
    const path = '/v1/pixa/short-drama/segments/batch/'
      + (kind === 'video' ? 'video' : 'keyframe') + '/generate';
    // ★ 质检自愈开关随请求发（值取自 `lib/quality.ts`，默认全关 = 人工模式）。
    //   放在这里而不是各调用点 —— 三条生成路径（单镜/批量/出片）共用本方法，
    //   各写一遍必然漏一个。
    //   ⚠️ **一次性构造**（`const` 不能再赋值：旧版实测被断言当场抓过）。
    const body = Object.assign({}, qcPayload(), payload || {});
    if (!body.segment_ids) body.segment_ids = body.segment_id ? [body.segment_id] : [];
    body.pid = pid;
    body.ep = body.ep || epOfEid(eid) || 1;
    delete body.segment_id;
    return http('POST', path, body);
  },

  /** 整片出片：**唯一一条会整片烧配额**的路径（静帧 → 视频 → 拼接，小时级）。 */
  composeEpisode(eid: string, payload?: Record<string, unknown>): Promise<RunRecord> {
    const body = Object.assign({}, qcPayload(), payload || {});
    body.ep = body.ep || epOfEid(eid) || 1;
    return http<RunRecord>('POST', '/v1/pixa/short-drama/episodes/' + eid + '/compose', body);
  },

  /** 生成剧本正文（两段式的第一段）：后端只跑到 scriptwriter 就收工。 */
  generateScript(eid: string, payload?: Record<string, unknown>): Promise<RunRecord> {
    const body = Object.assign({}, qcPayload(), chainModePayload(), payload || {});
    body.ep = body.ep || epOfEid(eid) || 1;
    return http<RunRecord>('POST', '/v1/pixa/short-drama/episodes/' + eid + '/script/generate', body);
  },

  /** 取消一个后台 run —— 过场动画上的「停止」用它。 */
  cancelRun(runId: string): Promise<unknown> {
    return http('DELETE', '/v1/pixa/short-drama/runs/' + runId);
  },

  /** 生成前算力预估。 */
  estimateCredits(modelCode: string, outputs?: unknown[]): Promise<unknown> {
    return http('POST', '/v1/aigc/credits/calculate', {
      model_code: modelCode || 'agnes-image',
      outputs: outputs || [{ media_type: 'image', count: 1, resolution: 'sd' }],
    });
  },
};

export interface WaitOpts {
  intervalMs?: number;
  /** 不传 = 60 分钟；**0 = 不限时**（逐步人工确认下要它） */
  timeoutMs?: number;
  onTick?: (rec: RunRecord) => void;
  onSubmitted?: (run: RunRecord) => void;
  refresh?: () => Promise<unknown>;
}

/* ---------------------------------------------------------------- 水合 */

/**
 * 按路由取数并写入 Store。
 *
 * 为什么要"水合"而不是让每个组件自己拉：视图有 30+ 处直接读 Store。
 * 在**唯一渲染入口**先按当前路由把远端数据灌进 Store，视图一行都不用动。
 *
 * @returns `hydrated=false` 表示"没做/没做成"，调用方照常渲染本地缓存即可。
 */
export async function hydrate(
  route: RouteInfo,
  store: {
    upsertProject: (p: Project) => void;
    upsertProjects: (list: Project[], replace: boolean) => void;
    upsertStoryboard: (pid: string, eid: string, sb: unknown) => void;
    setStyles: (s: StylePack[]) => void;
    setVendors: (v: Vendors | null) => void;
  },
): Promise<HydrateResult> {
  if (getDriver() !== 'http') return { hydrated: false, from: 'local' };

  const p = route.params || {};
  const path = route.path || '';
  const pattern = route.pattern || '';

  // ★★ 路由参数是字面串 `null`/`undefined` 时**不要发请求**（旧版实测）：
  //   某处把它拼进 URL（`'...' + null` → `'...null'`）⇒ 后端稳定 404
  //   「项目不存在：null」，而界面把它显示成"后端未连通"，**极难定位**。
  //   只在路由模板确实要 `:pid`/`:eid` 时才拦 —— 列表路由本来就没有 pid。
  const bogus = (v: string | undefined): boolean => !v || v === 'null' || v === 'undefined';
  if ((pattern.includes(':pid') && bogus(p.pid)) || (pattern.includes(':eid') && bogus(p.eid))) {
    console.warn('[api] 路由参数异常，跳过水合（不发明知会 404 的请求）：', pattern, p);
    return { hydrated: false, from: 'bad-route-params' };
  }

  if (p.pid && p.eid) {
    // 厂商档一起水合：分镜页的两个批量工具栏要按**后端真实厂商**列，
    // 前端不能写死一份（公司机 register("comfy") 之后要能看到它）。
    // 拉不到**不该**让分镜水合失败 ⇒ 照 styles 的写法兜底为 0。
    const vds = Api.getVendors().then(
      (v) => { store.setVendors(v); return ((v && v.items) || []).length; },
      () => 0,
    );
    /**
     * ★★ **必须先拉项目，再拉分镜**（2026-09-19 修的是旧版的既有缺陷）。
     *
     * 旧版这一支**只**拉 `storyboard/detail`，而 `Store.upsertStoryboard` 要求
     * 项目已在库中（它要往 `p.episodes` 里挂分镜）—— 于是：
     *   · 直接打开 / 刷新分镜页 URL ⇒ 库里没有这个项目 ⇒ 分镜无处可挂
     *   ⇒ 界面显示「剧集不存在」，而**后端一切正常**。
     * 旧版的规避方式是"必须先点进列表页再点进去"，刷新一次就现原形 ——
     * 这是很典型的"能跑但一刷新就坏"。这里补上项目水合，深链与刷新都正常。
     */
    const proj = await Api.getProgress(p.pid);
    store.upsertProject(proj);
    const sb = await Api.getStoryboard(p.eid);
    store.upsertStoryboard(p.pid, p.eid, sb);
    return { hydrated: true, from: 'storyboard+progress', vendors: await vds };
  }

  if (p.pid) {
    const vds = Api.getVendors().then(
      (v) => { store.setVendors(v); return ((v && v.items) || []).length; },
      () => 0,
    );
    const proj = await Api.getProgress(p.pid);
    store.upsertProject(proj);
    return { hydrated: true, from: 'progress', vendors: await vds };
  }

  if (path === '/playlet/list' || path === '/') {
    // 风格库一起水合：http 驱动下必须以**后端真实类型包**为准，
    // 否则下拉里是线上站点的风格名、实际却跑 v5 的某一个包（静默偏差）。
    const styles = Api.getStyles().then(
      (list) => { store.setStyles(list || []); return (list || []).length; },
      () => 0,      // 风格拉不到不该让整个列表水合失败
    );
    // ⚠️ 这里固定 `is_demo=false` —— 水合的是「**我的项目**」那批。
    //    精选项目**不进水合**：它是视图级的临时数据（只看一次），
    //    混进 Store 就等于让"精选"参与 localStorage 的读写周期，
    //    而它没有任何离线价值（服务端一改，本地缓存就是错的）。
    const d = await Api.listProjects(1, 100, false);
    // ★ `replace=true`：列表以服务端为全集，**移除**不在返回里的项目
    //   （否则出厂种子项目会与真项目混在一起 —— 旧版真实浏览器实测到）
    store.upsertProjects((d && d.list) || [], true);
    return { hydrated: true, from: 'projects', styles: await styles };
  }

  return { hydrated: false, from: 'unmapped' };
}
