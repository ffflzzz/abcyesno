# -*- coding: utf-8 -*-
"""台词 A/B 实测：模型到底认不认提示词里的 `Audio: <台词>` 段？

**要回答的问题**（2026-09-13，由"所有镜头都在说『大家好，很高兴和大家分享今天的内容』"引出）：
    noodle-night 的台词**确实写在提示词里**（实测 LN02 含 `Audio: 还能吃吗？`
    + `LANGUAGE: Mandarin Chinese spoken dialogue only`），但成片里没人念它。
    → 所以问题不是"链路缺失"，而是**模型遵从度**：它到底照不照 `Audio:` 段念？

**为什么用 A/B 而不是直接改**：这是模型行为问题，猜不得。同一镜、同一首帧、
只改提示词的某一处，跑出来用 ASR 转写比对，才能知道哪种写法真的有效。

四个变体（只动"台词相关"的写法，其余完全一致）：
    A  现状       —— `build_video_prompt` 原样输出（`Audio: 台词` 在尾部）
    B  台词前置   —— 把 `Audio:` 段提到提示词开头
    C  显式口播   —— 在 A 基础上追加一句明确的"照念、不得即兴"
    D  瘦身       —— 去掉 209 字的 pack 风格块（验证"注意力稀释"假设）

判定：`ffmpeg` 抽音轨 → faster-whisper 转写 → 看是否命中台词原文 / 是否复读"大家好"。

用法：
    py scripts/ab_dialogue.py noodle-night LN02
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v5.media import prompt as prompt_mod, providers, storyboard, style  # noqa: E402

# 变体 C 的追加指令：明确"照念、别即兴"。**用英文**——中文指令会被当可显示内容
# 烧到画面上（见 prompt.py 的 GLOBAL_VIDEO 注释）。
C_EXPLICIT = (" The spoken line is exactly the following sentence, spoken aloud in "
              "Mandarin by the character: 「%s」 — say only this, do not improvise "
              "any other words.")


def _variants(shot: dict) -> dict[str, str]:
    """构造四个变体。除台词写法外，其余段落完全一致。"""
    base = prompt_mod.build_video_prompt(shot)
    audio_m = re.search(r"Audio:\s*([^。\n]+)", base)
    line = (audio_m.group(1).strip() if audio_m else "").strip("。 ")

    out = {"A_current": base}

    # B：把整段 `Audio: xxx` 挪到最前面
    if audio_m:
        seg = base[audio_m.start():audio_m.end()]
        rest = (base[:audio_m.start()] + base[audio_m.end():]).strip()
        out["B_audio_first"] = seg.strip() + " " + rest

    # C：尾部追加显式口播指令（照念，不得即兴）
    if line:
        out["C_explicit"] = base + (C_EXPLICIT % line)

    # D：整段去掉 pack 风格块（209 字）
    d_shot = {**shot, "_style_block": ""}
    out["D_no_styleblock"] = prompt_mod.build_video_prompt(d_shot)

    return out


def _variants_silent(shot: dict) -> dict[str, str]:
    """静默镜变体：验证「**负面禁声指令反而诱发人声**」这个假设。

    现象（2026-09-13，对 noodle-night 成片全轨做 ASR 定位）：
      台词镜 9 句台词**全部正常落地**，而**三个静默镜 LN01/LN06/LN08 各自被配了
      一句「大家好，很高兴和大家分享今天的内容」**。
    这两类镜在提示词上的**唯一差别**就是静默镜多带两句负面禁声：
      · ` Ambient only, do not speak: <音效>`（AMBIENT_LABEL_SILENT）
      · `… ambient sound and background music only — no speech, no talking,
         no voice-over, no spoken words.`（GLOBAL_VIDEO_AMBIENT）
    → 假设：反复强调"语音/说话"概念，反而把它引入了提示词。
    这是本项目铁律「**负面提法会引入该概念**」的第 4 次现形
    （前三次：不要文字→烧字；不要分屏→分屏；不要五官→长出五官）。

    变体（**单一变量**：只动禁声相关句，其余完全一致）：
      A 现状     —— 保留两句负面禁声
      B 去负面   —— 换成正向的 ` Background ambience: `，全局指令换成不含禁声的版本
      C 去"绝不静音" —— 在 B 基础上，再把 `never go silent` 也去掉。
        理由：`never go silent`（绝不能安静）对**无台词镜**是第二种压力——
        模型必须产出点什么，于是补人声。B 里它还留着，所以 C 专门隔离它。
    """
    base = prompt_mod.build_video_prompt(shot)
    out = {"A_current": base}

    b = base.replace(prompt_mod.AMBIENT_LABEL_SILENT,
                     prompt_mod.AMBIENT_LABEL_SPEAKING)
    b = b.replace(prompt_mod.GLOBAL_VIDEO_AMBIENT, prompt_mod.GLOBAL_VIDEO)
    out["B_no_negative"] = b

    c = b.replace(prompt_mod.GLOBAL_VIDEO,
                  " Text-to-video directive: no on-screen text, no subtitles.")
    out["C_no_never_silent"] = c
    return out


def _poll(video_id: str, dest: Path, rounds: int = 90, interval: int = 10) -> str:
    """轮询到完成并落盘；返回本地路径，失败返回空串。"""
    import httpx
    for _ in range(rounds):
        try:
            r = providers.query_video(video_id)
        except Exception:  # noqa: BLE001
            time.sleep(interval)
            continue
        st = r.get("status")
        if st == "completed" and r.get("url"):
            try:
                with httpx.Client(timeout=120, trust_env=False) as c:
                    resp = c.get(r["url"])
                    resp.raise_for_status()
                    dest.write_bytes(resp.content)
                return str(dest)
            except Exception as e:  # noqa: BLE001
                print("    下载失败将重试：%s" % str(e)[:70], flush=True)
                time.sleep(interval)
                continue
        if st in ("failed", "error"):
            print("    FAILED: %s" % str(r.get("error"))[:120], flush=True)
            return ""
        time.sleep(interval)
    return ""


def _transcribe(mp4: Path, work: Path) -> str:
    """抽音轨 → faster-whisper 转写（装了才做，没装返回占位）。"""
    wav = work / (mp4.stem + ".wav")
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(mp4),
                    "-ac", "1", "-ar", "16000", str(wav)], check=False)
    if not wav.exists():
        return "<抽音轨失败>"
    # ⚠️ `.tools/asr`（faster-whisper）已于 **2026-09-14 按用户要求删除**（非用户所装）。
    #    本函数因此会走下方降级分支、返回占位串 → **请人工听**。
    #    要恢复 ASR：按 `2026-09-13.md`「ASR 环境」一节 `pip --target .tools/asr faster-whisper`。
    asr_dir = Path(".tools/asr")
    if not asr_dir.exists():
        return "<未装 ASR，请人工听：%s>" % wav
    sys.path.insert(0, str(asr_dir))
    # ★ 两个都必须设，缺一不可（2026-09-13 实测）：
    #   HF_ENDPOINT 走国内镜像；**HF_HUB_DISABLE_XET=1 是关键**——
    #   huggingface_hub 新版默认走 Xet(CAS) 通道，该通道会绕开镜像直连
    #   `cas-server.xethub.hf.co` → 401 Unauthorized。禁用后才走传统 CDN。
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    try:
        from faster_whisper import WhisperModel
        m = WhisperModel("small", device="cpu", compute_type="int8")
        segs, _ = m.transcribe(str(wav), language="zh", beam_size=1)
        return "".join(s.text for s in segs).strip()
    except Exception as e:  # noqa: BLE001
        return "<ASR 失败：%s>" % str(e)[:60]


def main() -> int:
    project = sys.argv[1] if len(sys.argv) > 1 else "noodle-night"
    shot_name = sys.argv[2] if len(sys.argv) > 2 else "LN02"
    root = Path("projects") / project
    work = root / ".tmp" / "ab"
    work.mkdir(parents=True, exist_ok=True)

    md = (root / "scenedesigner" / "scenedesigner.md").read_text(encoding="utf-8")
    shots = storyboard.parse(md)
    shot = next((s for s in shots if s.get("name") == shot_name), None)
    if not shot:
        print("找不到镜 %s（现有：%s）" % (shot_name, [s.get("name") for s in shots]))
        return 1

    block = style.wrap(style.load(root))
    shot = {**shot, "_style_block": block, "_still_tail": style.still_tail_kind(root)}
    print("镜 %s：dialogue = %r" % (shot_name, shot.get("dialogue")))

    stills = json.loads((root / "media" / "ep1" / "stills.json").read_text(encoding="utf-8"))
    first = (stills.get(shot_name) or {}).get("url") or ""
    if not first:
        print("该镜没有首帧 URL，无法实验")
        return 1

    vs = _variants_silent(shot) if "--silent" in sys.argv else _variants(shot)
    print("\n===== 变体提示词 =====")
    for k, v in vs.items():
        print("\n--- %s（%d 字）---\n%s" % (k, len(v), v))

    if "--dry-run" in sys.argv:
        print("\n[dry-run] 只打印变体，不提交视频")
        return 0

    print("\n===== 提交（供应商 1rpm，逐个间隔 65s）=====")
    results = {}
    last = 0.0
    for k, p in vs.items():
        gap = 65 - (time.time() - last)
        if gap > 0:
            time.sleep(gap)
        try:
            r = providers.submit_video(p, first_frame=first,
                                       seconds=shot.get("seconds") or 8)
        except Exception as e:  # noqa: BLE001
            print("[%s] 提交失败：%s" % (k, str(e)[:110]), flush=True)
            results[k] = "<提交失败>"
            continue
        vid = r.get("video_id") or r.get("task_id") or ""
        last = time.time()
        print("[%s] submitted %s" % (k, vid), flush=True)
        dest = work / (shot_name + "." + k + ".mp4")
        got = _poll(vid, dest)
        results[k] = str(dest) if got else "<生成失败>"

    print("\n===== 音轨转写 =====")
    for k, path in results.items():
        if path.startswith("<"):
            print("%-18s %s" % (k, path))
            continue
        txt = _transcribe(Path(path), work)
        print("%-18s %s" % (k, txt))

    print("\n对照：期望台词 = %r ；故障特征 = 「大家好」"
          % (shot.get("dialogue") or ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
