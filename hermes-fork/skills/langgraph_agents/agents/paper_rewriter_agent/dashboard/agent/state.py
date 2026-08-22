"""ETCLOVG L4: Lifecycle — 持久化状态管理

每完成一章立即写磁盘，崩了从断点续。
状态文件结构：
  state_dir/
    meta.json          — 元信息（标题、受众、语言、总章数）
    outline.md         — 大纲
    chapters/
      ch01.md          — 已完成的章节
      ch02.md
    reviews/
      ch01_review.md   — 每章审核报告
    logs/
      trace.jsonl      — 每个动作的trace日志（Observability层）
"""
import json
import os
from pathlib import Path
from datetime import datetime


class RunState:
    """管理一次论文重写的持久化状态"""

    def __init__(self, state_dir: str):
        self.dir = Path(state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "chapters").mkdir(exist_ok=True)
        (self.dir / "reviews").mkdir(exist_ok=True)
        (self.dir / "logs").mkdir(exist_ok=True)

    # ── meta ──
    def save_meta(self, paper_title: str, original_path: str,
                  target_audience: str, language: str, max_rounds: int):
        meta = {
            "paper_title": paper_title,
            "original_path": original_path,
            "target_audience": target_audience,
            "language": language,
            "max_rounds": max_rounds,
            "created_at": datetime.now().isoformat(),
        }
        self._write_json("meta.json", meta)

    def load_meta(self) -> dict:
        return self._read_json("meta.json")

    # ── outline ──
    def save_outline(self, text: str):
        self._write_text("outline.md", text)

    def load_outline(self) -> str:
        return self._read_text("outline.md")

    # ── chapters ──
    def save_chapter(self, ch_num: int, title: str, content: str):
        fname = f"ch{ch_num:02d}.md"
        header = f"# {title}\n\n"
        self._write_text(f"chapters/{fname}", header + content)

    def load_chapter(self, ch_num: int) -> str | None:
        path = self.dir / "chapters" / f"ch{ch_num:02d}.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    def list_chapters(self) -> list[int]:
        """返回已完成的章节编号列表"""
        chapters_dir = self.dir / "chapters"
        nums = []
        for f in sorted(chapters_dir.glob("ch*.md")):
            num = int(f.stem.replace("ch", ""))
            nums.append(num)
        return nums

    def next_chapter(self, total: int) -> int | None:
        """返回下一个待写的章节编号，全部完成返回None"""
        done = set(self.list_chapters())
        for i in range(1, total + 1):
            if i not in done:
                return i
        return None

    # ── reviews ──
    def save_review(self, ch_num: int, review: str):
        self._write_text(f"reviews/ch{ch_num:02d}_review.md", review)

    def load_review(self, ch_num: int) -> str | None:
        path = self.dir / "reviews" / f"ch{ch_num:02d}_review.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    # ── trace log (Observability层) ──
    def log_event(self, event_type: str, data: dict):
        entry = {
            "ts": datetime.now().isoformat(),
            "type": event_type,
            **data,
        }
        log_path = self.dir / "logs" / "trace.jsonl"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # ── summary ──
    def to_dict(self) -> dict:
        """返回当前状态摘要（供API使用）"""
        meta = self.load_meta()
        chapters = self.list_chapters()
        return {
            "dir": str(self.dir),
            "paper_title": meta.get("paper_title", ""),
            "total_chapters": len(chapters),
            "chapters": chapters,
        }

    # ── helpers ──
    def _write_json(self, name: str, data: dict):
        path = self.dir / name
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_json(self, name: str) -> dict:
        path = self.dir / name
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return {}

    def _write_text(self, name: str, content: str):
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _read_text(self, name: str) -> str:
        path = self.dir / name
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""
