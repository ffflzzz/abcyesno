// Deterministic integration test for the B-solution agui-server glue:
//   - /api/ag-ui/run registers a workflow-event subscriber + backgroundRuns entry
//   - POST /api/ag-ui/workflow-event {type:'started'}  -> marks keepOpen (SSE stays open)
//   - POST /api/ag-ui/workflow-event {type:'progress'} -> relays to SSE
//   - POST /api/ag-ui/interrupt {decision}             -> writes decision file
//   - POST /api/ag-ui/workflow-event {type:'done'}     -> closes SSE with RUN_FINISHED
//
// Uses a FAKE gateway client (never emits message.complete) so the agent turn
// stays open, letting us drive the lifecycle purely through the workflow-event
// / interrupt HTTP channels — exactly the new code path that needed coverage.
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import os from 'node:os';
import fs from 'node:fs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, '..');

process.env.HERMES_HOME = process.env.HERMES_HOME || path.join(os.homedir(), '.hermes_portable_data');

const aguiMod = await import(pathToFileURL(path.join(root, 'electron/backend/agui-server.js')).href);
const { createAgUIServer } = aguiMod.default || aguiMod;

// ---- fake gateway client -------------------------------------------------
const handlers = {};
const fakeClient = {
  ready: true,
  on(ev, h) { (handlers[ev] ||= []).push(h); return this; },
  once(ev, h) { return this.on(ev, h); },
  off(ev, h) { if (handlers[ev]) handlers[ev] = handlers[ev].filter((x) => x !== h); return this; },
  request(method) {
    if (method === 'session.create') {
      // emulate Hermes emitting session.info right after creation
      setTimeout(() => emit('event', 'session.info', { session_id: 'fake-session' }), 0);
      return Promise.resolve({ session_id: 'fake-session' });
    }
    if (method === 'session.status') return Promise.resolve({});
    if (method === 'prompt.submit') return Promise.resolve({});
    if (method === 'image.attach_bytes') return Promise.resolve({ path: 'x', bytes: 0 });
    return Promise.resolve({});
  },
};
function emit(ev, ...args) { (handlers[ev] || []).forEach((h) => h(...args)); }

// ---- fake storage --------------------------------------------------------
const mem = {};
const storage = {
  async getThreadMapping(t) { return mem[t] || null; },
  async setThreadMapping(t, s) { mem[t] = s; },
  async getAssistant() { return { id: 'default', defaultModel: 'agnes-2.5-flash', modelOverride: null }; },
  async listAssistants() { return []; },
};

const app = createAgUIServer(() => fakeClient, storage, {
  agentsDir: path.join(root, 'hermes-fork/skills/langgraph_agents/agents'),
});
const server = app.listen(0, '127.0.0.1');
await new Promise((r) => server.once('listening', r));
const port = server.address().port;
const base = `http://127.0.0.1:${port}`;
console.log(`agui-server listening on ${base}`);

// ---- helpers -------------------------------------------------------------
const WF = 'wf-r1'; // subscriber key = 'wf-' + ctx.runId
const runBody = {
  method: 'agent/run',
  body: {
    threadId: 't1',
    runId: 'r1',
    messages: [{ role: 'user', content: '请调用 langgraph_agent 工具，参数如下：{"agent_name":"manjucraft_agent","input":{"mode":"single","script":"测试"}}' }],
    forwardedProps: { skillId: 'default', assistantId: 'default', mentions: [] },
  },
};

let failures = 0;
function check(name, cond, extra = '') {
  console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${extra ? '  ' + extra : ''}`);
  if (!cond) failures++;
}

async function openRunStream() {
  const resp = await fetch(`${base}/api/ag-ui/run`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(runBody),
  });
  return resp;
}

function readSSE(resp, onChunk) {
  const reader = resp.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  return (async () => {
    while (true) {
      const { value, done } = await reader.read();
      if (done) return buf;
      buf += dec.decode(value, { stream: true });
      onChunk(buf);
    }
  })();
}

async function postWorkflowEvent(type, payload = {}) {
  return fetch(`${base}/api/ag-ui/workflow-event`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ runId: WF, type, payload }),
  }).then((r) => r.json());
}

async function postInterrupt(decision = 'approve') {
  return fetch(`${base}/api/ag-ui/interrupt`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ workflowRunId: WF, decision }),
  }).then((r) => r.json());
}

// ---- run the scenario ----------------------------------------------------
const collected = [];
const streamDone = openRunStream().then((resp) => {
  check('run HTTP 200', resp.status === 200, `status=${resp.status}`);
  return readSSE(resp, (buf) => { collected.push(buf); });
});

// give the run handler time to register subscriber + emit RUN_STARTED
await new Promise((r) => setTimeout(r, 500));

const sseSoFar = () => collected.join('');
check('RUN_STARTED emitted', sseSoFar().includes('RUN_STARTED'));
check('no premature RUN_FINISHED', !sseSoFar().includes('RUN_FINISHED'),
  `(stream still open after 500ms = keep-open working)`);

// 1) workflow.started -> keepOpen
const r1 = await postWorkflowEvent('started', { agent: 'manjucraft_agent' });
check('workflow.started accepted', r1.status === 'ok', JSON.stringify(r1));
await new Promise((r) => setTimeout(r, 150));
check('still open after started', !sseSoFar().includes('RUN_FINISHED'));

// 2) workflow.progress -> relayed
const r2 = await postWorkflowEvent('progress', { stage: 'generate_keyframes', pct: 30 });
check('workflow.progress accepted', r2.status === 'ok', JSON.stringify(r2));
await new Promise((r) => setTimeout(r, 150));
check('progress relayed to SSE', sseSoFar().includes('workflow.progress'));

// 3) interrupt -> decision file written
const r3 = await postInterrupt('approve');
check('interrupt accepted', r3.status === 'ok', JSON.stringify(r3));
const hitlDir = path.join(process.env.HERMES_HOME, 'workflow_hitl');
const decisionFile = path.join(hitlDir, `${WF}.json`);
let decisionOk = false;
try {
  const d = JSON.parse(fs.readFileSync(decisionFile, 'utf-8'));
  decisionOk = d.decision === 'approve';
} catch (_) {}
check('decision file written with approve', decisionOk, decisionFile);

// 4) workflow.done -> SSE closed with RUN_FINISHED
const r4 = await postWorkflowEvent('done', { status: 'done' });
check('workflow.done accepted', r4.status === 'ok', JSON.stringify(r4));

const finalBuf = await streamDone; // resolves when SSE ends
check('SSE closed after done', true);
check('RUN_FINISHED emitted', finalBuf.includes('RUN_FINISHED'));
check('no RUN_ERROR', !finalBuf.includes('RUN_ERROR'));

// cleanup decision file (best-effort)
try { fs.unlinkSync(decisionFile); } catch (_) {}

server.close();
console.log(`\n${failures === 0 ? 'ALL PASS' : failures + ' FAILED'}`);
process.exit(failures === 0 ? 0 : 1);
