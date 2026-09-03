import React, { useEffect, useMemo, useRef, useState } from "react";
import Icon from "./Icon.jsx";

/**
 * 技能面板（2026-09-03 重写）。
 *
 * 旧版只是一个纯选择器浮层：没搜索、没导入/删除，还用全屏 fixed mask 居中，
 * 跟触发按钮毫无关联（位置奇怪的根源）。现在：
 *  - 锚在输入区上方左下角（.skill-panel-mask 改 absolute + chat-layout relative）；
 *  - 「已安装」tab：本地过滤查找 + 逐项删除（两段确认）；
 *  - 「技能市场」tab：Hermes skills.manage search → 安装（hub 下载）；
 *  - 底部工具条：键入新建（生成 SKILL.md 骨架）/ 导入本地文件夹 / 刷新。
 * 工作流（manifest）区保持原行为：点击进入 workflow 模式。
 * 所有变更完成后统一走 skills.reload → onSkillsChanged 刷新列表。
 */
export default function SkillPanel({
  skills,
  manifests = [],
  selectedWorkflowId,
  onSelectWorkflow,
  onClose,
  onSkillsChanged,
}) {
  const panelRef = useRef(null);
  const [tab, setTab] = useState("installed"); // installed | discover
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState(null); // { kind: 'ok' | 'err', text }
  const [hubResults, setHubResults] = useState(null);
  const [confirmDelete, setConfirmDelete] = useState(null);
  const [showCreate, setShowCreate] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");

  const api = (typeof window !== "undefined" && window.hermes) || {};

  useEffect(() => {
    function onClickOutside(e) {
      if (panelRef.current && !panelRef.current.contains(e.target)) {
        onClose();
      }
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, [onClose]);

  const assistantSkills = useMemo(
    () => (skills || []).filter((s) => s.id && s.id !== "default"),
    [skills]
  );
  const workflowList = useMemo(() => manifests || [], [manifests]);

  const filteredWorkflows = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return workflowList;
    return workflowList.filter((m) =>
      `${m.name || ""} ${m.id || ""} ${m.category || ""} ${m.description || ""}`
        .toLowerCase()
        .includes(q)
    );
  }, [workflowList, query]);

  const filteredSkills = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return assistantSkills;
    return assistantSkills.filter((s) =>
      `${s.name || ""} ${s.id || ""} ${s.category || ""}`.toLowerCase().includes(q)
    );
  }, [assistantSkills, query]);

  async function run(busyLabel, fn) {
    setBusy(busyLabel);
    setNotice(null);
    try {
      await fn();
    } catch (err) {
      setNotice({ kind: "err", text: err && err.message ? err.message : String(err) });
    } finally {
      setBusy("");
    }
  }

  async function reloadBackendSkills() {
    const rl = await api.skillsReload?.();
    if (rl && rl.ok === false) throw new Error(rl.error || "技能重载失败");
    if (onSkillsChanged) await onSkillsChanged();
  }

  function pickWorkflow(id) {
    if (onSelectWorkflow) onSelectWorkflow(id);
    if (onClose) onClose();
  }

  function handleUninstall(id) {
    if (confirmDelete !== id) {
      setConfirmDelete(id);
      setTimeout(() => setConfirmDelete((cur) => (cur === id ? null : cur)), 4000);
      return;
    }
    setConfirmDelete(null);
    run("删除中", async () => {
      const res = await api.skillsManage?.({ action: "uninstall", query: id });
      if (!res || res.ok === false) throw new Error((res && res.error) || "删除失败");
      const d = res.data || {};
      if (d.success === false) throw new Error(d.message || "删除失败");
      await reloadBackendSkills();
      setNotice({ kind: "ok", text: `已删除 ${id}` });
    });
  }

  function handleHubSearch() {
    const q = query.trim();
    if (!q) return;
    run("搜索中", async () => {
      const res = await api.skillsManage?.({ action: "search", query: q, timeoutMs: 60000 });
      if (!res || res.ok === false) throw new Error((res && res.error) || "搜索失败");
      setHubResults((res.data && res.data.results) || []);
    });
  }

  function handleInstall(name) {
    run("安装中", async () => {
      const res = await api.skillsManage?.({ action: "install", query: name, timeoutMs: 300000 });
      if (!res || res.ok === false) throw new Error((res && res.error) || "安装失败");
      await reloadBackendSkills();
      setHubResults((prev) => (prev || []).filter((r) => r.name !== name));
      setNotice({ kind: "ok", text: `已安装 ${name}` });
    });
  }

  function handleCreate() {
    const name = newName.trim();
    if (!name) return;
    run("创建中", async () => {
      const res = await api.skillsCreate?.(name, newDesc);
      if (!res || res.ok === false) throw new Error((res && res.error) || "创建失败");
      await reloadBackendSkills();
      setShowCreate(false);
      setNewName("");
      setNewDesc("");
      setNotice({ kind: "ok", text: `已创建 skills/${res.name}，记得补全 SKILL.md 内容` });
    });
  }

  function handleImport() {
    run("导入中", async () => {
      const res = await api.skillsImportFolder?.();
      if (!res || res.ok === false) {
        if (res && res.canceled) return;
        throw new Error((res && res.error) || "导入失败");
      }
      await reloadBackendSkills();
      setNotice({
        kind: res.hasSkillMd === false ? "err" : "ok",
        text:
          res.hasSkillMd === false
            ? `已复制到 skills/${res.name}，但文件夹里没有 SKILL.md，引擎不会加载它`
            : `已导入 skills/${res.name}`,
      });
    });
  }

  function handleRefresh() {
    run("刷新中", async () => {
      await reloadBackendSkills();
      setNotice({ kind: "ok", text: "已刷新" });
    });
  }

  const hasAnything = filteredWorkflows.length > 0 || filteredSkills.length > 0;

  return (
    <div className="skill-panel-mask">
      <div className="skill-panel" ref={panelRef}>
        <div className="skill-panel-header">
          <div className="skill-tabs">
            <button
              className={`skill-tab ${tab === "installed" ? "active" : ""}`}
              onClick={() => { setTab("installed"); setHubResults(null); }}
            >
              已安装
            </button>
            <button
              className={`skill-tab ${tab === "discover" ? "active" : ""}`}
              onClick={() => { setTab("discover"); setHubResults(null); setQuery(""); }}
            >
              技能市场
            </button>
          </div>
          <button className="skill-panel-close" onClick={onClose}><Icon name="close" size={14} /></button>
        </div>

        {tab === "installed" ? (
          <>
            <div className="skill-search-row">
              <Icon name="search" size={14} />
              <input
                className="skill-search"
                placeholder="查找技能 / 工作流…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                autoFocus
              />
            </div>
            <div className="skill-panel-body">
              {busy && <div className="skill-busy">{busy}…</div>}
              {filteredWorkflows.length > 0 && <div className="skill-section-title">工作流（契约接入）</div>}
              {filteredWorkflows.map((m) => (
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
              {filteredSkills.length > 0 && <div className="skill-section-title">技能</div>}
              {filteredSkills.map((s) => {
                const m = workflowList.find((x) => x.id === s.id.replace(/-/g, "_"));
                return (
                  <div key={s.id} className="skill-item" onClick={() => pickWorkflow(m ? m.id : s.id)}>
                    <div className="skill-item-row">
                      <div className="skill-item-name">{s.name}</div>
                      <div className="skill-item-actions">
                        <button
                          className={`skill-item-btn ${confirmDelete === s.id ? "danger-confirm" : "danger"}`}
                          title={confirmDelete === s.id ? "再点一次确认删除" : "删除此技能"}
                          onClick={(e) => { e.stopPropagation(); handleUninstall(s.id); }}
                        >
                          {confirmDelete === s.id ? "确认删除?" : <Icon name="trash" size={13} />}
                        </button>
                      </div>
                    </div>
                    <div className="skill-item-meta">
                      {s.category ? `${s.category} · ` : ""}
                      {s.id}
                    </div>
                    {m?.description && <div className="skill-item-hint">{m.description}</div>}
                  </div>
                );
              })}
              {!hasAnything && (
                <div className="skill-empty">
                  {query ? "没有匹配的技能" : "暂无可用技能——去「技能市场」搜索，或用下方按钮新建/导入"}
                </div>
              )}
            </div>
          </>
        ) : (
          <>
            <div className="skill-search-row">
              <Icon name="search" size={14} />
              <input
                className="skill-search"
                placeholder="输入关键词搜索技能市场，或直接键入技能名安装"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") handleHubSearch(); }}
                autoFocus
              />
              <button className="skill-foot-btn" onClick={handleHubSearch} disabled={!!busy || !query.trim()}>
                搜索
              </button>
            </div>
            <div className="skill-panel-body">
              {busy && <div className="skill-busy">{busy}…</div>}
              {hubResults === null ? (
                <div className="skill-empty">搜索技能市场（GitHub / 社区源），找到后点「安装」</div>
              ) : hubResults.length === 0 ? (
                <div className="skill-empty">
                  没有搜到「{query}」。
                  {query.trim() && (
                    <button className="skill-foot-btn" onClick={() => handleInstall(query.trim())} disabled={!!busy}>
                      直接安装 skills/{query.trim().toLowerCase()}
                    </button>
                  )}
                </div>
              ) : (
                hubResults.map((r) => (
                  <div key={r.name} className="skill-item">
                    <div className="skill-item-row">
                      <div className="skill-item-name">{r.name}</div>
                      <div className="skill-item-actions">
                        <button className="skill-item-btn install" onClick={() => handleInstall(r.name)} disabled={!!busy}>
                          安装
                        </button>
                      </div>
                    </div>
                    {r.description && <div className="skill-item-hint">{r.description}</div>}
                  </div>
                ))
              )}
            </div>
          </>
        )}

        {notice && (
          <div className={`skill-notice ${notice.kind === "err" ? "err" : "ok"}`} onClick={() => setNotice(null)}>
            {notice.kind === "err" ? <Icon name="warning" size={13} /> : <Icon name="check" size={13} />}
            <span>{notice.text}</span>
          </div>
        )}

        <div className="skill-footer">
          <button className="skill-foot-btn" onClick={() => { setShowCreate((v) => !v); setNotice(null); }} disabled={!!busy}>
            <Icon name="plus" size={13} /> 新建
          </button>
          <button className="skill-foot-btn" onClick={handleImport} disabled={!!busy}>
            <Icon name="folder" size={13} /> 导入文件夹
          </button>
          <button className="skill-foot-btn" onClick={handleRefresh} disabled={!!busy}>
            <Icon name="refresh" size={13} /> 刷新
          </button>
        </div>

        {showCreate && (
          <div className="skill-create-form">
            <input
              className="skill-create-input"
              placeholder="技能名（字母/数字/连字符，如 my-skill）"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
            />
            <input
              className="skill-create-input"
              placeholder="一句话描述（写进 SKILL.md 的 description）"
              value={newDesc}
              onChange={(e) => setNewDesc(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") handleCreate(); }}
            />
            <button className="skill-foot-btn primary" onClick={handleCreate} disabled={!!busy || !newName.trim()}>
              创建
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
