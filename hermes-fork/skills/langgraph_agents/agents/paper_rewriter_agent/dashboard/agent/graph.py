"""LangGraph Agent — 最新架构版

使用 LangGraph 官方推荐组件：
- MessagesState（内置状态，自带messages键）
- ToolNode（预构建工具节点，自动处理并行执行和错误）
- @tool 装饰器定义工具
- llm.bind_tools() 自动处理tool_call消息格式

图结构：
  agent → (有tool_call?) → tools → agent → ...
                ↓ (无tool_call)
               END
"""
from __future__ import annotations
import os
import sys
import json
import time
import re
import threading
from typing import Literal

# 确保项目根目录在sys.path中
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
from langgraph.graph import MessagesState
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.types import interrupt


# ─────────────────────────────────────────────
# 工具定义（用 @tool 装饰器）
# ─────────────────────────────────────────────

# 运行目录辅助
# 与 Hermes langgraph_agent 调用的 paper_rewriter_agent (pr_config.RUNS_DIR)
# 保持一致，使 dashboard 启动的运行和对话启动的运行共享同一历史目录。
def _hermes_home() -> str:
    return os.environ.get("HERMES_HOME") or str(
        os.path.join(os.path.expanduser("~"), ".hermes_portable_data")
    )


_RUNS_DIR = os.environ.get("PR_RUNS_DIR") or os.path.join(
    _hermes_home(), "paper_rewriter_runs"
)


def _get_run_dir(run_id: str) -> str:
    d = os.path.join(_RUNS_DIR, run_id)
    os.makedirs(d, exist_ok=True)
    return d

def _log(msg: str):
    ts = time.strftime("%H:%M:%S")
    log_path = os.path.join(_PROJECT_ROOT, "agent.log")
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except OSError:
        pass


# 全局：当前run_id（由server设置）
_current_run_id: str = ""

# progress.json 并发保护锁（ToolNode并行执行工具时共用）
_progress_lock = threading.Lock()

def set_current_run_id(run_id: str):
    global _current_run_id
    _current_run_id = run_id


@tool
def search_original(query: str, context_chars: int = 2000) -> str:
    """搜索原文中包含关键词的段落，返回匹配片段及上下文。
    用于查找特定概念、术语、数据在原文中的位置。
    多个关键词用空格分隔（AND逻辑）。
    
    Args:
        query: 搜索关键词，多个词用空格分隔
        context_chars: 每个匹配周围的上下文字符数，默认2000
    """
    _log(f"search_original: query='{query}'")
    run_dir = _get_run_dir(_current_run_id)
    original_path = os.path.join(run_dir, "original.txt")
    
    if not os.path.exists(original_path):
        return "错误：原文文件不存在"
    
    with open(original_path, "r", encoding="utf-8") as f:
        text = f.read()
    
    keywords = query.strip().split()
    if not keywords:
        return "错误：搜索词为空"
    
    pattern = re.compile(re.escape(keywords[0]), re.IGNORECASE)
    matches = []
    
    for m in pattern.finditer(text):
        start = max(0, m.start() - context_chars)
        end = min(len(text), m.end() + context_chars)
        snippet = text[start:end]
        
        if all(re.search(re.escape(kw), snippet, re.IGNORECASE) for kw in keywords[1:]):
            matches.append({"position": m.start(), "snippet": snippet})
        
        if len(matches) >= 10:
            break
    
    if not matches:
        return f"未找到包含所有关键词 [{', '.join(keywords)}] 的段落。尝试单独搜索每个词。"
    
    result = f"找到 {len(matches)} 处匹配 [{', '.join(keywords)}]：\n\n"
    for i, match in enumerate(matches, 1):
        result += f"--- 匹配 {i} (位置 {match['position']}) ---\n{match['snippet']}\n\n"
    
    _log(f"search_original: 返回 {len(matches)} 处匹配, {len(result)} 字")
    return result


@tool
def read_original_segment(start_pct: float, end_pct: float) -> str:
    """按百分比位置读取原文的一段。用于浏览原文特定区域。
    
    Args:
        start_pct: 起始位置百分比 (0-100)
        end_pct: 结束位置百分比 (0-100)
    """
    _log(f"read_original_segment: {start_pct}%-{end_pct}%")
    run_dir = _get_run_dir(_current_run_id)
    original_path = os.path.join(run_dir, "original.txt")
    
    if not os.path.exists(original_path):
        return "错误：原文文件不存在"
    
    with open(original_path, "r", encoding="utf-8") as f:
        text = f.read()
    
    total = len(text)
    start = int(total * start_pct / 100)
    end = int(total * end_pct / 100)
    
    segment = text[start:end]
    _log(f"read_original_segment: 返回 {len(segment)} 字")
    return segment


@tool
def write_chapter(chapter_id: str, content: str) -> str:
    """写入或覆写一个章节。内容会立即持久化到磁盘。禁止使用markdown格式符号。
    每章至少3000字，充分展开不要压缩。

    Args:
        chapter_id: 章节ID，如 Ch1, Ch2
        content: 章节内容，纯文本，禁止markdown
    """
    _log(f"write_chapter: {chapter_id}, {len(content)} 字")

    # ── HITL: 确认后才写入；字符串决策 = 批准并附带用户指示 ──
    decision = interrupt({
        "tool": "write_chapter",
        "reason": f"即将写入章节 {chapter_id}（{len(content)} 字）",
        "args": {"chapter_id": chapter_id, "chars": len(content)},
        "preview": content[:600],
    })
    if str(decision).lower() in ("no", "n", "skip"):
        _log(f"write_chapter: 用户取消 ({decision})")
        return f"用户取消了写入 {chapter_id}"

    run_dir = _get_run_dir(_current_run_id)
    chapters_dir = os.path.join(run_dir, "chapters")
    os.makedirs(chapters_dir, exist_ok=True)

    chapter_path = os.path.join(chapters_dir, f"{chapter_id}.txt")

    # progress.json 是共享状态：并行工具调用下必须加锁 + 容错 + 原子替换
    with _progress_lock:
        with open(chapter_path, "w", encoding="utf-8") as f:
            f.write(content)

        progress_path = os.path.join(run_dir, "progress.json")
        progress = {"chapters": {}, "started_at": time.time()}
        if os.path.exists(progress_path):
            try:
                with open(progress_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    progress = loaded
            except (json.JSONDecodeError, OSError):
                _log("write_chapter: progress.json 损坏，重建")
        if not isinstance(progress.get("chapters"), dict):
            progress["chapters"] = {}

        progress["chapters"][chapter_id] = {"chars": len(content), "written_at": time.time()}
        progress["last_updated"] = time.time()

        tmp_path = progress_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(progress, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, progress_path)

    # 字符串决策 = 用户附加指示，必须回到模型上下文
    note = ""
    if not isinstance(decision, bool) and str(decision).strip():
        note = f"\n\n[用户附加指示] {decision}\n（请认真对待：如涉及本章内容，立即用 write_chapter 覆写落实）"
    return f"已保存 {chapter_id}，{len(content)} 字{note}"


@tool
def read_chapter(chapter_id: str) -> str:
    """读取一个已写章节的完整内容。
    
    Args:
        chapter_id: 章节ID
    """
    run_dir = _get_run_dir(_current_run_id)
    chapter_path = os.path.join(run_dir, "chapters", f"{chapter_id}.txt")
    
    if not os.path.exists(chapter_path):
        return f"错误：{chapter_id} 尚未写入"
    
    with open(chapter_path, "r", encoding="utf-8") as f:
        return f.read()


@tool
def list_chapters() -> str:
    """列出所有已写章节及其字数。"""
    run_dir = _get_run_dir(_current_run_id)
    progress_path = os.path.join(run_dir, "progress.json")
    
    if not os.path.exists(progress_path):
        return "尚无已写章节"
    
    with open(progress_path, "r", encoding="utf-8") as f:
        progress = json.load(f)
    
    chapters = progress.get("chapters", {})
    if not chapters:
        return "尚无已写章节"
    
    total = 0
    lines = []
    for ch_id in sorted(chapters.keys(), key=lambda x: int(re.search(r'\d+', x).group()) if re.search(r'\d+', x) else 0):
        info = chapters[ch_id]
        lines.append(f"  {ch_id}: {info['chars']} 字")
        total += info["chars"]
    
    return f"已写 {len(chapters)} 章，共 {total} 字：\n" + "\n".join(lines)


@tool
def self_review_chapter(chapter_id: str) -> str:
    """对单章进行自审，获取该章内容和大纲要求的对比材料。返回后由你自行判断质量。
    
    Args:
        chapter_id: 要审查的章节ID
    """
    _log(f"self_review_chapter: {chapter_id}")
    run_dir = _get_run_dir(_current_run_id)
    
    chapter_path = os.path.join(run_dir, "chapters", f"{chapter_id}.txt")
    if not os.path.exists(chapter_path):
        return f"错误：{chapter_id} 尚未写入"
    
    with open(chapter_path, "r", encoding="utf-8") as f:
        chapter_content = f.read()
    
    # 读大纲
    outline_path = os.path.join(run_dir, "outline.txt")
    outline = ""
    if os.path.exists(outline_path):
        with open(outline_path, "r", encoding="utf-8") as f:
            outline = f.read()
    
    ch_section = ""
    if outline:
        pattern = re.compile(rf"({chapter_id}[:\s].*?)(?=Ch\d+|$)", re.DOTALL)
        m = pattern.search(outline)
        if m:
            ch_section = m.group(1).strip()
    
    result = f"=== {chapter_id} 自审材料 ===\n\n"
    result += f"大纲要求：\n{ch_section}\n\n"
    result += f"章节内容（{len(chapter_content)} 字）：\n{chapter_content}\n\n"
    result += "请对比大纲要求和原文，检查：\n"
    result += "1. 概念是否覆盖完整\n2. 技术细节是否准确\n3. 是否有幻觉\n"
    result += "4. 行文是否通俗流畅\n5. 长度是否足够展开\n"
    
    return result


@tool
def save_outline(outline_text: str) -> str:
    """保存章节大纲到磁盘。
    
    Args:
        outline_text: 大纲内容
    """
    # ── HITL: 确认后才保存 ──
    decision = interrupt({
        "tool": "save_outline",
        "reason": f"即将保存大纲（{len(outline_text)} 字）",
        "args": {"chars": len(outline_text)},
        "preview": outline_text[:900],
    })
    if str(decision).lower() in ("no", "n", "skip"):
        _log(f"save_outline: 用户取消 ({decision})")
        return "用户取消了保存大纲"

    run_dir = _get_run_dir(_current_run_id)
    outline_path = os.path.join(run_dir, "outline.txt")
    with open(outline_path, "w", encoding="utf-8") as f:
        f.write(outline_text)
    _log(f"save_outline: {len(outline_text)} 字")
    note = ""
    if not isinstance(decision, bool) and str(decision).strip():
        note = f"\n\n[用户附加指示] {decision}\n（请认真对待：如涉及大纲结构，用 save_outline 重新保存后再开始写作）"
    return f"大纲已保存，{len(outline_text)} 字{note}"


@tool
def search_paper(query: str, max_results: int = 3) -> str:
    """搜索论文。使用arXiv、Semantic Scholar、CrossRef、PubMed等多个学术搜索源。
    
    Args:
        query: 论文标题或搜索关键词
        max_results: 每个源的最大返回数（默认3）
    
    Returns:
        论文列表，包含标题、作者、摘要、PDF链接等信息
    """
    _log(f"search_paper: query='{query}', max_results={max_results}")
    
    try:
        from .paper_search import search_papers
        papers = search_papers(query, max_results)
        
        if not papers:
            return f"未找到与'{query}'相关的论文。建议尝试英文关键词或更具体的论文标题。"
        
        result = f"找到 {len(papers)} 篇相关论文：\n\n"
        for i, paper in enumerate(papers, 1):
            result += f"{i}. {paper['title']}\n"
            result += f"   作者: {paper['authors']}\n"
            result += f"   发表: {paper['published']} | 来源: {paper['source']}\n"
            result += f"   ID: {paper['id']}\n"
            if paper.get('pdf_url'):
                result += f"   PDF: {paper['pdf_url']}\n"
            if paper.get('abstract'):
                result += f"   摘要: {paper['abstract'][:200]}...\n"
            result += "\n"
        
        return result
    except Exception as e:
        _log(f"search_paper error: {e}")
        return f"搜索失败: {str(e)}"


@tool
def download_paper(paper_id: str, source: str = "arxiv") -> str:
    """下载论文PDF并提取文本内容。
    
    Args:
        paper_id: 论文ID（arXiv ID、DOI等）
        source: 来源（arxiv, semantic_scholar, crossref, pubmed）
    
    Returns:
        提取的文本内容或错误信息
    """
    _log(f"download_paper: paper_id='{paper_id}', source='{source}'")
    
    # ── HITL: 确认后才下载 ──
    decision = interrupt({
        "tool": "download_paper",
        "reason": f"即将下载论文 '{paper_id}' (来源: {source})",
        "args": {"paper_id": paper_id, "source": source},
    })
    if str(decision).lower() in ("no", "n", "skip"):
        _log(f"download_paper: 用户取消 ({decision})")
        return "用户取消了下载操作"
    
    try:
        from .paper_search import download_paper as dl_paper
        result = dl_paper(paper_id, _current_run_id, source)
        
        if not result['success']:
            return result['message']
        
        # 保存原文到run目录
        if result.get('text'):
            run_dir = _get_run_dir(_current_run_id)
            original_path = os.path.join(run_dir, "original.txt")
            with open(original_path, "w", encoding="utf-8") as f:
                f.write(result['text'])
            _log(f"download_paper: saved {len(result['text'])} chars to original.txt")

        note = ""
        if not isinstance(decision, bool) and str(decision).strip():
            note = f"\n\n[用户附加指示] {decision}"
        return result['message'] + note
    except Exception as e:
        _log(f"download_paper error: {e}")
        return f"下载失败: {str(e)}"


@tool
def generate_pdf(run_id: str = "") -> str:
    """将所有已写章节合并生成一个PDF文件。写完所有章节后调用此工具生成最终PDF。

    Args:
        run_id: 运行ID，为空则使用当前运行ID
    """
    target_run_id = run_id or _current_run_id
    _log(f"generate_pdf: run_id='{target_run_id}'")

    run_dir = _get_run_dir(target_run_id)
    chapters_dir = os.path.join(run_dir, "chapters")

    if not os.path.isdir(chapters_dir):
        return "错误：章节目录不存在"

    # Collect chapter files sorted by number
    chapter_files = sorted(
        [f for f in os.listdir(chapters_dir) if f.endswith(".txt")],
        key=lambda x: int(re.search(r'\d+', x).group()) if re.search(r'\d+', x) else 0,
    )
    if not chapter_files:
        return "错误：没有找到任何章节文件"

    try:
        from fpdf import FPDF

        # Find a CJK font
        font_path = None
        candidates = [
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "C:\\Windows\\Fonts\\simhei.ttf",
            "C:\\Windows\\Fonts\\msyh.ttc",
        ]
        for fp in candidates:
            if os.path.exists(fp):
                font_path = fp
                break

        # If no known font, try to find any CJK font
        if not font_path:
            import glob
            for pattern in [
                "/usr/share/fonts/**/*CJK*",
                "/usr/share/fonts/**/*wqy*",
                "/usr/share/fonts/**/*noto*",
                "/usr/share/fonts/**/*droid*",
            ]:
                matches = glob.glob(pattern, recursive=True)
                if matches:
                    font_path = matches[0]
                    break

        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=15)

        # Add CJK font once if available
        use_cjk = bool(font_path)
        if use_cjk:
            pdf.add_font("CJK", "", font_path, uni=True)

        for ch_file in chapter_files:
            ch_path = os.path.join(chapters_dir, ch_file)
            with open(ch_path, "r", encoding="utf-8") as f:
                text = f.read()

            ch_name = os.path.splitext(ch_file)[0]
            pdf.add_page()

            # Chapter title
            if use_cjk:
                pdf.set_font("CJK", size=18)
            else:
                pdf.set_font("Helvetica", "B", 18)
            pdf.cell(0, 12, ch_name, ln=True, align="C")
            pdf.ln(6)

            # Chapter body
            if use_cjk:
                pdf.set_font("CJK", size=11)
            else:
                pdf.set_font("Helvetica", size=11)
            pdf.multi_cell(0, 6, text)

        output_path = os.path.join(run_dir, "output.pdf")
        pdf.output(output_path)
        size_kb = os.path.getsize(output_path) / 1024
        _log(f"generate_pdf: saved {output_path} ({size_kb:.0f} KB)")
        return f"PDF已生成: {output_path} ({size_kb:.0f} KB, {len(chapter_files)} 章)"

    except Exception as e:
        _log(f"generate_pdf error: {e}")
        import traceback
        _log(traceback.format_exc())
        return f"PDF生成失败: {str(e)}"


# 工具列表
tools = [search_original, read_original_segment, write_chapter, read_chapter, list_chapters, self_review_chapter, save_outline, search_paper, download_paper, generate_pdf]
tools_by_name = {t.name: t for t in tools}


# ─────────────────────────────────────────────
# 初始化run（保存原文到磁盘）
# ─────────────────────────────────────────────
def init_run(run_id: str, original_text: str, paper_title: str = ""):
    """初始化run目录；原文为空时不落盘，等 download_paper 自行创建"""
    run_dir = _get_run_dir(run_id)

    if original_text:
        original_path = os.path.join(run_dir, "original.txt")
        with open(original_path, "w", encoding="utf-8") as f:
            f.write(original_text)

    meta = {
        "run_id": run_id,
        "paper_title": paper_title,
        "original_chars": len(original_text),
        "created_at": time.time(),
    }
    with open(os.path.join(run_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # 预置合法的progress.json，避免并行工具首次读空/损坏文件
    progress_path = os.path.join(run_dir, "progress.json")
    if not os.path.exists(progress_path):
        with open(progress_path, "w", encoding="utf-8") as f:
            json.dump({"chapters": {}, "started_at": time.time()}, f, ensure_ascii=False)

    _log(f"init_run: {run_id}, 原文{len(original_text)}字")


# ─────────────────────────────────────────────
# LLM（带bind_tools）
# ─────────────────────────────────────────────
def _get_llm_with_tools():
    """获取绑定了工具的LLM"""
    from langchain_openai import ChatOpenAI
    from pipeline.config import LLM_BASE_URL, LLM_API_KEY, LLM_MODEL
    
    llm = ChatOpenAI(
        base_url=LLM_BASE_URL,
        api_key=LLM_API_KEY,
        model=LLM_MODEL,
        temperature=0.4,
        # 章节内容以tool_call参数传输，3000+字中文需要充足输出空间
        max_tokens=int(os.getenv("AGENT_MAX_TOKENS", "16384")),
        timeout=180,
    )
    return llm.bind_tools(tools)


# ─────────────────────────────────────────────
# Agent 节点
# ─────────────────────────────────────────────
SYSTEM_PROMPT = """你是论文重写助手，运行在本地终端中。

严格行为规则：
- 如果用户消息是问候（hello、hi、你好等）或闲聊，你必须直接回复，绝对不能调用任何工具
- 只有当用户明确说出"搜索论文"、"重写论文"、"下载论文"、或提供具体论文标题/链接时，才能调用工具
- 不确定时，先问用户要做什么，不要擅自行动

工作流程（仅在用户明确要求时启动）：
1. 先用 search_original 和 read_original_segment 浏览原文，理解整体结构
2. 用 save_outline 保存章节大纲（根据原文长度动态调整章节数：每1-2万字原文对应1章重写）
3. 严格串行逐章写作：每次回复只允许调用一个 write_chapter，写完并自审通过后再写下一章，禁止一次并行写多章
4. 每章至少3000字（不足会被审查退回），充分展开不要压缩
5. 每写完一章，用 self_review_chapter 自审
6. 如果自审发现问题，用 search_original 查原文确认，然后用 write_chapter 覆写
7. 写完所有章节后，用 list_chapters 确认覆盖情况
8. 全部完成后调用 generate_pdf 生成PDF，告知用户PDF路径

写作规则：
- 中文输出，术语首次出现时括号注英文原文
- 纯散文体，禁止使用任何markdown符号
- 每章至少3000字，充分展开不要压缩
- 行文流畅自然，像跟朋友聊天一样解释概念"""


def agent_node(state: MessagesState) -> dict:
    """Agent 节点：调用LLM决定下一步。"""
    from .event_log import log_event
    log_event("STEP_STARTED", {"stepName": "agent"})
    _log(f"agent_node: {len(state['messages'])} messages")
    
    llm_with_tools = _get_llm_with_tools()
    
    # 添加system prompt（如果还没有）
    messages = state["messages"]
    has_system = any(isinstance(m, SystemMessage) for m in messages)
    
    if not has_system:
        messages = [SystemMessage(content=SYSTEM_PROMPT)] + messages
    
    response = llm_with_tools.invoke(messages)
    _log(f"agent_node: response tool_calls={bool(response.tool_calls)}, content_len={len(response.content or '')}")

    # ETCLOVG: 记录token使用量
    try:
        from etclovg.governance import record_usage, extract_usage_from_response
        from etclovg.versioning import get_version_info
        inp, out = extract_usage_from_response(response)
        if inp > 0 or out > 0:
            record_usage(
                model=LLM_MODEL,
                input_tokens=inp, output_tokens=out,
                node="agent",
                prompt_version=get_version_info().get("current_version", ""),
            )
    except Exception:
        pass  # 不影响主流程
    
    # 记录tool_calls信息
    if response.tool_calls:
        for tc in response.tool_calls:
            log_event("TOOL_CALL_START", {
                "toolCallId": tc.get("id", ""),
                "name": tc.get("name", ""),
                "args": str(tc.get("args", ""))[:200],
            })
    
    log_event("STEP_FINISHED", {"stepName": "agent"})
    return {"messages": [response]}


# ─────────────────────────────────────────────
# 条件路由
# ─────────────────────────────────────────────
def should_continue(state: MessagesState) -> Literal["tools", "__end__"]:
    """判断是否继续：最后一条AIMessage有tool_calls就走tools，否则结束。"""
    messages = state["messages"]
    last_message = messages[-1]
    
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return "tools"
    return "__end__"


# ─────────────────────────────────────────────
# 构建图
# ─────────────────────────────────────────────
def build_agent_graph(checkpointer=None):
    """构建LangGraph agent图（最新架构）。"""
    from langgraph.checkpoint.memory import InMemorySaver
    from .event_log import log_event
    
    builder = StateGraph(MessagesState)
    
    # 包装ToolNode，记录工具完成事件
    _tool_node = ToolNode(tools)
    
    def logged_tools_node(state):
        log_event("STEP_STARTED", {"stepName": "tools"})
        result = _tool_node.invoke(state)
        # 记录每个工具的结果
        for msg in result.get("messages", []):
            if hasattr(msg, "name") and msg.name:
                log_event("TOOL_CALL_END", {
                    "toolCallId": getattr(msg, "tool_call_id", ""),
                    "name": msg.name,
                    "result": str(getattr(msg, "content", ""))[:200],
                })
        log_event("STEP_FINISHED", {"stepName": "tools"})
        return result
    
    builder.add_node("agent", agent_node)
    builder.add_node("tools", logged_tools_node)
    
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", should_continue, ["tools", END])
    builder.add_edge("tools", "agent")
    
    if checkpointer is None:
        checkpointer = InMemorySaver()
    
    return builder.compile(checkpointer=checkpointer)


# ─────────────────────────────────────────────
# 独立审查节点 (改进1: 同模型独立session)
# ─────────────────────────────────────────────
REVIEW_SYSTEM_PROMPT = """你是严格的学术审稿人。你的任务是审查论文章节的质量。

审查标准：
1. 内容准确性：是否忠实于原文？
2. 通俗性：非理工科大一学生能否理解？
3. 结构性：章节结构是否清晰？
4. 完整性：是否覆盖了原文要点？
5. 字数：是否达到3000字以上？

输出格式：
评分：X/10
通过：是/否
问题：（列出具体问题）
建议：（改进建议）"""

def review_node(state: MessagesState):
    """独立审查节点：用同模型独立session审查章节质量。"""
    from .event_log import log_event

    log_event("STEP_STARTED", {"stepName": "review"})

    messages = state["messages"]

    # write_chapter 的 ToolMessage 只含确认文本（"已保存 ChN，N 字"），
    # 章节正文要从磁盘读：先从确认文本提取章节ID
    chapter_id = None
    for msg in reversed(messages):
        if isinstance(msg, ToolMessage) and msg.name == "write_chapter":
            m = re.search(r"已保存\s+(\S+?)\s*[，,]", str(msg.content))
            if m:
                chapter_id = m.group(1)
            break

    chapter_content = ""
    if chapter_id:
        chapter_path = os.path.join(_get_run_dir(_current_run_id), "chapters", f"{chapter_id}.txt")
        if os.path.exists(chapter_path):
            with open(chapter_path, "r", encoding="utf-8") as f:
                chapter_content = f.read()

    if not chapter_content:
        log_event("STEP_FINISHED", {"stepName": "review"})
        return {"messages": [AIMessage(content="✅ 审查通过。（未找到需审查的章节，跳过）")]}

    # 创建独立的审查LLM (同模型，但不同temperature)
    from langchain_openai import ChatOpenAI
    from pipeline.config import LLM_BASE_URL, LLM_API_KEY, LLM_MODEL
    review_llm = ChatOpenAI(
        base_url=LLM_BASE_URL,
        api_key=LLM_API_KEY,
        model=LLM_MODEL,
        temperature=0.3,  # 更严格的temperature
    )

    # 独立session: 新的对话历史
    review_messages = [
        SystemMessage(content=REVIEW_SYSTEM_PROMPT),
        HumanMessage(content=f"请审查以下章节（{chapter_id}，共{len(chapter_content)}字）：\n\n{chapter_content[:6000]}")
    ]
    
    response = review_llm.invoke(review_messages)
    review_result = response.content
    
    log_event("TOOL_CALL_START", {"name": "review", "args": ""})
    log_event("TOOL_CALL_END", {"name": "review", "result": review_result[:200]})
    log_event("STEP_FINISHED", {"stepName": "review"})
    
    # 解析审查结果
    passed = "通过：是" in review_result or "评分：8" in review_result or "评分：9" in review_result or "评分：10" in review_result

    # 防无限循环：累计3次未通过后强制放行（质量由最终PDF把关）
    fail_count = sum(1 for m in messages if isinstance(m, AIMessage) and "审查未通过" in str(m.content))
    if not passed and fail_count >= 3:
        review_result += "\n\n（已连续多次未通过，达到重写上限，本轮放行）"
        passed = True
    
    if passed:
        return {"messages": [AIMessage(content=f"✅ 审查通过。\n\n{review_result}")]}
    else:
        return {"messages": [AIMessage(content=f"⚠️ 审查未通过，需要重写。\n\n{review_result}")]}


# ─────────────────────────────────────────────
# 质量阈值检查 (改进2)
# ─────────────────────────────────────────────
def quality_check(state: MessagesState) -> Literal["pass", "fail"]:
    """检查审查结果是否通过质量阈值。"""
    messages = state["messages"]
    last_message = messages[-1]
    
    if isinstance(last_message, AIMessage):
        content = last_message.content
        # 检查是否通过
        if "✅ 审查通过" in content:
            return "pass"
        if "⚠️ 审查未通过" in content:
            return "fail"
    
    return "pass"  # 默认通过


# ─────────────────────────────────────────────
# 流程顺序检查 (改进3)
# ─────────────────────────────────────────────
def check_flow_order(state: MessagesState) -> bool:
    """检查是否遵循大纲→写作→审查的顺序。"""
    messages = state["messages"]
    
    has_outline = False
    has_chapter = False
    has_review = False
    
    for msg in messages:
        if isinstance(msg, ToolMessage):
            if msg.name == "save_outline":
                has_outline = True
            elif msg.name == "write_chapter":
                has_chapter = True
            elif msg.name == "review":
                has_review = True
    
    # 如果写了章节但没有大纲，违反流程
    if has_chapter and not has_outline:
        return False
    
    return True


# ─────────────────────────────────────────────
# 重试机制 (改进4)
# ─────────────────────────────────────────────
MAX_RETRIES = 3

def should_retry(state: MessagesState) -> Literal["retry", "continue"]:
    """判断是否需要重试写作。"""
    messages = state["messages"]
    
    # 统计重试次数
    retry_count = 0
    for msg in messages:
        if isinstance(msg, AIMessage) and "⚠️ 审查未通过" in msg.content:
            retry_count += 1
    
    if retry_count >= MAX_RETRIES:
        return "continue"  # 达到最大重试次数，继续
    
    # 检查最近的审查结果
    last_message = messages[-1]
    if isinstance(last_message, AIMessage) and "⚠️ 审查未通过" in last_message.content:
        return "retry"
    
    return "continue"


# ─────────────────────────────────────────────
# 更新后的图结构
# ─────────────────────────────────────────────
def build_agent_graph_v2(checkpointer=None):
    """构建改进后的LangGraph agent图。"""
    from langgraph.checkpoint.memory import InMemorySaver
    from .event_log import log_event
    
    builder = StateGraph(MessagesState)
    
    # 工具报错不炸图：错误信息回传给agent自行恢复
    _tool_node = ToolNode(tools, handle_tool_errors=True)

    def logged_tools_node(state):
        log_event("STEP_STARTED", {"stepName": "tools"})
        result = _tool_node.invoke(state)
        for msg in result.get("messages", []):
            if hasattr(msg, "name") and msg.name:
                log_event("TOOL_CALL_END", {
                    "toolCallId": getattr(msg, "tool_call_id", ""),
                    "name": msg.name,
                    "result": str(getattr(msg, "content", ""))[:200],
                })
        log_event("STEP_FINISHED", {"stepName": "tools"})
        return result

    # 添加节点
    builder.add_node("agent", agent_node)
    builder.add_node("tools", logged_tools_node)
    builder.add_node("review", review_node)
    
    # 添加边
    builder.add_edge(START, "agent")
    
    # agent → tools 或 END
    builder.add_conditional_edges("agent", should_continue, ["tools", END])
    
    # tools → review (如果刚写了章节) 或 agent
    def after_tools(state: MessagesState) -> Literal["review", "agent"]:
        """工具执行后：如果是write_chapter则审查，否则继续agent。"""
        messages = state["messages"]
        last_msg = messages[-1]
        if isinstance(last_msg, ToolMessage) and last_msg.name == "write_chapter":
            return "review"
        return "agent"
    
    builder.add_conditional_edges("tools", after_tools, ["review", "agent"])
    
    # review → pass/fail
    builder.add_conditional_edges("review", quality_check, {
        "pass": "agent",      # 通过：继续agent
        "fail": "agent",      # 未通过：也回agent（agent会重写）
    })
    
    if checkpointer is None:
        checkpointer = InMemorySaver()
    
    return builder.compile(checkpointer=checkpointer)


# 保持向后兼容
def build_agent_graph(checkpointer=None):
    """构建LangGraph agent图（兼容旧版）。"""
    return build_agent_graph_v2(checkpointer)


# ─────────────────────────────────────────────
# 运行入口
# ─────────────────────────────────────────────
def run_agent(paper_title: str, original_text: str, target_audience: str = "大一非理工科学生",
              run_id: str = None, max_tool_calls: int = 200) -> str:
    """运行论文重写agent。"""
    if not run_id:
        run_id = str(int(time.time()))[-8:]
    
    _log(f"run_agent 开始: {run_id}, 原文{len(original_text)}字")
    
    # 初始化run目录
    set_current_run_id(run_id)
    init_run(run_id, original_text, paper_title=paper_title)
    
    # 构建初始消息
    audience = target_audience
    first_message = f"请开始重写论文《{paper_title}》。目标读者：{audience}。原文长度：{len(original_text)}字。先浏览原文结构，然后生成大纲。"
    
    # 运行图
    graph = build_agent_graph()
    
    try:
        result = graph.invoke(
            {"messages": [HumanMessage(content=first_message)]},
            {"recursion_limit": max_tool_calls * 2},
        )
        _log(f"run_agent 完成: {run_id}")
    except Exception as e:
        _log(f"run_agent 异常: {type(e).__name__}: {e}")
        import traceback
        _log(traceback.format_exc())
        raise
    
    return run_id
