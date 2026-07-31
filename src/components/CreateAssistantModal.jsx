import React, { useState, useEffect } from "react";
import Icon from "./Icon.jsx";

const AVATAR_PRESETS = ["bot", "palette", "tools", "search", "brain", "film", "folder", "pen"];

export default function CreateAssistantModal({ skills, onCreate, onClose }) {
  const [name, setName] = useState("");
  const [skillId, setSkillId] = useState("default");
  const [avatar, setAvatar] = useState(AVATAR_PRESETS[0]);

  useEffect(() => {
    if (skills.length > 0 && !skills.find((s) => s.id === skillId)) {
      setSkillId(skills[0].id);
    }
  }, [skills, skillId]);

  const save = () => {
    if (!name.trim()) return;
    onCreate({ name: name.trim(), skillId, avatar });
  };

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3>+ 添加助手</h3>
        <p className="modal-desc">选择助手的底层 skill 并给它起个名字。</p>
        <input
          type="text"
          autoFocus
          placeholder="助手名称"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && save()}
        />
        <select
          className="modal-select"
          value={skillId}
          onChange={(e) => setSkillId(e.target.value)}
        >
          {skills.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name} {s.category ? `（${s.category}）` : ""}
            </option>
          ))}
        </select>
        <div className="modal-avatar-picker">
          <div className="modal-avatar-label">选择头像</div>
          <div className="modal-avatar-options">
            {AVATAR_PRESETS.map((emoji) => (
              <button
                key={emoji}
                type="button"
                className={`modal-avatar-option ${avatar === emoji ? "selected" : ""}`}
                onClick={() => setAvatar(emoji)}
                title={emoji}
              >
                <Icon name={emoji} size={20} />
              </button>
            ))}
          </div>
          <div className="modal-avatar-preview">已选：<Icon name={avatar} size={16} /></div>
        </div>
        <div className="modal-actions">
          <button className="ghost" onClick={onClose}>取消</button>
          <button className="primary" onClick={save} disabled={!name.trim()}>
            创建
          </button>
        </div>
      </div>
    </div>
  );
}
