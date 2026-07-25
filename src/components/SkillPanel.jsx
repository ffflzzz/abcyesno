import React, { useEffect, useRef } from "react";

// Skill picker. Lists contract workflows (from the manifest registry) and the
// assistant skills. Selecting a contract workflow enters workflow mode in the
// composer - no per-workflow branch, the UI is data-driven by manifests.
export default function SkillPanel({
  skills,
  manifests = [],
  selectedWorkflowId,
  onSelectWorkflow,
  onClose,
}) {
  const panelRef = useRef(null);

  useEffect(() => {
    function onClickOutside(e) {
      if (panelRef.current && !panelRef.current.contains(e.target)) {
        onClose();
      }
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, [onClose]);

  const assistantSkills = (skills || []).filter((s) => s.id && s.id !== "default");
  const workflowList = manifests || [];

  function pickWorkflow(id) {
    if (onSelectWorkflow) onSelectWorkflow(id);
    if (onClose) onClose();
  }

  if (workflowList.length === 0 && assistantSkills.length === 0) {
    return (
      <div className="skill-panel-mask">
        <div className="skill-panel" ref={panelRef}>
          <div className="skill-panel-header">
            <h4>技能 / Skill</h4>
            <button className="skill-panel-close" onClick={onClose}>✕</button>
          </div>
          <div className="skill-panel-body">
            <div className="skill-empty">暂无可用技能</div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="skill-panel-mask">
      <div className="skill-panel" ref={panelRef}>
        <div className="skill-panel-header">
          <h4>技能 / Skill</h4>
          <button className="skill-panel-close" onClick={onClose}>✕</button>
        </div>
        <div className="skill-panel-body">
          {workflowList.length > 0 && (
            <div className="skill-section-title">工作流（契约接入）</div>
          )}
          {workflowList.map((m) => (
            <div
              key={m.id}
              className={`skill-item ${selectedWorkflowId === m.id ? "active" : ""}`}
              onClick={() => pickWorkflow(m.id)}
            >
              <div className="skill-item-name">{m.name}</div>
              <div className="skill-item-meta">
                {m.category ? `${m.category} · ` : ""}
                {m.id}
              </div>
              {m.description && <div className="skill-item-hint">{m.description}</div>}
            </div>
          ))}
          {assistantSkills.length > 0 && (
            <div className="skill-section-title">助手技能</div>
          )}
          {assistantSkills.map((s) => {
            const m = workflowList.find((x) => x.id === s.id.replace(/-/g, "_"));
            return (
              <div
                key={s.id}
                className="skill-item"
                onClick={() => pickWorkflow(m ? m.id : s.id)}
              >
                <div className="skill-item-name">{s.name}</div>
                <div className="skill-item-meta">
                  {s.category ? `${s.category} · ` : ""}
                  {s.id}
                </div>
                {m?.description && <div className="skill-item-hint">{m.description}</div>}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
