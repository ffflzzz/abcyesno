# -*- coding: utf-8 -*-
"""问到哪了 —— 从盘上算进度，不靠 agent 回忆。

**要回答的问题**（2026-10-01，由"跑到哪了？"得到一份错答案引出）：
    驱动在后台跑，用户/agent 随时可能问进度。当天实测的失败不是"没在跑"，而是
    **答错了**：agent 在界面上写"真相查清了——v2 的 reviewer 早在 15:42 就写完了"，
    而 `review_ep1.md` 的 mtime 一直停在 15:33:33、md5 从未变过。回忆会错，
    盘上的字节不会。所以这个脚本只做一件事：把散在各处的事实拼成一句人话。

**事实分散在**（都是驱动/管线自己写的，本脚本一个字段都不新造）：
    projects/<p>/.agent_state.json          阶段完成记账、审片判决、force_passed
    projects/<p>/reviewer/review_ep<N>.md   审片报告正文（判决从这里解析）
    projects/<p>/media/ep<N>/stills.json    静帧产出
    projects/<p>/media/ep<N>/video_jobs.json 本集打算渲染哪些镜 + 每镜状态与失败原因
    projects/<p>/media/ep<N>/clips/*.mp4    实际落地的镜头
    projects/<p>/media/ep<N>/episode_final.mp4  成片（时长走 ffprobe，不信文件名）
    .tmp/recover.log                        驱动最近一行日志

**顺带把"假完成"看得见的部分摊开**：每集的审片报告多久没动过，直接印在行上
（只报事实，不下"没重写"的判断 —— 那需要阶段起始时刻，在驱动进程里才有，
放在这里推断会把正常跑完的回合全判成误报）。

审片判决本身复用 `v5.decision` 的解析器，不另写一套判据（两套判据迟早不一致）。

用法：
    py scripts/probe_progress.py xianxia-swordmound
    py scripts/probe_progress.py xianxia-swordmound --json
    py scripts/probe_progress.py xianxia-swordmound --ep 1 --ep 2

退出码：0 每集都有成片；1 有集没成片或有镜缺片；2 用法/目录错误。
"""
import argparse
import io
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v5.decision import normalize_pass, parse_decision  # noqa: E402


def _read_json(path):
    if not path or not path.exists():
        return None
    try:
        with io.open(str(path), encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:  # 半截写入 / 编码问题：报出来而不是当没有
        return {"__error__": "%s: %s" % (type(e).__name__, e)}


def _mtime(path):
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _age(seconds):
    s = max(0, int(seconds or 0))
    if s < 60:
        return "%d 秒前" % s
    if s < 3600:
        return "%d 分钟前" % (s // 60)
    return "%d 小时 %d 分前" % (s // 3600, (s % 3600) // 60)


def ffprobe_duration(path):
    """成片时长。走 ffprobe，不信文件名——0 字节的 mp4 也叫"存在"是骗人。"""
    if not path or not path.exists():
        return None
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=20,
        )
        return round(float(out.stdout.strip()), 2)
    except Exception:
        return None


def shot_ids(root, project, ep):
    """本集"应该有几镜"——以媒体链自己的任务表为准。

    别去分镜 md 里找镜号：实测 `scenedesigner_ep1.md` 里一个 `LNxx` 都没有
    （第一版就栽在这个假设上，镜数报 0，于是"缺哪几镜"永远查不出来）。
    `video_jobs.json` 才是媒体链打算渲染的镜集，缺片要对着它算。
    """
    base = root / "projects" / project / "media" / ("ep%d" % ep)
    for src, key in (("video_jobs.json", "jobs"), ("stills.json", "stills")):
        data = _read_json(base / src)
        if isinstance(data, dict) and data:
            return sorted(k for k in data if not str(k).startswith("__"))
    return []


def review_state(root, project, ep):
    p = root / "projects" / project / "reviewer" / ("review_ep%d.md" % ep)
    info = {"path": str(p), "exists": p.exists(), "mtime": _mtime(p)}
    if not p.exists():
        return info
    text = io.open(str(p), encoding="utf-8", errors="replace").read()
    dec = parse_decision(text)
    info["parsed"] = dec is not None
    if dec is not None:
        info["passed"] = bool(normalize_pass(dec))
        info["rerun"] = dec.get("rerun") or []
        info["reasons"] = len(dec.get("reasons") or [])
    return info


def media_state(root, project, ep):
    base = root / "projects" / project / "media" / ("ep%d" % ep)
    jobs = _read_json(base / "video_jobs.json") or {}
    stills = _read_json(base / "stills.json") or {}
    clips_dir = base / "clips"
    clips = sorted(p.stem for p in clips_dir.glob("*.mp4")) if clips_dir.exists() else []
    failed = {}
    if isinstance(jobs, dict):
        for k, v in jobs.items():
            if isinstance(v, dict) and str(v.get("state")) == "failed":
                failed[k] = str(v.get("error") or "")
    final = base / "episode_final.mp4"
    return {
        "jobs": len(jobs) if isinstance(jobs, dict) else 0,
        "stills": sorted(stills.keys()) if isinstance(stills, dict) else [],
        "clips": clips,
        "failed": failed,
        "final": str(final),
        "final_exists": final.exists(),
        "final_size_mb": round(final.stat().st_size / 1048576, 1) if final.exists() else None,
        "final_duration_s": ffprobe_duration(final) if final.exists() else None,
    }


def driver_tail(root, log_rel=".tmp/recover.log"):
    p = root / log_rel
    m = _mtime(p)
    if m is None:
        return {"path": str(p), "line": None, "age_s": None}
    try:
        lines = [l.strip() for l in io.open(str(p), encoding="utf-8", errors="replace") if l.strip()]
    except OSError:
        return {"path": str(p), "line": None, "age_s": None}
    return {"path": str(p), "line": lines[-1] if lines else "", "age_s": time.time() - m}


def _discover_eps(pdir, state):
    """没指定 --ep 时，从盘上看出有几集。

    三处取并集：状态里的 phases 记账、media/epN 目录、reviewer/review_epN.md。
    只看其中一处会漏 —— 例如媒体链已经跑了但记账还没写，或反过来。
    """
    eps = set()
    for k in (state.get("phases") or {}) if isinstance(state, dict) else {}:
        try:
            eps.add(int(k))
        except (TypeError, ValueError):
            pass
    for p in (pdir / "media").glob("ep*") if (pdir / "media").exists() else []:
        m = re.fullmatch(r"ep(\d+)", p.name)
        if m:
            eps.add(int(m.group(1)))
    if (pdir / "reviewer").exists():
        for p in pdir.glob("reviewer/review_ep*.md"):
            m = re.search(r"review_ep(\d+)\.md$", p.name)
            if m:
                eps.add(int(m.group(1)))
    return sorted(eps) or [1]


def build_report(root, project, eps=None):
    pdir = root / "projects" / project
    if not pdir.exists():
        raise SystemExit(2)
    state = _read_json(pdir / ".agent_state.json") or {}
    review_state_block = (state.get("review") or {}) if isinstance(state, dict) else {}
    tail = driver_tail(root)

    if not eps:
        eps = _discover_eps(pdir, state)

    out = {"project": project, "root": str(root), "driver": tail, "episodes": []}
    for ep in eps:
        ids = shot_ids(root, project, ep)
        media = media_state(root, project, ep)
        rev = review_state(root, project, ep)
        missing = [s for s in ids if s not in media["clips"]]
        notes = []
        if not rev["exists"]:
            notes.append("无审片报告")
        if missing:
            notes.append("缺 %d 镜未出片：%s" % (len(missing), "、".join(map(str, missing[:8]))))
        # 故意不比对 stills 与 video_jobs 的键集：两者不一定是同一套 id。
        # 实测 luanzhen-xue-1001 的媒体链按 pack01..pack06 组织，静帧却是逐镜的
        # LN01..LN12 —— 差集永远非空，报出来就是误报机器（同一天在"报告比日志旧"
        # 那条上已经栽过一次）。这里只把两个计数如实印出来，让人自己看。
        # 真正可靠的缺件判据是"任务表里的 id 有没有对应 clip"，即上面的 missing。
        if media["failed"]:
            notes.append("视频阶段失败 %d 镜：%s" % (
                len(media["failed"]),
                "、".join("%s(%s)" % (k, v) for k, v in sorted(media["failed"].items())[:5])))
        if not media["final_exists"]:
            notes.append("无成片")
        out["episodes"].append({
            "ep": ep,
            "shots": ids,
            "review": rev,
            # 状态里的 force_passed 是"最后一次审片"的，不分集。只有当本集自己的
            # 审片报告确实判不过时，才把它算到本集头上（否则 ep2 会被 ep1 的
            # 强制放行蹭上一个"→强制放行"的脏名）。
            "force_passed": bool(review_state_block.get("force_passed"))
                            and rev.get("exists") and rev.get("passed") is False,
            "media": media,
            "clips_missing": missing,
            "notes": notes,
        })
    return out


def render_text(rep):
    d = rep["driver"]
    lines = ["%s  ·  驱动 %s" % (rep["project"], d["line"] or "（无日志）")]
    if d.get("age_s") is not None:
        lines[0] += "  [%s]" % _age(d["age_s"])
    for e in rep["episodes"]:
        m = e["media"]
        rev = e["review"]
        verdict = "未落盘" if not rev["exists"] else (
            ("过" if rev.get("passed") else "不过") + ("→强制放行" if e["force_passed"] else ""))
        bits = [
            "ep%d" % e["ep"],
            "审片 %s%s" % (verdict, "（报告 %s）" % _age(time.time() - rev["mtime"])
                            if rev.get("mtime") else ""),
            "任务 %d 镜" % len(e["shots"]),
            "静帧 %d" % len(m["stills"]),
            "出片 %d" % len(m["clips"]),
        ]
        if m["final_exists"]:
            bits.append("成片 ✓ %.1fMB %.1fs" % (m["final_size_mb"], m["final_duration_s"] or 0))
        else:
            bits.append("成片 ✗")
        lines.append("  " + "  ".join(bits))
        for n in e["notes"]:
            lines.append("     ⚠️ %s" % n)
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="从盘上算短剧管线进度（不信回忆）")
    ap.add_argument("project")
    ap.add_argument("--root", default=None, help="shortdrama 根目录，默认取脚本所在目录的上一级")
    ap.add_argument("--ep", type=int, action="append", dest="eps")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    root = Path(a.root).resolve() if a.root else Path(__file__).resolve().parents[1]
    rep = build_report(root, a.project, a.eps)
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1, default=str))
    else:
        print(render_text(rep))
    ok = all(
        e["media"]["final_exists"] and not e["clips_missing"] and not e["media"]["failed"]
        for e in rep["episodes"]
    )
    return 0 if ok else 1


if __name__ == "__main__":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    sys.exit(main())
