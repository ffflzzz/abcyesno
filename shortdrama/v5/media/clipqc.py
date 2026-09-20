# -*- coding: utf-8 -*-
"""成片抽帧复核 + 单镜重拍（旧架构 review_frames / revise_shot 的等价物）。

为什么必须有这一层（实测事故）：
    静帧 QC 审的是**首帧**。视频阶段是另一个模型，会自己加东西——
    nightshift-45 的 42s 帧底部烧着「滴格林宇 / l'ryoym hmnell」乱码字幕，
    而对应的静帧是干净的。也就是说：**首帧合规 ≠ 成片合规**。
    新架构此前只有静帧 QC，视频渲完直接拼接，没有任何复核 → 这类问题必然漏网。

做法（与静帧 QC 同源，判据一致）：
    1. 每镜抽首/中/尾三帧（尾帧另有用：连续镜的承接依据）
    2. 走同一套视觉硬伤判据（烧字 / 分屏 / 缺人 / 多余人脸 / 血腥）
    3. 不合格镜**暂存** clip 并清 job 状态 → 交给调用方重渲该镜；
       重渲成功则丢弃暂存，失败则恢复暂存（见 `invalidate` 的收敛保证）

**只抽 3 帧而不是逐帧**：复核成本要远低于重渲成本才有意义。烧字与分屏在
任意一帧都可见，抽 3 帧足够；逐帧扫查是人工验收时才做的事。

**判据是概率性的（2026-09-10 实测）**：复核走的是 LLM，同一镜同一帧连审多次
会给出不同结论（实测 LN08 判 1/1/2 处硬伤、LN10 判 0/1/0）。因此"零硬伤"
**结构上不可达**，`pipeline` 侧必须有累计重拍上限 + residual 落地，
否则"渲出来→被作废→重渲→再被作废"会无限烧配额。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import qc

# 硬伤判据**统一在 `qc.py`**（2026-09-13）：这里原本另有一份 `_HARD_KEYS`，
# 而且**少了 10 个词**（五官/无脸/面具/真人脸/面部特征/漂移/窗外/纯黑/背景出现/
# 背景里出现）—— 与 `pipeline.HARD_KEYS` 已经漂移。判定也改用 `qc.is_hard_issue`，
# 它多一道「否定语境」过滤（防"面部为正常写实五官"被机械判死）。

# 暂存目录名（clips/ 下的隐藏子目录）。命名以 `.` 开头保证
# `CLIPS.glob("LN*.mp4")` 之类的计数与 `compose.concat` 的遍历都不会看见它。
_STASH_DIR = ".clipqc_bad"


def grab(clip: Path, out_dir: Path, n: int = 3) -> list[Path]:
    """抽 n 帧（首/中/尾分布）。返回成功落盘的帧路径列表。"""
    dur = 0.0
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration", "-of", "csv=p=0", str(clip)],
                           capture_output=True, timeout=30)
        dur = float(r.stdout.decode().strip() or 0)
    except Exception:  # noqa: BLE001
        pass
    if dur <= 0:
        return []
    out_dir.mkdir(parents=True, exist_ok=True)
    # 首帧取 0.3s（避开黑场/转场），尾帧取 dur-0.3，中间均分
    times = [0.3]
    if n > 2:
        times += [dur * i / (n - 1) for i in range(1, n - 1)]
    times.append(max(0.3, dur - 0.3))
    frames = []
    for i, t in enumerate(times[:n]):
        fp = out_dir / ("%s.f%d.jpg" % (clip.stem, i))
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", "%.2f" % t,
                            "-i", str(clip), "-frames:v", "1", "-q:v", "3", str(fp)],
                           capture_output=True, timeout=60)
        if r.returncode == 0 and fp.exists():
            frames.append(fp)
    return frames


def review_clip(clip: Path, shot: dict, work_dir: Path, log=print,
                hard_keys=None) -> list[str]:
    """复核单个 clip：抽 5 帧，**逐帧累计**问题后去重。返回硬伤描述列表（空 = 合格）。

    hard_keys: 本项目的硬伤关键词表（pack 级覆盖，见 `qc.is_hard_issue` 的说明）。
    逐帧累计的语义见下（2026-09-12 修过一个缩进 bug）。

    2026-09-12 修正（原实现是缩进 bug，保留了错误的语义）：
        `rep` 在帧循环内被赋值、却只在**循环外**使用 → 只有**最后一帧**的结论
        生效，前 4 帧的调用结果被静默丢弃（40 镜 × 5 帧 = 200 次调用，实际
        只用 40 次，**80% 白调**）。而且若 5 帧**全部**异常，`rep` 从未绑定
        → `UnboundLocalError` 直接崩掉整个 clipqc。
        原注释写的"去重（同一问题在 3 帧里都出现）"说明**意图本就是跨帧累计
        + 去重**，故此处按意图实现：逐帧收集 → 统一去重。
    """
    frames = grab(clip, work_dir, n=5)
    if not frames:
        return []
    issues: list[str] = []
    warned: set = set()
    failed = 0
    for fp in frames:
        try:
            rep = qc.review(str(fp), shot=shot)
        except Exception as e:  # noqa: BLE001
            failed += 1
            log("[clipqc] %s 复核失败（%s）：%s" % (clip.stem, fp.name, str(e)[:80]))
            continue
        if not isinstance(rep, dict):
            continue
        for i in (rep.get("issues") or []):
            lvl = str(i.get("level", ""))
            desc = str(i.get("desc", ""))
            # P1 = 一致性警告（服装/发型/光线漂移）。**只打印不阻断**：这类判定是
            # 概率性的（同一帧连审多次结论会抖），进重拍循环会误杀烧配额；
            # 但必须让人看见——2026-09-10《热牛奶》的跨镜换装就是这么溜过去的。
            if lvl == "P1":
                if desc[:120] not in warned:
                    warned.add(desc[:120])
                    log("[clipqc] %s 一致性警告：%s" % (clip.stem, desc[:120]))
                continue
            if not lvl.startswith("P0"):
                continue
            if qc.is_hard_issue({"level": lvl, "desc": desc}, keys=hard_keys):
                issues.append(desc[:160])
    if failed == len(frames):
        log("[clipqc] %s 全部 %d 帧复核失败 → 本镜未判定（不阻断）"
            % (clip.stem, failed))
    # 去重（同一问题在多个帧里都出现）
    return list(dict.fromkeys(issues))


def audit(clips: dict[str, str], shots: list[dict], work_dir: Path,
          log=print, workers: int | None = None,
          hard_keys=None) -> dict[str, list[str]]:
    """批量复核。返回 {镜名: [硬伤描述]}（只含不合格镜）。

    **并发化（2026-09-12 实测）**：单镜 5 帧串行视觉调用实测 ~35s，40 镜
    ≈ 24 分钟。更关键的是**串行的失败模式**——实测 LN18 的一次视觉调用挂了
    **整整 15 分钟**（`timeout=300` 没掐断，是流式响应一直在缓慢吐字节），
    整个 clipqc 就被那一镜冻住 15 分钟，成片被推迟 15 分钟。
    改 `ThreadPoolExecutor`（IO 密集，GIL 不影响）：同轮次降到分钟级，
    且单镜慢/卡不再阻塞其余镜。

    `workers` 可注入（测试用），默认 `SHORTDRAMA_QC_WORKERS`（与静帧 QC 同源）。
    """
    import os
    from concurrent.futures import ThreadPoolExecutor, as_completed

    by_name = {s["name"]: s for s in shots}
    items = [(n, Path(p)) for n, p in clips.items() if Path(p).exists()]
    bad: dict[str, list[str]] = {}
    if not items:
        log("[clipqc] 复核 0 镜，不合格 0 镜")
        return bad
    if workers is None:
        workers = max(1, int(os.environ.get("SHORTDRAMA_QC_WORKERS", "3")))
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(review_clip, p, by_name.get(n) or {}, work_dir, log,
                          hard_keys): n
                for n, p in items}
        for fut in as_completed(futs):
            name = futs[fut]
            issues = fut.result()
            if issues:
                bad[name] = issues
                log("[clipqc] %s 不合格：%s" % (name, issues[0][:80]))
    log("[clipqc] 复核 %d 镜，不合格 %d 镜（%d workers）"
        % (len(items), len(bad), max(1, workers)))
    return bad


def invalidate(root: Path, names: list[str], ep: int = 1, log=print) -> dict[str, Path]:
    """把不合格镜的 clip **移到暂存区**（不删除），并清掉 job 状态使其可重渲。

    返回 {镜名: 暂存路径}，交给调用方在重渲后 `discard()`（成功）或
    `restore()`（失败）。

    为什么必须暂存而不是删除（2026-09-10 maskparade 实测事故）：
        原实现直接 `unlink()`。而重渲要排队（1rpm + 队列满退避），外部续跑器
        又有单轮时间上限——上限到时进程被杀，被删的 clip 还没渲出替代品，
        于是**永久丢失**。实测：7 镜 → 作废 5 镜 → 只渲回 2 镜 → 下轮再审
        再作废 → 盘上只剩 1 镜，`compose.concat` 因缺镜拒绝拼接，成片永远出不来。
        暂存后无论在哪一步被打断，最坏情况都只是"保留有瑕疵的旧 clip"，
        **绝不会比循环开始时更少**。

    为什么同时清 job 状态：`jobs.done()` 要求 state=completed **且**成片在盘。
    只移文件不清状态 → 留下 completed + 文件不在 → `done()` 判 False（会重提交，
    结果正确）；只清状态不移文件 → clip 还在，拼接会带旧片。两者必须一起做。
    """
    from . import jobs as jobs_mod
    out_dir = root / "media" / ("ep" + str(ep))
    clip_dir = out_dir / "clips"
    stash_dir = clip_dir / _STASH_DIR
    stash_dir.mkdir(parents=True, exist_ok=True)
    jobs = jobs_mod.load(out_dir)
    moved: dict[str, Path] = {}
    for name in names:
        for suffix in (".mp4", ".last.jpg"):
            f = clip_dir / (name + suffix)
            if not f.exists():
                continue
            dest = stash_dir / (name + suffix)
            try:
                if dest.exists():
                    dest.unlink()
                f.replace(dest)
                if suffix == ".mp4":
                    moved[name] = dest
            except Exception:  # noqa: BLE001
                pass
        jobs.pop(name, None)
    jobs_mod.save(out_dir, jobs)
    log("[clipqc] 已暂存 %d 镜（clip 移入 %s，job 状态清空）待重渲"
        % (len(names), _STASH_DIR))
    return moved


def discard(root: Path, stashed: dict[str, Path], log=print) -> int:
    """重渲成功 → 删掉暂存的旧 clip（它已被新片取代）。"""
    n = 0
    for name, _p in (stashed or {}).items():
        for suffix in (".mp4", ".last.jpg"):
            f = Path(_p).parent / (name + suffix)
            if f.exists():
                try:
                    f.unlink()
                    n += 1
                except Exception:  # noqa: BLE001
                    pass
    if n:
        log("[clipqc] 新片已落盘，丢弃暂存 %d 个文件" % n)
    return n


def restore(root: Path, stashed: dict[str, Path], ep: int = 1, log=print) -> list[str]:
    """重渲没回来 → 把暂存的旧 clip **放回原位**，并标回 completed。

    这是"宁要有瑕疵但完整"的落地点：放回后该镜重新进入拼接集合，
    同时记入 residual 如实汇报。**收敛保证**：restore 之后该镜是 completed
    + 文件在盘，下一轮 `done()` 判 True，不会被重复提交。
    """
    from . import jobs as jobs_mod
    out_dir = root / "media" / ("ep" + str(ep))
    clip_dir = out_dir / "clips"
    jobs = jobs_mod.load(out_dir)
    back: list[str] = []
    for name, src in (stashed or {}).items():
        src = Path(src)
        dest = clip_dir / (name + ".mp4")
        try:
            if src.exists():
                if dest.exists():
                    dest.unlink()
                src.replace(dest)
            if dest.exists():
                jobs_mod.mark(jobs, name, "completed", local=str(dest),
                              error="clipqc 保留：重渲未回，暂存恢复")
                back.append(name)
        except Exception:  # noqa: BLE001
            pass
    if back:
        jobs_mod.save(out_dir, jobs)
        log("[clipqc] 重渲未回，已恢复暂存 %d 镜（保留有瑕疵的旧 clip 参与拼接）：%s"
            % (len(back), back[:6]))
    return back


def restore_leftovers(root: Path, ep: int = 1, log=print) -> list[str]:
    """启动时把暂存区里的残留 clip 全部放回。

    覆盖"进程被单轮上限杀掉、没来得及 restore"的场景——那正是原实现永久
    丢片的路径。幂等：暂存区空则什么都不做。

    **只放回 clips/ 里已经没有的那一份**（2026-09-13 修正）：
      暂存区里的文件**永远不比 clips/ 里的新** —— `invalidate` 只把 clip
      "移出" clips/，之后 clips/ 里再出现同名文件，只可能来自一次**新的渲染**。
      所以同名 clip 已在 clips/ 时，暂存那一份是**陈旧**的：放回会把刚渲好的
      新片覆盖成旧片。
      这不是理论风险：单镜重渲（`pipeline.rerender`）**必然**经过
      "作废 → 重渲成功 → 进程在 discard 之前被杀"这个状态 —— 旧实现会在下一次
      启动时把重渲成果**悄悄回滚**（重渲等于没做，而且看不出来）。
    """
    clip_dir = root / "media" / ("ep" + str(ep)) / "clips"
    stash_dir = clip_dir / _STASH_DIR
    if not stash_dir.exists():
        return []
    left = {p.stem: p for p in stash_dir.glob("*.mp4")}
    if not left:
        return []
    stale = {n: p for n, p in left.items() if (clip_dir / (n + ".mp4")).exists()}
    if stale:
        log("[clipqc] %d 镜的新片已在盘 → 丢弃陈旧暂存（放回会覆盖新片）：%s"
            % (len(stale), sorted(stale)[:6]))
        discard(root, stale, log=log)
    fresh = {n: p for n, p in left.items() if n not in stale}
    if not fresh:
        return []
    return restore(root, fresh, ep=ep, log=log)

