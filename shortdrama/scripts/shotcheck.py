# -*- coding: utf-8 -*-
"""分镜表体检（**渲之前**跑，语义那层只花文本调用）。

用法：python scripts/shotcheck.py <项目名> [--ep=N] [--no-judge] [--json]

  --no-judge  只跑可数判据（完全零配额、确定性）
  --json      输出机器可读结果（给"退回清单"接线用）

退出码：0 = 可以渲；1 = 先改分镜（或判定失败）。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v5 import config, shotcheck  # noqa: E402
from v5.guards import resolve_path  # noqa: E402
from v5.media import storyboard  # noqa: E402


def main() -> int:
    project = next((a for a in sys.argv[1:] if not a.startswith("--")), "<请显式传项目名>")
    ep = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--ep=")), "1")
    root = config.PROJECTS_DIR / project
    p = resolve_path(root, "scenedesigner", int(ep))
    if not p.exists():
        print("⛔ 找不到分镜：%s" % p)
        return 2
    shots = storyboard.parse(p.read_text(encoding="utf-8"))
    if not shots:
        print("⛔ 分镜解析出 0 镜（表头/列名不对？）：%s" % p)
        return 2
    tgt = 0
    brief = {}                      # 必须预先绑定：brief.json 读不到时下面还要用它判音频模式
    try:
        from v5 import validate
        brief = json.loads((root / "brief.json").read_text(encoding="utf-8"))
        tgt = int(validate.parse_target_seconds(brief.get("target_duration")) or 0)
    except Exception as e:  # noqa: BLE001 -- brief 坏不该挡住可数判据
        print("⚠️ brief 读不到（%s）→ 跳过片长这条" % str(e)[:60])
    print("=== %s 第 %s 集：%d 镜 / %d 秒（目标 %ds）"
          % (project, ep, len(shots), sum(int(s.get("seconds") or 0) for s in shots), tgt))
    from v5.media import style as _style
    # ★ 入参必须和**创作链里那份调用**（`roles.role_input` → `shotcheck.punch_list`）对齐，
    #   否则这个体检工具会报出角色根本没见过的阻断项：2026-10-05 实测 `madfate-abc-1005`
    #   是 narration-led（旁白在「音效」列、对白列统一「（无声，环境音）」），
    #   CLI 没传 `audio_mode` ⇒ 落回默认 `dialogue-led` ⇒ 报「❌ 台词镜 ≥50% —— 0/24」，
    #   一条**根本不该判**的红字。`chars` / `target_shots` / `single_at_law` 同漏。
    _am = "dialogue-led"
    _rng = None
    try:
        _am = validate.audio_mode_of(brief)          # brief 已在上面读过（读不到时是 {}）
        _rng = validate.parse_shot_range(brief.get("target_duration") if brief else None)
    except Exception as e:  # noqa: BLE001 -- 模式判不出就用默认，但必须响亮
        print("⚠️ audio_mode 判不出（%s）→ 按 dialogue-led 报，台词镜那条可能误报" % str(e)[:60])
    r = shotcheck.check(shots, target_seconds=tgt,
                        chars=shotcheck.character_names(root),
                        target_shots=_rng,
                        camera_light=("camera-light-physics"
                                      in _style.script_craft_of(root)),
                        single_at_law=shotcheck.pack_requires_single_at(root),
                        audio_mode=_am,
                        use_judge=("--no-judge" not in sys.argv))
    if "--json" in sys.argv:
        print(json.dumps(r, ensure_ascii=False, indent=1))
    return 1 if r["blocking"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
