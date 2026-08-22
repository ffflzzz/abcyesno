import React, { useEffect, useMemo, useState, useRef, useCallback } from "react";
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
  // Per-card generation state: id -> "running" | error string.
  const [genState, setGenState] = useState({});
  // Guards the auto-backfill pass so re-opening / effect retries don't double-run.
  const backfillRunningRef = useRef(false);

  // A card still shows placeholder art when it has no frontUrl at all or only
  // one of the legacy SVG gradient placeholders from the original seed.
  const needsGen = (c) =>
    !c.frontUrl || String(c.frontUrl).startsWith("data:image/svg");

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

  // Replace one card in place after its image was generated.
  function applyGeneratedCard(card) {
    if (!card || !card.id) return;
    setCards((prev) => prev.map((c) => (c.id === card.id ? card : c)));
    setGenState((prev) => {
      const next = { ...prev };
      delete next[card.id];
      return next;
    });
  }

  const generateOne = useCallback(
    async (card) => {
      if (!card || !needsGen(card)) return null;
      setGenState((prev) => ({ ...prev, [card.id]: "running" }));
      try {
        const j = await (api || window.hermes?.studioCall)(
          "character_library.generate",
          { id: card.id }
        );
        if (j && j.ok && j.card) {
          applyGeneratedCard(j.card);
          return j.card;
        }
        throw new Error((j && j.error) || "生成失败");
      } catch (e) {
        setGenState((prev) => ({
          ...prev,
          [card.id]: String((e && e.message) || e),
        }));
        return null;
      }
    },
    [api]
  );

  const pendingCards = useMemo(() => cards.filter(needsGen), [cards]);
  const runningCount = useMemo(
    () => Object.values(genState).filter((s) => s === "running").length,
    [genState]
  );

  // Auto-backfill: the first time the library opens with placeholder cards,
  // generate their real artwork sequentially (each success persists to disk,
  // so this is a one-time cost per character).
  useEffect(() => {
    if (!open || loading || backfillRunningRef.current) return;
    if (pendingCards.length === 0) return;
    backfillRunningRef.current = true;
    (async () => {
      for (const c of pendingCards) {
        await generateOne(c);
      }
      backfillRunningRef.current = false;
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, loading]);

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
                {filtered.map((c) => {
                  const st = genState[c.id];
                  const missing = needsGen(c);
                  return (
                    <button
                      key={c.id}
                      className={`clm-card ${selectedId === c.id ? "active" : ""}`}
                      onClick={() => setSelectedId(c.id)}
                    >
                      <div className="clm-card-img">
                        {c.frontUrl && !missing ? (
                          <img src={toLoadableSrc(c.frontUrl)} alt={c.name} />
                        ) : (
                          <div className="clm-card-ph">
                            {st === "running" ? (
                              <span className="clm-gen-spin" aria-label="生成中" />
                            ) : (
                              <span className="clm-gen-hint">待生成</span>
                            )}
                          </div>
                        )}
                      </div>
                      <div className="clm-card-name">{c.name}</div>
                      <div className="clm-card-tags">
                        {(c.tags || []).slice(0, 3).join(" · ")}
                      </div>
                      {c.source === "builtin" && (
                        <span className="clm-badge">内置</span>
                      )}
                      {st === "running" && (
                        <span className="clm-badge clm-badge-gen">生成中</span>
                      )}
                    </button>
                  );
                })}
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
                  {selected.frontUrl && !needsGen(selected) ? (
                    <img src={toLoadableSrc(selected.frontUrl)} alt={selected.name} />
                  ) : (
                    <div className="clm-card-ph clm-detail-ph">
                      {genState[selected.id] === "running" ? (
                        <>
                          <span className="clm-gen-spin clm-gen-spin-lg" aria-label="生成中" />
                          <span className="clm-gen-hint">正在生成立绘…</span>
                        </>
                      ) : (
                        <>
                          <span className="clm-gen-hint">暂无真实立绘</span>
                          <button
                            className="clm-gen-btn"
                            disabled={runningCount > 0}
                            onClick={() => generateOne(selected)}
                          >
                            ⚡ 生成立绘
                          </button>
                          {typeof genState[selected.id] === "string" &&
                            genState[selected.id] !== "running" && (
                              <span className="clm-gen-err">
                                {genState[selected.id]}
                              </span>
                            )}
                        </>
                      )}
                    </div>
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
          {pendingCards.length > 0 && (
            <button
              className="clm-gen-all"
              disabled={runningCount > 0}
              onClick={async () => {
                for (const c of pendingCards) {
                  await generateOne(c);
                }
              }}
            >
              {runningCount > 0
                ? `生成中…（剩余 ${pendingCards.length} 张）`
                : `⚡ 补全缺失立绘（${pendingCards.length}）`}
            </button>
          )}
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
