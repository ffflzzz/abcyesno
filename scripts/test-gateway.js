const { GatewayClient } = require('../electron/backend/gateway-client');

const client = new GatewayClient({ url: 'ws://127.0.0.1:9120/api/ws', token: 'testtoken' });

client.on('event', (type, params) => {
  const sid = params?.session_id || params?.sid;
  const text = params?.payload?.text;
  console.log('EVENT', type, 'sid=', sid, 'text=', typeof text === 'string' ? text.slice(0, 80) : JSON.stringify(params?.payload || {}).slice(0,200));
});
client.on('open', () => console.log('WS_OPEN'));
client.on('close', () => console.log('WS_CLOSE'));
client.on('error', (err) => console.log('WS_ERROR', err.message));

(async () => {
  await client.connect();
  const created = await client.request('session.create', { close_on_disconnect: false, model: 'agnes-2.0-flash' }, 30000);
  const sid = created?.session_id;
  console.log('SESSION', sid);
  setTimeout(() => {
    client.request('prompt.submit', { session_id: sid, text: 'hello' }, 120000).then((res) => console.log('SUBMIT_RES', JSON.stringify(res).slice(0,200))).catch((err) => console.log('SUBMIT_ERR', err.message));
  }, 500);
  setTimeout(() => process.exit(0), 25000);
})();
