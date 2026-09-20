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
}

export interface VendorItem {
  code: string;
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
}

export interface Segment {
  id: string;
  order: number;
  title?: string;
  duration_ms?: number;
  video_prompt?: string;
  keyframe?: string;
  video?: string;
  /** 镜头分组的场景切片；`shots.length` 用来数"镜次"（见 `episodeStats`）。 */
  scenes?: { shots?: unknown[] }[];
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
