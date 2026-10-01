/**
 * session-reconcile 单测
 *
 * 病样本（2026-10-01 实测形状）：桌面存档停在用户最后一次主动交互（15:47），
 * 引擎侧同一会话已经多出自主唤醒回合的汇报（16:50 终版验收）。对账必须把
 * 缺的补进存档，且重复对账不能补两遍、不能把 tool 行搬进去。
 *
 * 用法：node scripts/test-session-reconcile.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const { createSessionReconciler } = require(resolve(repoRoot, 'electron/backend/session-reconcile.js'));

const APP_SID = '5ed8e4a8-6911-480f-b5f4-a8affcc2a185';
const HERMES_SID = '20261001_113450_08a77d';

function makeStorage(messages = []) {
  return {
    _messages: messages.map((m) => ({ ...m })),
    _writes: 0,
    async listThreadMappings() {
      return { [APP_SID]: HERMES_SID };
    },
    async getSession(id) {
      if (id !== APP_SID) return null;
      return { id, messages: this._messages.map((m) => ({ ...m })) };
    },
    async updateSession(id, patch) {
      if (id !== APP_SID) return null;
      this._messages = (patch.messages || []).map((m) => ({ ...m }));
      this._writes += 1;
      return { id, messages: this._messages };
    },
  };
}

function makeClient(messages) {
  return {
    ready: true,
    calls: [],
    async request(method, params) {
      this.calls.push(method);
      if (method !== 'session.history') throw new Error(`unexpected method ${method}`);
      return { count: messages.length, messages };
    },
  };
}

test('补回界面上看不到的自主唤醒回合', async () => {
  const storage = makeStorage([
    { id: '1', role: 'user', content: '跑一下恢复驱动' },
    { id: '2', role: 'assistant', content: '你先不用盯。等它出片我报。' },
  ]);
  const client = makeClient([
    { role: 'user', content: '跑一下恢复驱动' },
    { role: 'assistant', content: '你先不用盯。等它出片我报。' },
    { role: 'tool', content: '{"output": "Background process started"}' },
    { role: 'assistant', content: 'v2 停了，两集还没出片。' },
    { role: 'assistant', content: '收口完成，两集全出了。' },
    { role: 'user', content: '[IMPORTANT: Background process proc_x completed normally]' },
  ]);
  const events = [];
  const r = createSessionReconciler({
    getGatewayClient: () => client,
    storage,
    onAppended: (e) => events.push(e),
  });

  const out = await r.reconcileHermesSession(HERMES_SID);

  assert.equal(out.appended, 3, '缺 3 条就该补 3 条（tool 行不算）');
  assert.equal(storage._messages.length, 5, '必须真的落盘，不是只在返回值里数');
  assert.deepEqual(
    storage._messages.map((m) => m.content),
    [
      '跑一下恢复驱动',
      '你先不用盯。等它出片我报。',
      'v2 停了，两集还没出片。',
      '收口完成，两集全出了。',
      '[IMPORTANT: Background process proc_x completed normally]',
    ]
  );
  assert.equal(events.length, 1, '要通知渲染端刷新');
  assert.equal(events[0].appSessionId, APP_SID);
});

test('重复对账不产生第二条（幂等）', async () => {
  const storage = makeStorage([{ id: '1', role: 'assistant', content: '甲' }]);
  const client = makeClient([
    { role: 'assistant', content: '甲' },
    { role: 'assistant', content: '乙' },
  ]);
  const r = createSessionReconciler({ getGatewayClient: () => client, storage });

  assert.equal((await r.reconcileHermesSession(HERMES_SID)).appended, 1);
  const second = await r.reconcileHermesSession(HERMES_SID);
  assert.equal(second.appended, 0, '第二次不该再补');
  assert.equal(second.reason, 'in-sync');
  assert.equal(storage._messages.length, 2);
  assert.equal(storage._writes, 1, '第二次不该再写盘');
});

test('换行/空格差异不算两条不同消息', async () => {
  const storage = makeStorage([{ id: '1', role: 'assistant', content: 'A\n\n  B' }]);
  const client = makeClient([{ role: 'assistant', content: 'A B' }, { role: 'assistant', content: 'C' }]);
  const r = createSessionReconciler({ getGatewayClient: () => client, storage });

  const out = await r.reconcileHermesSession(HERMES_SID);
  assert.equal(out.appended, 1, '归一化后同一条不能重复补');
  assert.equal(storage._messages.length, 2);
});

test('tool 行不进存档', async () => {
  const storage = makeStorage([]);
  const client = makeClient([
    { role: 'tool', name: 'terminal', context: 'x' },
    { role: 'assistant', content: '只有这句该被搬' },
  ]);
  const r = createSessionReconciler({ getGatewayClient: () => client, storage });

  await r.reconcileHermesSession(HERMES_SID);
  assert.equal(storage._messages.length, 1);
  assert.equal(storage._messages[0].role, 'assistant');
});

test('网关没连上 / 会话没映射时不动存档', async () => {
  const storage = makeStorage([{ id: '1', role: 'assistant', content: '甲' }]);
  const off = createSessionReconciler({
    getGatewayClient: () => ({ ready: false }),
    storage,
  });
  assert.equal((await off.reconcileHermesSession(HERMES_SID)).reason, 'gateway-off');

  const unmapped = createSessionReconciler({
    getGatewayClient: () => makeClient([{ role: 'assistant', content: '乙' }]),
    storage: {
      async listThreadMappings() {
        return {};
      },
      async getSession() {
        return null;
      },
      async updateSession() {
        throw new Error('unmapped 时不该写盘');
      },
    },
  });
  assert.equal((await unmapped.reconcileHermesSession('other-session')).reason, 'unmapped');
  assert.equal(storage._messages.length, 1, '未对账成功时存档保持原样');
});

test('session.history 失败时静默跳过而不是抛穿', async () => {
  const storage = makeStorage([{ id: '1', role: 'assistant', content: '甲' }]);
  const client = {
    ready: true,
    async request() {
      throw new Error('no such session');
    },
  };
  const r = createSessionReconciler({ getGatewayClient: () => client, storage, log: () => {} });
  const out = await r.reconcileHermesSession(HERMES_SID);
  assert.equal(out.reason, 'history-failed');
  assert.equal(storage._messages.length, 1);
});

test('reconcileAll 覆盖多个会话', async () => {
  const storage = {
    sessions: {
      a: [{ id: '1', role: 'assistant', content: '甲' }],
      b: [{ id: '1', role: 'assistant', content: '乙' }],
    },
    async listThreadMappings() {
      return { a: 'h-a', b: 'h-b' };
    },
    async getSession(id) {
      return { id, messages: this.sessions[id] || [] };
    },
    async updateSession(id, patch) {
      this.sessions[id] = patch.messages;
      return { id, messages: patch.messages };
    },
  };
  const client = {
    ready: true,
    async request(method, params) {
      const tail = params.session_id === 'h-a' ? '甲的新汇报' : '乙的新汇报';
      return { messages: [{ role: 'assistant', content: tail }] };
    },
  };
  const r = createSessionReconciler({ getGatewayClient: () => client, storage });
  const out = await r.reconcileAll();
  assert.equal(out.checked, 2);
  assert.equal(out.appended, 2);
  assert.equal(storage.sessions.a.length, 2);
  assert.equal(storage.sessions.b.length, 2);
});
