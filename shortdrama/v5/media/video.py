# -*- coding: utf-8 -*-
"""图生视频：一条请求吃什么素材，由**档位**决定（唯一判定点在 `video_plan.VideoPlan`）。

2026-10-07 起的默认（`reference` 档 + `VIDEO_REF_SOURCE=sheets`）：
    资产图（定妆照 / 场景空镜 / 道具图）+ 文字 → 视频，**静帧不再是输入**。
    依据与代价都写在 `config.VIDEO_REF_SOURCE` 与 `sheettext.py` 的文件头。
下面的"静帧 → first_frame"描述的是 `keyframe` 回退档，以及 `pack` / `mixed`
里仍然离不开静帧的那几处（`needs_stills`）。

尾帧承接（2026-09-08 修）：
  continuous/match 关系的首帧必须是**上一镜渲染成片的真实尾帧**，不是上一镜的
  静帧——静帧是上一镜的首帧构图，直接拿它当下一镜首帧会让两镜画面逐帧重复
  （实测 LN01/LN02 成片首帧完全相同）。因此连续镜必须**串行**：提交→等完成→
  抽尾帧→下一镜首帧。keyframe 模式接受 base64 data URI，无需公网托管。
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import subprocess
import time
from pathlib import Path

from .. import config
from .. import vendors
from . import assets
from . import jobs as jobs_mod
from . import keypool
from . import prompt as prompt_mod
from . import style as style_mod
from . import providers
from . import sheettext
from . import video_plan


# ─── 尾帧抽取（continuous 承接的唯一正确来源）─────────────────────────────────

def extract_last_frame(clip: Path, max_side: int = 512) -> str | None:
    """从**已渲染成片**抽真实尾帧，返回 JPEG data URI（keyframe 可直接吃）。

    失败返回 None（调用方退化为本镜静帧，画面会重复但不阻断生产）。
    """
    if not clip.exists():
        return None
    frame = clip.with_name(clip.stem + ".last.jpg")
    try:
        r = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-sseof", "-1", "-i", str(clip),
             "-vf", "reverse", "-frames:v", "1", "-q:v", "2", str(frame)],
            capture_output=True, timeout=120)
    except Exception:  # noqa: BLE001
        return None
    if r.returncode != 0 or not frame.exists():
        return None
    try:
        from PIL import Image
        im = Image.open(frame).convert("RGB")
        im.thumbnail((max_side, max_side))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:  # noqa: BLE001
        return None


#: 云端厂商的轮询窗口：60 轮 × 10 秒 ≈ 10 分钟（与接入本机厂商前**一字不变**）。
WAIT_ROUNDS = 60
WAIT_INTERVAL = 10
POLL_ROUNDS = 40


def poll_window(rounds: int | None = None, interval: int | None = None,
                default_rounds: int = WAIT_ROUNDS,
                default_interval: int = WAIT_INTERVAL) -> tuple[int, int]:
    """轮询窗口 = 调用方显式传的 > **当前视频厂商档里的** `poll_rounds`/`poll_interval` > 旧常量。

    ★ 为什么必须按厂商取：60×10 秒是照着云端 Agnes 的「排队 + 生成」时间定的。
    本机一块 12G 卡渲 12 秒可能要十几分钟，用云端的窗口会把**还在渲**判成
    `expired` ⇒ 上层重提 ⇒ 同一张显卡上把同一镜渲两遍，越等越久，而日志里
    只看得到「超时重提」这一条正常流程。厂商档里有这两个值就用它，没有（云端）
    就落回旧常量 ⇒ **改造前一字不变**。
    """
    try:
        spec = vendors.spec_for("video")
    except Exception:  # noqa: BLE001 —— 厂商未注册等场景不在这里判死
        spec = {}
    r = int(rounds or spec.get("poll_rounds") or default_rounds)
    i = int(interval or spec.get("poll_interval") or default_interval)
    return max(1, r), max(1, i)


def _wait_one(video_id: str, dest: Path, rounds: int | None = None,
              interval: int | None = None,
              log=print, key: str | None = None) -> str:
    """轮询单个任务直到完成并落盘。

    返回本地路径（**失败/超窗都返回空串**）。空串的原因由调用方按
    jobs 状态机区分：failed（供应商报错）或 expired（窗口内没完成）。

    `key`（2026-09-22）：**必须传创建该任务的那条 key** —— 国内/国际双入口下
    key 带自己的地址（`config.AGNES_KEY_BASE`），不传会拿全局地址去查国内
    创建的 video_id（403/404/查不到，任务假死）。

    `rounds` / `interval`（2026-10-05）：不传 ⇒ 按当前视频厂商的档取
    （本机 ComfyUI 一条渲十几分钟，云端的 10 分钟窗口会把「还在渲」判成超时）。
    """
    import httpx
    rounds, interval = poll_window(rounds, interval)
    for _ in range(rounds):
        try:
            r = providers.query_video(video_id, key=key)
        except Exception:  # noqa: BLE001
            time.sleep(interval)
            continue
        st = r.get("status")
        if st == "completed" and r.get("url"):
            # 下载**必须有重试与异常捕获**（2026-09-13 事故）：
            # 这段原本是裸奔的 `dest.write_bytes(c.get(url).content)`，
            # 一次 `httpx.ProxyError: 502 Bad Gateway`（CDN 抖动 / 代理抽风）
            # 就直接抛到顶层 → **整条媒体链垮掉**，46 镜项目里已经跑完的
            # 40 张静帧 + 7 个视频全废。
            # 与上面 `query_video` 那一支保持同样的宽容度：下载失败就等下一轮，
            # rounds 用尽才返回 ""（由上层状态机判 failed/expired）。
            try:
                with httpx.Client(timeout=120, trust_env=False) as c:  # CDN 直连
                    resp = c.get(r["url"])
                    resp.raise_for_status()
                    dest.write_bytes(resp.content)
                return str(dest)
            except Exception as e:  # noqa: BLE001
                log("[video] %s 下载失败（将重试）：%s" % (dest.name, str(e)[:80]))
                time.sleep(interval)
                continue
        if st in ("failed", "error"):
            log("[video] FAILED: %s" % str(r.get("error"))[:100])
            return ""
        time.sleep(interval)
    return ""


def drop_flagged(urls, roles, blocked, name, log=print):
    """把带可读文字的资产图从这次请求里剔掉（`urls` 与 `roles` 同序，一起剔）。

    剔完还剩几张就喂几张；全被剔光则由调用方退回静帧那条老路（见 `from_sheets` 分支）。
    """
    if not blocked or not urls:
        return urls, roles
    keep_u, keep_r = [], []
    for i, u in enumerate(urls):
        if u in blocked:
            log("[video] %s 剔掉带字的资产图：%s（%s）"
                % (name, blocked[u], (roles[i][1] if roles and i < len(roles) else "")))
            continue
        keep_u.append(u)
        if roles and i < len(roles):
            keep_r.append(roles[i])
    return keep_u, keep_r


# ─── 串行链式（默认；语义正确）───────────────────────────────────────────────

def _tail_if_needed(clip: Path, plan: "video_plan.VideoPlan") -> str | None:
    """要不要抽尾帧 —— 由 `VideoPlan.needs_tail_extract` 决定（**唯一决策点**）。

    2026-09-13：`config.VIDEO_MODE` 默认切到 `reference` 后，"上一镜尾帧承接"
    这条数据流已经取消（reference 不允许 `first_frame`）—— 抽出来**没人消费**，
    每遇到一个已完成的镜就白解码一遍视频。keyframe 回退档仍需要它，故按 plan 分流。
    """
    if not plan.needs_tail_extract:
        return None
    return extract_last_frame(clip)


def submit_chain(project_root: Path, shots: list[dict], stills: dict, planned: list[dict],
                 ep: int = 1, log=print, rounds: int | None = None,
                 only: list[str] | None = None) -> dict:
    """提交 → 等完成 →（keyframe 模式下）抽尾帧给下一镜当首帧。

    返回 {name: local_path}。**两种模式（见 `config.VIDEO_MODE`）**：
      · `reference`（默认，2026-09-13 起）—— 各镜**完全独立**，"抽尾帧承接"不参与
        （reference 不允许 first_frame/last_frame）。喂什么图看 `from_sheets`：
        默认喂本镜资产图，设 `SHORTDRAMA_VIDEO_REF_SOURCE=stills` 才回到"静帧当
        `<Picture 1>`"。函数名里的"chain"在此时只是历史遗留。
      · `keyframe`（回退档）—— 连续镜靠上一镜真实尾帧承接，跳切镜用自己的静帧。

    `only`（2026-09-13，单镜重渲）：**只提交这些镜**。非目标镜即便
    `jobs.done()` 判 False 也**绝不提交**（那会白烧视频配额）；但盘上已有的
    clip 仍会进 `done` 并**取出尾帧**，否则 keyframe 档下目标镜取不到
    "上一镜真实尾帧"、调用方也会因缺镜而拒绝拼接。

    进度以 `video_jobs.json` 的**显式状态**为准（见 media/jobs.py）：
    只有 state=completed 且成片在盘才算完成；submitted/failed/expired 都会
    在续跑时被重新推进，不再出现"有 video_id 就永远跳过"的隐式黑洞。
    """
    out_dir = project_root / "media" / ("ep" + str(ep))
    clip_dir = out_dir / "clips"
    clip_dir.mkdir(parents=True, exist_ok=True)
    jobs = jobs_mod.load(out_dir)
    _only = set(only) if only else None

    done: dict[str, str] = {}
    prev_tail: str | None = None
    last_submit = 0.0

    shots = prompt_mod.resolve_styles(shots)   # 「同上」→ 上一镜实际风格
    # 「模式」的**唯一决策点**（见 media/video_plan.py）：用什么图 / 要不要抽尾帧 /
    # 能不能平铺，都在一次 `VideoPlan.of()` 里算清。下面只读字段，不再自己 `if mode ==`。
    vplan = video_plan.VideoPlan.of()
    video_mode = vplan.mode
    blocked = sheettext.flagged_urls(project_root, ep, log) if vplan.from_sheets else {}
    for s, p in zip(shots, planned):
        name = s["name"]
        dest = jobs_mod.local_clip(clip_dir, name)
        if _only is not None and name not in _only:
            # 单镜重渲：非目标镜**永不提交**（哪怕 job 状态不是 completed）。
            # 盘上有 clip 就照样进 `done` 并抽尾帧 —— 前者供调用方拼完整成片，
            # 后者供 keyframe 档下目标镜承接。
            if dest.exists():
                done[name] = str(dest)
                prev_tail = _tail_if_needed(dest, vplan) or prev_tail
            continue
        if jobs_mod.done(jobs, name, clip_dir):
            done[name] = str(dest)
            prev_tail = _tail_if_needed(dest, vplan) or prev_tail
            continue
        plan = p.get("frame_plan", {})
        own = (stills.get(name) or {}).get("url")
        # 「图怎么用」按模式分流（2026-09-13，见 config.VIDEO_MODE）：
        #   · reference（默认）：喂什么图由 `VideoPlan.from_sheets` 定 ——
        #       True（2026-10-07 新默认）→ 本镜的资产图（定妆照/场景空镜/道具），
        #         **静帧不再是输入**，所以这一镜没有静帧也能渲（见下面的放行）；
        #       False → 旧行为，静帧当 `<Picture 1>` 参考图，各镜完全独立。
        #   · keyframe（回退档）：连续/匹配镜 first=上一镜真实尾帧（承接动作）、
        #     last=本镜静帧（收在本镜该有的构图）——静帧因此不会被浪费。
        images: list[str] = []
        ref_roles: list | None = None
        first = last = None
        use_tail = False
        if video_mode == "reference":
            if vplan.from_sheets:
                images, ref_roles = assets.sheets_for_shot(project_root, s, ep=ep)
                images, ref_roles = drop_flagged(images, ref_roles, blocked, name, log)
            if not images:
                if not own:
                    log("[video] %s 既无资产图也无静帧，跳过" % name)
                    jobs_mod.mark(jobs, name, "failed", error="无图可喂")
                    continue
                if vplan.from_sheets:
                    log("[video] ⚠️ %s 没绑到任何资产图（注册表空/名字没对上？）"
                        "⇒ 这一镜退回喂静帧。整批都这样说明资产阶段没跑成。" % name)
                images = [own]
                ref_roles = None
        else:
            if not own:
                log("[video] %s 无静帧，跳过" % name)
                jobs_mod.mark(jobs, name, "failed", error="无静帧")
                continue
            use_tail = bool(plan.get("use_prev_last")) and bool(prev_tail)
            # 首尾帧模式（连续/匹配镜）
            first = prev_tail if use_tail else own
            last = own if use_tail else None

        rec = jobs.setdefault(name, {"state": "pending", "attempts": 0})
        # 续跑认领：已提交但未完成的任务先接着轮询，不重复提交（省配额）
        if rec.get("state") == "submitted" and rec.get("video_id"):
            log("[video] %s 续跑认领已提交任务（attempts=%d）"
                % (name, rec.get("attempts") or 1))
            local = _wait_one(rec["video_id"], dest, rounds=rounds, log=log,
                              key=rec.get("key"))
            if local:
                jobs_mod.mark(jobs, name, "completed", local=str(dest))
                jobs_mod.save(out_dir, jobs)
                done[name] = local
                prev_tail = _tail_if_needed(Path(local), vplan) or prev_tail
                log("[video] %s done（认领）" % name)
                continue
            jobs_mod.mark(jobs, name, "expired", error="续跑轮询超窗")
            jobs_mod.save(out_dir, jobs)

        # 提交：**队列满(503) 是可恢复的**——退避重试同一镜，而不是直接判死。
        # 实测（2026-09-10）：批量重拍时反复收到 503 video_queue_full，原实现
        # 立即 mark failed → 该镜缺席成片。队列满只意味着"稍后再来"。
        vprompt = prompt_mod.build_video_prompt(s, p, mode=video_mode,
                                                ref_roles=ref_roles)
        r = None
        stop_chain = False
        for q_try in range(config.VIDEO_QUEUE_RETRIES + 1):
            # 平铺闸门：供应商 1rpm 的现实约束（串行等待通常已超过间隔）
            if last_submit:
                gap = config.VIDEO_SUBMIT_MIN_INTERVAL_S - (time.time() - last_submit)
                if gap > 0:
                    time.sleep(gap)
            try:
                if video_mode == "reference":
                    r = providers.submit_video(vprompt, mode="reference",
                                               images=images,
                                               seconds=s.get("seconds") or 8)
                else:
                    r = providers.submit_video(vprompt, mode="keyframe",
                                               first_frame=first, last_frame=last,
                                               seconds=s.get("seconds") or 8)
                break
            except providers.QueueFullError:
                if q_try >= config.VIDEO_QUEUE_RETRIES:
                    log("[video] %s 队列持续满（%d 次），本轮放弃、下轮再补"
                        % (name, q_try + 1))
                    break
                wait = min(120, 20 * (q_try + 1))
                log("[video] %s 队列满，%ds 后重试（%d/%d）"
                    % (name, wait, q_try + 1, config.VIDEO_QUEUE_RETRIES))
                time.sleep(wait)
            except providers.RateLimitError:
                log("[video] %s 429：闸门/下一轮再补" % name)
                stop_chain = True
                break
            except Exception as e:  # noqa: BLE001
                log("[video] %s FAILED: %s" % (name, str(e)[:120]))
                jobs_mod.mark(jobs, name, "failed", error=str(e)[:200])
                jobs_mod.save(out_dir, jobs)
                break
        if stop_chain:
            break
        if r is None:
            continue
        last_submit = time.time()
        vid = r.get("video_id") or r.get("task_id")
        # 记账里的 `first_frame` 字段语义随模式变（两模式都必须有"驱动图"）：
        #   reference → 记静帧 URL（它进的是 images，不是 first_frame）
        #   keyframe  → 记真正的首帧（可能是上一镜尾帧）
        anchor = ((images[0] if images else own) if video_mode == "reference"
                  else first)
        jobs_mod.submitted(
            jobs, name, vid,
            first_frame_kind=("reference_sheets" if (video_mode == "reference"
                                                    and ref_roles)
                              else ("reference_still" if video_mode == "reference"
                                    else ("prev_tail" if use_tail else "own_still"))),
            has_last_frame=bool(last),
            first_frame=(anchor or "")[:120]
            + ("..." if anchor and len(anchor) > 120 else ""),
            # `first_frame` 这个名字是 keyframe 档留下的；sheets 档真正发出去的是
            # 下面这批图 —— 整批记下来，读账的人不必猜那个字段现在装的是什么。
            input_images=[u[:120] for u in images] if images else None,
            seconds=s.get("seconds") or 8)
        jobs_mod.save(out_dir, jobs)
        log("[video] %s submitted (%s, %s%s)"
            % (name, plan.get("relation"), jobs[name]["first_frame_kind"],
               ", last=own_still" if last else ""))

        # 注：submit_chain 是**单 key 顺序路径**（提交不带 key ⇒ providers 走默认
        # AGNES_API_KEY/全局地址），因此轮询也不传 key —— key 与地址同源自洽。
        # 多 key/双入口走 submit_all / submit_packs（那边逐任务记 key）。
        local = _wait_one(vid, dest, rounds=rounds, log=log)
        if local:
            jobs_mod.mark(jobs, name, "completed", local=str(dest), error="")
            done[name] = local
            prev_tail = _tail_if_needed(Path(local), vplan) or prev_tail
            log("[video] %s done" % name)
        else:
            jobs_mod.mark(jobs, name, "expired", error="轮询超窗")
            log("[video] %s 未在轮询窗口内完成" % name)
        jobs_mod.save(out_dir, jobs)
    log("[video] 任务状态：%s" % jobs_mod.summary(jobs))
    return done


# ─── 并铺式（降级；各镜用自己的静帧，速度快但连续镜会重复）───────────────────

def submit_all(project_root: Path, shots: list[dict], stills: dict, planned: list[dict],
               ep: int = 1, log=print, tails: dict | None = None,
               only: list[str] | None = None) -> dict:
    """一次性平铺提交全部镜头。返回 jobs（显式状态机）。

    **tails（落幅帧图，可选）**：给了就支持连续镜承接 ——
    连续/匹配镜的 first_frame 取**上一镜的预生成落幅图**，last_frame 取本镜静帧。
    这样各镜之间没有依赖，可以整批同时排队。

    不给 tails 时退化为"各镜都用自己的静帧"：快，但连续镜的画面会重复
    （首帧等于自己的构图，接不上上一镜的落点）——这是历史行为，保留为兜底。
    详细取舍见 `config.TAIL_PREGEN`。

    **模式（2026-09-13）**：与 `submit_chain` 使用**同一个** `config.VIDEO_MODE`。
    两条提交路径必须同构 —— 否则开了 `TAIL_PREGEN` 就会走到这条路，用 keyframe
    渲出与主路径不同的产物（keyframe 继承静帧画幅、reference 按 `aspect_ratio`
    输出 → 同一部片画幅漂移）。reference 下 `tails` 不再有消费方。

    **`only`（2026-09-13，单镜重渲）**：只提交这些镜；非目标镜**绝不提交**
    （白烧配额），但仍会推进 `prev_name`，使目标镜能取到"上一镜预生成落幅图"。

    **提交配速（2026-09-16）**：闸门从"**全局** sleep(`VIDEO_SUBMIT_MIN_INTERVAL_S`)"
    改为"**per-key 闸门 + key 轮转**"（`keypool.KeyPool`）—— 实测 1rpm 是 per-key，
    故提交段可按 key 数摊薄。未开 `VIDEO_KEY_ROTATE` 时池里只有第一条 key，
    行为与改造前等价。详见 `keypool.py` 文件头与 `config.VIDEO_KEY_ROTATE`。
    """
    out_dir = project_root / "media" / ("ep" + str(ep))
    out_dir.mkdir(parents=True, exist_ok=True)
    clip_dir = out_dir / "clips"
    clip_dir.mkdir(parents=True, exist_ok=True)
    jobs = jobs_mod.load(out_dir)
    tails = tails or {}
    _only = set(only) if only else None
    # 「模式」的**唯一决策点**（见 media/video_plan.py）：用什么图 / 要不要抽尾帧 /
    # 能不能平铺，都在一次 `VideoPlan.of()` 里算清。下面只读字段，不再自己 `if mode ==`。
    vplan = video_plan.VideoPlan.of()
    video_mode = vplan.mode
    blocked = sheettext.flagged_urls(project_root, ep, log) if vplan.from_sheets else {}

    prev_name = None
    # ── 提交配速：**per-key 闸门 + key 轮转**（2026-09-16）──────────────────────
    # 旧实现是全局 `sleep(VIDEO_SUBMIT_MIN_INTERVAL_S)`：40 镜 × 65s ≈ **43 分钟**
    # 纯等待。实测（`scripts/probe_multikey.py`）证明 1rpm 是 **per-key** ——
    # 同 key 60s 内第二次 429、**不同 key 间隔 2s 都通过** ⇒ 让每镜轮流用不同 key，
    # 提交段即按 key 数摊薄。**不需要多线程**（提交调用本身只占 1–2 秒，瓶颈 100%
    # 是闸门），因此 `video_jobs.json` 的单写入者假设**不必动**。
    # `VIDEO_KEY_ROTATE=0`（默认）时池里只有第一条 key ⇒ 闸门与旧行为等价。
    pool = keypool.KeyPool.of()
    log("[video] 提交配速：%d 条 key × %s%s"
        % (len(pool), pool.pacing(),
           "（轮转：提交段约摊薄 %d 倍）" % len(pool) if len(pool) > 1 else ""))
    for s, p in zip(shots, planned):
        name = s["name"]
        if _only is not None and name not in _only:
            # 单镜重渲：非目标镜绝不提交（白烧配额）；仍推进 `prev_name`，
            # 使目标镜能取到"上一镜预生成落幅图"（keyframe 档的承接依据）。
            prev_name = name
            continue
        if jobs_mod.done(jobs, name, clip_dir):
            prev_name = name
            continue
        rec = jobs.setdefault(name, {"state": "pending", "attempts": 0})
        if rec.get("state") == "submitted" and rec.get("video_id"):
            prev_name = name
            continue          # 已提交，等 poll_all 轮询，不重复提交
        plan = p.get("frame_plan", {})
        own = (stills.get(name) or {}).get("url")
        # mixed 下逐镜选模式（其余模式恒等返回全局值）——见 video_plan.mode_for
        shot_mode = vplan.mode_for(plan.get("relation"))
        # 连续/匹配镜：承上一镜的**落幅图**（预生成），收在本镜自己的静帧。
        # 没有落幅图就退回 own-still（不能因此不渲）。
        tail_url = (tails.get(prev_name) or {}).get("url") if prev_name else None
        first, last, first_kind = own, None, "own_still"
        # 喂资产图时（2026-10-07 新默认）这一镜的输入不再是静帧
        ref_roles: list | None = None
        sheet_imgs: list[str] = []
        if shot_mode == "reference":
            # reference 不允许 first_frame（见 providers.submit_video）→ 各镜独立。
            # 喂什么图由 `from_sheets` 定：True → 本镜资产图（定妆照/场景/道具），
            # 静帧退出输入；False → 旧行为，静帧进 images 当 `<Picture 1>`。
            # 连带 `tails`（落幅预生成）在这条路径上不再有消费方。
            if vplan.from_sheets:
                sheet_imgs, ref_roles = assets.sheets_for_shot(project_root, s, ep=ep)
                sheet_imgs, ref_roles = drop_flagged(sheet_imgs, ref_roles,
                                                     blocked, name, log)
            first_kind = "reference_sheets" if sheet_imgs else "reference_still"
            if sheet_imgs:
                first = sheet_imgs[0]
        elif plan.get("use_prev_last") and tail_url:
            first, last, first_kind = tail_url, own, "prev_tail_pregen"
        if not first:
            log("[video] %s 无首帧，跳过" % name)
            jobs_mod.mark(jobs, name, "failed", error="无首帧")
            prev_name = name
            continue
        # 提交重试（队列满是**瞬时**故障，与 submit_chain 保持同一策略）。
        # 2026-09-10 实测缺口：并铺式路径只特判了 429，队列满走通用异常 →
        # 立即 mark failed → 该镜缺席（LN09/LN10 就这样丢的）。
        r = None
        # **领 key = 过闸门**：阻塞到这条 key 的窗口放开，返回即已占用。
        # 选的是"最早到期"的那条（多条 key 自然轮转）。
        vp = prompt_mod.build_video_prompt(s, p, mode=shot_mode,
                                          ref_roles=ref_roles)
        for q_try in range(config.VIDEO_QUEUE_RETRIES + 1):
            # ★ 每轮**重新领 key**（2026-09-22 队列满换通道改造）：队列满是
            #   **每条通道各自的状态**（实测同一晚 pack03 撞满 2 次后由另一条 key
            #   提交成功；国际池堵死时国内入口可能立刻就过）。旧逻辑一条 key 原地
            #   退避 60/120/180/240/300s（≈15 分钟）——换通道能绕开单池拥堵。
            key_idx, key = pool.claim()
            try:
                if shot_mode == "reference":
                    r = providers.submit_video(vp, mode="reference",
                                               images=sheet_imgs or [first],
                                               seconds=s.get("seconds") or 8, key=key)
                else:
                    r = providers.submit_video(vp, mode="keyframe",
                                               first_frame=first, last_frame=last,
                                               seconds=s.get("seconds") or 8, key=key)
                break
            except providers.QueueFullError:
                # 这条通道满了 → 冷却它一轮（下一轮 claim 自然换到别的 key）
                pool.note_rate_limited(key_idx)
                if q_try >= config.VIDEO_QUEUE_RETRIES:
                    log("[video] %s 队列持续满（%d 次），本轮放弃、下轮再补"
                        % (name, config.VIDEO_QUEUE_RETRIES))
                    break
                # 池里还有没试过的通道 → 快试；一圈试完 → 回到长退避等队列恢复。
                n_keys = max(1, len(pool))
                tried = q_try + 1
                wait = (8 * tried if tried < n_keys
                        else min(300, 60 * (tried - n_keys + 1)))
                log("[video] %s 队列满，换 key 重试（%d/%d）%ds 后"
                    % (name, tried, config.VIDEO_QUEUE_RETRIES, wait))
                time.sleep(wait)
            except providers.RateLimitError:
                # 429 是配额闸门：**跳过本镜、继续提交其余镜**。
                # 旧实现的 `break` 会让 429 直接中断整批提交——后面所有镜
                # 连提交机会都没有（LN11 之后的镜就被这样挡掉了）。
                # 2026-09-16：本镜撞的这个 key 先**拉黑一轮**，下一镜自动换 key ——
                # 否则轮转会把 429 甩给别人、又被甩回来（三镜卡死在同一窗口里）。
                pool.note_rate_limited(key_idx)
                log("[video] %s 429（%s 拉黑一轮）：跳过本镜，其余镜继续提交"
                    % (name, pool.label(key_idx)))
                r = None
                break
            except Exception as e:  # noqa: BLE001
                log("[video] %s FAILED: %s" % (name, str(e)[:100]))
                jobs_mod.mark(jobs, name, "failed", error=str(e)[:200])
                jobs_mod.save(out_dir, jobs)
                r = None
                break
        if r is None:
            prev_name = name
            continue
        jobs_mod.submitted(jobs, name, r.get("video_id") or r.get("task_id"),
                           first_frame_kind=first_kind, first_frame=first[:120],
                           has_last_frame=bool(last),
                           input_images=[u[:120]
                                         for u in (sheet_imgs or [first])],
                           seconds=s.get("seconds") or 8, key=key)
        jobs_mod.save(out_dir, jobs)
        log("[video] %s submitted (%s, first=%s%s, %s)"
            % (name, plan.get("relation"), first_kind,
               ", last=own_still" if last else "", pool.label(key_idx)))
        prev_name = name
    if len(pool) > 1:
        log("[video] key 用量：%s" % pool.stats())
    return jobs


# ─── pack 档（2026-09-22）：相邻同场景镜打包 ≤12s reference 请求 ─────────────
#
# 依据（三项目实测闭环，详见 scripts/pack_render.py 与项目记忆）：
#   · 逐镜 reference 时模型不知道相邻镜存在 → 跨请求接缝形制跳变；
#   · 打包成一条 ≤12s 请求后接戏变成"单请求内部问题"——捕梦师 15/15 组零拒绝、
#     打包内部缝教科书级连续、时长纪律 +1%；
#   · 分组算法/prompt 骨架自旁路脚本搬入 `video_plan.group_shots` /
#     `prompt.build_pack_prompt`（两处判据必须同步，脚本保留为独立验证入口）。
#
# 与 submit_all 同构（提交与等待解耦）：这里只提交，等待走 poll_all；
# 产物是**组级** clip（clips/packNN.mp4，一个文件含该组全部镜），
# `expand_packs` 负责把组级结果展开成"每镜→其组成片"，下游缺镜判定零改动。

def rate_limit_should_retry(q_try: int, n_keys: int) -> bool:
    """撞 429 后，**同一组内**要不要换 key 再试一次。

    抽成纯函数的理由同 `series._rerender_ep_conflict`：整条 pack 提交循环要 mock
    掉 provider / pool / 静帧 / 绑定 / 提示词才测得到，而真正会错的只有这一个判断。

    规则：一条通道撞 429 就把它拉黑，**还有没试过的通道就换一条重试本组**；
    试完一圈（`q_try + 1 >= n_keys`）才放弃本组。
    单 key 池 ⇒ 立刻放弃（等价于改造前的行为，不多等）。
    """
    return q_try + 1 < max(1, n_keys)


def pack_ref_images(project_root: Path, group: list[dict],
                    prev_url: str | None = None, ep=None) -> tuple[list[str], list[tuple[str, str]]]:
    """A 臂图序（2026-09-28 实测）：**身份由人物设定表锁，静帧只当场景实现与接续锚**。

    返回 `(urls, roles)`，`roles` 与 `urls` 同序，供
    `prompt.pack_ref_declaration` 生成"第 N 张参考图是什么"的分工声明。

    ★ **为什么不再"每镜一张静帧"**：静帧本身可能画错身份，而视频模型会**忠实继承**
    那个错。v2 华山论剑逐帧实测：喂静帧的基准臂里谢潮生一路是黑发+粉紫裙（静帧就这么
    画的，QC 重画 2-3 次没收敛）；把身份来源换成设定表后 6/6 帧回到霜白长发+月白袍。
    ★ **为什么仍留两张静帧**：设定表锁不住"这一处场景长什么样"——去掉静帧后同一个
    「云海之上的孤峰松坪」在相邻两组里长成石台孤松 vs 高大松林两种样子（接缝跳）。
    留「本组首镜静帧」当场景实现、「前组末镜静帧」当接续，并在声明里写明
    "这两张里的人物若与设定表不一致就忽略其人物"，实测身份与场景同时保住。

    槽位优先级（`images` 上限 5）：人物设定表（≤2）→ 场景空镜 → 本组首镜静帧
    → 前组末镜静帧 → 道具图补空位。
    """
    names_out: dict = {}
    types_out: dict = {}
    bound = assets.bind(project_root, group, names_out=names_out, types_out=types_out,
                        ep=ep)
    chars: list[tuple[str, str]] = []      # (url, 资产名)
    locs: list[tuple[str, str]] = []
    props: list[tuple[str, str]] = []
    seen: set[str] = set()
    for s in group:
        urls_s = bound.get(s["name"]) or []
        nms = names_out.get(s["name"]) or []
        tys = types_out.get(s["name"]) or []
        for u, nm, kind in zip(urls_s, nms, tys):
            if not u or u in seen:
                continue
            seen.add(u)
            if kind == "character":
                chars.append((u, nm))
            elif kind == "location":
                locs.append((u, nm))
            else:
                props.append((u, nm))
    # bind 为静帧限制总图数：宽景两人+场景已经占满，关键道具会被截掉。
    # pack 的五槽分工不同，在分组层从命中的资产补取道具，再按本入口槽位裁决。
    reg = assets.auto_sync(project_root)
    for s in group:
        hits, _ = assets.hits_for_shot(reg, s, max_n=max(5, len(reg.get('assets', []))))
        for a in hits:
            if a.get('type') == 'character' or a.get('type') in assets.LOCATION_TYPES:
                continue
            for u in assets._safe_ref_urls(a, project_root):
                if u and u not in seen:
                    seen.add(u)
                    props.append((u, str(a.get('name') or '')))
    urls: list[str] = []
    roles: list[tuple[str, str]] = []

    def add(u: str | None, kind: str, label: str) -> None:
        if u and len(urls) < video_plan.REF_SLOTS and u not in urls:
            urls.append(u)
            roles.append((kind, label))

    for u, nm in chars[:video_plan.PACK_REF_MAX_CHARS]:
        add(u, "character", "角色「%s」的人物设定表" % nm)
    # 场景图：`bind()` 只在**宽景**绑 location（那条律是给静帧构图定的 —— 场景图自带
    # 固定机位会把静帧拉回大 Wide）。pack 档不吃这条律：这里的声明写明"只锁建筑与地貌、
    # 机位听文字"，所以按**表列**无条件取，与 `assets.sheets_for_shot` 同一口径
    # —— 两处判据不一样，就会出现"逐镜档能拿到场景、打包档拿不到"的怪事。
    if not locs:
        reg = assets.auto_sync(project_root)
        for g0 in group:
            a, nm = assets.scene_asset_for_shot(reg, g0)
            if a:
                for u in assets._safe_ref_urls(a, project_root):
                    if u:
                        locs.append((u, nm))
                break
    for u, nm in locs[:video_plan.PACK_REF_MAX_LOCS]:
        add(u, "location", "场景「%s」的空镜（只锁建筑与地貌，不锁机位）" % nm)
    # ★ 2026-10-07：**去掉「本组首镜静帧」这一格**（用户决定，与 reference 档同口径）。
    #   原先占这一格的理由是 09-28 那次实测（"完全去掉静帧，同一处场景在相邻两组里
    #   长成两种样子"），但那次实验之后绑图侧改了三件事，前提已经不成立：
    #     · 10-05/10-06 道具与场景的**简称也能绑上**、场景按关键词兜底
    #       （`assets.hits_for_text` / `scene_asset_for_shot`）—— 当年场景空镜常常
    #       一张都进不来，等于让静帧替它上班；
    #     · 10-07 逐镜档两轮实跑（命案 15 镜、仙侠 6 镜）证明"场景空镜 + 文字锚点"
    #       撑得住地貌一致；
    #   ⇒ 这一格让给道具与更多设定表。**跨组接缝另有正确的来源**：`seam_anchor`
    #     抽上一组成片的真实末帧当下一段的起帧锚（那是"结束画面"，静帧是"起幅画面"，
    #     拿静帧接下一段本来就是 09-28 记过的那句假话）。
    if prev_url:
        add(prev_url, "prev",
            "**上一片段的真实结束状态**（场景、人物站位、姿态、持物与动作进度）")
    prop_specs = assets.key_prop_specs(project_root)
    props.sort(key=lambda item: item[1] not in prop_specs)
    for u, nm in props:
        label = "道具「%s」" % nm
        if nm in prop_specs:
            label += "；本项目道具规格：" + prop_specs[nm]
        add(u, "prop", label)
    return urls, roles


def seam_anchor(clip_dir: Path, prev_pname: str | None) -> tuple[str | None, str]:
    """跨组接续锚帧 = **上一组成片的真实末帧**。抽不到就没有锚帧（不再拿静帧凑数）。

    ★ 2026-09-28 实测修：原先这里退回用 `stills[前组末镜]`，但静帧是该镜的**第一拍
    = 起幅画面**，而提示词把它声明成"上一片段的结束画面"。并排对照（`tmp/ANCHOR_wrong.png`）：
    pack08 的锚帧是"两人远景站立"、真实末帧是"两人近景剑已相交"；pack07 的锚帧里还画着
    **第三人**。⇒ 每次交接都在对模型说一句假话。
    ★ 2026-10-07 进一步：**静帧整条退出 pack 的输入**，所以连"兜底"这层也删掉 ——
    留着它，媒体链就得继续为 pack 画静帧（`VideoPlan.needs_stills`）。第一组本来也没有
    前段可接，行为一致。
    """
    if prev_pname:
        u = extract_last_frame(clip_dir / (prev_pname + ".mp4"))
        if u:
            return u, "末帧"
    return None, "无"


def _pack_transports(root: Path, groups: list, planned: list[dict]) -> list:
    reg, prot, fallback = assets._cast_ctx(root)
    by_name = {s['name']: s.get('frame_plan', {}).get('relation', 'cut') for s in planned}
    previous_cast = set()
    out = []
    for group, declared in groups:
        casts = [set(assets._shot_cast_lines(s, reg, prot, fallback)) for s in group]
        same_cast = bool(previous_cast) and all(c == previous_cast for c in casts)
        # Use the same registry matcher as reference binding, including prop aliases.
        # Do not drop an available prop image merely because the action is continuous.
        has_prop_reference = any(
            a.get('type') == 'prop' and assets._safe_ref_urls(a, root)
            for s in group
            for a in assets.hits_for_shot(reg, s, max_n=max(5, len(reg.get('assets', []))))[0])
        relation = by_name.get(group[0]['name'], 'cut')
        mode = video_plan.pack_transport(group, relation, same_cast,
                                         has_prop_reference=has_prop_reference)
        out.append(([dict(s, _pack_transport=mode, _pack_relation=relation) for s in group], declared))
        previous_cast = casts[-1]
    return out


def _pack_inputs(root: Path, group: list[dict], previous_url: str | None, ep: int):
    if group and group[0].get('_pack_transport') == 'keyframe':
        return ([previous_url] if previous_url else []), [('prev', '连续动作首帧')]
    urls, roles = pack_ref_images(root, group, prev_url=previous_url, ep=ep)
    if group and group[0].get('_pack_relation') in ('continuous', 'match'):
        # The continuation's primary image is its actual state, not a front-view
        # character sheet. Keep identity and prop images available as references.
        order = sorted(range(len(roles)), key=lambda i: roles[i][0] != 'prev')
        urls, roles = [urls[i] for i in order], [roles[i] for i in order]
    return urls, roles


def pack_input_signature(project_root: Path, group: list[dict], declared: list,
                         ep: int, previous: Path | None = None, *, legacy_reference: bool = False,
                         legacy_labels: bool = False) -> str:
    """Bind reuse to the compiled input and actual local reference pixels.

    Legacy jobs without this field are not silently given current provenance.
    Remote-only references are identified by their URL; no download is needed.
    """
    prev_url = extract_last_frame(previous) if previous else None
    urls, roles = _pack_inputs(project_root, group, prev_url, ep)
    compiled = prompt_mod.build_pack_prompt(
        style_mod.prepare_shots(project_root, group), declared, sum(declared),
        style_block=style_mod.visual_block(project_root), ref_roles=roles)
    local = []
    for kind, label in roles:
        if kind == 'prev':
            continue
        # Reference roles contain display labels, not raw registry names.
        # Passing e.g. 道具「白玉佩」；规格... directly silently returned None.
        named = re.search(r'「([^」]+)」', label) if not legacy_labels else None
        p = assets.local_ref_path(project_root, named.group(1) if named else label)
        local.append((kind, label, hashlib.sha256(p.read_bytes()).hexdigest()
                      if p and p.is_file() else None))
    payload = {'prompt': compiled, 'images': urls, 'local': local, 'aspect': config.ASPECT_RATIO}
    if not legacy_reference:
        payload['mode'] = group[0].get('_pack_transport', 'reference') if group else 'reference'
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False,
        sort_keys=True).encode('utf-8')).hexdigest()


def _pack_input_matches(rec: dict, root: Path, group: list[dict], declared: list,
                        ep: int, previous: Path | None = None) -> bool:
    """Accept the original reference-only hash format without inventing provenance."""
    signature = rec.get('input_signature')
    if not signature:
        return True
    if signature == pack_input_signature(root, group, declared, ep, previous):
        return True
    if rec.get('input_signature_version') == 2:
        return False
    # Old display-label hashes did not record local asset pixels. Grandfather
    # their existing URI/prompt checks, but do not fabricate a pixel baseline.
    if signature == pack_input_signature(root, group, declared, ep, previous,
                                         legacy_labels=True):
        return True
    # Before per-group transport was introduced every tracked job was reference.
    # This alternate hash still checks all actual inputs, including previous pixels.
    if not rec.get('generation_mode') and (not group or group[0].get('_pack_transport', 'reference') == 'reference'):
        return any(signature == pack_input_signature(root, group, declared, ep, previous,
                                                      legacy_reference=True, legacy_labels=old_labels)
                   for old_labels in (False, True))
    return False


def submit_packs(project_root: Path, shots: list[dict], planned: list[dict],
                 ep: int = 1, log=print, only: list[str] | None = None,
                 max_group: int | None = None, *, invalidate: bool = True) -> dict:
    """pack 档提交：相邻同场景镜 → ≤12s 的 reference 请求（一个 job = 一组）。

    job name = `pack01`/`pack02`…（组级）；产物 = `clips/packNN.mp4`（组级）。
    jobs 记账里每条 pack 记录带 `shots`（组内镜名）与 `declared_seconds`
    —— `expand_packs` 与补渲轮都从这里读分组事实，不重算分组（保证幂等：
    分镜若中途改动，重算会错位，这里**以 jobs 表为准**）。

    **`only`（补渲/单镜重渲）**：语义是"镜名"——组内**任一镜**在 only 里，
    整组重渲（打包单位不可拆）。重渲前作废旧产物（删 clip + 状态回 pending）。

    复用机制与 submit_all 逐项对齐：keypool per-key 闸门、队列满退避、
    429 拉黑跳过、jobs 显式状态机、submitted 续跑认领（认领交给 poll_all）。
    """
    out_dir = project_root / "media" / ("ep" + str(ep))
    clip_dir = out_dir / "clips"
    clip_dir.mkdir(parents=True, exist_ok=True)
    jobs = jobs_mod.load(out_dir)
    _only = set(only) if only else None
    shots = prompt_mod.resolve_styles(shots)
    groups = _pack_transports(project_root, video_plan.group_shots(shots, max_group), planned)
    if _only is not None and invalidate:
        # 一次作废所有目标组，避免前组超窗后把尚未重做的旧后组当成本轮成片。
        for k, (g, _declared) in enumerate(groups, 1):
            if _only.intersection(s["name"] for s in g):
                pname = "pack%02d" % k
                dest = clip_dir / (pname + ".mp4")
                if dest.exists():
                    dest.unlink()
                jobs_mod.mark(jobs, pname, "pending", error="", video_id=None)
        jobs_mod.save(out_dir, jobs)
    log("[video] pack 档：%d 镜 → %d 组（max_group=%d）"
        % (len(shots), len(groups), max_group or config.VIDEO_PACK_MAX_GROUP))

    pool = keypool.KeyPool.of()
    log("[video] 提交配速：%d 条 key × %s" % (len(pool), pool.pacing()))
    for k, (g, declared) in enumerate(groups, 1):
        pname = "pack%02d" % k
        names = [s["name"] for s in g]
        total = sum(declared)
        dest = clip_dir / (pname + ".mp4")
        if _only is not None and not (set(names) & _only):
            continue                      # 补渲：只动包含目标镜的组
        rec = jobs.get(pname) or {}
        same_group = rec.get("shots") == names and rec.get("declared_seconds") == declared
        if (_only is None or not invalidate) and same_group and jobs_mod.done(jobs, pname, clip_dir):
            previous = clip_dir / ('pack%02d.mp4' % (k - 1)) if k > 1 else None
            if _pack_input_matches(rec, project_root, g, declared, ep, previous):
                continue                  # 未记录来源的老片不因升级而整集重烧。
            log('[video] %s 实际生成输入已变，旧组不复用' % pname)
            jobs_mod.mark(jobs, pname, 'pending', video_id=None, error='生成输入变化')
        if jobs_mod.done(jobs, pname, clip_dir) and not same_group:
            log("[video] %s 分组成员/时长已变，旧组不复用" % pname)
            jobs_mod.mark(jobs, pname, "pending", video_id=None, error="分组契约变化")
        rec = jobs.setdefault(pname, {"state": "pending", "attempts": 0})
        if not invalidate and rec.get("state") in ("failed", "expired"):
            break  # 失败交给既有补渲预算；接续循环不重提。
        if rec.get("state") == "submitted" and rec.get("video_id"):
            log("[video] %s 续跑认领已提交任务（poll_all 接管）" % pname)
            break  # 等原任务落盘后再提交依赖它的下一组。
        # ★ 跨组接续锚（2026-09-23 立、2026-10-07 收口）：**只用上一组成片的真实末帧**。
        #   组与组独立生成互不知情（灯下棋实测：柳娘坐/站组间跳变、玉佩位置漂移），
        #   而静帧是"起幅画面"、拿它当"上一段的结束画面"是句假话（09-28 记过）——
        #   所以抽不到就这一组没有锚帧，⛔ 不再拿静帧兜底。
        prev_url, prev_src = seam_anchor(
            clip_dir, ("pack%02d" % (k - 1)) if k > 1 else None)
        if k > 1 and not prev_url:
            log("[video] %s 暂缓：上一组真实末帧不可用，保留任务等待接续" % pname)
            break
        # 图序（2026-10-07 起）：设定表 → 场景空镜 → 上一段末帧 → 道具，**不含静帧**。
        urls, roles = _pack_inputs(project_root, g, prev_url, ep)
        if not urls:
            log("[video] %s：%s 一张资产图都没绑上（注册表空 / 名字没对上？）"
                "→ 标 failed（先跑资产生成，别去画静帧）" % (pname, "+".join(names)))
            jobs_mod.mark(jobs, pname, "failed", error="无资产图")
            jobs_mod.save(out_dir, jobs)
            continue
        # 项目风格块（style-block / 项目 style.md）一次加载，逐组复用——
        # 2026-09-22：替换 build_pack_prompt 里硬编码的「国风古装」句（题材污染）。
        prompt = prompt_mod.build_pack_prompt(
            style_mod.prepare_shots(project_root, g), declared, total,
            style_block=style_mod.visual_block(project_root),
            ref_roles=roles)
        log("[video] %s：%s 合计 %ds，prompt=%d 字，images=%d（%s）接续锚=%s"
            % (pname, "+".join(names), total, len(prompt), len(urls),
               "、".join(k for k, _l in roles), prev_src))
        input_signature = pack_input_signature(
            project_root, g, declared, ep,
            clip_dir / ('pack%02d.mp4' % (k - 1)) if k > 1 else None)
        transport = g[0]['_pack_transport']
        log('[video] %s 实际请求模式=%s' % (pname, transport))
        # 提交：队列满时**换 key 重试**（2026-09-22 改造，同 submit_all 的理由：
        # 队列满是每条通道各自的状态，跨入口换通道能绕开单池拥堵）。
        r = None
        for q_try in range(config.VIDEO_QUEUE_RETRIES + 1):
            key_idx, key = pool.claim()
            try:
                media_input = ({'first_frame': prev_url} if transport == 'keyframe'
                               else {'images': [u for u in urls if u]})
                r = providers.submit_video(prompt, mode=transport,
                                           **media_input,
                                           seconds=total, key=key,
                                           aspect_ratio=config.ASPECT_RATIO)
                break
            except providers.QueueFullError:
                pool.note_rate_limited(key_idx)      # 该通道满了 → 冷却一轮
                if q_try >= config.VIDEO_QUEUE_RETRIES:
                    log("[video] %s 队列持续满（%d 次），本轮放弃、下轮再补"
                        % (pname, q_try + 1))
                    break
                n_keys = max(1, len(pool))
                tried = q_try + 1
                wait = (8 * tried if tried < n_keys
                        else min(300, 60 * (tried - n_keys + 1)))
                log("[video] %s 队列满，换 key 重试（%d/%d）%ds 后"
                    % (pname, tried, config.VIDEO_QUEUE_RETRIES, wait))
                time.sleep(wait)
            except providers.RateLimitError:
                pool.note_rate_limited(key_idx)
                # ★ 429 不再一撞就放弃本组（2026-09-30 实测）。
                #   旧行为：拉黑当前 key + `break` —— 拉黑的意义是让**下一组**换 key，
                #   可如果这是最后一组，就没有下一组了。实测 duanji-gui-0930 ep2 的
                #   pack10 连续三次都是 `k1=1/429x1  k2=0  k3=0`：三条通道里
                #   **两条从没被试过**，而新进程的轮转又固定从 k1 开始 ⇒ 盲目重试
                #   必然再撞同一个 429（三次实测同一结果，已证伪"重跑就好"）。
                #   ⇒ 对齐上面 503 的处理：同一组内把 key **试完一圈**才放弃；
                #     一圈之后仍照旧"跳过本组、其余组继续"，不阻塞整批。
                n_keys = max(1, len(pool))
                if rate_limit_should_retry(q_try, n_keys):
                    log("[video] %s 429（%s 拉黑）→ 换下一条 key 重试本组"
                        "（已试 %d/%d 条通道）"
                        % (pname, pool.label(key_idx), q_try + 1, n_keys))
                    time.sleep(6)
                    continue
                log("[video] %s 429：%d 条通道都试过 → 跳过本组，其余组继续"
                    % (pname, n_keys))
                r = None
                break
            except Exception as e:  # noqa: BLE001
                log("[video] %s FAILED: %s" % (pname, str(e)[:120]))
                jobs_mod.mark(jobs, pname, "failed", error=str(e)[:200])
                jobs_mod.save(out_dir, jobs)
                r = None
                break
        if r is None:
            continue
        vid = r.get("video_id") or r.get("task_id")
        jobs_mod.submitted(jobs, pname, vid,
                           first_frame_kind=("keyframe_pack" if transport == 'keyframe' else "reference_pack"),
                           shots=names, declared_seconds=declared, total_seconds=total,
                           key=key,
                           input_signature=input_signature, input_signature_version=2)
        jobs[pname]['generation_mode'] = transport
        jobs_mod.save(out_dir, jobs)
        log("[video] %s submitted（%d 镜打包，%ds，%s）"
            % (pname, len(names), total, pool.label(key_idx)))
        # ★ **串行等本组落盘再提交下一组**（2026-09-28 修）：下一组的接续锚帧要从
        #   **本组成片**抽真实末帧（`seam_anchor`），而旧时序是"先把所有组提交完、
        #   最后统一轮询下载"⇒ 提交 pack N 时 pack N-1 还没落盘，锚帧**永远**走
        #   "静帧兜底"，末帧修复形同没做（v4 实测 12 组全是 `接续锚=静帧兜底`）。
        #   代价：失去跨 key 并发，整轮多 20-40 分钟；换来的是真的动作接续。
        landed = _wait_one(vid, dest, log=log, key=key)
        if landed:
            jobs_mod.mark(jobs, pname, "completed", local=str(dest))
            jobs_mod.save(out_dir, jobs)
            log("[video] %s done（串行落盘，供下一组抽末帧锚）" % pname)
        else:
            log("[video] %s 尚未落盘，后续组暂缓；继续轮询原任务" % pname)
            break
        # 没落盘就**保持 submitted**：`poll_all` 与补渲轮靠这个状态认领原任务继续轮询，
        # 在这里改判 expired 会把一个可能还在出的任务丢掉（并导致下一组退化成静帧兜底，
        # 那是降级不是失败）。
    if len(pool) > 1:
        log("[video] key 用量：%s" % pool.stats())
    jobs_mod.save(out_dir, jobs)
    return jobs


def run_packs(project_root: Path, shots: list[dict], planned: list[dict],
              ep: int = 1, log=print, only: list[str] | None = None) -> tuple[dict, dict]:
    """串行提交并轮询；超窗后落盘的前组完成，才继续提交后组。

    重渲只在第一次作废目标组，接续循环复用已完成/已提交任务。
    没有进展就返回缺镜，不无限等待，也不提交缺少接续图的后段。
    """
    jobs = submit_packs(project_root, shots, planned, ep=ep, log=log, only=only)
    done = poll_all(project_root, jobs, ep=ep, log=log)
    groups = _pack_transports(project_root,
                             video_plan.group_shots(prompt_mod.resolve_styles(shots)), planned)
    targets = {"pack%02d" % k for k, (g, _d) in enumerate(groups, 1)
               if not only or set(only).intersection(s["name"] for s in g)}
    for _ in range(len(targets)):
        missing = targets.difference(done)
        if not missing:
            break
        # 只有前组已经落盘、后组从未提交时才继续；失败留给流水线的一轮补渲。
        ready = any(not jobs.get(n, {}).get("video_id")
                    and (n == "pack01" or "pack%02d" % (int(n[4:]) - 1) in done)
                    for n in missing)
        if not ready:
            break
        before = set(done)
        jobs = submit_packs(project_root, shots, planned, ep=ep, log=log,
                            only=only, invalidate=False)
        done = poll_all(project_root, jobs, ep=ep, log=log)
        if not set(done).difference(before):
            break
    # 历史组可保留在任务表与磁盘，但不能覆盖当前镜或冒充本轮完整。
    expected = {"pack%02d" % k: ([s["name"] for s in g], d)
                for k, (g, d) in enumerate(groups, 1)}
    done = {n: p for n, p in done.items() if n in expected
            and jobs.get(n, {}).get("shots") == expected[n][0]
            and jobs.get(n, {}).get("declared_seconds") == expected[n][1]}
    # 单镜重渲不扩面，但已知输入过期的非目标组不能混进新成片。
    stale_previous = False
    for k, (g, declared) in enumerate(groups, 1):
        name = 'pack%02d' % k
        rec = jobs.get(name, {})
        signature = rec.get('input_signature')
        previous = project_root / 'media' / ('ep%d' % ep) / 'clips' / ('pack%02d.mp4' % (k - 1)) if k > 1 else None
        stale = name in done and signature and not _pack_input_matches(
            rec, project_root, g, declared, ep, previous)
        if stale or stale_previous:
            done.pop(name, None)
            log('[video] %s 输入或前组接续已过期，不参与本轮合成' % name)
            stale_previous = True
    return jobs, done


def expand_packs(project_root: Path, ep: int, done: dict, log=print) -> dict:
    """组级结果 → 镜级结果：{pack01: path} → {LN01: path, ...}。

    每镜指向**其所在组的成片**（下游缺镜判定/成片路径消费零改动）。
    分组事实读 jobs 表的 `shots` 字段（submit_packs 提交时写的），**不重算**
    —— 分镜若在两轮之间改动，重算会错位；以磁盘记账为准。
    只展开带 `shots` 字段的记录（pack 记录）；旧的逐镜 LN 记录不透传
    —— 切到 pack 模式就是要按打包产物出片，历史单镜 clip 不顶包。
    """
    jobs = jobs_mod.load(project_root / "media" / ("ep" + str(ep)))
    out: dict[str, str] = {}
    for pname, path in (done or {}).items():
        if not (pname.startswith("pack") and path):
            continue
        for n in (jobs.get(pname) or {}).get("shots") or []:
            out[n] = path
    log("[video] pack 展开：%d 组 → %d 镜" % (len(done or {}), len(out)))
    return out


def poll_all(project_root: Path, jobs: dict, ep: int = 1,
             rounds: int | None = None, log=print) -> dict:
    """轮询 + 落盘（并铺式提交的收尾）。返回 {name: local_path}。"""
    out_dir = project_root / "media" / ("ep" + str(ep))
    clip_dir = out_dir / "clips"
    clip_dir.mkdir(parents=True, exist_ok=True)
    jobs = jobs_mod.migrate(jobs or jobs_mod.load(out_dir))
    rounds, gap = poll_window(rounds, default_rounds=POLL_ROUNDS, default_interval=30)
    done: dict[str, str] = {}
    for name in jobs:
        if jobs_mod.done(jobs, name, clip_dir):
            done[name] = str(jobs_mod.local_clip(clip_dir, name))
    for _ in range(rounds):
        pending = [n for n in jobs
                   if jobs[n].get("state") == "submitted" and n not in done]
        if not pending:
            break
        for name in pending:
            try:
                r = providers.query_video(jobs[name]["video_id"],
                                          key=jobs[name].get("key"))
            except Exception:  # noqa: BLE001
                continue
            if r.get("status") == "completed" and r.get("url"):
                import httpx
                dest = jobs_mod.local_clip(clip_dir, name)
                # ★ 下载必须有异常捕获 —— 与 `_wait_one` 里同一条纪律（2026-09-13
                #   CDN 抖动 502 把整条媒体链弄垮、46 镜里已完成的 40 张静帧全废）。
                #   这一支原先是裸奔的 `dest.write_bytes(c.get(url).content)`，
                #   接了本机 ComfyUI 之后更容易撞：那台机器重启 / 文件被清理时
                #   `/view` 直接连不上，异常会从**已经跑完的收尾阶段**冒到顶层。
                try:
                    with httpx.Client(timeout=120, trust_env=False) as c:  # 直连
                        resp = c.get(r["url"])
                        resp.raise_for_status()
                        dest.write_bytes(resp.content)
                except Exception as e:  # noqa: BLE001
                    log("[video] %s 下载失败（将重试）：%s" % (name, str(e)[:80]))
                    continue
                jobs_mod.mark(jobs, name, "completed", local=str(dest), error="")
                done[name] = str(dest)
                log("[video] %s done" % name)
            elif r.get("status") in ("failed", "error"):
                log("[video] %s FAILED: %s" % (name, str(r.get("error"))[:80]))
                jobs_mod.mark(jobs, name, "failed", error=str(r.get("error"))[:200])
        jobs_mod.save(out_dir, jobs)
        time.sleep(gap)
    for name in jobs:
        if name not in done and jobs[name].get("state") == "submitted":
            jobs_mod.mark(jobs, name, "expired", error="轮询超窗")
    jobs_mod.save(out_dir, jobs)
    log("[video] 任务状态：%s" % jobs_mod.summary(jobs))
    return done
