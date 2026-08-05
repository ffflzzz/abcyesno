/**
 * uiBus — 轻量模块级事件总线，用于解耦「后端推送的通知 / 后台任务完成」等
 * 瞬时 UI 事件与具体的会话状态（sess）。这些事件不属于某条消息，也不该污染
 * sess 的可序列化快照，所以用独立总线投递，由 <Toasts> 订阅渲染。
 *
 * 事件类型：
 *  - TOAST_SHOW  : 显示一条 toast  { key?, level?, text?, ttlMs?, kind? }
 *  - TOAST_CLEAR : 关闭指定 key 的 toast { key }
 */

const showListeners = new Set();
const clearListeners = new Set();

export function onToastShow(cb) {
  showListeners.add(cb);
  return () => showListeners.delete(cb);
}

export function onToastClear(cb) {
  clearListeners.add(cb);
  return () => clearListeners.delete(cb);
}

export function emitToastShow(payload) {
  showListeners.forEach((cb) => {
    try {
      cb(payload);
    } catch (err) {
      // 单个订阅者出错不应中断其余投递
      console.error("uiBus toast:show listener error", err);
    }
  });
}

export function emitToastClear(payload) {
  clearListeners.forEach((cb) => {
    try {
      cb(payload);
    } catch (err) {
      console.error("uiBus toast:clear listener error", err);
    }
  });
}
