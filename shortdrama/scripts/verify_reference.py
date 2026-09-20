# -*- coding: utf-8 -*-
"""端到端验证：**改造后的真实生产链路**在 reference 模式下能否跑通。

与 `ab_mode.py` 的区别：那个脚本用自己拼的 ref_prefix 提示词；
这个脚本走**真实路径** —— `prompt.build_video_prompt(mode="reference")`
（含 <Picture 1> 声明）+ `providers.submit_video(mode="reference", images=[静帧])`，
即 `video.submit_chain` 现在用的那套参数组装。确保改造后的生产路径本身可用。

用法：py scripts/verify_reference.py noodle-night LN02
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE))

from v5.media import prompt as pm, providers, storyboard, style  # noqa: E402
from ab_dialogue import _poll, _transcribe  # noqa: E402


def main() -> int:
    project = sys.argv[1] if len(sys.argv) > 1 else "noodle-night"
    shot_name = sys.argv[2] if len(sys.argv) > 2 else "LN02"
    root = Path("projects") / project

    shots = storyboard.parse(
        (root / "scenedesigner" / "scenedesigner.md").read_text(encoding="utf-8"))
    shot = next((s for s in shots if s.get("name") == shot_name), None)
    if not shot:
        print("找不到镜 %s" % shot_name)
        return 1
    shot = {**shot, "_style_block": style.wrap(style.load(root)),
            "_still_tail": style.still_tail_kind(root)}

    stills = json.loads((root / "media" / "ep1" / "stills.json").read_text(encoding="utf-8"))
    own = (stills.get(shot_name) or {}).get("url") or ""
    if not own:
        print("该镜无静帧 URL")
        return 1

    p = pm.build_video_prompt(shot, mode="reference")
    print("=== 真实链路提示词（%d 字）===" % len(p))
    print(p)
    print("\n含 <Picture 1>：%s" % ("<Picture 1>" in p))
    print("images 数组将是：[<本镜静帧>]  = %s…" % own[:60])

    r = providers.submit_video(p, mode="reference", images=[own],
                               seconds=shot.get("seconds") or 8)
    vid = r.get("video_id") or r.get("task_id") or ""
    print("\nsubmitted: %s" % vid, flush=True)
    if not vid:
        print("提交失败：%s" % r)
        return 1

    dest = root / ".tmp" / "verify_ref" / ("%s.mp4" % shot_name)
    dest.parent.mkdir(parents=True, exist_ok=True)
    got = _poll(vid, dest)
    if not got:
        print("生成失败/超窗")
        return 1
    print("生成成功：%s（%.1f KB）" % (dest, dest.stat().st_size / 1024))
    print("ASR 转写：%s" % _transcribe(dest, dest.parent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
