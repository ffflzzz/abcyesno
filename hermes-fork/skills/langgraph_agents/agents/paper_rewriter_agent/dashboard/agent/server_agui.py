"""论文重写 Agent — FastAPI 服务（AG-UI 版本）

保留原有API + 新增AG-UI端点 + 图结构端点 + pipeline事件桥接

API:
  POST /api/run          — 启动一次论文重写
  GET  /api/status       — 当前运行状态
  GET  /api/events       — SSE事件流
  POST /api/stop         — 停止当前运行
  GET  /api/runs         — 历史run列表
  GET  /api/run/{id}     — 某次run的详情
  GET  /api/graph        — 流水线图结构（节点+边）
  GET  /api/event-log    — AG-UI事件日志（供Dashboard轮询）
  POST /api/copilotkit   — AG-UI端点
"""
# CRITICAL: 绕过Clash代理，否则langchain_openai导入超时20秒
import os as _os
for _k in list(_os.environ.keys()):
    if "proxy" in _k.lower():
        del _os.environ[_k]
_os.environ["no_proxy"] = "*"
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
from .graph import build_agent_graph

# AG-UI imports
from fastapi.staticfiles import StaticFiles
from ag_ui_langgraph import LangGraphAgent, add_langgraph_fastapi_endpoint

app = FastAPI(title="论文重写 Agent")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# GZip压缩（vis.js等大文件通过CF Tunnel需要压缩）
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=500)

# Strip /pr/ prefix for reverse proxy compatibility (abcyesno.cn/projects/paper-rewriter)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest

class StripPrPrefixMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):
        if request.url.path.startswith("/pr/"):
            request.scope["path"] = request.url.path[3:]  # strip "/pr"
            request.scope["root_path"] = "/pr"
        return await call_next(request)

app.add_middleware(StripPrPrefixMiddleware)

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
    """广播事件给所有SSE客户端 + 写trace日志 + event_log"""
    from .event_log import log_event
    event = {"type": event_type, "ts": time.time(), **data}
    for q in _event_queues:
        await q.put(event)
    log_event(event_type, data)
    # 写本地trace
    if current_run["state"]:
        current_run["state"].log_event(event_type, data)


# ── Pipeline事件桥接 ──
# 订阅pipeline.events.emitter，转发到agent event_log + SSE
_pipeline_subscribed = False

def _setup_pipeline_bridge():
    """桥接pipeline事件到前端（只注册一次）"""
    global _pipeline_subscribed
    if _pipeline_subscribed:
        return
    _pipeline_subscribed = True

    from pipeline.events import emitter
    from .event_log import log_event

    queue = emitter.subscribe()

    async def _forward():
        while True:
            try:
                evt = await asyncio.wait_for(queue.get(), timeout=1.0)
                # 转为agent event_log格式
                log_event(evt.event_type, {
                    "node_id": evt.node_id,
                    "message": evt.message,
                    "state_snapshot": evt.state_snapshot,
                    "metadata": evt.metadata,
                    "timestamp": evt.timestamp,
                })
                # 也广播到SSE
                for q in _event_queues:
                    await q.put({
                        "type": evt.event_type,
                        "ts": evt.timestamp,
                        "node_id": evt.node_id,
                        "message": evt.message,
                        "state_snapshot": evt.state_snapshot,
                        "metadata": evt.metadata,
                    })
            except asyncio.TimeoutError:
                continue
            except Exception:
                await asyncio.sleep(1)

    asyncio.ensure_future(_forward())


# ── Agent运行 ──

async def run_agent(req: RunRequest, run_id: str):
    """在后台线程中运行agent"""
    try:
        # 读取原文
        if req.original_path:
            original_text = Path(req.original_path).read_text(encoding="utf-8")
        else:
            original_text = req.original_text

        current_run["status"] = "running"
        current_run["started_at"] = time.time()
        current_run["run_id"] = run_id
        current_run["state"] = RunState(run_id)

        # 设置pipeline事件循环（供emit_sync使用）
        from pipeline.events import emitter
        emitter.set_loop(asyncio.get_event_loop())

        await broadcast("run_start", {"run_id": run_id, "title": req.paper_title})

        # 运行（在线程中执行同步代码）
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: run_rewrite(
                original_text=original_text,
                paper_title=req.paper_title,
                target_audience=req.target_audience,
                run_id=run_id,
                max_retries=req.max_rounds,
            ),
        )

        current_run["status"] = "completed"
        current_run["ended_at"] = time.time()
        await broadcast("run_end", {"run_id": run_id, "result": result})

    except Exception as e:
        current_run["status"] = "error"
        current_run["error"] = str(e)
        current_run["ended_at"] = time.time()
        await broadcast("run_error", {"run_id": run_id, "error": str(e)})


# ── 图结构端点 ──

PIPELINE_GRAPH = {
    "nodes": [
        {"id": "outline_generator", "label": "大纲生成", "type": "process",
         "description": "分析原文，生成章节大纲"},
        {"id": "writer", "label": "章节写作", "type": "process",
         "description": "逐章写作，微分-积分方法展开"},
        {"id": "reviewer", "label": "质量审查", "type": "process",
         "description": "对比原文，评分内容质量"},
        {"id": "fact_checker", "label": "事实核查", "type": "process",
         "description": "检查重写是否忠于原文"},
        {"id": "judge", "label": "裁判判定", "type": "decision",
         "description": "综合评分，PASS/FAIL"},
        {"id": "pdf_generator", "label": "PDF生成", "type": "end",
         "description": "生成最终PDF文件"},
    ],
    "edges": [
        {"from": "__start__", "to": "outline_generator", "label": ""},
        {"from": "outline_generator", "to": "writer", "label": "大纲完成"},
        {"from": "writer", "to": "reviewer", "label": "写作完成"},
        {"from": "reviewer", "to": "fact_checker", "label": "审查完成"},
        {"from": "fact_checker", "to": "judge", "label": "核查完成"},
        {"from": "judge", "to": "writer", "label": "FAIL: 重写"},
        {"from": "judge", "to": "pdf_generator", "label": "PASS"},
        {"from": "pdf_generator", "to": "__end__", "label": ""},
    ],
}


@app.get("/api/graph")
async def get_graph():
    """返回Agent图结构（从LangGraph动态获取）"""
    if _agui_agent is None:
        return {"nodes": [], "edges": []}

    g = _agui_agent.graph.get_graph()

    nodes = []
    for n in g.nodes.values():
        nid = n.id
        if nid == "__start__":
            nodes.append({"id": nid, "label": "▶ START", "type": "start", "desc": "接收用户消息"})
        elif nid == "__end__":
            nodes.append({"id": nid, "label": "⏹ END", "type": "end", "desc": "任务完成"})
        elif nid == "agent":
            nodes.append({"id": nid, "label": "🤖 Agent", "type": "process", "desc": "Agnes 2.0 Flash — 决定调用工具或回复用户"})
        elif nid == "tools":
            # 展开tools节点为子工具
            from agent.graph import tools as tool_list
            for t in tool_list:
                nodes.append({
                    "id": f"t_{t.name}",
                    "label": f"🔧 {t.name}",
                    "type": "tool",
                    "desc": (t.description or "")[:100],
                })
        else:
            nodes.append({"id": nid, "label": nid, "type": "process", "desc": ""})

    edges = []
    for e in g.edges:
        src, tgt = e.source, e.target
        if src == "agent" and tgt == "tools":
            # agent → 每个tool
            from agent.graph import tools as tool_list
            for t in tool_list:
                edges.append({"from": "agent", "to": f"t_{t.name}", "label": "", "color": "#58a6ff"})
        elif src == "tools" and tgt == "agent":
            # 每个tool → agent
            from agent.graph import tools as tool_list
            for t in tool_list:
                edges.append({"from": f"t_{t.name}", "to": "agent", "label": "", "color": "#8b949e"})
        elif src == "agent" and tgt == "__end__":
            edges.append({"from": src, "to": tgt, "label": "无tool_call", "color": "#3fb950"})
        else:
            edges.append({"from": src, "to": tgt, "label": ""})

    return {"nodes": nodes, "edges": edges}


# ── 原有API端点 ──

@app.post("/api/run")
async def start_run(req: RunRequest):
    """启动一次论文重写"""
    if current_run["status"] == "running":
        return JSONResponse(status_code=409, content={"error": "已有任务在运行"})

    run_id = str(uuid.uuid4())[:8]
    task = asyncio.create_task(run_agent(req, run_id))
    current_run["task"] = task

    return {"run_id": run_id, "status": "started"}


@app.get("/api/status")
async def get_status():
    """获取当前状态"""
    return {
        "run_id": current_run["run_id"],
        "status": current_run["status"],
        "started_at": current_run["started_at"],
        "ended_at": current_run["ended_at"],
        "error": current_run["error"],
        "state": current_run["state"].to_dict() if current_run["state"] else None,
    }


# ── AG-UI事件日志（供Dashboard轮询） ──
from .event_log import log_event, get_events

@app.get("/api/event-log")
async def get_event_log(after: float = 0):
    """获取AG-UI事件日志（支持增量拉取）"""
    return {"events": get_events(after)}


@app.get("/api/events")
async def events():
    """SSE事件流"""
    queue: asyncio.Queue = asyncio.Queue()
    _event_queues.append(queue)

    async def event_generator():
        try:
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            _event_queues.remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/stop")
async def stop_run():
    """停止当前运行"""
    if current_run["task"] and not current_run["task"].done():
        current_run["task"].cancel()
        current_run["status"] = "stopped"
        current_run["ended_at"] = time.time()
        return {"status": "stopped"}
    return {"status": "no_active_run"}


@app.get("/api/runs")
async def list_runs():
    """列出所有历史run"""
    runs = []
    if RUNS_DIR.exists():
        for run_dir in sorted(RUNS_DIR.iterdir(), reverse=True):
            if run_dir.is_dir():
                meta_path = run_dir / "meta.json"
                if meta_path.exists():
                    meta = json.loads(meta_path.read_text())
                    runs.append(meta)
    return {"runs": runs}


@app.get("/api/run/{run_id}")
async def get_run(run_id: str):
    """获取某个run的详情"""
    run_dir = RUNS_DIR / run_id
    if not run_dir.exists():
        return JSONResponse(status_code=404, content={"error": "Run not found"})

    meta = {}
    meta_path = run_dir / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text())

    progress = {}
    progress_path = run_dir / "progress.json"
    if progress_path.exists():
        progress = json.loads(progress_path.read_text())

    chapters = {}
    chapters_dir = run_dir / "chapters"
    if chapters_dir.exists():
        for f in sorted(chapters_dir.iterdir()):
            if f.suffix == ".txt":
                chapters[f.stem] = f.read_text(encoding="utf-8")

    return {"meta": meta, "progress": progress, "chapters": chapters}


# ── AG-UI 端点 ──

# 创建AG-UI agent（AsyncSqliteSaver for persistence）
import aiosqlite
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

_db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "checkpoints.db")
_checkpointer = None
_agui_agent = None

@app.on_event("startup")
async def init_checkpointer():
    global _checkpointer, _agui_agent

    # 桥接pipeline事件
    _setup_pipeline_bridge()

    # 初始化session存储
    try:
        from etclovg.session_store import init_db
        init_db()
        print("Session store initialized")
    except Exception as e:
        print(f"Session store init failed: {e}")

    # ETCLOVG: 注册system prompt版本
    try:
        from etclovg.versioning import register_prompt
        from agent.graph import SYSTEM_PROMPT
        register_prompt(SYSTEM_PROMPT, notes="agent startup")
    except Exception:
        pass

    conn = await aiosqlite.connect(_db_path)
    _checkpointer = AsyncSqliteSaver(conn)

    graph = build_agent_graph(checkpointer=_checkpointer)
    _agui_agent = LangGraphAgent(
        config={"recursion_limit": 500},
        name="paper_rewriter",
        graph=graph,
        description="论文重写多Agent系统：writer→reviewer→factchecker→judge循环",
    )
    # 自建AG-UI端点（兼容CopilotKit Runtime不传state的问题）
    from ag_ui.core.types import RunAgentInput
    from ag_ui.encoder import EventEncoder

    @app.post("/api/copilotkit")
    async def copilotkit_endpoint(request: Request):
        """AG-UI端点 — 兼容CopilotKit Runtime和直连两种格式"""
        try:
            body = await request.json()

            # 处理info方法（CopilotKit Runtime查询agent信息）
            if body.get("method") == "info":
                return {
                    "version": "1.0.0",
                    "agents": {"paper_rewriter": {"name": "paper_rewriter", "description": "论文重写多Agent系统"}},
                }

            # 处理connect/stop方法（CopilotKit Runtime管理连接）
            if body.get("method") in ("agent/connect", "agent/stop"):
                return {"status": "ok"}

            # agent/run 方法或直连格式：解析为RunAgentInput
            if "method" in body:
                inner = {k: v for k, v in body.items() if k not in ("method", "params")}
            else:
                inner = body


            # 规范化 messages，确保每条消息都有 id（AG-UI schema 要求）
            if "messages" in inner:
                for msg in inner["messages"]:
                    if isinstance(msg, dict) and "id" not in msg:
                        msg["id"] = f"msg_{len(inner['messages'])}"

            # 补默认值
            if "state" not in inner:
                inner["state"] = {}
            if "tools" not in inner:
                inner["tools"] = []
            if "context" not in inner:
                inner["context"] = []
            if "forwardedProps" not in inner:
                inner["forwardedProps"] = {}

            input_data = RunAgentInput(**inner)

            accept_header = request.headers.get("accept")
            encoder = EventEncoder(accept=accept_header)
            request_agent = _agui_agent.clone()

            async def event_generator():
                async for event in request_agent.run(input_data):
                    yield encoder.encode(event)

            return StreamingResponse(
                event_generator(),
                media_type=encoder.get_content_type(),
            )
        except Exception as e:
            import traceback
            print(f"AGUI ERROR: {e}")
            traceback.print_exc()
            return JSONResponse(status_code=500, content={"error": str(e)})
    print(f"AG-UI initialized with AsyncSqliteSaver: {_db_path}")

    # 在AG-UI端点之后挂载静态文件，确保API路由优先级更高
    if FRONTEND_DIR.exists():
        app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="static")
        print(f"Static files served from: {FRONTEND_DIR}")


# ── 论文搜索API ──

@app.get("/api/search_paper")
async def search_paper_api(query: str, max_results: int = 3):
    """搜索论文API"""
    from .paper_search import search_papers
    papers = search_papers(query, max_results)
    return {"papers": papers}


@app.post("/api/download_paper")
async def download_paper_api(arxiv_id: str, run_id: str):
    """下载论文API"""
    from .paper_search import confirm_and_download
    result = confirm_and_download(arxiv_id, run_id)
    return result


# ── PDF下载 ──
from fastapi.responses import FileResponse

OUTPUT_DIR = Path(__file__).parent.parent / "output"

@app.get("/api/output/list")
async def list_output_files():
    """列出所有生成的PDF文件"""
    files = []
    if OUTPUT_DIR.exists():
        for f in sorted(OUTPUT_DIR.iterdir(), reverse=True):
            if f.suffix == ".pdf":
                files.append({
                    "name": f.name,
                    "size": f.stat().st_size,
                    "modified": f.stat().st_mtime,
                })
    return {"files": files}

@app.get("/api/output/{filename}")
async def download_output_file(filename: str):
    """下载生成的PDF文件"""
    file_path = OUTPUT_DIR / filename
    if not file_path.exists() or file_path.suffix != ".pdf":
        return JSONResponse(status_code=404, content={"error": "File not found"})
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="application/pdf",
    )


# ── Session API ──

@app.get("/api/sessions")
async def api_list_sessions():
    from etclovg.session_store import list_sessions
    return {"sessions": list_sessions()}

@app.post("/api/sessions")
async def api_upsert_session(request: Request):
    body = await request.json()
    from etclovg.session_store import upsert_session
    upsert_session(body["id"], body.get("title", ""))
    return {"ok": True}

@app.delete("/api/sessions/{session_id}")
async def api_delete_session(session_id: str):
    from etclovg.session_store import delete_session
    delete_session(session_id)
    return {"ok": True}

@app.get("/api/sessions/{session_id}/messages")
async def api_get_messages(session_id: str):
    from etclovg.session_store import get_messages
    return {"messages": get_messages(session_id)}

@app.post("/api/sessions/{session_id}/messages")
async def api_add_message(session_id: str, request: Request):
    body = await request.json()
    from etclovg.session_store import add_message
    add_message(session_id, body["id"], body["role"], body["content"], body.get("tool_name", ""))
    return {"ok": True}

# ── ETCLOVG API ──

@app.get("/api/etclovg")
async def get_etclovg_status():
    """ETCLOVG框架状态总览"""
    from etclovg.governance import get_governance_status
    from etclovg.versioning import get_version_info
    from etclovg.evaluation import get_evaluation_status
    return {
        "governance": get_governance_status(),
        "versioning": get_version_info(),
        "evaluation": get_evaluation_status(),
    }

# ── 静态文件服务（在startup中挂载，确保AG-UI端点优先级更高） ──
FRONTEND_DIR = Path(__file__).parent.parent / "frontend" / "dist"


# ── 启动 ──

def main():
    port = int(os.environ.get("PORT", 8765))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()


