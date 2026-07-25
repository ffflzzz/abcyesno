import React, { useState, useRef, useEffect } from "react";
import { useContractEvents } from "../hooks/useContractEvents.js";
import ArtifactSlot from "./ArtifactSlot.jsx";

// Mock pipeline skeleton for the manju_craft video workflow. In production the
// nodes + per-step artifacts are driven by workflow.* progress/artifact events
// (L5 of the LangGraph contract). For P3 we render the canvas + per-step
// artifact slots from a static skeleton, with a mock run that advances each
// node and fills its artifact slot, so the "every step's output is observable"
// UX can be verified before the backend streams real events.
const PIPELINE = [
  { id: "parse_script", title: "解析剧本", desc: "拆分分镜与角色", fill: null },
  { id: "generate_characters", title: "生成角色", desc: "设计角色形象", fill: null },
  { id: "gate_first_frame", title: "首帧确认", desc: "人工确认首帧", fill: null },
  { id: "batch_generate_keyframes", title: "生成分镜图", desc: "按分镜生成画面", fill: null },
  { id: "consistency_check", title: "一致性检查", desc: "检查角色一致性", fill: null },
  { id: "gate_each_scene", title: "分镜确认", desc: "人工确认分镜", fill: null },
  { id: "fix_drift", title: "修正漂移", desc: "修正形象漂移", fill: null },
  { id: "batch_generate_video", title: "生成视频", desc: "分镜转视频", fill: null },
  { id: "generate_tts", title: "生成配音", desc: "生成旁白语音", fill: null },
  { id: "merge_and_concat", title: "合成成片", desc: "画面+语音+字幕", fill: null },
  { id: "generate_jianying_draft", title: "导出剪映草稿", desc: "生成剪映草稿", fill: null },
  { id: "gate_end", title: "成片确认", desc: "人工确认成片", fill: null },
  { id: "finalize", title: "完成", desc: "输出最终成片", fill: null },
];

const STATUS_LABEL = { pending: "待运行", running: "进行中", done: "已完成" };

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
        <div className="wb-slot-label">{node.artifact?.label || "产物"}</div>
        <ArtifactSlot artifact={node.artifact} />
      </div>
    </div>
  );
}

export default function ManjuCraftWorkbench({ manifest, session, onSend, onStop, onExit, onRun, disabled, backendStatus }) {
  const [nodes, setNodes] = useState(() => PIPELINE.map((n) => ({ ...n, status: "pending", artifact: null })));
  const [running, setRunning] = useState(false);
  const cancelledRef = useRef(false);

  // Spec §3.3: the workbench reuses the existing workflow.* CUSTOM events - no
  // new backend protocol. When the backend streams real step progress / artifacts
  // (keyed by step_id) we map them onto the matching node, so each step's output
  // is observable live. The mock run() below stays as a no-backend demo fallback.
  const contractEvents = useContractEvents(session?.id);
  useEffect(() => {
    if (!contractEvents || contractEvents.length === 0) return;
    setNodes((prev) =>
      prev.map((n) => {
        let next = n;
        for (const ev of contractEvents) {
          const p = ev.payload || ev.value || ev;
          const stepId = p.step_id || p.stepId;
          if (!stepId || stepId !== n.id) continue;
          if ((ev.type || ev.name) === "workflow.progress") {
            if (p.status && p.status !== next.status) next = { ...next, status: p.status };
          } else if ((ev.type || ev.name) === "workflow.artifact") {
            const artifact = {
              type: p.type || "file",
              label: p.label || next.artifact?.label || "产物",
              src: p.src || p.url || p.path || (p.content ? `data:${p.mime || "application/octet-stream"};base64,${p.content}` : null),
            };
            next = { ...next, artifact, status: "done" };
          }
        }
        return next;
      })
    );
  }, [contractEvents]);

  function stop() {
    cancelledRef.current = true;
    setRunning(false);
  }

  function handleRun() {
    // Reset node states; real progress arrives via workflow.* contract events
    // (keyed by step_id, matching WORKFLOW_STAGES) when onRun (backend) is
    // wired, otherwise fall back to the local mock.
    cancelledRef.current = false;
    setRunning(true);
    setNodes(PIPELINE.map((n) => ({ ...n, status: "pending", artifact: null })));
    if (onRun) onRun();
    else run();
  }

  function run() {
    cancelledRef.current = false;
    setRunning(true);
    setNodes(PIPELINE.map((n) => ({ ...n, status: "pending", artifact: null })));
    let i = 0;
    const tick = () => {
      if (cancelledRef.current) return;
      if (i >= PIPELINE.length) {
        setRunning(false);
        return;
      }
      setNodes((prev) => prev.map((n, idx) => (idx === i ? { ...n, status: "running" } : n)));
      setTimeout(() => {
        if (cancelledRef.current) return;
        const id = PIPELINE[i].id;
        setNodes((prev) =>
          prev.map((n) => (n.id === id ? { ...n, status: "done", artifact: PIPELINE[i].fill } : n))
        );
        i += 1;
        setTimeout(tick, 700);
      }, 900);
    };
    tick();
  }

  const doneCount = nodes.filter((n) => n.status === "done").length;
  const total = nodes.length;

  return (
    <div className="workbench">
      <div className="wb-toolbar">
        <div className="wb-toolbar-info">
          <h3 className="wb-title">{manifest?.name || "漫剧工作台"}</h3>
          <span className="wb-progress">
            {doneCount}/{total} 步完成
          </span>
        </div>
        <div className="wb-toolbar-actions">
          {running ? (
            <button className="wb-btn stop" onClick={stop}>
              停止
            </button>
          ) : (
            <button className="wb-btn run" onClick={handleRun} disabled={disabled}>
              运行工作流
            </button>
          )}
          <button className="wb-btn ghost" onClick={() => onExit && onExit()} disabled={!session}>
            退出
          </button>
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
          {nodes.filter((n) => n.status === "done" && n.artifact).length === 0 ? (
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
