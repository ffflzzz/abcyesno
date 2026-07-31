import { useRef, useState, useCallback, useEffect, useMemo } from "react";

/**
 * Row gap in px. MUST stay in sync with `.vs-content { gap: 16px }` in
 * src/styles/index.css. `.vs-content` is `display:flex; flex-direction:column`,
 * so every flex child (virtual rows + the two spacer divs) is separated by
 * this gap. getBoundingClientRect().height (used by measureRow) does NOT
 * include it, which is exactly the mismatch this file compensates for.
 */
const ROW_GAP = 16;

/**
 * Self-built virtual scroll hook for variable-height message lists.
 * See docs/VIRTUAL_SCROLL_SPEC.md for design rationale.
 *
 * Core idea: only render visible rows (+ overscan), use top/bottom spacer
 * divs to preserve total scroll height. No flex-chain height dependency —
 * the container just needs `overflow-y: auto` and a definite height from CSS.
 *
 * @param {Object} opts
 * @param {Array}  opts.rows            - Row objects (type/message/tools/thinking)
 * @param {Function} opts.estimatedHeight - (row, index) => px estimate
 * @param {number} opts.overscanTop      - Extra rows above viewport (default 5)
 * @param {number} opts.overscanBottom   - Extra rows below viewport (default 3)
 * @param {number} opts.atBottomThreshold - px distance to consider "at bottom" (default 200)
 */
export default function useVirtualRows({
  rows,
  estimatedHeight: estimatedHeightFn,
  overscanTop = 5,
  overscanBottom = 3,
  atBottomThreshold = 200,
}) {
  const containerRef = useRef(null);
  const heightMapRef = useRef(new Map());       // index -> measured px
  const scrollTopRef = useRef(0);
  const viewportHeightRef = useRef(0);
  const rafIdRef = useRef(0);
  const prevRowCountRef = useRef(0);
  const rowObserverRef = useRef(null);          // shared ResizeObserver for rows
  const measuredElsRef = useRef(new Map());     // element -> row index
  // FIX (followOutput): true once the user scrolls up to read history.
  // Auto-follow is suspended until the user returns to the bottom.
  const userScrolledUpRef = useRef(false);

  // ── Reactive state ────────────────────────────────────────────────
  const [scrollState, setScrollState] = useState({
    scrollTop: 0,
    viewportHeight: 0,
    heightVersion: 0,   // bump when heightMap changes to trigger recalc
  });
  const [atBottom, setAtBottom] = useState(true);

  // ── Default estimated height by row type ──────────────────────────
  const getEstimatedHeight = useCallback(
    (row, index) => {
      if (estimatedHeightFn) return estimatedHeightFn(row, index);
      if (row.type === "tools") return 120;
      if (row.type === "thinking") return 48;
      return 80; // message
    },
    [estimatedHeightFn]
  );

  // ── Offsets (prefix-sum of heights) ───────────────────────────────
  const offsets = useMemo(() => {
    const arr = new Array(rows.length + 1);
    arr[0] = 0;
    for (let i = 0; i < rows.length; i++) {
      const h = heightMapRef.current.get(i) ?? getEstimatedHeight(rows[i], i);
      // FIX (gap misalignment): each row is followed by a flex `gap` of
      // ROW_GAP in `.vs-content`, but measureRow() returns the bare
      // getBoundingClientRect().height which excludes that gap. Adding
      // ROW_GAP to every stride makes the virtual-scroll range match the
      // real rendered layout. Without this, `offsets` was short by
      // (n-1)*ROW_GAP px, so scrolling up to history was mis-anchored /
      // could not reach the top. The trailing gap is absorbed by the
      // bottomSpacer subtraction below.
      arr[i + 1] = arr[i] + h + ROW_GAP;
    }
    return arr;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, scrollState.heightVersion, getEstimatedHeight]);

  const totalHeight = offsets[rows.length] || 0;

  // ── Binary search: first row whose bottom > scrollTop ─────────────
  function findStart(st) {
    let lo = 0, hi = rows.length - 1, ans = 0;
    while (lo <= hi) {
      const mid = (lo + hi) >>> 1;
      if (offsets[mid + 1] > st) { ans = mid; hi = mid - 1; }
      else lo = mid + 1;
    }
    return ans;
  }

  // ── Binary search: first row whose top > scrollTop + viewportHeight ──
  function findEnd(st, vh) {
    const limit = st + vh;
    let lo = 0, hi = rows.length - 1, ans = rows.length - 1;
    while (lo <= hi) {
      const mid = (lo + hi) >>> 1;
      if (offsets[mid] > limit) { ans = mid - 1; hi = mid - 1; }
      else lo = mid + 1;
    }
    return ans;
  }

  // ── Compute visible range with overscan ───────────────────────────
  const { startIndex, endIndex, topSpacer, bottomSpacer } = useMemo(() => {
    const { scrollTop, viewportHeight } = scrollState;
    if (rows.length === 0 || viewportHeight === 0) {
      // Not yet measured — render all rows so ResizeObserver can measure them
      return {
        startIndex: 0,
        endIndex: rows.length - 1,
        topSpacer: 0,
        bottomSpacer: 0,
      };
    }
    let s = findStart(scrollTop);
    let e = findEnd(scrollTop, viewportHeight);
    s = Math.max(0, s - overscanTop);
    e = Math.min(rows.length - 1, e + overscanBottom);
    return {
      startIndex: s,
      endIndex: e,
      topSpacer: offsets[s] || 0,
      // FIX (gap misalignment): offsets now include a trailing ROW_GAP after
      // every row, but the real DOM only needs one gap between the last
      // visible row and the bottom spacer. Subtract it so the total rendered
      // height stays self-consistent (== totalHeight) under virtualization.
      bottomSpacer: Math.max(0, (totalHeight - (offsets[e + 1] || 0)) - ROW_GAP),
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows.length, scrollState, offsets, totalHeight, overscanTop, overscanBottom]);

  // ── Virtual items for render ──────────────────────────────────────
  const virtualItems = useMemo(() => {
    const items = [];
    for (let i = startIndex; i <= endIndex && i < rows.length; i++) {
      items.push({ index: i, row: rows[i], offsetTop: offsets[i] });
    }
    return items;
  }, [startIndex, endIndex, rows, offsets]);

  // ── atBottom check ────────────────────────────────────────────────
  const checkAtBottom = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    const { scrollTop, scrollHeight, clientHeight } = el;
    const bottom = scrollHeight - scrollTop - clientHeight < atBottomThreshold;
    setAtBottom(bottom);
  }, [atBottomThreshold]);

  // ── Scroll handler (rAF throttled) ────────────────────────────────
  const onScroll = useCallback(() => {
    if (rafIdRef.current) return;
    rafIdRef.current = requestAnimationFrame(() => {
      rafIdRef.current = 0;
      const el = containerRef.current;
      if (!el) return;
      scrollTopRef.current = el.scrollTop;
      viewportHeightRef.current = el.clientHeight;
      setScrollState((prev) => ({
        ...prev,
        scrollTop: el.scrollTop,
        viewportHeight: el.clientHeight,
      }));
      // FIX (followOutput): whenever the user is not at the bottom, they are
      // reading history — suspend auto-follow. The guard is cleared again by
      // scrollToBottom() once they return to the bottom.
      const bottom =
        el.scrollHeight - el.scrollTop - el.clientHeight < atBottomThreshold;
      userScrolledUpRef.current = !bottom;
      checkAtBottom();
    });
  }, [checkAtBottom]);

  // ── scrollToBottom ────────────────────────────────────────────────
  const scrollToBottom = useCallback(
    (smooth) => {
      const el = containerRef.current;
      if (!el) return;
      // FIX (followOutput): reaching the bottom clears the "user scrolled up"
      // guard, so auto-follow resumes. Triggered both by auto-follow and by
      // the user clicking the "回到底部" button.
      userScrolledUpRef.current = false;
      el.scrollTo({
        top: el.scrollHeight,
        behavior: smooth ? "smooth" : "auto",
      });
      setAtBottom(true);
    },
    []
  );

  // ── measureRow: called via ref callback on each row wrapper ───────
  const measureRow = useCallback(
    (index, el) => {
      if (!el) {
        // Unmount: remove from observer
        if (rowObserverRef.current && measuredElsRef.current) {
          for (const [elem, idx] of measuredElsRef.current) {
            if (idx === index) {
              rowObserverRef.current.unobserve(elem);
              measuredElsRef.current.delete(elem);
              break;
            }
          }
        }
        return;
      }
      // Observe for size changes (shared observer)
      if (rowObserverRef.current) {
        measuredElsRef.current.set(el, index);
        rowObserverRef.current.observe(el);
      }
      // Immediate measure
      const h = el.getBoundingClientRect().height;
      if (h > 0 && heightMapRef.current.get(index) !== h) {
        heightMapRef.current.set(index, h);
        // Bump version to recalculate offsets (batch via rAF)
        if (!rafIdRef.current) {
          rafIdRef.current = requestAnimationFrame(() => {
            rafIdRef.current = 0;
            setScrollState((prev) => ({ ...prev, heightVersion: prev.heightVersion + 1 }));
          });
        }
      }
    },
    []
  );

  // ── Initialize shared ResizeObserver ──────────────────────────────
  useEffect(() => {
    if (typeof ResizeObserver === "undefined") return;
    rowObserverRef.current = new ResizeObserver((entries) => {
      let changed = false;
      for (const entry of entries) {
        const idx = measuredElsRef.current.get(entry.target);
        if (idx === undefined) continue;
        const h = entry.borderBoxSize?.[0]?.blockSize ?? entry.contentRect.height;
        if (h > 0 && heightMapRef.current.get(idx) !== h) {
          heightMapRef.current.set(idx, h);
          changed = true;
        }
      }
      if (changed) {
        setScrollState((prev) => ({ ...prev, heightVersion: prev.heightVersion + 1 }));
      }
    });
    return () => {
      rowObserverRef.current?.disconnect();
      rowObserverRef.current = null;
      measuredElsRef.current.clear();
    };
  }, []);

  // ── Container ResizeObserver (viewport height) ───────────────────
  useEffect(() => {
    const el = containerRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      const vh = el.clientHeight;
      viewportHeightRef.current = vh;
      setScrollState((prev) => ({
        ...prev,
        viewportHeight: vh,
        scrollTop: el.scrollTop,
      }));
    });
    ro.observe(el);
    // Initial measure
    const vh = el.clientHeight;
    viewportHeightRef.current = vh;
    setScrollState((prev) => ({ ...prev, viewportHeight: vh, scrollTop: el.scrollTop }));
    return () => ro.disconnect();
  }, []);

  // ── followOutput: auto-scroll to bottom when new rows arrive ──────
  useEffect(() => {
    // FIX (followOutput): only auto-follow when the user is at the bottom AND
    // has not scrolled up to read history. This stops streaming output from
    // yanking the viewport back down while the user is scrolling through old
    // messages. atBottomThreshold itself is unchanged.
    if (atBottom && !userScrolledUpRef.current) {
      scrollToBottom(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows.length, scrollState.heightVersion]);

  // ── Reset when rows identity changes (session switch) ─────────────
  useEffect(() => {
    if (rows.length !== prevRowCountRef.current) {
      // Row count changed — could be new session or new messages
      if (rows.length < prevRowCountRef.current) {
        // Session switch / clear — reset measurements
        heightMapRef.current.clear();
        // FIX (followOutput): a fresh session must auto-follow from the top.
        userScrolledUpRef.current = false;
        setScrollState((prev) => ({
          ...prev,
          heightVersion: prev.heightVersion + 1,
          scrollTop: 0,
        }));
        setAtBottom(true);
        // Scroll to bottom after render
        requestAnimationFrame(() => scrollToBottom(false));
      }
      prevRowCountRef.current = rows.length;
    }
  }, [rows.length, scrollToBottom]);

  // ── Viewport anchor: adjust scrollTop when rows above viewport change height ──
  useEffect(() => {
    // This is handled implicitly: when heightMap updates, offsets recalc,
    // and the browser's native scroll anchoring keeps the viewport stable
    // for content above the visible area. No manual adjustment needed
    // because we use normal document flow (not absolute positioning).
  }, [scrollState.heightVersion]);

  // ── Cleanup rAF on unmount ────────────────────────────────────────
  useEffect(() => {
    return () => {
      if (rafIdRef.current) cancelAnimationFrame(rafIdRef.current);
    };
  }, []);

  return {
    containerRef,
    virtualItems,
    topSpacer,
    bottomSpacer,
    atBottom,
    scrollToBottom,
    measureRow,
    onScroll, // attach to container's onScroll event
  };
}
