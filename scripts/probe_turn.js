const path = require('path');
const { GatewayClient } = require('C:/Users/Administrator/Downloads/abcyesno-v8/abcyesno-v8/electron/backend/gateway-client.js');

const URL = 'ws://127.0.0.1:9120/api/ws';
const TOKEN = process.env.HERMES_DASHBOARD_SESSION_TOKEN || 'probe-token-abc123';

const client = new GatewayClient({ url: URL, token: TOKEN });

const events = [];
function logEv(type, params) {
  const p = params && params.payload !== undefined ? params.payload : params;
  const txt = p && typeof p.text === 'string' ? p.text.slice(0, 80) : '';
  console.log(`[EVT] ${type}${txt ? ' :: ' + txt : ''}`);
  events.push({ type, params });
}
client.on('event', (type, params) => logEv(type, params));
client.on('open', () => console.log('[WS] open'));
client.on('error', (e) => console.log('[WS] error', e && e.message));
client.on('close', (c, r) => console.log('[WS] close', c, r && r.toString()));

(async () => {
  await client.connect();
  console.log('[STEP] connected, creating session...');
  const created = await client.request('session.create', { close_on_disconnect: false }, 30000);
  const sid = created && (created.session_id || created.id);
  console.log('[STEP] session created:', sid);
  // wait for session.info
  await new Promise((resolve) => {
    const t = setTimeout(resolve, 8000);
    const h = (type, params) => {
      if (type === 'session.info') { clearTimeout(t); client.off('event', h); resolve(); }
    };
    client.on('event', h);
  });
  console.log('[STEP] submitting prompt...');
  const text = 'Reply with exactly the word: PONG';
  await client.request('prompt.submit', { session_id: sid, text }, 120000).catch((e) => console.log('[prompt.submit err]', e.message));
  // wait for completion
  await new Promise((resolve) => {
    const t = setTimeout(resolve, 30000);
    const h = (type) => {
      if (type === 'message.complete' || type === 'message.end' || type === 'error') {
        clearTimeout(t); client.off('event', h); resolve();
      }
    };
    client.on('event', h);
  });
  console.log('[DONE] total events:', events.length);
  const types = {};
  for (const e of events) types[e.type] = (types[e.type] || 0) + 1;
  console.log('[SUMMARY] event types:', JSON.stringify(types));
  process.exit(0);
})().catch((e) => { console.error('FATAL', e); process.exit(1); });
