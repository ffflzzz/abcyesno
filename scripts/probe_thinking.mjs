// Probe via the app's own agui-server SSE bridge (same path as the renderer).
import { createRequire } from "module";
const require = createRequire(import.meta.url);

const PORT = process.env.AGUI_PROBE_PORT || "9121";
const threadId = `probe-${Date.now()}`;

const res = await fetch(`http://127.0.0.1:${PORT}/api/ag-ui/run`, {
  method: "POST",
  headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
  body: JSON.stringify({
    method: "agent/run",
    threadId,
    runId: `run-${Date.now()}`,
    messages: [{ id: `u-${Date.now()}`, role: "user", content: "用一句话介绍你自己" }],
    forwardedProps: { assistantId: "default" },
  }),
});

if (!res.ok || !res.body) {
  console.error("[probe] HTTP", res.status);
  process.exit(1);
}

const counts = {};
const samples = {};
let content = "";
let thinkingText = "";
let reasoningText = "";

const reader = res.body.getReader();
const decoder = new TextDecoder();
let buffer = "";

const timer = setTimeout(() => { console.log("[probe] timeout"); finish(); }, 90000);
function finish() {
  console.log("[probe] event counts:", JSON.stringify(counts));
  console.log("[probe] thinking sample:", JSON.stringify(thinkingText.slice(0, 200)) || null);
  console.log("[probe] reasoning sample:", JSON.stringify(reasoningText.slice(0, 200)) || null);
  console.log("[probe] assembled content len:", content.length);
  console.log("[probe] content head:", JSON.stringify(content.slice(0, 300)));
  process.exit(0);
}

for (;;) {
  const { done, value } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });
  let idx;
  while ((idx = buffer.indexOf("\n")) >= 0) {
    const line = buffer.slice(0, idx).trim();
    buffer = buffer.slice(idx + 1);
    if (!line) continue;
    let ev;
    try { ev = JSON.parse(line); } catch { continue; }
    const t = ev.type || "?";
    counts[t] = (counts[t] || 0) + 1;
    if (t === "CUSTOM") {
      const n = ev.name || "";
      counts[`custom:${n}`] = (counts[`custom:${n}`] || 0) + 1;
      if (n === "thinking.delta") {
        const txt = ev.value?.text || "";
        if (txt.trim()) thinkingText += txt;
      } else if (n === "reasoning.delta") {
        const txt = ev.value?.text || "";
        if (txt.trim()) reasoningText += txt;
      } else if (n === "reasoning.snapshot") {
        reasoningText = ev.value?.text || reasoningText;
      }
    } else if (t === "TEXT_MESSAGE_CONTENT") {
      content += ev.delta || "";
    } else if (t === "RUN_FINISHED" || t === "RUN_ERROR") {
      clearTimeout(timer);
      setTimeout(finish, 300);
    }
  }
}
finish();
