/* ==========================================================================
   src/pages/Canvas.test.tsx —— 画布入口的放行判据
   --------------------------------------------------------------------------
   这批断言只围着一件事：**入口能不能点，必须按"画布真有几格"定，不按分镜表镜数定。**

   真实事故（2026-10-04）：安装包数据目录里那颗唯一可点的按钮写着「第1 集 · 13镜」，
   而那一集盘上一格产物都没有 ⇒ 点进去是画布应用的一句「接口返回里没有节点」，
   人停在列表页，也就是用户报的"点画布里的项目进不去"。

   ⇒ 每个用例里 `shots`（分镜表镜数）和 `canvas_nodes`（画布格数）**故意给不同的值**：
     入口要是又退回按镜数放行，标签会立刻从「37格」变回「18镜」，测试就红。
   ========================================================================== */

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Canvas } from './Placeholders';
import { Store } from '../store';
import { setDriver } from '../api';
import type { Project } from '../types';

const NO_STILLS = '第 1 集没有静帧登记表（stills.json）⇒ 画布里没有逐镜格';

function mk(eps: Array<{ no: number; shots: number; nodes?: number; warns?: string[] }>): Project {
  return {
    id: 'paste-0920-1949',
    name: '修表匠的最后一块表',
    ratio: '9:16',
    created_at: '2026-09-20 20:54',
    episodes: eps.map((e) => ({
      id: `paste-0920-1949-ep${e.no}`, no: e.no, title: `第${e.no}集`,
      shots: e.shots, canvas_nodes: e.nodes, canvas_warnings: e.warns,
    })),
    assets: { characters: [], scenes: [], props: [] },
  };
}

/** 那颗「第N 集 · …」按钮（按集号找，别按文案找 —— 文案正是要断言的东西）。 */
function epButton(no: number): HTMLButtonElement {
  return screen.getByRole('button', { name: new RegExp(`^第${no} 集`) }) as HTMLButtonElement;
}

describe('画布入口', () => {
  beforeEach(() => {
    setDriver('http');
    Store.upsertProjects([], true);
    // `location` 要被换成可写的桩：兜底导航是往 location.href 赋值，
    // jsdom 的真 location 既读不回写、也不让写。
    vi.stubGlobal('location', { href: '', origin: 'http://127.0.0.1:8788' });
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it('★ 有分镜表、画布 0 格 ⇒ 按钮灰掉，理由直接写在卡片上（不靠 hover 才看得见）', () => {
    Store.upsertProjects([mk([{ no: 1, shots: 13, nodes: 0, warns: [NO_STILLS] }])], true);
    render(<Canvas />);
    const btn = epButton(1);
    expect(btn.disabled).toBe(true);
    expect(btn.textContent).toContain('空');
    expect(screen.getByText(/点不开/).textContent).toContain('没有静帧登记表');
  });

  it('★ 画布有格 ⇒ 按钮放行，标签上是**格数**不是镜数', () => {
    Store.upsertProjects([mk([{ no: 1, shots: 18, nodes: 37 }])], true);
    render(<Canvas />);
    const btn = epButton(1);
    expect(btn.disabled).toBe(false);
    expect(btn.textContent).toContain('37格');
    expect(btn.textContent).not.toContain('18镜');
  });

  it('★ 后端没给格数读数 ⇒ 按 0 处理，**不许**猜成"有"', () => {
    Store.upsertProjects([mk([{ no: 1, shots: 13 }])], true);
    render(<Canvas />);
    expect(epButton(1).disabled).toBe(true);
  });

  it('点按钮 ⇒ 新标签能开就用新标签，当前页不动', () => {
    Store.upsertProjects([mk([{ no: 1, shots: 18, nodes: 37 }])], true);
    const open = vi.fn((_url: string) => ({ closed: false }) as unknown as Window);
    vi.stubGlobal('open', open);
    render(<Canvas />);
    fireEvent.click(epButton(1));
    expect(open).toHaveBeenCalledTimes(1);
    expect(String(open.mock.calls[0][0])).toContain('/atelier/canvas?from=');
    expect((globalThis as unknown as { location: { href: string } }).location.href).toBe('');
  });

  it('★ 新标签被宿主拦下（返回 null）⇒ 回落在当前标签内导航：一定有出口', () => {
    // Abcyesno 桌面应用把本页嵌在 <webview> 里，宿主对 guest 的 window.open
    // 一律 deny、返回 null。没有这条回落，点按钮就是"什么都没发生"。
    Store.upsertProjects([mk([{ no: 1, shots: 18, nodes: 37 }])], true);
    vi.stubGlobal('open', vi.fn(() => null));
    render(<Canvas />);
    fireEvent.click(epButton(1));
    const href = (globalThis as unknown as { location: { href: string } }).location.href;
    expect(href).toContain('/atelier/canvas?from=');
    // from 必须是**绝对**地址：画布与后端不同源时相对地址会解析到画布那个源 ⇒ 每格破图
    expect(href).toContain(encodeURIComponent('http://127.0.0.1:8788/v1/pixa'));
  });

  it('整张卡也可点（进第一集有内容的），点按钮不会重复开两次', () => {
    Store.upsertProjects([mk([
      { no: 1, shots: 13, nodes: 0, warns: [NO_STILLS] },
      { no: 12, shots: 20, nodes: 41 },
    ])], true);
    const open = vi.fn((_url: string) => ({ closed: false }) as unknown as Window);
    vi.stubGlobal('open', open);
    render(<Canvas />);
    fireEvent.click(screen.getByText('修表匠的最后一块表'));
    expect(open).toHaveBeenCalledTimes(1);
    // 第 1 集是空的 ⇒ 卡片该进**第 12 集**
    expect(String(open.mock.calls[0][0])).toContain('ep%3D12');
    open.mockClear();
    fireEvent.click(epButton(12));
    expect(open).toHaveBeenCalledTimes(1);
  });
});
