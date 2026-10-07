/**
 * 设置面板「关于 Abcyesno」那一行的验收 harness：渲染**真实的 SettingsPanel**，
 * window.hermes 用罐头数据顶掉 IPC，更新状态由测试推。
 *
 * 要证明的是这条小字与按钮跟着新顺序走：查到新版 → 文案说「等你点下载」并且
 * 按钮真的能发起下载（提醒卡被关掉时，这里是唯一出口）；没下完不许出现「重启更新」。
 */
import React from "react";
import { createRoot } from "react-dom/client";
import SettingsPanel from "../../src/components/SettingsPanel.jsx";
import { TtsProvider } from "../../src/hooks/useTts.jsx";

const listeners = new Set();
const ctl = {
  calls: [],
  snapshot: { supported: true, status: "idle", info: null, progress: null, error: null },
  set(patch) {
    ctl.snapshot = { ...ctl.snapshot, ...patch };
    [...listeners].forEach((cb) => cb({ ...ctl.snapshot }));
  },
};
window.__ctl = ctl;

window.hermes = {
  getVersion: async () => "1.5.8",
  getApiKeyPool: async () => ({ main: { set: true, masked: "cpk-...5678" } }),
  getUpdaterState: async () => ({ ...ctl.snapshot }),
  onUpdaterState: (cb) => listeners.add(cb),
  offUpdaterState: (cb) => listeners.delete(cb),
  checkForUpdate: async () => { ctl.calls.push("check"); return { ...ctl.snapshot }; },
  downloadUpdate: async () => {
    ctl.calls.push("download");
    ctl.set({ status: "downloading", progress: { percent: 0, transferred: 0, total: 122000000 } });
    return true;
  },
  installUpdate: async () => { ctl.calls.push("install"); return true; },
  openExternal: (url) => { ctl.calls.push(`open:${url}`); },
  openDataDir: async () => ({ success: true }),
  openDevTools: async () => {},
  quitApp: async () => {},
  restartShortdrama: async () => ({ ok: true }),
  localMediaCall: async () => ({ ok: false, error: "本 harness 不管这张卡" }),
};

const root = createRoot(document.getElementById("root"));
// 语音分类要用 useTts()，Provider 在真应用里挂在 App 根部 —— harness 复刻这一层。
root.render(
  <TtsProvider>
    <SettingsPanel
      apiKeys={{ main: { set: true, masked: "cpk-...5678" } }}
      keyStatus="已配置"
      model="agnes-3.0-flash"
      theme="dark"
      version="1.5.8"
      onThemeChange={() => {}}
      onEditApiKey={() => {}}
      onClearApiKey={() => {}}
      onOpenWechatBind={() => {}}
      onClose={() => {}}
    />
  </TtsProvider>
);
