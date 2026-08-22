"""ETCLOVG C3: Context Layer — 原文智能检索

不全量塞入，而是按需搜索。
用jieba分词 + TF-IDF构建本地索引，agent调search工具按关键词取相关段落。
"""
import re
import math
from collections import Counter
from pathlib import Path


class TextIndex:
    """轻量级中文文本索引，支持段落级检索"""

    def __init__(self, text: str, chunk_size: int = 500, overlap: int = 100):
        """
        将原文切分为段落块，建立倒排索引。
        chunk_size: 每块字符数
        overlap: 块之间重叠字符数（避免切断语义）
        """
        self.raw = text
        self.chunks = self._split_chunks(text, chunk_size, overlap)
        self.inverted: dict[str, list[int]] = {}  # token -> [chunk_ids]
        self._build_index()

    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """
        搜索相关段落。
        返回: [{"chunk_id": int, "text": str, "score": float}, ...]
        """
        tokens = self._tokenize(query)
        if not tokens:
            return []

        # 计算每个chunk的相关性分数
        scores: dict[int, float] = {}
        for token in tokens:
            if token in self.inverted:
                for cid in self.inverted[token]:
                    tf = self.chunks[cid].count(token) / max(len(self.chunks[cid]), 1)
                    idf = math.log(len(self.chunks) / max(len(self.inverted[token]), 1))
                    scores[cid] = scores.get(cid, 0) + tf * idf

        # 排序取top_k
        ranked = sorted(scores.items(), key=lambda x: -x[1])[:top_k]
        results = []
        for cid, score in ranked:
            results.append({
                "chunk_id": cid,
                "text": self.chunks[cid],
                "score": round(score, 4),
                "char_offset": cid * (500 - 100),  # approximate
            })
        return results

    def get_chapter_context(self, chapter_keywords: list[str], max_chars: int = 25000) -> str:
        """
        根据章节关键词获取相关上下文，控制总长度。
        供writer使用。
        """
        seen = set()
        all_chunks = []
        for kw in chapter_keywords:
            results = self.search(kw, top_k=8)
            for r in results:
                if r["chunk_id"] not in seen:
                    seen.add(r["chunk_id"])
                    all_chunks.append(r)

        # 按chunk_id排序（保持原文顺序）
        all_chunks.sort(key=lambda x: x["chunk_id"])

        # 截取到max_chars
        total = 0
        selected = []
        for c in all_chunks:
            if total + len(c["text"]) > max_chars:
                break
            selected.append(c)
            total += len(c["text"])

        return "\n\n---\n\n".join(c["text"] for c in selected)

    def total_chars(self) -> int:
        return len(self.raw)

    def chunk_count(self) -> int:
        return len(self.chunks)

    # ── internals ──

    def _split_chunks(self, text: str, size: int, overlap: int) -> list[str]:
        chunks = []
        step = size - overlap
        for i in range(0, len(text), step):
            chunk = text[i:i + size]
            if chunk.strip():
                chunks.append(chunk)
        return chunks

    def _build_index(self):
        for cid, chunk in enumerate(self.chunks):
            tokens = set(self._tokenize(chunk))
            for token in tokens:
                if token not in self.inverted:
                    self.inverted[token] = []
                self.inverted[token].append(cid)

    def _tokenize(self, text: str) -> list[str]:
        """简单分词：中文按字/词切分，英文按空格"""
        # 先尝试用jieba
        try:
            import jieba
            return [w for w in jieba.cut(text) if len(w.strip()) > 0 and w not in _STOPWORDS]
        except ImportError:
            pass
        # fallback: 简单切分
        tokens = []
        # 英文单词
        tokens.extend(re.findall(r'[a-zA-Z]{2,}', text))
        # 中文连续字符（2-4字滑窗）
        chinese = re.findall(r'[\u4e00-\u9fff]+', text)
        for seg in chinese:
            for i in range(len(seg)):
                for n in (2, 3, 4):
                    if i + n <= len(seg):
                        tokens.append(seg[i:i+n])
        return [t for t in tokens if t not in _STOPWORDS]


_STOPWORDS = set("""
的 了 在 是 我 有 和 就 不 人 都 一 一个 上 也 很 到 说 要 去 你 会 着 没有
过 对 自己 这 里 后 来 把 那 好 还 没 什么 吧 因为 所以 可以 这个 那个
the a an is are was were be been being have has had do does did will would
shall should may might can could of to in for on with at by from as into
about between through after above below and but or not no nor so yet both
each every all any few more most other some such than too very just
""".split())
