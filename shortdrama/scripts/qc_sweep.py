# -*- coding: utf-8 -*-
"""静帧全量 QC 审查：逐张送视觉质检，只报告硬伤，不生成任何图。

用途：静帧批次跑完后，先摸清哪几镜有硬伤（文字/缺人物/杂脸），
再决定定向重生成名单——避免全量重画白烧配额。

用法：python scripts/qc_sweep.py [project] [--json]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v5 import config  # noqa: E402
from v5.media import qc, storyboard  # noqa: E402
from v5.media.pipeline import HARD_KEYS  # noqa: E402


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    as_json = "--json" in sys.argv
    # ⚠️ 原默认值 `bootleg99-full` 已随 2026-09-14 的老架构清理删除；请显式传项目名。
    project = args[0] if args else "<请显式传项目名>"
    root = config.PROJECTS_DIR / project
    sd = root / "media" / "ep1" / "stills"
    shots = storyboard.parse(
        (root / "scenedesigner" / "scenedesigner.md").read_text(encoding="utf-8"))
    shots = shots[: config.VIDEO_MAX_SHOTS]

    rows, hard_names = [], []
    for s in shots:
        p = sd / (s["name"] + ".jpg")
        if not p.exists():
            rows.append({"name": s["name"], "status": "missing"})
            continue
        try:
            rep = qc.review(str(p), shot=s)
        except Exception as e:  # noqa: BLE001
            rows.append({"name": s["name"], "status": "qc_error",
                         "error": str(e)[:120]})
            continue
        issues = rep.get("issues") or []
        hard = [i for i in issues
                if str(i.get("level", "")).startswith("P0")
                and any(k in str(i.get("desc", "")) for k in HARD_KEYS)]
        soft = [i for i in issues if i not in hard]
        rows.append({"name": s["name"], "status": "ok" if not hard else "hard",
                     "hard": [str(i.get("desc"))[:110] for i in hard],
                     "other": [str(i.get("desc"))[:110] for i in soft]})
        if hard:
            hard_names.append(s["name"])

    if as_json:
        print(json.dumps({"rows": rows, "hard_names": hard_names},
                         ensure_ascii=False, indent=2))
        return

    print("=== 静帧 QC 全量审查（%d 镜）===" % len(shots))
    for r in rows:
        tag = {"ok": "✅", "hard": "❌", "missing": "⬜", "qc_error": "⚠️"}[r["status"]]
        line = "  %s %-5s %s" % (tag, r["name"], r["status"])
        if r.get("hard"):
            line += " | " + " / ".join(r["hard"])
        print(line)
    print()
    print("硬伤镜（需定向重生成）: %s" % (" ".join(hard_names) or "无"))
    print("达标镜: %d/%d" % (len(shots) - len(hard_names), len(shots)))


if __name__ == "__main__":
    main()
