import React, { useState, useRef, useEffect } from "react";
import { useContractEvents } from "../hooks/useContractEvents.js";
import ArtifactSlot from "./ArtifactSlot.jsx";

// Generic node-graph workbench. Driven entirely by `manifest.graph.nodes` - no
// per-workflow code. Adding a new workflow of type "blueprint" = write a
// manifest with a graph; this component renders it. Per spec §3.3 it also
// consumes the existing workflow.* CUSTOM events (mapped by step_id) so real
// backend progress/artifacts fill the nodes when streams are available.

const STATUS_LABEL = { pending: "待运行", running: "进行中", done: "已完成" };

function defaultArtifact(node) {
  const t = node.artifactType || "text";
  return {
    type: t,
    label: node.artifactLabel || node.title,
    text: t === "text" ? `步骤「${node.title}」的输出` : null,
    src: null,
  };
}

function NodeCard({ node }) {
  return (
    <div className={`wb-node ${node.status}`}>
      <div className="wb-node-head">
        <span className={`wb-dot ${node.status}`} />
        <span className="wb-node-title">{node.title}</span>
        <span className="wb-node-status">{STATUS_LABEL[node.status]}</span>
      </div>
      <div className="wb-node-desc">{node.desc}</div>
      <div className="wb-slot-wrap">
        <div className="wb-slot-label">{node.artifact?.label || node.artifactLabel || "产物"}</div>
        <ArtifactSlot artifact={node.artifact} />
      </div>
    </div>
  );
}

function applyContractEvents(prev, events) {
  return prev.map((n) => {
    let next = n;
    for (const ev of events) {
      const p = ev.payload || ev.value || ev;
      const stepId = p.step_id || p.stepId;
      if (!stepId || stepId !== n.id) continue;
      const kind = ev.type || ev.name;
      if (kind === "workflow.progress" && p.status && p.status !== next.status) {
        next = { ...next, status: p.status };
      } else if (kind === "workflow.artifact") {
        next = {
          ...next,
          status: "done",
          artifact: {
            type: p.type || "file",
            label: p.label || next.artifact?.label || "产物",
            src: p.src || p.url || p.path || null,
          },
        };
      }
    }
    return next;
  });
}

export default function BlueprintWorkbench({ manifest, session, onExit, onRun, disabled }) {
  const graph = (manifest && manifest.graph) || { nodes: [] };
  const nodes0 = graph.nodes || [];
  const [nodes, setNodes] = useState(() => nodes0.map((n) => ({ ...n, status: "pending", artifact: null })));
  const [running, setRunning] = useState(false);
  const cancelledRef = useRef(false);

  const contractEvents = useContractEvents(session?.id);
  useEffect(() => {
    if (!contractEvents || contractEvents.length === 0) return;
    setNodes((prev) => applyContractEvents(prev, contractEvents));
  }, [contractEvents]);

  function stop() {
    cancelledRef.current = true;
    setRunning(false);
  }
  function handleRun() {
    // Reset node states; real progress arrives via workflow.* contract events
    // when onRun (backend) is wired, otherwise fall back to the local mock.
    cancelledRef.current = false;
    setRunning(true);
    setNodes(nodes0.map((n) => ({ ...n, status: "pending", artifact: null })));
    if (onRun) onRun();
    else run();
  }
  function run() {
    cancelledRef.current = false;
    setRunning(true);
    setNodes(nodes0.map((n) => ({ ...n, status: "pending", artifact: null })));
    let i = 0;
    const tick = () => {
      if (cancelledRef.current) return;
      if (i >= nodes0.length) {
        setRunning(false);
        return;
      }
      setNodes((prev) => prev.map((n, idx) => (idx === i ? { ...n, status: "running" } : n)));
      setTimeout(() => {
        if (cancelledRef.current) return;
        const id = nodes0[i].id;
        setNodes((prev) =>
          prev.map((n) => (n.id === id ? { ...n, status: "done", artifact: defaultArtifact(n) } : n))
        );
        i += 1;
        setTimeout(tick, 700);
      }, 900);
    };
    tick();
  }

  const doneCount = nodes.filter((n) => n.status === "done").length;

  return (
    <div className="workbench">
      <div className="wb-toolbar">
        <div className="wb-toolbar-info">
          <h3 className="wb-title">{manifest?.name || "工作流"}</h3>
          <span className="wb-progress">{doneCount}/{nodes.length} 步完成</span>
        </div>
        <div className="wb-toolbar-actions">
          {running ? (
            <button className="wb-btn stop" onClick={stop}>停止</button>
          ) : (
            <button className="wb-btn run" onClick={handleRun} disabled={disabled}>运行工作流</button>
          )}
          <button className="wb-btn ghost" onClick={() => onExit && onExit()} disabled={!session}>退出</button>
        </div>
      </div>

      <div className="wb-canvas">
        {nodes.map((node, i) => (
          <React.Fragment key={node.id}>
            <NodeCard node={node} />
            {i < nodes.length - 1 && <div className="wb-connector" aria-hidden="true" />}
          </React.Fragment>
        ))}
      </div>

      <div className="wb-summary">
        <div className="wb-summary-title">终态产物</div>
        <div className="wb-summary-list">
          {nodes.filter((n) => n.artifact).length === 0 ? (
            <span className="wb-summary-empty">运行后在此汇总每步产物</span>
          ) : (
            nodes
              .filter((n) => n.artifact)
              .map((n) => (
                <div className="wb-summary-item" key={n.id}>
                  <span className="wb-summary-step">{n.title}</span>
                  <ArtifactSlot artifact={n.artifact} />
                </div>
              ))
          )}
        </div>
      </div>
    </div>
  );
}
