#!/usr/bin/env python3
"""
render_ui Tool — Agent 自渲染 UI 组件能力 (spec: AGENT_UI_RENDER_SPEC.md)

让普通对话中的 agent 能主动声明要在回复里插入的结构化 UI 组件
（table / flowchart / card / progress / action）。

机制：
  1. 模型调用 render_ui(blockId, type, props) 工具
  2. 本工具读取 agui-server 写入的 .ui_active.json 拿到当前 runId + 端口
  3. 通过 HTTP 桥 POST 到 agui-server 的 /api/ag-ui/ui-event
  4. agui-server 中继为 AG-UI CUSTOM{name:"ui.render"} 注入当前对话轮 SSE 流
  5. 前端 useAgentStream.handleCustom("ui.render") 推入 uiBlocks[] 并渲染

前端白名单（useAgentStream.UI_BLOCK_TYPES）是最终安全闸门；这里再做一次
输入校验，作为纵深防御。实际渲染走事件通道，绝不在 tool result 里塞 HTML。
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

# 与前端白名单保持同步（spec §6.1）
UI_BLOCK_TYPES = {"table", "flowchart", "card", "progress", "action"}
# 与前端 BLOCK_ID_RE 同步（spec §6.5）
import re
_BLOCK_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# props 硬上限，防止超大 payload（spec §6.4）
_MAX_PROPS_CHARS = 200_000


def _ui_active_coord() -> Optional[Dict[str, str]]:
    """读取 agui-server 在对话轮开始时写入的协调文件，拿到当前 runId / 端口。"""
    home = os.environ.get("HERMES_HOME")
    if not home:
        return None
    coord = Path(home) / "workflow_hitl" / ".ui_active.json"
    try:
        if coord.exists():
            return json.loads(coord.read_text(encoding="utf-8"))
    except Exception:
        return None
    return None


def _post_ui_event(run_id: str, payload: Dict[str, Any]) -> bool:
    """把 ui.render 事件经 HTTP 桥推给 agui-server。best-effort，不阻塞模型。"""
    import urllib.request

    port = os.environ.get("AGUI_PORT") or "9121"
    url = f"http://127.0.0.1:{port}/api/ag-ui/ui-event"
    try:
        body = json.dumps(
            {"runId": run_id, "payload": payload}
        ).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("status") in ("ok", "dropped")
    except Exception as exc:  # pragma: no cover - best-effort
        logger.warning("render_ui emit failed: %s", exc)
        return False


def render_ui(
    blockId: str,
    type: str,
    props: Optional[Dict[str, Any]] = None,
    replace: bool = False,
    appendPreview: bool = False,
) -> str:
    """声明在对话流中渲染一个结构化 UI 组件。

    Args:
        blockId: 幂等键。相同 blockId 重复调用 → 前端更新该组件而非新增。
                 合法字符：字母/数字/_/-，长度 1-64。
        type: 组件类型，必须是白名单之一：
              table | flowchart | card | progress | action
        props: 组件参数对象（结构见 spec §5）。不同 type 的 props 不同。
        replace: 预留字段（前端按 blockId 幂等更新，此字段可忽略）。
        appendPreview: action 组件专用——为 true 时前端仅追加 preview 文本而非整体替换。

    Returns:
        JSON 字符串 {ok, delivered}。渲染结果走事件通道异步呈现，
        不会同步返回 HTML。
    """
    # 输入校验（纵深防御；前端白名单是最终闸门）
    if not isinstance(blockId, str) or not _BLOCK_ID_RE.match(blockId):
        return tool_error("blockId 必须为 1-64 位 [a-zA-Z0-9_-]")
    if not isinstance(type, str) or type not in UI_BLOCK_TYPES:
        return tool_error(f"type 必须是 {sorted(UI_BLOCK_TYPES)} 之一")
    if props is not None and not isinstance(props, dict):
        return tool_error("props 必须是对象")
    safe_props = props if isinstance(props, dict) else {}

    # 尺寸上限保护
    try:
        if len(json.dumps(safe_props, ensure_ascii=False)) > _MAX_PROPS_CHARS:
            return tool_error("props 过大（超过硬上限）")
    except Exception:
        pass

    coord = _ui_active_coord()
    if not coord or not coord.get("runId"):
        # 没有活跃对话轮（例如离线/非交互上下文）——静默成功，避免打断模型
        return json.dumps({"ok": True, "delivered": False, "note": "no active turn"}, ensure_ascii=False)

    run_id = coord["runId"]
    payload: Dict[str, Any] = {"blockId": blockId, "type": type, "props": safe_props}
    if replace:
        payload["replace"] = True
    if appendPreview:
        payload["appendPreview"] = True

    delivered = _post_ui_event(run_id, payload)
    return json.dumps({"ok": True, "delivered": delivered}, ensure_ascii=False)


def check_render_ui_requirements() -> bool:
    """render_ui 无外部依赖——只要能连上 agui-server 即可（best-effort）。"""
    return True


# =============================================================================
# OpenAI Function-Calling Schema
# =============================================================================
RENDER_UI_SCHEMA = {
    "name": "render_ui",
    "description": (
        "在对话流中渲染一个结构化 UI 组件，向用户直观展示信息或操作进度。\n"
        "当你判断纯文本不足以表达（对比表格、流程/架构图、步骤进度、信息卡片、"
        "或需要实时展示文件写入/命令执行的进度）时，主动调用本工具。\n\n"
        "组件会出现在最近一条 assistant 回复下方，不是独立面板。\n"
        "相同 blockId 重复调用会更新（而非新增）该组件——用它来实时刷新进度。\n\n"
        "可用 type：\n"
        "- table: 结构化表格 {columns:[str], rows:[[cell]], caption?, highlightRow?}\n"
        "- flowchart: 流程图 {nodes:[{id,label,shape?,status?}], edges:[{from,to,label?}], direction?}\n"
        "- card: 信息卡片 {title, icon?, body(markdown), actions?, tone?}\n"
        "- progress: 步骤进度 {steps:[{label,status}], current?}\n"
        "- action: 操作实时预览 {type:'file_write'|'command'|'http_request'|'generic', "
        "status:'pending'|'running'|'done'|'error', target?, preview?, previewLang?, detail?, error?}\n\n"
        "注意：不要输出 JSX/HTML；组件渲染由前端按 type 白名单处理。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "blockId": {
                "type": "string",
                "description": "幂等键（1-64 位字母数字_/-）。相同 blockId 重复调用会更新组件。"
            },
            "type": {
                "type": "string",
                "enum": ["table", "flowchart", "card", "progress", "action"],
                "description": "组件类型（白名单）"
            },
            "props": {
                "type": "object",
                "description": "组件参数对象，结构取决于 type（见工具描述）。"
            },
            "replace": {
                "type": "boolean",
                "description": "预留：前端按 blockId 幂等更新，一般无需设置。",
                "default": False,
            },
            "appendPreview": {
                "type": "boolean",
                "description": "action 组件专用：true 时仅追加 preview 文本（流式进度），否则整体替换。",
                "default": False,
            },
        },
        "required": ["blockId", "type", "props"],
    },
}


# --- Registry ---
from tools.registry import registry, tool_error  # noqa: E402
import logging  # noqa: E402
logger = logging.getLogger(__name__)

registry.register(
    name="render_ui",
    toolset="ui",
    schema=RENDER_UI_SCHEMA,
    handler=lambda args, **kw: render_ui(
        blockId=args.get("blockId"),
        type=args.get("type"),
        props=args.get("props"),
        replace=bool(args.get("replace", False)),
        appendPreview=bool(args.get("appendPreview", False)),
    ),
    check_fn=check_render_ui_requirements,
    emoji="🧩",
)
