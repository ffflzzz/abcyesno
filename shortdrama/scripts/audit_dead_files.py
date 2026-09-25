"""死文件审计 v2：按【加载机制】判定，不按文本引用判定。

v1 的教训：把两类文件混在一起扫 → 大量假阳性。
本项目有两种契约加载方式，判据必须分开：

  · **约定加载**（路径由代码动态拼接，文本零引用是正常的，**不能当死文件**）：
      - `packs/<pack>/<角色>/SKILL.md`   ← `roles._role_skill` 按角色名拼路径
      - `packs/<pack>/style-block.md`     ← `media.style` 约定读取
      - `packs/<pack>/pack.json`          ← 同上
  · **引用加载**（要靠 import / 被提及才可达）：
      - `v5/**.py`、`scripts/*.py`、`packs/*/references/*.md`

用法：`python scripts/audit_dead_files.py`（可从任意 cwd 运行）

另有两类"看起来零引用但另有归属"，单独归类：
  · `__init__.py` = 包标记
  · 文档（根目录 *.md）= 供人阅读，不计入死文件
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

# 仓库根 = 本脚本所在目录的父目录（从任意 cwd 运行都能工作）
ROOT = Path(__file__).resolve().parent.parent
SKIP = {".git", ".venv", ".tmp", "projects", "__pycache__", ".workbuddy", "node_modules"}
ROLES = {"worldbuilder", "assetdesigner", "plotdesigner", "scriptwriter",
         "dialogue", "scenedesigner", "reviewer", "director"}


def walk(exts: set[str]) -> list[Path]:
    out: list[Path] = []
    for dp, dns, fns in os.walk(ROOT):
        dns[:] = [d for d in dns if d not in SKIP]
        for fn in fns:
            p = Path(dp) / fn
            if p.suffix in exts:
                out.append(p)
    return sorted(out)


CORPUS: dict[Path, str] = {}
for _p in walk({".py", ".md", ".json", ".sh", ".yml", ".yaml", ".txt"}):
    try:
        CORPUS[_p] = _p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        CORPUS[_p] = ""


def mentioned(t: Path) -> list[str]:
    """被别的文件以文件名 / 模块路径提及。"""
    rel = t.as_posix().lstrip("./")
    stem = t.stem
    pats = [re.escape(t.name), re.escape(rel)]
    if t.suffix == ".py":
        pats += [re.escape(rel[:-3].replace("/", ".")),
                 r"from\s+\.%s\b" % re.escape(stem),
                 r"from\s+\.\s+import\s+[^\n]*\b%s\b" % re.escape(stem)]
    rx = re.compile("|".join(pats))
    return [q.as_posix().lstrip("./") for q, txt in CORPUS.items()
            if q != t and rx.search(txt)]


# 收集被声明的叙事技法（pack.json 包级 + 各项目 brief.json 项目级）
DECLARED: set[str] = set()
for _p in walk({".json"}):
    try:
        d = json.loads(CORPUS.get(_p, "") or "{}")
    except Exception:
        continue
    if not isinstance(d, dict):        # 有些 .json 顶层是数组（如 pack 清单快照）
        continue
    v = d.get("script-craft")
    if isinstance(v, list):
        DECLARED.update(str(x).strip() for x in v)
for _b in sorted((ROOT / "projects").glob("*/brief.json")):
    try:
        d = json.loads(_b.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        continue
    if not isinstance(d, dict):
        continue
    v = d.get("script-craft")
    if isinstance(v, list):
        DECLARED.update(str(x).strip() for x in v)

BUCKETS: dict[str, list[tuple[str, str]]] = {}


def put(bucket: str, rel: str, note: str = "") -> None:
    BUCKETS.setdefault(bucket, []).append((rel, note))


for t in walk({".py", ".md"}):
    rel = t.as_posix().lstrip("./")
    parts = rel.split("/")

    # ── 1. 包契约：按加载机制判定 ──────────────────────────────────
    if len(parts) >= 5 and parts[:3] == ["v5", "skills", "packs"]:
        pack = parts[3]
        rest = parts[4:]
        if pack == "craft" and len(rest) > 1:
            name = rest[0]
            if name in DECLARED:
                put("A. 活（已声明技法）", rel, "被 pack.json / brief 声明")
            else:
                put("C. 未被启用的技法包", rel, "无任何 pack/brief 声明 script-craft")
        elif rest[0] == "references":
            m = mentioned(t)
            if m:
                put("A. 活（references 被同包引用）", rel, f"{len(m)} 处")
            else:
                put("B. references 零引用", rel, "查其自述是否声明为归档")
        elif rest[0] in ("style-block.md", "pack.json"):
            put("A. 活（约定加载）", rel, "style.py 读取")
        elif len(rest) == 2 and rest[1] == "SKILL.md":
            role = rest[0]
            if role in ROLES:
                put("A. 活（角色 SKILL，约定加载）", rel, "")
            else:
                put("C. 目录名不是合法角色", rel, f"role={role}")
        else:
            m = mentioned(t)
            put("A. 活" if m else "C. 包内零引用", rel, f"{len(m)} 处" if m else "")
        continue

    # ── 2. 包标记 ────────────────────────────────────────────────
    if t.name == "__init__.py":
        put("A. 包标记（正常）", rel, "")
        continue

    # ── 3. 文档：不计入死文件 ─────────────────────────────────────
    if parts[0] == "docs" or t.suffix == ".md" and len(parts) == 1:
        m = mentioned(t)
        tag = "归档目录（正常）" if "archive" in parts else (f"{len(m)} 处提及" if m else "零引用")
        put("D. 文档（供人阅读，不计死文件）", rel, tag)
        continue
    if t.suffix == ".md" and parts[0] == "v5":
        m = mentioned(t)
        put("D. 文档（供人阅读，不计死文件）", rel, f"{len(m)} 处提及" if m else "零引用（可能过期）")
        continue

    # ── 4. 代码 / 脚本：按引用与入口判定 ──────────────────────────
    m = mentioned(t)
    src = CORPUS.get(t, "")
    has_main = "__main__" in src
    if m:
        put("A. 活（被引用）", rel, f"{len(m)} 处")
    elif has_main:
        put("B. 入口脚本（零引用但有 __main__）", rel, "可独立运行")
    else:
        put("B. ★★ 疑似死模块（零引用且无入口）", rel, "")

print("=" * 92)
print(f"死文件审计 v2 ｜ 扫描 {len(CORPUS)} 个源文件 ｜ 声明技法集：{sorted(DECLARED) or '（空）'}")
print("=" * 92)
for k in sorted(BUCKETS):
    items = BUCKETS[k]
    print(f"\n【{k}】共 {len(items)}")
    for rel, note in items:
        print(f"    {rel:<62} {note}")
