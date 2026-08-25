"""FastAPI 服务器 — Agent 架构版（最新LangGraph）

运行: python -m server.agent_app
或:   uvicorn server.agent_app:app --host 0.0.0.0 --port 8765
"""
from __future__ import annotations
import asyncio
import json
import os
import re
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse
from pydantic import BaseModel

# 确保项目根目录在path中
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent.graph import build_agent_graph, set_current_run_id, init_run, _get_run_dir, _log
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, SystemMessage
from langgraph.types import Command
from pipeline.config import SERVER_HOST, SERVER_PORT, OUTPUT_DIR

# ─── 全局状态 ───
current_run: dict = {
    "run_id": None,
    "status": "idle",
    "started_at": None,
    "ended_at": None,
    "error": "",
    "tool_calls": 0,
    "last_action": "",
    "auto_approve": False,
    "awaiting": None,   # HITL 挂起信息：{tool, reason, args, preview}
    "auto_tools": set(),  # 本次运行内被授权免批的工具集合
}

# HITL 决策通道：运行线程在此等待，resume 端点注入决策值
_resume_event: threading.Event = threading.Event()
_resume_value: list = [True]
_run_thread: threading.Thread | None = None

_sse_queues: list = []


def _fire_sse(event_type: str, data: dict):
    payload = json.dumps(data, ensure_ascii=False)
    for q in _sse_queues:
        try:
            q.put_nowait({"event": event_type, "data": payload})
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    yield


app = FastAPI(
    title="论文重写 Agent Dashboard",
    version="2.0.0",
    lifespan=lifespan,
)

# 静态文件
_ui_dir = os.path.join(_PROJECT_ROOT, "ui")
if os.path.exists(_ui_dir):
    app.mount("/ui", StaticFiles(directory=_ui_dir), name="ui")

_frontend_dist = os.path.join(_PROJECT_ROOT, "frontend", "dist")
if os.path.exists(_frontend_dist):
    # 构建产物可能全部内联进 index.html（CDN React + 浏览器内 Babel），assets 目录不一定存在
    _assets_dir = os.path.join(_frontend_dist, "assets")
    if os.path.isdir(_assets_dir):
        app.mount("/assets", StaticFiles(directory=_assets_dir), name="frontend-assets")


class RunRequest(BaseModel):
    paper_title: str
    original_text: str = ""  # 可选：为空时 Agent 自动搜索并下载论文
    target_audience: str = "大一非理工科学生"
    max_tool_calls: int = 200
    auto_approve: bool = False  # True=全自动（HITL 中断自动批准）；False=每步等人工确认
    force: bool = False         # True=终止当前运行并接管启动新任务


class ResumeRequest(BaseModel):
    # True/False = 批准/跳过；字符串 = 批准并捎话给 Agent（成为工具返回值进入其上下文）
    decision: bool | str = True
    # once=仅本次；tool=本次运行内同类操作自动批准（免重复决策）
    scope: str = "once"


from fastapi import UploadFile, File as FastAPIFile


@app.post("/api/upload")
async def upload_file(file: UploadFile = FastAPIFile(...)):
    """上传文件（PDF/TXT），提取文本"""
    import tempfile
    content = await file.read()
    suffix = os.path.splitext(file.filename or "")[1].lower()
    text = ""

    if suffix in (".txt", ".md"):
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
        return JSONResponse({"error": f"不支持的文件格式: {suffix}"}, status_code=400)

    return {"filename": file.filename, "text": text, "chars": len(text)}


@app.get("/", response_class=HTMLResponse)
async def index():
    react_index = os.path.join(_PROJECT_ROOT, "frontend", "dist", "index.html")
    if os.path.exists(react_index):
        with open(react_index, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    ui_path = os.path.join(_PROJECT_ROOT, "ui", "index.html")
    with open(ui_path, "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/api/graph")
async def get_graph_definition():
    return {
        "nodes": [
            {"id": "agent", "label": "Agent LLM", "type": "process"},
            {"id": "tools", "label": "ToolNode", "type": "process"},
        ],
        "edges": [
            {"from": "__start__", "to": "agent", "label": ""},
            {"from": "agent", "to": "tools", "label": "有tool_call"},
            {"from": "agent", "to": "__end__", "label": "无tool_call"},
            {"from": "tools", "to": "agent", "label": "返回结果"},
        ],
    }


@app.get("/api/status")
async def get_status():
    chapters_info = {}
    run_id = current_run.get("run_id")
    if run_id:
        run_dir = _get_run_dir(run_id)
        progress_path = os.path.join(run_dir, "progress.json")
        if os.path.exists(progress_path):
            with open(progress_path, "r", encoding="utf-8") as f:
                chapters_info = json.load(f).get("chapters", {})

    return {
        "run_id": current_run["run_id"],
        "status": current_run["status"],
        "started_at": current_run["started_at"],
        "ended_at": current_run["ended_at"],
        "error": current_run.get("error", ""),
        "tool_calls": current_run.get("tool_calls", 0),
        "last_action": current_run.get("last_action", ""),
        "auto_approve": current_run.get("auto_approve", False),
        "awaiting": current_run.get("awaiting"),
        "auto_tools": sorted(current_run.get("auto_tools") or []),
        "chapters": chapters_info,
    }


@app.get("/api/runs")
async def list_runs():
    runs_dir = os.path.join(_PROJECT_ROOT, "runs")
    if not os.path.exists(runs_dir):
        return []
    
    runs = []
    for name in sorted(os.listdir(runs_dir), reverse=True):
        run_dir = os.path.join(runs_dir, name)
        if not os.path.isdir(run_dir):
            continue
        
        meta = {}
        progress = {}
        
        meta_path = os.path.join(run_dir, "meta.json")
        progress_path = os.path.join(run_dir, "progress.json")
        
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        if os.path.exists(progress_path):
            with open(progress_path, "r", encoding="utf-8") as f:
                progress = json.load(f)
        
        chapters = progress.get("chapters", {})
        total_chars = sum(ch.get("chars", 0) for ch in chapters.values())
        
        runs.append({
            "run_id": name,
            "paper_title": meta.get("paper_title", ""),
            "original_chars": meta.get("original_chars", 0),
            "chapters_written": len(chapters),
            "total_chars": total_chars,
            "created_at": meta.get("created_at", 0),
        })
    
    return runs


@app.get("/api/runs/{run_id}/detail")
async def run_detail(run_id: str):
    """历史会话详情：元信息 + 章节 + 大纲 + 原文规模（事件流不可回放，产物均可读）"""
    d = _get_run_dir(run_id)
    if not os.path.isdir(d):
        return JSONResponse({"error": f"{run_id} 不存在"}, status_code=404)

    meta: dict = {}
    progress: dict = {}
    for name, target in (("meta.json", meta), ("progress.json", progress)):
        p = os.path.join(d, name)
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    target.update(json.load(f))
            except Exception:
                pass

    outline = ""
    op = os.path.join(d, "outline.txt")
    if os.path.exists(op):
        with open(op, "r", encoding="utf-8") as f:
            outline = f.read()

    original_chars = 0
    gp = os.path.join(d, "original.txt")
    if os.path.exists(gp):
        original_chars = os.path.getsize(gp)

    # 若尚未生成 PDF 但已有章节，则按需生成（让历史会话也能下载）
    pdf_path = os.path.join(d, "output.pdf")
    if not os.path.exists(pdf_path) and progress.get("chapters"):
        try:
            generate_pdf(run_id)
        except Exception as e:
            _log(f"[detail {run_id}] PDF 生成失败: {e}")
    pdf_ready = os.path.exists(pdf_path)

    return {
        "run_id": run_id,
        "paper_title": meta.get("paper_title", run_id),
        "created_at": meta.get("created_at", 0),
        "chapters": progress.get("chapters", {}),
        "outline": outline,
        "original_chars": original_chars,
        "pdf_ready": pdf_ready,
    }


# ─── PDF 导出（中文需嵌入 CJK 字体）───
_CJK_FONT_CANDIDATES = [
    r"C:/Windows/Fonts/simhei.ttf",
    r"C:/Windows/Fonts/msyh.ttc",
    r"C:/Windows/Fonts/simsun.ttc",
    r"C:/Windows/Fonts/NotoSansSC-VF.ttf",
    r"C:/Windows/Fonts/simhei.ttf",
]


def _find_cjk_font() -> str | None:
    for p in _CJK_FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    # 兜底：在仓库内查找常见中文字体
    for root, _dirs, files in os.walk(_PROJECT_ROOT):
        for f in files:
            if f.lower() in ("simhei.ttf", "msyh.ttc", "simsun.ttc", "notosanssc.ttf"):
                return os.path.join(root, f)
    return None


def _natural_chapter_order(chapter_ids):
    """按章节编号自然排序：Ch1, Ch2 ... Ch10（避免字典序 Ch10 < Ch2）"""
    def key(cid):
        m = re.search(r"(\d+)", str(cid))
        return (0, int(m.group(1))) if m else (1, str(cid))
    return sorted(chapter_ids, key=key)


def _pdf_line(pdf, line: str, base_size: int = 11):
    """按行渲染：# 标题加粗放大，## 次级标题，其余正文。"""
    pdf.set_x(pdf.l_margin)
    stripped = line.lstrip()
    if stripped.startswith("# "):
        pdf.set_font("CJK", size=base_size + 5)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, base_size + 3, stripped[2:])
        pdf.ln(2)
        pdf.set_font("CJK", size=base_size)
    elif stripped.startswith("## "):
        pdf.set_font("CJK", size=base_size + 3)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, base_size + 1, stripped[3:])
        pdf.ln(1)
        pdf.set_font("CJK", size=base_size)
    else:
        pdf.set_font("CJK", size=base_size)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, base_size + 1, line)


def generate_pdf(run_id: str) -> str | None:
    """从已完成章节编译 output.pdf。无章节时返回 None。

    重写后的论文为纯中文，因此必须嵌入 CJK 字体，否则 fpdf2 默认
    Helvetica 无法渲染汉字（会留空或报错）。
    """
    run_dir = _get_run_dir(run_id)
    if not os.path.isdir(run_dir):
        return None
    progress_path = os.path.join(run_dir, "progress.json")
    chapters: dict = {}
    if os.path.exists(progress_path):
        try:
            chapters = json.load(open(progress_path, "r", encoding="utf-8")).get("chapters", {})
        except Exception:
            chapters = {}
    if not chapters:
        return None

    font_path = _find_cjk_font()
    if not font_path:
        _log(f"[pdf {run_id}] 未找到中文字体，跳过 PDF 生成")
        return None

    meta: dict = {}
    meta_path = os.path.join(run_dir, "meta.json")
    if os.path.exists(meta_path):
        try:
            meta = json.load(open(meta_path, "r", encoding="utf-8"))
        except Exception:
            meta = {}
    title = meta.get("paper_title") or run_id

    from fpdf import FPDF
    pdf = FPDF()
    pdf.set_margins(20, 20, 20)
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_font("CJK", "", font_path)

    # 封面
    pdf.add_page()
    pdf.set_font("CJK", size=22)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(0, 12, title, align="C")
    pdf.ln(6)
    pdf.set_font("CJK", size=13)
    pdf.set_x(pdf.l_margin)
    pdf.multi_cell(0, 8, "重写版 · 论文重写 Agent 生成", align="C")
    created = meta.get("created_at")
    if created:
        try:
            import datetime
            ts = datetime.datetime.fromtimestamp(float(created)).strftime("%Y-%m-%d %H:%M")
            pdf.ln(2)
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(0, 8, ts, align="C")
        except Exception:
            pass

    # 大纲
    outline_path = os.path.join(run_dir, "outline.txt")
    if os.path.exists(outline_path):
        outline = open(outline_path, "r", encoding="utf-8").read().strip()
        if outline:
            pdf.add_page()
            pdf.set_font("CJK", size=16)
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(0, 10, "大纲")
            pdf.ln(2)
            for line in outline.split("\n"):
                line = line.rstrip()
                if not line:
                    pdf.ln(2)
                    continue
                _pdf_line(pdf, line, base_size=11)

    # 章节
    for cid in _natural_chapter_order(chapters.keys()):
        cpath = os.path.join(run_dir, "chapters", f"{cid}.txt")
        if not os.path.exists(cpath):
            continue
        content = open(cpath, "r", encoding="utf-8").read()
        pdf.add_page()
        pdf.set_font("CJK", size=17)
        pdf.set_x(pdf.l_margin)
        pdf.multi_cell(0, 10, str(cid))
        pdf.ln(2)
        for line in content.split("\n"):
            line = line.rstrip()
            if not line:
                pdf.ln(3)
                continue
            _pdf_line(pdf, line, base_size=11)

    out_path = os.path.join(run_dir, "output.pdf")
    pdf.output(out_path)
    return out_path


@app.get("/api/runs/{run_id}/pdf")
async def run_pdf(run_id: str):
    from fastapi.responses import FileResponse
    run_dir = _get_run_dir(run_id)
    p = os.path.join(run_dir, "output.pdf")
    if not os.path.exists(p):
        # 按需生成：存在章节就现场编译 PDF，避免历史会话无法下载
        progress_path = os.path.join(run_dir, "progress.json")
        has_chapters = False
        if os.path.exists(progress_path):
            try:
                has_chapters = bool(json.load(open(progress_path, "r", encoding="utf-8")).get("chapters"))
            except Exception:
                has_chapters = False
        if has_chapters:
            try:
                p = generate_pdf(run_id) or p
            except Exception as e:
                _log(f"[pdf {run_id}] 生成失败: {e}")
        if not p or not os.path.exists(p):
            return JSONResponse({"error": "该运行尚未生成章节，无法导出 PDF"}, status_code=404)
    return FileResponse(p, media_type="application/pdf", filename=f"{run_id}.pdf")


@app.post("/api/run")
async def start_run(req: RunRequest):
    global _run_thread
    if current_run["status"] == "running":
        if req.force:
            # 终止当前运行并解除其可能卡住的 HITL 等待，等旧线程退出后接管
            current_run["status"] = "stopped"
            current_run["ended_at"] = time.time()
            if current_run.get("awaiting"):
                _resume_value[0] = False
                _resume_event.set()
            t = _run_thread
            if t and t.is_alive():
                t.join(timeout=8)
        else:
            return JSONResponse({
                "error": "已有任务在运行中（可勾选接管或点击停止）",
                "run_id": current_run.get("run_id"),
                "awaiting": current_run.get("awaiting"),
            }, status_code=409)

    run_id = str(uuid.uuid4())[:8]
    current_run.update({
        "run_id": run_id,
        "status": "running",
        "error": "",
        "started_at": time.time(),
        "ended_at": None,
        "tool_calls": 0,
        "last_action": "启动中...",
        "auto_approve": req.auto_approve,
        "awaiting": None,
        "auto_tools": set(),
    })
    _resume_event.clear()

    thread = threading.Thread(
        target=_run_agent,
        args=(run_id, req.paper_title, req.original_text, req.target_audience, req.max_tool_calls),
        daemon=True,
    )
    _run_thread = thread
    thread.start()

    return {"run_id": run_id, "status": "started"}


@app.post("/api/stop")
async def stop_run():
    current_run["status"] = "stopped"
    current_run["ended_at"] = time.time()
    # 若正卡在 HITL 等待，注入 False 解除阻塞，运行线程随即退出
    if current_run.get("awaiting"):
        _resume_value[0] = False
        _resume_event.set()
    return {"status": "stopped"}


@app.post("/api/runs/{run_id}/resume")
async def resume_run(run_id: str, req: ResumeRequest):
    """人工决策：批准/跳过当前挂起的 HITL 中断；字符串则作为指示捎给 Agent"""
    if current_run.get("run_id") != run_id:
        return JSONResponse({"error": "run_id 不匹配"}, status_code=409)
    if not current_run.get("awaiting"):
        return JSONResponse({"error": "当前没有挂起的人工确认"}, status_code=409)

    tool = current_run["awaiting"].get("tool")
    _resume_value[0] = req.decision
    current_run["awaiting"] = None
    # 授权范围：同类操作本次运行免批
    if req.scope == "tool" and isinstance(req.decision, bool) and req.decision and tool:
        current_run.setdefault("auto_tools", set()).add(tool)
        _log(f"[Agent {run_id}] 已授权 {tool} 本次运行内自动批准")
    _resume_event.set()
    return {
        "status": "resumed",
        "decision": req.decision if isinstance(req.decision, bool) else "instructed",
        "auto_tools": sorted(current_run.get("auto_tools") or []),
    }


@app.post("/api/runs/{run_id}/auto_tools/revoke")
async def revoke_auto_tool(run_id: str, body: dict):
    """撤销某工具的本次运行免批授权（下次该工具触发时重新等待确认）"""
    if current_run.get("run_id") != run_id:
        return JSONResponse({"error": "run_id 不匹配"}, status_code=409)
    tool = str(body.get("tool", ""))
    tools: set = current_run.setdefault("auto_tools", set())
    tools.discard(tool)
    return {"status": "revoked", "auto_tools": sorted(tools)}


@app.get("/api/events")
async def event_stream(request: Request):
    queue: asyncio.Queue = asyncio.Queue()
    _sse_queues.append(queue)

    async def generate():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    evt = await asyncio.wait_for(queue.get(), timeout=30)
                    yield evt
                except asyncio.TimeoutError:
                    yield {"event": "heartbeat", "data": "{}"}
        finally:
            if queue in _sse_queues:
                _sse_queues.remove(queue)

    return EventSourceResponse(generate())


@app.get("/api/chapter/{run_id}/{chapter_id}")
async def get_chapter(run_id: str, chapter_id: str):
    chapter_path = os.path.join(_get_run_dir(run_id), "chapters", f"{chapter_id}.txt")
    if not os.path.exists(chapter_path):
        return JSONResponse({"error": f"{chapter_id} 不存在"}, status_code=404)
    with open(chapter_path, "r", encoding="utf-8") as f:
        content = f.read()
    return {"chapter_id": chapter_id, "content": content, "chars": len(content)}


@app.get("/api/output/{filename}")
async def get_output_file(filename: str):
    from fastapi.responses import FileResponse
    path = os.path.join(OUTPUT_DIR, filename)
    if os.path.exists(path):
        return FileResponse(path)
    return JSONResponse({"error": "文件不存在"}, status_code=404)


# ─── Agent 运行器 ───

def _run_agent(run_id: str, paper_title: str, original_text: str,
               target_audience: str, max_tool_calls: int):
    """在后台线程中执行 LangGraph Agent（最新架构）"""
    _log(f"[Agent {run_id}] 启动，原文{len(original_text)}字")
    _fire_sse("agent_start", {"run_id": run_id, "paper_title": paper_title})

    try:
        # 初始化run目录（原文为空时跳过写入，由 Agent 自行检索下载）
        set_current_run_id(run_id)
        init_run(run_id, original_text, paper_title=paper_title)

        # 构建图
        graph = build_agent_graph()

        # 初始消息：提供了原文 → 直接浏览重写；未提供 → 让 Agent 自己搜索下载
        if original_text.strip():
            first_message = (
                f"请开始重写论文《{paper_title}》。目标读者：{target_audience}。"
                f"原文长度：{len(original_text)}字。先浏览原文结构，然后生成大纲，逐章写作，最后生成PDF。"
            )
        else:
            first_message = (
                f"用户只提供了论文标题《{paper_title}》，没有提供原文。"
                f"请先用 search_paper 工具搜索这篇论文（目标读者：{target_audience}），"
                "从结果中选择标题最匹配的一篇，用 download_paper 下载（它会自动提取全文并保存）；"
                "然后浏览原文、生成大纲、逐章写作，最后 generate_pdf。现在开始。"
            )

        tool_call_count = 0

        config = {
            "configurable": {"thread_id": run_id},
            "recursion_limit": max_tool_calls * 2,
        }

        def extract_interrupt_info() -> dict:
            """从图检查点状态提取挂起中断的详情（供审批卡展示）"""
            try:
                snap = graph.get_state(config)
                for t in getattr(snap, "tasks", []) or []:
                    its = getattr(t, "interrupts", None)
                    if its:
                        val = getattr(its[0], "value", None)
                        if isinstance(val, dict):
                            return {
                                "tool": str(val.get("tool", "")),
                                "reason": str(val.get("reason", "")),
                                "args": json.dumps(val.get("args", {}), ensure_ascii=False)[:300],
                                "preview": str(val.get("preview", ""))[:800],
                            }
            except Exception as e:
                _log(f"[Agent {run_id}] 提取中断信息失败: {e}")
            return {"tool": "?", "reason": "工具执行前等待确认", "args": "", "preview": ""}

        def consume(stream_iter) -> bool:
            """消费一个 stream；返回是否以中断收尾"""
            nonlocal tool_call_count
            pending = False
            for event in stream_iter:
                if current_run["status"] == "stopped":
                    return False
                for node_name, node_output in event.items():
                    _log(f"[Agent {run_id}] {node_name}")
                    if node_name == "__interrupt__":
                        pending = True
                        continue
                    if not isinstance(node_output, dict):
                        continue
                    for msg in node_output.get("messages", []):
                        if isinstance(msg, AIMessage):
                            # 思考文本与工具调用可并存于同一条 AIMessage：都推送
                            if msg.content:
                                current_run["last_action"] = str(msg.content)[:200]
                                _fire_sse("agent_message", {
                                    "run_id": run_id,
                                    "content": str(msg.content)[:2000],
                                })
                            for tc in msg.tool_calls or []:
                                tool_call_count += 1
                                current_run["tool_calls"] = tool_call_count
                                current_run["last_action"] = f"调用 {tc['name']}"
                                _fire_sse("tool_call", {
                                    "run_id": run_id,
                                    "tool": tc["name"],
                                    "args": str(tc.get("args", ""))[:500],
                                    "count": tool_call_count,
                                })
                        elif isinstance(msg, ToolMessage):
                            _fire_sse("tool_result", {
                                "run_id": run_id,
                                "result": str(msg.content)[:800],
                            })
                        elif isinstance(msg, HumanMessage) and str(msg.content).startswith("[用户指示]"):
                            _fire_sse("agent_message", {
                                "run_id": run_id,
                                "content": str(msg.content),
                            })
            return pending

        # 主循环 + HITL 中断处理，优先级：
        #   1) 全自动模式(auto_approve) → 批准
        #   2) 该工具已有本次运行授权(auto_tools) → 免批放行
        #   3) 交互模式 → 推送审批卡（含内容预览），阻塞等待人工决策
        pending = consume(graph.stream(
            {"messages": [HumanMessage(content=first_message)]}, config, stream_mode="updates",
        ))
        resumes = 0
        while pending and current_run["status"] == "running":
            resumes += 1
            info = extract_interrupt_info()
            auto_tools: set = current_run.setdefault("auto_tools", set())
            if current_run.get("auto_approve"):
                decision: bool | str = True
                _log(f"[Agent {run_id}] 自动批准 HITL 中断 #{resumes}")
            elif info.get("tool") in auto_tools:
                decision = True
                _fire_sse("agent_message", {
                    "run_id": run_id,
                    "content": f"[自动批准] {info.get('tool')} 已获本次运行授权，免批放行",
                })
                _log(f"[Agent {run_id}] HITL 中断 #{resumes}: {info.get('tool')} 已授权，自动批准")
            else:
                current_run["awaiting"] = info
                _fire_sse("interrupt", {"run_id": run_id, **info})
                _log(f"[Agent {run_id}] HITL 等待人工决策 #{resumes}: {info.get('tool')}")
                _resume_event.clear()
                _resume_event.wait()          # 阻塞直至人工决策 / 停止注入解除
                decision = _resume_value[0]
                current_run["awaiting"] = None
                if current_run["status"] != "running":
                    break
                if isinstance(decision, bool):
                    _log(f"[Agent {run_id}] HITL 决策 #{resumes}: {'批准' if decision else '跳过'}")
                else:
                    _log(f"[Agent {run_id}] HITL 决策 #{resumes}: 批准并附指示（{len(decision)}字）")
            pending = consume(graph.stream(Command(resume=decision), config, stream_mode="updates"))

        if current_run["status"] != "stopped":
            current_run["status"] = "completed"

        # 读取最终章节信息
        run_dir = _get_run_dir(run_id)
        progress_path = os.path.join(run_dir, "progress.json")
        chapters_info = {}
        if os.path.exists(progress_path):
            with open(progress_path, "r", encoding="utf-8") as f:
                chapters_info = json.load(f).get("chapters", {})

        _fire_sse("agent_complete", {
            "run_id": run_id,
            "tool_calls": tool_call_count,
            "chapters": chapters_info,
        })
        _log(f"[Agent {run_id}] 完成，{tool_call_count}次工具调用，{len(chapters_info)}章")

        # 运行结束：尽量导出 PDF 产物（即使被手动停止，只要有章节就生成）
        try:
            if chapters_info:
                gp = generate_pdf(run_id)
                if gp:
                    _log(f"[Agent {run_id}] 已生成 PDF: {gp}")
                    _fire_sse("pdf_ready", {"run_id": run_id, "pdf": os.path.basename(gp)})
        except Exception as e:
            _log(f"[Agent {run_id}] PDF 生成失败: {e}")

    except Exception as e:
        current_run["status"] = "error"
        current_run["error"] = str(e)
        _log(f"[Agent {run_id}] 错误: {e}")
        import traceback
        _log(traceback.format_exc())
        _fire_sse("agent_error", {"run_id": run_id, "error": str(e)})

    finally:
        current_run["ended_at"] = time.time()


if __name__ == "__main__":
    import uvicorn
    print(f"🚀 论文重写 Agent Dashboard (LangGraph 最新架构)")
    print(f"   地址: http://localhost:{SERVER_PORT}")
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT)
