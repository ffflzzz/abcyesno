import React, { useEffect, useRef, useState } from "react";
import Icon from "./Icon.jsx";

// Browser-style tab strip. Each tab is a pill (icon + label + close ×); a +
// button at the end opens a fresh homepage tab. Tabs with duplicate titles get
// a numeric suffix (对话 1 / 对话 2) so they stay distinguishable — exactly
// like a browser where you can have several tabs on the same site open.
//
// DnD is implemented with Pointer Events (NOT HTML5 `draggable`), because
// HTML5 dragstart preempts the OS window-drag region on the topbar (so
// clicking a tab to move the whole window stopped working once tabs were
// made draggable). Pointer events stay out of the OS's way: a click without
// movement is a click; a movement past 5px kicks off the drag handler
// (reorder / tear-off).
export default function TabBar({ tabs, activeTabId, onActivate, onClose, onAdd, onReorder, onTearOff }) {
  const totals = {};
  for (const t of tabs) {
    const base = t.title || "标签";
    totals[base] = (totals[base] || 0) + 1;
  }

  let seen = {};
  // draggingIndex: which tab is currently being dragged (state so React
  // re-renders the .dragging className); overIndex/overSide: drop marker
  // position. All other DnD bookkeeping (pointer id, start coords, etc.)
  // lives in refs so the pointermove handler doesn't trigger renders.
  const [draggingIndex, setDraggingIndex] = useState(null);
  const [dropMarker, setDropMarker] = useState({ index: null, side: null });
  const dragStateRef = useRef(null); // { pointerId, startX, startY, started, tab, overIndex, overSide }

  // Cleanup any in-flight drag listeners when the component unmounts mid-drag.
  useEffect(() => {
    return () => {
      const s = dragStateRef.current;
      if (!s) return;
      document.removeEventListener("pointermove", s.onMove);
      document.removeEventListener("pointerup", s.onUp);
      document.removeEventListener("pointercancel", s.onCancel);
    };
  }, []);

  function computeOver(x, y) {
    // Hit-test against the rendered tab elements to find which tab the
    // pointer is over, then split by clientX into "before" / "after".
    const els = document.querySelectorAll(".tabbar-tab");
    let overIndex = null;
    let overSide = null;
    for (let i = 0; i < els.length; i++) {
      const r = els[i].getBoundingClientRect();
      if (x >= r.left && x <= r.right && y >= r.top && y <= r.bottom) {
        overIndex = i;
        overSide = x < r.left + r.width / 2 ? "before" : "after";
        break;
      }
    }
    return { index: overIndex, side: overSide };
  }

  function handlePointerDown(e, index) {
    if (e.button !== 0) return; // left mouse only
    if (e.target.closest(".tabbar-tab-close")) return; // don't hijack the × button
    const startX = e.clientX;
    const startY = e.clientY;
    const tab = tabs[index];

    const state = {
      pointerId: e.pointerId,
      startX,
      startY,
      started: false,
      tab,
      overIndex: null,
      overSide: null,
      onMove: null,
      onUp: null,
      onCancel: null,
    };

    const onMove = (ev) => {
      const dx = ev.clientX - state.startX;
      const dy = ev.clientY - state.startY;
      if (!state.started) {
        if (Math.hypot(dx, dy) < 5) return;
        state.started = true;
        setDraggingIndex(index);
      }
      const over = computeOver(ev.clientX, ev.clientY);
      if (over.index !== state.overIndex || over.side !== state.overSide) {
        state.overIndex = over.index;
        state.overSide = over.side;
        setDropMarker(over);
      }
    };

    const finish = (ev, cancelled) => {
      document.removeEventListener("pointermove", state.onMove);
      document.removeEventListener("pointerup", state.onUp);
      document.removeEventListener("pointercancel", state.onCancel);
      dragStateRef.current = null;
      if (!state.started || cancelled) {
        // No drag started — let the normal onClick fire for tab activation.
        setDraggingIndex((d) => (d === index ? null : d));
        return;
      }
      setDraggingIndex(null);
      setDropMarker({ index: null, side: null });
      const topbarEl = document.querySelector(".topbar");
      const threshold = topbarEl
        ? topbarEl.getBoundingClientRect().bottom + 60
        : 120;
      if (ev.clientY > threshold) {
        if (state.tab && typeof onTearOff === "function") onTearOff(state.tab);
        return;
      }
      if (typeof onReorder !== "function") return;
      if (state.overIndex === null || state.overIndex === undefined) return;
      const from = index;
      let to = state.overIndex + (state.overSide === "after" ? 1 : 0);
      if (to > from + 1) to -= 1;
      if (to === from) return;
      onReorder(from, to);
    };

    const onUp = (ev) => finish(ev, false);
    const onCancel = (ev) => finish(ev, true);
    state.onMove = onMove;
    state.onUp = onUp;
    state.onCancel = onCancel;
    dragStateRef.current = state;

    document.addEventListener("pointermove", onMove);
    document.addEventListener("pointerup", onUp);
    document.addEventListener("pointercancel", onCancel);
  }

  return (
    <div className="tabbar">
      <div className="tabbar-tabs">
        {tabs.map((tab, index) => {
          const base = tab.title || "标签";
          seen[base] = (seen[base] || 0) + 1;
          // Homepage tabs are always just "启动台"; other duplicate titles
          // (e.g. several 对话 tabs) keep a numeric suffix to stay distinct.
          const label = tab.type === "homepage" || totals[base] <= 1 ? base : `${base} ${seen[base]}`;
          const active = tab.id === activeTabId;
          const showBeforeMarker = dropMarker.index === index && dropMarker.side === "before";
          const showAfterMarker = dropMarker.index === index && dropMarker.side === "after";
          return (
            <div
              key={tab.id}
              className={`tabbar-tab${active ? " active" : ""}${draggingIndex === index ? " dragging" : ""}`}
              onPointerDown={(e) => handlePointerDown(e, index)}
              onClick={() => onActivate(tab.id)}
              title={base}
            >
              {showBeforeMarker && <span className="tabbar-drop-marker tabbar-drop-marker----" />}
              <span className="tabbar-tab-icon">
                {tab.iconSrc ? (
                  <img src={tab.iconSrc} alt="" draggable={false} style={{ width: 15, height: 15, objectFit: "contain", borderRadius: 3 }} />
                ) : (
                  <Icon name={tab.icon || "home"} size={15} />
                )}
              </span>
              <span className="tabbar-tab-label">{label}</span>
              {tabs.length > 1 && (
                <button
                  className="tabbar-tab-close"
                  onClick={(e) => { e.stopPropagation(); onClose(tab.id); }}
                  title="关闭标签"
                >
                  <Icon name="x" size={13} />
                </button>
              )}
              {showAfterMarker && <span className="tabbar-drop-marker tabbar-drop-marker--after" />}
            </div>
          );
        })}
        <button className="tabbar-add" onClick={onAdd} title="新建标签页">
          <Icon name="plus" size={16} />
        </button>
      </div>
    </div>
  );
}