# -*- coding: utf-8 -*-
"""顽固镜多轮重滚（名单驱动）：生成 → QC → 不过就再生成，直到通过或达上限。

为什么需要多轮：烧字/分屏都是**概率性**的，同一提示词多滚几次有机会通过；
带定向强化约束（ANTI_TEXT_HARD / ANTI_SPLIT_HARD）可提高单次通过率。

为什么改成名单驱动（教训）：上一版把名单硬编码在脚本里，每换一批硬伤镜
就要改代码。现在支持 `--names` 直接传，或 `--from-qc` 从 qc_sweep 的 JSON
里读 hard_names，避免手抄漏镜。

用法：
  python scripts/reroll_list.py --names LN05,LN06,LN09 --rounds 6
  python scripts/reroll_list.py --from-qc .tmp/qc_run3.json
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v5 import config  # noqa: E402
from v5.media import (assets, pipeline, qc, relations, stills,  # noqa: E402
                                  storyboard, style)

ROUNDS = 6
# ⚠️ 原值 `bootleg99-full` 已随 2026-09-14 的老架构清理删除；请用 `--project` 覆盖。
PROJECT = "<请显式传项目名>"


def _arg(flag: str, default: str = "") -> str:
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def is_clean(path: str) -> tuple[bool, str]:
    try:
        rep = qc.review(path)
    except Exception as e:  # noqa: BLE001
        return False, "qc_error:%s" % str(e)[:60]
    hard = [i for i in (rep.get("issues") or [])
            if str(i.get("level", "")).startswith("P0")
            and any(k in str(i.get("desc", "")) for k in pipeline.HARD_KEYS)]
    return (not hard), (str(hard[0].get("desc"))[:90] if hard else "")


def extra_for(desc: str, root) -> str:
    """按 QC 描述的硬伤类型给定向强化约束（纯正向）。"""
    e = ""
    if any(k in desc for k in ("文字", "字符", "字幕")):
        e += pipeline._anti_text_hard(root)
    if any(k in desc for k in ("分屏", "多格", "拼接", "拼图", "上下两", "两幅",
                               "重复", "多出来", "复制")):
        e += pipeline.ANTI_SPLIT_HARD
    return e or pipeline._anti_text_hard(root)


def main() -> None:
    names = [n.strip() for n in _arg("--names").split(",") if n.strip()]
    from_qc = _arg("--from-qc")
    if from_qc:
        d = json.loads(Path(from_qc).read_text(encoding="utf-8"))
        names = names or list(d.get("hard_names") or [])
    if not names:
        print("没有要重滚的镜（用 --names 或 --from-qc 指定）")
        return
    rounds = int(_arg("--rounds", str(ROUNDS)))

    project = _arg("--project", PROJECT)
    root = config.PROJECTS_DIR / project
    sd = root / "media" / "ep1" / "stills"
    sb = root / "scenedesigner" / "scenedesigner_ep1.md"   # 集级新名优先
    if not sb.exists():
        sb = root / "scenedesigner" / "scenedesigner.md"   # 旧项目回退
    shots = storyboard.parse(sb.read_text(encoding="utf-8"))
    shots = shots[: config.VIDEO_MAX_SHOTS]
    blk = style.wrap(style.load(root))
    shots = [{**s, "_style_block": blk} for s in shots]
    idl = assets.identity_lines(root, shots)
    shots = [{**s, "_identity_line": idl.get(s["name"], "")} for s in shots]
    planned = relations.plan_frames(shots)
    pmap = {p["name"]: p for p in planned}

    todo = list(names)
    print("重滚名单: %s（上限 %d 轮）" % (" ".join(todo), rounds))
    for rnd in range(1, rounds + 1):
        pending = []
        print("=== 第 %d 轮 ===" % rnd)
        for name in todo:
            path = sd / (name + ".jpg")
            ok, why = is_clean(str(path)) if path.exists() else (False, "missing")
            if ok:
                print("  %-5s ✅ 已通过" % name)
                continue
            print("  %-5s 重滚（%s）" % (name, why[:60]))
            s = next(x for x in shots if x["name"] == name)
            stills.ensure(root, [s], ep=1, force=True, extra=extra_for(why, root),
                          planned=[pmap[name]], log=print)
            pending.append(name)
        todo = pending
        if not todo:
            print("全部通过 ✅")
            return
    print("达上限仍有: %s" % todo)


if __name__ == "__main__":
    main()
