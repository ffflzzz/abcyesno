// src/hooks/usePaperRewriteArtifacts.js
//
// 长轮询论文重写 (paper_rewriter_agent) dashboard 后端，拉取 run 产物清单。
//
// 设计要点：
// - 后端是 Electron 主进程 spawn 的独立 FastAPI（默认 http://127.0.0.1:8765），
//   与当前 React 会话无关 —— 它是全局常驻服务，故本 hook 在 ResultPanel 挂载
//   期间始终轮询（1Hz），不绑定特定 session。
// - 服务未就绪（端口未监听 / fetch 抛错）时静默降级：runs=null，不报错、不刷屏。
// - 只要后端有 run 数据，ResultPanel 的「论文产物」tab 即出现（按需显示）。
// - 零改动 dashboard 后端：只消费其既有的 /api/runs 与 /api/runs/{id}/detail。
//
// 返回：
// {
//   loading: bool,
//   error: string | null,
//   runs: Array<{
//     run_id: string,
//     paper_title: string,
//     created_at: number,
//     chapters_written: number,
//     total_chars: number,
//     pdf_ready: boolean,
//     chapters: Array<{ id: string, chars: number }>,   // 来自 detail
//   }> | null,
//   reload: () => void,   // 手动刷新（点击 ResultPanel 刷新按钮时调用）
// }

import { useState, useEffect, useRef, useCallback } from "react";

const DASHBOARD_BASE = "http://127.0.0.1:8765";
const POLL_INTERVAL_MS = 1000;

async function fetchJSON(url) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), 1500);
  try {
    const res = await fetch(url, { signal: ctrl.signal });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } finally {
    clearTimeout(t);
  }
}

export function usePaperRewriteArtifacts() {
  const [runs, setRuns] = useState(null); // null = 尚未成功获取（服务未就绪）
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [reloadToken, setReloadToken] = useState(0);
  const inFlight = useRef(false);

  const reload = useCallback(() => setReloadToken((n) => n + 1), []);

  useEffect(() => {
    let alive = true;
    let timer = null;

    const tick = async () => {
      if (inFlight.current) return; // 跳过重叠的请求
      inFlight.current = true;
      setLoading(true);
      try {
        // 1) 列表（轻量，不含章节明细）
        const list = await fetchJSON(`${DASHBOARD_BASE}/api/runs`);
        if (!Array.isArray(list)) throw new Error("unexpected list payload");

        // 2) 对每个 run 拉 detail 拿 pdf_ready + chapters（detail 也很轻）
        const detailed = await Promise.all(
          list.map(async (r) => {
            try {
              const d = await fetchJSON(`${DASHBOARD_BASE}/api/runs/${r.run_id}/detail`);
              const chapterIds = Object.keys(d.chapters || {}).sort((a, b) => {
                // Ch1..ChN 数值排序；非 Ch 前缀的按字符串
                const ma = String(a).match(/^Ch(\d+)$/i);
                const mb = String(b).match(/^Ch(\d+)$/i);
                if (ma && mb) return Number(ma[1]) - Number(mb[1]);
                return String(a).localeCompare(String(b));
              });
              return {
                run_id: r.run_id,
                paper_title: r.paper_title || d.paper_title || r.run_id,
                created_at: r.created_at || d.created_at || 0,
                chapters_written: r.chapters_written || chapterIds.length,
                total_chars: r.total_chars || 0,
                pdf_ready: !!d.pdf_ready,
                chapters: chapterIds.map((id) => ({
                  id,
                  chars: (d.chapters[id] && d.chapters[id].chars) || 0,
                })),
              };
            } catch (_) {
              // 单个 detail 失败不致命，退化为只有列表信息
              return {
                run_id: r.run_id,
                paper_title: r.paper_title || r.run_id,
                created_at: r.created_at || 0,
                chapters_written: r.chapters_written || 0,
                total_chars: r.total_chars || 0,
                pdf_ready: false,
                chapters: [],
              };
            }
          })
        );

        if (!alive) return;
        setRuns(detailed);
        setError(null);
      } catch (err) {
        if (!alive) return;
        // 服务未就绪：静默降级，保留上一次的 runs（若有）
        setError(String(err && err.message ? err.message : err));
        if (runs === null) setRuns([]); // 首次失败也给空数组，便于 tab 隐藏判断
      } finally {
        if (alive) setLoading(false);
        inFlight.current = false;
        if (alive) timer = setTimeout(tick, POLL_INTERVAL_MS);
      }
    };

    tick();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [reloadToken]);

  return { loading, error, runs, reload };
}

// 章节内容懒加载：点开某章节时按需调用（来自 /api/chapter/{run_id}/{id}）。
// 返回 { chapter_id, content, chars } 或抛错（由调用方 catch）。
export async function fetchChapterContent(runId, chapterId) {
  return fetchJSON(`${DASHBOARD_BASE}/api/chapter/${encodeURIComponent(runId)}/${encodeURIComponent(chapterId)}`);
}

// 后端 HTTP 端点构造（预览 / 下载 / 外开都走 HTTP，不依赖本地路径，
// 避免 sandboxed renderer 的 file:// 跨目录限制与协议解析差异）。
export function paperPdfUrl(runId) {
  return `${DASHBOARD_BASE}/api/runs/${encodeURIComponent(runId)}/pdf`;
}
