"""Agent 工具层 —— 搜索原文、写章节、读章节、事实核查

每个工具是纯函数，接受明确参数，返回字符串结果。
Agent 通过 function calling 调用这些工具。
"""
from __future__ import annotations
import os
import re
import json
import time
from pathlib import Path

_PIPELINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RUNS_DIR = os.path.join(_PIPELINE_DIR, "runs")


def _log(msg: str):
    ts = time.strftime("%H:%M:%S")
    log_path = os.path.join(_PIPELINE_DIR, "agent.log")
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except OSError:
        pass


def _get_run_dir(run_id: str) -> str:
    d = os.path.join(_RUNS_DIR, run_id)
    os.makedirs(d, exist_ok=True)
    return d


# ─────────────────────────────────────────────
# 工具 1: 搜索原文
# ─────────────────────────────────────────────
def search_original(run_id: str, query: str, context_chars: int = 2000) -> str:
    """搜索原文中包含关键词的段落，返回匹配片段及上下文。
    
    Args:
        run_id: 运行ID
        query: 搜索关键词（支持多个词，空格分隔，AND逻辑）
        context_chars: 每个匹配周围的上下文字符数
    
    Returns:
        匹配片段列表，格式化为可读文本
    """
    _log(f"search_original: query='{query}', context_chars={context_chars}")
    run_dir = _get_run_dir(run_id)
    original_path = os.path.join(run_dir, "original.txt")
    
    if not os.path.exists(original_path):
        return "错误：原文文件不存在"
    
    with open(original_path, "r", encoding="utf-8") as f:
        text = f.read()
    
    keywords = query.strip().split()
    if not keywords:
        return "错误：搜索词为空"
    
    # 找到所有关键词都出现的位置（AND逻辑）
    matches = []
    # 用第一个关键词定位，然后检查其他关键词是否在附近出现
    pattern = re.compile(re.escape(keywords[0]), re.IGNORECASE)
    
    for m in pattern.finditer(text):
        start = max(0, m.start() - context_chars)
        end = min(len(text), m.end() + context_chars)
        snippet = text[start:end]
        
        # 检查其他关键词是否在这个snippet中
        if all(re.search(re.escape(kw), snippet, re.IGNORECASE) for kw in keywords[1:]):
            # 标记匹配位置
            matches.append({
                "position": m.start(),
                "snippet": snippet,
            })
        
        if len(matches) >= 10:  # 最多返回10个匹配
            break
    
    if not matches:
        return f"未找到包含所有关键词 [{', '.join(keywords)}] 的段落。尝试单独搜索每个词。"
    
    result = f"找到 {len(matches)} 处匹配 [{', '.join(keywords)}]：\n\n"
    for i, match in enumerate(matches, 1):
        result += f"--- 匹配 {i} (位置 {match['position']}) ---\n"
        result += match["snippet"] + "\n\n"
    
    _log(f"search_original: 返回 {len(matches)} 处匹配, {len(result)} 字")
    return result


# ─────────────────────────────────────────────
# 工具 2: 读取原文段落
# ─────────────────────────────────────────────
def read_original_segment(run_id: str, start_pct: float = 0, end_pct: float = 100) -> str:
    """按百分比读取原文的一段（用于浏览特定章节区域）。
    
    Args:
        run_id: 运行ID
        start_pct: 起始位置百分比 (0-100)
        end_pct: 结束位置百分比 (0-100)
    
    Returns:
        该段原文文本
    """
    _log(f"read_original_segment: {start_pct}%-{end_pct}%")
    run_dir = _get_run_dir(run_id)
    original_path = os.path.join(run_dir, "original.txt")
    
    if not os.path.exists(original_path):
        return "错误：原文文件不存在"
    
    with open(original_path, "r", encoding="utf-8") as f:
        text = f.read()
    
    total = len(text)
    start = int(total * start_pct / 100)
    end = int(total * end_pct / 100)
    
    # 调整到段落边界
    if start > 0:
        nl = text.find("\n", start)
        if nl != -1 and nl - start < 200:
            start = nl + 1
    if end < total:
        nl = text.find("\n", end)
        if nl != -1 and nl - end < 200:
            end = nl + 1
    
    segment = text[start:end]
    _log(f"read_original_segment: 返回 {len(segment)} 字 ({start_pct}%-{end_pct}%)")
    return segment


# ─────────────────────────────────────────────
# 工具 3: 写入/更新章节
# ─────────────────────────────────────────────
def write_chapter(run_id: str, chapter_id: str, content: str) -> str:
    """写入或覆写一个章节。立即持久化到磁盘。
    
    Args:
        run_id: 运行ID
        chapter_id: 章节ID (如 "Ch1", "Ch2")
        content: 章节内容（纯文本，禁止markdown格式符号）
    
    Returns:
        确认信息
    """
    _log(f"write_chapter: {chapter_id}, {len(content)} 字")
    run_dir = _get_run_dir(run_id)
    chapters_dir = os.path.join(run_dir, "chapters")
    os.makedirs(chapters_dir, exist_ok=True)
    
    chapter_path = os.path.join(chapters_dir, f"{chapter_id}.txt")
    with open(chapter_path, "w", encoding="utf-8") as f:
        f.write(content)
    
    # 更新进度文件
    _update_progress(run_id, chapter_id, len(content))
    
    return f"已保存 {chapter_id}，{len(content)} 字"


def _update_progress(run_id: str, chapter_id: str, char_count: int):
    """更新进度文件"""
    run_dir = _get_run_dir(run_id)
    progress_path = os.path.join(run_dir, "progress.json")
    
    if os.path.exists(progress_path):
        with open(progress_path, "r", encoding="utf-8") as f:
            progress = json.load(f)
    else:
        progress = {"chapters": {}, "started_at": time.time()}
    
    progress["chapters"][chapter_id] = {
        "chars": char_count,
        "written_at": time.time(),
    }
    progress["last_updated"] = time.time()
    
    with open(progress_path, "w", encoding="utf-8") as f:
        json.dump(progress, f, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────────
# 工具 4: 读取已写章节
# ─────────────────────────────────────────────
def read_chapter(run_id: str, chapter_id: str) -> str:
    """读取一个已写章节的内容。
    
    Args:
        run_id: 运行ID
        chapter_id: 章节ID
    
    Returns:
        章节内容，或错误信息
    """
    run_dir = _get_run_dir(run_id)
    chapter_path = os.path.join(run_dir, "chapters", f"{chapter_id}.txt")
    
    if not os.path.exists(chapter_path):
        return f"错误：{chapter_id} 尚未写入"
    
    with open(chapter_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    return content


# ─────────────────────────────────────────────
# 工具 5: 列出所有章节状态
# ─────────────────────────────────────────────
def list_chapters(run_id: str) -> str:
    """列出所有章节及其状态。
    
    Returns:
        章节列表和字数统计
    """
    run_dir = _get_run_dir(run_id)
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


# ─────────────────────────────────────────────
# 工具 6: 自审单章
# ─────────────────────────────────────────────
def self_review_chapter(run_id: str, chapter_id: str) -> str:
    """对单章进行自审，对比原文检查质量和准确度。
    返回审查报告（不调用LLM，由agent自己判断）。
    这个工具只是准备好对比材料，实际判断由agent完成。
    
    Args:
        run_id: 运行ID
        chapter_id: 要审查的章节ID
    
    Returns:
        包含章节内容和相关原文片段的对比材料
    """
    _log(f"self_review_chapter: {chapter_id}")
    run_dir = _get_run_dir(run_id)
    
    # 读章节
    chapter_path = os.path.join(run_dir, "chapters", f"{chapter_id}.txt")
    if not os.path.exists(chapter_path):
        return f"错误：{chapter_id} 尚未写入"
    
    with open(chapter_path, "r", encoding="utf-8") as f:
        chapter_content = f.read()
    
    # 读大纲（如果有）
    outline_path = os.path.join(run_dir, "outline.txt")
    outline = ""
    if os.path.exists(outline_path):
        with open(outline_path, "r", encoding="utf-8") as f:
            outline = f.read()
    
    # 提取该章节在大纲中的描述
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
    result += "1. 概念是否覆盖完整\n2. 技术细节是否准确\n3. 是否有幻觉（添加了原文没有的内容）\n"
    result += "4. 行文是否通俗流畅\n5. 长度是否足够展开\n"
    
    return result


# ─────────────────────────────────────────────
# 工具初始化：保存原文和大纲到run目录
# ─────────────────────────────────────────────
def init_run(run_id: str, original_text: str, outline: str = "", paper_title: str = "") -> str:
    """初始化一个run的持久化目录。
    
    Args:
        run_id: 运行ID
        original_text: 原文全文
        outline: 大纲（可选，agent可以自己生成）
        paper_title: 论文标题
    
    Returns:
        确认信息
    """
    run_dir = _get_run_dir(run_id)
    
    # 保存原文
    original_path = os.path.join(run_dir, "original.txt")
    with open(original_path, "w", encoding="utf-8") as f:
        f.write(original_text)
    
    # 保存大纲（如果有）
    if outline:
        outline_path = os.path.join(run_dir, "outline.txt")
        with open(outline_path, "w", encoding="utf-8") as f:
            f.write(outline)
    
    # 保存元信息
    meta = {
        "run_id": run_id,
        "paper_title": paper_title,
        "original_chars": len(original_text),
        "created_at": time.time(),
    }
    meta_path = os.path.join(run_dir, "meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    
    _log(f"init_run: {run_id}, 原文{len(original_text)}字, 大纲{len(outline)}字")
    return f"Run {run_id} 已初始化。原文 {len(original_text)} 字已保存到 {original_path}"


# ─────────────────────────────────────────────
# 恢复：从磁盘加载已有进度
# ─────────────────────────────────────────────
def load_run_state(run_id: str) -> dict:
    """从磁盘加载run的当前状态（用于断点续传）。"""
    run_dir = _get_run_dir(run_id)
    
    state = {"run_id": run_id, "chapters": {}, "outline": "", "meta": {}}
    
    # 加载元信息
    meta_path = os.path.join(run_dir, "meta.json")
    if os.path.exists(meta_path):
        with open(meta_path, "r", encoding="utf-8") as f:
            state["meta"] = json.load(f)
    
    # 加载大纲
    outline_path = os.path.join(run_dir, "outline.txt")
    if os.path.exists(outline_path):
        with open(outline_path, "r", encoding="utf-8") as f:
            state["outline"] = f.read()
    
    # 加载已写章节
    chapters_dir = os.path.join(run_dir, "chapters")
    if os.path.exists(chapters_dir):
        for fname in os.listdir(chapters_dir):
            if fname.endswith(".txt"):
                ch_id = fname.replace(".txt", "")
                with open(os.path.join(chapters_dir, fname), "r", encoding="utf-8") as f:
                    state["chapters"][ch_id] = f.read()
    
    # 加载进度
    progress_path = os.path.join(run_dir, "progress.json")
    if os.path.exists(progress_path):
        with open(progress_path, "r", encoding="utf-8") as f:
            state["progress"] = json.load(f)
    
    _log(f"load_run_state: {run_id}, {len(state['chapters'])} 章已加载")
    return state


# ─────────────────────────────────────────────
# 工具定义（给LLM的function calling schema）
# ─────────────────────────────────────────────
TOOLS_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "search_original",
            "description": "在原文中搜索关键词，返回匹配的段落及上下文。用于查找特定概念、术语、数据在原文中的位置。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，多个词用空格分隔（AND逻辑）"
                    },
                    "context_chars": {
                        "type": "integer",
                        "description": "每个匹配周围的上下文字符数，默认2000",
                        "default": 2000
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_original_segment",
            "description": "按百分比位置读取原文的一段。用于浏览原文特定区域（如前10%、中间50-60%等）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_pct": {
                        "type": "number",
                        "description": "起始位置百分比 (0-100)"
                    },
                    "end_pct": {
                        "type": "number",
                        "description": "结束位置百分比 (0-100)"
                    }
                },
                "required": ["start_pct", "end_pct"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_chapter",
            "description": "写入或覆写一个章节。内容会立即持久化到磁盘。禁止使用markdown格式符号。",
            "parameters": {
                "type": "object",
                "properties": {
                    "chapter_id": {
                        "type": "string",
                        "description": "章节ID，如 Ch1, Ch2"
                    },
                    "content": {
                        "type": "string",
                        "description": "章节内容，纯文本，禁止markdown"
                    }
                },
                "required": ["chapter_id", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_chapter",
            "description": "读取一个已写章节的完整内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "chapter_id": {
                        "type": "string",
                        "description": "章节ID"
                    }
                },
                "required": ["chapter_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_chapters",
            "description": "列出所有已写章节及其字数。",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "self_review_chapter",
            "description": "对单章进行自审，获取该章内容和大纲要求的对比材料。返回后由你自行判断质量。",
            "parameters": {
                "type": "object",
                "properties": {
                    "chapter_id": {
                        "type": "string",
                        "description": "要审查的章节ID"
                    }
                },
                "required": ["chapter_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": "所有章节已完成且自审通过，结束写作。",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {
                        "type": "string",
                        "description": "完成总结"
                    }
                },
                "required": ["summary"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_and_download_paper",
            "description": "搜索并下载论文。使用arXiv API搜索论文，下载PDF并提取文本内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "论文标题或搜索关键词"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最大返回结果数（默认3）",
                        "default": 3
                    }
                },
                "required": ["query"]
            }
        }
    }
]
