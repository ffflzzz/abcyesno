// Multi-session concurrency regression test.
//
//   node scripts/test-multisession/run.mjs
//
// Drives the REAL src/hooks/useAgentStream.js against a fake SSE server to
// prove per-session concurrency. The only source modification is rewriting
// the `react` / eventBus imports; all stream logic under test is verbatim.
//
// Guards the invariants that were broken before the 2026-08-01 refactor
// (a single global abortRef killed whichever stream you switched away from,
// and background output was never persisted):
//   - two sessions stream simultaneously
//   - the foreground snapshot never mixes in another session's tokens
//   - a background run completes and persists via onSettled
//   - hydrateSession refuses to overwrite a live session with a disk snapshot
//   - stop(x) affects only session x

import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, "../..");

// rAF polyfill (hook batches visible updates per frame)
globalThis.requestAnimationFrame = (fn) => setTimeout(() => fn(Date.now()), 4);

// ── Build an importable copy of the real hook ───────────────────────────
const src = fs.readFileSync(path.join(root, "src/hooks/useAgentStream.js"), "utf8");
const patched = src
  .replace(/from\s+"react"/, 'from "./mini-react.mjs"')
  .replace(/from\s+"\.\.\/contract\/eventBus\.js"/, 'from "./fake-eventbus.mjs"');
fs.writeFileSync(path.join(here, "hook.mjs"), patched);
fs.writeFileSync(
  path.join(here, "fake-eventbus.mjs"),
  "export function emitContractEvent() {}\n"
);

const { useAgentStream } = await import("./hook.mjs");
const mini = await import("./mini-react.mjs");

// ── Fake agui-server: per-thread scripted SSE streams ───────────────────
const scripts = new Map(); // threadId -> array of {delay, event}

function sse(event) {
  return `data: ${JSON.stringify(event)}\n\n`;
}

const server = http.createServer((req, res) => {
  let body = "";
  req.on("data", (c) => (body += c));
  req.on("end", async () => {
    const parsed = JSON.parse(body);
    const threadId = parsed.threadId;
    const frames = scripts.get(threadId) || [];
    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
    });
    let aborted = false;
    req.on("aborted", () => {
      aborted = true;
    });
    for (const f of frames) {
      await new Promise((r) => setTimeout(r, f.delay));
      if (aborted || res.destroyed) return;
      res.write(sse(f.event));
    }
    res.end();
  });
});

await new Promise((r) => server.listen(0, "127.0.0.1", r));
const port = server.address().port;

// ── Host component ──────────────────────────────────────────────────────
let activeSessionId = "A";
const settled = [];
const onSettled = (sid, msgs) => {
  settled.push({ sid, count: msgs.length, text: msgs.map((m) => m.content).join("") });
};

let api = null;
mini.mount(() => {
  api = useAgentStream(port, activeSessionId, { onSettled });
  return api;
});

function setActive(id) {
  activeSessionId = id;
  mini.rerender();
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ── Test scripts ────────────────────────────────────────────────────────
function textRun(prefix, chunks, perChunkDelay) {
  const mid = `${prefix}-msg`;
  const frames = [
    { delay: 10, event: { type: "RUN_STARTED", runId: `${prefix}-run` } },
    { delay: 5, event: { type: "TEXT_MESSAGE_START", role: "assistant", messageId: mid } },
  ];
  for (const c of chunks) {
    frames.push({
      delay: perChunkDelay,
      event: { type: "TEXT_MESSAGE_CONTENT", messageId: mid, delta: c },
    });
  }
  frames.push({ delay: 5, event: { type: "TEXT_MESSAGE_END", messageId: mid } });
  frames.push({ delay: 5, event: { type: "RUN_FINISHED" } });
  return frames;
}

// A: slow, 6 chunks. B: fast, 3 chunks.
scripts.set("A", textRun("A", ["a1", "a2", "a3", "a4", "a5", "a6"], 90));
scripts.set("B", textRun("B", ["b1", "b2", "b3"], 40));

const results = [];
function check(name, pass, detail) {
  results.push({ name, pass, detail });
  console.log(`${pass ? "PASS" : "FAIL"}  ${name}${detail ? `  — ${detail}` : ""}`);
}

// ── Scenario ────────────────────────────────────────────────────────────
console.log(`fake agui-server on :${port}\n`);

// 1. Start A, then start B while A is mid-flight
api.sendMessage("hello A", { threadId: "A" });
await sleep(150);
setActive("B");
api.sendMessage("hello B", { threadId: "B" });
await sleep(60);

check(
  "两条流同时在跑（runningSessionIds 含 A 和 B）",
  api.runningSessionIds.includes("A") && api.runningSessionIds.includes("B"),
  `running=[${api.runningSessionIds.join(",")}]`
);

// 2. Foreground is B — its snapshot must not contain A's tokens
const bText = api.messages.map((m) => m.content).join("|");
check(
  "前台 B 的消息不含 A 的 token",
  !bText.includes("a1") && bText.includes("hello B"),
  `B snapshot="${bText}"`
);

// 3. Wait for B to finish while A keeps streaming in the background
await sleep(300);
check(
  "B 完成后 A 仍在后台运行",
  api.runningSessionIds.includes("A"),
  `running=[${api.runningSessionIds.join(",")}]`
);

const bSettled = settled.find((s) => s.sid === "B");
check(
  "B 完成触发 onSettled 且内容完整",
  !!bSettled && bSettled.text.includes("b1b2b3"),
  bSettled ? `"${bSettled.text}"` : "未触发"
);

// 4. hydrateSession must NOT clobber the still-running A
const hydrated = api.hydrateSession("A", [{ id: "stale", role: "user", content: "STALE-FROM-DISK" }]);
check("hydrateSession 拒绝覆盖运行中的会话 A", hydrated === false, `返回 ${hydrated}`);

// 5. Switch back to A and confirm its live deltas survived
setActive("A");
const aTextMid = api.messages.map((m) => m.content).join("|");
check(
  "切回 A 看到的是内存中的实时增量，不是磁盘旧快照",
  aTextMid.includes("a1") && !aTextMid.includes("STALE-FROM-DISK"),
  `A snapshot="${aTextMid}"`
);

// 6. Let A finish
await sleep(500);
const aSettled = settled.find((s) => s.sid === "A");
check(
  "A 在后台完整跑完（6 个 chunk 一个不丢）",
  !!aSettled && aSettled.text.includes("a1a2a3a4a5a6"),
  aSettled ? `"${aSettled.text}"` : "未触发"
);

check(
  "全部会话回到 idle",
  api.runningSessionIds.length === 0,
  `running=[${api.runningSessionIds.join(",")}]`
);

// 7. Regression: stopping one session must not touch the other
scripts.set("C", textRun("C", ["c1", "c2", "c3", "c4", "c5"], 80));
scripts.set("D", textRun("D", ["d1", "d2", "d3", "d4", "d5"], 80));
setActive("C");
api.sendMessage("hello C", { threadId: "C" });
api.sendMessage("hello D", { threadId: "D" });
await sleep(200);
api.stop("C");
await sleep(60);
check(
  "stop(C) 只停 C，D 不受影响",
  !api.runningSessionIds.includes("C") && api.runningSessionIds.includes("D"),
  `running=[${api.runningSessionIds.join(",")}]`
);
await sleep(600);
const dSettled = settled.find((s) => s.sid === "D");
check(
  "D 在 C 被停后仍完整跑完",
  !!dSettled && dSettled.text.includes("d1d2d3d4d5"),
  dSettled ? `"${dSettled.text}"` : "未触发"
);

// 7b. stop() 后仍在 running 的 tool message 必须被标 interrupted（避免 UI 永久残留“执行中…”）
function toolInterruptRun() {
  return [
    { delay: 10, event: { type: "RUN_STARTED", runId: "F-run" } },
    { delay: 10, event: { type: "TOOL_CALL_START", toolCallId: "F-tool-1", toolCallName: "browser_navigate" } },
    { delay: 10, event: { type: "TOOL_CALL_START", toolCallId: "F-tool-2", toolCallName: "terminal" } },
    // 注意：故意不发 TOOL_CALL_END，模拟用户中途打断场景
  ];
}
scripts.set("F", toolInterruptRun());
setActive("F");
api.sendMessage("hello F", { threadId: "F" });
await sleep(80); // 等两条 TOOL_CALL_START 落入消息
api.stop("F");
await sleep(60);
const fMessages = api.messages.filter((m) => m.role === "tool");
const fInterruptedCount = fMessages.filter((m) => m.status === "interrupted").length;
const fStillRunningCount = fMessages.filter((m) => m.status === "running").length;
check(
  "stop() 后 running 工具卡被强制标 interrupted（不残留“执行中…”）",
  fMessages.length === 2 && fInterruptedCount === 2 && fStillRunningCount === 0,
  `tools=${JSON.stringify(fMessages.map((m) => ({ name: m.toolName, status: m.status })))}`
);

// 8. P1 事件：reasoning / subagent / usage / notification / moa / inline_diff
//    驱动真实 hook 消费新增 CUSTOM 事件，确认不崩溃且字段正确填充。
function p1Run() {
  const mid = "E-msg";
  return [
    { delay: 10, event: { type: "RUN_STARTED", runId: "E-run" } },
    { delay: 5, event: { type: "CUSTOM", name: "reasoning.delta", value: { text: "deep thought" } } },
    { delay: 5, event: { type: "CUSTOM", name: "status.update", value: { kind: "info", text: "compacting…" } } },
    { delay: 5, event: { type: "CUSTOM", name: "subagent.start", value: { subagent_id: "s1", goal: "research", status: "start" } } },
    { delay: 5, event: { type: "CUSTOM", name: "subagent.complete", value: { subagent_id: "s1", status: "complete", input_tokens: 100, output_tokens: 50, cost_usd: 0.001 } } },
    { delay: 5, event: { type: "CUSTOM", name: "moa.reference", value: { label: "m1", text: "ref content" } } },
    { delay: 5, event: { type: "CUSTOM", name: "tool.inline_diff", value: { toolCallId: "tool-1", diff: "+added\n-removed" } } },
    { delay: 5, event: { type: "CUSTOM", name: "usage.update", value: { input: 100, output: 50, reasoning: 20, total: 170, cost_usd: 0.002, context_used: 170, context_max: 128000, context_percent: 0.1 } } },
    { delay: 5, event: { type: "CUSTOM", name: "notification.show", value: { level: "info", text: "P1 toast" } } },
    { delay: 5, event: { type: "CUSTOM", name: "browser.progress", value: { message: "正在打开 example.com", level: "info" } } },
    { delay: 5, event: { type: "CUSTOM", name: "browser.progress", value: { message: "点击登录按钮", level: "info" } } },
    { delay: 5, event: { type: "CUSTOM", name: "browser.progress", value: { message: "页面未找到", level: "warn" } } },
    { delay: 5, event: { type: "TEXT_MESSAGE_START", role: "assistant", messageId: mid } },
    { delay: 5, event: { type: "TEXT_MESSAGE_CONTENT", messageId: mid, delta: "done" } },
    { delay: 5, event: { type: "TEXT_MESSAGE_END", messageId: mid } },
    { delay: 5, event: { type: "RUN_FINISHED" } },
  ];
}
scripts.set("E", p1Run());
setActive("E");
api.sendMessage("hello E", { threadId: "E" });
await sleep(300);
check(
  "P1 reasoning.delta 累积到 reasoningText",
  api.reasoningText.includes("deep thought"),
  `reasoningText="${api.reasoningText}"`
);
check(
  "P1 subagent.* 镜像到 subagents",
  Array.isArray(api.subagents) && api.subagents.length === 1 && api.subagents[0].subagent_id === "s1",
  `subagents=${JSON.stringify(api.subagents)}`
);
check(
  "P1 usage.update 注入真实用量",
  api.usage && api.usage.cost_usd === 0.002 && api.usage.total === 170,
  `usage=${JSON.stringify(api.usage)}`
);
check(
  "P1 moa.reference 累积到 moaRefs",
  Array.isArray(api.moaRefs) && api.moaRefs.length === 1 && api.moaRefs[0].label === "m1",
  `moaRefs=${JSON.stringify(api.moaRefs)}`
);
check(
  "P1 status.update 写入 statusLine",
  api.statusLine === "compacting…",
  `statusLine="${api.statusLine}"`
);
check(
  "P1 browser.progress 累积到 browserProgress",
  Array.isArray(api.browserProgress) && api.browserProgress.length === 3 &&
    api.browserProgress[2].level === "warn" && api.browserProgress[2].message === "页面未找到",
  `browserProgress=${JSON.stringify(api.browserProgress)}`
);
await sleep(60);

// ── Reasoning snapshot / delta dedup ───────────────────────────────────
// 模拟 hermes 两条独立 emit 路径（streaming delta + model_progress snapshot）
// 同时推同一段 reasoning。snapshot 必须覆盖而非累加，dedup 必须吃掉重复 suffix。
function pReasoning() {
  const mid = "F-msg";
  return [
    { delay: 10, event: { type: "RUN_STARTED", runId: "F-run" } },
    // streaming delta：先推一段
    { delay: 5, event: { type: "CUSTOM", name: "reasoning.delta", value: { text: "(•ㅅ•) formulating..." } } },
    // snapshot 到达：覆盖整个 reasoningText（关键：不能追加）
    { delay: 5, event: { type: "CUSTOM", name: "reasoning.snapshot", value: { text: "完整思考：决定搜索一下" } } },
    // 又一段 delta 续在 snapshot 之后
    { delay: 5, event: { type: "CUSTOM", name: "reasoning.delta", value: { text: "...继续推理" } } },
    // 重复 suffix：必须被 dedup 吃掉
    { delay: 5, event: { type: "CUSTOM", name: "reasoning.delta", value: { text: "...继续推理" } } },
    { delay: 5, event: { type: "TEXT_MESSAGE_START", role: "assistant", messageId: mid } },
    { delay: 5, event: { type: "TEXT_MESSAGE_CONTENT", messageId: mid, delta: "ok" } },
    { delay: 5, event: { type: "TEXT_MESSAGE_END", messageId: mid } },
    { delay: 5, event: { type: "RUN_FINISHED" } },
  ];
}
scripts.set("F", pReasoning());
setActive("F");
api.sendMessage("test reasoning snapshot", { threadId: "F" });
await sleep(200);
check(
  "F1 reasoning.snapshot 覆盖而非累加（不重复 formulating/重复 deliberating 之类）",
  api.reasoningText === "完整思考：决定搜索一下...继续推理",
  `reasoningText="${api.reasoningText}"`
);
await sleep(60);

// ── Summary ─────────────────────────────────────────────────────────────
// G1: detach-result-panel IPC contract — preload exposes it, ResultPanel
// calls it. Pure-static smoke test (the regression is a UI gesture, not a
// state machine), but it fails fast if anyone removes the wiring.
const detachRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const preloadSrc = fs.readFileSync(path.join(detachRoot, "electron", "preload.js"), "utf8");
const resultPanelSrc = fs.readFileSync(path.join(detachRoot, "src", "components", "ResultPanel.jsx"), "utf8");
const mainJsSrc = fs.readFileSync(path.join(detachRoot, "electron", "main.js"), "utf8");
const detachedAppSrc = fs.readFileSync(path.join(detachRoot, "src", "DetachedApp.jsx"), "utf8");
const mainJsxSrc = fs.readFileSync(path.join(detachRoot, "src", "main.jsx"), "utf8");
check(
  "G1 preload 暴露 detachResultPanel",
  /detachResultPanel\s*:\s*\(opts\)\s*=>/.test(preloadSrc),
  "preload.js 必须 export detachResultPanel"
);
check(
  "G2 main 处理 detach-result-panel IPC",
  /ipcMain\.handle\(['"]detach-result-panel['"]/.test(mainJsSrc),
  "main.js 必须注册 detach-result-panel handler"
);
check(
  "G3 main 支持 ?panel=result 加载",
  /panel:\s*['"]result['"]/.test(mainJsSrc),
  "main.js 必须把 panel=result 透传给 loadURL"
);
check(
  "G4 ResultPanel 含脱离按钮 + handleDetach",
  /handleDetach\s*=/.test(resultPanelSrc) && /脱离为独立窗口/.test(resultPanelSrc),
  "ResultPanel.jsx 必须有 handleDetach + title=\"脱离为独立窗口\" 按钮"
);
check(
  "G5 DetachedApp 存在并独立渲染",
  /export default function DetachedApp/.test(detachedAppSrc) &&
    /<ResultPanel/.test(detachedAppSrc) &&
    /detachHidden/.test(detachedAppSrc),
  "DetachedApp.jsx 必须导出组件、渲染 ResultPanel、传 detachHidden"
);
check(
  "G6 main.jsx 据 ?panel=result 分发",
  /isDetachedPanel\s*\(\)/.test(mainJsxSrc) && /DetachedApp/.test(mainJsxSrc),
  "main.jsx 必须根据 panel=result 渲染 DetachedApp 替代 Bootstrap"
);
// G7: detach must close the in-window panel so we don't render the same
// content in two windows (lex feedback after first deploy).
const appSrc = fs.readFileSync(path.join(detachRoot, "src", "App.jsx"), "utf8");
check(
  "G7 App.jsx 脱离后清掉 selectedWorkflowId（避免双窗口渲染）",
  /handleDetachResultPanel\s*=/.test(appSrc) &&
    /setSelectedWorkflowId\(.{0,5}'?\"?'?\s*\)/.test(appSrc) &&
    /onDetachResultPanel=\{handleDetachResultPanel\}/.test(appSrc),
  "App.jsx 必须 handleDetachResultPanel 内 setSelectedWorkflowId('') + 把 handler 传给 ResultPanel"
);
check(
  "G8 ResultPanel 用 onDetachResultPanel prop 而不是直接 IPC",
  /onDetachResultPanel/.test(resultPanelSrc) &&
    /typeof onDetachResultPanel === 'function'/.test(resultPanelSrc),
  "ResultPanel.jsx 必须用 onDetachResultPanel prop（fallback 才直接 IPC）"
);

// ── Studio window (openMode:"window") regression guards ──
const launcherAppsSrc = fs.readFileSync(
  path.join(detachRoot, "src", "contract", "manifests.generated.js"),
  "utf8"
);
const genContractSrc = fs.readFileSync(
  path.join(detachRoot, "scripts", "gen-contract.mjs"),
  "utf8"
);
check(
  "G9 preload 暴露 openAppWindow IPC",
  /openAppWindow\s*:\s*\(opts\)\s*=>/.test(preloadSrc),
  "preload.js 必须 export openAppWindow（漫剧go 独立窗口入口）"
);
check(
  "G10 main 处理 open-app-window IPC + studio 加载",
  /ipcMain\.handle\(['"]open-app-window['"]/.test(mainJsSrc) &&
    /panel:\s*['"]studio['"]/.test(mainJsSrc) &&
    /__appWindowKey/.test(mainJsSrc),
  "main.js 必须注册 open-app-window handler，并按 panel=studio 加载、用 __appWindowKey 去重"
);
check(
  "G11 gen-contract 把 openMode 写入 launcherApps（默认 tab）",
  /openMode\s*===?\s*['"]window['"]\s*\?\s*['"]window['"]\s*:\s*['"]tab['"]/.test(genContractSrc) &&
    /openMode/.test(genContractSrc),
  "gen-contract.mjs buildLauncherApps 必须按 launcher.openMode 决定 'window'|'tab'（默认 tab）"
);
check(
  "G12 生成的 launcherApps 含 openMode:window（漫剧go）",
  /"openMode":\s*"window"/.test(launcherAppsSrc),
  "manifests.generated.js 的 launcherApps 必须带 openMode:window（manjucraft_agent 声明了 launcher.openMode）"
);
check(
  "G13 App 按 openMode 派发 + studioEntry 注入 workflowId",
  /app\.openMode\s*===\s*['"]window['"]\s*\?\s*\(\)\s*=>\s*window\.hermes\?\.openAppWindow/.test(appSrc) &&
    /studioEntry/.test(appSrc) &&
    /initialWorkflowId/.test(appSrc),
  "App.jsx 必须按 openMode 派发 onClick（window→openAppWindow），并接收 studioEntry/initialWorkflowId"
);

const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
mini.unmount();
server.close();
process.exit(failed.length === 0 ? 0 : 1);
