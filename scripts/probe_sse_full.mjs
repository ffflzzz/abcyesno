// Comprehensive SSE probe: log every event type + truncated value.
const threadId = 'probe-full-' + Date.now();
const runId = 'run-' + Date.now();
const body = JSON.stringify({
  method: 'agent/run',
  threadId,
  runId,
  messages: [{ id: 'u1', role: 'user', content: '用 Python 写一个函数计算斐波那契数列，先想一想实现思路再写代码' }],
  forwardedProps: { assistantId: 'default' },
});
fetch('http://127.0.0.1:9121/api/ag-ui/run', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
  body,
}).then(async (res) => {
  console.log('status', res.status);
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  const seen = {};
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, idx).trim();
      buf = buf.slice(idx + 1);
      if (!line.startsWith('data:')) continue;
      const json = line.slice(5).trim();
      if (!json) continue;
      try {
        const ev = JSON.parse(json);
        const t = ev.type || (ev.event && ev.event.params && ev.event.params.type);
        if (!t) continue;
        seen[t] = (seen[t] || 0) + 1;
        if (t === 'CUSTOM') {
          const name = ev.name || (ev.value && ev.value.name);
          const v = ev.value && (ev.value.value || ev.value);
          const txt = v && (v.text || (v.value && v.value.text));
          console.log(`CUSTOM[${name}] #${seen[t]}:`, String(txt || '').slice(0, 120));
        } else if (t === 'RUN_COMPLETED' || t === 'RUN_ERROR' || t === 'RUN_FINISHED') {
          console.log(`>>> ${t}:`, JSON.stringify(ev).slice(0, 300));
        }
      } catch (e) { /* ignore */ }
    }
  }
  console.log('--- EVENT COUNTS ---');
  console.log(JSON.stringify(seen, null, 0));
}).catch((e) => console.error('ERR', e.message));
