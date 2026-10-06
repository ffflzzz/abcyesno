/* ==========================================================================
   src/pages/PlayletList.test.tsx —— 验证层示例②：组件断言
   --------------------------------------------------------------------------
   这批断言对应旧版那套「桩 window → 调 view() → 断言 HTML 字符串」，
   换成 jsdom + testing-library 后断言的**语义完全一样**（页面里该有什么、不该有什么），
   只是不再依赖"视图函数返回字符串"这个实现细节。

   重点覆盖的是**旧版用事故换来的那条判据**：空态文案必须按真实原因分档 ——
   「本地离线取不到」和「后端说没有精选」和「拉取失败」是三种不同的说法，
   混成一句会把排查方向带偏。
   ========================================================================== */

import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PlayletList } from './PlayletList';
import { Store } from '../store';
import { setDriver } from '../api';
import type { Project } from '../types';

function mkProject(id: string, name: string): Project {
  return {
    id, name, ratio: '9:16', created_at: '2026-09-19 12:00',
    episodes: [{ id: id + '-ep1', no: 1, title: '第1集' }],
    assets: { characters: [], scenes: [], props: [] },
  };
}

describe('PlayletList', () => {
  beforeEach(() => {
    setDriver('http');
    Store.upsertProjects([], true);
    Store.setStyles([]);
  });
  afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

  it('我的项目为空 ⇒ 提示去创建（不说"离线"）', () => {
    render(<PlayletList />);
    expect(screen.getByText('还没有项目，先在上方创建一个吧')).toBeTruthy();
  });

  it('有项目 ⇒ 渲染卡片（名称 + 集数）', () => {
    Store.upsertProjects([mkProject('p1', '借脸')], true);
    render(<PlayletList />);
    expect(screen.getByText('借脸')).toBeTruthy();
    expect(screen.getByText(/1集/)).toBeTruthy();
  });

  it('★ 空态分档：http 驱动下拉取失败 ⇒ 说"失败"且说明我的项目不受影响', async () => {
    vi.stubGlobal('fetch', () => Promise.reject(new TypeError('Failed to fetch')));
    render(<PlayletList />);
    fireEvent.click(screen.getByText('精选项目'));
    await waitFor(() => {
      expect(screen.getByText(/精选项目拉取失败：后端未连通/)).toBeTruthy();
    });
    // 后半句是关键：别让人以为"我的项目"也坏了
    expect(screen.getByText(/我的项目不受影响/)).toBeTruthy();
  });

  it('★ 空态分档：后端答复了 0 条 ⇒ 说"后端暂无精选"，**不许**说成离线或失败', async () => {
    vi.stubGlobal('fetch', () => Promise.resolve(new Response(
      JSON.stringify({ code: '000000', data: { list: [] } }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    )));
    render(<PlayletList />);
    fireEvent.click(screen.getByText('精选项目'));
    await waitFor(() => {
      expect(screen.getByText(/后端暂无标记为精选的项目/)).toBeTruthy();
    });
    expect(screen.queryByText(/拉取失败/)).toBeNull();
    expect(screen.queryByText(/离线/)).toBeNull();
  });

  it('★ 空态分档：local 驱动（离线模式）⇒ 只有这一档可以说"离线"', () => {
    setDriver('local');
    render(<PlayletList />);
    fireEvent.click(screen.getByText('精选项目'));
    expect(screen.getByText(/本地离线模式取不到/)).toBeTruthy();
  });

  it('AI 面板提示不许把离线模式说成"能跑"', () => {
    setDriver('local');
    render(<PlayletList />);
    fireEvent.click(screen.getByText('AI生成剧本'));
    expect(screen.getByText(/AI 创作需要后端/)).toBeTruthy();
  });
});
