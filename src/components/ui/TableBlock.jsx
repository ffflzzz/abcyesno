import React from "react";

/**
 * TableBlock — 结构化表格 (spec §5.1)
 * props: { columns: string[], rows: (string|{text,bold,align})[][], caption?, highlightRow? }
 */
const MAX_ROWS = 50;

function renderCell(cell) {
  if (cell == null) return "";
  if (typeof cell === "object") {
    const style = {};
    if (cell.bold) style.fontWeight = 600;
    if (cell.align) style.textAlign = cell.align;
    return <td style={style}>{cell.text ?? ""}</td>;
  }
  return <td>{String(cell)}</td>;
}

export default function TableBlock({ columns = [], rows = [], caption, highlightRow }) {
  const safeColumns = Array.isArray(columns) ? columns.slice(0, 24) : [];
  const safeRows = Array.isArray(rows) ? rows.slice(0, MAX_ROWS) : [];
  if (safeColumns.length === 0 && safeRows.length === 0) return null;

  return (
    <div className="ui-block ui-table">
      {caption && <div className="ui-block-caption">{caption}</div>}
      <div className="ui-table-scroll">
        <table>
          {safeColumns.length > 0 && (
            <thead>
              <tr>
                {safeColumns.map((c, i) => (
                  <th key={i}>{typeof c === "string" ? c : String(c?.text ?? c)}</th>
                ))}
              </tr>
            </thead>
          )}
          <tbody>
            {safeRows.map((row, ri) => (
              <tr
                key={ri}
                className={highlightRow === ri ? "ui-row-highlight" : ""}
              >
                {Array.isArray(row)
                  ? row.map((cell, ci) => <React.Fragment key={ci}>{renderCell(cell)}</React.Fragment>)
                  : renderCell(row)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
