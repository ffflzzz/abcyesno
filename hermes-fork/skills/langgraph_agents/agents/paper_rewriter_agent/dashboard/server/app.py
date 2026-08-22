"""FastAPI 服务器 — SSE 实时推送 + REST 状态查询

运行: python -m server.app
或:   uvicorn server.app:app --host 0.0.0.0 --port 8765
"""
from __future__ import annotations
import asyncio
import json
import threading
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel

from pipeline.graph import compile_graph
from pipeline.state import RewriteState
from pipeline.events import emitter, PipelineEvent, fire_node_start, fire_error
from pipeline.config import SERVER_HOST, SERVER_PORT, OUTPUT_DIR
from server.agui_agent import setup_agui_endpoint
import os

# ─── 全局状态 ───
current_run: dict = {
    "run_id": None,
    "status": "idle",       # idle | running | completed | error
    "state": None,
    "started_at": None,
    "ended_at": None,
    "graph_definition": None,
}

# 事件历史（最近500条）
event_history: list[PipelineEvent] = []
MAX_HISTORY = 500


# ─── 统一事件发射：emitter + history ───
# 让所有 fire_* 函数同时写入 event_history，避免重复事件源
_orig_emit_sync = emitter.emit_sync

def _unified_emit(event: PipelineEvent):
    """发射事件到 SSE 客户端 + 写入 event_history（跳过 llm_token，太嘈杂）"""
    _orig_emit_sync(event)
    if event.event_type != "llm_token":
        event_history.append(event)
        if len(event_history) > MAX_HISTORY:
            event_history.pop(0)

emitter.emit_sync = _unified_emit


# ─── Graph 定义（静态，给前端画图用）───
GRAPH_NODES = [
    {"id": "outline_generator", "label": "大纲生成", "type": "start"},
    {"id": "writer", "label": "写手Agent", "type": "process"},
    {"id": "reviewer", "label": "比对员Agent", "type": "process"},
    {"id": "judge", "label": "裁判Agent", "type": "decision"},
    {"id": "pdf_generator", "label": "PDF生成", "type": "end"},
]

GRAPH_EDGES = [
    {"from": "__start__", "to": "outline_generator", "label": ""},
    {"from": "outline_generator", "to": "writer", "label": ""},
    {"from": "writer", "to": "reviewer", "label": ""},
    {"from": "reviewer", "to": "judge", "label": ""},
    {"from": "judge", "to": "pdf_generator", "label": "PASS"},
    {"from": "judge", "to": "writer", "label": "FAIL"},
    {"from": "pdf_generator", "to": "__end__", "label": ""},
]


# ─── Lifespan ───
@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    # 注册事件循环，让 emit_sync 从后台线程安全唤醒 SSE
    emitter.set_loop(asyncio.get_running_loop())
    yield


# ─── FastAPI App ───
app = FastAPI(
    title="论文重写 LangGraph Dashboard",
    version="1.0.0",
    lifespan=lifespan,
)

# 注册 AG-UI endpoint（CopilotKit 标准协议）
setup_agui_endpoint(app)

# 静态文件：/ui/ 指向旧 dashboard 目录
_ui_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ui")
app.mount("/ui", StaticFiles(directory=_ui_dir), name="ui")

# React build 产物
_frontend_dist = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend", "dist")
if os.path.exists(_frontend_dist):
    app.mount("/assets", StaticFiles(directory=os.path.join(_frontend_dist, "assets")), name="frontend-assets")


# ─── Models ───
class RunRequest(BaseModel):
    paper_title: str
    original_text: str
    target_audience: str = "大一非理工科学生"
    language: str = "zh"
    max_rounds: int = 3


from fastapi import UploadFile, File as FastAPIFile, Form


@app.post("/api/upload")
async def upload_file(file: UploadFile = FastAPIFile(...)):
    """上传文件（PDF/TXT），提取文本"""
    import tempfile, os
    content = await file.read()
    suffix = os.path.splitext(file.filename or "")[1].lower()
    text = ""

    if suffix == ".txt" or suffix == ".md":
        text = content.decode("utf-8", errors="ignore")
    elif suffix == ".pdf":
        try:
            import fitz
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            doc = fitz.open(tmp_path)
            text = "\n".join(page.get_text() for page in doc)
            doc.close()
            os.unlink(tmp_path)
        except ImportError:
            return JSONResponse({"error": "需要安装pymupdf: pip install pymupdf"}, status_code=500)
        except Exception as e:
            return JSONResponse({"error": f"PDF解析失败: {e}"}, status_code=400)
    else:
        return JSONResponse({"error": f"不支持的文件格式: {suffix}，支持 .txt .md .pdf"}, status_code=400)

    return {"filename": file.filename, "text": text, "chars": len(text)}


@app.post("/api/fetch-url")
async def fetch_url(request: Request):
    """从URL抓取文本内容"""
    import httpx
    body = await request.json()
    url = body.get("url", "")
    if not url:
        return JSONResponse({"error": "缺少url参数"}, status_code=400)
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if "text/html" in content_type:
                # 简单去HTML标签
                import re
                text = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', resp.text, flags=re.IGNORECASE)
                text = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', text, flags=re.IGNORECASE)
                text = re.sub(r'<[^>]+>', '', text)
                text = re.sub(r'\s+', ' ', text).strip()
            else:
                text = resp.text
            return {"url": url, "text": text, "chars": len(text)}
    except Exception as e:
        return JSONResponse({"error": f"抓取失败: {e}"}, status_code=400)


# ─── API 路由 ───

@app.get("/", response_class=HTMLResponse)
async def index():
    """返回 React 前端（如有），否则返回旧 dashboard UI"""
    react_index = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist", "index.html")
    if os.path.exists(react_index):
        with open(react_index, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    # fallback: 旧 dashboard
    ui_path = os.path.join(os.path.dirname(__file__), "..", "ui", "index.html")
    with open(ui_path, "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/api/graph")
async def get_graph_definition():
    """返回图结构定义（供前端绘制）"""
    return {
        "nodes": GRAPH_NODES,
        "edges": GRAPH_EDGES,
    }


@app.get("/api/status")
async def get_status():
    """返回当前运行状态"""
    state_snap = None
    if current_run["state"]:
        from pipeline.events import make_state_snapshot
        state_snap = make_state_snapshot(current_run["state"])

    return {
        "run_id": current_run["run_id"],
        "status": current_run["status"],
        "started_at": current_run["started_at"],
        "ended_at": current_run["ended_at"],
        "error": current_run.get("error", ""),
        "state": state_snap,
    }


@app.post("/api/run")
async def start_run(req: RunRequest):
    """启动一次pipeline执行"""
    if current_run["status"] == "running":
        return JSONResponse(
            {"error": "Pipeline 正在运行中，请等待完成"},
            status_code=409,
        )

    run_id = str(uuid.uuid4())[:8]
    current_run.update({
        "run_id": run_id,
        "status": "running",
        "state": None,
        "error": "",
        "started_at": time.time(),
        "ended_at": None,
    })
    event_history.clear()  # 清空旧事件，防止新客户端收到旧run的事件

    # 在后台线程运行pipeline
    initial_state: RewriteState = {
        "paper_title": req.paper_title,
        "original_text": req.original_text,
        "target_audience": req.target_audience,
        "language": req.language,
        "max_rounds": req.max_rounds,
        "current_phase": "init",
        "round_num": 1,
        "chapters": {},
        "chapter_order": [],
        "current_chapter_idx": 0,
        "full_rewrite": "",
        "outline": "",
        "review_report": "",
        "score": 0.0,
        "fact_check_report": "",
        "factual_accuracy": 0.0,
        "judge_verdict": "",
        "fix_list": [],
        "pdf_path": "",
        "status": "running",
        "error": "",
    }

    thread = threading.Thread(
        target=_run_pipeline,
        args=(run_id, initial_state),
        daemon=True,
    )
    thread.start()

    return {"run_id": run_id, "status": "started"}


@app.post("/api/stop")
async def stop_run():
    """标记当前运行为停止"""
    current_run["status"] = "stopped"
    current_run["ended_at"] = time.time()
    return {"status": "stopped"}


@app.get("/api/events")
async def event_stream(request: Request):
    """SSE 事件流 — 前端订阅此端点获取实时更新"""
    queue = emitter.subscribe()

    async def generate():
        try:
            # 先发送历史事件
            for evt in event_history[-50:]:
                yield {
                    "event": evt.event_type,
                    "data": evt.to_json(),
                }

            # 然后实时推送
            while True:
                if await request.is_disconnected():
                    break
                try:
                    evt: PipelineEvent = await asyncio.wait_for(
                        queue.get(), timeout=30
                    )
                    yield {
                        "event": evt.event_type,
                        "data": evt.to_json(),
                    }
                except asyncio.TimeoutError:
                    # 心跳
                    yield {"event": "heartbeat", "data": "{}"}
        finally:
            emitter.unsubscribe(queue)

    return EventSourceResponse(generate())


@app.get("/api/history")
async def get_event_history(limit: int = 50):
    """获取最近的事件历史"""
    return [
        {
            "timestamp": evt.timestamp,
            "node_id": evt.node_id,
            "event_type": evt.event_type,
            "message": evt.message,
            "state_snapshot": evt.state_snapshot,
        }
        for evt in event_history[-limit:]
    ]


@app.get("/api/output/{filename}")
async def get_output_file(filename: str):
    """下载生成的文件"""
    from fastapi.responses import FileResponse
    path = os.path.join(OUTPUT_DIR, filename)
    if os.path.exists(path):
        return FileResponse(path)
    return JSONResponse({"error": "文件不存在"}, status_code=404)


# ─── Pipeline 运行器 ───

def _run_pipeline(run_id: str, initial_state: RewriteState):
    """在后台线程中执行 LangGraph pipeline

    事件统一由 nodes.py 的 fire_* 函数发射（已通过 _unified_emit 同步写入 event_history）。
    此函数只负责：执行图、更新 current_run 状态、错误处理。
    """
    import sys as _sys
    _pf_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pipeline_debug.log")
    _pf = open(_pf_path, "a", encoding="utf-8")
    def plog(msg):
        import time as _t
        line = f"[{_t.strftime('%H:%M:%S')}] {msg}"
        try:
            print(line, flush=True)
        except OSError:
            pass  # Windows console encoding issue — don't crash the pipeline
        _pf.write(line + "\n")
        _pf.flush()
    try:
        plog(f"[Pipeline {run_id}] 开始执行")
        app_graph = compile_graph()

        for event in app_graph.stream(initial_state, {"recursion_limit": 50}):
            if current_run["status"] == "stopped":
                plog(f"[Pipeline {run_id}] 用户停止")
                break

            plog(f"event keys: {list(event.keys())}")
            for node_name, state_update in event.items():
                plog(f"节点 {node_name} 完成, update keys: {list(state_update.keys()) if isinstance(state_update, dict) else type(state_update)}")
                if current_run["state"] is None:
                    current_run["state"] = dict(initial_state)
                try:
                    current_run["state"].update(state_update)
                except Exception as update_err:
                    plog(f"state.update 错误: {update_err}")
                    import traceback; traceback.print_exc(); _pf.write(traceback.format_exc()); _pf.flush()
                    raise

        if current_run["status"] != "stopped":
            current_run["status"] = "completed"
            plog(f"执行完成")

    except Exception as e:
        current_run["status"] = "error"
        current_run["error"] = str(e)
        plog(f"错误: {e}")
        import traceback
        tb = traceback.format_exc()
        plog(tb)
        _err_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pipeline_error.log")
        try:
            with open(_err_path, "a", encoding="utf-8") as f:
                f.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
                f.write(tb)
        except OSError as log_err:
            plog(f"写错误日志失败: {log_err}")
        try:
            fire_error("__pipeline__", f"Pipeline 异常: {e}", current_run.get("state") or {})
        except OSError:
            pass  # event emission failure shouldn't crash

    finally:
        current_run["ended_at"] = time.time()
        try:
            _pf.close()
        except OSError:
            pass


# ─── 启动 ───
if __name__ == "__main__":
    import uvicorn
    print(f"🚀 论文重写 Dashboard 启动: http://localhost:{SERVER_PORT}")
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT)
