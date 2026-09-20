# -*- coding: utf-8 -*-
"""CC: 顽固镜多轮重滚——生成 → QC → 不过就再生成，直到通过或达上限。

针对 BB 之后仍失败的 5 镜（2 文字 + 3 分屏）。分屏/文字都是**概率性**的，
同一提示词多滚几次有机会通过；带定向强化约束可提高单次通过率。

用法：python scripts/reroll_bad.py [--rounds N]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v5 import config  # noqa: E402
from v5.media import (assets, pipeline, qc, relations, stills,  # noqa: E402
                                  storyboard, style)

TEXT_BAD = ("LN19", "LN22", "LN25")
SPLIT_BAD = ("LN06",)
ROUNDS = 6


def is_clean(path: str) -> tuple[bool, str]:
    try:
        rep = qc.review(path)
    except Exception as e:  # noqa: BLE001
        return False, "qc_error:%s" % str(e)[:60]
    hard = [i for i in (rep.get("issues") or [])
            if str(i.get("level", "")).startswith("P0")
            and any(k in str(i.get("desc", "")) for k in pipeline.HARD_KEYS)]
    return (not hard), (str(hard[0].get("desc"))[:90] if hard else "")


def main() -> None:
    # ⚠️ 原硬编码项目 `bootleg99-full` 已随 2026-09-14 的老架构清理删除。
    root = config.PROJECTS_DIR / "<请显式传项目名>"
    sd = root / "media" / "ep1" / "stills"
    shots = storyboard.parse(
        (root / "scenedesigner" / "scenedesigner.md").read_text(encoding="utf-8"))
    shots = shots[: config.VIDEO_MAX_SHOTS]
    blk = style.wrap(style.load(root))
    shots = [{**s, "_style_block": blk} for s in shots]
    idl = assets.identity_lines(root, shots)
    shots = [{**s, "_identity_line": idl.get(s["name"], "")} for s in shots]
    planned = relations.plan_frames(shots)
    pmap = {p["name"]: p for p in planned}

    todo = [(n, pipeline.ANTI_TEXT_HARD) for n in TEXT_BAD]
    todo += [(n, pipeline.ANTI_SPLIT_HARD) for n in SPLIT_BAD]

    for rnd in range(1, ROUNDS + 1):
        pending = []
        print("=== 第 %d 轮 ===" % rnd)
        for name, extra in todo:
            path = str(sd / (name + ".jpg"))
            ok, why = is_clean(path) if (sd / (name + ".jpg")).exists() else (False, "missing")
            if ok:
                print("  %-5s ✅ 已通过" % name)
                continue
            print("  %-5s 重滚（%s）" % (name, why[:60]))
            s = next(x for x in shots if x["name"] == name)
            stills.ensure(root, [s], ep=1, force=True, extra=extra,
                          planned=[pmap[name]], log=print)
            pending.append((name, extra))
        todo = pending
        if not todo:
            print("全部通过 ✅")
            return
    print("达上限仍有: %s" % [t[0] for t in todo])


if __name__ == "__main__":
    main()
