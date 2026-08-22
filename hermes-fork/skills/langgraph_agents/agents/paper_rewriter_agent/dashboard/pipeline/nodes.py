"""LangGraph 节点函数 — 各 Agent 角色实现

每个节点是一个纯函数: (state) -> dict(state_updates)
通过 events 模块发射实时事件给 UI。
"""
from __future__ import annotations
import re
import os
import time as _time
from .state import RewriteState
from .llm import llm_call, llm_call_json, llm_call_stream
from .config import PASS_SCORE, MAX_ROUNDS
from .events import fire_node_start, fire_node_end, fire_state_update, fire_error, fire_complete, fire_llm_token
from .context import build_chapter_context, build_review_context

_PIPELINE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _log(msg: str):
    ts = _time.strftime("%H:%M:%S")
    log_path = os.path.join(_PIPELINE_DIR, "pipeline.log")
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except OSError:
        pass  # Windows encoding issue — don't crash pipeline


# ─────────────────────────────────────────────
# 1. 大纲生成器
# ─────────────────────────────────────────────
def outline_generator(state: RewriteState) -> dict:
    fire_node_start("outline_generator", "正在分析原文并生成章节大纲...", state)
    _log("outline_generator 开始")

    title = state.get("paper_title", "未命名论文")
    original = state["original_text"]
    audience = state.get("target_audience", "大一非理工科学生")

    system = """你是论文重写专家（微分-积分方法）。
任务：为一篇学术论文生成中文重写的章节大纲。

要求：
1. 每个章节有编号和标题（格式：ChN: 标题）
2. 章节覆盖原文所有主要概念，不遗漏
3. 从最简单的直觉出发，逐步建立复杂概念
4. 章节数量根据原文长度动态调整：每1-2万字原文对应1章重写（例如10万字原文约10章，27万字原文约15-20章）
5. 每章标题下附2-3个要点提示，说明本章要覆盖的核心概念
6. 输出纯Markdown"""

    user = f"""论文标题：{title}
目标读者：{audience}
原文长度：{len(original)}字

原文内容：
{original}

请生成章节大纲。"""

    _log("outline_generator 调用LLM...")
    outline = llm_call_stream(
        system, user, temperature=0.5,
        on_token=lambda t: fire_llm_token("outline_generator", "token", t, "大纲"),
        on_reasoning=lambda t: fire_llm_token("outline_generator", "reasoning", t, "大纲"),
    )
    _log(f"outline_generator LLM返回 {len(outline)} 字")

    chapter_order = re.findall(r"Ch\d+", outline)
    if not chapter_order:
        chapter_order = ["Ch1", "Ch2", "Ch3"]
        outline = "Ch1: 概述\nCh2: 核心概念\nCh3: 应用与总结"

    _log(f"outline_generator 完成，{len(chapter_order)} 章")
    fire_node_end("outline_generator", f"大纲生成完成，共 {len(chapter_order)} 章", state)

    return {
        "outline": outline,
        "chapter_order": chapter_order,
        "chapters": {},
        "current_chapter_idx": 0,
        "current_phase": "outline_done",
        "round_num": 1,
        "max_rounds": MAX_ROUNDS,
    }


# ─────────────────────────────────────────────
# 2. 写手（单章写作，由 graph 循环驱动）
# ─────────────────────────────────────────────
def writer(state: RewriteState) -> dict:
    try:
        return _writer_impl(state)
    except Exception as e:
        _log(f"writer 异常: {type(e).__name__}: {e}")
        import traceback; _log(traceback.format_exc())
        raise

def _writer_impl(state: RewriteState) -> dict:
    fire_node_start("writer", f"第 {state.get('round_num',1)} 轮写作", state)
    _log(f"writer 开始，轮次 {state.get('round_num',1)}")

    original = state["original_text"]
    outline = state.get("outline", "")
    chapters = dict(state.get("chapters", {}))
    chapter_order = state.get("chapter_order", [])
    fix_list = state.get("fix_list", [])
    round_num = state.get("round_num", 1)

    system = """你是一位优秀的科普作家，擅长把复杂学术内容写成通俗易懂的中文文章。
使用「微分-积分」方法：先把每个概念拆成最小可理解单元，用日常比喻帮助理解，然后重新组装成连贯叙事。

写作规则（严格遵守）：
- 中文输出，术语首次出现时括号注英文原文
- 每个原文句子展开为3-5句
- 纯散文体，禁止使用任何markdown符号（#、*、**、-、|、>、```等）
- 段落之间空一行即可，不要加标题标记
- 短句为主，一句话一个概念
- 不用"换言之""显然""易知"等学术衔接词
- 不要压缩，要展开——这是给人读的通俗文章，不是摘要
- 行文流畅自然，像在跟朋友聊天一样解释概念"""

    fix_instruction = ""
    if fix_list and round_num > 1:
        fix_instruction = "\n\n裁判要求修改：\n" + "\n".join(f"- {f}" for f in fix_list)

    for ch_id in chapter_order:
        if ch_id in chapters and round_num == 1:
            continue
        _log(f"writer 写 {ch_id}...")
        fire_state_update("writer", f"正在写 {ch_id}...", state)

        ch_title_match = re.search(rf"{ch_id}[:\s]+(.+)", outline)
        ch_title = ch_title_match.group(1).strip() if ch_title_match else ch_id

        # 智能分窗：只传相关段落而不是全文
        chapter_context, orig_len, ctx_len = build_chapter_context(
            original, outline, ch_id, ch_title
        )
        _log(f"writer {ch_id} 上下文: 原文{orig_len}字 → 精简{ctx_len}字 (压缩{100-ctx_len*100//orig_len}%)")

        user = f"""大纲：{outline}
当前章节：{ch_id}: {ch_title}
相关原文片段：
{chapter_context}
请写出这一章完整内容。{fix_instruction}"""

        try:
            content = llm_call_stream(
                system, user, temperature=0.7, max_tokens=16384,
                on_token=lambda t, _ch=ch_id: fire_llm_token("writer", "token", t, _ch),
                on_reasoning=lambda t, _ch=ch_id: fire_llm_token("writer", "reasoning", t, _ch),
            )
            _log(f"writer {ch_id} 完成 {len(content)} 字")
        except Exception as e:
            content = f"[写作失败: {e}]"
            _log(f"writer {ch_id} 失败: {e}")
            fire_error("writer", f"{ch_id} 失败: {e}", state)
        chapters[ch_id] = content

    full_rewrite = "\n\n".join(chapters.get(cid, "") for cid in chapter_order)
    _log(f"writer 全部完成，{len(chapters)} 章，共 {len(full_rewrite)} 字")

    fire_node_end("writer", f"写作完成 {len(chapters)} 章", state)
    result = {
        "chapters": chapters,
        "full_rewrite": full_rewrite,
        "current_phase": "writing_done",
    }
    _log(f"writer 返回, full_rewrite={len(full_rewrite)}字, chapters={len(chapters)}个")
    return result


# ─────────────────────────────────────────────
# 3. 比对员
# ─────────────────────────────────────────────
def reviewer(state: RewriteState) -> dict:
    try:
        return _reviewer_impl(state)
    except Exception as e:
        _log(f"reviewer 异常: {type(e).__name__}: {e}")
        import traceback; _log(traceback.format_exc())
        raise

def _reviewer_impl(state: RewriteState) -> dict:
    fire_node_start("reviewer", "开始对比审查...", state)
    _log("reviewer 开始")

    original = state["original_text"]
    rewrite = state.get("full_rewrite", "")
    outline = state.get("outline", "")
    chapter_order = state.get("chapter_order", [])

    system = """你是论文重写比对员。对比原文和重写版本，输出JSON评分。
检查：概念覆盖率、技术准确性、章节连续性、长度是否更长。
输出：{"overall_score": 0-10, "issues": [...], "missing_concepts": [...]}"""

    _log(f"reviewer 构建对比上下文...")
    review_ctx = build_review_context(original, rewrite, outline)
    _log(f"reviewer 对比上下文长度: {len(review_ctx)}字")

    user = f"""{review_ctx}
章节：{', '.join(chapter_order)}
请评分。"""

    _log(f"reviewer 调用LLM..., prompt长度={len(user)}字")
    try:
        result = llm_call_json(system, user, temperature=0.2)
        score = result.get("overall_score", 7.0)
        report = str(result)
        _log(f"reviewer 评分 {score}")
    except Exception as e:
        score = 7.0
        report = f"审查解析失败: {e}"
        _log(f"reviewer 解析失败: {e}")

    fire_node_end("reviewer", f"审查完成 评分 {score}/10", state)
    ret = {
        "review_report": report,
        "score": score,
        "current_phase": "review_done",
    }
    _log(f"reviewer 返回, report长度={len(report)}字")
    return ret


# ─────────────────────────────────────────────
# 3.5 事实核查
# ─────────────────────────────────────────────
def fact_checker(state: RewriteState) -> dict:
    try:
        return _fact_checker_impl(state)
    except Exception as e:
        _log(f"fact_checker 异常: {type(e).__name__}: {e}")
        import traceback; _log(traceback.format_exc())
        raise

def _fact_checker_impl(state: RewriteState) -> dict:
    fire_node_start("fact_checker", "事实核查中...", state)
    _log("fact_checker 开始")

    original = state["original_text"]
    rewrite = state.get("full_rewrite", "")
    outline = state.get("outline", "")
    chapter_order = state.get("chapter_order", [])

    system = """你是事实核查员。对比原文和重写版本，检查：
1. 重写中引用的数据、术语、人名是否在原文中有依据
2. 重写是否遗漏了原文中的关键概念（特别是专有名词、分类法、框架名称）
3. 重写是否添加了原文中没有的"事实"（幻觉）

输出JSON：
{
  "factual_accuracy": 0-10,
  "missing_key_concepts": ["遗漏的关键概念"],
  "hallucinations": ["重写中添加的原文没有的内容"],
  "verified_claims": ["已验证正确的关键陈述"]
}"""

    # 用采样方式构建上下文，避免全文塞入
    from .context import build_review_context
    review_ctx = build_review_context(original, rewrite, outline, max_chars=30000)

    user = f"""{review_ctx}
章节：{', '.join(chapter_order)}
请进行事实核查。"""

    _log(f"fact_checker 调用LLM..., prompt长度={len(user)}字")
    try:
        result = llm_call_json(system, user, temperature=0.1)
        accuracy = result.get("factual_accuracy", 7.0)
        missing = result.get("missing_key_concepts", [])
        hallucinations = result.get("hallucinations", [])
        report = str(result)
        _log(f"fact_checker 完成, 准确度={accuracy}, 遗漏={len(missing)}, 幻觉={len(hallucinations)}")
    except Exception as e:
        accuracy = 7.0
        report = f"事实核查解析失败: {e}"
        _log(f"fact_checker 解析失败: {e}")

    fire_node_end("fact_checker", f"事实核查完成 准确度={accuracy}/10", state)
    ret = {
        "fact_check_report": report,
        "factual_accuracy": accuracy,
        "current_phase": "fact_check_done",
    }
    _log(f"fact_checker 返回")
    return ret


# ─────────────────────────────────────────────
# 4. 裁判
# ─────────────────────────────────────────────
def judge(state: RewriteState) -> dict:
    try:
        return _judge_impl(state)
    except Exception as e:
        _log(f"judge 异常: {type(e).__name__}: {e}")
        import traceback; _log(traceback.format_exc())
        raise

def _judge_impl(state: RewriteState) -> dict:
    fire_node_start("judge", "裁判评审中...", state)
    _log("judge 开始")

    score = state.get("score", 0)
    factual_accuracy = state.get("factual_accuracy", 7.0)
    round_num = state.get("round_num", 1)
    max_rounds = state.get("max_rounds", MAX_ROUNDS)
    review_report = state.get("review_report", "")
    fact_check_report = state.get("fact_check_report", "")

    # 综合评分：内容质量60% + 事实准确度40%
    combined_score = score * 0.6 + factual_accuracy * 0.4

    system = f"""你是裁判。综合评分>= {PASS_SCORE} 判PASS，否则FAIL。
综合评分 = 内容质量×0.6 + 事实准确度×0.4
输出JSON：{{"verdict":"PASS/FAIL","reason":"理由","fix_list":["修改项"]}}"""

    user = f"内容质量：{score}/10\n事实准确度：{factual_accuracy}/10\n综合评分：{combined_score:.1f}/10\n轮次：{round_num}/{max_rounds}\n审查报告：{review_report[:2000]}\n事实核查：{fact_check_report[:2000]}"

    _log("judge 调用LLM...")
    try:
        result = llm_call_json(system, user, temperature=0.1)
        verdict = result.get("verdict", "FAIL")
        fix_list = result.get("fix_list", [])
        reason = result.get("reason", "")
    except Exception:
        verdict = "PASS" if score >= PASS_SCORE else "FAIL"
        fix_list = ["审查报告解析失败，请修改"] if verdict == "FAIL" else []
        reason = f"评分 {score}，阈值 {PASS_SCORE}"

    if round_num >= max_rounds and verdict == "FAIL":
        verdict = "PASS"
        reason += f"（已达最大轮次 {max_rounds}）"

    _log(f"judge 判定 {verdict}，轮次 {round_num}")
    fire_node_end("judge", f"判定 {verdict}", state)

    update = {
        "judge_verdict": verdict,
        "fix_list": fix_list,
        "current_phase": "judge_done",
    }
    if verdict == "FAIL":
        update["round_num"] = round_num + 1

    _log(f"judge 判定 {verdict}，轮次 {round_num}，返回keys={list(update.keys())}")
    fire_node_end("judge", f"判定 {verdict}", state)
    return update


# ─────────────────────────────────────────────
# 5. PDF 生成器
# ─────────────────────────────────────────────
def pdf_generator(state: RewriteState) -> dict:
    fire_node_start("pdf_generator", "正在生成PDF...", state)
    _log("pdf_generator 开始")

    import os
    from .config import OUTPUT_DIR, FONT_PATH

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    title = state.get("paper_title", "未命名论文")
    chapters = state.get("chapters", {})
    chapter_order = state.get("chapter_order", [])
    safe_title = re.sub(r'[<>:"/\\|?*]', '_', title)
    pdf_path = os.path.join(OUTPUT_DIR, f"{safe_title}_中文重写.pdf")

    try:
        from fpdf import FPDF
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=20)
        pdf.add_font("SimHei", "", FONT_PATH)
        W = 170

        def strip_md(text):
            """去掉markdown格式符号"""
            import re
            text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)  # #标题
            text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)  # **粗体**
            text = re.sub(r'\*(.+?)\*', r'\1', text)  # *斜体*
            text = re.sub(r'^[-*+]\s+', '', text, flags=re.MULTILINE)  # 列表
            text = re.sub(r'^>\s+', '', text, flags=re.MULTILINE)  # 引用
            text = re.sub(r'`(.+?)`', r'\1', text)  # 行内代码
            text = re.sub(r'```[\s\S]*?```', '', text)  # 代码块
            text = re.sub(r'\[(.+?)\]\(.+?\)', r'\1', text)  # 链接
            text = re.sub(r'^\|.*\|$', '', text, flags=re.MULTILINE)  # 表格行
            text = re.sub(r'^-{3,}$', '', text, flags=re.MULTILINE)  # 分割线
            return text.strip()

        def h1(t):
            pdf.add_page(); pdf.set_font("SimHei", "", 18)
            t = strip_md(t)
            pdf.cell(W, 14, t, new_x="LMARGIN", new_y="NEXT"); pdf.ln(5)

        def p(t):
            t = strip_md(t)
            if not t: return
            pdf.set_font("SimHei", "", 12)
            pdf.multi_cell(W, 7, t, align="L"); pdf.ln(1)

        h1(title); p("中文通俗重写版"); pdf.ln(10)
        for cid in chapter_order:
            content = chapters.get(cid, "")
            content = strip_md(content)
            for para in content.split("\n\n"):
                para = para.strip()
                if not para: continue
                # 短行当标题，长行当正文
                if len(para) < 40 and not para.endswith("。"):
                    pdf.ln(3)
                    pdf.set_font("SimHei", "", 14)
                    pdf.cell(W, 9, para, new_x="LMARGIN", new_y="NEXT")
                    pdf.ln(3)
                else:
                    p(para)

        pdf.output(pdf_path)
        _log(f"pdf_generator 完成 {pdf_path}")
    except Exception as e:
        pdf_path = ""
        _log(f"pdf_generator 失败: {e}")
        fire_error("pdf_generator", f"PDF失败: {e}", state)

    fire_complete("Pipeline 执行完毕！", state)
    return {"pdf_path": pdf_path, "status": "completed", "current_phase": "done"}


# ─────────────────────────────────────────────
# 条件路由
# ─────────────────────────────────────────────
def should_continue_writing(state: RewriteState) -> str:
    verdict = state.get("judge_verdict", "FAIL")
    _log(f"路由判定: {verdict}")
    return "pdf_generator" if verdict == "PASS" else "writer"
