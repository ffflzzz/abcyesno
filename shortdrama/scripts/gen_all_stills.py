# -*- coding: utf-8 -*-
"""全量强制重生成静帧（不跑 QC）。

为什么单独写这个脚本（教训）：
  pipeline 的 `--stills-only` 内置 QC 自动重滚，但本片的视觉 QC 对**小字**
  概率性漏报（实测 LN12 满墙汉字仍被判"通过"）→ 该镜因此被跳过、从未用新
  提示词重新生成。风格块/尾缀改动后必须**全量重生成**才能验证修复是否生效。

用法：python scripts/gen_all_stills.py [project]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v5 import config  # noqa: E402
from v5.media import assets, relations, stills, storyboard, style  # noqa: E402


def main() -> None:
    # ⚠️ 原默认值 `bootleg99-full` 已随 2026-09-14 的老架构清理删除；请显式传项目名。
    project = next((a for a in sys.argv[1:] if not a.startswith("--")),
                   "<请显式传项目名>")
    root = config.PROJECTS_DIR / project
    shots = storyboard.parse(
        (root / "scenedesigner" / "scenedesigner.md").read_text(encoding="utf-8"))
    shots = shots[: config.VIDEO_MAX_SHOTS]
    blk = style.wrap(style.load(root))
    if blk:
        shots = [{**s, "_style_block": blk} for s in shots]
        print("[gen] 风格块 %d 字" % len(blk))
    idl = assets.identity_lines(root, shots)
    shots = [{**s, "_identity_line": idl.get(s["name"], "")} for s in shots]
    print("[gen] 身份锚点 %d/%d 镜" % (len(idl), len(shots)))
    planned = relations.plan_frames(shots)
    stills.ensure(root, shots, ep=1, force=True, planned=planned, log=print)
    ok = sum(1 for s in shots
             if (root / "media" / "ep1" / "stills" / (s["name"] + ".jpg")).exists())
    print("[gen] 完成 %d/%d" % (ok, len(shots)))


if __name__ == "__main__":
    main()
