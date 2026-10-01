/**
 * 轮询本会话的后台进程登记表（常驻"正在跑"记号的数据源）
 *
 * 病根（2026-10-01）：网关一直有 process.list 这个 RPC，桌面端从没调用过，
 * 所以 agent 后台跑一小时和闲着，界面上完全一样。
 *
 * 节奏：有在跑的进程时 5s 一次，全空闲时退到 20s；窗口重新拿到焦点立刻补一次
 * （用户回来那一刻不该看到过期数字）。
 *
 * justFinished：登记表会连"最近退出"的一起返回，但没有退出时间戳，直接全显示
 * 会让常驻条被历史进程撑满。所以只保留"这一轮从在跑变成退出"的那些，10 分钟后
 * 自动丢弃 —— 目的是让用户看到"它跑完了 / 它挂了"，而不是翻旧账。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { partition } from "../utils/backgroundProcesses.js";

const ACTIVE_MS = 5000;
const IDLE_MS = 20000;
const JUST_FINISHED_TTL_MS = 10 * 60 * 1000;

export function useBackgroundProcesses(sessionId) {
  const [running, setRunning] = useState([]);
  const [justFinished, setJustFinished] = useState([]);
  const wasRunning = useRef(new Map()); // procId -> proc 快照
  const finishedAt = useRef(new Map()); // procId -> 观察到退出的时间
  const lastTail = useRef(new Map()); // procId -> 上次看到的输出尾
  const lastChangeAt = useRef(new Map()); // procId -> 输出最后一次变化的时刻
  const timer = useRef(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    wasRunning.current = new Map();
    finishedAt.current = new Map();
    lastTail.current = new Map();
    lastChangeAt.current = new Map();
    setRunning([]);
    setJustFinished([]);
    return () => {
      mounted.current = false;
      if (timer.current) clearTimeout(timer.current);
    };
  }, [sessionId]);

  /** 输出尾行变了就把"上次变化时刻"推到 now；没变就沿用旧值。 */
  function touchSilence(proc, now) {
    const tail = String(proc.output_tail ?? proc.output_preview ?? "");
    const id = proc.session_id;
    if (lastTail.current.get(id) !== tail) {
      lastTail.current.set(id, tail);
      lastChangeAt.current.set(id, now);
    } else if (!lastChangeAt.current.has(id)) {
      lastChangeAt.current.set(id, now);
    }
    return { ...proc, _silentSeconds: Math.max(0, Math.round((now - lastChangeAt.current.get(id)) / 1000)) };
  }

  /** 清掉已经不在表里的进程的跟踪状态，避免 Map 无限增长。 */
  function pruneSilence(aliveIds) {
    for (const id of [...lastTail.current.keys()]) {
      if (!aliveIds.has(id)) {
        lastTail.current.delete(id);
        lastChangeAt.current.delete(id);
      }
    }
  }

  const tick = useCallback(async () => {
    const api = typeof window !== "undefined" ? window.hermes : null;
    if (!api || !api.listBackgroundProcesses || !sessionId) return;
    let nextDelay = IDLE_MS;
    try {
      const res = await api.listBackgroundProcesses(sessionId);
      if (!mounted.current || !res || !res.ok) return;
      const { running: run, finished } = partition(res.processes || []);

      // 刚掉出去的：上一轮在跑、这一轮在 exited 里
      const now = Date.now();
      const prevIds = wasRunning.current;
      const byId = new Map(finished.map((p) => [p.session_id, p]));
      const fresh = [];
      for (const [id, snap] of prevIds) {
        if (byId.has(id)) {
          fresh.push({ ...byId.get(id), _seenExitedAt: now });
          finishedAt.current.set(id, now);
        }
      }
      // 保留仍在 10 分钟窗口内的旧"刚退出"，并刷新其状态快照
      const carried = [];
      for (const [id, at] of finishedAt.current) {
        if (now - at > JUST_FINISHED_TTL_MS) {
          finishedAt.current.delete(id);
          continue;
        }
        if (byId.has(id) && !prevIds.has(id)) carried.push({ ...byId.get(id), _seenExitedAt: at });
      }

      const stamped = run.map((p) => touchSilence(p, now));
      pruneSilence(
        new Set([...stamped.map((p) => p.session_id), ...finishedAt.current.keys()])
      );
      wasRunning.current = new Map(stamped.map((p) => [p.session_id, p]));
      setRunning(stamped);
      setJustFinished([...fresh, ...carried]);
      nextDelay = run.length > 0 ? ACTIVE_MS : IDLE_MS;
    } catch {
      // 轮询失败保持静默：网关重启/会话未恢复时不该在界面上刷错误
      nextDelay = IDLE_MS;
    } finally {
      if (mounted.current) {
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(tick, nextDelay);
      }
    }
  }, [sessionId]);

  useEffect(() => {
    if (!sessionId) return undefined;
    tick();
    const onFocus = () => tick();
    window.addEventListener("focus", onFocus);
    return () => {
      window.removeEventListener("focus", onFocus);
      if (timer.current) clearTimeout(timer.current);
    };
  }, [sessionId, tick]);

  const refresh = useCallback(() => tick(), [tick]);

  return { running, justFinished, refresh };
}
