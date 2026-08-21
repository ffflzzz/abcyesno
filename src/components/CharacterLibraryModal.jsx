import React, { useEffect, useMemo, useState } from "react";
import { toLoadableSrc } from "../utils/mediaSrc";

// Global character library browser. Opens as a full-screen overlay on top of
// the StudioWorkbench. Lets the user inspect built-in / auto-archived
// characters and apply one to the current project canvas.
//
// Props:
//   open:        boolean
//   onClose:     () => void
//   onApply:     (card) => void   apply the selected card to the current project
//   api:         (action, params) => Promise<{ok, ...}>  studio-call proxy
export default function CharacterLibraryModal({ open, onClose, onApply, api }) {
  const [cards, setCards] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [filter, setFilter] = useState("全部");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!open) return;
    let alive = true;
    (async () => {
      setLoading(true);
      setError("");
      try {
        const j = await (api || window.hermes?.studioCall)(
          "character_library.list",
          {}
        );
        if (!alive) return;
        if (j && j.ok) {
          const list = Array.isArray(j.cards) ? j.cards : [];
          setCards(list);
          setSelectedId((prev) =>
            prev && list.some((c) => c.id === prev)
              ? prev
              : list.length
              ? list[0].id
              : null
          );
        } else {
          setError((j && j.error) || "读取角色库失败");
        }
      } catch (e) {
        if (alive) setError(String((e && e.message) || e));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [open, api]);

  const tags = useMemo(() => {
    const set = new Set(["全部"]);
    cards.forEach((c) => (c.tags || []).forEach((t) => set.add(t)));
    return Array.from(set);
  }, [cards]);

  const filtered = useMemo(() => {
    if (filter === "全部") return cards;
    return cards.filter((c) => (c.tags || []).includes(filter));
  }, [cards, filter]);

  const selected = useMemo(
    () => cards.find((c) => c.id === selectedId) || null,
    [cards, selectedId]
  );

  if (!open) return null;

  async function handleApply() {
    if (!selected) return;
    try {
      await (api || window.hermes?.studioCall)("character_library.touch_used", {
        id: selected.id,
      });
    } catch (_) {
      /* non-fatal */
    }
    onApply && onApply(selected);
  }

  return (
    <aside className="char-lib-modal" onMouseDown={(e) => e.stopPropagation()}>
      <div className="clm-inner">
        <header className="clm-head">
          <div className="clm-title">📚 角色库</div>
          <button className="clm-close" onClick={onClose} aria-label="关闭">
            ✕
          </button>
        </header>

        <div className="clm-body">
          {/* Left: card grid */}
          <section className="clm-grid-pane">
            <div className="clm-filters">
              {tags.map((t) => (
                <button
                  key={t}
                  className={`clm-filter ${filter === t ? "active" : ""}`}
                  onClick={() => setFilter(t)}
                >
                  {t}
                </button>
              ))}
            </div>
            {loading && <div className="clm-loading">读取中…</div>}
            {error && <div className="clm-error">{error}</div>}
            {!loading && !error && (
              <div className="clm-grid">
                {filtered.map((c) => (
                  <button
                    key={c.id}
                    className={`clm-card ${selectedId === c.id ? "active" : ""}`}
                    onClick={() => setSelectedId(c.id)}
                  >
                    <div className="clm-card-img">
                      {c.frontUrl ? (
                        <img src={toLoadableSrc(c.frontUrl)} alt={c.name} />
                      ) : (
                        <div className="clm-card-ph" />
                      )}
                    </div>
                    <div className="clm-card-name">{c.name}</div>
                    <div className="clm-card-tags">
                      {(c.tags || []).slice(0, 3).join(" · ")}
                    </div>
                    {c.source === "builtin" && (
                      <span className="clm-badge">内置</span>
                    )}
                  </button>
                ))}
                {filtered.length === 0 && (
                  <div className="clm-empty">该标签下暂无角色</div>
                )}
              </div>
            )}
          </section>

          {/* Right: detail */}
          <section className="clm-detail-pane">
            {selected ? (
              <>
                <div className="clm-detail-head">
                  <h3>{selected.name}</h3>
                  <div className="clm-detail-tags">
                    {(selected.tags || []).map((t) => (
                      <span key={t} className="clm-tag">
                        {t}
                      </span>
                    ))}
                  </div>
                  <div className="clm-detail-meta">
                    <span>
                      {selected.source === "builtin"
                        ? "内置资产"
                        : `来源：${selected.source || "未知"}`}
                    </span>
                    {selected.useCount > 0 && (
                      <span>使用 {selected.useCount} 次</span>
                    )}
                  </div>
                </div>

                <div className="clm-detail-img">
                  {selected.frontUrl ? (
                    <img src={toLoadableSrc(selected.frontUrl)} alt={selected.name} />
                  ) : (
                    <div className="clm-card-ph">无预览图</div>
                  )}
                </div>

                {selected.views && Object.keys(selected.views).length > 1 && (
                  <div className="clm-views">
                    {Object.entries(selected.views).map(([vlabel, vsrc]) => (
                      <div className="clm-view" key={vlabel}>
                        <img src={toLoadableSrc(vsrc)} alt={vlabel} />
                        <span>{vlabel}</span>
                      </div>
                    ))}
                  </div>
                )}

                {selected.prompt && (
                  <div className="clm-prompt">
                    <div className="clm-prompt-label">角色提示词</div>
                    <div className="clm-prompt-text">{selected.prompt}</div>
                  </div>
                )}
              </>
            ) : (
              <div className="clm-empty">选择左侧角色查看详情</div>
            )}
          </section>
        </div>

        <footer className="clm-foot">
          <span className="clm-count">共 {filtered.length} 个角色</span>
          <button
            className="clm-apply"
            disabled={!selected}
            onClick={handleApply}
          >
            + 应用至画布
          </button>
        </footer>
      </div>
    </aside>
  );
}
