"""LangGraph 三Agent架构 — Writer / Reviewer / FactChecker 循环

图结构：
  outline → writer → reviewer → fact_checker → judge
                                         ↑          │
                                         └── FAIL ──┘
                                               ↓ PASS
                                            next_chapter → writer → ...
                                               ↓ all done
                                            pdf_generator → END

每章写完立刻存盘，崩了可续。
"""
from __future__ import annotations
import os
import sys
import json
import re
import time
from typing import TypedDict, Literal

# 确保项目根目录在sys.path中
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from langgraph.graph import StateGraph, END
from agent.tools import (
    search_original, read_original_segment, write_chapter,
    read_chapter, list_chapters, init_run,
    _get_run_dir, _log,
)


# ─────────────────────────────────────────────
# State
# ─────────────────────────────────────────────
class RewriteState(TypedDict):
    run_id: str
    paper_title: str
    original_text: str
    target_audience: str
    outline: str
    chapter_order: list[str]
    current_chapter_idx: int
    current_chapter_content: str      # 当前章正在写的内容
    current_chapter_review: str       # 当前章审查报告
    current_chapter_factcheck: str    # 当前章事实核查报告
    current_chapter_score: float      # 当前章综合分
    chapter_retry_count: int          # 当前章重试次数
    max_retries: int                  # 每章最大重试
    min_chapter_chars: int            # 每章最少字数（固定值，用于向后兼容）
    target_chars_per_chapter: int     # 每章目标字数（动态计算）
    phase: str                        # outline/writing/reviewing/factchecking/judging/done


# ─────────────────────────────────────────────
# LLM 调用
# ─────────────────────────────────────────────
def _llm(system: str, user: str, temperature: float = 0.4, max_tokens: int = 16384) -> str:
    """调用LLM，返回文本。使用pipeline.llm的llm_call（带300秒超时+重试）。"""
    from pipeline.llm import llm_call
    return llm_call(system, user, temperature=temperature, max_tokens=max_tokens, timeout=300.0)


def _llm_json(system: str, user: str, temperature: float = 0.1) -> dict:
    """调用LLM，返回JSON"""
    text = _llm(system, user, temperature=temperature, max_tokens=4096)
    # 提取JSON
    m = re.search(r'\{[\s\S]*\}', text)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return {"error": "JSON解析失败", "raw": text[:500]}


# ─────────────────────────────────────────────
# 节点 1: 大纲生成
# ─────────────────────────────────────────────
def outline_node(state: RewriteState) -> dict:
    _log("outline_node 开始")
    title = state["paper_title"]
    original = state["original_text"]
    audience = state.get("target_audience", "大一非理工科学生")

    system = """你是论文重写专家（微分-积分方法）。
任务：为一篇学术论文生成中文重写的章节大纲。

要求：
1. 每个章节有编号和标题（格式：ChN: 标题）
2. 章节覆盖原文所有主要概念，不遗漏
3. 从最简单的直觉出发，逐步建立复杂概念
4. 章节数量：每2-3万字原文对应1章（40万字原文约15-20章）
5. 每章标题下附5-8个要点提示，说明本章要覆盖的核心概念、关键术语和主要论点
6. 确保覆盖原文的所有主要章节和概念，不要遗漏任何重要内容
7. 输出纯Markdown"""

    # 分段生成大纲：每次处理10万字，最后合并
    CHUNK_SIZE = 100000
    chunks = [original[i:i+CHUNK_SIZE] for i in range(0, len(original), CHUNK_SIZE)]
    outlines = []
    
    for ci, chunk in enumerate(chunks):
        _log(f"outline_node: 处理第{ci+1}/{len(chunks)}段 ({len(chunk)}字)")
        user = f"""论文标题：{title}
目标读者：{audience}
原文长度：{len(original)}字（当前处理第{ci+1}段，共{len(chunks)}段）

原文内容（第{ci+1}段）：
{chunk}

请为这一段内容生成章节大纲。如果是第1段，从Ch1开始编号；如果是后续段落，继续上一段的编号。"""
        
        chunk_outline = _llm(system, user, temperature=0.5)
        outlines.append(chunk_outline)
    
    outline = "\n\n".join(outlines)
    chapter_order = re.findall(r"Ch\d+", outline)
    if not chapter_order:
        chapter_order = [f"Ch{i}" for i in range(1, 11)]

    # 计算每章目标字数：原文字数 / 章节数 * 扩展系数
    original_chars = len(original)
    chapter_count = len(chapter_order)
    if chapter_count > 0:
        # 扩展系数1.5，下限5000字（实际长度由reviewer反馈驱动）
        target_chars = int(original_chars / chapter_count * 1.5)
        target_chars = max(target_chars, 5000)
    else:
        target_chars = 8000

    # 保存大纲到磁盘
    run_dir = _get_run_dir(state["run_id"])
    with open(os.path.join(run_dir, "outline.txt"), "w", encoding="utf-8") as f:
        f.write(outline)

    _log(f"outline_node 完成: {len(chapter_order)} 章, 每章目标 {target_chars} 字")
    return {
        "outline": outline,
        "chapter_order": chapter_order,
        "current_chapter_idx": 0,
        "target_chars_per_chapter": target_chars,
        "phase": "writing",
    }


# ─────────────────────────────────────────────
# 节点 2: 写手（单章）
# ─────────────────────────────────────────────
def writer_node(state: RewriteState) -> dict:
    idx = state["current_chapter_idx"]
    ch_id = state["chapter_order"][idx]
    retry = state.get("chapter_retry_count", 0)
    target = state.get("target_chars_per_chapter", 10000)

    _log(f"writer_node: {ch_id} (第{retry+1}次), 目标{target}字")

    original = state["original_text"]
    outline = state.get("outline", "")
    audience = state.get("target_audience", "大一非理工科学生")

    # 提取本章大纲
    pattern = re.compile(rf"({ch_id}[:\s].*?)(?=Ch\d+|$)", re.DOTALL)
    m = pattern.search(outline)
    ch_outline = m.group(1).strip() if m else ch_id

    # 搜索相关原文段落（更多关键词，更大上下文）
    keywords = re.findall(r'[\u4e00-\u9fff]{2,6}|[A-Za-z]{3,}', ch_outline)[:8]
    context_parts = []
    for kw in keywords[:5]:
        result = search_original(state["run_id"], kw, context_chars=8000)
        if not result.startswith("未找到"):
            context_parts.append(result)
    context = "\n\n".join(context_parts) if context_parts else ""

    # 如果有审查反馈，加入提示
    review_hint = ""
    if retry > 0 and state.get("current_chapter_review"):
        try:
            review_data = json.loads(state["current_chapter_review"])
            fixes = []
            for issue in review_data.get("issues", []):
                if isinstance(issue, dict):
                    fixes.append(f"- 【{issue.get('location', '?')}】{issue.get('problem', '')} → {issue.get('fix', '')}")
            for m in review_data.get("missing", []):
                if isinstance(m, dict):
                    fixes.append(f"- 【遗漏】{m.get('concept', '')} — {m.get('suggestion', '')}")
            if fixes:
                review_hint = "\n\n上一轮审查反馈（请据此改进）：\n" + "\n".join(fixes)
        except (json.JSONDecodeError, TypeError):
            review_hint = f"""

上一轮审查反馈（请据此改进）：
{state['current_chapter_review'][:1000]}"""

    system = f"""你是一位优秀的科普作家，擅长把复杂学术内容写成通俗易懂的中文文章。
使用「微分-积分」方法：先把每个概念拆成最小可理解单元，用日常比喻帮助理解，然后重新组装成连贯叙事。

目标读者：{audience}

写作规则（严格遵守）：
- 中文输出，术语首次出现时括号注英文原文
- 充分展开每个概念，不要压缩。用微分-积分方法逐层拆解
- 纯散文体，禁止使用任何markdown符号（#、*、**、-、|、>、```等）
- 段落之间空一行即可，不要加标题标记
- 短句为主，一句话一个概念
- 不用"换言之""显然""易知"等学术衔接词
- 行文流畅自然，像跟朋友聊天一样解释概念
- 微积分标准：每个概念拆解到目标读者能独立理解就停止展开，不要注水也不要压缩
- 覆盖本章大纲中的所有要点，每个要点都要充分展开
- 本章目标字数：至少{target}字。这是硬性要求，必须写够。如果写到一半发现不够，继续展开更多细节和例子
- 不要提前结束。写完一个要点后，继续写下一个要点，直到所有要点都覆盖完毕{review_hint}"""

    # 多段写入：如果目标超过15K字，分多次写入
    MAX_SINGLE = 15000  # LLM单次产出上限（mimo-v2.5约能写5000-8000字）
    sections = []
    
    if target <= MAX_SINGLE:
        # 单次写入
        user = f"""大纲：
{outline}

当前章节：{ch_id}
本章要求：
{ch_outline}

相关原文片段：
{context[:30000]}

请写出这一章的完整内容。每个概念用微积分方法拆解到目标读者能理解的程度。"""
        content = _llm(system, user, temperature=0.7, max_tokens=16384)
        sections.append(content)
    else:
        # 顺序扩展模式：第一轮写完整章，后续轮次扩展加厚
        max_rounds = max(2, target // 5000)  # 每轮约5000字，至少2轮
        
        for ri in range(max_rounds):
            is_first = ri == 0
            existing = "\n\n".join(sections) if sections else ""
            
            if is_first:
                user = f"""大纲：
{outline}

当前章节：{ch_id}
本章要求：
{ch_outline}

相关原文片段：
{context[:30000]}

请写出这一章的完整内容。覆盖大纲中的所有要点，每个要点充分展开。目标至少{target // max_rounds}字。"""
            else:
                user = f"""大纲：
{outline}

当前章节：{ch_id}
本章要求：
{ch_outline}

已写内容（{len(existing)}字）：
{existing[-8000:]}

相关原文片段：
{context[:20000]}

请继续扩展这一章的内容。要求：
1. 不要重复已有内容，只写新增部分
2. 为已有的概念补充更多具体例子和类比
3. 对重要公式或定理展开逐步推导
4. 补充原文中提到但重写遗漏的细节
5. 目标再增加{target // max_rounds}字"""
            
            section = _llm(system, user, temperature=0.7, max_tokens=16384)
            sections.append(section)
            _log(f"writer_node {ch_id} 第{ri+1}轮: {len(section)}字, 累计{sum(len(s) for s in sections)}字")
            
            # 如果已经写够了，提前结束
            if sum(len(s) for s in sections) >= target:
                _log(f"writer_node {ch_id} 已达到目标字数，提前结束")
                break
    
    content = "\n\n".join(sections)

    # 立刻存盘
    write_chapter(state["run_id"], ch_id, content)

    _log(f"writer_node {ch_id} 完成: {len(content)} 字")
    return {
        "current_chapter_content": content,
        "current_chapter_review": "",
        "current_chapter_factcheck": "",
        "current_chapter_score": 0.0,
        "phase": "reviewing",
    }


# ─────────────────────────────────────────────
# 节点 3: 审查员
# ─────────────────────────────────────────────
def reviewer_node(state: RewriteState) -> dict:
    idx = state["current_chapter_idx"]
    ch_id = state["chapter_order"][idx]
    content = state.get("current_chapter_content", "")
    outline = state.get("outline", "")
    original = state["original_text"]

    _log(f"reviewer_node: {ch_id}, {len(content)} 字")

    # 搜索原文中本章相关的关键概念
    pattern = re.compile(rf"({ch_id}[:\s].*?)(?=Ch\d+|$)", re.DOTALL)
    m = pattern.search(outline)
    ch_outline = m.group(1).strip() if m else ""
    keywords = re.findall(r'[\u4e00-\u9fff]{2,6}|[A-Za-z]{3,}', ch_outline)[:8]

    ref_parts = []
    for kw in keywords[:5]:
        result = search_original(state["run_id"], kw, context_chars=5000)
        if not result.startswith("未找到"):
            ref_parts.append(result)
    ref_text = "\n\n".join(ref_parts)

    system = """你是论文重写审查员。逐段对比原文和重写章节，输出具体的改进建议。

审查重点：
1. 原文中有哪些段落/概念在重写中被跳过或一笔带过？列出具体位置
2. 哪些概念的拆解深度不够？目标读者（大一非理工科）能否理解？
3. 重写是否添加了原文没有的内容（幻觉）？
4. 行文是否流畅？有没有生硬的过渡？

输出JSON：
{
  "score": 0-10,
  "coverage": "概念覆盖率评估（具体列出遗漏的段落/概念）",
  "quality": "行文质量评估",
  "decomposition": "概念拆解深度评估",
  "issues": [{"location": "第几段/哪个概念", "problem": "具体问题", "fix": "如何改进"}],
  "missing": [{"concept": "遗漏的概念", "original_ref": "原文中的位置/上下文", "suggestion": "建议如何展开"}],
  "verdict": "PASS/FAIL"
}
PASS标准：score>=7 且无重大遗漏。"""

    user = f"""章节大纲：{ch_outline}

重写内容（{len(content)}字）：
{content[:16000]}

相关原文片段：
{ref_text[:10000]}

请审查评分。"""

    result = _llm_json(system, user)
    score = result.get("score", 5.0)
    verdict = result.get("verdict", "FAIL")
    review_report = json.dumps(result, ensure_ascii=False, indent=2)

    _log(f"reviewer_node {ch_id}: score={score}, verdict={verdict}")
    return {
        "current_chapter_review": review_report,
        "current_chapter_score": score,
        "phase": "factchecking",
    }


# ─────────────────────────────────────────────
# 节点 4: 事实核查
# ─────────────────────────────────────────────
def factchecker_node(state: RewriteState) -> dict:
    idx = state["current_chapter_idx"]
    ch_id = state["chapter_order"][idx]
    content = state.get("current_chapter_content", "")
    original = state["original_text"]

    _log(f"factchecker_node: {ch_id}")

    # 搜索原文中相关内容
    outline = state.get("outline", "")
    pattern = re.compile(rf"({ch_id}[:\s].*?)(?=Ch\d+|$)", re.DOTALL)
    m = pattern.search(outline)
    ch_outline = m.group(1).strip() if m else ""
    keywords = re.findall(r'[\u4e00-\u9fff]{2,6}|[A-Za-z]{3,}', ch_outline)[:5]

    ref_parts = []
    for kw in keywords:
        result = search_original(state["run_id"], kw, context_chars=5000)
        if not result.startswith("未找到"):
            ref_parts.append(result)
    ref_text = "\n\n".join(ref_parts)

    system = """你是事实核查员。检查重写内容是否忠于原文。
输出JSON：
{
  "accuracy": 0-10,
  "hallucinations": ["重写中添加的原文没有的内容"],
  "missing_facts": ["原文有但重写遗漏的关键事实"],
  "verified": ["已验证正确的陈述"],
  "verdict": "PASS/FAIL"
}
PASS标准：accuracy>=7 且无明显幻觉。"""

    user = f"""重写内容（{len(content)}字）：
{content[:16000]}

相关原文：
{ref_text[:10000]}

请事实核查。"""

    result = _llm_json(system, user)
    accuracy = result.get("accuracy", 5.0)
    verdict = result.get("verdict", "PASS")
    factcheck_report = json.dumps(result, ensure_ascii=False, indent=2)

    _log(f"factchecker_node {ch_id}: accuracy={accuracy}, verdict={verdict}")
    return {
        "current_chapter_factcheck": factcheck_report,
        "phase": "judging",
    }


# ─────────────────────────────────────────────
# 节点 5: 裁判（决定重写还是下一章）
# ─────────────────────────────────────────────
def judge_node(state: RewriteState) -> dict:
    idx = state["current_chapter_idx"]
    ch_id = state["chapter_order"][idx]
    content = state.get("current_chapter_content", "")
    score = state.get("current_chapter_score", 0)
    retry = state.get("chapter_retry_count", 0)
    max_retries = state.get("max_retries", 2)
    review_report = state.get("current_chapter_review", "")
    factcheck_report = state.get("current_chapter_factcheck", "")
    _log(f"judge_node: {ch_id}, score={score}, retry={retry}, chars={len(content)}")

    # 解析reviewer的结构化反馈
    fix_list = []
    try:
        review_data = json.loads(review_report) if review_report else {}
        # 从issues提取具体修改项
        for issue in review_data.get("issues", []):
            if isinstance(issue, dict):
                fix_list.append(f"【{issue.get('location', '?')}】{issue.get('problem', '')} → {issue.get('fix', '')}")
            else:
                fix_list.append(str(issue))
        # 从missing提取遗漏概念
        for m in review_data.get("missing", []):
            if isinstance(m, dict):
                fix_list.append(f"【遗漏】{m.get('concept', '')} — {m.get('suggestion', '')}")
            else:
                fix_list.append(f"【遗漏】{m}")
    except (json.JSONDecodeError, TypeError):
        pass

    # 判定逻辑
    pass_score = 7.0
    low_score = score < pass_score
    can_retry = retry < max_retries

    if low_score and can_retry:
        reason = [f"评分低: {score}/{pass_score}"]
        _log(f"judge_node {ch_id}: RETRY ({', '.join(reason)})")
        return {
            "chapter_retry_count": retry + 1,
            "current_chapter_review": review_report,  # 保留review供writer参考
            "phase": "writing",  # 回到writer
        }
    else:
        # 通过，下一章
        _log(f"judge_node {ch_id}: PASS (score={score}, chars={len(content)})")
        return {
            "current_chapter_idx": idx + 1,
            "chapter_retry_count": 0,
            "phase": "writing" if idx + 1 < len(state["chapter_order"]) else "done",
        }


# ─────────────────────────────────────────────
# 条件路由
# ─────────────────────────────────────────────
def route_after_outline(state: RewriteState) -> str:
    return "writer"

def route_after_writer(state: RewriteState) -> str:
    return "reviewer"

def route_after_reviewer(state: RewriteState) -> str:
    return "factchecker"

def route_after_factchecker(state: RewriteState) -> str:
    return "judge"

def route_after_judge(state: RewriteState) -> str:
    if state.get("phase") == "done":
        return "pdf_generator"
    elif state.get("phase") == "writing" and state.get("chapter_retry_count", 0) > 0:
        return "writer"  # 重写当前章
    else:
        return "writer"  # 写下一章


# ─────────────────────────────────────────────
# 节点 6: PDF生成
# ─────────────────────────────────────────────
def pdf_generator_node(state: RewriteState) -> dict:
    _log("pdf_generator_node 开始")
    from fpdf import FPDF

    run_dir = _get_run_dir(state["run_id"])
    title = state.get("paper_title", "未命名论文")
    chapter_order = state.get("chapter_order", [])

    # 读所有章节
    contents = {}
    for ch_id in chapter_order:
        ch_path = os.path.join(run_dir, "chapters", f"{ch_id}.txt")
        if os.path.exists(ch_path):
            with open(ch_path, "r", encoding="utf-8") as f:
                contents[ch_id] = f.read()

    total_chars = sum(len(v) for v in contents.values())

    FONT_PATH = "C:/Windows/Fonts/simhei.ttf"
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_font("SimHei", "", FONT_PATH)
    W = 170

    def strip_md(text):
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'\*(.+?)\*', r'\1', text)
        text = re.sub(r'^[-*+]\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'^>\s+', '', text, flags=re.MULTILINE)
        text = re.sub(r'`(.+?)`', r'\1', text)
        text = re.sub(r'```[\s\S]*?```', '', text)
        text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)
        text = re.sub(r'^\|.*\|$', '', text, flags=re.MULTILINE)
        text = re.sub(r'^-{3,}$', '', text, flags=re.MULTILINE)
        return text.strip()

    # 封面
    pdf.add_page()
    pdf.set_font("SimHei", "", 18)
    pdf.cell(W, 14, title, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(5)
    pdf.set_font("SimHei", "", 12)
    pdf.cell(W, 7, "中文通俗重写版", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(W, 7, f"原文: {len(state.get('original_text', ''))} 字", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(W, 7, f"重写: {total_chars} 字", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(W, 7, f"章节数: {len(contents)}", new_x="LMARGIN", new_y="NEXT")

    # 正文
    for ch_id in chapter_order:
        content = contents.get(ch_id, "")
        if not content:
            continue
        paras = content.split("\n\n")
        for para in paras:
            para = para.strip()
            if not para:
                continue
            if len(para) < 40 and not para.endswith("。"):
                pdf.ln(3)
                pdf.set_font("SimHei", "", 14)
                pdf.cell(W, 9, strip_md(para), new_x="LMARGIN", new_y="NEXT")
                pdf.ln(3)
            else:
                t = strip_md(para)
                if not t:
                    continue
                pdf.set_font("SimHei", "", 12)
                pdf.multi_cell(W, 7, t, align="L")
                pdf.ln(1)

    safe_title = re.sub(r'[<>:"/\\|?*]', "_", title)
    pdf_path = os.path.join(_PROJECT_ROOT, "output", f"{safe_title}_中文重写.pdf")
    os.makedirs(os.path.dirname(pdf_path), exist_ok=True)
    pdf.output(pdf_path)

    _log(f"pdf_generator_node 完成: {pdf_path}, {total_chars} 字")
    return {"phase": "done"}


# ─────────────────────────────────────────────
# 构建图
# ─────────────────────────────────────────────
def build_rewrite_graph():
    graph = StateGraph(RewriteState)

    graph.add_node("outline", outline_node)
    graph.add_node("writer", writer_node)
    graph.add_node("reviewer", reviewer_node)
    graph.add_node("factchecker", factchecker_node)
    graph.add_node("judge", judge_node)
    graph.add_node("pdf_generator", pdf_generator_node)

    graph.set_entry_point("outline")

    graph.add_conditional_edges("outline", route_after_outline, {"writer": "writer"})
    graph.add_conditional_edges("writer", route_after_writer, {"reviewer": "reviewer"})
    graph.add_conditional_edges("reviewer", route_after_reviewer, {"factchecker": "factchecker"})
    graph.add_conditional_edges("factchecker", route_after_factchecker, {"judge": "judge"})
    graph.add_conditional_edges("judge", route_after_judge, {
        "writer": "writer",
        "pdf_generator": "pdf_generator",
    })
    graph.add_edge("pdf_generator", END)

    return graph.compile()


# ─────────────────────────────────────────────
# 运行入口
# ─────────────────────────────────────────────
def run_rewrite(paper_title: str, original_text: str,
                target_audience: str = "大一非理工科学生",
                run_id: str = None,
                max_retries: int = 2) -> str:
    if not run_id:
        run_id = str(int(time.time()))[-8:]

    _log(f"run_rewrite 开始: {run_id}, 原文{len(original_text)}字")

    # 初始化持久化
    init_run(run_id, original_text, paper_title=paper_title)

    initial_state: RewriteState = {
        "run_id": run_id,
        "paper_title": paper_title,
        "original_text": original_text,
        "target_audience": target_audience,
        "outline": "",
        "chapter_order": [],
        "current_chapter_idx": 0,
        "current_chapter_content": "",
        "current_chapter_review": "",
        "current_chapter_factcheck": "",
        "current_chapter_score": 0.0,
        "chapter_retry_count": 0,
        "max_retries": max_retries,
        "min_chapter_chars": 0,  # 已废弃，仅保留state字段兼容
        "target_chars_per_chapter": 0,  # 信息性，由outline_node计算
        "phase": "outline",
    }

    graph = build_rewrite_graph()

    try:
        for event in graph.stream(initial_state, {"recursion_limit": 200}):
            for node_name, node_output in event.items():
                _log(f"graph event: {node_name}")
                if node_name == "judge":
                    idx = node_output.get("current_chapter_idx", 0)
                    phase = node_output.get("phase", "")
                    _log(f"  → idx={idx}, phase={phase}")
    except Exception as e:
        _log(f"run_rewrite 异常: {e}")
        import traceback
        _log(traceback.format_exc())
        raise

    _log(f"run_rewrite 完成: {run_id}")
    return run_id
