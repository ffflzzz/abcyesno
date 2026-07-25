const { createAgUIServer } = require('C:/Users/Administrator/Downloads/abcyesno-v8/abcyesno-v8/electron/backend/agui-server.js');
const { GatewayClient } = require('C:/Users/Administrator/Downloads/abcyesno-v8/abcyesno-v8/electron/backend/gateway-client.js');
const { EventEncoder } = require('@ag-ui/encoder');

const HERMES_WS = 'ws://127.0.0.1:9120/api/ws';
const TOKEN = 'probe-token-abc123';
const AGUI_PORT = 9122;
const AGENTS_DIR = 'C:/Users/Administrator/Downloads/abcyesno-v8/abcyesno-v8/hermes-fork/skills/langgraph_agents/agents';

// minimal in-memory Storage mock
const threadMap = {};
const assistants = [{ id: 'default', name: 'ABC', description: '默认助手', skillId: 'default' }];
const storage = {
  getThreadMapping: async (tid) => threadMap[tid] || null,
  setThreadMapping: async (tid, sid) => { threadMap[tid] = sid; },
  getAssistant: async (id) => assistants.find((a) => a.id === id) || null,
  listAssistants: async () => assistants,
};

const gatewayClient = new GatewayClient({ url: HERMES_WS, token: TOKEN });

const app = createAgUIServer(() => gatewayClient, storage, { agentsDir: AGENTS_DIR });

function decodeFrame(raw) {
  // raw is a single SSE frame chunk; split by \n\n, each line data:
  const frames = raw.split('\n\n');
  const out = [];
  for (const f of frames) {
    const line = f.trim();
    if (line.startsWith('data:')) {
      try { out.push(JSON.parse(line.slice(5).trim())); } catch (_) {}
    }
  }
  return out;
}

(async () => {
  await gatewayClient.connect();
  console.log('[WS] gateway connected');

  await new Promise((resolve) => {
    const srv = app.listen(AGUI_PORT, '127.0.0.1', () => { console.log(`[HTTP] agui bridge on ${AGUI_PORT}`); resolve(); });
    srv.on('error', (e) => { console.error('listen err', e.message); process.exit(1); });
  });

  const threadId = 'probe-thread-1';
  const runId = 'probe-run-1';
  const body = {
    method: 'agent/run',
    threadId,
    runId,
    messages: [{ id: 'u1', role: 'user', content: 'Reply with exactly the word: PONG' }],
    forwardedProps: { assistantId: 'default', skillId: 'default', model: 'agnes-2.0-flash', mentions: [] },
  };

  console.log('[HTTP] POST /api/ag-ui/run ...');
  const res = await fetch(`http://127.0.0.1:${AGUI_PORT}/api/ag-ui/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify(body),
  });
  console.log('[HTTP] status', res.status, 'content-type', res.headers.get('content-type'));
  if (!res.body) { console.log('NO BODY'); process.exit(1); }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  let count = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) { console.log('[SSE] stream done'); break; }
    buf += decoder.decode(value, { stream: true });
    const frames = buf.split('\n\n');
    buf = frames.pop() || '';
    for (const fr of frames) {
      const line = fr.trim();
      if (!line.startsWith('data:')) continue;
      let ev;
      try { ev = JSON.parse(line.slice(5).trim()); } catch (_) { continue; }
      count++;
      const extra = ev.type === 'TEXT_MESSAGE_CONTENT' ? ` :: "${ev.delta}"`
        : ev.type === 'CUSTOM' ? ` :: ${ev.name}${ev.value && ev.value.text ? ' ' + ev.value.text.slice(0,40) : ''}`
        : '';
      console.log(`[SSE#${count}] ${ev.type}${extra}`);
    }
  }
  console.log(`[DONE] total SSE frames: ${count}`);
  process.exit(0);
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
