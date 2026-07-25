import React from "react";
import ArtifactCard from "./ArtifactCard.jsx";

// Generic workflow progress timeline (L5). Consumes workflow.* events and
// renders a vertical timeline. It NEVER inspects a stage name - every label
// comes from the event payload, so adding a workflow with different stages
// requires zero component changes.
function statusLabel(status) {
  if (status === "done") return "完成";
  if (status === "running") return "进行中";
  if (status === "error") return "失败";
  if (status === "pending") return "等待";
  return status || "";
}

export default function WorkflowTimeline({ events = [] }) {
  if (!events || !events.length) return null;
  return (
    <div className="workflow-timeline">
      {events.map((ev, i) => {
        const type = ev.type || ev.name || "";
        if (type.endsWith("progress")) {
          const p = ev.payload || {};
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
        if (type.endsWith("artifact")) {
          const a = ev.payload || {};
          return (
            <div className="tl-node" key={i}>
              <span className="tl-dot artifact" />
              <div className="tl-body">
                <div className="tl-stage">{a.label || "产物"}</div>
                <ArtifactCard artifact={a} />
              </div>
            </div>
          );
        }
        if (type.endsWith("approval")) {
          const a = ev.payload || {};
          return (
            <div className="tl-node" key={i}>
              <span className="tl-dot approval" />
              <div className="tl-body">
                <div className="tl-stage">待确认：{a.label || a.gate_id}</div>
              </div>
            </div>
          );
        }
        if (type.endsWith("error")) {
          const e = ev.payload || {};
          return (
            <div className="tl-node" key={i}>
              <span className="tl-dot error" />
              <div className="tl-body">
                <div className="tl-stage tl-error">{e.message || "出错"}</div>
              </div>
            </div>
          );
        }
        if (type.endsWith("done")) {
          return (
            <div className="tl-node" key={i}>
              <span className="tl-dot done" />
              <div className="tl-body">
                <div className="tl-stage">完成</div>
              </div>
            </div>
          );
        }
        return null;
      })}
    </div>
  );
}
