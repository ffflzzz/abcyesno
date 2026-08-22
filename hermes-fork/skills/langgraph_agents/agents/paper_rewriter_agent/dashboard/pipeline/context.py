"""上下文智能分窗 — 按语义段落切分原文，为每章匹配相关片段

核心思路：
1. 原文按空行/段落切块
2. 每个块提取关键词
3. 根据章节大纲关键词匹配相关块
4. writer每章只拿到相关段落，而不是27万字全文
"""
from __future__ import annotations
import re
from typing import List, Tuple


def chunk_text(text: str, min_chunk_chars: int = 200, max_chunk_chars: int = 2000) -> List[str]:
    """把原文按段落切块。
    
    策略：
    - 按双换行分段
    - 过短的段落合并到前一个块
    - 过长的段落按句子再切
    """
    # 按双换行或多个换行分段
    raw_chunks = re.split(r'\n\s*\n', text.strip())
    
    chunks = []
    buffer = ""
    
    for raw in raw_chunks:
        raw = raw.strip()
        if not raw:
            continue
        
        if len(buffer) + len(raw) < min_chunk_chars:
            buffer = (buffer + "\n\n" + raw).strip()
        else:
            if buffer:
                chunks.append(buffer)
            buffer = raw
    
    if buffer:
        chunks.append(buffer)
    
    # 过长的块按句子切
    final = []
    for chunk in chunks:
        if len(chunk) <= max_chunk_chars:
            final.append(chunk)
        else:
            sentences = re.split(r'(?<=[。！？\.!?])\s*', chunk)
            sub = ""
            for sent in sentences:
                if len(sub) + len(sent) > max_chunk_chars and sub:
                    final.append(sub.strip())
                    sub = ""
                sub += sent + " "
            if sub.strip():
                final.append(sub.strip())
    
    return final


def extract_keywords(text: str, top_n: int = 20) -> List[str]:
    """从文本中提取关键词（简单的基于频率的方法）。
    
    不依赖NLP库，用正则提取中文词和英文词，按频率排序。
    """
    # 提取中文词（2-6字）
    cn_words = re.findall(r'[\u4e00-\u9fff]{2,6}', text)
    # 提取英文词（3+字母）
    en_words = re.findall(r'[A-Za-z][A-Za-z0-9_]{2,}', text)
    
    # 停用词
    cn_stop = {'这个', '那个', '一个', '我们', '他们', '可以', '没有', '不是', 
               '但是', '因为', '所以', '如果', '已经', '还是', '就是', '这是',
               '在下', '通过', '以及', '其中', '同时', '这些', '那些', '对于',
               '来说', '进行', '使用', '提供', '需要', '能够', '或者', '而且',
               '不过', '虽然', '然后', '因此', '由于', '基于', '关于', '为了',
               '从而', '至下', '其是', '文中', '本文', '作者', '研究', '方法',
               '结果', '问题', '部分', '方面', '情况', '系统', '数据', '模型'}
    en_stop = {'the', 'and', 'for', 'that', 'this', 'with', 'from', 'are', 'was',
               'were', 'been', 'have', 'has', 'had', 'not', 'but', 'can', 'will',
               'would', 'could', 'should', 'may', 'might', 'shall', 'into', 'each',
               'which', 'their', 'them', 'they', 'than', 'then', 'also', 'when',
               'where', 'how', 'what', 'who', 'whom', 'why', 'all', 'any', 'both',
               'more', 'most', 'other', 'some', 'such', 'only', 'own', 'same',
               'using', 'used', 'based', 'two', 'one', 'new', 'first', 'last'}
    
    word_freq = {}
    for w in cn_words:
        if w not in cn_stop and len(w) >= 2:
            word_freq[w] = word_freq.get(w, 0) + 1
    for w in en_words:
        wl = w.lower()
        if wl not in en_stop and len(w) >= 3:
            word_freq[w] = word_freq.get(w, 0) + 1
    
    # 按频率排序
    sorted_words = sorted(word_freq.items(), key=lambda x: -x[1])
    return [w for w, _ in sorted_words[:top_n]]


def match_chunks_to_chapter(
    chunks: List[str],
    chapter_id: str,
    chapter_title: str,
    chapter_keywords_hint: str = "",
    max_chunks: int = 15,
) -> Tuple[str, List[int]]:
    """为一个章节匹配最相关的原文段落。
    
    匹配策略：
    1. 从章节标题提取关键词
    2. 从关键词提示（大纲要点）提取关键词
    3. 对每个chunk计算关键词命中数
    4. 返回top-N最相关的chunk
    
    返回：(拼接的相关文本, 命中的chunk索引列表)
    """
    # 合并所有关键词来源
    kw_source = chapter_title + " " + chapter_keywords_hint
    keywords = extract_keywords(kw_source, top_n=30)
    
    if not keywords:
        # 没有关键词，返回前N个chunk
        selected = chunks[:max_chunks]
        return "\n\n".join(selected), list(range(min(max_chunks, len(chunks))))
    
    # 计算每个chunk的相关性分数
    scores = []
    for i, chunk in enumerate(chunks):
        score = 0
        chunk_lower = chunk.lower()
        for kw in keywords:
            if kw.lower() in chunk_lower:
                # 中文词权重更高（更稀有）
                if re.match(r'[\u4e00-\u9fff]', kw):
                    score += 3
                else:
                    score += 1
        scores.append((score, i))
    
    # 按分数排序，取top-N
    scores.sort(key=lambda x: -x[0])
    selected_indices = sorted([i for _, i in scores[:max_chunks] if scores[scores.index((_, i))][0] > 0])
    
    if not selected_indices:
        # 没有匹配到，返回前N个chunk
        selected_indices = list(range(min(max_chunks, len(chunks))))
    
    selected = [chunks[i] for i in selected_indices]
    return "\n\n".join(selected), selected_indices


def build_chapter_context(
    original_text: str,
    outline: str,
    chapter_id: str,
    chapter_title: str,
) -> Tuple[str, int, int]:
    """为一个章节构建精简上下文。
    
    流程：
    1. 切分原文为段落块
    2. 从大纲中提取该章节的关键词提示
    3. 匹配相关段落
    4. 返回精简上下文
    
    返回：(精简上下文, 原文总字数, 精简后字数)
    """
    chunks = chunk_text(original_text)
    
    # 从大纲中提取该章节的关键词提示
    # 找到大纲中该章节的部分
    hint = ""
    pattern = rf'{chapter_id}[:\s]+(.*?)(?=Ch\d+|$)'
    match = re.search(pattern, outline, re.DOTALL)
    if match:
        hint = match.group(1).strip()[:500]
    
    context, indices = match_chunks_to_chapter(
        chunks, chapter_id, chapter_title, hint, max_chunks=15
    )
    
    return context, len(original_text), len(context)


def build_review_context(
    original_text: str,
    rewrite_text: str,
    outline: str,
    max_chars: int = 50000,
) -> str:
    """为reviewer构建上下文。
    
    策略：取原文的前N字 + 重写全文，控制总长度。
    reviewer不需要看到原文每一个字，只需要足够的对比样本。
    """
    # 原文取前max_chars/2字
    orig_sample = original_text[:max_chars // 2]
    
    # 重写全文（通常比原文短很多）
    rewrite_sample = rewrite_text[:max_chars // 2]
    
    return f"原文（节选前{len(orig_sample)}字）：\n{orig_sample}\n\n重写全文（{len(rewrite_text)}字）：\n{rewrite_sample}"
