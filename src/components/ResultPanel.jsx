import React, { useState, useEffect, useMemo, useRef, useCallback } from "react";
import Icon from "./Icon.jsx";
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
// Mode 3 (external preview): When externalPreviewUrl is set, shows a webview.

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

// Collect artifacts from tool messages using the same extraction logic as ArtifactPreview.
// This ensures consistency: if the chat chip shows N artifacts, the 产物 tab shows the same.
function collectToolArtifacts(messages) {
  const items = [];
  if (!messages || !Array.isArray(messages)) return items;
  const seen = new Set();

  // ── Same validation helpers as ArtifactPreview.jsx ──
  function looksLikeImageUrl(str) {
    if (!str || typeof str !== "string") return false;
    const t = str.trim();
    if (/^data:image\//i.test(t)) return true;
    if (/^https?:\/\/.+\.(?:png|jpe?g|gif|svg|webp|bmp)/i.test(t)) return true;
    if (/\.(?:png|jpe?g|gif|svg|webp|bmp)(\?.*)?$/i.test(t)) return true;
    return false;
  }
  function normalizeImageUrl(str) {
    if (!str) return null;
    const t = str.trim();
    if (/^data:image\//i.test(t)) return t;
    if (/^https?:\/\//i.test(t)) return t;
    if (/^[A-Za-z]:\\/.test(t) || /^\//.test(t)) return "file://" + t.replace(/\\/g, "/");
    return t;
  }
  function extractImages(value, found) {
    if (typeof value === "string") {
      if (looksLikeImageUrl(value)) {
        const url = normalizeImageUrl(value);
        if (url && !found.has(url)) found.add(url);
      } else {
        const re = /(data:image\/[a-zA-Z0-9+]+;base64,[A-Za-z0-9+/=]+|https?:\/\/[^\s"')]+\.(?:png|jpe?g|gif|svg|webp|bmp)[^\s"')]*|[A-Za-z]:\\[^\s"')]+\.(?:png|jpe?g|gif|svg|webp|bmp))/gi;
        let m;
        while ((m = re.exec(value)) !== null) {
          const url = normalizeImageUrl(m[1]);
          if (url && !found.has(url)) found.add(url);
        }
      }
    } else if (Array.isArray(value)) {
      value.forEach(v => extractImages(v, found));
    } else if (value && typeof value === "object") {
      ["image","image_url","url","path","file","src","preview","thumbnail","frames"].forEach(k => {
        if (value[k] !== undefined) extractImages(value[k], found);
      });
      Object.values(value).forEach(v => extractImages(v, found));
    }
  }

  // ── Main loop ──
  for (const m of messages) {
    if (m.role !== "tool") continue;
    const content = m.result !== undefined ? m.result : m.content;
    if (!content) continue;
    const urls = new Set();
    extractImages(content, urls);
    for (const url of urls) {
      if (!seen.has(url)) {
        seen.add(url);
        items.push({
          id: m.id || `tool-artifact-${items.length}`,
          type: "image",
          source: "url",
          url,
          label: `${m.toolName || "工具调用"}${m.args ? `: ${JSON.stringify(m.args).slice(0, 30)}` : ""}`,
        });
      }
    }
    // Also check chunks array for streaming image data
    if (Array.isArray(m.chunks)) {
      for (const chunk of m.chunks) {
        const chunkUrls = new Set();
        extractImages(chunk, chunkUrls);
        for (const url of chunkUrls) {
          if (!seen.has(url)) {
            seen.add(url);
            items.push({
              id: `${m.id}-chunk-${items.length}`,
              type: "image",
              source: "url",
              url,
              label: `${m.toolName || "工具调用"}(流式)`,
            });
          }
        }
      }
    }
  }
  return items;
}

export default function ResultPanel({
  sessionId, aguiPort,
  // ── Workflow mode props (when set, panel shows workflow UI instead of tabs) ──
  selectedWorkflowId, manifests, session,
  onSend, onStop, onWorkflowRun, model, backendStatus, onSelectWorkflow,
  // ── External URL preview (e.g. abcyesno.cn opened from Bach click) ──
  externalPreviewUrl,
  onClearExternalPreview,
  // ── Collapse control (owned by parent so header button can toggle) ──
  collapsed = false,
  onToggleCollapse,
  // ── Detach control: when true, hides the "detach to new window" button
  //    (the standalone window already shows the panel; offering detach there
  //    would spawn even more windows). ──
  detachHidden = false,
  // ── Layout override: lets the detached window set width:100% etc. ──
  style,
  // ── Detach handler — owned by App so it can clear the in-window state
  //    (selectedWorkflowId, etc.) right after the new window opens. ──
  onDetachResultPanel,
}) {
  const [activeTab, setActiveTab] = useState("overview");
  const [maximized, setMaximized] = useState(false);
  const [root, setRoot] = useState("home"); // home | project

  // Handle tab switch via externalPreviewUrl protocol (e.g. "tab:artifacts" from chat chip click)
  useEffect(() => {
    if (externalPreviewUrl && externalPreviewUrl.startsWith("tab:")) {
      const targetTab = externalPreviewUrl.slice(4); // e.g. "artifacts"
      if (targetTab && targetTab !== activeTab) {
        setActiveTab(targetTab);
      }
      // Clear the protocol URL so it doesn't keep firing
      onClearExternalPreview?.();
    }
  }, [externalPreviewUrl]);

  const contractEvents = useContractEvents(sessionId);
  const artifacts = useMemo(() => {
    // Merge artifacts from both sources: contract events (LangGraph workflows) + tool messages (agent tool calls)
    const sessionMessages = session?.messages;
    if (!Array.isArray(sessionMessages)) return [];
    const fromEvents = collectArtifacts(contractEvents);
    const fromTools = collectToolArtifacts(sessionMessages);
    // Deduplicate by URL
    const seen = new Set();
    const merged = [];
    for (const a of [...fromEvents, ...fromTools]) {
      const key = a.url || a.id || JSON.stringify(a);
      if (!seen.has(key)) { seen.add(key); merged.push(a); }
    }
    return merged;
  }, [contractEvents, session?.messages]);

  // ── Workflow mode ──
  const activeManifest = selectedWorkflowId ? getManifest(selectedWorkflowId) : null;
  const uiType = activeManifest?.ui?.type;
  const Workbench = activeManifest && uiType && uiType !== "form" && uiType !== "chat"
    ? getWorkbench(activeManifest.ui.component)
    : null;
  const isWorkflowMode = !!selectedWorkflowId;

  function handleContractRun(manifest, inputObj) {
    if (!manifest) return;
    // Prefer the independent workflow session path (creates a dedicated
    // session so chat and workflow runs don't abort each other).
    if (onWorkflowRun) {
      onWorkflowRun(manifest, inputObj);
      return;
    }
    // Fallback: send through chat session (old behaviour).
    if (!onSend) return;
    const envelope = {
      agent_name: manifest.id,
      input: inputObj,
      thread_id: session?.id || sessionId || undefined,
    };
    const text = `请调用 langgraph_agent 工具完成任务：\n${JSON.stringify(envelope)}`;
    onSend(text);
  }

  // Auto-switch to artifacts tab when workflow produces results.
  useEffect(() => {
    if (!isWorkflowMode && artifacts.length > 0 && activeTab === "overview") {
      setActiveTab("artifacts");
    }
  }, [artifacts.length, isWorkflowMode, activeTab]);

  // Overview webview URL (read-only preview of local dev server).
  const previewUrl = aguiPort ? `http://127.0.0.1:${aguiPort}` : "";

  // ── Files tab state ──
  const [tree, setTree] = useState(null);
  const [openFiles, setOpenFiles] = useState([]);
  const [selectedFile, setSelectedFile] = useState(null);
  const [treeFilter, setTreeFilter] = useState("");

  // ── Changes tab state ──
  const [baseline, setBaseline] = useState(null);
  const [changes, setChanges] = useState([]);
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
            ? { ...o, ext: res.ext || "", content: res.content || "", size: res.size, tooLarge: !!res.tooLarge, binary: !!res.binary, error: res.error || null, loading: false }
            : o
        )
      );
    } catch (err) {
      setOpenFiles((prev) => prev.map((o) => (o.path === path ? { ...o, loading: false, error: String(err) } : o)));
    }
  }, [root, openFiles]);

  const closeFile = useCallback((path) => {
    setOpenFiles((prev) => prev.filter((o) => o.path !== path));
    if (selectedFile === path) setSelectedFile(null);
  }, [selectedFile]);

  // ── Changes: snapshot baseline + compute diff ──
  const captureBaseline = useCallback(async () => {
    const snap = {};
    const walk = async (nodes) => {
      for (const n of nodes || []) {
        if (n.type === "dir") { await walk(n.children || []); continue; }
        const ext = (n.name.match(/\.[^.]+$/) || [""])[0].toLowerCase();
        if (TEXT_EXTS.has(ext) && (n.size || 0) <= 256 * 1024) {
          try {
            const res = await window.hermes.readFile({ root, path: n.path });
            if (res && res.content != null) snap[n.path] = { mtime: n.mtime || 0, size: n.size || 0, content: res.content };
          } catch (_) {}
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
    const walk = (nodes) => { for (const n of nodes || []) { if (n.type === "dir") walk(n.children || []); else current[n.path] = n; } };
    walk(t.children || []);
    const result = [], seen = new Set();
    for (const rel of Object.keys(current)) {
      seen.add(rel);
      const b = base[rel];
      if (!b) result.push({ path: rel, status: "added" });
      else if (b.mtime !== current[rel].mtime || b.size !== current[rel].size) result.push({ path: rel, status: "modified" });
    }
    for (const rel of Object.keys(base)) { if (!seen.has(rel)) result.push({ path: rel, status: "deleted" }); }
    result.sort((a, b) => a.path.localeCompare(b.path));
    setChanges(result);
  }, [baseline, root, captureBaseline]);

  useEffect(() => { if (activeTab === "changes") computeChanges(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [activeTab]);

  const openChange = useCallback(async (ch) => {
    setSelectedChange(ch.path);
    const base = baseline?.files?.[ch.path];
    let oldText = base?.content || "", newText = "";
    if (ch.status !== "deleted") {
      try { const res = await window.hermes.readFile({ root, path: ch.path }); newText = res?.content ?? ""; } catch (_) {}
    }
    setChanges((prev) => prev.map((c) => c.path === ch.path ? { ...c, oldText, newText } : c));
  }, [baseline, root]);

  const refresh = useCallback(() => { loadTree(); if (activeTab === "changes") { setBaseline(null); computeChanges(); } }, [loadTree, activeTab, computeChanges]);

  const handleOpenExternal = useCallback((url) => {
    if (!url || !window.hermes?.openExternal) return;
    // Only allow safe protocols — block internal protocols like artifact://, tab://, etc.
    const SAFE = /^(https?:|file:|data:image\/|mailto:|tel:)/i;
    if (!SAFE.test(url)) return;
    window.hermes.openExternal(url);
  }, []);

  // ── Detach: pop the entire result panel into its own Electron window ──
  // Mirrors Chrome's "move tab to a new window". The new window keeps the
  // same workflow context (sessionId / workflowId / active tab / collapsed
  // state), and they share the same backend (AG-UI / Hermes) over IPC/HTTP.
  //
  // The actual move-to-new-window orchestration (IPC + clearing in-window
  // state) is owned by App.jsx via the `onDetachResultPanel` prop. We just
  // forward — keeping ResultPanel pure of layout-level decisions.
  const handleDetach = useCallback(() => {
    if (typeof onDetachResultPanel === 'function') {
      onDetachResultPanel();
      return;
    }
    // Fallback when used standalone (e.g. inside the detached window
    // itself, where this prop is intentionally absent): just hit the IPC
    // directly. The standalone window has nothing to close — it's already
    // the only copy.
    if (!window.hermes?.detachResultPanel) {
      console.warn('[ResultPanel] detachResultPanel IPC not available');
      return;
    }
    window.hermes
      .detachResultPanel({
        workflowId: selectedWorkflowId || '',
        sessionId: sessionId || '',
        tab: activeTab || 'overview',
        collapsed: collapsed ? 'true' : 'false',
      })
      .catch((err) => console.error('[ResultPanel] detach failed', err));
  }, [onDetachResultPanel, selectedWorkflowId, sessionId, activeTab, collapsed]);

  // ── Render helpers ──
  const selectedFileObj = openFiles.find((o) => o.path === selectedFile);
  const selectedChangeObj = changes.find((c) => c.path === selectedChange);
  const changeCount = changes.length;

  // Body content renderer: external URL → workflow → tabs
  const renderBody = () => {
    // Priority 1: external URL preview (e.g. abcyesno.cn from Bach click).
    // NOTE: "tab:" protocol URLs are handled by the useEffect above — they must NOT
    // be treated as external previews here, otherwise they block all tab content.
    if (externalPreviewUrl && !externalPreviewUrl.startsWith("tab:")) {
      return (
        <div className="result-external-preview">
          <div className="result-external-header">
            <span className="result-external-url" title={externalPreviewUrl}>{externalPreviewUrl}</span>
            <button className="result-icon-btn" onClick={onClearExternalPreview} title="关闭预览"><Icon name="close" size={14} /></button>
          </div>
          <webview className="result-webview result-external-webview" src={externalPreviewUrl} partition="isolated-external" webpreferences="contextIsolation=true" />
        </div>
      );
    }

    // Priority 2: workflow mode
    if (isWorkflowMode) {
      // Guard: if manifest not found, show helpful fallback instead of crashing
      if (!activeManifest) {
        return (
          <div className="result-workflow">
            <div className="result-empty" style={{ padding: 32 }}>
              <div style={{ fontSize: 13, color: 'var(--muted)', marginBottom: 8 }}>
                工作流 <strong>{selectedWorkflowId}</strong> 未找到对应的 manifest。
              </div>
              <div style={{ fontSize: 12, color: 'var(--muted)' }}>
                可能原因：工作流未注册 / manifest 加载失败 / ID 不匹配
              </div>
              <button className="result-icon-btn" style={{ marginTop: 12 }} onClick={() => onSelectWorkflow?.("")}>
                <Icon name="close" size={14} /> 关闭
              </button>
            </div>
          </div>
        );
      }
      return (
        <div className="result-workflow">
          {Workbench ? (
            <Workbench manifest={activeManifest} session={session} onSend={onSend} onStop={onStop}
              onRun={(inputObj) => handleContractRun(activeManifest, inputObj || {})} onExit={() => onSelectWorkflow?.("")}
              disabled={!backendStatus?.gatewayConnected} model={model} backendStatus={backendStatus} />
          ) : (
            <>
              <div className="result-workflow-form">
                <ContractForm manifest={activeManifest} onRun={handleContractRun} onExit={() => onSelectWorkflow?.("")} disabled={!backendStatus?.gatewayConnected} />
              </div>
              {(activeManifest || contractEvents.length > 0) && (
                <div className="result-workflow-timeline"><WorkflowTimeline events={contractEvents} /></div>
              )}
            </>
          )}
        </div>
      );
    }

    // Default: tab content
    return (
      <>
        {/* T1 Overview */}
        {activeTab === "overview" && (
          <div className="result-overview">
            <div className="result-section">
              <div className="result-section-title">网页预览（只读）</div>
              {previewUrl ? (
                <webview className="result-webview" src={previewUrl} partition="isolated-result" webpreferences="contextIsolation=true" />
              ) : (
                <div className="result-empty">后端未就绪，暂无预览地址</div>
              )}
            </div>
            <div className="result-section">
              <div className="result-section-title">
                工作空间文件
                <select className="result-root-select" value={root} onChange={(e) => setRoot(e.target.value)}>
                  <option value="home">HERMES_HOME</option>
                  <option value="project">项目根</option>
                </select>
              </div>
              <WorkspaceTree tree={tree} onOpenFile={openFile} />
            </div>
            {changeCount > 0 && (
              <div className="result-section">
                <div className="result-section-title">变更摘要（{changeCount} 个文件）<button className="result-link" onClick={() => setActiveTab("changes")}>查看</button></div>
                <div className="result-change-summary">本次任务改动 {changeCount} 个文件，点击查看详情。</div>
              </div>
            )}
          </div>
        )}

        {/* T2 Artifacts */}
        {activeTab === "artifacts" && (
          <div className="result-artifacts">
            {artifacts.length === 0 ? (
              <div className="result-empty">尚无产物。运行工作流（如文生图、漫剧生成）后，产物会自动出现在这里。</div>
            ) : (
              <div className="result-artifact-layout">
                <div className="result-artifact-list">
                  {artifacts.map((a) => (
                    <div key={a.id || a.label} className={`artifact-card-row ${selectedArtifact === a ? "active" : ""}`} onClick={() => setSelectedArtifact(a)}>
                      <span className={`artifact-row-icon artifact-type-${a.type}`}><Icon name={a.type === "image" ? "image" : a.type === "video" ? "film" : a.type === "audio" ? "audio" : a.type === "text" ? "note" : "file"} size={14} /></span>
                      <div className="artifact-row-meta"><div className="artifact-row-label">{a.label || a.id}</div><div className="artifact-row-sub">{a.type}</div></div>
                    </div>
                  ))}
                </div>
                <div className="result-artifact-view">
                  {selectedArtifact ? <ArtifactViewer artifact={selectedArtifact} onOpenExternal={handleOpenExternal} /> : <div className="result-empty">选择一个产物查看</div>}
                </div>
              </div>
            )}
          </div>
        )}

        {/* T3 Files */}
        {activeTab === "files" && (
          <div className="result-files">
            <div className="result-files-toolbar">
              <input className="result-files-filter" placeholder="过滤文件..." value={treeFilter} onChange={(e) => setTreeFilter(e.target.value)} />
            </div>
            <div className="result-files-layout">
              <div className="result-files-tree"><WorkspaceTree tree={tree} onOpenFile={openFile} filter={treeFilter} /></div>
              <div className="result-files-editor">
                {selectedFileObj ? (
                  selectedFileObj.loading ? <div className="result-empty">加载中…</div> :
                  selectedFileObj.error ? <div className="result-empty">读取失败: {selectedFileObj.error}</div> :
                  selectedFileObj.tooLarge ? <div className="result-empty">文件过大（{(selectedFileObj.size / 1024 / 1024).toFixed(1)}MB），请用系统程序打开</div> :
                  selectedFileObj.binary ? <div className="result-empty">二进制文件，无法预览</div> :
                  selectedFileObj.ext === ".pdf" || selectedFileObj.ext === ".html" || selectedFileObj.ext === ".htm" ? (
                    <webview className="result-webview" src={"file://" + (selectedFileObj.absPath || selectedFileObj.path).replace(/\\/g, "/")} partition="isolated-file" webpreferences="contextIsolation=true" />
                  ) : (
                    <pre className="result-file-content">{selectedFileObj.content}</pre>
                  )
                ) : (
                  <div className="result-empty">选择一个文件查看</div>
                )}
              </div>
            </div>
            {openFiles.length > 1 && (
              <div className="result-files-tabs">
                {openFiles.map((f) => (
                  <button key={f.path} className={`result-file-tab ${selectedFile === f.path ? "active" : ""}`} onClick={() => setSelectedFile(f.path)}>
                    <span className="result-file-tab-name">{f.path.split(/[\\/]/).pop()}</span>
                    <span className="result-file-tab-close" onClick={(e) => { e.stopPropagation(); closeFile(f.path); }}><Icon name="close" size={14} /></span>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {/* T4 Changes */}
        {activeTab === "changes" && (
          <div className="result-changes">
            {changes.length === 0 ? (
              <div className="result-empty">暂无变更。切换会话或在任务运行后刷新。</div>
            ) : (
              <div className="result-changes-layout">
                <div className="result-changes-list">
                  {changes.map((c) => (
                    <div key={c.path} className={`result-change-row status-${c.status} ${selectedChange === c.path ? "active" : ""}`} onClick={() => openChange(c)}>
                      <span className="result-change-status">{c.status === "added" ? "A" : c.status === "modified" ? "M" : "D"}</span>
                      <span className="result-change-path">{c.path}</span>
                    </div>
                  ))}
                </div>
                <div className="result-change-pane">
                  {!selectedChangeObj ? <div className="result-empty">选择一个文件查看差异</div> : <ChangeDiff oldText={selectedChangeObj.oldText || ""} newText={selectedChangeObj.newText || ""} />}
                </div>
              </div>
            )}
          </div>
        )}
      </>
    );
  };

  // ── Collapsed: render nothing (parent header owns the toggle button) ──
  if (collapsed) {
    return null;
  }

  // ── Main render ──
  return (
    <aside className={`result-panel ${maximized ? "maximized" : ""} ${isWorkflowMode ? "workflow-mode" : ""} ${detachHidden ? "no-detach" : ""}`} style={style}>
      {!detachHidden && (
      /* Header — only when running inside the main window.
         Detached windows keep Electron's native chrome (BrowserWindow title bar
         + OS-level close/maximize/minimize) and don't need a duplicate header
         inside the panel. */
      <div className="result-header">
        {isWorkflowMode ? (
          <div className="result-tabs">
            <span className="result-tab active" style={{ cursor: "default", fontWeight: 600 }}>{activeManifest?.name || "工作流"}</span>
            <button className="result-tab" onClick={() => onSelectWorkflow?.("")} title="退出工作流" style={{ marginLeft: "auto", color: "var(--muted)" }}><Icon name="close" size={14} /> 关闭</button>
          </div>
        ) : (
          <div className="result-tabs">
            {TABS.map((t) => (
              <button key={t.id} className={`result-tab ${activeTab === t.id ? "active" : ""}`} onClick={() => { setActiveTab(t.id); if (externalPreviewUrl) onClearExternalPreview?.(); }}>
                {t.label}
                {t.id === "artifacts" && artifacts.length > 0 && <span className="result-tab-badge">{artifacts.length}</span>}
                {t.id === "changes" && changeCount > 0 && <span className="result-tab-badge">{changeCount}</span>}
              </button>
            ))}
          </div>
        )}
        <div className="result-header-actions">
          {!isWorkflowMode && <button className="result-icon-btn" onClick={refresh} title="刷新"><Icon name="refresh" size={14} /></button>}
          {!isWorkflowMode && <button className="result-icon-btn" onClick={() => handleOpenExternal(popOutUrl)} disabled={!popOutUrl} title="外开新窗"><Icon name="external" size={14} /></button>}
          <button className="result-icon-btn" onClick={() => setMaximized((m) => !m)} title={maximized ? "还原" : "最大化"}><Icon name="square" size={14} /></button>
          <button className="result-icon-btn" onClick={handleDetach} title="脱离为独立窗口"><Icon name="detach" size={14} /></button>
          <button className="result-icon-btn" onClick={() => onToggleCollapse?.()} title="折叠"><Icon name="chevron" size={14} style={{ transform: "rotate(180deg)" }} /></button>
        </div>
      </div>
      )}

      {/* Body */}
      <div className="result-body">
        {renderBody()}
      </div>
    </aside>
  );
}
