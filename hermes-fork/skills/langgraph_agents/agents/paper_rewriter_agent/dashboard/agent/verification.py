"""ETCLOVG V6: Verification Layer — 自审与事实核查

review: LLM自审章节质量
fact_check: 逐条断言验证（LLM对比改写与原文）
"""
from langchain_openai import ChatOpenAI


def make_reviewer(llm: ChatOpenAI):
    """返回一个review函数"""

    def review_chapter(chapter_content: str, chapter_title: str,
                       target_audience: str) -> str:
        prompt = f"""你是论文改写审核员。请审核以下改写章节的质量。

目标读者：{target_audience}
章节标题：{chapter_title}

章节内容：
{chapter_content}

审核维度：
1. 准确性 — 是否忠实于原文论点？有无曲解或添加原文没有的内容？
2. 完整性 — 原文该章的核心要点是否都覆盖到了？
3. 可读性 — 对目标读者来说是否容易理解？有无过于晦涩的段落？
4. 连接性 — 与前后章节的逻辑衔接是否自然？
5. 文字质量 — 语言是否流畅？有无AI腔（"值得注意的是"、"总之"等套话）？

输出格式：
- 总分（1-10）
- 各维度评分（1-10）
- 具体问题列表（带引用原文位置）
- 修改建议

如果总分>=7，说明"通过"。
如果总分<7，说明"需要修改"并列出具体修改项。"""

        resp = llm.invoke(prompt)
        return resp.content

    return review_chapter


def make_fact_checker(llm: ChatOpenAI, search_fn):
    """返回一个fact_check函数"""

    def fact_check_chapter(chapter_num: int, chapter_content: str) -> str:
        # Step 1: 提取关键断言
        extract_prompt = f"""从以下改写文本中提取所有关键断言（事实性陈述、数据、定义、因果关系）。
每条断言单独一行，编号。
只提取可验证的断言，忽略过渡句和修辞。

{chapter_content[:8000]}"""

        resp = llm.invoke(extract_prompt)
        claims = resp.content

        # Step 2: 对每条断言搜索原文验证
        verify_prompt = f"""你是事实核查员。以下是论文原文中的相关段落，以及改写文本中的断言列表。

请逐条核对每个断言是否得到原文支持。

原文相关段落：
{search_fn(chapter_content[:200], top_k=10)}

改写文本断言：
{claims}

对每条断言，判定：
- ✓ 通过：原文明确支持
- ? 存疑：原文未直接提及，但合理推断
- ✗ 错误：与原文矛盾或原文不支持

输出核查报告。"""

        resp2 = llm.invoke(verify_prompt)
        return resp2.content

    return fact_check_chapter
