// 设置面板「本地出片服务」的无头验收 harness：渲染**真实的 SettingsPanel**
// （不是复刻的假卡片），window.hermes 用罐头数据顶掉 IPC。
//
// 为什么值得单独立一个 harness：这张卡片是跨进程功能（Electron → 短剧后台 HTTP），
// 后端那侧已由 `shortdrama/v5/tests_local_services.py` 用本机假 ComfyUI 真跑过五条
// 路由；这里要证明的是**面板这一侧的状态机与排版** —— 探测结果摊开、选了机器才
// 出现工作流入口、参数没指全时接入被拦住、窄窗口不溢出。这些光过构建看不出来。
import React from "react";
import { createRoot } from "react-dom/client";
import SettingsPanel from "../../src/components/SettingsPanel.jsx";
import { TtsProvider } from "../../src/hooks/useTts.jsx";

window.__stub = {
  connectCalls: [],
  probes: 0,
};

const DEVICE = { name: "NVIDIA GeForce RTX 3060", type: "cuda", vram_gb: 12.0 };
const LIVE = {
  address: "http://127.0.0.1:8188",
  reachable: true,
  kind: "comfyui",
  reason: "",
  comfyui_version: "0.3.40",
  devices: [DEVICE],
  h3_nodes: ["MiniMaxH3Fl2vaSampler"],
  model_files: ["minimax_h3_v1.safetensors"],
};
const DEAD = {
  address: "http://192.168.1.31:8188",
  reachable: false,
  kind: "",
  reason: "连不上 http://192.168.1.31:8188（握手超时 ⇒ 跨网/VPN 不通，或那台机器不允许这个端口进来）",
  comfyui_version: "",
  devices: [],
  h3_nodes: [],
  model_files: [],
};

const STATUS_IDLE = {
  profile: {},
  current_video_vendor: "agnes",
  current_image_vendor: "agnes",
  registered: false,
  source_note: "",
  notes: [],
  default_address: "http://127.0.0.1:8188",
  env_key: "SHORTDRAMA_VIDEO_VENDOR",
};

const STATUS_ON = {
  ...STATUS_IDLE,
  profile: {
    vendor: "comfyui",
    address: LIVE.address,
    default: true,
    fps: 24,
    seconds_min: 4,
    seconds_max: 12,
    detected: { comfyui_version: "0.3.40", devices: [DEVICE] },
    workflow_exists: true,
  },
  current_video_vendor: "comfyui",
  registered: true,
  source_note: "视频厂商 = comfyui（来源：本机接入档案 http://127.0.0.1:8188）",
  notes: [
    "图片（静帧）仍走 agnes：本地只接了视频。面板选到 ComfyUI 生图会直接报错。",
    "reference / pack 档的身份靠多张参考图，本机工作流只有 1 个图位，多余的图会被丢弃。",
    "秒数→帧数按 24 fps 与 4n+1 网格换算，与云端按秒收费的口径不同。",
  ],
};

function inspectOf(missing) {
  const mapping = {
    prompt: { node: "2", field: "text", class_type: "MiniMaxH3Fl2vaSampler" },
    image: { node: "1", field: "image", class_type: "LoadImage" },
    width: { node: "2", field: "width", class_type: "MiniMaxH3Fl2vaSampler" },
    height: { node: "2", field: "height", class_type: "MiniMaxH3Fl2vaSampler" },
    frames: { node: "2", field: "length", class_type: "MiniMaxH3Fl2vaSampler" },
  };
  (missing || []).forEach((r) => delete mapping[r]);
  return {
    ok: true,
    nodes: 4,
    // 真实来历文案由后端拼（`resolve_workflow`），面板只回显 —— 这里照形状给
    source: "它最近跑过的那一次（队列号 7，产出 h3_take7.mp4）",
    required: ["prompt", "image", "frames"],
    mapping,
    missing: missing || [],
  };
}

window.hermes = {
  getVersion: () => "1.5.6",
  getUpdaterState: async () => null,
  onUpdaterState: () => {},
  offUpdaterState: () => {},
  openDataDir: async () => ({ success: true }),
  openExternal: () => {},
  selectFile: async () => "C:/Users/you/ComfyUI/user/default/workflows/h3_i2v.api.json",
  localMediaCall: async (action, params) => {
    if (action === "probe") {
      window.__stub.probes += 1;
      return { ok: true, data: { found: [LIVE], tried: [LIVE, DEAD], connected: false, profile: {} } };
    }
    if (action === "status") return { ok: true, data: STATUS_IDLE };
    if (action === "inspect") {
      // 用例可以指定「这几项认不出来」，用来验手填那一支（run.mjs 设 __stub.missing）
      const missing = window.__stub.missing || null;
      return { ok: true, data: missing ? inspectOf(missing) : inspectOf([]) };
    }
    if (action === "connect") {
      window.__stub.connectCalls.push(params || {});
      return { ok: true, data: { ok: true, status: STATUS_ON, mapping_suggested: inspectOf([]) } };
    }
    if (action === "default") return { ok: true, data: { ok: true, status: STATUS_ON } };
    if (action === "forget") {
      return { ok: true, data: { ok: true, message: "已断开", status: STATUS_IDLE } };
    }
    return { ok: false, error: "stub 不认的动作：" + action };
  },
};

const root = createRoot(document.getElementById("root"));
// `SettingsPanel` 里的语音分类要用 `useTts()`，而那个钩子必须在 `TtsProvider` 下
// —— 真应用里 Provider 在 App 根部，harness 复刻这一层，否则整棵树直接抛错。
root.render(
  <TtsProvider>
    <SettingsPanel
      apiKey="cpk-abcdefgh12345678"
      hasApiKey
      apiKeys={{ main: { set: true, masked: "cpk-...5678" }, image: { set: false }, video: { set: false }, fallback: { set: false } }}
      model="agnes-3.0-flash"
      theme="dark"
      version="1.5.6"
      onThemeChange={() => {}}
      onEditApiKey={() => {}}
      onClearApiKey={() => {}}
      onOpenWechatBind={() => {}}
      onClose={() => {}}
    />
  </TtsProvider>
);
