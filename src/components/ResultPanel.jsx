import React, { useState, useEffect, useMemo, useRef, useCallback } from "react";
import { useContractEvents } from "../hooks/useContractEvents.js";
import { getManifest } from "../contract/registry.js";
import { getWorkbench } from "../workbenches/registry.js";
import ContractForm from "./ContractForm.jsx";
import WorkflowTimeline from "./WorkflowTimeline.jsx";
import ArtifactViewer from "./ArtifactViewer.jsx";
import WorkspaceTree from "./WorkspaceTree.jsx";
import ChangeDiff from "./ChangeDiff.jsx";

// ResultPanel — right-side dock (spec RESULT_PANEL_SPEC.md).
// Mode 1 (default): Tabs 概览 / 产物 / 文件 / 变更.
// Mode 2 (workflow): When selectedWorkflowId is set, the entire panel shows
//   the workflow UI (ContractForm + Workbench + Timeline) — keeping the main
//   chat area clean.

const TABS = [
  { id: "overview", label: "概览" },
  { id: "artifacts", label: "产物" },
  { id: "files", label: "文件" },
  { id: "changes", label: "变更" },
];

const TEXT_EXTS = new Set([
  ".txt", ".md", ".markdown", ".json", ".js", ".jsx", ".ts", ".tsx", ".mjs",
  ".py", ".css", ".scss", ".less", ".html", ".htm", ".csv", ".yml", ".yaml",
  ".xml", ".log", ".env", ".toml", ".ini", ".cfg", ".sh", ".bat", ".ps1",
]);

function artifactSrc(a) {
  if (!a) return null;
  if (a.source === "url") return a.url;
  if (a.path) return "file://" + a.path.replace(/\\/g, "/");
  return a.inline || null;
}

// Collect unique artifacts from contract events (workflow.artifact).
function collectArtifacts(events) {
  const map = new Map();
  for (const ev of events || []) {
    if (ev && ev.type === "workflow.artifact" && ev.payload) {
      const p = ev.payload;
      if (p.id) map.set(p.id, p);
      else map.set(JSON.stringify(p), p);
    }
  }
  return Array.from(map.values()).reverse();
}

export default function ResultPanel({
  sessionId, aguiPort,
  // ── Workflow mode props (when set, panel shows workflow UI instead of tabs) ──
  selectedWorkflowId, manifests, session,
  onSend, onStop, model, backendStatus, onSelectWorkflow,
}) {
  const [activeTab, setActiveTab] = useState("overview");
  const [collapsed, setCollapsed] = useState(false);
  const [maximized, setMaximized] = useState(false);
  const [root, setRoot] = useState("home"); // home | project

  const contractEvents = useContractEvents(sessionId);
  const artifacts = useMemo(() => collectArtifacts(contractEvents), [contractEvents]);

  // ── Workflow mode ──
  const activeManifest = selectedWorkflowId ? getManifest(selectedWorkflowId) : null;
  const uiType = activeManifest?.ui?.type;
  const Workbench = activeManifest && uiType && uiType !== "form" && uiType !== "chat"
    ? getWorkbench(activeManifest.ui.component)
    : null;
  const isWorkflowMode = !!selectedWorkflowId;

  function handleContractRun(manifest, inputObj) {
    if (!manifest || !onSend) return;
    const envelope = {
      agent_name: manifest.id,
      input: inputObj,
      thread_id: session?.id || sessionId || undefined,
    };
    const text = `请调用 langgraph_agent 工具完成任务：\n${JSON.stringify(envelope)}`;
    onSend(text);
  }

  // Auto-switch to artifacts tab when workflow produces results (stay in workflow mode though).
  useEffect(() => {
    if (!isWorkflowMode && artifacts.length > 0 && activeTab === "overview") {
      setActiveTab("artifacts");
    }
  }, [artifacts.length, isWorkflowMode, activeTab]);

  // Overview webview URL (read-only preview of local dev server).
  const previewUrl = aguiPort ? `http://127.0.0.1:${aguiPort}` : "";

  // ── Files tab state ──
  const [tree, setTree] = useState(null);
  const [treeFilter, setTreeFilter] = useState("");
  const [openFiles, setOpenFiles] = useState([]); // [{path, ext, content, loading, error, tooLarge, binary}]
  const [selectedFile, setSelectedFile] = useState(null);

  // ── Changes tab state ──
  const [baseline, setBaseline] = useState(null); // {root, files: {rel: {mtime,size,content}}}
  const [changes, setChanges] = useState([]); // [{path, status, oldText, newText}]
  const [selectedChange, setSelectedChange] = useState(null);

  // Pop-out target (the URL/file currently previewed).
  const [selectedArtifact, setSelectedArtifact] = useState(null);

  const popOutUrl = useMemo(() => {
    if (activeTab === "overview") return previewUrl;
    if (activeTab === "artifacts" && selectedArtifact) return artifactSrc(selectedArtifact);
    if (activeTab === "files" && selectedFile) {
      const f = openFiles.find((o) => o.path === selectedFile);
      if (f && (f.ext === ".html" || f.ext === ".htm" || f.ext === ".pdf")) {
        return "file://" + (f.absPath || f.path).replace(/\\/g, "/");
      }
    }
    return "";
  }, [activeTab, previewUrl, selectedArtifact, selectedFile, openFiles]);

  // ── Load workspace tree ──
  const loadTree = useCallback(async () => {
    if (!window.hermes || !window.hermes.listWorkspace) return;
    try {
      const t = await window.hermes.listWorkspace({ root });
      setTree(t);
    } catch (err) {
      console.error("list-workspace failed", err);
      setTree({ root, path: "", name: root, type: "dir", children: [], error: String(err) });
    }
  }, [root]);

  useEffect(() => {
    if (activeTab === "files" || activeTab === "overview" || activeTab === "changes") {
      loadTree();
    }
  }, [activeTab, loadTree]);

  // ── Open a file in the files tab ──
  const openFile = useCallback(async (node) => {
    if (!node || node.type !== "file") return;
    const path = node.path;
    setSelectedFile(path);
    if (openFiles.find((o) => o.path === path)) return;
    setOpenFiles((prev) => [...prev, { path, ext: "", content: "", loading: true }]);
    try {
      const res = await window.hermes.readFile({ root, path });
      setOpenFiles((prev) =>
        prev.map((o) =>
          o.path === path
            ? {
                ...o,
                ext: res.ext || "",
                content: res.content || "",
                size: res.size,
                tooLarge: !!res.tooLarge,
                binary: !!res.binary,
                error: res.error || null,
                loading: false,
              }
            : o
        )
      );
    } catch (err) {
      setOpenFiles((prev) =>
        prev.map((o) => (o.path === path ? { ...o, loading: false, error: String(err) } : o))
      );
    }
  }, [root, openFiles]);

  const closeFile = useCallback((path) => {
    setOpenFiles((prev) => prev.filter((o) => o.path !== path));
    if (selectedFile === path) setSelectedFile(null);
  }, [selectedFile]);

  // ── Changes: snapshot baseline + compute diff ──
  const captureBaseline = useCallback(async () => {
    // Walk the tree and read small text files to seed the "before" snapshot.
    const snap = {};
    const walk = async (nodes) => {
      for (const n of nodes || []) {
        if (n.type === "dir") {
          await walk(n.children || []);
        } else if (TEXT_EXTS.has((n.name || "").toLowerCase().match(/\.[^.]+$/) ? (n.name.match(/\.[^.]+$/) || [""])[0].toLowerCase() : "")) {
          if ((n.size || 0) <= 256 * 1024) {
            try {
              const res = await window.hermes.readFile({ root, path: n.path });
              if (res && res.content != null) {
                snap[n.path] = { mtime: n.mtime || 0, size: n.size || 0, content: res.content };
              }
            } catch (_) {}
          }
        }
      }
    };
    const t = tree || (await window.hermes.listWorkspace({ root }));
    await walk(t.children || []);
    setBaseline({ root, files: snap, capturedAt: Date.now() });
    return snap;
  }, [root, tree]);

  const computeChanges = useCallback(async () => {
    const base = baseline && baseline.root === root ? baseline.files : await captureBaseline();
    const t = await window.hermes.listWorkspace({ root });
    const current = {};
    const walk = (nodes) => {
      for (const n of nodes || []) {
        if (n.type === "dir") walk(n.children || []);
        else current[n.path] = n;
      }
    };
    walk(t.children || []);

    const result = [];
    const seen = new Set();
    for (const rel of Object.keys(current)) {
      seen.add(rel);
      const node = current[rel];
      const b = base[rel];
      if (!b) {
        result.push({ path: rel, status: "added" });
      } else if (b.mtime !== node.mtime || b.size !== node.size) {
        result.push({ path: rel, status: "modified" });
      }
    }
    for (const rel of Object.keys(base)) {
      if (!seen.has(rel)) result.push({ path: rel, status: "deleted" });
    }
    result.sort((a, b) => a.path.localeCompare(b.path));
    setChanges(result);
  }, [baseline, root, captureBaseline]);

  useEffect(() => {
    if (activeTab === "changes") {
      computeChanges();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeTab]);

  const openChange = useCallback(async (ch) => {
    setSelectedChange(ch.path);
    const base = baseline && baseline.files ? baseline.files[ch.path] : null;
    let oldText = base ? base.content : "";
    let newText = "";
    if (ch.status !== "deleted") {
      try {
        const res = await window.hermes.readFile({ root, path: ch.path });
        newText = res && res.content != null ? res.content : "";
      } catch (_) {}
    }
    // Store the diff text on the change object for the viewer.
    setChanges((prev) => prev.map((c) => (c.path === ch.path ? { ...c, oldText, newText } : c)));
  }, [baseline, root]);

  const refresh = useCallback(() => {
    loadTree();
    if (activeTab === "changes") {
      setBaseline(null);
      computeChanges();
    }
  }, [loadTree, activeTab, computeChanges]);

  const handleOpenExternal = useCallback((url) => {
    if (window.hermes && window.hermes.openExternal && url) {
      window.hermes.openExternal(url);
    }
  }, []);

  // ── Collapsed rail ──
  if (collapsed) {
    return (
      <div className="result-panel collapsed">
        <button className="result-rail-btn" onClick={() => setCollapsed(false)} title="展开结果区">
          ▤
        </button>
        <div className="result-rail-label">结果</div>
      </div>
    );
  }

  const selectedFileObj = openFiles.find((o) => o.path === selectedFile);
  const selectedChangeObj = changes.find((c) => c.path === selectedChange);
  const changeCount = changes.length;

  return (
    <aside className={`result-panel ${maximized ? "maximized" : ""} ${isWorkflowMode ? "workflow-mode" : ""}`}>
      {/* ── Header ── */}
      <div className="result-header">
        {isWorkflowMode ? (
          /* Workflow mode header: name + exit */
          <div className="result-tabs">
            <span className="result-tab active" style={{ cursor: "default", fontWeight: 600 }}>
              {activeManifest?.name || "工作流"}
            </span>
            <button
              className="result-tab"
              onClick={() => onSelectWorkflow && onSelectWorkflow("")}
              title="退出工作流"
              style={{ marginLeft: "auto", color: "var(--muted)" }}
            >
              ✕ 关闭
            </button>
          </div>
        ) : (
          /* Normal tab bar */
          <div className="result-tabs">
            {TABS.map((t) => (
              <button
                key={t.id}
                className={`result-tab ${activeTab === t.id ? "active" : ""}`}
                onClick={() => setActiveTab(t.id)}
              >
                {t.label}
                {t.id === "artifacts" && artifacts.length > 0 && (
                  <span className="result-tab-badge">{artifacts.length}</span>
                )}
                {t.id === "changes" && changeCount > 0 && (
                  <span className="result-tab-badge">{changeCount}</span>
                )}
              </button>
            ))}
          </div>
        )}
        <div className="result-header-actions">
          {!isWorkflowMode && (
            <button className="result-icon-btn" onClick={refresh} title="刷新">↻</button>
          )}
          {!isWorkflowMode && (
            <button
              className="result-icon-btn"
              onClick={() => handleOpenExternal(popOutUrl)}
              disabled={!popOutUrl}
              title="外开新窗"
            >
              ⧉
            </button>
          )}
          <button className="result-icon-btn" onClick={() => setMaximized((m) => !m)} title={maximized ? "还原" : "最大化"}>
            {maximized ? "❐" : "▢"}
          </button>
          <button className="result-icon-btn" onClick={() => setCollapsed(true)} title="折叠">⟨</button>
        </div>
      </div>

      {/* ── Body ── */}
      <div className="result-body">
        {isWorkflowMode ? (
          /* ═══ Workflow mode: full workbench UI ═══ */
          <div className="result-workflow">
            {Workbench ? (
              <Workbench
                manifest={activeManifest}
                session={session}
                onSend={onSend}
                onStop={onStop}
                onRun={() => handleContractRun(activeManifest, {})}
                onExit={() => onSelectWorkflow && onSelectWorkflow("")}
                disabled={!backendStatus?.gatewayConnected}
                model={model}
                backendStatus={backendStatus}
              />
            ) : (
              <>
                <div className="result-workflow-form">
                  <ContractForm
                    manifest={activeManifest}
                    onRun={handleContractRun}
                    onExit={() => onSelectWorkflow && onSelectWorkflow("")}
                    disabled={!backendStatus?.gatewayConnected}
                  />
                </div>
                {(activeManifest || contractEvents.length > 0) && (
                  <div className="result-workflow-timeline">
                    <WorkflowTimeline events={contractEvents} />
                  </div>
                )}
              </>
            )}
          </div>
        ) : (
          <>
          {/* ── T1 概览 ── */}
        {activeTab === "overview" && (
          <div className="result-overview">
            <div className="result-section">
              <div className="result-section-title">网页预览（只读）</div>
              {previewUrl ? (
                <webview
                  className="result-webview"
                  src={previewUrl}
                  partition="isolated-result"
                  webpreferences="contextIsolation=true"
                />
              ) : (
                <div className="result-empty">后端未就绪，暂无预览地址</div>
              )}
            </div>
            <div className="result-section">
              <div className="result-section-title">
                工作空间文件
                <select
                  className="result-root-select"
                  value={root}
                  onChange={(e) => setRoot(e.target.value)}
                >
                  <option value="home">HERMES_HOME</option>
                  <option value="project">项目根</option>
                </select>
              </div>
              <WorkspaceTree tree={tree} onOpenFile={openFile} />
            </div>
            {changeCount > 0 && (
              <div className="result-section">
                <div className="result-section-title">
                  变更摘要（{changeCount} 个文件）
                  <button className="result-link" onClick={() => setActiveTab("changes")}>查看</button>
                </div>
                <div className="result-change-summary">
                  本次任务改动 {changeCount} 个文件，点击查看详情。
                </div>
              </div>
            )}
          </div>
        )}

        {/* ── T2 产物 ── */}
        {activeTab === "artifacts" && (
          <div className="result-artifacts">
            {artifacts.length === 0 ? (
              <div className="result-empty">尚无产物。运行工作流（如文生图、漫剧生成）后，产物会自动出现在这里。</div>
            ) : (
              <div className="result-artifact-layout">
                <div className="result-artifact-list">
                  {artifacts.map((a) => (
                    <div
                      key={a.id || a.label}
                      className={`artifact-card-row ${selectedArtifact === a ? "active" : ""}`}
                      onClick={() => setSelectedArtifact(a)}
                    >
                      <span className={`artifact-row-icon artifact-type-${a.type}`}>
                        {a.type === "image" ? "🖼" : a.type === "video" ? "🎬" : a.type === "audio" ? "🔊" : a.type === "text" ? "📝" : "📄"}
                      </span>
                      <div className="artifact-row-meta">
                        <div className="artifact-row-label">{a.label || a.id}</div>
                        <div className="artifact-row-sub">{a.type}</div>
                      </div>
                    </div>
                  ))}
                </div>
                <div className="result-artifact-view">
                  {selectedArtifact ? (
                    <ArtifactViewer artifact={selectedArtifact} onOpenExternal={handleOpenExternal} />
                  ) : (
                    <div className="result-empty">选择一个产物预览</div>
                  )}
                </div>
              </div>
            )}
          </div>
        )}

        {/* ── T3 文件 ── */}
        {activeTab === "files" && (
          <div className="result-files">
            <div className="result-files-toolbar">
              <select className="result-root-select" value={root} onChange={(e) => setRoot(e.target.value)}>
                <option value="home">HERMES_HOME</option>
                <option value="project">项目根</option>
              </select>
              <input
                className="result-search"
                placeholder="搜索文件名…"
                value={treeFilter}
                onChange={(e) => setTreeFilter(e.target.value)}
              />
            </div>
            <div className="result-files-layout">
              <div className="result-tree-pane">
                <WorkspaceTree tree={tree} onOpenFile={openFile} filter={treeFilter} />
              </div>
              <div className="result-file-pane">
                <div className="result-file-tabs">
                  {openFiles.map((o) => (
                    <div
                      key={o.path}
                      className={`result-file-tab ${selectedFile === o.path ? "active" : ""}`}
                      onClick={() => setSelectedFile(o.path)}
                    >
                      <span className="result-file-tab-name">{o.path.split(/[\\/]/).pop()}</span>
                      <button className="result-file-tab-close" onClick={(e) => { e.stopPropagation(); closeFile(o.path); }}>✕</button>
                    </div>
                  ))}
                </div>
                <div className="result-file-content">
                  {!selectedFileObj ? (
                    <div className="result-empty">点击左侧文件查看内容</div>
                  ) : selectedFileObj.loading ? (
                    <div className="result-empty">读取中…</div>
                  ) : selectedFileObj.error ? (
                    <div className="result-empty">读取失败：{selectedFileObj.error}</div>
                  ) : selectedFileObj.tooLarge ? (
                    <div className="result-empty">文件过大（{(selectedFileObj.size / 1024 / 1024).toFixed(1)}MB），请用系统程序打开</div>
                  ) : selectedFileObj.binary ? (
                    <div className="result-empty">二进制文件，无法预览</div>
                  ) : (
                    <pre className="result-file-text">{selectedFileObj.content}</pre>
                  )}
                </div>
              </div>
            </div>
          </div>
        )}

        {/* ── T4 变更 ── */}
        {activeTab === "changes" && (
          <div className="result-changes">
            {changes.length === 0 ? (
              <div className="result-empty">暂无变更。切换会话或在任务运行后刷新。</div>
            ) : (
              <div className="result-changes-layout">
                <div className="result-changes-list">
                  {changes.map((c) => (
                    <div
                      key={c.path}
                      className={`result-change-row status-${c.status} ${selectedChange === c.path ? "active" : ""}`}
                      onClick={() => openChange(c)}
                    >
                      <span className="result-change-status">
                        {c.status === "added" ? "A" : c.status === "modified" ? "M" : "D"}
                      </span>
                      <span className="result-change-path">{c.path}</span>
                    </div>
                  ))}
                </div>
                <div className="result-change-pane">
                  {!selectedChangeObj ? (
                    <div className="result-empty">选择一个文件查看差异</div>
                  ) : (
                    <ChangeDiff oldText={selectedChangeObj.oldText || ""} newText={selectedChangeObj.newText || ""} />
                  )}
                </div>
              </div>
            )}
          </div>
        )}
          </>
        )}
      </div>
    </aside>
  );
}
