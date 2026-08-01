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

// ── Summary ─────────────────────────────────────────────────────────────
const failed = results.filter((r) => !r.pass);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
mini.unmount();
server.close();
process.exit(failed.length === 0 ? 0 : 1);
