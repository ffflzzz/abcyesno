import React, { useRef, useState, useMemo, useCallback, useEffect } from "react";

// Live LangGraph node-trace panel.
//
// Renders the agent's DAG (topology pushed via `workflow.graph`) as a vertical
// pipeline and animates node execution as it streams in through `workflow.trace`
// (running → done, with pending for HITL gates and error for failures).
//
// Interactions:
//   • wheel            → zoom around cursor
//   • drag             → pan
//   • ─ / ＋ / ⤢      → zoom out / in / fit-to-view
//
// Pure SVG, no external graph libs. Honors prefers-reduced-motion by disabling
// the pulse (the active node still gets a static accent ring).

const NODE_W = 156;
const NODE_H = 44;
const GAP_Y = 36; // vertical gap between node boxes
const PAD = 28; // content padding

// Which phase a node belongs to — drives the left accent color.
function phaseOf(id) {
  if (["plan_episodes", "parse_script"].includes(id)) return "script";
  if (id === "generate_characters" || id === "gate_first_frame") return "assets";
  if (["batch_generate_keyframes", "consistency_check", "gate_each_scene", "fix_drift", "batch_generate_video", "generate_tts", "merge_and_concat", "generate_jianying_draft", "gate_end", "finalize_episode"].includes(id))
    return "storyboard";
  if (id === "finalize_series") return "export";
  return "script";
}

const PHASE_VAR = {
  script: "var(--accent)",
  assets: "#8b949e",
  storyboard: "#c0c8d4",
  export: "#6f7785",
};

function layoutGraph(topology) {
  const nodes = (topology && topology.nodes) || [];
  const edges = (topology && topology.edges) || [];
  const pos = {};
  let maxX = 0;
  nodes.forEach((n, i) => {
    const x = PAD;
    const y = PAD + i * (NODE_H + GAP_Y);
    pos[n.id] = { x, y, index: i };
    maxX = Math.max(maxX, x + NODE_W);
  });
  const height = PAD * 2 + nodes.length * (NODE_H + GAP_Y);
  return { pos, edges, width: maxX + PAD, height, count: nodes.length };
}

function edgePath(src, tgt) {
  // Vertical elbow from bottom-center of source to top-center of target.
  const x1 = src.x + NODE_W / 2;
  const y1 = src.y + NODE_H;
  const x2 = tgt.x + NODE_W / 2;
  const y2 = tgt.y;
  const my = (y1 + y2) / 2;
  return `M ${x1} ${y1} L ${x1} ${my} L ${x2} ${my} L ${x2} ${y2}`;
}

export default function WorkflowGraphPanel({ topology, trace, runState, episode, total }) {
  const layout = useMemo(() => layoutGraph(topology), [topology]);
  const wrapRef = useRef(null);
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const drag = useRef(null);

  // Determine the currently active (running) node for the header + pulse.
  const activeNode = useMemo(() => {
    let active = null;
    const t = trace || {};
    for (const id in t) {
      if (t[id] === "running") active = id;
    }
    return active;
  }, [trace]);

  // Fit-to-view whenever the topology or container size changes. Surplus
  // topology scrolls in the canvas (overflow:auto + SVG overflow:visible);
  // we clamp k into [0.4, 1.6] — the lower bound (0.4) is intentionally
  // tight so 9-node DAGs default to "everything visible, scroll to explore"
  // rather than cropping the tail behind the SVG viewport.
  const fit = useCallback(() => {
    const el = wrapRef.current;
    if (!el || !layout.count) return;
    const cw = el.clientWidth;
    const ch = el.clientHeight;
    const kRaw = Math.min(cw / layout.width, ch / layout.height, 1.6);
    const k = Math.max(0.4, kRaw);
    const x = (cw - layout.width * k) / 2;
    const y = Math.max(8, (ch - layout.height * k) / 2);
    setView({ x, y, k });
  }, [layout]);

  useEffect(() => {
    fit();
  }, [fit]);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => fit());
    ro.observe(el);
    return () => ro.disconnect();
  }, [fit]);

  // Mouse-wheel zoom.
//
// IMPORTANT: React 17+ installs passive listeners on the root for `wheel`
// (and `touchmove`/`touchstart`), so any synthetic `onWheel` handler that
// calls `e.preventDefault()` triggers Chrome's "Unable to preventDefault
// inside passive event listener invocation" warning — and that fires on
// every single wheel event, which (when DevTools is open) degrades to a
// visible freeze. Register a native listener with `{ passive: false }` so
// `preventDefault()` actually sticks and the warning never appears.
useEffect(() => {
  const el = wrapRef.current;
  if (!el) return;
  const handler = (e) => {
    e.preventDefault();
    const rect = el.getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    setView((v) => {
      const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
      const k = Math.min(2.6, Math.max(0.3, v.k * factor));
      // keep the point under the cursor stable while zooming
      const nx = mx - ((mx - v.x) * k) / v.k;
      const ny = my - ((my - v.y) * k) / v.k;
      return { x: nx, y: ny, k };
    });
  };
  el.addEventListener("wheel", handler, { passive: false });
  return () => el.removeEventListener("wheel", handler);
}, []);

  const onMouseDown = useCallback((e) => {
    drag.current = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
    e.currentTarget.style.cursor = "grabbing";
  }, [view]);

  const onMouseMove = useCallback((e) => {
    if (!drag.current) return;
    const dx = e.clientX - drag.current.x;
    const dy = e.clientY - drag.current.y;
    // Snapshot vx/vy into locals BEFORE enqueueing setState. React invokes
    // the updater function at render time, which may be many microtasks after
    // mousemove returned — by then onMouseUp/onMouseLeave may already have
    // nulled `drag.current`, so reading it inside the updater throws
    // "Cannot read properties of null (reading 'vx')".
    const startVx = drag.current.vx;
    const startVy = drag.current.vy;
    setView((v) => ({ ...v, x: startVx + dx, y: startVy + dy }));
  }, []);

  const onMouseUp = useCallback((e) => {
    drag.current = null;
    e.currentTarget.style.cursor = "grab";
  }, []);

  const zoomBy = useCallback((factor) => {
    const el = wrapRef.current;
    if (!el) return;
    const cw = el.clientWidth / 2;
    const ch = el.clientHeight / 2;
    setView((v) => {
      const k = Math.min(2.6, Math.max(0.3, v.k * factor));
      const nx = cw - ((cw - v.x) * k) / v.k;
      const ny = ch - ((ch - v.y) * k) / v.k;
      return { x: nx, y: ny, k };
    });
  }, []);

  const nodes = (topology && topology.nodes) || [];
  const showSeries = (total || 1) > 1;

  return (
    <div className="wf-panel">
      <div className="wf-head">
        <span className="wf-title">运行追踪</span>
        {showSeries && (
          <span className="wf-ep">
            第 {(episode || 0) + 1}/{total} 集
          </span>
        )}
        {runState === "running" && <span className="wf-live">● LIVE</span>}
        <div className="wf-zoom">
          <button title="缩小" onClick={() => zoomBy(1 / 1.2)}>－</button>
          <button title="放大" onClick={() => zoomBy(1.2)}>＋</button>
          <button title="适应窗口" onClick={fit}>⤢</button>
        </div>
      </div>

      <div
        className="wf-canvas"
        ref={wrapRef}
        onMouseDown={onMouseDown}
        onMouseMove={onMouseMove}
        onMouseUp={onMouseUp}
        onMouseLeave={onMouseUp}
      >
        <svg className="wf-svg" width="100%" height="100%">
          <g transform={`translate(${view.x},${view.y}) scale(${view.k})`}>
            {/* edges */}
            {layout.edges.map((e, i) => {
              const s = layout.pos[e.from];
              const t = layout.pos[e.to];
              if (!s || !t) return null;
              const isLoop = e.to === "parse_script" && e.from === "finalize_episode";
              const active = trace && trace[e.from] && trace[e.to] && trace[e.from] !== "idle" && trace[e.to] !== "idle";
              if (isLoop) {
                // curved bow on the left side connecting bottom of finalize_episode back to top of parse_script
                const x1 = s.x - 6;
                const y1 = s.y + NODE_H / 2;
                const x2 = t.x - 6;
                const y2 = t.y + NODE_H / 2;
                const cx = Math.min(x1, x2) - 70;
                return (
                  <path
                    key={`e${i}`}
                    className={`wf-edge wf-edge-loop ${active ? "wf-edge-active" : ""}`}
                    d={`M ${x1} ${y1} C ${cx} ${y1}, ${cx} ${y2}, ${x2} ${y2}`}
                    fill="none"
                  />
                );
              }
              return (
                <path
                  key={`e${i}`}
                  className={`wf-edge ${active ? "wf-edge-active" : ""}`}
                  d={edgePath(s, t)}
                  fill="none"
                />
              );
            })}

            {/* nodes */}
            {nodes.map((n) => {
              const p = layout.pos[n.id];
              if (!p) return null;
              let status = (trace && trace[n.id]) || "idle";
              if (runState === "done" && status !== "error") status = "done";
              const phase = phaseOf(n.id);
              const accent = PHASE_VAR[phase] || "var(--accent)";
              const cls =
                "wf-node" +
                (status === "running" ? " wf-node-running" : "") +
                (status === "done" ? " wf-node-done" : "") +
                (status === "pending" ? " wf-node-pending" : "") +
                (status === "error" ? " wf-node-error" : "");
              return (
                <g key={n.id} className={cls} transform={`translate(${p.x},${p.y})`}>
                  <rect className="wf-node-box" width={NODE_W} height={NODE_H} rx={9} />
                  <rect className="wf-node-accent" width={4} height={NODE_H} rx={2} fill={accent} />
                  <text className="wf-node-label" x={NODE_W / 2 + 2} y={NODE_H / 2} dominantBaseline="central">
                    {n.label || n.id}
                  </text>
                  {status === "done" && (
                    <text className="wf-node-check" x={NODE_W - 14} y={NODE_H / 2} dominantBaseline="central">
                      ✓
                    </text>
                  )}
                  {status === "running" && (
                    <circle className="wf-node-dot" cx={NODE_W - 14} cy={NODE_H / 2} r={4} />
                  )}
                </g>
              );
            })}
          </g>
        </svg>
        {!nodes.length && <div className="wf-empty">图结构加载中…</div>}
      </div>
    </div>
  );
}
