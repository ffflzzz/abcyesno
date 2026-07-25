// Bundled contract manifests (offline fallback; mirrors the manifest.json
// files shipped beside each LangGraph agent under hermes-fork/skills/
// langgraph_agents/agents/<id>/). This is DATA, not logic: the frontend never
// branches on a workflow id. Adding a workflow = append one object here (and a
// manifest.json on the backend). Frontend code stays untouched.
const manifests = [
  {
    id: "hello_agent",
    name: "问候助手",
    description: "最小 LangGraph 示例，验证契约最小接口",
    category: "general",
    icon: "chat",
    version: "1.0.0",
    entry: "agents/hello_agent/agent.py",
    runtime: "inprocess",
    input_schema: {
      type: "object",
      properties: {
        input: {
          type: "string",
          x_ui: { control: "textarea", label: "输入", placeholder: "说点什么…" },
        },
      },
      required: ["input"],
    },
    output_schema: { summary: "markdown", artifacts: [] },
    capabilities: ["chat"],
    approval_gates: [],
    progress_events: [],
    // P4 demo: hello_agent now showcases the generic BlueprintWorkbench. No
    // bespoke component was written - only a graph + ui.type, proving acceptance
    // #3 (a new workflow is a manifest-only change; the frontend router is
    // untouched because the generic renderer is registry-driven).
    graph: {
      nodes: [
        { id: "greet", title: "问候解析", desc: "理解用户输入意图", artifactLabel: "意图" },
        { id: "compose", title: "生成回复", desc: "组织自然语言问候", artifactType: "text", artifactLabel: "问候语" },
        { id: "review", title: "自审", desc: "检查语气与礼貌", artifactLabel: "审阅结果" },
      ],
    },
    ui: { type: "blueprint", component: "BlueprintWorkbench", title: "问候助手" },
  },
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
    // P3: dedicated node-canvas workbench. The router resolves `component`
    // against the workbenches registry and renders ManjuCraftWorkbench, so the
    // entry is a workflow-specific UI (not the generic chat/composer).
    ui: { type: "workbench", component: "ManjuCraftWorkbench", title: "漫剧工作台" },
  },
  {
    id: "image_gen",
    name: "文生图",
    description: "根据提示词生成一张图片",
    category: "media",
    icon: "image",
    version: "1.0.0",
    entry: "agents/image_gen/agent.py",
    runtime: "inprocess",
    input_schema: {
      type: "object",
      properties: {
        prompt: {
          type: "string",
          minLength: 1,
          x_ui: { control: "textarea", label: "提示词", placeholder: "描述你要生成的画面…" },
        },
        size: {
          type: "string",
          enum: ["1024x768", "1024x1024", "768x1024"],
          default: "1024x768",
          x_ui: { control: "select", label: "尺寸" },
        },
      },
      required: ["prompt"],
    },
    output_schema: {
      summary: "markdown",
      artifacts: [{ id: "image", type: "image", source: "path", label: "生成结果" }],
    },
    capabilities: ["image-generation"],
    approval_gates: [],
    progress_events: ["step_started", "step_progress", "artifact_produced"],
    // P4 demo: image_gen showcases the generic TimelineWorkbench, driven entirely
    // by a manifest graph (no bespoke component). Another acceptance-#3 proof.
    graph: {
      steps: [
        { id: "interpret", title: "提示词解读", desc: "解析画面描述与风格约束", artifactLabel: "解读" },
        { id: "generate", title: "图像生成", desc: "调用文生图模型出图", artifactType: "image", artifactLabel: "生成图" },
        { id: "post", title: "后处理", desc: "裁切 / 增强 / 加水印", artifactLabel: "成品" },
      ],
    },
    ui: { type: "timeline", component: "TimelineWorkbench", title: "文生图工作台" },
  },
];

export default manifests;
