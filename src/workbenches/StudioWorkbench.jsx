import React, { useState, useRef, useEffect, useMemo, useCallback } from "react";
import Icon from "../components/Icon.jsx";
import ApprovalBubble from "../components/ApprovalBubble.jsx";
import WorkflowGraphPanel from "../components/WorkflowGraphPanel.jsx";
import { subscribeContractEvents } from "../contract/eventBus.js";
import "./StudioWorkbench.css";

// Module-level state cache keyed by workflowId.
//
// Why: switching tabs (e.g. 漫剧go → 启动台 → 漫剧go) unmounts StudioWorkbench;
// without persistence, all in-progress work (timeline, shot list, generated
// assets, current phase, project form data, run state, loop-animation trace)
// is destroyed on unmount. The user reported "一切换窗口就崩溃丢失正在跑的进程"
// because the workflow was still running in the backend but the workbench had
// lost all visual context.
//
// Pattern: each useState is initialized from `_studioCache[key]`. A cleanup
// effect on unmount persists the latest values back to the cache. The cache
// survives React component lifecycle as long as the renderer process lives.
const _studioCache = {}; // { [workflowId]: { phase, runState, runId, project, ... } }

function _readCache(key, fallback) {
  return _studioCache[key] !== undefined ? _studioCache[key] : fallback;
}

function _writeCache(key, patch) {
  _studioCache[key] = { ...(_studioCache[key] || {}), ...patch };
}

// Standard prompt templates for the smart-input "🎲 随机生成" button. These
// cover a range of styles (皮克斯/吉卜力/写实/二次元) and genres (日常/末世/
// 科幻/治愈/校园/赛博) so the user can hit "generate" with no idea and
// still get a coherent 30s single-episode baseline. Each template is tuned
// to trigger the smart-fill path correctly: scene description + style hint +
// explicit "固定角色" so the parser extracts a Character entry.
const RANDOM_PROMPT_TEMPLATES = [
  "帮我生成一集漫剧，30 秒。皮克斯风格。一只小狗在后花园漫步时突然被异度漩涡卷入，最后在混沌中觉醒为神威犬。重点突出中间的混沌过程。固定角色：神威狗-皮克斯风格小狗，蓝眼睛，体型娇小，尾端带光。",
  "生成一集赛博朋克漫剧，30 秒。霓虹废墟里一名少年在雨水倒灌的巷子里觉醒异能，瞳孔由暗转蓝。固定角色：觉醒者-日系动漫少年，蓝色霓虹瞳孔，左臂纹路发光。",
  "一集治愈系漫剧，30 秒。吉卜力风格。太空船上一只孤独的小狐狸在舷窗看地球，遇见发光的小鲸鱼，结伴穿过星云。固定角色：小狐狸-吉卜力风格橘色狐狸，大尾巴，眼神温柔。",
  "末世废土写实风格漫剧，30 秒。一名拾荒者在荒漠里发现发光的孩子，激起求生意志，两人向远方灯塔走去。固定角色：拾荒者-写实风格中年男性，满脸风霜但眼神坚定，背金属背包。",
  "日系甜美校园漫剧，30 秒。樱花校园里女生表白失败，却意外收获一段友谊。重点刻画失落与微笑转折。固定角色：小樱-日系动漫高中女生，粉色樱花服装，扎双马尾，眼神含泪到弯笑。",
  "硬科幻深海探险漫剧，30 秒。深海潜水器遭遇发光的巨型水母，紧张对峙后化险为夷。固定角色：探险员-写实风格，黄黑潜水服，背光剪影。",
  "一集温馨宠物漫剧，30 秒，皮克斯风格。一只橘猫在城市屋顶追逐飘动的纸飞机，最后与放纸飞机的小女孩相遇。固定角色：橘猫-皮克斯风格橘色虎斑猫，圆眼睛，机灵。",
  "古风水墨意境漫剧，30 秒。青绿山水间一把古琴自鸣，山中少年循声而至。固定角色：琴师-水墨写意风格少年，白衣束发，眉眼清淡。",
];

function pickRandomPrompt() {
  return RANDOM_PROMPT_TEMPLATES[Math.floor(Math.random() * RANDOM_PROMPT_TEMPLATES.length)];
}

// Detect multi-episode intent directly from the user's free-text prompt. The
// server LLM fallback (parsed.mode === 'series' ? 'series' : 'single') often
// defaults to single even when the user clearly asks for a series ("帮我做
// 一个 5 集的漫剧系列"), so we override that with our own keyword + episode
// count parse from the original text. Returns { mode, eps } where each is
// `null` when not detected — caller merges into project state.
function detectEpisodeIntent(text) {
  if (!text) return { mode: null, eps: null };
  // Multi-episode indicators (Chinese + English).
  const seriesKw = /(系列|连载|多集|连续剧|saga|多季|分季|三集|四集|五集|六集|七集|八集|九集|十集|若干集|很多集|集\s*数|episode)/i;
  // More specific: "X 集" where X is a number (1-24), or "第N集".
  const arabicMatch = text.match(/(\d{1,2})\s*集/);
  const ordinalMatch = text.match(/第\s*([一二三四五六七八九十\d]{1,3})\s*集/);
  // "想做几集"/"做几集" etc. → ambiguous, not series signal alone.
  const looksLikeSeries = seriesKw.test(text) || !!arabicMatch || !!ordinalMatch;
  if (!looksLikeSeries) return { mode: null, eps: null };
  let eps = null;
  if (arabicMatch) {
    const n = parseInt(arabicMatch[1], 10);
    if (n >= 2 && n <= 24) eps = n;
  } else if (ordinalMatch) {
    const n = chineseToInt(ordinalMatch[1]);
    if (n >= 2 && n <= 24) eps = n;
  }
  return { mode: "series", eps };
}

function chineseToInt(s) {
  const map = { 一: 1, 两: 2, 二: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9, 十: 10 };
  if (map[s] !== undefined) return map[s];
  // Handle "十二" etc. by adding 十 + ones.
  if (/^\d+$/.test(s)) return parseInt(s, 10);
  const tens = s.match(/^十([一二三四五六七八九]?)$/);
  if (tens) {
    const ones = tens[1];
    return 10 + (map[ones] || 0);
  }
  const compound = s.match(/^([一二三四五六七八九])十([一二三四五六七八九]?)$/);
  if (compound) {
    const tens = map[compound[1]] || 1;
    const ones = map[compound[2] || ""] || 0;
    return tens * 10 + ones;
  }
  return NaN;
}

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
  // Module-level cache key: every workflowId gets its own persistent state.
  const cacheKey = manifest?.id || "__default_studio__";

  const [phase, setPhaseRaw] = useState(_readCache(cacheKey + ":phase", "script"));
  const [done, setDoneRaw] = useState(_readCache(cacheKey + ":done", {}));
  const [tasks, setTasksRaw] = useState(_readCache(cacheKey + ":tasks", []));
  const [assetsReady, setAssetsReadyRaw] = useState(_readCache(cacheKey + ":assetsReady", { character: false, scene: false, prop: false }));
  const [assetImgs, setAssetImgsRaw] = useState(_readCache(cacheKey + ":assetImgs", { character: {}, scene: {}, prop: {} }));
  const [curTab, setCurTabRaw] = useState(_readCache(cacheKey + ":curTab", "character"));
  const [shotState, setShotStateRaw] = useState(_readCache(cacheKey + ":shotState", {}));
  const [shotCfg, setShotCfgRaw] = useState(_readCache(cacheKey + ":shotCfg", {}));
  const [timeline, setTimelineRaw] = useState(_readCache(cacheKey + ":timeline", []));
  const [selectedClip, setSelectedClipRaw] = useState(_readCache(cacheKey + ":selectedClip", null));
  const [exportJson, setExportJsonRaw] = useState(_readCache(cacheKey + ":exportJson", null));
  const [exporting, setExportingRaw] = useState(_readCache(cacheKey + ":exporting", false));
  const [runState, setRunStateRaw] = useState(_readCache(cacheKey + ":runState", "idle")); // idle | running | done | error
  const [runId, setRunIdRaw] = useState(_readCache(cacheKey + ":runId", null));
  // HITL approval gate (first-frame / each-scene / end) surfaced while running
  // inside the workbench. The chat-shell ApprovalBubble is NOT mounted here, so
  // we render our own overlay and route the decision through the file control
  // channel (window.hermes.sendWorkflowInterrupt).
  const [approval, setApprovalRaw] = useState(_readCache(cacheKey + ":approval", null));
  // Live LangGraph node-trace (topology + per-node status map).
  const [topology, setTopologyRaw] = useState(_readCache(cacheKey + ":topology", null));
  const [trace, setTraceRaw] = useState(_readCache(cacheKey + ":trace", {}));
  const [traceEpisode, setTraceEpisodeRaw] = useState(_readCache(cacheKey + ":traceEpisode", 0));
  const [traceTotal, setTraceTotalRaw] = useState(_readCache(cacheKey + ":traceTotal", 1));

  const [project, setProjectRaw] = useState(_readCache(cacheKey + ":project", {
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
  }));

  // 智能输入：自然语言 → 自动填表
  const [nlText, setNlTextRaw] = useState(_readCache(cacheKey + ":nlText", ""));
  const [nlParsing, setNlParsingRaw] = useState(_readCache(cacheKey + ":nlParsing", false));
  const [nlError, setNlErrorRaw] = useState(_readCache(cacheKey + ":nlError", ""));

  // Refs that always hold the latest value of each piece of state. The wrapped
  // setters read the latest from these refs so functional updates like
  // `setPhase((p) => p + 1)` always see the most recent value (avoiding the
  // stale closure trap that React's own useState setter handles by capturing
  // a queue — we cannot do that here without re-implementing it).
  const phaseRef = useRef(phase); phaseRef.current = phase;
  const doneRef = useRef(done); doneRef.current = done;
  const tasksRef = useRef(tasks); tasksRef.current = tasks;
  const assetsReadyRef = useRef(assetsReady); assetsReadyRef.current = assetsReady;
  const assetImgsRef = useRef(assetImgs); assetImgsRef.current = assetImgs;
  const curTabRef = useRef(curTab); curTabRef.current = curTab;
  const shotStateRef = useRef(shotState); shotStateRef.current = shotState;
  const shotCfgRef = useRef(shotCfg); shotCfgRef.current = shotCfg;
  const timelineRef = useRef(timeline); timelineRef.current = timeline;
  const selectedClipRef = useRef(selectedClip); selectedClipRef.current = selectedClip;
  const exportJsonRef = useRef(exportJson); exportJsonRef.current = exportJson;
  const exportingRef = useRef(exporting); exportingRef.current = exporting;
  const runStateRef = useRef(runState); runStateRef.current = runState;
  const runIdRef = useRef(runId); runIdRef.current = runId;
  const approvalRef = useRef(approval); approvalRef.current = approval;
  const topologyRef = useRef(topology); topologyRef.current = topology;
  const traceRef = useRef(trace); traceRef.current = trace;
  const traceEpisodeRef = useRef(traceEpisode); traceEpisodeRef.current = traceEpisode;
  const traceTotalRef = useRef(traceTotal); traceTotalRef.current = traceTotal;
  const projectRef = useRef(project); projectRef.current = project;
  const nlTextRef = useRef(nlText); nlTextRef.current = nlText;
  const nlParsingRef = useRef(nlParsing); nlParsingRef.current = nlParsing;
  const nlErrorRef = useRef(nlError); nlErrorRef.current = nlError;

  // Wrapped setters: each one writes the new value to the module-level cache
  // immediately so that if the component is unmounted before its cleanup runs
  // (e.g. fast tab switching during heavy state churn) nothing is lost.
  const setPhase = (v) => setPhaseRaw((prev) => { const n = typeof v === "function" ? v(prev) : v; _writeCache(cacheKey, { phase: n }); return n; });
  const setDone = (v) => setDoneRaw((prev) => { const n = typeof v === "function" ? v(prev) : v; _writeCache(cacheKey, { done: n }); return n; });
  const setTasks = (v) => setTasksRaw((prev) => { const n = typeof v === "function" ? v(prev) : v; _writeCache(cacheKey, { tasks: n }); return n; });
  const setAssetsReady = (v) => setAssetsReadyRaw((prev) => { const n = typeof v === "function" ? v(prev) : v; _writeCache(cacheKey, { assetsReady: n }); return n; });
  const setAssetImgs = (v) => setAssetImgsRaw((prev) => { const n = typeof v === "function" ? v(prev) : v; _writeCache(cacheKey, { assetImgs: n }); return n; });
  const setCurTab = (v) => setCurTabRaw((prev) => { const n = typeof v === "function" ? v(prev) : v; _writeCache(cacheKey, { curTab: n }); return n; });
  const setShotState = (v) => setShotStateRaw((prev) => { const n = typeof v === "function" ? v(prev) : v; _writeCache(cacheKey, { shotState: n }); return n; });
  const setShotCfg = (v) => setShotCfgRaw((prev) => { const n = typeof v === "function" ? v(prev) : v; _writeCache(cacheKey, { shotCfg: n }); return n; });
  const setTimeline = (v) => setTimelineRaw((prev) => { const n = typeof v === "function" ? v(prev) : v; _writeCache(cacheKey, { timeline: n }); return n; });
  const setSelectedClip = (v) => setSelectedClipRaw((prev) => { const n = typeof v === "function" ? v(prev) : v; _writeCache(cacheKey, { selectedClip: n }); return n; });
  const setExportJson = (v) => setExportJsonRaw((prev) => { const n = typeof v === "function" ? v(prev) : v; _writeCache(cacheKey, { exportJson: n }); return n; });
  const setExporting = (v) => setExportingRaw((prev) => { const n = typeof v === "function" ? v(prev) : v; _writeCache(cacheKey, { exporting: n }); return n; });
  const setRunState = (v) => setRunStateRaw((prev) => { const n = typeof v === "function" ? v(prev) : v; _writeCache(cacheKey, { runState: n }); return n; });
  const setRunId = (v) => setRunIdRaw((prev) => { const n = typeof v === "function" ? v(prev) : v; _writeCache(cacheKey, { runId: n }); return n; });
  const setApproval = (v) => setApprovalRaw((prev) => { const n = typeof v === "function" ? v(prev) : v; _writeCache(cacheKey, { approval: n }); return n; });
  const setTopology = (v) => setTopologyRaw((prev) => { const n = typeof v === "function" ? v(prev) : v; _writeCache(cacheKey, { topology: n }); return n; });
  const setTrace = (v) => setTraceRaw((prev) => { const n = typeof v === "function" ? v(prev) : v; _writeCache(cacheKey, { trace: n }); return n; });
  const setTraceEpisode = (v) => setTraceEpisodeRaw((prev) => { const n = typeof v === "function" ? v(prev) : v; _writeCache(cacheKey, { traceEpisode: n }); return n; });
  const setTraceTotal = (v) => setTraceTotalRaw((prev) => { const n = typeof v === "function" ? v(prev) : v; _writeCache(cacheKey, { traceTotal: n }); return n; });
  const setProject = (v) => setProjectRaw((prev) => { const n = typeof v === "function" ? v(prev) : v; _writeCache(cacheKey, { project: n }); return n; });
  const setNlText = (v) => setNlTextRaw((prev) => { const n = typeof v === "function" ? v(prev) : v; _writeCache(cacheKey, { nlText: n }); return n; });
  const setNlParsing = (v) => setNlParsingRaw((prev) => { const n = typeof v === "function" ? v(prev) : v; _writeCache(cacheKey, { nlParsing: n }); return n; });
  const setNlError = (v) => setNlErrorRaw((prev) => { const n = typeof v === "function" ? v(prev) : v; _writeCache(cacheKey, { nlError: n }); return n; });

  async function handleSmartFill() {
    if (!nlText.trim() || nlParsing) return;
    setNlParsing(true);
    setNlError("");
    try {
      const resp = await window.hermes.parseWorkflowIntent(nlText, manifest?.id);
      if (resp?.error) { setNlError(resp.error); return; }
      const v = resp?.inputObj;
      if (!v) { setNlError("解析无结果"); return; }
      const fixedChars = Array.isArray(v.characters) && v.characters.length > 0
        ? v.characters.map((c) => `${c.name}=${c.prompt}`).join("\n")
        : "";
      // Frontend overrides: detect multi-episode intent from the original text
      // and let it win over the server LLM's `single` fallback. This is the
      // single common failure mode — user types "做一个 5 集的漫剧系列" but
      // LLM returns mode="single", wiping the user's clear intent.
      const intent = detectEpisodeIntent(nlText);
      // NB: resolvedMode/resolvedEps MUST be computed inside the setProject
      // updater so we close over `prev` (the real current project state) and
      // not over a free `p` that doesn't exist in this scope — that bug
      // crashed every smart-fill attempt with "p is not defined". The
      // detectEpisodeIntent call itself is pure (only reads nlText), so it's
      // safe to hoist outside the updater.
      setProject((prev) => {
        const resolvedMode = intent.mode
          || (v.mode === "series" ? "series"
              : (v.mode === "single" ? "single" : prev.mode));
        const llmEps = Number(v.total_episodes);
        const resolvedEps = intent.eps
          || (Number.isFinite(llmEps) && llmEps >= 2 && llmEps <= 24 ? llmEps : prev.eps);
        return {
          ...prev,
          name: v.project_name || prev.name,
          script: v.script || prev.script,
          seriesScript: v.series_script || prev.seriesScript,
          mode: resolvedMode,
          eps: resolvedMode === "series" ? resolvedEps : (resolvedMode === "single" ? 1 : prev.eps),
          style: v.style || prev.style,
          fixedChars: fixedChars || prev.fixedChars,
        };
      });
      setNlText("");
    } catch (e) {
      setNlError("解析失败：" + (e?.message || String(e)));
    } finally {
      setNlParsing(false);
    }
  }

  const timersRef = useRef([]);
  useEffect(() => {
    return () => timersRef.current.forEach((t) => clearInterval(t));
  }, []);

  // 🎲 Random-fill button: when the user has no idea what to make, this fills
  // the smart-input textarea with a curated standard template (one of several
  // styles/genres tuned to be parser-friendly). The user can then either edit
  // it, hit "✨ 智能解析填表" to LLM-parse it, or click "一键生成全部资产 →"
  // directly. Keeps the click-count low: 1 click gets a usable prompt.
  function handleRandomPrompt() {
    if (nlParsing) return;
    setNlText(pickRandomPrompt());
    setNlError("");
  }

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

      if (type === "workflow.error") {
        // Surface backend workflow errors (Python `langgraph_runtime` reports
        // failures via on_event("workflow.error", ...); `useAgentStream`
        // forwards it through the contract eventBus). Without this branch
        // the workbench kept the runState stuck on "running" while the
        // chat panel reported a generic "工作流运行出错" with no detail.
        const errMsg = ev.payload?.message || ev.message || "工作流运行出错";
        setRunState("error");
        setApproval(null);
        // Stop any active gate (don't let stale approval UI linger).
        if (typeof ev.payload?.gate_id === "string") {
          // Future: map gate_id to specific task to mark as failed.
        }
        // Mark the in-flight node as errored in the trace for the loop panel.
        setTrace((prev) => {
          const next = { ...prev };
          for (const k in next) if (next[k] === "running") next[k] = "error";
          return next;
        });
        setTasks((prev) =>
          prev.map((t) =>
            t.status === "run"
              ? { ...t, status: "err", error: errMsg, prog: 100 }
              : t
          )
        );
        // Stash the full error payload for the banner render in <TaskErrorBanner/>.
        // We keep it on the approval slot so it's cheap to read from a sibling
        // component without lifting state into App.
        setApproval({ id: "workflow-error", label: "工作流错误", message: errMsg, source: "workflow" });
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
          // The Python runtime never sends a workflow.progress with
          // status="done" — node completion is signalled only via
          // workflow.trace { node, status: "done" }. Without syncing the
          // tasks list here, the TaskCenter would leave every step stuck
          // on "运行中" (status="run", prog capped at 95) even after the
          // step finished, and the progress bar never reached 100%.
          if (p.status === "done") {
            setTasks((prev) =>
              prev.map((t) =>
                t.step === p.node ? { ...t, status: "ok", prog: 100 } : t
              )
            );
          }
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
  // NOTE: episode is 0-indexed on the backend (Python `state.current_episode`
  // starts at 0 and is bumped by `finalize_episode` AFTER the episode is
  // rendered). Using `st.ep && st.n` would silently drop episode 0 (truthy
  // check fails on 0). Check both fields are non-null finite numbers instead.
  const shots = useMemo(() => {
    const list = Object.entries(shotState)
      .filter(([, st]) => Number.isFinite(st.ep) && Number.isFinite(st.n))
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
    onRun(manifest, input);
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
          {runState === "error" && (
            <div className="st-card st-runerror">
              <div className="st-section">
                <div className="st-section-title st-runerror-title">
                  ❌ 工作流运行出错
                </div>
                <div className="st-runerror-msg">
                  {approval?.source === "workflow" ? approval.message : "请查看右侧任务中心或 chat 流的错误详情。"}
                </div>
                <div className="st-runerror-hint">
                  常见原因：Agnes API 超时（生成图片/视频的服务等不到响应）、网络代理 127.0.0.1:7897 不通、key 过期、prompt 触发内容审核。
                  可重试，或调整文案后重跑。
                </div>
                <div style={{ marginTop: 12 }}>
                  <button
                    className="st-primary"
                    onClick={() => {
                      setRunState("idle");
                      setApproval(null);
                    }}
                  >
                    我知道了
                  </button>
                </div>
              </div>
            </div>
          )}
          {phase === "script" && (
            <div className="st-card st-form-card">
              <div className="st-section st-smart-input">
                <div className="st-section-title">
                  ✨ 智能输入
                  <small className="st-sub" style={{ marginLeft: 8 }}>
                    用自然语言描述漫剧需求，自动填表（项目名/脚本/模式/集数/风格/角色）
                  </small>
                </div>
                <textarea
                  rows={3}
                  value={nlText}
                  onChange={(e) => setNlText(e.target.value)}
                  placeholder={"例如：帮我生成一集漫剧，不少于 30 秒。皮克斯风格。一只小狗在后花园漫步，然后堕落了异度空间，最后变成神威狗。重点突出中间的混沌过程。"}
                  disabled={nlParsing}
                />
                {nlError && <div className="st-smart-error">{nlError}</div>}
                <div className="st-form-actions" style={{ marginTop: 8 }}>
                  <button
                    className="st-secondary"
                    onClick={handleRandomPrompt}
                    disabled={nlParsing}
                    title="从 8 个标准模板中随机抽一条填入，方便没想法时快速试"
                  >
                    🎲 随机生成
                  </button>
                  <button
                    className="st-primary"
                    onClick={handleSmartFill}
                    disabled={!nlText.trim() || nlParsing}
                  >
                    {nlParsing ? "解析中…" : "✨ 智能解析填表"}
                  </button>
                  {nlText && (
                    <button
                      className="st-secondary"
                      onClick={() => { setNlText(""); setNlError(""); }}
                      disabled={nlParsing}
                    >
                      清空
                    </button>
                  )}
                </div>
              </div>

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
