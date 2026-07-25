import React, { useMemo } from "react";

// ChangeDiff — §6 line-level diff view.
// Lightweight LCS-based line diff (spec §6.2 option B: no heavy editor dep,
// portable-friendly). `oldText`/`newText` are plain strings; rows are rendered
// with added / removed / unchanged coloring.

function diffLines(oldText, newText) {
  const a = (oldText || "").split("\n");
  const b = (newText || "").split("\n");
  const m = a.length;
  const n = b.length;
  // Cap to keep the O(m*n) table bounded for very large files.
  const CAP = 4000;
  if (m > CAP || n > CAP) {
    return [
      ...a.map((t, i) => ({ type: "del", text: t })),
      ...b.map((t, i) => ({ type: "add", text: t })),
    ];
  }
  const dp = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = m - 1; i >= 0; i--) {
    for (let j = n - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  const rows = [];
  let i = 0;
  let j = 0;
  while (i < m && j < n) {
    if (a[i] === b[j]) {
      rows.push({ type: "same", text: a[i] });
      i++;
      j++;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      rows.push({ type: "del", text: a[i] });
      i++;
    } else {
      rows.push({ type: "add", text: b[j] });
      j++;
    }
  }
  while (i < m) rows.push({ type: "del", text: a[i++] });
  while (j < n) rows.push({ type: "add", text: b[j++] });
  return rows;
}

export default function ChangeDiff({ oldText, newText }) {
  const rows = useMemo(() => diffLines(oldText, newText), [oldText, newText]);
  let add = 0;
  let del = 0;
  for (const r of rows) {
    if (r.type === "add") add++;
    else if (r.type === "del") del++;
  }
  if (rows.length === 0) return <div className="diff-empty">无差异</div>;
  return (
    <div className="diff-view">
      <div className="diff-summary">
        <span className="diff-add">+{add}</span>
        <span className="diff-del">-{del}</span>
      </div>
      <div className="diff-lines">
        {rows.map((r, idx) => (
          <div key={idx} className={`diff-row diff-${r.type}`}>
            <span className="diff-gutter">{r.type === "add" ? "+" : r.type === "del" ? "-" : " "}</span>
            <span className="diff-text">{r.text || " "}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
