/* ==========================================================================
   src/api.test.ts —— 验证层示例①：**直接跑真模块**（不桩 Api）
   --------------------------------------------------------------------------
   为什么这几条值得写（旧版 skill 里记过同型教训）：
     「凡改动落在 core/*.js，必须有至少一条断言走真模块」——
     因为桩住 `Api.*` 的测试**根本不进 api.js 的代码**，
     像"`const` 被重新赋值""合并顺序反了"这类运行时错，桩全是盲区。
   这批断言就是那个位置的替代品：跑的是 `src/api.ts` 本身。
   ========================================================================== */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Api, setDriver } from './api';

/** 造一个「后端答复了」的响应（带本项目的信封）。 */
function backendReply(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('http() 的两类失败必须可区分', () => {
  beforeEach(() => {
    setDriver('http');
    localStorage.setItem('pavo.api.baseUrl', 'http://127.0.0.1:8787');
  });
  afterEach(() => vi.unstubAllGlobals());

  it('后端答复了 404 ⇒ 带 status / fromBackend，且**不是** isNetwork', async () => {
    vi.stubGlobal('fetch', () => Promise.resolve(
      backendReply(404, { code: '404', message: '项目不存在：null' }),
    ));
    const err = await Api.getProgress('null').catch((e) => e) as Error & {
      status?: number; fromBackend?: boolean; isNetwork?: boolean;
    };
    // ★ 这两条就是「别把后端明确回的 404 说成『没连上』」的判据
    expect(err.message).toBe('项目不存在：null');
    expect(err.status).toBe(404);
    expect(err.fromBackend).toBe(true);
    expect(err.isNetwork).toBeUndefined();
  });

  it('联系不上 ⇒ isNetwork，且**没有** fromBackend', async () => {
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('Failed to fetch')));
    const err = await Api.getProgress('p1').catch((e) => e) as Error & {
      status?: number; fromBackend?: boolean; isNetwork?: boolean;
    };
    expect(err.isNetwork).toBe(true);
    expect(err.fromBackend).toBeUndefined();
    expect(err.status).toBeUndefined();
  });
});

describe('ep 必须从 eid 推导（写死 ||1 会把第 2 集渲成第 1 集）', () => {
  beforeEach(() => {
    setDriver('http');
    localStorage.setItem('pavo.api.baseUrl', 'http://127.0.0.1:8787');
  });
  afterEach(() => vi.unstubAllGlobals());

  it('eid=<pid>-ep2 ⇒ 请求体里 ep=2（旧版实测踩到 ep=1）', async () => {
    let captured: Record<string, unknown> | null = null;
    vi.stubGlobal('fetch', (_url: string, opt: RequestInit) => {
      captured = JSON.parse(String(opt.body)) as Record<string, unknown>;
      return Promise.resolve(backendReply(200, { code: '000000', data: { run_id: 'r1' } }));
    });
    await Api.generateMedia('p1', 'p1-ep2', 'keyframe', { segment_ids: ['s1', 's2'] });
    expect(captured).not.toBeNull();
    expect(captured!.ep).toBe(2);
    expect(captured!.pid).toBe('p1');
    expect(captured!.segment_ids).toEqual(['s1', 's2']);
    expect(captured!.segment_id).toBeUndefined();       // 必须被 delete 掉
  });

  it('单镜 `segment_id` 会被统一成 `segment_ids` 数组', async () => {
    let captured: Record<string, unknown> | null = null;
    vi.stubGlobal('fetch', (_url: string, opt: RequestInit) => {
      captured = JSON.parse(String(opt.body)) as Record<string, unknown>;
      return Promise.resolve(backendReply(200, { code: '000000', data: {} }));
    });
    await Api.generateMedia('p1', 'p1-ep1', 'video', { segment_id: 's9' });
    expect(captured!.segment_ids).toEqual(['s9']);
  });
});
