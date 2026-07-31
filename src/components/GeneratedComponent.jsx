import React from "react";
import TableBlock from "./ui/TableBlock.jsx";
import FlowchartBlock from "./ui/FlowchartBlock.jsx";
import CardBlock from "./ui/CardBlock.jsx";
import ProgressBlock from "./ui/ProgressBlock.jsx";
import ActionBlock from "./ui/ActionBlock.jsx";

/**
 * GeneratedComponent — Agent 自渲染 UI 组件路由 (spec §4.1)
 * 按 block.type 白名单映射到具体组件；未知 type 静默不渲染（§6.1）。
 */
const REGISTRY = {
  table: TableBlock,
  flowchart: FlowchartBlock,
  card: CardBlock,
  progress: ProgressBlock,
  action: ActionBlock,
};

export default function GeneratedComponent({ block }) {
  if (!block || !block.type) return null;
  const Cmp = REGISTRY[block.type];
  if (!Cmp) return null;
  return (
    <div className="ui-block-wrap" data-block-id={block.blockId}>
      <Cmp {...block.props} blockId={block.blockId} />
    </div>
  );
}
