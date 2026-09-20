# -*- coding: utf-8 -*-
"""keyframe vs reference 对照实验：官方示例那条路到底好不好？

**背景**（2026-09-13）：
  官方示例（`@角色-基础形象` / `@场景` 引用素材）走的是 **reference** 模式，
  而我们一直硬编码 `keyframe`（静帧当首帧）。用户指出"应该按官方示例用 reference"。

  **我不该靠文档的定性描述（"reference 可能重新构图"）下结论**——那是没实测过的推断。
  这个脚本就是来做实测对照的。

**两条路线的机制差异（官方规则，非取舍）**：
  · `keyframe`：必须给 first_frame/last_frame，**不允许** images/audios。
    构图被首帧图锁死；能承接上一镜尾帧。
  · `reference`：必须给 images/audios，**不允许** first_frame/last_frame。
    构图只能靠 prompt 文本；但**可以用 audios**（keyframe 拿不到）。

**单变量**：同一镜、同一张图（静帧），只改 mode 与"图怎么用"：
  · `K_keyframe` —— 图当**首帧**；提示词用现成的
  · `R_reference` —— 图当**参考图**（`<Picture 1>`）；提示词前面加一句声明素材用途
    （官方建议："在 reference 模式中应在提示词里明确写出素材占位符及其用途"）

判定维度：构图是否符合分镜景别 / 角色是否与参考图一致 / 有无烧字 / 是否还念台词（ASR）。

用法：
    py scripts/ab_mode.py noodle-night LN02            # 实跑
    py scripts/ab_mode.py noodle-night LN02 --dry-run  # 只看提示词差异
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parents[0]))
sys.path.insert(0, str(_HERE))

from v5.media import prompt as prompt_mod, providers, storyboard, style  # noqa: E402
from ab_dialogue import _poll, _transcribe  # noqa: E402


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    project = args[0] if args else "noodle-night"
    shot_name = args[1] if len(args) > 1 else "LN02"
    dry = "--dry-run" in sys.argv

    root = Path("projects") / project
    work = root / ".tmp" / "ab_mode"
    work.mkdir(parents=True, exist_ok=True)

    shots = storyboard.parse((root / "scenedesigner" / "scenedesigner.md").read_text(encoding="utf-8"))
    shot = next((s for s in shots if s.get("name") == shot_name), None)
    if not shot:
        print("找不到镜 %s" % shot_name)
        return 1
    shot = {**shot, "_style_block": style.wrap(style.load(root)),
            "_still_tail": style.still_tail_kind(root)}

    still = json.loads((root / "media" / "ep1" / "stills.json").read_text(encoding="utf-8"))
    first = (still.get(shot_name) or {}).get("url") or ""
    if not first:
        print("该镜无首帧 URL")
        return 1

    base = prompt_mod.build_video_prompt(shot)
    # reference 模式必须声明素材用途（官方建议），否则模型不知道 <Picture 1> 干嘛用
    ref_prefix = ("以 <Picture 1> 中的人物外貌、服装与场景为参考，保持外观与场景一致，"
                  "并按分镜描述重新组织镜头与构图。")

    jobs = {
        "K_keyframe":  (base,                    dict(mode="keyframe",  first_frame=first)),
        "R_reference": (ref_prefix + base,       dict(mode="reference", images=[first])),
    }

    print("镜 %s：dialogue=%r  seconds=%s" % (shot_name, shot.get("dialogue"), shot.get("seconds")))
    print("图：%s" % first[:70])
    for k, (p, kw) in jobs.items():
        print("\n--- %s（%d 字）submit_kwargs=%s ---\n%s"
              % (k, len(p), {kk: (vv[:40] if isinstance(vv, str) else vv) for kk, vv in kw.items()}, p))

    if dry:
        print("\n[dry-run] 不提交")
        return 0

    print("\n===== 提交（逐个间隔 65s）=====")
    import time
    results, last = {}, 0.0
    for k, (p, kw) in jobs.items():
        gap = 65 - (time.time() - last)
        if gap > 0:
            time.sleep(gap)
        try:
            r = providers.submit_video(p, seconds=shot.get("seconds") or 8, **kw)
        except Exception as e:  # noqa: BLE001
            print("[%s] 提交失败：%s" % (k, str(e)[:140]), flush=True)
            results[k] = "<提交失败>"
            continue
        vid = r.get("video_id") or r.get("task_id") or ""
        last = time.time()
        print("[%s] submitted %s" % (k, vid), flush=True)
        dest = work / ("%s.%s.mp4" % (shot_name, k))
        results[k] = str(dest) if _poll(vid, dest) else "<生成失败>"

    print("\n===== 音轨转写（顺便看台词还在不在）=====")
    for k, path in results.items():
        if str(path).startswith("<"):
            print("%-14s %s" % (k, path))
        else:
            print("%-14s %s" % (k, _transcribe(Path(path), work)))

    print("\n产物目录：%s（请对比构图/角色一致性/烧字）" % work)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
