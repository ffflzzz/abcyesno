"""论文重写 Agent — FastAPI 服务

ETCLOVG O5: Observability — SSE事件流 + trace日志
ETCLOVG G7: Governance — 重试上限、超时、限流

API:
  POST /api/run          — 启动一次论文重写
  GET  /api/status       — 当前运行状态
  GET  /api/events       — SSE事件流
  POST /api/stop         — 停止当前运行
  GET  /api/runs         — 历史run列表
  GET  /api/run/{id}     — 某次run的详情
"""
import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import AsyncGenerator

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from .rewrite_graph import run_rewrite
from .state import RunState

app = FastAPI(title="论文重写 Agent")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ── 状态 ──
RUNS_DIR = Path(__file__).parent.parent / "runs"
RUNS_DIR.mkdir(exist_ok=True)

current_run = {
    "run_id": None,
    "status": "idle",
    "started_at": None,
    "ended_at": None,
    "agent": None,
    "state": None,
    "task": None,
    "error": "",
}


class RunRequest(BaseModel):
    paper_title: str
    original_text: str = ""
    original_path: str = ""
    target_audience: str = "大一非理工科学生"
    language: str = "zh"
    max_rounds: int = 3


# ── 事件广播 ──
_event_queues: list[asyncio.Queue] = []


async def broadcast(event_type: str, data: dict):
    """广播事件给所有SSE客户端 + 写trace日志"""
    event = {"type": event_type, "ts": time.time(), **data}
    for q in _event_queues:
        await q.put(event)
    # 写本地trace
    if current_run["state"]:
        current_run["state"].log_event(event_type, data)


# ── Agent运行 ──

async def run_agent(req: RunRequest, run_id: str):
    """在后台线程中运行agent"""
    try:
        # 读取原文
        if req.original_path:
            text = Path(req.original_path).read_text(encoding="utf-8")
        elif req.original_text:
            text = req.original_text
        else:
            raise ValueError("需要提供 original_text 或 original_path")

        state_dir = str(RUNS_DIR / run_id)

        await broadcast("agent_start", {
            "paper_title": req.paper_title,
            "text_len": len(text),
        })

        # 使用rewrite_graph运行
        await broadcast("phase", {"phase": "outline"})
        
        # 在线程中运行同步函数
        import asyncio
        loop = asyncio.get_event_loop()
        run_id_result = await loop.run_in_executor(
            None,
            lambda: run_rewrite(
                paper_title=req.paper_title,
                original_text=text,
                target_audience=req.target_audience,
                run_id=run_id,
                max_retries=2,
            )
        )
        
        await broadcast("agent_complete", {"run_id": run_id_result})
        current_run["status"] = "completed"
        current_run["ended_at"] = time.time()
        
    except Exception as e:
        current_run["status"] = "error"
        current_run["error"] = str(e)
        current_run["ended_at"] = time.time()
        await broadcast("error", {"error": str(e)})
        raise


def generate_pdf(run_state: RunState, title: str, language: str) -> str:
    """从已完成的章节生成PDF"""
    from fpdf import FPDF

    chapters = run_state.list_chapters()
    if not chapters:
        return ""

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)

    # 封面
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 24)
    pdf.cell(0, 40, "", ln=True)
    pdf.multi_cell(0, 12, title, align="C")
    pdf.set_font("Helvetica", "", 14)
    pdf.cell(0, 20, "", ln=True)
    pdf.cell(0, 10, "Rewritten", align="C", ln=True)

    # 章节
    for ch_num in chapters:
        content = run_state.load_chapter(ch_num)
        if not content:
            continue
        pdf.add_page()
        # 简单处理：去markdown标记
        lines = content.split("\n")
        for line in lines:
            line = line.strip()
            if not line:
                pdf.ln(5)
                continue
            if line.startswith("# "):
                pdf.set_font("Helvetica", "B", 18)
                pdf.multi_cell(0, 10, line[2:])
                pdf.ln(5)
            elif line.startswith("## "):
                pdf.set_font("Helvetica", "B", 14)
                pdf.multi_cell(0, 8, line[3:])
                pdf.ln(3)
            elif line.startswith("### "):
                pdf.set_font("Helvetica", "B", 12)
                pdf.multi_cell(0, 7, line[4:])
                pdf.ln(2)
            else:
                pdf.set_font("Helvetica", "", 11)
                pdf.multi_cell(0, 6, line)

    safe_title = title.replace("/", "_").replace("\\", "_")[:50]
    pdf_path = str(RUNS_DIR / run_state.dir.name / f"{safe_title}.pdf")
    pdf.output(pdf_path)
    return pdf_path


# ── API Endpoints ──

@app.post("/api/run")
async def start_run(req: RunRequest):
    if current_run["status"] == "running":
        return JSONResponse({"error": "Agent正在运行"}, status_code=409)

    run_id = str(uuid.uuid4())[:8]
    current_run.update({
        "run_id": run_id,
        "status": "running",
        "started_at": time.time(),
        "ended_at": None,
        "agent": None,
        "state": None,
        "error": "",
    })

    task = asyncio.create_task(run_agent(req, run_id))
    current_run["task"] = task

    return {"run_id": run_id, "status": "started"}


@app.get("/api/status")
async def get_status():
    state_snap = None
    if current_run["state"]:
        s = current_run["state"]
        meta = s.load_meta()
        state_snap = {
            "meta": meta,
            "chapters_done": s.list_chapters(),
            "outline_exists": bool(s.load_outline()),
        }
    return {
        "run_id": current_run["run_id"],
        "status": current_run["status"],
        "started_at": current_run["started_at"],
        "ended_at": current_run["ended_at"],
        "error": current_run["error"],
        "state": state_snap,
    }


@app.get("/api/events")
async def sse_events(request: Request):
    async def event_stream() -> AsyncGenerator[str, None]:
        q = asyncio.Queue()
        _event_queues.append(q)
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=30)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield f": keepalive\n\n"
        finally:
            _event_queues.remove(q)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/api/stop")
async def stop_run():
    if current_run["status"] != "running":
        return {"message": "没有运行中的agent"}
    current_run["status"] = "stopping"
    return {"message": "正在停止..."}


@app.get("/api/runs")
async def list_runs():
    runs = []
    for d in sorted(RUNS_DIR.iterdir(), reverse=True):
        if d.is_dir():
            meta_file = d / "meta.json"
            if meta_file.exists():
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                chapters = list((d / "chapters").glob("ch*.md")) if (d / "chapters").exists() else []
                runs.append({
                    "run_id": d.name,
                    "meta": meta,
                    "chapters_count": len(chapters),
                })
    return runs


@app.get("/api/run/{run_id}")
async def get_run(run_id: str):
    run_dir = RUNS_DIR / run_id
    if not run_dir.exists():
        return JSONResponse({"error": "run不存在"}, status_code=404)

    from .state import RunState
    s = RunState(str(run_dir))
    meta = s.load_meta()
    chapters = s.list_chapters()
    outline = s.load_outline()

    chapter_contents = {}
    for ch in chapters:
        content = s.load_chapter(ch)
        if content:
            chapter_contents[ch] = {"chars": len(content), "preview": content[:200]}

    return {
        "run_id": run_id,
        "meta": meta,
        "outline": outline,
        "chapters": chapter_contents,
    }


def main():
    uvicorn.run(app, host="0.0.0.0", port=8765)


if __name__ == "__main__":
    main()
