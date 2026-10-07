/**
 * 更新提醒卡的测试床：挂真 UpdatePopup + 假 window.hermes。
 * 假 hermes 与主进程同契约（getUpdaterState / onUpdaterState / updaterPopupAction），
 * 点击后由测试决定状态怎么变，这样验的是卡片自己的状态渲染与按钮去处。
 */
import React from 'react';
import { createRoot } from 'react-dom/client';
import UpdatePopup from '../../src/components/UpdatePopup.jsx';

const listeners = new Set();
const ctl = {
  actions: [],
  snapshot: { supported: true, status: 'idle', info: null, progress: null, error: null },
  set(patch) {
    ctl.snapshot = {
      supported: true,
      status: 'idle',
      info: null,
      progress: null,
      error: null,
      ...ctl.snapshot,
      ...patch,
    };
    [...listeners].forEach((cb) => cb({ ...ctl.snapshot }));
  },
};
window.__ctl = ctl;

window.hermes = {
  getUpdaterState: async () => ({ ...ctl.snapshot }),
  onUpdaterState: (cb) => listeners.add(cb),
  offUpdaterState: (cb) => listeners.delete(cb),
  updaterPopupAction: async (action) => {
    ctl.actions.push(action);
    if (action === 'download') {
      ctl.set({ status: 'downloading', progress: { percent: 0, transferred: 0, total: 122000000 } });
    }
    return true;
  },
};

createRoot(document.getElementById('root')).render(<UpdatePopup />);
