import React, { useState, useRef, useEffect } from "react";
import { useContractEvents } from "../hooks/useContractEvents.js";
import ArtifactSlot from "./ArtifactSlot.jsx";

// Generic vertical timeline workbench. Driven by `manifest.graph.steps` (or
// `manifest.graph.nodes`). A lightweight alternative to the node-graph for
// step/phase style workflows. Same contract-event + mock-run behavior as
// BlueprintWorkbench; only the layout differs.

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

export default function TimelineWorkbench({ manifest, session, onExit, onRun, disabled }) {
  const steps0 = (manifest && manifest.graph && (manifest.graph.steps || manifest.graph.nodes)) || [];
  const [steps, setSteps] = useState(() => steps0.map((s) => ({ ...s, status: "pending", artifact: null })));
  const [running, setRunning] = useState(false);
  const cancelledRef = useRef(false);

  const contractEvents = useContractEvents(session?.id);
  useEffect(() => {
    if (!contractEvents || contractEvents.length === 0) return;
    setSteps((prev) => applyContractEvents(prev, contractEvents));
  }, [contractEvents]);

  function stop() {
    cancelledRef.current = true;
    setRunning(false);
  }
  function handleRun() {
    // Reset step states; real progress arrives via workflow.* contract events
    // when onRun (backend) is wired, otherwise fall back to the local mock.
    cancelledRef.current = false;
    setRunning(true);
    setSteps(steps0.map((s) => ({ ...s, status: "pending", artifact: null })));
    if (onRun) onRun();
    else run();
  }
  function run() {
    cancelledRef.current = false;
    setRunning(true);
    setSteps(steps0.map((s) => ({ ...s, status: "pending", artifact: null })));
    let i = 0;
    const tick = () => {
      if (cancelledRef.current) return;
      if (i >= steps0.length) {
        setRunning(false);
        return;
      }
      setSteps((prev) => prev.map((s, idx) => (idx === i ? { ...s, status: "running" } : s)));
      setTimeout(() => {
        if (cancelledRef.current) return;
        const id = steps0[i].id;
        setSteps((prev) =>
          prev.map((s) => (s.id === id ? { ...s, status: "done", artifact: defaultArtifact(s) } : s))
        );
        i += 1;
        setTimeout(tick, 700);
      }, 900);
    };
    tick();
  }

  const doneCount = steps.filter((s) => s.status === "done").length;

  return (
    <div className="workbench timeline">
      <div className="wb-toolbar">
        <div className="wb-toolbar-info">
          <h3 className="wb-title">{manifest?.name || "工作流"}</h3>
          <span className="wb-progress">{doneCount}/{steps.length} 步完成</span>
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

      <div className="wb-timeline">
        {steps.map((s, i) => (
          <div className={`wb-step ${s.status}`} key={s.id}>
            <div className="wb-step-rail">
              <span className={`wb-dot ${s.status}`} />
              {i < steps.length - 1 && <span className="wb-rail-line" />}
            </div>
            <div className="wb-step-body">
              <div className="wb-step-head">
                <span className="wb-node-title">{s.title}</span>
                <span className="wb-node-status">{STATUS_LABEL[s.status]}</span>
              </div>
              <div className="wb-node-desc">{s.desc}</div>
              <div className="wb-slot-wrap">
                <div className="wb-slot-label">{s.artifact?.label || s.artifactLabel || "产物"}</div>
                <ArtifactSlot artifact={s.artifact} />
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="wb-summary">
        <div className="wb-summary-title">终态产物</div>
        <div className="wb-summary-list">
          {steps.filter((s) => s.artifact).length === 0 ? (
            <span className="wb-summary-empty">运行后在此汇总每步产物</span>
          ) : (
            steps
              .filter((s) => s.artifact)
              .map((s) => (
                <div className="wb-summary-item" key={s.id}>
                  <span className="wb-summary-step">{s.title}</span>
                  <ArtifactSlot artifact={s.artifact} />
                </div>
              ))
          )}
        </div>
      </div>
    </div>
  );
}
