import React, { useState, useRef, useEffect, useMemo, useCallback } from "react";
import Icon from "../components/Icon.jsx";
import "./StudioWorkbench.css";

// Short-drama production studio workbench.
//
// Frontend authoring UI: script → assets → storyboard → edit/export. The final
// "成片" step builds a Jianying (CapCut) draft_content.json so the cut can be
// refined in Jianying. Generation steps are mocked client-side for now (same
// pattern as ManjuCraftWorkbench's mock run); they can later call manju_craft's
// builder via onRun. Adding this workbench required NO router/App.jsx change —
// only: this component + registry entry + a manifest with ui.type "workbench".

const PHASES = [
  { id: "script", label: "剧本" },
  { id: "assets", label: "资产" },
  { id: "storyboard", label: "分镜" },
  { id: "export", label: "成片" },
];

const ASSET_DEFS = {
  character: [
    { name: "林夕", tag: "女主·实习生", views: ["正面", "侧面", "全身"] },
    { name: "顾屿", tag: "男主·霸总", views: ["正面", "侧面", "全身"] },
  ],
  scene: [
    { name: "雨夜天桥", tag: "第1集", views: ["全景", "近景", "空镜"] },
    { name: "集团大堂", tag: "第2集", views: ["全景", "近景", "空镜"] },
  ],
  prop: [
    { name: "旧怀表", tag: "关键道具", views: ["正面", "细节", "佩戴"] },
  ],
};

const DEMO_SHOTS = [
  { ep: 1, n: 1, script: "雨夜，林夕撑伞跑过天桥，怀表从包里滑落。", cam: "特写·手持", model: "agnes-2.5-flash", prompt: "雨夜天桥，女生撑伞奔跑，脚下积水倒映霓虹" },
  { ep: 1, n: 2, script: "顾屿的车撞上护栏，他茫然抬头，记忆碎片闪回。", cam: "中景·慢镜", model: "agnes-2.5-flash", prompt: "豪华轿车追尾护栏，男主失神，雨刷摆动" },
  { ep: 1, n: 3, script: "林夕拾起怀表，背面刻着熟悉的名字。", cam: "近景·推镜", model: "agnes-2.5-flash", prompt: "女生指尖特写，旧怀表背面刻字" },
  { ep: 2, n: 1, script: "集团大堂，顾屿恢复记忆，却假装不识林夕。", cam: "全景·固定", model: "agnes-2.5-flash", prompt: "大理石大堂，男女主对峙，玻璃幕墙反光" },
  { ep: 2, n: 2, script: "林夕递交辞呈，转身时眼泪落下。", cam: "中景·跟拍", model: "agnes-2.5-flash", prompt: "女生递信封，背影，电梯门缓缓关上" },
  { ep: 2, n: 3, script: "顾屿追出，倒数三秒在雨中喊出她的名字。", cam: "全景·航拍", model: "agnes-2.5-flash", prompt: "雨中广场，男主奔跑呼喊，镜头拉升" },
];

const PX_PER_SEC = 48;
const US = 1_000_000; // Jianying timerange unit = microseconds
const shotKey = (s) => `${s.ep}-${s.n}`;

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

function AssetLibrary({ curTab, setCurTab, assetsReady, assetImgs, onGenOne, onGenAll }) {
  const list = assetsReady[curTab] ? ASSET_DEFS[curTab] : null;
  const imgs = assetImgs[curTab] || {};
  return (
    <div className="st-col st-left">
      <div className="st-tabs">
        {Object.keys(ASSET_DEFS).map((t) => (
          <div
            key={t}
            className={`st-tab ${curTab === t ? "active" : ""}`}
            onClick={() => setCurTab(t)}
          >
            {t === "character" ? "角色" : t === "scene" ? "场景" : "道具"}
          </div>
        ))}
      </div>
      <div className="st-asset-list">
        {!list && <div className="st-empty">尚未生成<br />去「资产」页一键生成</div>}
        {list &&
          list.map((a) => (
            <div className="st-asset-card" key={a.name}>
              <div className="st-asset-name">
                {a.name}
                <span className="st-asset-tag">{a.tag}</span>
              </div>
              {imgs[a.name] ? (
                <div className="st-asset-img"><img src={imgs[a.name]} alt={a.name} /></div>
              ) : (
                <div className="st-views">
                  {a.views.map((v) => (
                    <div className="st-view" key={v}>{v}</div>
                  ))}
                </div>
              )}
              <div className="st-asset-views-label">{a.views.join(" / ")}</div>
            </div>
          ))}
        {list && (
          <button className="st-gen-btn" onClick={() => onGenOne(curTab)}>
            ↻ 重新生成该组
          </button>
        )}
        <div style={{ marginTop: 14 }}>
          <button className="st-primary st-block" onClick={onGenAll}>
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
              <textarea
                defaultValue={s.script}
                onChange={(e) => onScriptChange(k, e.target.value)}
              />
            </div>
            <div className="st-shot-col">
              <textarea
                defaultValue={s.prompt}
                placeholder="拍摄法 / 提示词"
              />
              <div className="st-row2">
                <select defaultValue={s.cam}>
                  <option>{s.cam}</option>
                  <option>特写</option>
                  <option>全景</option>
                  <option>俯拍</option>
                </select>
                <select defaultValue={s.model}>
                  <option>{s.model}</option>
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
                <div className="st-mini-prog"><i /></div>
              )}
              <div className="st-shot-btns">
                <button className="st-gen-shot" onClick={() => onGenShot(k)}>
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
    const x = e.clientX - rect.left + track.scrollLeft - 100; // minus label width
    let acc = 0;
    let toIdx = timeline.length;
    for (let i = 0; i < timeline.length; i++) {
      const w = Math.max(30, (shotCfg[timeline[i]]?.dur || 4) * PX_PER_SEC);
      if (x < acc + w / 2) { toIdx = i; break; }
      acc += w;
    }
    const fromIdx = timeline.indexOf(fromKey);
    if (fromIdx === -1) return;
    if (fromIdx === toIdx || fromIdx === toIdx - 1) return; // no-op
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
          <span
            key={s}
            className={`st-tick ${s % 5 === 0 ? "maj" : ""}`}
            style={{ left: 100 + s * PX_PER_SEC }}
          >
            {s}s
          </span>
        ))}
      </div>
      <div style={{ position: "relative" }}>
        <div className="st-track-labels">视频轨道</div>
        <div
          className="st-tl-track"
          ref={trackRef}
          onDragOver={(e) => e.preventDefault()}
          onDrop={handleDrop}
        >
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
                <span className="st-clabel">第{s.ep}集·镜{s.n}</span>
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

export default function StudioWorkbench({ manifest, session, onExit, model, backendStatus }) {
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
  const [project, setProject] = useState({
    name: "倒数三秒说爱你",
    style: "二次元",
    eps: 2,
    res: "1080×1920",
    sec: 4,
    fps: 30,
  });

  const timersRef = useRef([]);
  useEffect(() => {
    return () => timersRef.current.forEach((t) => clearInterval(t));
  }, []);

  const shotStateRef = useRef(shotState);
  useEffect(() => { shotStateRef.current = shotState; }, [shotState]);

  // Studio backend bridge: Electron renderer → IPC → main process → Agnes API.
  // Uses window.hermes.studioCall (IPC) to avoid renderer fetch/CSP issues.
  const api = useCallback(async (action, params) => {
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
  }, []);

  // ── tasks ──
  const addTask = useCallback((name) => {
    const t = { id: Date.now() + Math.random(), name, status: "run", prog: 0 };
    setTasks((prev) => [t, ...prev]);
    return t;
  }, []);
  const finishTask = useCallback((id, error) => {
    setTasks((prev) => prev.map((x) => (x.id === id ? { ...x, prog: 100, status: error ? "err" : "ok", error } : x)));
  }, []);
  const tick = useCallback((t, to) => {
    const iv = setInterval(() => {
      setTasks((prev) =>
        prev.map((x) => {
          if (x.id !== t.id) return x;
          const next = Math.min(to, x.prog + Math.max(4, (to - x.prog) / 8));
          if (next >= to) {
            clearInterval(iv);
            return { ...x, prog: 100, status: "ok" };
          }
          return { ...x, prog: next };
        })
      );
    }, 120);
    timersRef.current.push(iv);
  }, []);

  // ── assets (real Agnes image generation) ──
  const genAssetImage = useCallback(async (t, asset) => {
    const label = { character: "角色三视图设定", scene: "场景概念图", prop: "道具细节图" }[t];
    const task = addTask(`生成 ${asset.name} 参考图`);
    tick(task, 100);
    const j = await api("generate-image", {
      prompt: `${asset.name}，${asset.tag}，${label}，高细节，电影级，统一风格`,
      size: "2K",
      ratio: "3:4",
    });
    if (j && j.ok && j.url) {
      setAssetImgs((prev) => ({ ...prev, [t]: { ...(prev[t] || {}), [asset.name]: j.url } }));
      finishTask(task.id);
    } else {
      finishTask(task.id, (j && j.error) || "生成失败");
    }
  }, [api, addTask, tick, finishTask]);

  const genOne = useCallback(async (t) => {
    await Promise.all(ASSET_DEFS[t].map((a) => genAssetImage(t, a)));
    setAssetsReady((prev) => ({ ...prev, [t]: true }));
  }, [genAssetImage]);
  const genAll = useCallback(async () => {
    for (const t of ["character", "scene", "prop"]) await genOne(t);
  }, [genOne]);

  // ── shots (real Agnes image + video generation) ──
  const genShot = useCallback(async (k) => {
    const s = DEMO_SHOTS.find((x) => shotKey(x) === k);
    setShotState((prev) => ({ ...prev, [k]: { ...(prev[k] || {}), status: "busy" } }));
    const task = addTask(`生成 第${s.ep}集·镜${s.n} 关键帧`);
    tick(task, 100);
    const j = await api("generate-image", {
      prompt: `${s.prompt}，${project.style}风格，电影级镜头，高细节`,
      size: "2K",
      ratio: "9:16",
    });
    const prev = shotStateRef.current[k] || {};
    const st = { ...prev, status: j && j.ok ? "img" : "error" };
    if (j && j.ok && j.url) st.imgUrl = j.url;
    else st.error = j && j.error;
    setShotState((prev2) => ({ ...prev2, [k]: st }));
    finishTask(task.id, (j && !j.ok && j.error) || undefined);
  }, [api, addTask, tick, finishTask, project.style]);

  const genVideoShot = useCallback(async (k) => {
    const s = DEMO_SHOTS.find((x) => shotKey(x) === k);
    const prevImg = shotStateRef.current[k] && shotStateRef.current[k].imgUrl;
    setShotState((prev) => ({ ...prev, [k]: { ...(prev[k] || {}), status: "busy" } }));
    const task = addTask(`生成 第${s.ep}集·镜${s.n} 视频`);
    tick(task, 100);
    const j = await api("generate-video", {
      prompt: `${s.prompt}，${project.style}风格，自然运动，电影级镜头`,
      image: prevImg || undefined,
      width: 1152,
      height: 768,
      num_frames: 81,
      frame_rate: 24,
    });
    const prev = shotStateRef.current[k] || {};
    const st = { ...prev, status: j && j.ok ? "done" : (prev.imgUrl ? "img" : "error") };
    if (j && j.ok && j.url) st.videoUrl = j.url;
    else st.error = j && j.error;
    setShotState((prev2) => ({ ...prev2, [k]: st }));
    finishTask(task.id, (j && !j.ok && j.error) || undefined);
  }, [api, addTask, tick, finishTask, project.style]);

  const onScriptChange = useCallback((k, val) => {
    setShotState((prev) => ({ ...prev, [k]: { ...(prev[k] || {}), script: val } }));
  }, []);

  // Sync timeline from generated shots (preserve user reorder).
  useEffect(() => {
    setTimeline((prev) => {
      const doneKeys = DEMO_SHOTS.filter((s) => {
        const st = shotState[shotKey(s)] || {};
        return st.status === "img" || st.status === "done";
      }).map(shotKey);
      const set = new Set(doneKeys);
      const kept = prev.filter((k) => set.has(k));
      const keptSet = new Set(kept);
      const added = doneKeys.filter((k) => !keptSet.has(k));
      const next = kept.concat(added);
      // ensure cfg exists for each
      return next;
    });
  }, [shotState]);

  useEffect(() => {
    setShotCfg((prev) => {
      const next = { ...prev };
      timeline.forEach((k) => {
        if (!next[k]) next[k] = { dur: project.sec, trans: "none" };
      });
      return next;
    });
  }, [timeline, project.sec]);

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

  // ── export ──
  const totalDur = useMemo(
    () => timeline.reduce((sum, k) => sum + (shotCfg[k]?.dur || 4), 0),
    [timeline, shotCfg]
  );

  const handleExport = useCallback(async () => {
    if (!timeline.length) {
      setExportJson({ error: "没有可导出的镜头，请先到「分镜」页生成" });
      return;
    }
    const shotsPayload = timeline.map((k) => {
      const s = DEMO_SHOTS.find((x) => shotKey(x) === k);
      const st = shotState[k] || {};
      return { key: k, ep: s?.ep, n: s?.n, videoUrl: st.videoUrl || null, imgUrl: st.imgUrl || null };
    });
    setExporting(true);
    const t = addTask("导出剪映工程（下载素材 + 生成草稿）");
    tick(t, 100);
    const j = await api("prepare-export", {
      project,
      timeline,
      shotCfg,
      shots: shotsPayload,
    });
    setExporting(false);
    if (j && j.ok) {
      setExportJson({ json: j.json, totalSec: j.totalSec, count: j.count, draftDir: j.draftDir });
    } else {
      setExportJson({ error: (j && j.error) || "导出失败" });
    }
    finishTask(t.id);
  }, [timeline, shotCfg, project, shotState, api, addTask, tick, finishTask]);

  const goPhase = useCallback((id) => {
    setPhase(id);
    setDone((prev) => (id === "script" ? prev : { ...prev, script: true }));
    if (id === "storyboard") setDone((prev) => ({ ...prev, assets: true }));
    if (id === "export") setDone((prev) => ({ ...prev, storyboard: true }));
  }, []);

  function handleScriptGenerate() {
    const t = addTask("拆分剧本 → 资产 + 分镜");
    tick(t, 100);
    const to = setTimeout(() => {
      setDone((prev) => ({ ...prev, script: true }));
      setPhase("assets");
    }, 700);
    timersRef.current.push(to);
  }

  return (
    <div className="st-workbench" onClick={() => selectedClip && setSelectedClip(null)}>
      <div className="st-topbar">
        <div className="st-brand">
          {manifest?.name || "短剧制片工作台"}
          <small>前端编排台 · 导出剪映工程</small>
        </div>
        {onExit && (
          <button className="st-icon-btn" onClick={onExit} title="退出工作台">
            <Icon name="close" size={14} />
          </button>
        )}
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
        />

        <div className="st-center">
          {phase === "script" && (
            <div className="st-card">
              <label className="st-fld">
                <span>项目名</span>
                <input
                  value={project.name}
                  onChange={(e) => setProject((p) => ({ ...p, name: e.target.value }))}
                />
              </label>
              <label className="st-fld">
                <span>剧本 / 大纲</span>
                <textarea
                  defaultValue="都市爱情短剧。霸总顾屿在雨夜车祸失忆，只记得女孩林夕的声音。林夕是他公司的实习生，为还债接近他，却在相处中动了真心。当记忆归来，他必须在家族利益与真爱间抉择。"
                  style={{ minHeight: 90 }}
                />
              </label>
              <div className="st-row2">
                <label className="st-fld">
                  <span>风格</span>
                  <select
                    value={project.style}
                    onChange={(e) => setProject((p) => ({ ...p, style: e.target.value }))}
                  >
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
                    max="6"
                    value={project.eps}
                    onChange={(e) => setProject((p) => ({ ...p, eps: e.target.value }))}
                  />
                </label>
              </div>
              <div className="st-row2">
                <label className="st-fld">
                  <span>分辨率</span>
                  <select
                    value={project.res}
                    onChange={(e) => setProject((p) => ({ ...p, res: e.target.value }))}
                  >
                    <option>1080×1920（竖屏）</option>
                    <option>1920×1080（横屏）</option>
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
              <button className="st-primary" onClick={handleScriptGenerate}>
                生成资产与分镜 →
              </button>
              <div className="st-hint">点击后模拟：生成角色/场景/道具参考图 → 自动拆出分镜。</div>
            </div>
          )}

          {phase === "assets" && (
            <div className="st-card">
              <div className="st-hint">
                资产由独立工作流（manju_assets）生成，分镜在生成时引用这里的参考图保证一致性。点击下方按钮模拟生成。
              </div>
              <button className="st-primary" onClick={genAll}>
                一键生成全部资产 →
              </button>
            </div>
          )}

          {phase === "storyboard" && (
            <StoryboardEditor
              shots={DEMO_SHOTS}
              shotState={shotState}
              onGenShot={genShot}
              onGenVideo={genVideoShot}
              onScriptChange={onScriptChange}
            />
          )}

          {phase === "export" && (
            <div className="st-export-wrap">
              <div className="st-card">
                <div className="st-hint">
                  横轨时间轴：shot 按时长占宽度，左右拖拽换顺序。点击片段打开详情编辑时长/转场。编排完点「导出剪映工程」生成 draft JSON。
                </div>
                <div className="st-row2">
                  <label className="st-fld">
                    <span>分辨率</span>
                    <select
                      value={project.res}
                      onChange={(e) => setProject((p) => ({ ...p, res: e.target.value }))}
                    >
                      <option>1080×1920</option>
                      <option>1920×1080</option>
                    </select>
                  </label>
                  <label className="st-fld">
                    <span>帧率 fps</span>
                    <select
                      value={project.fps}
                      onChange={(e) => setProject((p) => ({ ...p, fps: parseInt(e.target.value, 10) }))}
                    >
                      <option>30</option>
                      <option>25</option>
                      <option>60</option>
                    </select>
                  </label>
                </div>
                <button className="st-primary" onClick={handleExport} disabled={exporting}>
                  {exporting ? "导出中…（下载素材 + 生成草稿）" : "导出剪映工程 ↓"}
                </button>
              </div>

              <EditConsole
                timeline={timeline}
                shotCfg={shotCfg}
                shots={DEMO_SHOTS}
                selectedClip={selectedClip}
                totalDur={totalDur}
                onSelect={onSelect}
                onReorder={onReorder}
                onCfgChange={onCfgChange}
                onDelete={onDelete}
              />

              {exportJson && exportJson.error && (
                <div className="st-export-note st-export-note-err">{exportJson.error}</div>
              )}
              {exportJson && exportJson.json && (
                <div className="st-export-json">
                  <pre>{`draft_content.json\n\n${JSON.stringify(exportJson.json, null, 2)}`}</pre>
                  <div className="st-export-note">
                    工程已生成并下载真实素材到本地：<br />
                    📁 {exportJson.draftDir}<br />
                    &nbsp;&nbsp;├─ draft_content.json（上）<br />
                    &nbsp;&nbsp;├─ draft_meta.json<br />
                    &nbsp;&nbsp;└─ materials/（{exportJson.count} 个真实 shot_*.mp4 / .png，剪映按相对路径找素材）<br />
                    用剪映「导入草稿」打开即可精修。总时长 ≈ {exportJson.totalSec.toFixed(0)}s。
                  </div>
                </div>
              )}
            </div>
          )}
        </div>

        <div className="st-col st-right">
          <div style={{ padding: 14 }}>
            <h3 className="st-sec-title">任务中心</h3>
            {tasks.length === 0 && <div className="st-empty">暂无任务</div>}
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
    </div>
  );
}
