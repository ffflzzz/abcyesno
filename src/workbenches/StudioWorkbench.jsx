import React, { useState, useRef, useEffect, useMemo, useCallback } from "react";
import Icon from "../components/Icon.jsx";
import ApprovalBubble from "../components/ApprovalBubble.jsx";
import WorkflowGraphPanel from "../components/WorkflowGraphPanel.jsx";
import { subscribeContractEvents } from "../contract/eventBus.js";
import "./StudioWorkbench.css";

// Short-drama production studio workbench — unified video production front-end.
//
// This workbench is the single UI entry for the manjucraft_agent LangGraph
// pipeline (script → assets → storyboard → export). It drives the backend by
// calling onRun(input), then consumes workflow.* contract events to update the
// 4-phase UI, the asset library, the shot list, and the export timeline.
//
// The legacy manju_craft / manju_studio agents have been removed entirely;
// this workbench is the only video-production entry point.

const PHASES = [
  { id: "script", label: "剧本" },
  { id: "assets", label: "资产" },
  { id: "storyboard", label: "分镜" },
  { id: "export", label: "成片" },
];

const PX_PER_SEC = 48;
const US = 1_000_000; // Jianying timerange unit = microseconds
const shotKey = (s) => `${s.ep}-${s.n}`;

function eventType(ev) {
  return ev?.type || ev?.name || "";
}

function phaseForStep(stepId) {
  if (!stepId) return "script";
  if (["parse_script", "plan_episodes"].includes(stepId)) return "script";
  if (stepId === "generate_characters") return "assets";
  if (["gate_first_frame", "batch_generate_keyframes", "consistency_check", "gate_each_scene", "fix_drift"].includes(stepId)) {
    return "storyboard";
  }
  return "export";
}

function PhaseStepper({ phase, done, onGo }) {
  return (
    <div className="st-stepper">
      {PHASES.map((p, i) => {
        const isDone = done[p.id] && phase !== p.id;
        return (
          <React.Fragment key={p.id}>
            <div
              className={`st-step ${phase === p.id ? "active" : ""} ${isDone ? "done" : ""}`}
              onClick={() => onGo(p.id)}
            >
              <span className="st-dot">{isDone ? "✓" : i + 1}</span>
              {p.label}
            </div>
            {i < PHASES.length - 1 && <span className="st-step-sep">—</span>}
          </React.Fragment>
        );
      })}
    </div>
  );
}

function AssetLibrary({ curTab, setCurTab, assetsReady, assetImgs, onGenOne, onGenAll, disabled }) {
  const hasAny = Object.keys(assetImgs.character || {}).length > 0;
  const list = hasAny ? Object.entries(assetImgs.character || {}).map(([name, url]) => ({ name, tag: "角色", views: ["正面"], url })) : null;
  return (
    <div className="st-col st-left">
      <div className="st-tabs">
        {[
          { k: "character", label: "角色" },
          { k: "scene", label: "场景" },
          { k: "prop", label: "道具" },
        ].map((t) => (
          <div key={t.k} className={`st-tab ${curTab === t.k ? "active" : ""}`} onClick={() => setCurTab(t.k)}>
            {t.label}
          </div>
        ))}
      </div>
      <div className="st-asset-list">
        {curTab !== "character" && (
          <div className="st-empty">{curTab === "scene" ? "场景参考由分镜图直接承载" : "道具参考由分镜图直接承载"}</div>
        )}
        {curTab === "character" && !list && (
          <div className="st-empty">
            尚未生成
            <br />
            运行工作流后会在此显示角色参考图
          </div>
        )}
        {curTab === "character" &&
          list &&
          list.map((a) => (
            <div className="st-asset-card" key={a.name}>
              <div className="st-asset-name">
                {a.name}
                <span className="st-asset-tag">{a.tag}</span>
              </div>
              {a.url ? (
                <div className="st-asset-img">
                  <img src={a.url} alt={a.name} />
                </div>
              ) : (
                <div className="st-views">
                  {a.views.map((v) => (
                    <div className="st-view" key={v}>
                      {v}
                    </div>
                  ))}
                </div>
              )}
              <div className="st-asset-views-label">{a.views.join(" / ")}</div>
            </div>
          ))}
        {curTab === "character" && list && (
          <button className="st-gen-btn" onClick={() => onGenOne(curTab)} disabled={disabled}>
            ↻ 重新生成角色
          </button>
        )}
        <div style={{ marginTop: 14 }}>
          <button className="st-primary st-block" onClick={onGenAll} disabled={disabled}>
            一键生成全部资产 →
          </button>
        </div>
      </div>
    </div>
  );
}

function StoryboardEditor({ shots, shotState, onGenShot, onGenVideo, onScriptChange }) {
  return (
    <div className="st-center-inner">
      <div className="st-sb-head">
        <div>剧本文本（可编辑）</div>
        <div>拍摄法 + 模型参数</div>
        <div>视频预览 + 生成</div>
      </div>
      {shots.map((s) => {
        const k = shotKey(s);
        const st = shotState[k] || { status: "idle" };
        return (
          <div className="st-shot" key={k}>
            <div className="st-shot-col">
              <div className="st-ep-tag">第{s.ep}集 · 镜{s.n}</div>
              <textarea defaultValue={st.script || s.script || ""} onChange={(e) => onScriptChange(k, e.target.value)} />
            </div>
            <div className="st-shot-col">
              <textarea
                defaultValue={st.prompt || s.prompt || ""}
                placeholder="拍摄法 / 提示词"
                onChange={(e) => onScriptChange(k, e.target.value, "prompt")}
              />
              <div className="st-row2">
                <select defaultValue={st.cam || s.cam || "特写"}>
                  <option>特写</option>
                  <option>中景</option>
                  <option>全景</option>
                  <option>俯拍</option>
                </select>
                <select defaultValue={st.model || s.model || "agnes-2.5-flash"}>
                  <option>agnes-2.5-flash</option>
                  <option>agnes-2.5-pro</option>
                </select>
              </div>
            </div>
            <div className="st-shot-col">
              <div className={`st-preview ${st.status === "busy" ? "busy" : ""}`}>
                {st.videoUrl ? (
                  <video src={st.videoUrl} controls muted loop playsInline />
                ) : st.imgUrl ? (
                  <img className="st-media-img" src={st.imgUrl} alt={k} />
                ) : st.status === "done" ? (
                  <div className="st-meta">▶ 00:0{s.n}</div>
                ) : null}
                {!st.videoUrl && !st.imgUrl && <div className="st-play" />}
              </div>
              {st.status === "busy" && (
                <div className="st-mini-prog">
                  <i />
                </div>
              )}
              <div className="st-shot-btns">
                <button className="st-gen-shot" onClick={() => onGenShot(k)} disabled={st.status === "busy"}>
                  {st.imgUrl ? "↻ 重生成图" : "▶ 生成此镜"}
                </button>
                <button className="st-gen-shot st-gen-video" onClick={() => onGenVideo(k)} disabled={st.status === "busy"}>
                  {st.videoUrl ? "↻ 重生成视频" : "🎬 生成视频"}
                </button>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function EditConsole({ timeline, shotCfg, shots, selectedClip, totalDur, onSelect, onReorder, onCfgChange, onDelete }) {
  const trackRef = useRef(null);
  const [dragKey, setDragKey] = useState(null);
  const byKey = useMemo(() => {
    const m = {};
    shots.forEach((s) => (m[shotKey(s)] = s));
    return m;
  }, [shots]);

  const total = Math.max(1, Math.ceil(totalDur));
  const rulerTicks = [];
  for (let s = 0; s <= total; s++) rulerTicks.push(s);

  function handleDrop(e) {
    e.preventDefault();
    const fromKey = dragKey;
    setDragKey(null);
    if (!fromKey) return;
    const track = trackRef.current;
    if (!track) return;
    const rect = track.getBoundingClientRect();
    const x = e.clientX - rect.left + track.scrollLeft - 100;
    let acc = 0;
    let toIdx = timeline.length;
    for (let i = 0; i < timeline.length; i++) {
      const w = Math.max(30, (shotCfg[timeline[i]]?.dur || 4) * PX_PER_SEC);
      if (x < acc + w / 2) {
        toIdx = i;
        break;
      }
      acc += w;
    }
    const fromIdx = timeline.indexOf(fromKey);
    if (fromIdx === -1) return;
    if (fromIdx === toIdx || fromIdx === toIdx - 1) return;
    const next = timeline.slice();
    next.splice(toIdx, 0, next.splice(fromIdx, 1)[0]);
    onReorder(next);
  }

  if (!timeline.length) {
    return (
      <div className="st-tl-wrap">
        <div className="st-empty" style={{ padding: 20 }}>
          先到「分镜」页生成镜头，这里会出现可拖拽编排的时间轴
        </div>
      </div>
    );
  }

  return (
    <div className="st-tl-wrap">
      <div className="st-tl-ruler">
        {rulerTicks.map((s) => (
          <span key={s} className={`st-tick ${s % 5 === 0 ? "maj" : ""}`} style={{ left: 100 + s * PX_PER_SEC }}>
            {s}s
          </span>
        ))}
      </div>
      <div style={{ position: "relative" }}>
        <div className="st-track-labels">视频轨道</div>
        <div className="st-tl-track" ref={trackRef} onDragOver={(e) => e.preventDefault()} onDrop={handleDrop}>
          {timeline.map((k) => {
            const s = byKey[k];
            const c = shotCfg[k] || { dur: 4, trans: "none" };
            const w = Math.max(30, c.dur * PX_PER_SEC);
            return (
              <div
                key={k}
                className={`st-clip ${selectedClip === k ? "sel" : ""} ${dragKey === k ? "dragging" : ""}`}
                style={{ width: w }}
                draggable
                onDragStart={() => setDragKey(k)}
                onDragEnd={() => setDragKey(null)}
                onClick={(e) => {
                  e.stopPropagation();
                  onSelect(k);
                }}
              >
                <span className="st-clabel">{s ? `第${s.ep}集·镜${s.n}` : k}</span>
                <span className="st-cdur">{c.dur}s</span>
              </div>
            );
          })}
        </div>
      </div>

      {selectedClip && byKey[selectedClip] && (
        <div className="st-shot-detail active">
          <h4>
            第{byKey[selectedClip].ep}集 · 镜{byKey[selectedClip].n} —{" "}
            {byKey[selectedClip].script.slice(0, 24)}…
          </h4>
          <div className="st-sd-row">
            <label>
              时长(秒)
              <input
                type="number"
                min="1"
                max="20"
                defaultValue={shotCfg[selectedClip]?.dur || 4}
                onChange={(e) =>
                  onCfgChange(selectedClip, {
                    dur: Math.max(1, parseInt(e.target.value, 10) || 4),
                  })
                }
              />
            </label>
            <label>
              转场
              <select
                defaultValue={shotCfg[selectedClip]?.trans || "none"}
                onChange={(e) => onCfgChange(selectedClip, { trans: e.target.value })}
              >
                <option value="none">无</option>
                <option value="fadein">淡入</option>
                <option value="fadeout">淡出</option>
              </select>
            </label>
            <label className="st-sd-del-wrap">
              <button
                className="st-del"
                onClick={() => {
                  onDelete(selectedClip);
                  onSelect(null);
                }}
              >
                删除此镜
              </button>
            </label>
          </div>
        </div>
      )}
    </div>
  );
}

export default function StudioWorkbench({ manifest, session, onExit, model, backendStatus, onRun }) {
  const [phase, setPhase] = useState("script");
  const [done, setDone] = useState({});
  const [tasks, setTasks] = useState([]);
  const [assetsReady, setAssetsReady] = useState({ character: false, scene: false, prop: false });
  const [assetImgs, setAssetImgs] = useState({ character: {}, scene: {}, prop: {} });
  const [curTab, setCurTab] = useState("character");
  const [shotState, setShotState] = useState({});
  const [shotCfg, setShotCfg] = useState({});
  const [timeline, setTimeline] = useState([]);
  const [selectedClip, setSelectedClip] = useState(null);
  const [exportJson, setExportJson] = useState(null);
  const [exporting, setExporting] = useState(false);
  const [runState, setRunState] = useState("idle"); // idle | running | done | error
  const [runId, setRunId] = useState(null);
  // HITL approval gate (first-frame / each-scene / end) surfaced while running
  // inside the workbench. The chat-shell ApprovalBubble is NOT mounted here, so
  // we render our own overlay and route the decision through the file control
  // channel (window.hermes.sendWorkflowInterrupt).
  const [approval, setApproval] = useState(null);
  // Live LangGraph node-trace (topology + per-node status map).
  const [topology, setTopology] = useState(null);
  const [trace, setTrace] = useState({});
  const [traceEpisode, setTraceEpisode] = useState(0);
  const [traceTotal, setTraceTotal] = useState(1);

  const [project, setProject] = useState({
    name: "",
    script: "",
    seriesScript: "",
    mode: "single",
    style: "二次元",
    eps: 1,
    res: "1080x1920",
    sec: 4,
    fps: 30,
    consistency: "lock_bible",
    fixedChars: "",
  });

  const timersRef = useRef([]);
  useEffect(() => {
    return () => timersRef.current.forEach((t) => clearInterval(t));
  }, []);

  // ── Persistence: save/restore workbench state across page switches ────────
  const storageKey = useMemo(() => {
    const sid = session?.id || "default";
    const mid = manifest?.id || "studio";
    return `abcyesno:studio:${mid}:${sid}`;
  }, [session?.id, manifest?.id]);

  const loadPersistedState = useCallback(() => {
    try {
      const raw = localStorage.getItem(storageKey);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object") return null;
      // Do not restore an active run; it cannot be resumed from the frontend.
      if (parsed.runState === "running") {
        parsed.runState = "idle";
      }
      return parsed;
    } catch {
      return null;
    }
  }, [storageKey]);

  const clearPersistedState = useCallback(() => {
    try {
      localStorage.removeItem(storageKey);
    } catch {}
  }, [storageKey]);

  // Restore once on mount.
  useEffect(() => {
    const saved = loadPersistedState();
    if (!saved) return;
    if (saved.project) setProject(saved.project);
    if (saved.phase) setPhase(saved.phase);
    if (saved.done) setDone(saved.done);
    if (saved.tasks) setTasks(saved.tasks);
    if (saved.assetsReady) setAssetsReady(saved.assetsReady);
    if (saved.assetImgs) setAssetImgs(saved.assetImgs);
    if (saved.curTab) setCurTab(saved.curTab);
    if (saved.shotState) setShotState(saved.shotState);
    if (saved.shotCfg) setShotCfg(saved.shotCfg);
    if (saved.timeline) setTimeline(saved.timeline);
    if (saved.selectedClip !== undefined) setSelectedClip(saved.selectedClip);
    if (saved.exportJson) setExportJson(saved.exportJson);
    if (saved.runState) setRunState(saved.runState);
    if (saved.runId) setRunId(saved.runId);
  }, [loadPersistedState]); // only run when key changes (mount)

  // Debounced save whenever meaningful state changes.
  useEffect(() => {
    const t = setTimeout(() => {
      try {
        const payload = {
          project,
          phase,
          done,
          tasks,
          assetsReady,
          assetImgs,
          curTab,
          shotState,
          shotCfg,
          timeline,
          selectedClip,
          exportJson,
          runState,
          runId,
        };
        localStorage.setItem(storageKey, JSON.stringify(payload));
      } catch (err) {
        console.error("studio persistence save failed", err);
      }
    }, 1000);
    return () => clearTimeout(t);
  }, [
    storageKey,
    project,
    phase,
    done,
    tasks,
    assetsReady,
    assetImgs,
    curTab,
    shotState,
    shotCfg,
    timeline,
    selectedClip,
    exportJson,
    runState,
    runId,
  ]);

  // Parse the fixed-characters textarea into [{name, prompt}].
  // Accepts two line formats: "名称=描述" or "名称：描述". Blank lines ignored.
  function parseFixedCharacters(text) {
    if (!text || typeof text !== "string") return [];
    const out = [];
    for (const raw of text.split(/\n+/)) {
      const line = raw.trim();
      if (!line) continue;
      let name = "", prompt = "";
      const eq = line.indexOf("=");
      const colon = line.search(/[:：]/);
      const sep = eq >= 0 && (colon < 0 || eq < colon) ? eq : colon;
      if (sep >= 0) {
        name = line.slice(0, sep).trim();
        prompt = line.slice(sep + 1).trim();
      } else {
        name = line;
      }
      if (!name) continue;
      out.push({ name, prompt });
    }
    return out;
  }

  const shotStateRef = useRef(shotState);
  useEffect(() => {
    shotStateRef.current = shotState;
  }, [shotState]);

  // Consume workflow.* events globally; the workflow may run in a dedicated
  // session (via TaskPanel), so session?.id alone is not enough.
  useEffect(() => {
    const unsub = subscribeContractEvents((rid, ev) => {
      if (!ev) return;
      const type = eventType(ev);

      if (type === "RUN_STARTED") {
        setRunId(rid);
        setRunState("running");
        setTasks([]);
        setDone({});
        setPhase("script");
        setExportJson(null);
        setTopology(null);
        setTrace({});
        setTraceEpisode(0);
        setTraceTotal(1);
        return;
      }

      // Only accept events belonging to the latest run.
      if (runId && rid !== runId) return;
      if (!runId && type !== "RUN_STARTED") {
        // First non-start event locks the run id.
        setRunId(rid);
      }

      if (type === "RUN_ERROR") {
        setRunState("error");
        setApproval(null);
        // Mark the currently active (running) node as errored in the trace.
        setTrace((prev) => {
          const next = { ...prev };
          for (const k in next) if (next[k] === "running") next[k] = "error";
          return next;
        });
        const msg = ev.payload?.message || ev.message || "运行失败";
        setTasks((prev) =>
          prev.map((t) => (t.status === "run" ? { ...t, status: "err", error: msg, prog: 100 } : t))
        );
        return;
      }

      if (type === "workflow.graph") {
        const p = ev.payload || {};
        setTopology({ nodes: p.nodes || [], edges: p.edges || [] });
        setTraceTotal(p.totalEpisodes || 1);
        setTrace({});
        return;
      }

      if (type === "workflow.trace") {
        const p = ev.payload || {};
        if (p.node) {
          setTrace((prev) => ({ ...prev, [p.node]: p.status }));
          if (typeof p.episode === "number") setTraceEpisode(p.episode);
        }
        return;
      }

      if (type === "workflow.approval") {
        const p = ev.payload || {};
        setApproval({
          id: p.gate_id || "workflow-approval",
          operation: p.gate_id || "workflow-approval",
          source: "workflow",
          runId: p.workflowRunId,
          gateId: p.gate_id,
          label: p.label,
          message: p.message || p.label || "工作流需要确认",
          artifacts: p.artifacts || [],
          allowSteer: !!p.allowSteer,
        });
        return;
      }

      if (type === "workflow.progress") {
        const p = ev.payload || {};
        const step = p.step_id || "step";
        const status = p.status || "running";
        const msg = p.message || step;
        const ph = phaseForStep(step);
        setPhase(ph);
        if (status === "done") {
          setDone((prev) => ({ ...prev, [ph]: true }));
        }
        setTasks((prev) => {
          const existing = prev.find((t) => t.step === step);
          if (!existing) {
            return [
              {
                id: `${rid}-${step}`,
                name: msg,
                step,
                status: status === "done" ? "ok" : "run",
                prog: status === "done" ? 100 : 25,
              },
              ...prev,
            ];
          }
          return prev.map((t) =>
            t.step === step
              ? {
                  ...t,
                  name: msg,
                  status: status === "done" ? "ok" : "run",
                  prog: status === "done" ? 100 : Math.max(t.prog, Math.min(95, t.prog + 10)),
                }
              : t
          );
        });
        return;
      }

      if (type === "workflow.artifact") {
        const a = ev.payload || {};
        ingestArtifact(a);
        return;
      }

      if (type === "workflow.done") {
        setApproval(null);
        setRunState("done");
        setDone({ script: true, assets: true, storyboard: true, export: true });
        setPhase("export");
        // Settle the trace: every known node becomes done (unless already error).
        setTrace((prev) => {
          const next = { ...prev };
          (topology?.nodes || []).forEach((n) => {
            if (next[n.id] !== "error") next[n.id] = "done";
          });
          return next;
        });
        setTasks((prev) => prev.map((t) => (t.status === "run" ? { ...t, status: "ok", prog: 100 } : t)));
      }
    });
    return unsub;
  }, [runId]);

  function ingestArtifact(a) {
    const { id, type, path, label, url, episode } = a;
    const src = url || path;
    if (!src) return;

    // Character reference images from the locked bible.
    if (type === "image" && (label || "").includes("角色")) {
      const name = (label || "").replace(/^角色·/, "").trim() || "角色";
      setAssetImgs((prev) => ({
        ...prev,
        character: { ...(prev.character || {}), [name]: src },
      }));
      setAssetsReady((prev) => ({ ...prev, character: true }));
      return;
    }

    // Per-shot keyframes.
    const kfMatch = id && String(id).match(/^shot_(\d+)_keyframe$/);
    if (kfMatch) {
      const idx = parseInt(kfMatch[1], 10);
      const ep = episode ?? 1;
      const key = `${ep}-${idx + 1}`;
      setShotState((prev) => ({
        ...prev,
        [key]: { ...(prev[key] || {}), status: "img", imgUrl: src, ep, n: idx + 1 },
      }));
      return;
    }

    // Per-shot videos.
    const vidMatch = id && String(id).match(/^shot_(\d+)_video$/);
    if (vidMatch) {
      const idx = parseInt(vidMatch[1], 10);
      const ep = episode ?? 1;
      const key = `${ep}-${idx + 1}`;
      setShotState((prev) => ({
        ...prev,
        [key]: { ...(prev[key] || {}), status: "done", videoUrl: src, ep, n: idx + 1 },
      }));
      return;
    }

    // Per-shot TTS audio.
    const ttsMatch = id && String(id).match(/^shot_(\d+)_tts$/);
    if (ttsMatch) {
      const idx = parseInt(ttsMatch[1], 10);
      const ep = episode ?? 1;
      const key = `${ep}-${idx + 1}`;
      setShotState((prev) => ({
        ...prev,
        [key]: { ...(prev[key] || {}), audioUrl: src, ep, n: idx + 1 },
      }));
      return;
    }

    // Final video.
    if (id === "final_video") {
      setExportJson((prev) => ({ ...(prev || {}), finalVideo: src }));
      return;
    }

    // Jianying draft.
    if (id === "jianying_draft") {
      setExportJson((prev) => ({ ...(prev || {}), draftPath: src }));
    }
  }

  // Resume a paused LangGraph HITL gate from inside the workbench. The chat
  // ApprovalBubble is not mounted here, so we drive the file control channel
  // (window.hermes.sendWorkflowInterrupt) directly and clear our local gate.
  async function handleWorkbenchApprove(choice, remember, steerText) {
    if (!approval) return;
    try {
      const hasSteer = !!(choice && steerText && steerText.trim());
      const api = typeof window !== "undefined" && window.hermes;
      if (api && api.sendWorkflowInterrupt) {
        await api.sendWorkflowInterrupt({
          workflowRunId: approval.runId,
          decision: choice ? (hasSteer ? "steer" : "approve") : "reject",
          steerText: hasSteer ? steerText : "",
        });
      }
    } catch (err) {
      console.error("workbench approval response failed", err);
    }
    setApproval(null);
  }

  // Build shot list and timeline from incoming artifacts.
  const shots = useMemo(() => {
    const list = Object.entries(shotState)
      .filter(([, st]) => st.ep && st.n)
      .map(([key, st]) => ({ key, ep: st.ep, n: st.n, script: st.script || "", prompt: st.prompt || "", cam: "特写", model: "agnes-2.5-flash" }));
    list.sort((a, b) => (a.ep === b.ep ? a.n - b.n : a.ep - b.ep));
    return list;
  }, [shotState]);

  useEffect(() => {
    setTimeline((prev) => {
      const ready = shots.filter((s) => {
        const st = shotState[s.key] || {};
        return st.status === "img" || st.status === "done";
      }).map((s) => s.key);
      const set = new Set(ready);
      const kept = prev.filter((k) => set.has(k));
      const keptSet = new Set(kept);
      const added = ready.filter((k) => !keptSet.has(k));
      return kept.concat(added);
    });
  }, [shotState, shots]);

  useEffect(() => {
    setShotCfg((prev) => {
      const next = { ...prev };
      timeline.forEach((k) => {
        if (!next[k]) next[k] = { dur: project.sec, trans: "none" };
      });
      return next;
    });
  }, [timeline, project.sec]);

  const api = useCallback(
    async (action, params) => {
      try {
        if (typeof window !== "undefined" && window.hermes && window.hermes.studioCall) {
          const j = await window.hermes.studioCall(action, params);
          if (!j.ok) console.error("[Studio API]", action, "→", j.error || j);
          return j;
        }
        return { ok: false, error: "studioCall 不可用" };
      } catch (e) {
        const err = String((e && e.message) || e);
        console.error("[Studio API]", action, "→", err);
        return { ok: false, error: err };
      }
    },
    []
  );

  // ── asset regeneration (reuses the standalone Agnes IPC) ──
  const genOne = useCallback(
    async (t) => {
      if (t !== "character") return;
      const names = Object.keys(assetImgs.character || {});
      for (const name of names) {
        const task = { id: Date.now() + Math.random(), name: `重生成 ${name}`, status: "run", prog: 0 };
        setTasks((prev) => [task, ...prev]);
        const j = await api("generate-image", {
          prompt: `${name}，角色设定图，${project.style}风格，高细节，统一风格`,
          size: "2K",
          ratio: "3:4",
        });
        if (j && j.ok && j.url) {
          setAssetImgs((prev) => ({ ...prev, character: { ...(prev.character || {}), [name]: j.url } }));
        }
        setTasks((prev) => prev.map((x) => (x.id === task.id ? { ...x, status: j?.ok ? "ok" : "err", error: j?.error, prog: 100 } : x)));
      }
    },
    [api, assetImgs.character, project.style]
  );

  const genAll = useCallback(async () => {
    // The real agent will generate characters when the pipeline runs.
    // This button just advances the phase if already generated; otherwise
    // prompts the user to run the pipeline from the script page.
    if (Object.keys(assetImgs.character || {}).length === 0) {
      setPhase("script");
      return;
    }
    setPhase("storyboard");
  }, [assetImgs.character]);

  // ── per-shot regeneration (standalone Agnes IPC, independent of the agent) ──
  const genShot = useCallback(
    async (k) => {
      const s = shots.find((x) => shotKey(x) === k);
      const st = shotState[k] || {};
      setShotState((prev) => ({ ...prev, [k]: { ...st, status: "busy" } }));
      const j = await api("generate-image", {
        prompt: `${st.prompt || s?.prompt || ""}，${project.style}风格，电影级镜头，高细节`,
        size: "2K",
        ratio: "9:16",
      });
      setShotState((prev) => ({
        ...prev,
        [k]: {
          ...prev[k],
          status: j?.ok ? "img" : "error",
          imgUrl: j?.ok ? j.url : prev[k]?.imgUrl,
          error: j?.ok ? undefined : j?.error,
        },
      }));
    },
    [api, shots, shotState, project.style]
  );

  const genVideoShot = useCallback(
    async (k) => {
      const st = shotState[k] || {};
      setShotState((prev) => ({ ...prev, [k]: { ...st, status: "busy" } }));
      const j = await api("generate-video", {
        prompt: `${st.prompt || ""}，${project.style}风格，自然运动，电影级镜头`,
        image: st.imgUrl || undefined,
        width: 1152,
        height: 768,
        num_frames: 81,
        frame_rate: 24,
      });
      setShotState((prev) => ({
        ...prev,
        [k]: {
          ...prev[k],
          status: j?.ok ? "done" : (prev[k]?.imgUrl ? "img" : "error"),
          videoUrl: j?.ok ? j.url : prev[k]?.videoUrl,
          error: j?.ok ? undefined : j?.error,
        },
      }));
    },
    [api, shotState, project.style]
  );

  const onScriptChange = useCallback((k, val, field = "script") => {
    setShotState((prev) => ({ ...prev, [k]: { ...(prev[k] || {}), [field]: val } }));
  }, []);

  const onReorder = useCallback((next) => setTimeline(next), []);
  const onCfgChange = useCallback((k, patch) => {
    setShotCfg((prev) => ({ ...prev, [k]: { ...(prev[k] || { dur: 4, trans: "none" }), ...patch } }));
  }, []);
  const onDelete = useCallback((k) => {
    setShotState((prev) => {
      const n = { ...prev };
      delete n[k];
      return n;
    });
    setTimeline((prev) => prev.filter((x) => x !== k));
  }, []);
  const onSelect = useCallback((k) => setSelectedClip(k), []);

  // ── export (prepare Jianying draft from real generated videos/images) ──
  const totalDur = useMemo(() => timeline.reduce((sum, k) => sum + (shotCfg[k]?.dur || 4), 0), [timeline, shotCfg]);

  const handleExport = useCallback(async () => {
    if (!timeline.length) {
      setExportJson((prev) => ({ ...(prev || {}), error: "没有可导出的镜头，请先到「分镜」页生成" }));
      return;
    }
    const shotsPayload = timeline.map((k) => {
      const s = shots.find((x) => shotKey(x) === k);
      const st = shotState[k] || {};
      return { key: k, ep: s?.ep, n: s?.n, videoUrl: st.videoUrl || null, imgUrl: st.imgUrl || null };
    });
    setExporting(true);
    const t = { id: Date.now() + Math.random(), name: "导出剪映工程（下载素材 + 生成草稿）", status: "run", prog: 0 };
    setTasks((prev) => [t, ...prev]);
    const j = await api("prepare-export", {
      project: { name: project.name || "short_drama", res: project.res, fps: project.fps },
      timeline,
      shotCfg,
      shots: shotsPayload,
    });
    setExporting(false);
    if (j && j.ok) {
      setExportJson((prev) => ({ ...(prev || {}), json: j.json, totalSec: j.totalSec, count: j.count, draftDir: j.draftDir }));
    } else {
      setExportJson((prev) => ({ ...(prev || {}), error: (j && j.error) || "导出失败" }));
    }
    setTasks((prev) => prev.map((x) => (x.id === t.id ? { ...x, status: j?.ok ? "ok" : "err", error: j?.error, prog: 100 } : x)));
  }, [timeline, shotCfg, project, shots, shotState, api]);

  const goPhase = useCallback((id) => {
    setPhase(id);
  }, []);

  function handleStart() {
    if (!onRun) return;
    const script = project.mode === "series" ? project.seriesScript : project.script;
    if (!script || !script.trim()) {
      alert(project.mode === "series" ? "请填写系列脚本" : "请填写剧本");
      return;
    }
    // Reset state for a fresh run.
    setRunState("running");
    setRunId(null);
    setTasks([]);
    setDone({});
    setPhase("script");
    setTopology(null);
    setTrace({});
    setTraceEpisode(0);
    setTraceTotal(1);
    setAssetImgs({ character: {}, scene: {}, prop: {} });
    setAssetsReady({ character: false, scene: false, prop: false });
    setShotState({});
    setTimeline([]);
    setExportJson(null);

    const input = {
      mode: project.mode,
      script: project.mode === "single" ? project.script : undefined,
      series_script: project.mode === "series" ? project.seriesScript : undefined,
      style: project.style,
      project_name: project.name || undefined,
      total_episodes: project.mode === "series" ? Number(project.eps) : undefined,
      consistency_policy: project.mode === "series" ? project.consistency : undefined,
      resolution: project.res,
      sec_per_shot: Number(project.sec),
      characters: parseFixedCharacters(project.fixedChars),
    };
    onRun(input);
  }

  const running = runState === "running";

  return (
    <div className="st-workbench" onClick={() => selectedClip && setSelectedClip(null)}>
      <div className="st-topbar">
        <div className="st-brand">
          {manifest?.name || "短剧制片工作台"}
          <small>前端编排台 · 导出剪映工程</small>
        </div>
        <div className="st-topbar-actions">
          <button
            className="st-icon-btn"
            onClick={() => {
              clearPersistedState();
              setPhase("script");
              setDone({});
              setTasks([]);
              setAssetsReady({ character: false, scene: false, prop: false });
              setAssetImgs({ character: {}, scene: {}, prop: {} });
              setShotState({});
              setShotCfg({});
              setTimeline([]);
              setSelectedClip(null);
              setExportJson(null);
              setRunState("idle");
              setRunId(null);
              setTopology(null);
              setTrace({});
              setTraceEpisode(0);
              setTraceTotal(1);
              setProject({
                name: "",
                script: "",
                seriesScript: "",
                mode: "single",
                style: "二次元",
                eps: 1,
                res: "1080x1920",
                sec: 4,
                fps: 30,
                consistency: "lock_bible",
                fixedChars: "",
              });
            }}
            title="重置工作台"
          >
            <Icon name="refresh" size={14} />
          </button>
          {onExit && (
            <button className="st-icon-btn" onClick={onExit} title="退出工作台">
              <Icon name="close" size={14} />
            </button>
          )}
        </div>
      </div>

      <PhaseStepper phase={phase} done={done} onGo={goPhase} />

      <div className="st-grid" onClick={(e) => e.stopPropagation()}>
        <AssetLibrary
          curTab={curTab}
          setCurTab={setCurTab}
          assetsReady={assetsReady}
          assetImgs={assetImgs}
          onGenOne={genOne}
          onGenAll={genAll}
          disabled={running}
        />

        <div className="st-center">
          {phase === "script" && (
            <div className="st-card st-form-card">
              <div className="st-section">
                <div className="st-section-title">项目信息</div>
                <label className="st-fld">
                  <span>项目名</span>
                  <input value={project.name} onChange={(e) => setProject((p) => ({ ...p, name: e.target.value }))} placeholder="倒数三秒说爱你" />
                </label>

                <label className="st-fld">
                  <span>模式</span>
                  <select value={project.mode} onChange={(e) => setProject((p) => ({ ...p, mode: e.target.value }))}>
                    <option value="single">单条视频</option>
                    <option value="series">多集连载</option>
                  </select>
                </label>
              </div>

              <div className="st-section">
                <div className="st-section-title">内容设定</div>
                <label className="st-fld">
                  <span>{project.mode === "series" ? "系列脚本 / 大纲" : "剧本 / 大纲"}</span>
                  <textarea
                    value={project.mode === "series" ? project.seriesScript : project.script}
                    onChange={(e) =>
                      setProject((p) =>
                        p.mode === "series" ? { ...p, seriesScript: e.target.value } : { ...p, script: e.target.value }
                      )
                    }
                    placeholder={
                      project.mode === "series"
                        ? "整部连载的大纲/剧情，将按集数拆分…"
                        : "描述你要的漫剧情节…"
                    }
                  />
                </label>

                <div className="st-row2">
                  <label className="st-fld">
                    <span>风格</span>
                    <select value={project.style} onChange={(e) => setProject((p) => ({ ...p, style: e.target.value }))}>
                      <option>二次元</option>
                      <option>写实</option>
                      <option>3D</option>
                    </select>
                  </label>
                  <label className="st-fld">
                    <span>集数</span>
                    <input
                      type="number"
                      min="1"
                      max="24"
                      value={project.eps}
                      disabled={project.mode !== "series"}
                      onChange={(e) => setProject((p) => ({ ...p, eps: Math.max(1, parseInt(e.target.value, 10) || 1) }))}
                    />
                  </label>
                </div>

                {project.mode === "series" && (
                  <label className="st-fld">
                    <span>跨集一致性</span>
                    <select
                      value={project.consistency}
                      onChange={(e) => setProject((p) => ({ ...p, consistency: e.target.value }))}
                    >
                      <option value="lock_bible">锁定角色圣经</option>
                      <option value="per_episode">每集独立</option>
                    </select>
                  </label>
                )}
              </div>

              <div className="st-section">
                <div className="st-section-title">生成参数</div>
                <div className="st-row2">
                  <label className="st-fld">
                    <span>分辨率</span>
                    <select value={project.res} onChange={(e) => setProject((p) => ({ ...p, res: e.target.value }))}>
                      <option value="1080x1920">1080×1920（竖屏）</option>
                      <option value="1920x1080">1920×1080（横屏）</option>
                      <option value="2560x1440">2560×1440（2K 横屏）</option>
                      <option value="3840x2160">3840×2160（4K 横屏）</option>
                    </select>
                  </label>
                  <label className="st-fld">
                    <span>每镜秒数</span>
                    <input
                      type="number"
                      min="2"
                      max="12"
                      value={project.sec}
                      onChange={(e) => setProject((p) => ({ ...p, sec: parseInt(e.target.value, 10) || 4 }))}
                    />
                  </label>
                </div>
              </div>

              <div className="st-section">
                <div className="st-section-title">角色设定</div>
                <label className="st-fld st-fld-col">
                  <span>固定角色（可选，用于一致性）</span>
                  <textarea
                    rows={3}
                    value={project.fixedChars}
                    placeholder={"每行一个，如：\n糯糯=白色长毛猫，女，温柔但毒舌\n老周=60岁男人，沉默寡言，左手有旧伤疤"}
                    onChange={(e) => setProject((p) => ({ ...p, fixedChars: e.target.value }))}
                  />
                  <small className="st-sub">格式：名称=描述 或 名称：描述。这些角色不会由 AI 重新生成设定，直接锁定进角色圣经。</small>
                </label>
              </div>

              <div className="st-form-actions">
                <button className="st-primary" onClick={handleStart} disabled={running || !onRun}>
                  {running ? "工作流运行中…" : "生成资产与分镜 →"}
                </button>
                <div className="st-hint">点击后调用 manjucraft_agent：拆分剧本 → 生成角色 → 首帧/分镜审批 → 视频/配音 → 导出剪映草稿。</div>
              </div>
            </div>
          )}

          {phase === "assets" && (
            <div className="st-card st-form-card">
              <div className="st-section">
                <div className="st-section-title">角色资产</div>
                <div className="st-hint">角色参考图由工作流生成并锁定到角色圣经（series 模式下首集批准后即锁定）。确认无误后进入分镜编排。</div>
              </div>
              <div className="st-form-actions">
                <button className="st-primary" onClick={genAll} disabled={running}>
                  下一步：分镜 →
                </button>
              </div>
            </div>
          )}

          {phase === "storyboard" && (
            <StoryboardEditor
              shots={shots}
              shotState={shotState}
              onGenShot={genShot}
              onGenVideo={genVideoShot}
              onScriptChange={onScriptChange}
            />
          )}

          {phase === "export" && (
            <div className="st-export-wrap">
              <div className="st-card">
                <div className="st-section">
                  <div className="st-section-title">导出设置</div>
                  <div className="st-hint">
                    横轨时间轴：shot 按时长占宽度，左右拖拽换顺序。点击片段打开详情编辑时长/转场。编排完点「导出剪映工程」生成 draft JSON。
                  </div>
                  <div className="st-row2">
                    <label className="st-fld">
                      <span>分辨率</span>
                      <select value={project.res} onChange={(e) => setProject((p) => ({ ...p, res: e.target.value }))}>
                        <option value="1080x1920">1080×1920</option>
                        <option value="1920x1080">1920×1080</option>
                        <option value="2560x1440">2560×1440</option>
                        <option value="3840x2160">3840×2160</option>
                      </select>
                    </label>
                    <label className="st-fld">
                      <span>帧率 fps</span>
                      <select value={project.fps} onChange={(e) => setProject((p) => ({ ...p, fps: parseInt(e.target.value, 10) }))}>
                        <option>30</option>
                        <option>25</option>
                        <option>60</option>
                      </select>
                    </label>
                  </div>
                </div>
                <div className="st-form-actions">
                  <button className="st-primary" onClick={handleExport} disabled={exporting}>
                    {exporting ? "导出中…（下载素材 + 生成草稿）" : "导出剪映工程 ↓"}
                  </button>
                  {exportJson?.finalVideo && (
                    <div className="st-hint" style={{ marginTop: 10 }}>
                      成片已生成：{exportJson.finalVideo}
                    </div>
                  )}
                  {exportJson?.draftPath && (
                    <div className="st-hint" style={{ marginTop: 10 }}>
                      剪映草稿：{exportJson.draftPath}
                    </div>
                  )}
                </div>
              </div>

              <EditConsole
                timeline={timeline}
                shotCfg={shotCfg}
                shots={shots}
                selectedClip={selectedClip}
                totalDur={totalDur}
                onSelect={onSelect}
                onReorder={onReorder}
                onCfgChange={onCfgChange}
                onDelete={onDelete}
              />

              {exportJson && exportJson.error && <div className="st-export-note st-export-note-err">{exportJson.error}</div>}
              {exportJson && exportJson.json && (
                <div className="st-export-json">
                  <pre>{`draft_content.json\n\n${JSON.stringify(exportJson.json, null, 2)}`}</pre>
                  <div className="st-export-note">
                    工程已生成并下载真实素材到本地：
                    <br />
                    📁 {exportJson.draftDir}
                    <br />
                    &nbsp;&nbsp;├─ draft_content.json（上）
                    <br />
                    &nbsp;&nbsp;├─ draft_meta.json
                    <br />
                    &nbsp;&nbsp;└─ materials/（{exportJson.count} 个真实 shot_*.mp4 / .png，剪映按相对路径找素材）
                    <br />
                    用剪映「导入草稿」打开即可精修。总时长 ≈ {exportJson.totalSec.toFixed(0)}s。
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        <div className="st-col st-right">
          {topology && (
            <WorkflowGraphPanel
              topology={topology}
              trace={trace}
              runState={runState}
              episode={traceEpisode}
              total={traceTotal}
            />
          )}
          {running && !topology && (
            <div className="wf-panel wf-panel-skeleton">
              <div className="wf-head">
                <span className="wf-title">运行追踪</span>
                <span className="wf-live">● LIVE</span>
              </div>
              <div className="wf-canvas">
                <div className="wf-empty">等待图结构…</div>
              </div>
            </div>
          )}
          <div style={{ padding: 14 }}>
            <h3 className="st-sec-title">任务中心</h3>
            {tasks.length === 0 && !topology && <div className="st-empty">暂无任务</div>}
            {tasks.map((t) => (
              <div className={`st-task${t.status === "err" ? " st-task-err" : ""}`} key={t.id}>
                <div className="st-task-t">
                  <span>{t.name}</span>
                  <span className={`st-task-st ${t.status}`}>
                    {t.status === "ok" ? "完成" : t.status === "err" ? "失败" : "运行中"}
                  </span>
                </div>
                <div className="st-bar">
                  <i style={{ width: t.prog + "%" }} />
                </div>
                {t.error && <div className="st-task-err-msg">{t.error}</div>}
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* HITL approval gate overlay — renders the returned image(s) and the
          approve / reject / steer controls when the backend pauses at a gate. */}
      {approval && (
        <div className="st-approval-overlay">
          <ApprovalBubble approval={approval} onRespond={handleWorkbenchApprove} />
        </div>
      )}
    </div>
  );
}
