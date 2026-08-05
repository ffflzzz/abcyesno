// Bundled contract manifests (offline fallback; mirrors the manifest.json
// files shipped beside each LangGraph agent under hermes-fork/skills/
// langgraph_agents/agents/<id>/). This is DATA, not logic: the frontend never
// branches on a workflow id. Adding a workflow = append one object here (and a
// manifest.json on the backend). Frontend code stays untouched.
//
// NOTE: Only production workflows belong here. Test/demo workflows (hello_agent,
// image_gen) have been removed — they were temporary validation artifacts.
const manifests = [
  {
    id: "manju_craft",
    name: "漫剧生成",
    description: "根据脚本生成竖屏漫剧视频（剪映草稿）",
    category: "media",
    icon: "film",
    version: "1.0.0",
    entry: "agents/manju_craft/agent.py",
    runtime: "inprocess",
    input_schema: {
      type: "object",
      properties: {
        script: {
          type: "string",
          minLength: 1,
          x_ui: { control: "textarea", label: "脚本", placeholder: "描述你要的漫剧情节…" },
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
      },
      required: ["script"],
    },
    output_schema: {
      summary: "markdown",
      artifacts: [
        { id: "video", type: "video", source: "path", label: "成片" },
        { id: "jianying", type: "file", mime: "application/json", label: "剪映草稿" },
        { id: "assets", type: "file", mime: "application/zip", label: "素材包" },
      ],
    },
    capabilities: ["video-generation", "script-to-video"],
    approval_gates: [],
    progress_events: ["step_started", "step_progress", "artifact_produced"],
    ui: { type: "workbench", component: "ManjuCraftWorkbench", title: "漫剧工作台" },
  },
  {
    id: "manju_studio",
    name: "短剧制片工作台",
    description: "剧本→资产→分镜→剪辑台，一键导出剪映草稿工程",
    category: "media",
    icon: "clapperboard",
    version: "1.0.0",
    entry: "agents/manju_studio/agent.py",
    runtime: "inprocess",
    input_schema: {
      type: "object",
      properties: {
        project_name: {
          type: "string",
          x_ui: { control: "text", label: "项目名（可选）" },
        },
      },
      required: [],
    },
    output_schema: {
      summary: "markdown",
      artifacts: [
        { id: "jianying", type: "file", mime: "application/json", label: "剪映草稿" },
      ],
    },
    capabilities: ["short-drama-studio", "script-to-video"],
    approval_gates: [],
    progress_events: [],
    ui: { type: "workbench", component: "StudioWorkbench", title: "短剧制片工作台" },
  },
];

export default manifests;
