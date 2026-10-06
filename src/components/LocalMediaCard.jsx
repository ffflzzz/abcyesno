import React, { useEffect, useRef, useState } from "react";

// 设置面板「本地出片服务」卡片（2026-10-05）。
//
// ⚠️ 这里**不做探测逻辑** —— 厂商档、接入档案、轮询窗口都在短剧后台那一侧
// （`shortdrama/v5/local_services.py`），面板只转发与展示。理由与本仓库其他
// 面板一致：同一件事写两份，迟早只对一边生效。
//
// 面板上也**不会自动查状态**：那会顺手启动一个 Python 进程。设置面板是常开常关的，
// 所以只有按下这两颗按钮才起后台，文案里写清楚这一点。

const ROLE_LABEL = {
  prompt: "提示词",
  image: "首帧图",
  width: "画面宽",
  height: "画面高",
  frames: "帧数",
  last_image: "尾帧图",
  seed: "随机种子",
};

const REQUIRED_ROLES = ["prompt", "image", "frames"];

function api() {
  if (typeof window === "undefined" || !window.hermes) return null;
  return window.hermes.localMediaCall || null;
}

function deviceLine(devices) {
  const list = devices || [];
  if (!list.length) return "没报到显卡";
  return list
    .map((d) => `${d.name || "未知显卡"} ${d.vram_gb || "?"}GB`)
    .join("、");
}

export default function LocalMediaCard() {
  const [status, setStatus] = useState(null);
  const [found, setFound] = useState([]);
  const [tried, setTried] = useState([]);
  const [extraAddr, setExtraAddr] = useState("");
  const [picked, setPicked] = useState("");
  const [wfPath, setWfPath] = useState("");
  const [inspect, setInspect] = useState(null);
  const [manual, setManual] = useState({});
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  async function call(action, params) {
    const fn = api();
    if (!fn) {
      setError("这版应用没有本地服务接口（需要重启应用加载新版）");
      return null;
    }
    setBusy(action);
    setError("");
    try {
      const r = await fn(action, params || {});
      if (!r || !r.ok) {
        setError((r && r.error) || "操作失败");
        return null;
      }
      return r.data;
    } finally {
      if (mounted.current) setBusy("");
    }
  }

  function applyStatus(st) {
    if (!st) return;
    setStatus(st);
    if (st.profile && st.profile.address && !picked) setPicked(st.profile.address);
  }

  async function runInspect(address, opts) {
    if (!address) { setError("先选一台机器"); return; }
    const params = { address };
    const path = (opts && opts.workflow_path) !== undefined ? opts.workflow_path : wfPath;
    if (path) params.workflow_path = path;
    const d = await call("inspect", params);
    if (!d || !mounted.current) return;
    setInspect(d);
    setNote(
      (d.missing || []).length
        ? `这张图（${d.source}）认出 ${Object.keys(d.mapping || {}).length} 项，还有 ${(d.missing || []).length} 项要你在下面指一下`
        : `这张图来自：${d.source}。${Object.keys(d.mapping || {}).length} 项参数都认出来了（共 ${d.nodes} 个节点），可以直接接入`
    );
  }

  async function handleProbe() {
    const addrs = extraAddr.trim() ? [extraAddr.trim()] : [];
    const d = await call("probe", { addresses: addrs });
    if (!d || !mounted.current) return;
    setFound(d.found || []);
    setTried(d.tried || []);
    if ((d.found || []).length === 1) {
      // 只有一台候选时不必再让人点一次：直接把它跑过的那张图取来看。
      // ★ 为什么默认取「历史里最近那次」而不是让用户导出 JSON：那张图已经在
      //   那台机器上出过片，节点链与模型名都是对的，比手边随便导出的文件更可靠。
      const only = d.found[0].address;
      setPicked(only);
      await runInspect(only, { workflow_path: "" });
      return;
    }
    setNote(
      (d.found || []).length
        ? `探到 ${(d.found || []).length} 台，点一台来用它跑过的那张图`
        : "一台都没探到。逐条原因见上面，别急着重装 ComfyUI"
    );
  }

  async function handleStatus() {
    applyStatus(await call("status", {}));
  }

  async function handleUseOwnFile() {
    const h = window.hermes;
    if (!h || !h.selectFile) { setError("这版应用不能选文件"); return; }
    const p = await h.selectFile({ filters: [{ name: "工作流 JSON", extensions: ["json"] }] });
    if (!p) return;
    setWfPath(p);
    await runInspect(picked, { workflow_path: p });
  }

  async function handleConnect() {
    if (!picked) { setError("先选一台机器"); return; }
    const mapping = { ...(inspect && inspect.mapping ? inspect.mapping : {}) };
    Object.keys(manual).forEach((role) => {
      const m = manual[role] || {};
      if (m.node && m.field) mapping[role] = { node: String(m.node), field: String(m.field) };
    });
    const missing = REQUIRED_ROLES.filter((r) => !mapping[r]);
    if (missing.length) {
      setError(`这几项还没指到节点：${missing.map((r) => ROLE_LABEL[r]).join("、")}`);
      return;
    }
    const d = await call("connect", {
      address: picked,
      ...(wfPath ? { workflow_path: wfPath } : {}),
      mapping,
      set_default: true,
    });
    if (!d || !mounted.current) return;
    applyStatus(d.status);
    setNote("已接入。这台机器上的命令行与外部任务出的片也会一起走本地（这是「设为默认」的含义）");
  }

  async function handleToggleDefault(on) {
    const d = await call("default", { on });
    if (!d || !mounted.current) return;
    applyStatus(d.status);
    setNote(on ? "已把本机设成默认出片厂商" : "本机档案留着，但出片默认回到云端");
  }

  async function handleForget() {
    const d = await call("forget", {});
    if (!d || !mounted.current) return;
    applyStatus(d.status);
    setInspect(null);
    setWfPath("");
    setNote("已断开本机出片服务，出片回到云端 Agnes");
  }

  const fn = api();
  const videoVendor = status ? status.current_video_vendor : "";
  const profile = status ? status.profile : null;
  const connected = !!(profile && profile.address);
  const isDefault = videoVendor === "comfyui";
  const missingRoles = inspect ? inspect.missing || [] : [];

  return (
    <div className="lmc">
      {!fn && <div className="lmc-warn">这版应用没有本地服务接口（重启应用加载新版）</div>}

      <div className="lmc-line">
        <span className="lmc-k">当前出片视频</span>
        <span className="lmc-v">
          {status ? (videoVendor || "没读到") : "还没查（按下面的按钮会顺带启动短剧后台，几秒钟）"}
        </span>
      </div>
      {status && status.source_note ? (
        <div className="lmc-sub">{status.source_note}</div>
      ) : null}

      <div className="lmc-actions">
        <button className="primary settings-inline-btn" onClick={handleProbe} disabled={!!busy}>
          {busy === "probe" ? "正在探测…" : "一键探测"}
        </button>
        <button className="ghost settings-inline-btn" onClick={handleStatus} disabled={!!busy}>
          {busy === "status" ? "读取中…" : "看当前状态"}
        </button>
        {connected && (
          <button className="ghost settings-inline-btn" onClick={() => void handleToggleDefault(!isDefault)} disabled={!!busy}>
            {isDefault ? "这次不当默认" : "设为本机默认"}
          </button>
        )}
        {connected && (
          <button className="ghost danger-text settings-inline-btn" onClick={handleForget} disabled={!!busy}>
            断开并回云端
          </button>
        )}
      </div>

      <div className="lmc-field">
        <input
          className="lmc-input"
          type="text"
          placeholder="另一台机器的地址（如 192.168.1.20:8188，留空只探本机）"
          value={extraAddr}
          onChange={(e) => setExtraAddr(e.target.value)}
        />
      </div>

      {found.length > 0 && (
        <div className="lmc-list">
          {found.map((f) => (
            <button
              key={f.address}
              className={`lmc-item ${picked === f.address ? "active" : ""}`}
              onClick={() => setPicked(f.address)}
            >
              <span className="lmc-item-title">{f.address}</span>
              <span className="lmc-item-desc">
                ComfyUI {f.comfyui_version || "?"} · {deviceLine(f.devices)} ·
                H3 相关节点 {(f.h3_nodes || []).length} 个 · 模型文件 {(f.model_files || []).length} 个
              </span>
              {(f.h3_nodes || []).length === 0 ? (
                <span className="lmc-warn">没认出 H3 的节点，接进来也可能出不了片（先看下面那两列）</span>
              ) : null}
            </button>
          ))}
        </div>
      )}

      {tried.filter((t) => !t.reachable).map((t) => (
        <div className="lmc-dead" key={t.address}>
          <b>{t.address}</b> · {t.reason}
        </div>
      ))}

      {picked && (
        <div className="lmc-section">
          <div className="lmc-line">
            <span className="lmc-k">准备用</span>
            <span className="lmc-v">{picked}</span>
            <button className="ghost settings-inline-btn" onClick={() => runInspect(picked)} disabled={!!busy}>
              {busy === "inspect" ? "正在看…" : "重新看一次"}
            </button>
          </div>
          <div className="lmc-sub">
            图用的是那台机器上「最近跑成功的那一次」，不用你导出文件。
            {wfPath ? <span> 已改用你选的：{wfPath}</span> : (
              <button className="lmc-link" onClick={handleUseOwnFile} disabled={!!busy}>
                换一张（自己选 JSON）
              </button>
            )}
          </div>
        </div>
      )}

      {inspect && (
        <div className="lmc-section">
          <div className="lmc-sub">
            {inspect.nodes} 个节点，来自 {inspect.source}。参数落点：
          </div>
          {Object.keys(inspect.mapping || {}).map((role) => {
            const m = inspect.mapping[role];
            return (
              <div className="lmc-map" key={role}>
                <span className="lmc-k">{ROLE_LABEL[role] || role}</span>
                <span className="lmc-v">
                  节点 {m.node}（{m.class_type}）的 {m.field}
                </span>
              </div>
            );
          })}
          {missingRoles.length > 0 && (
            <div className="lmc-warn">
              这几项认不出来，请在下面填它们在图里的节点号和字段名：
              {missingRoles.map((role) => (
                <div className="lmc-manual" key={role}>
                  <span className="lmc-k">{ROLE_LABEL[role] || role}</span>
                  <input
                    className="lmc-input lmc-input-sm"
                    placeholder="节点号"
                    value={(manual[role] || {}).node || ""}
                    onChange={(e) => setManual({ ...manual, [role]: { ...(manual[role] || {}), node: e.target.value } })}
                  />
                  <input
                    className="lmc-input lmc-input-sm"
                    placeholder="字段名"
                    value={(manual[role] || {}).field || ""}
                    onChange={(e) => setManual({ ...manual, [role]: { ...(manual[role] || {}), field: e.target.value } })}
                  />
                </div>
              ))}
            </div>
          )}
          <div className="lmc-actions">
            <button className="primary settings-inline-btn" onClick={handleConnect} disabled={!!busy}>
              {busy === "connect" ? "正在接入…" : "接入并设为本机默认"}
            </button>
          </div>
        </div>
      )}

      {status && status.notes && status.notes.length > 0 && (
        <ul className="lmc-notes">
          {status.notes.map((n) => (<li key={n}>{n}</li>))}
        </ul>
      )}

      {note ? <div className="lmc-sub">{note}</div> : null}
      {error ? <div className="settings-status-error">{error}</div> : null}
    </div>
  );
}
