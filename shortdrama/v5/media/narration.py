"""旁白音轨（half-narrated-live-action 包，audio_mode=narration-led）。

拼接完成后按真实时间轴生成旁白配音并混音：
  1. 解析 dialogue 产物的「旁白清单」表（镜号 | 旁白文本）；
  2. 按 video_jobs 的镜级时长累计时间轴（每句旁白的起点 = 前面所有镜的时长和）；
  3. edge-tts 逐句生成 mp3（音色可配 SHORTDRAMA_NARRATION_VOICE）；
  4. 逐句槽对齐（不足补静音、超时顺延）拼成与成片等长的旁白音轨；
  5. 与原声 amix（duration=first，成片为基准）→ 覆盖 episode_final.mp4。

失败不挡链：任何异常都保留原声成片（调用方 catch）。
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Callable

# ★ edge-tts 依赖微软服务（speech.platform.bing.com）——沙箱透明代理挂了会
#   让 TTS 静默产出空文件（实测 2026-09-23：amix 报 No audio was received）。
#   模块加载时把微软域名追加进 NO_PROXY/no_proxy（直连），幂等。
import os as _os

_TTS_DIRECT = "speech.platform.bing.com,api.msedgesvc.com,bing.com"
for _v in ("NO_PROXY", "no_proxy"):
    _cur = _os.environ.get(_v, "")
    for _d in _TTS_DIRECT.split(","):
        if _d not in _cur:
            _cur = (_cur + "," + _d) if _cur else _d
    _os.environ[_v] = _cur

VOICE_DEFAULT = "zh-CN-YunxiNeural"      # 低沉男声（半解说常用）；可 env 覆盖
RATE_DEFAULT = "+0%"                     # 语速（edge-tts 语法，如 "+10%"）


# ─────────────────────────── 解析 ───────────────────────────

def _parse_narration_table(md: str) -> list[tuple[str, str]]:
    """解析 dialogue 产物的「旁白清单」表 → [(镜号, 旁白文本)]。

    表列序（half-narrated 包 dialogue SKILL 定义）：| 镜号 | 旁白文本 | 字数与建议秒 | 视角 |
    """
    out: list[tuple[str, str]] = []
    in_table = False
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("##") or s.startswith("**"):
            in_table = "旁白清单" in s
            continue
        if not in_table or not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 2 or re.match(r"^[-\s:]+$", cells[0] or "-"):
            continue
        raw = cells[0]
        m = re.match(r"^(LN)?(\d+(-\d+)?)$", raw)
        if m:
            shot = "LN%02d" % int(m.group(2).split("-")[0])
        elif "①" <= raw <= "⑳":     # 圈号镜号（half-narrated 实测产物）
            shot = "LN%02d" % (ord(raw) - ord("①") + 1)
        else:
            continue
        text = cells[1].strip()
        if text and text not in ("旁白文本", "—"):
            out.append((shot, text))
    return out


def _shot_durations(jobs: dict) -> dict[str, float]:
    """从 video_jobs 展开 {镜号: 秒}（pack 记录的 shots/declared_seconds）。"""
    d: dict[str, float] = {}
    for rec in jobs.values():
        shots = rec.get("shots") or []
        decl = rec.get("declared_seconds") or []
        for name, sec in zip(shots, decl):
            try:
                d[str(name)] = float(sec)
            except (TypeError, ValueError):
                pass
    return d


def _schedule(entries: list[tuple[str, str]], durs: dict[str, float]) -> list[dict]:
    """按分镜序累计时间轴 → [{shot, text, start, slot}]（start=计划起点秒）。"""
    order = [k for k in durs]                    # jobs 展开序即分镜序
    # 用 entries 的镜号在 order 中的位置排序（旁白只挂在有台词的镜上）
    idx = {name: i for i, name in enumerate(order)}
    known = [(idx[k], k, v) for k, v in entries if k in idx]
    known.sort()
    sched, cursor = [], 0.0
    for _, k, text in known:
        slot = durs.get(k, 0.0)
        sched.append({"shot": k, "text": text, "start": round(cursor, 2),
                      "slot": round(slot, 2)})
        cursor += slot
    return sched


# ─────────────────────────── TTS 与音轨 ───────────────────────────

def _tts_sapi(text: str, path: Path, log=print) -> None:
    """Windows SAPI 回落（离线必成；音色机械但可读，MVP 够用）。

    用 -EncodedCommand（base64 UTF-16LE）避免中文/引号转义问题。
    """
    import base64
    import subprocess as sp

    out = str(path).replace("\\", "/")
    text_esc = text.replace("'", "''")
    cmd = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.SelectVoice('Microsoft Kangkang'); "
        f"$s.SetOutputToWaveFile('{out}'); "
        f"$s.Speak('{text_esc}')"
    )
    enc = base64.b64encode(cmd.encode("utf-16-le")).decode()
    sp.run(["powershell", "-NoProfile", "-EncodedCommand", enc],
           check=True, capture_output=True)


def _tts_one(text: str, path: Path, voice: str, rate: str, log=print) -> None:
    try:
        import asyncio
        import edge_tts

        async def _run():
            await edge_tts.Communicate(text, voice, rate=rate).save(str(path))

        asyncio.run(_run())
        if path.exists() and path.stat().st_size > 1000:
            return
        raise RuntimeError("edge-tts 空输出")
    except Exception as e:
        log("[narration] edge-tts 失败（%s）→ 回落 Windows SAPI" % str(e)[:60])
        _tts_sapi(text, path, log=log)


def _run_ffmpeg(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error"] + args,
                   check=True, capture_output=True)


def _wav_len(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate() or 44100)


def _silence(path: Path, sec: float) -> None:
    _run_ffmpeg(["-f", "lavfi", "-i", f"anullsrc=r=24000:cl=mono",
                 "-t", f"{sec:.3f}", str(path)])


def _slot_fit(src: Path, dst: Path, sec: float) -> None:
    """句音频对齐到槽长：不足补静音、超长截断（截断仅发生在溢出顺延关闭时）。"""
    _run_ffmpeg(["-i", str(src), "-af", f"apad,atrim=0:{sec:.3f}",
                 "-ar", "24000", "-ac", "1", str(dst)])


def _concat(paths: list[Path], out: Path) -> None:
    lst = out.parent / "narration_concat.txt"
    with open(lst, "w", encoding="utf-8") as f:
        for p in paths:
            f.write("file '%s'\n" % str(p).replace("\\", "/").replace("'", "'\\''"))
    _run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst),
                 "-c", "copy", str(out)])
    lst.unlink(missing_ok=True)


def attach(project_root: Path, ep: int, final: Path,
           voice: str | None = None, rate: str = RATE_DEFAULT,
           log: Callable = print) -> bool:
    """拼接完成后挂旁白音轨。返回是否混音成功（失败保留原声成片，不抛出）。"""
    import os

    dlg = project_root / "dialogue" / "dialogue_ep1.md"
    if not dlg.exists():
        log("[narration] 无 dialogue 产物 → 跳过")
        return False
    entries = _parse_narration_table(dlg.read_text(encoding="utf-8"))
    if not entries:
        log("[narration] 旁白清单为空 → 跳过")
        return False
    jobs_path = project_root / "media" / f"ep{ep}" / "video_jobs.json"
    if not jobs_path.exists():
        log("[narration] 无 video_jobs → 跳过")
        return False
    durs = _shot_durations(json.loads(jobs_path.read_text(encoding="utf-8")))
    sched = _schedule(entries, durs)
    if not sched:
        log("[narration] 旁白镜号未命中分镜 → 跳过")
        return False

    voice = voice or os.environ.get("SHORTDRAMA_NARRATION_VOICE", VOICE_DEFAULT)
    rate = os.environ.get("SHORTDRAMA_NARRATION_RATE", rate)
    work = Path(tempfile.mkdtemp(prefix="narration_"))
    segs: list[Path] = []
    cursor, overflow = 0.0, 0
    try:
        total = sum(durs.get(k, 0.0) for k in durs)
        for i, item in enumerate(sched):
            start = max(item["start"], cursor)
            gap = start - cursor
            if gap > 0.05:
                gp = work / f"gap_{i:03d}.wav"
                _silence(gp, gap)
                segs.append(gp)
            mp3 = work / f"v_{i:03d}.mp3"
            _tts_one(item["text"], mp3, voice, rate, log=log)
            wav = work / f"v_{i:03d}.wav"
            _run_ffmpeg(["-i", str(mp3), "-ar", "24000", "-ac", "1", str(wav)])
            ln = _wav_len(wav)
            end = start + ln
            if end > total:                       # 尾句溢出成片 → 截到片尾
                _slot_fit(wav, wav, max(total - start, 0.2))
                ln = total - start
                end = total
                overflow += 1
            elif ln > item["slot"] * 1.3:         # 超槽 30%+ → 顺延（不截词）
                overflow += 1
                log("[narration] %s 旁白 %.1fs 超槽 %.1fs → 顺延"
                    % (item["shot"], ln, item["slot"]))
            segs.append(wav)
            cursor = end
        tail = total - cursor
        if tail > 0.05:
            gp = work / "gap_tail.wav"
            _silence(gp, tail)
            segs.append(gp)
        track = work / "narration_track.wav"
        _concat(segs, track)
        mixed = final.parent / "episode_final_narr.mp4"
        _run_ffmpeg(["-i", str(final), "-i", str(track),
                     "-filter_complex",
                     "[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=0[a]",
                     "-map", "0:v", "-map", "[a]", "-c:v", "copy",
                     str(mixed)])
        mixed.replace(final)                      # 原子替换：成片即含旁白
        log("[narration] 旁白音轨已混入：%d 句 / 音色 %s / 溢出顺延 %d 句"
            % (len(sched), voice, overflow))
        return True
    finally:
        for p in work.glob("*"):
            try:
                p.unlink()
            except OSError:
                pass
        try:
            work.rmdir()
        except OSError:
            pass
