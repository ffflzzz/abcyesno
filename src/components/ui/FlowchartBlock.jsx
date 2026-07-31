import React from "react";

/**
 * FlowchartBlock — 流程图 / 架构图 (spec §5.2)
 * props: { nodes:[{id,label,shape?,status?}], edges:[{from,to,label?}], direction?:'LR'|'TB' }
 * 用 CSS flex 线性布局（MVP 不引入 mermaid/dagre）。direction=TB 纵向，LR 横向。
 */
const MAX_NODES = 50;

const STATUS_CLASS = {
  done: "ui-fc-done",
  active: "ui-fc-active",
  pending: "ui-fc-pending",
};

export default function FlowchartBlock({ nodes = [], edges = [], direction = "TB" }) {
  const safeNodes = Array.isArray(nodes) ? nodes.slice(0, MAX_NODES) : [];
  const safeEdges = Array.isArray(edges) ? edges.slice(0, MAX_NODES * 2) : [];
  if (safeNodes.length === 0) return null;

  const byId = new Map(safeNodes.map((n) => [n.id, n]));
  const horizontal = direction === "LR";

  // 简单分层布局：按 edges 拓扑排序，失败则保持原顺序
  const ordered = layout(safeNodes, safeEdges);
  const edgeMap = new Map(); // from -> [edge]
  for (const e of safeEdges) {
    if (!edgeMap.has(e.from)) edgeMap.set(e.from, []);
    edgeMap.get(e.from).push(e);
  }

  return (
    <div className={`ui-block ui-flowchart ui-fc-${horizontal ? "lr" : "tb"}`}>
      <div className="ui-fc-track">
        {ordered.map((node, i) => {
          const statusCls = STATUS_CLASS[node.status] || "ui-fc-pending";
          const shape = node.shape || "rect";
          const outEdges = edgeMap.get(node.id) || [];
          return (
            <React.Fragment key={node.id}>
              <div className={`ui-fc-node ui-fc-${shape} ${statusCls}`}>
                <span className="ui-fc-label">{node.label}</span>
              </div>
              {outEdges.map((e, ei) => {
                const to = byId.get(e.to);
                return (
                  <div className="ui-fc-edge" key={`${node.id}->${e.to}-${ei}`}>
                    <span className="ui-fc-arrow" aria-hidden>
                      {horizontal ? "→" : "↓"}
                    </span>
                    {e.label && <span className="ui-fc-edge-label">{e.label}</span>}
                    {!to && <span className="ui-fc-missing">({e.to})</span>}
                  </div>
                );
              })}
              {outEdges.length === 0 && i < ordered.length - 1 && (
                <div className="ui-fc-edge ui-fc-spacer">
                  <span className="ui-fc-arrow" aria-hidden>
                    {horizontal ? "→" : "↓"}
                  </span>
                </div>
              )}
            </React.Fragment>
          );
        })}
      </div>
    </div>
  );
}

// 拓扑排序（Kahn）；环或失败回退到原始顺序
function layout(nodes, edges) {
  try {
    const indeg = new Map(nodes.map((n) => [n.id, 0]));
    const adj = new Map(nodes.map((n) => [n.id, []]));
    for (const e of edges) {
      if (adj.has(e.from) && indeg.has(e.to)) {
        adj.get(e.from).push(e.to);
        indeg.set(e.to, indeg.get(e.to) + 1);
      }
    }
    const queue = nodes.filter((n) => indeg.get(n.id) === 0).map((n) => n.id);
    const out = [];
    const seen = new Set();
    while (queue.length) {
      const id = queue.shift();
      if (seen.has(id)) continue;
      seen.add(id);
      const node = nodes.find((n) => n.id === id);
      if (node) out.push(node);
      for (const nx of adj.get(id) || []) {
        indeg.set(nx, indeg.get(nx) - 1);
        if (indeg.get(nx) === 0) queue.push(nx);
      }
    }
    if (out.length === nodes.length) return out;
  } catch {
    /* fall through */
  }
  return nodes;
}
