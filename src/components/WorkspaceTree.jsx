import React, { useState, useMemo } from "react";
import Icon from "./Icon.jsx";

// WorkspaceTree — §5.1 file tree renderer.
// `tree` is the JSON returned by window.hermes.listWorkspace({root,path}):
// { name, path, type: 'dir'|'file', children?[], mtime?, size? }.
// `onOpenFile(node)` fires when a file row is clicked. `filter` narrows by name.

function matchTree(nodes, q) {
  const out = [];
  for (const n of nodes || []) {
    if (n.type === "dir") {
      const sub = matchTree(n.children || [], q);
      if (n.name.toLowerCase().includes(q) || sub.length > 0) {
        out.push({ ...n, children: sub.length > 0 ? sub : n.name.toLowerCase().includes(q) ? (n.children || []) : [] });
      }
    } else if (n.name.toLowerCase().includes(q)) {
      out.push(n);
    }
  }
  return out;
}

function TreeNode({ node, depth, onOpenFile, defaultOpen }) {
  const isDir = node.type === "dir";
  const [open, setOpen] = useState(depth < 1 ? defaultOpen : false);
  return (
    <div className="ws-node">
      <div
        className={`ws-row ${isDir ? "ws-dir" : "ws-file"}`}
        style={{ paddingLeft: 8 + depth * 14 }}
        onClick={() => (isDir ? setOpen((o) => !o) : onOpenFile(node))}
        title={node.path}
      >
        <span className="ws-icon"><Icon name={isDir ? (open ? "folder-open" : "folder") : "file"} size={14} /></span>
        <span className="ws-name">{node.name}</span>
        {!isDir && node.size != null && (
          <span className="ws-size">{node.size > 1024 ? `${(node.size / 1024).toFixed(1)}K` : `${node.size}B`}</span>
        )}
      </div>
      {isDir && open && node.children && node.children.length > 0 && (
        <div className="ws-children">
          {node.children.map((c) => (
            <TreeNode key={c.path} node={c} depth={depth + 1} onOpenFile={onOpenFile} defaultOpen={defaultOpen} />
          ))}
        </div>
      )}
    </div>
  );
}

export default function WorkspaceTree({ tree, onOpenFile, filter = "" }) {
  const q = filter.trim().toLowerCase();
  const nodes = useMemo(() => {
    if (!tree || !tree.children) return [];
    return q ? matchTree(tree.children, q) : tree.children;
  }, [tree, q]);

  if (!tree) return <div className="ws-empty">加载中…</div>;
  if (nodes.length === 0) return <div className="ws-empty">{q ? "无匹配文件" : "空目录"}</div>;

  return (
    <div className="ws-tree">
      {nodes.map((n) => (
        <TreeNode key={n.path} node={n} depth={0} onOpenFile={onOpenFile} defaultOpen={!q} />
      ))}
    </div>
  );
}
