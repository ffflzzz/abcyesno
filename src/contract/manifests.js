// Bundled contract manifests (offline fallback; mirrors the manifest.json
// files shipped beside each LangGraph agent under hermes-fork/skills/
// langgraph_agents/agents/<id>/). This is DATA, not logic: the frontend never
// branches on a workflow id. Adding a workflow = append one object here (and a
// manifest.json on the backend). Frontend code stays untouched.
//
// NOTE: Only production workflows are exposed in the UI. Test/demo workflows
// (hello_agent, image_gen) have been removed. The legacy manju_craft entry is
// also hidden: its functionality has been unified into the single
// "短剧制片工作台" front-end, which executes the manjucraft_agent LangGraph
// pipeline on the backend.
const manifests = [
  {
    id: "manjucraft_agent",
    name: "短剧制片工作台",
    description: "剧本→资产→分镜→成片，一站式竖屏漫剧/短剧生产，导出剪映草稿工程",
    category: "media",
    icon: "clapperboard",
    version: "1.0.0",
    entry: "agents/manjucraft_agent/agent.py",
    runtime: "inprocess",
    input_schema: {
      type: "object",
      properties: {
        mode: {
          type: "string",
          enum: ["single", "series"],
          default: "single",
          x_ui: { control: "select", label: "模式" },
        },
        script: {
          type: "string",
          minLength: 1,
          x_ui: { control: "textarea", label: "剧本 / 脚本", placeholder: "描述你要的漫剧情节（single 模式）…" },
        },
        series_script: {
          type: "string",
          x_ui: { control: "textarea", label: "系列脚本（series 模式）", placeholder: "整部连载的大纲/剧情，将按集数拆分" },
        },
        style: {
          type: "string",
          enum: ["写实", "二次元", "3D"],
          default: "二次元",
          x_ui: { control: "select", label: "风格" },
        },
        project_name: {
          type: "string",
          x_ui: { control: "text", label: "项目名（可选）" },
        },
        total_episodes: {
          type: "integer",
          minimum: 1,
          maximum: 24,
          default: 3,
          x_ui: { control: "number", label: "集数" },
        },
        consistency_policy: {
          type: "string",
          enum: ["lock_bible", "per_episode"],
          default: "lock_bible",
          x_ui: { control: "select", label: "跨集一致性" },
        },
        resolution: {
          type: "string",
          enum: ["1080×1920", "1920×1080"],
          default: "1080×1920",
          x_ui: { control: "select", label: "分辨率" },
        },
        sec_per_shot: {
          type: "integer",
          minimum: 2,
          maximum: 12,
          default: 4,
          x_ui: { control: "number", label: "每镜秒数" },
        },
      },
      required: [],
    },
    output_schema: {
      summary: "markdown",
      artifacts: [
        { id: "video", type: "video", source: "path", label: "成片" },
        { id: "jianying", type: "file", mime: "application/json", source: "path", label: "剪映草稿" },
        { id: "assets", type: "file", mime: "application/zip", source: "path", label: "素材包" },
      ],
    },
    capabilities: ["video-generation", "script-to-video", "series", "jianying-export"],
    approval_gates: [
      { gate_id: "first_frame", label: "首帧确认", allowSteer: true, modes: ["single", "series"], episodes: "first" },
      { gate_id: "episode_ready", label: "本集确认", allowSteer: true, modes: ["series"], episodes: "later" },
      { gate_id: "each_scene", label: "分镜确认", allowSteer: true, modes: ["single", "series"], episodes: "first" },
      { gate_id: "end", label: "成片确认", allowSteer: false, modes: ["single", "series"], episodes: "first" },
    ],
    progress_events: ["workflow.progress", "workflow.artifact", "workflow.approval", "workflow.done"],
    ui: { type: "workbench", component: "StudioWorkbench", title: "短剧制片工作台" },
    notes: "series 模式：首集走 first_frame/each_scene/end 三个完整门（首帧门批准即锁定 character_bible）；续集仅走轻量 episode_ready 门。",
  },
];

export default manifests;
