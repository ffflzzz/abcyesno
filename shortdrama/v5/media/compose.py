# -*- coding: utf-8 -*-
"""Deterministic concat of rendered clips (ffmpeg).

三条剪辑能力（2026-09-21 起，默认温和开启）：
  一、规格统一 —— 各镜分辨率/帧率不齐（Agnes keyframe 模式实测 704x960/992/1024 混出），
      以众数画布为基准：crop=等比放大居中裁切（竖屏推荐，损约 6% 边缘）或 pad=等比缩小补黑边。
  二、掐头去尾 —— 每镜首尾各剪 TRIM 秒（去首帧闪烁/尾帧凝滞），输入侧 -ss/-t 音画同步。
  三、镜间转场 —— 相邻镜 xfade 叠化 + acrossfade 声音交叉，链式推进。

开关（env；TRIM/XFADE 填 0 = 关，FIT 填 off = 关）：
  SHORTDRAMA_COMPOSE_FIT    crop|pad|off   默认 crop（与 scripts/concat_robust.py 竖屏推荐一致）
  SHORTDRAMA_COMPOSE_TRIM   秒             默认 0.15（取小值：xfade 还会吃交界 0.3s，防切 dialogue-led 台词）
  SHORTDRAMA_COMPOSE_XFADE  秒             **默认 0（硬切）**——2026-10-02 由 0.3 改默认，
    理由见下方 XFADE 处的实测记录；要淡入淡出自己填秒数。

兼容与兜底哲学：
  - concat() 签名不动（pipeline.py 调用点零改动）。
  - 规格全一致且三开关全关 → 走原 `-c copy` 快路径，行为与旧版逐字节一致。
  - 新路径任何一步失败 → 降级回 `-c copy` 直拼：宁要完整不要缺镜，拼接失败绝不能炸整条链。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections import Counter
from pathlib import Path


def _env_seconds(name: str, default: float) -> float:
    try:
        v = float(os.environ.get(name, "") or default)
    except ValueError:
        v = default
    return v if v > 0 else 0.0


FIT = (os.environ.get("SHORTDRAMA_COMPOSE_FIT", "crop") or "crop").strip().lower()
TRIM = _env_seconds("SHORTDRAMA_COMPOSE_TRIM", 0.15)
# ★ 默认 0 = **硬切**（2026-10-02 实测改，此前默认 0.3 秒叠化）。
#   两条理由：
#   ① 换场那一刀上，0.3 秒叠化把**两个不同空间糊在同一帧**里（`leak-upstairs-1002`
#      楼道→卧室的接缝，抽帧看得见重影）——那不是转场，是鬼影。
#      对照图：`tmp/seam/CMP_seam_frames.jpg`（叠化 vs 硬切，同一段素材重拼）。
#   ② 它还在偷时长：四段拼一片，叠化 + 掐头去尾吃掉 2.0 秒（38.58s → 40.61s 重拼实测），
#      快节奏片这个损耗占比不小。
#   电影语法上这也更对：**同一场戏的镜头之间本来就是硬切**，淡入淡出留给"时间流逝"，
#   而我们的组间叠化把两者都抹成了淡出。要恢复淡入淡出填秒数即可（env 不变）。
XFADE = _env_seconds("SHORTDRAMA_COMPOSE_XFADE", 0.0)


def _has_audio(clip: Path) -> bool | None:
    """ffprobe 问一句「这条片段有没有音频流」。问不动 ⇒ None（**不当作「有」**）。

    只报不拦（2026-10-05 用户定的四条之一）：拦下来会把已经跑完的二十几镜一起作废，
    而「成片是哑的」这件事最怕的不是问题，是**没人知道**。
    """
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(clip)],
            capture_output=True, timeout=30)
    except Exception:  # noqa: BLE001
        return None
    if r.returncode != 0:
        return None
    return bool((r.stdout or b"").strip())


def _audio_note(clips: list[Path]) -> None:
    """拼接前统计无声片段并**打一行**（本地 ComfyUI 的工作流可能不存音轨）。"""
    results = [(p.name, _has_audio(p)) for p in clips]
    silent = [n for n, v in results if v is False]
    unknown = [n for n, v in results if v is None]
    if silent:
        print("[compose] ⚠ %d/%d 条片段**没有音轨**（%s%s）⇒ 拼出来的成片这些段落是静的"
              % (len(silent), len(clips), "、".join(s[:6] for s in silent[:5]),
                 "…" if len(silent) > 5 else ""), flush=True)
    elif unknown:
        # 问不出来 ≠ 有声。这一行的作用是别让「没查」被读成「查过、没问题」。
        print("[compose] ⚠ %d 条片段用 ffprobe 问不出音轨（ffprobe 不可用或文件损坏）"
              " ⇒ 无法确认成片有没有声" % len(unknown), flush=True)


def concat(clip_dir: Path, out: Path) -> int:
    # 产物识别（2026-09-22 起 pack 档）：clips/ 下若存在 `pack*.mp4`（组级产物，
    # 一个文件含该组全部镜），按组编号顺序拼 —— pack 模式的 clips/ 里不会有
    # LN*.mp4（submit_packs 不产单镜文件），两者互斥、pack 优先。
    # pack%02d 两位编号保证字典序 = 组序；旁路脚本产物 `pack01_LN01-LN03.mp4`
    # 若被手动放入 clips/，字典序同样按编号排，兼容。
    clips = (sorted(clip_dir.glob("pack*.mp4"), key=lambda p: p.stem)
             or sorted(clip_dir.glob("LN*.mp4"), key=lambda p: p.stem))
    if not clips:
        return 0
    _audio_note(clips)
    out.parent.mkdir(parents=True, exist_ok=True)
    if len(clips) == 1:
        out.write_bytes(clips[0].read_bytes())
        return 1

    specs = [_probe(p) for p in clips]
    uniform = len({(s["w"], s["h"], round(s["fps"], 3)) for s in specs}) == 1
    if uniform and TRIM <= 0 and XFADE <= 0:
        return _copy_concat(clips, out, clip_dir)

    try:
        return _compose(clips, specs, out, clip_dir)
    except BaseException:  # noqa: BLE001  拼接降级：宁要完整不要缺镜
        return _copy_concat(clips, out, clip_dir)


# ---------------------------------------------------------------- 旧路径（逐字节不变）

def _copy_concat(clips: list[Path], out: Path, lst_dir: Path) -> int:
    # 清单必须放片段同目录：concat demuxer 的 file 行是相对清单位置解析的
    lst = lst_dir / "_concat.txt"
    lst.write_text("\n".join("file '" + p.name + "'" for p in clips), encoding="utf-8")
    r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                        "-i", str(lst), "-c", "copy", str(out)], capture_output=True)
    try:
        lst.unlink()
    except BaseException:  # noqa: BLE001
        # ★ **必须 catch `BaseException` 而不只是 `Exception`**（2026-09-14 实测两次）：
        # 沙箱的批量删除保护会**拦下删除并抛非 Exception 的异常**（实测把
        # `clips/_concat.txt` 的清理拦掉）→ 异常一路冒到顶层 → **已经跑完 25 镜、
        # 已经拼完成片**的那一轮在 `print("RESULT:")` **之前**结束
        # → 编排层据此判定"整条链失败"（实质成功被记成失败）。
        # 这里只是删一个临时清单文件：**清理失败绝不能影响成片**，留着也无害
        # （`clips/` 的遍历是 `glob("LN*.mp4")`，`_concat.txt` 不会命中）。
        pass
    return len(clips) if r.returncode == 0 else 0


# ---------------------------------------------------------------- 新路径

def _probe(p: Path) -> dict:
    """探测单镜：宽/高/帧率/时长。探测失败给 (0,0,30,0)，由后续逻辑兜底。"""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,r_frame_rate:format=duration",
             "-of", "json", str(p)],
            capture_output=True, timeout=30)
        j = json.loads(r.stdout.decode("utf-8", "replace") or "{}")
        vs = (j.get("streams") or [{}])[0]
        num, _, den = (vs.get("r_frame_rate") or "30/1").partition("/")
        numf, denf = float(num or 30), float(den or 1)
        fps = numf / denf if denf else 30.0
        return {"w": int(vs.get("width") or 0), "h": int(vs.get("height") or 0),
                "fps": fps, "dur": float(j.get("format", {}).get("duration") or 0.0)}
    except Exception:  # noqa: BLE001
        return {"w": 0, "h": 0, "fps": 30.0, "dur": 0.0}


def _compose(clips: list[Path], specs: list[dict], out: Path, clip_dir: Path) -> int:
    base_w, base_h = Counter((s["w"], s["h"]) for s in specs).most_common(1)[0][0]
    base_fps = Counter(round(s["fps"], 3) for s in specs).most_common(1)[0][0]

    stage = clip_dir / "_compose_staged"
    stage.mkdir(parents=True, exist_ok=True)
    staged: list[Path] = []
    try:
        for p in clips:
            dst = stage / p.name
            _stage_one(p, dst, base_w, base_h, base_fps)
            staged.append(dst)

        if XFADE > 0:
            _xfade_chain(staged, out)
        else:
            # 只做规格统一/trim：staged 全规格一致，copy 拼接即可
            rc = _copy_concat(staged, out, stage)
            if rc == 0:
                raise RuntimeError("staged copy-concat failed")
        return len(clips)
    finally:
        try:
            shutil.rmtree(stage, ignore_errors=True)
        except BaseException:  # noqa: BLE001
            # 清理失败不影响成片：stage 在子目录里，glob("LN*.mp4")/glob("pack*.mp4")
            # 都不会命中
            pass


def _has_audio(p: Path) -> bool:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a",
                            "-show_entries", "stream=codec_name",
                            "-of", "csv=p=0", str(p)],
                           capture_output=True, timeout=30)
        return bool(r.stdout.strip())
    except Exception:  # noqa: BLE001
        return False


def _stage_one(src: Path, dst: Path, w: int, h: int, fps: float) -> None:
    s = _probe(src)
    dur = s["dur"]
    trim_in = TRIM if (TRIM > 0 and dur > TRIM * 4) else 0.0
    keep = dur - trim_in * 2 if trim_in else dur

    vf = []
    if FIT in ("crop", "pad") and (s["w"] != w or s["h"] != h) and w > 0 and h > 0:
        if FIT == "pad":
            vf.append("scale=%d:%d:force_original_aspect_ratio=decrease" % (w, h))
            vf.append("pad=%d:%d:(ow-iw)/2:(oh-ih)/2:black" % (w, h))
        else:
            vf.append("scale=%d:%d:force_original_aspect_ratio=increase" % (w, h))
            vf.append("crop=%d:%d" % (w, h))
    # fps + settb 无条件统一：xfade 硬要求分辨率/帧率/timebase 三者一致
    vf.append("fps=%.4f" % fps)
    vf.append("settb=AVTB")
    vf.append("format=yuv420p")

    cmd = ["ffmpeg", "-y", "-v", "error"]
    if trim_in:
        cmd += ["-ss", "%.3f" % trim_in, "-t", "%.3f" % keep]
    cmd += ["-i", str(src)]
    if _has_audio(src):
        # ★ **保留原声**（2026-09-21 实测：Agnes keyframe 模式的视频自带声音，
        #   13 镜里 10 镜峰值≈0dB）。旧实现无条件 map anullsrc 静音轨把原声
        #   全顶掉 → 成片 -91dB「有轨无声」。仅当源片段**无**音轨时才补静音轨
        #   （acrossfade 链硬要求每镜都有音轨）。
        cmd += ["-map", "0:v:0", "-map", "0:a:0"]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                "-map", "0:v:0", "-map", "1:a:0", "-shortest"]
    cmd += ["-vf", ",".join(vf),
            "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
            "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
            str(dst)]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("stage %s failed: %s" % (src.name, r.stderr.decode("utf-8", "replace")[-400:]))


def _xfade_chain(files: list[Path], out: Path) -> None:
    durs = [_probe(f)["dur"] for f in files]
    n = len(files)
    x = XFADE
    if any(d <= 2 * x for d in durs):
        raise RuntimeError("clip too short for xfade %.2fs" % x)

    cmd = ["ffmpeg", "-y", "-v", "error"]
    for f in files:
        cmd += ["-i", str(f)]

    flt: list[str] = []
    off = durs[0] - x          # 首个转场在第一镜 (d0 - x) 处开始
    prev_v, prev_a = "[0:v]", "[0:a]"
    for i in range(1, n):
        last = (i == n - 1)
        vout = "[vout]" if last else "[vx%d]" % i
        aout = "[aout]" if last else "[ax%d]" % i
        flt.append("%s[%d:v]xfade=transition=fade:duration=%.3f:offset=%.3f%s"
                   % (prev_v, i, x, off, vout))
        flt.append("%s[%d:a]acrossfade=d=%.3f%s" % (prev_a, i, x, aout))
        prev_v, prev_a = vout, aout
        if not last:
            off += durs[i] - x  # 合并时长每次缩短一个 x，下一转场点随之左移

    cmd += ["-filter_complex", ";".join(flt), "-map", "[vout]", "-map", "[aout]",
            "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
            "-c:a", "aac", "-b:a", "128k", str(out)]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError("xfade failed: " + r.stderr.decode("utf-8", "replace")[-400:])


def duration(path: Path) -> float:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration", "-of", "csv=p=0", str(path)],
                           capture_output=True, timeout=30)
        return float(r.stdout.decode().strip())
    except Exception:  # noqa: BLE001
        return 0.0
