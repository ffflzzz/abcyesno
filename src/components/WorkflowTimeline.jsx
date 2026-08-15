import React, { useMemo } from "react";
import ArtifactCard from "./ArtifactCard.jsx";

// Generic workflow progress timeline (L5). Consumes the full workflow.* event
// family and renders a live node graph + feed. It NEVER inspects a stage name
// — every label comes from the event payload, so adding a workflow with
// different stages requires zero component changes.
//
// Two rendering modes:
//   1. Graph mode (workflow.graph present): render the node DAG from
//      workflow.graph, and reflect live node state from workflow.trace /
//      workflow.progress. The currently-running node is highlighted with a
//      "生成中" spinner + episode counter — this is the "current node" view.
//   2. Legacy mode (no graph): fall back to the old per-event feed so older
//      non-LangGraph contract workflows keep rendering.

function statusLabel(status) {
  if (status === "done") return "完成";
  if (status === "running") return "生成中";
  if (status === "error") return "失败";
  if (status === "pending") return "等待";
  return status || "";
}

export default function WorkflowTimeline({ events = [] }) {
  const model = useMemo(() => {
    if (!events || !events.length) return null;

    const graphEv = events.find((e) => (e.type || "").endsWith("graph"));
    const graph = graphEv ? graphEv.payload || {} : null;
    const nodes = (graph && graph.nodes) || [];
    const edges = (graph && graph.edges) || [];
    const totalEpisodes = (graph && graph.totalEpisodes) || 1;
    const hasGraph = nodes.length > 0;

    // node id -> status ("pending" | "running" | "done" | "pending"(gate))
    const nodeStates = new Map();
    for (const n of nodes) nodeStates.set(n.id, "pending");

    let progress = null; // { completed, total, message }
    let currentEpisode = 0;
    let lastRunningNode = null;
    const feed = []; // non-topology events (legacy + artifact/approval/error/done)

    for (const ev of events) {
      const type = ev.type || ev.name || "";
      const p = ev.payload || {};

      if (type.endsWith("graph")) continue;

      if (type.endsWith("trace")) {
        const node = p.node;
        const st = p.status;
        if (node) {
          if (!nodeStates.has(node)) nodeStates.set(node, "pending");
          nodeStates.set(node, st);
          if (st === "running") lastRunningNode = node;
        }
        if (typeof p.episode === "number") currentEpisode = p.episode;
        continue;
      }

      if (type.endsWith("progress")) {
        const node = p.step_id;
        const st = p.status;
        if (node) {
          if (!nodeStates.has(node)) nodeStates.set(node, "pending");
          nodeStates.set(node, st);
          if (st === "running") lastRunningNode = node;
        }
        progress = { completed: p.completed, total: p.total, message: p.message, stage: p.stage };
        if (!hasGraph) feed.push({ kind: "progress", ev });
        continue;
      }

      if (type.endsWith("approval")) {
        if (p.node && nodeStates.has(p.node)) nodeStates.set(p.node, "pending");
        feed.push({ kind: "approval", ev });
        continue;
      }
      if (type.endsWith("artifact")) {
        feed.push({ kind: "artifact", ev });
        continue;
      }
      if (type.endsWith("error")) {
        feed.push({ kind: "error", ev });
        continue;
      }
      if (type.endsWith("done")) {
        feed.push({ kind: "done", ev });
        continue;
      }
    }

    // The currently-running node (graph mode). If none, fall back to the last
    // one we saw running (covers edge cases where a done re-mark was missed).
    let currentNode = null;
    for (const [id, st] of nodeStates) {
      if (st === "running") {
        currentNode = id;
        break;
      }
    }
    if (!currentNode) currentNode = lastRunningNode;

    return {
      hasGraph,
      nodes,
      edges,
      totalEpisodes,
      nodeStates,
      progress,
      currentEpisode,
      currentNode,
      feed,
    };
  }, [events]);

  if (!model) return null;

  const labelOf = (id) => {
    const n = model.nodes.find((x) => x.id === id);
    return n ? n.label : id;
  };

  return (
    <div className="workflow-timeline">
      {/* Live node graph (current-node / 生成中 view) */}
      {model.hasGraph && (
        <div className="wf-nodes">
          {model.nodes.map((n) => {
            const st = model.nodeStates.get(n.id) || "pending";
            const isCurrent = n.id === model.currentNode && st === "running";
            return (
              <div className={`wf-node ${st} ${isCurrent ? "current" : ""}`} key={n.id}>
                <span className={`wf-dot ${st}`}>
                  {st === "running" ? <span className="wf-spinner" /> : null}
                </span>
                <div className="wf-node-body">
                  <div className="wf-node-label">{n.label}</div>
                  <div className="wf-node-status">
                    {isCurrent ? (
                      <span className="wf-generating">
                        生成中
                        {model.totalEpisodes > 1
                          ? ` · 第 ${(model.currentEpisode || 1)}/${model.totalEpisodes} 集`
                          : ""}
                      </span>
                    ) : (
                      <span>{statusLabel(st)}</span>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Overall progress bar */}
      {model.progress && model.progress.total ? (
        <div className="wf-progress">
          <div className="wf-progress-bar">
            <div
              className="wf-progress-fill"
              style={{
                width: `${Math.round(
                  ((model.progress.completed || 0) / (model.progress.total || 1)) * 100
                )}%`,
              }}
            />
          </div>
          <span className="wf-progress-text">
            {model.progress.completed}/{model.progress.total}
            {model.progress.message ? ` · ${model.progress.message}` : ""}
          </span>
        </div>
      ) : null}

      {/* Event feed: artifacts / approval / error / done (legacy + graph mode) */}
      {model.feed.map((item, i) => {
        const ev = item.ev;
        const p = ev.payload || {};
        if (item.kind === "progress") {
          const pct = p.total ? Math.round((p.completed / p.total) * 100) : null;
          return (
            <div className="tl-node" key={i}>
              <span className={`tl-dot ${p.status || ""}`} />
              <div className="tl-body">
                <div className="tl-stage">
                  {p.stage || "步骤"}{" "}
                  <span className="tl-status">{statusLabel(p.status)}</span>
                </div>
                {p.message ? <div className="tl-msg">{p.message}</div> : null}
                {pct !== null ? (
                  <div className="tl-progress">
                    <div className="tl-progress-bar" style={{ width: `${pct}%` }} />
                  </div>
                ) : null}
              </div>
            </div>
          );
        }
        if (item.kind === "artifact") {
          return (
            <div className="tl-node" key={i}>
              <span className="tl-dot artifact" />
              <div className="tl-body">
                <div className="tl-stage">{p.label || "产物"}</div>
                <ArtifactCard artifact={p} />
              </div>
            </div>
          );
        }
        if (item.kind === "approval") {
          return (
            <div className="tl-node" key={i}>
              <span className="tl-dot approval" />
              <div className="tl-body">
                <div className="tl-stage">待确认：{p.label || p.gate_id}</div>
              </div>
            </div>
          );
        }
        if (item.kind === "error") {
          return (
            <div className="tl-node" key={i}>
              <span className="tl-dot error" />
              <div className="tl-body">
                <div className="tl-stage tl-error">{p.message || "出错"}</div>
              </div>
            </div>
          );
        }
        if (item.kind === "done") {
          return (
            <div className="tl-node" key={i}>
              <span className="tl-dot done" />
              <div className="tl-body">
                <div className="tl-stage">
                  {p.status === "done" ? "完成" : p.status ? `结束：${p.status}` : "完成"}
                </div>
              </div>
            </div>
          );
        }
        return null;
      })}
    </div>
  );
}
