import React, { useEffect, useRef } from "react";

export default function MarketPanel({ skills, enabledSkills = {}, manifests = [], onToggleSkill, onClose }) {
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

  const list = (skills || []).filter((s) => s.id && s.id !== "default");

  function handleToggle(id) {
    if (onToggleSkill) {
      try {
        onToggleSkill(id);
      } catch (err) {
        console.error("toggle skill failed", err);
      }
    }
  }

  // Description comes from the manifest registry (data), not hardcoded hints.
  function manifestFor(skillId) {
    const snake = skillId.replace(/-/g, "_");
    return (manifests || []).find((m) => m.id === snake);
  }

  return (
    <div className="market-panel-mask">
      <div className="market-panel" ref={panelRef}>
        <div className="market-panel-header">
          <h4>技能市场 / Skill Market</h4>
          <button className="market-panel-close" onClick={onClose}>✕</button>
        </div>
        <div className="market-panel-body">
          {list.length === 0 ? (
            <div className="skill-empty">市场暂无可安装技能</div>
          ) : (
            list.map((s) => {
              const m = manifestFor(s.id);
              const isEnabled = !!enabledSkills[s.id];
              return (
                <div key={s.id} className="market-item">
                  <div className="market-item-info">
                    <div className="market-item-name">{s.name}</div>
                    <div className="market-item-meta">
                      {s.category ? `${s.category} · ` : ""}{s.id}
                    </div>
                    {m?.description && <div className="market-item-hint">{m.description}</div>}
                  </div>
                  <button
                    className={`market-item-toggle ${isEnabled ? "active" : ""}`}
                    onClick={() => handleToggle(s.id)}
                    title={isEnabled ? "已启用" : "未启用"}
                  >
                    {isEnabled ? "已启用" : "启用"}
                  </button>
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
