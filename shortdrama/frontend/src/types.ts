/** 后端接口的形状。只声明前端真正读到的字段 —— 其余留 `unknown`，避免编造契约。 */

export interface StylePack {
  code: string;
  name: string;
  group?: string;
  /** 该包**真实项目**的静帧（后端从 projects/ 里确定性挑的） */
  cover_url?: string;
  /** 该包**真正会注入每一镜提示词**的那段（不是宣传语） */
  visual_style?: string;
  sample_topic?: string;
  sample_project?: string;
  sample_shot?: string;
  sample_stills?: number;
  /**
   * 以下三项是后端 `styles()` 从每个包 `pack.json` 直接读出来给的（`webmap.styles`），
   * 2026-10-02 才补进声明 —— 之前只有向导页用一处**局部转型**在读，
   * 于是"后端一直在给、类型里却查无此字段"，第二个页面想用就得再 cast 一次（两份口径）。
   * 展示口径统一在 `lib/packcaps.ts`。
   */
  audio_modes?: string[];
  still_refs?: boolean | null;
  has_style_block?: boolean;
}

/**
 * `GET /health` 的载荷（`v5/webmap.py` 的 `health()`）。
 *
 * ★ 为什么要专门水合它：`ratio_choices` / `ratio_default` 是**后端**的画幅候选
 *   （单一来源 `config.RATIO_CHOICES`），而前端原先把选择器**写死成只有 `9:16`** ——
 *   但 `brief.ratio` 是**真的会生效**的（`media/runner.py:244` 拿它写子进程的
 *   `SHORTDRAMA_STILL_RATIO` + `SHORTDRAMA_ASPECT`），于是横屏包（如 xianxia-vfx-action）
 *   在网页上**根本建不出 16:9 的项目**。与 `styles` / `vendors` 同一条教训：
 *   **候选列表不许在前端写死**，后端加了档位前端看不到就是静默偏差。
 *   `video_mode` / `aspect_ratio` 也一并给，出片页可以直接说清真实现场。
 */
export interface Health {
  ok: boolean;
  projects_dir?: string;
  projects?: number;
  packs?: string[];
  video_mode?: string;
  aspect_ratio?: string;
  ratio_choices?: string[];
  ratio_default?: string;
}

export interface VendorItem {  code: string;
  name?: string;
  short?: string;
  builtin?: boolean;
}

export interface Vendors {
  items: VendorItem[];
  current: { image: string; video: string };
  current_ok: { image: boolean; video: boolean };
}

export interface Episode {
  id: string;
  no: number;
  title: string;
  summary?: string;
  /** 剧本正文（原样字符串，**不要 trim**：见 wizard 编辑态的注释） */
  script?: string;
  script_status?: string;
  storyboard?: { ratio?: string; segments?: Segment[] };
  /** 后端便利字段：分镜表的镜数（`len(shots(root,ep))`），**不是**已生成的静帧数。 */
  shots?: number;
  /** ★ 后端便利字段：这一集的**画布会有几格**（`canvasout.node_sources().nodes`）。
   *  0 = 盘上没有静帧/片段组/定妆照/成片，点进去只会是一张空画布 ⇒ 入口不该放行。
   *  ⛔ 别拿 `shots` 代替它：镜数是"分镜表写了几镜"，与"媒体链跑出了什么"完全两回事
   *  （实测安装包那颗唯一可点的按钮 13 镜、画布 0 格）。 */
  canvas_nodes?: number;
  /** 后端便利字段：格数为 0（或少于预期）时的**理由原文**，与画布自己报的 warnings 同一份。 */
  canvas_warnings?: string[];
  /** 后端便利字段：`media/ep{N}/episode_final.mp4` 在不在盘上。 */
  has_final?: boolean;
  duration_s?: number;
}

export interface Segment {
  id: string;
  order: number;
  title?: string;
  summary?: string;
  duration_ms?: number;
  video_prompt?: string;
  keyframe?: string;
  video?: string;
  /** 任务三色态（`pending` / `generating` / `completed` / `failed`）——只有 detail 端点给。 */
  status?: string;
  /** 本镜场景名（后端已剥掉 `@`；拿不到时是「未标注场景」）——只有 detail 端点给。 */
  scene?: string;
  /** v5 侧真实字段（`webmap.storyboard_detail` 的 `v5` 段）。镜名在这里，卡片标题用它。 */
  v5?: {
    shot_name?: string;
    shot_type?: string;
    angle?: string;
    camera?: string;
    dialogue?: string;
    sfx?: string;
    job_state?: string;
    job_error?: string;
    still_prompt?: string;
  };
  /** 镜头分组的场景切片；`shots.length` 用来数"镜次"（见 `episodeStats`）。 */
  scenes?: { shots?: unknown[] }[];
}

/**
 * `GET /episodes/{eid}/storyboard/detail` —— **唯一**带逐镜 `keyframe` / `video` 的端点。
 *
 * ★ 为什么必须单独声明它：项目列表 / `progress` / 分集列表里的
 *   `episodes[].storyboard` 是后端**刻意给的轻量壳**（`v5/webmap.py` 的
 *   `_episode_row()` 写死 `"segments": []`，注释原话是"列表接口不该变重"）。
 *   ⇒ 任何"要真实逐镜产物"的页面都只能按需拉这一条；
 *   去列表数据里数 `segments` 的写法**永远数到 0**
 *   （`Works` / `Visuals` 旧实现正是这样，于是页面恒显"没有素材"，
 *   而后端盘上素材齐全 —— 2026-10-02 契约审计查实）。
 */
export interface StoryboardDetail {
  episode_id?: string;
  episode_no?: number;
  title?: string;
  phase?: string;
  ratio?: string;
  segments?: Segment[];
  /** 最近一次分镜契约门的判决；取不到 → `{}`（后端绝不假报"通过"）。 */
  gates?: Record<string, unknown>;
  [k: string]: unknown;
}

export interface AssetState {
  ref_id?: string;
  /** 展示名（旧版 `state_thumb` 的 title 用它） */
  display_name?: string;
  state_name?: string;
  is_default?: boolean;
  image?: string;
}

export interface AssetItem {
  id: string;
  name: string;
  /** 会注入每一镜提示词的外观描述（v5 的唯一身份来源） */
  identity?: string;
  main_image?: { url?: string };
  states?: AssetState[];
}

export interface ProjectAssets {
  characters: AssetItem[];
  scenes: AssetItem[];
  props: AssetItem[];
}

export interface Project {
  id: string;
  name: string;
  ratio?: string;
  style?: { code?: string; name?: string; style_id?: string; cover_url?: string };
  source_type?: string;
  status?: string;
  cover?: string;
  created_at?: string;
  episodes: Episode[];
  assets?: ProjectAssets;
  outline?: Record<string, unknown>;
  [k: string]: unknown;
}

export interface RunRecord {
  run_id?: string;
  status?: string;
  kind?: string;
  note?: string;
  poll_errors?: number;
  [k: string]: unknown;
}

/** `GET /projects/{pid}/hitl` —— 步级人工确认的挂起状态。 */
export interface HitlState {
  pending: boolean;
  stamp?: string;
  next_role?: string;
  prev_role?: string;
  redo_targets?: string[];
  /** `null` = **不知道**（那个 dev server 不是本服务起的），别当成"没开" */
  manual_steps?: boolean | null;
  decision?: unknown;
  stale_decision?: unknown;
}

export interface ApiError extends Error {
  /** 后端**答复了**（有 HTTP 状态） */
  status?: number;
  url?: string;
  fromBackend?: boolean;
  /** 根本**联系不上**（fetch 自身失败） */
  isNetwork?: boolean;
}

export interface RouteInfo {
  path: string;
  params: Record<string, string>;
  query: Record<string, string>;
  pattern: string;
}

export interface HydrateResult {
  hydrated: boolean;
  from?: string;
  error?: string;
  [k: string]: unknown;
}
