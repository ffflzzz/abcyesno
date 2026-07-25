#!/usr/bin/env python3
"""Image generation sample workflow (contract reference sample #3).

This package exists to prove the core contract claim: adding a NEW workflow
requires NO frontend code change. The frontend renders it with the same four
generic components used by hello_agent and manju_craft - it only reads this
package's manifest.json plus the running events.

To stay runnable without external image credits, generate_node writes a
placeholder PNG via Pillow. Swap in a real image-model call there for
production use.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, TypedDict

from langgraph.graph import END, START, StateGraph

_DEFAULT_OUT = os.path.join(
    os.path.expanduser("~"), ".hermes_portable_data", "image_gen"
)


class ImageState(TypedDict, total=False):
    prompt: str
    size: str
    image_path: str
    status: str


def _resolve_out(prompt: str) -> str:
    os.makedirs(_DEFAULT_OUT, exist_ok=True)
    safe = "".join(c if c.isalnum() else "_" for c in (prompt or "out"))[:30] or "out"
    return os.path.join(_DEFAULT_OUT, f"{safe}.png")


def generate_node(state: ImageState) -> Dict[str, Any]:
    prompt = state.get("prompt", "")
    size = state.get("size", "1024x768")
    out = _resolve_out(prompt)
    try:
        from PIL import Image

        if "x" in size:
            w, h = (int(x) for x in size.split("x"))
        else:
            w, h = 1024, 768
        Image.new("RGB", (w, h), (60, 90, 160)).save(out)
    except Exception as exc:  # pragma: no cover - defensive fallback
        Path(out).write_text(f"image-gen placeholder for: {prompt}\n{exc}")
    return {"image_path": out, "status": "done"}


def build_graph():
    builder = StateGraph(ImageState)
    builder.add_node("generate", generate_node)
    builder.add_edge(START, "generate")
    builder.add_edge("generate", END)
    return builder.compile()


# Contract layer (L3/L5): ordered stages drive progress % and per-node status
# in the generic TimelineWorkbench. Node name must match the graph node so the
# runtime's emit_progress maps correctly.
WORKFLOW_STAGES = [
    ("generate", "图像生成"),
]


def build_initial_state_obj(obj: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "prompt": (obj.get("prompt") or "").strip(),
        "size": obj.get("size") or "1024x768",
        "image_path": "",
        "status": "idle",
    }


def summarize_state(result: Dict[str, Any]) -> Dict[str, Any]:
    path = result.get("image_path", "")
    artifacts = []
    if path:
        artifacts.append(
            {"id": "image", "type": "image", "source": "path", "path": path, "label": "生成结果"}
        )
    return {
        "summary": f"已生成图片：{result.get('prompt', '')}",
        "artifacts": artifacts,
    }
