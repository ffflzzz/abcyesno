/* ==========================================================================
   src/pages/Storyboard.test.tsx —— 分镜页三条**曾经没有测试**的契约
   --------------------------------------------------------------------------
   为什么是这几条（2026-10-02）：这一页上坏掉的三个东西都不是"报错"，而是
   **界面看起来正常、后端根本没收到对的东西**，所以只有把契约写成断言才拦得住：

   ① 「编辑镜头」弹窗原先提交 `title / duration_ms / summary / video_prompt`，
      而后端 `webwrite.EDITABLE_COLS` 只认另外 10 个列名 ⇒ **每次保存必 400**，
      分镜表一个字都改不动。修好之后如果没有这条断言，下一次改字段名照样静默复发。
   ② 画面描述还是 `[[待补]]` 占位的镜**不许送去生成** —— 那句占位本身 27 字，
      会照常进渲染清单，模型收到"请填写不少于 15 字的具体内容"这种中间指令文字，
      画出来就是烧字废图：白烧一次图片配额 + 一次视频配额。
   ③ 每镜的渲染台账状态（`v5.job_state` / `v5.job_error`）要显示出来 ——
      后端一直在给，前端以前不读 ⇒ 人只有点了生成才知道上一镜是失败的。
   ========================================================================== */

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Storyboard } from './Storyboard';
import { Api, setDriver } from '../api';
import { Store } from '../store';
import type { Project, Segment } from '../types';

const PID = 'demo-drama';
const EID = 'demo-drama-ep1';

/**
 * 后端 `v5/webwrite.py` 的 `EDITABLE_COLS` 白名单（**逐字抄**，改一边就该红）。
 * 写死在这里是故意的：这是**跨语言的两端契约**，只有把另一端的字面量钉进测试，
 * 才会出现"前端改了键名 ⇒ 测试红"，而不是"前端改了键名 ⇒ 用户点保存才知坏"。
 */
const BACKEND_EDITABLE = [
  'visual', 'dialogue', 'seconds', 'shot_type', 'angle', 'camera',
  'scene', 'visual_style', 'tail', 'sfx',
];

function shot(visual: string) {
  return {
    shot_id: EID + '-sc1-1', title: '近景·铺面', duration_sec: 6,
    rich: [{ type: 'text', value: visual }],
    content_rich: visual, content_plain: visual, content: visual,
  };
}

function seg(name: string, visual: string, extra: Record<string, unknown> = {}): Segment {
  const s = shot(visual);
  return Object.assign({
    id: EID + '-' + name, order: Number(name.slice(2)), title: '近景·铺面',
    summary: visual.slice(0, 12), video_prompt: '装配出来的提示词', duration_ms: 6000,
    keyframe: '', video: '', status: 'pending', scene: '铺面',
    scenes: [{ scene_id: EID + '-sc1', title: '铺面', shots: [s] }],
    shots: [s],
    v5: { shot_name: name, shot_type: '近景', angle: '平视', camera: '固定',
      dialogue: '纸扎匠：这单我接了。', tail: '', sfx: '环境音' },
  }, extra) as unknown as Segment;
}

function seed(segs: Segment[]): void {
  const p = {
    id: PID, name: '纸扎铺', ratio: '9:16', created_at: '2026-10-02 10:00',
    episodes: [{ id: EID, no: 1, title: '第 1 集', shots: segs.length, storyboard: { segments: segs } }],
    assets: { characters: [], scenes: [], props: [] },
  } as unknown as Project;
  Store.upsertProjects([p], true);
}

describe('Storyboard 分镜页契约', () => {
  beforeEach(() => { setDriver('http'); Store.setVendors(null); });
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });

  it('① 编辑弹窗只提交后端认的列名（曾经四个键全不认 ⇒ 每次保存 400）', async () => {
    const good = seg('LN01', '纸扎匠在逼仄的铺子里看着空钱盒，指尖刚触到盒沿又停住');
    seed([good]);
    const spy = vi.spyOn(Api, 'updateSegment').mockResolvedValue({
      shot: 'LN01', applied: { dialogue: '换过的台词。' }, skipped: [],
      invalidated: { still: true, clip: true, job: true }, note: '已作废，需重新生成',
    } as never);
    vi.spyOn(Api, 'getStoryboard').mockResolvedValue({ segments: [] } as never);

    render(<Storyboard pid={PID} eid={EID} />);
    // 面板头上的铅笔 = 编辑
    const editBtn = document.querySelector('.sb-head-actions button.icon-btn');
    expect(editBtn, '找不到「编辑」按钮').toBeTruthy();
    fireEvent.click(editBtn as Element);

    const save = await screen.findByText(/保存到分镜表/);
    // 作用域直接取全文：弹窗挂在 body 上，猜 class 只会造出"测试自己找不到"的假红
    const input = [...document.querySelectorAll('textarea, input')].find(
      (el) => (el as HTMLTextAreaElement).value === '纸扎匠：这单我接了。');
    expect(input, '对白输入框没带上盘上的初值（初值取不到 = 保存会把它清空）').toBeTruthy();
    fireEvent.change(input as HTMLTextAreaElement, { target: { value: '换过的台词。' } });
    fireEvent.click(save);

    await waitFor(() => expect(spy).toHaveBeenCalled());
    const [sid, patch] = spy.mock.calls[0];
    const keys = Object.keys(patch as Record<string, unknown>);
    expect(sid).toBe(EID + '-LN01');
    // ★ 核心断言：**不许出现后端不认的键**（旧实现就是死在这：4 个键全不在名单里）
    expect(keys.filter((k) => BACKEND_EDITABLE.indexOf(k) < 0)).toEqual([]);
    expect(keys).toContain('visual');
    expect(keys).toContain('dialogue');
    expect((patch as Record<string, unknown>).dialogue).toBe('换过的台词。');
    // 时长必须按**秒**送（后端按秒写进「时长」列），不许再送毫秒
    expect(String((patch as Record<string, unknown>).seconds)).toBe('6');
    expect(keys).not.toContain('duration_ms');
    expect(keys).not.toContain('summary');
    expect(keys).not.toContain('video_prompt');
  });

  it('①b 画面描述初值必须还原 `@资产名`（用 content_plain 会静默丢参考图绑定）', async () => {
    // ★ 画面描述 ≥15 字：弹窗对更短的描述会**拦住保存**（`parse` 的门槛），
    //   所以夹具必须给够长度，否则测的是"被校验拦住"而不是"还原 @名"。
    const withRef = seg('LN01', '纸扎匠站在柜台后面，手停在盒沿上没有动', {});
    // 模拟后端 `_to_rich` 的产物：token 数组里有一个 ref
    (withRef as unknown as { scenes: { shots: { rich: unknown[] }[] }[] }).scenes[0].shots[0].rich = [
      { type: 'ref', kind: 'character', id: '纸扎匠', name: '纸扎匠' },
      { type: 'text', value: '站在柜台后面，手停在盒沿上没有动' },
    ];
    seed([withRef]);
    vi.spyOn(Api, 'updateSegment').mockResolvedValue({} as never);
    render(<Storyboard pid={PID} eid={EID} />);
    fireEvent.click(document.querySelector('.sb-head-actions button.icon-btn') as Element);
    const save = await screen.findByText(/保存到分镜表/);
    fireEvent.click(save);
    const spy = Api.updateSegment as unknown as { mock: { calls: unknown[][] } };
    await waitFor(() => expect(spy.mock.calls.length).toBeGreaterThan(0));
    const patch = spy.mock.calls[0][1] as Record<string, unknown>;
    expect(String(patch.visual)).toBe('@纸扎匠站在柜台后面，手停在盒沿上没有动');
    expect(String(patch.visual)).not.toMatch(/基础形象|sd-asset/);
  });


  it('② 占位镜（[[待补]]）不许被送去生成，且界面说清为什么', () => {
    const ph = seg('LN02', '[[待补]] 本镜画面描述：请填写不少于 15 字的具体内容后再生成');
    seed([ph]);
    render(<Storyboard pid={PID} eid={EID} />);
    const gen = [...document.querySelectorAll('button')].filter(
      (b) => /^生成$/.test((b.textContent || '').trim()));
    expect(gen.length, '没渲染出生成按钮').toBeGreaterThan(0);
    expect(gen.every((b) => b.disabled === true), '占位镜的生成按钮必须禁用').toBe(true);
    expect(document.body.textContent).toMatch(/待补|占位/);
    // 「全选」也不该把它算进去（全选一个不能生成的镜 = 白烧）
    const all = [...document.querySelectorAll('button')].find(
      (b) => /全选/.test(b.textContent || ''));
    fireEvent.click(all as Element);
    expect((document.body.textContent || '')).toMatch(/已选择 0/);
  });

  it('③ 台账状态与失败原因要显示（后端一直在给，前端以前不读）', () => {
    const failed = seg('LN03', '镜头推进到门缝，外面在下雨，门轴锈色清晰', {
      v5: { shot_name: 'LN03', job_state: 'failed', job_error: 'HTTP 429 too many requests' },
    });
    seed([failed]);
    render(<Storyboard pid={PID} eid={EID} />);
    const t = document.body.textContent || '';
    expect(t).toMatch(/上次提交失败|失败/);
    // `job_error` 的原文要能在页面上找到（vitest 的 `toMatch` 不收第二参数，
    // 说明文字放进上面的正则注释里，别为了写 message 换成 `assert`）
    expect(t).toMatch(/429 too many requests/);
  });
});
