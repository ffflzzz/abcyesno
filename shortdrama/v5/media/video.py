# -*- coding: utf-8 -*-
"""图生视频：每镜的 first_frame / last_frame 由镜间关系决定。

旧架构：文字 + 参考图 → 视频（一次概率跳跃，构图不可控、镜间容易跳脱）
新架构：静帧（已锁身份与构图）→ first_frame → 视频（只让它"动起来"）

尾帧承接（2026-09-08 修）：
  continuous/match 关系的首帧必须是**上一镜渲染成片的真实尾帧**，不是上一镜的
  静帧——静帧是上一镜的首帧构图，直接拿它当下一镜首帧会让两镜画面逐帧重复
  （实测 LN01/LN02 成片首帧完全相同）。因此连续镜必须**串行**：提交→等完成→
  抽尾帧→下一镜首帧。keyframe 模式接受 base64 data URI，无需公网托管。
"""
from __future__ import annotations

import base64
import io
import subprocess
import time
from pathlib import Path

from .. import config
from . import jobs as jobs_mod
from . import keypool
from . import prompt as prompt_mod
from . import style as style_mod
from . import providers
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
             "-frames:v", "1", "-q:v", "2", str(frame)],
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


def _wait_one(video_id: str, dest: Path, rounds: int = 60, interval: int = 10,
              log=print, key: str | None = None) -> str:
    """轮询单个任务直到完成并落盘。

    返回本地路径（**失败/超窗都返回空串**）。空串的原因由调用方按
    jobs 状态机区分：failed（供应商报错）或 expired（窗口内没完成）。

    `key`（2026-09-22）：**必须传创建该任务的那条 key** —— 国内/国际双入口下
    key 带自己的地址（`config.AGNES_KEY_BASE`），不传会拿全局地址去查国内
    创建的 video_id（403/404/查不到，任务假死）。
    """
    import httpx
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
                 ep: int = 1, log=print, rounds: int = 60,
                 only: list[str] | None = None) -> dict:
    """提交 → 等完成 →（keyframe 模式下）抽尾帧给下一镜当首帧。

    返回 {name: local_path}。**两种模式（见 `config.VIDEO_MODE`）**：
      · `reference`（默认，2026-09-13 起）—— 每镜用自己的静帧当 `<Picture 1>`
        参考图，各镜**完全独立**；"抽尾帧承接"不参与（reference 不允许
        first_frame/last_frame）。函数名里的"chain"在此时只是历史遗留。
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
        if not own:
            log("[video] %s 无静帧，跳过" % name)
            jobs_mod.mark(jobs, name, "failed", error="无静帧")
            continue
        # 「图怎么用」按模式分流（2026-09-13，见 config.VIDEO_MODE）：
        #   · reference（默认）：静帧进 `images`，在提示词里作 `<Picture 1>` 参考图。
        #     **没有首帧锁定**，因此不做承接——各镜完全独立（这顺带消灭了
        #     「连续镜必须等上一镜渲完」的串行瓶颈）。
        #   · keyframe（回退档）：连续/匹配镜 first=上一镜真实尾帧（承接动作）、
        #     last=本镜静帧（收在本镜该有的构图）——静帧因此不会被浪费。
        images: list[str] = []
        first = last = None
        use_tail = False
        if video_mode == "reference":
            images = [own]
        else:
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
        vprompt = prompt_mod.build_video_prompt(s, p, mode=video_mode)
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
        anchor = own if video_mode == "reference" else first
        jobs_mod.submitted(
            jobs, name, vid,
            first_frame_kind=("reference_still" if video_mode == "reference"
                              else ("prev_tail" if use_tail else "own_still")),
            has_last_frame=bool(last),
            first_frame=anchor[:120] + ("..." if len(anchor) > 120 else ""),
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

    prev_name = None
    # ── 提交配速：**per-key 闸门 + key 轮转**（2026-09-16）──────────────────────
    # 旧实现是全局 `sleep(VIDEO_SUBMIT_MIN_INTERVAL_S)`：40 镜 × 65s ≈ **43 分钟**
    # 纯等待。实测（`scripts/probe_multikey.py`）证明 1rpm 是 **per-key** ——
    # 同 key 60s 内第二次 429、**不同 key 间隔 2s 都通过** ⇒ 让每镜轮流用不同 key，
    # 提交段即按 key 数摊薄。**不需要多线程**（提交调用本身只占 1–2 秒，瓶颈 100%
    # 是闸门），因此 `video_jobs.json` 的单写入者假设**不必动**。
    # `VIDEO_KEY_ROTATE=0`（默认）时池里只有第一条 key ⇒ 闸门与旧行为等价。
    pool = keypool.KeyPool.of()
    log("[video] 提交配速：%d 条 key × 间隔 %ss%s"
        % (len(pool), pool.interval_s,
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
        if shot_mode == "reference":
            # reference 不允许 first_frame（见 providers.submit_video）→ 各镜独立，
            # 静帧进 images 当 <Picture 1>。连带 `tails`（落幅预生成）在这条路径上
            # 也不再有消费方。
            first_kind = "reference_still"
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
        vp = prompt_mod.build_video_prompt(s, p, mode=shot_mode)
        for q_try in range(config.VIDEO_QUEUE_RETRIES + 1):
            # ★ 每轮**重新领 key**（2026-09-22 队列满换通道改造）：队列满是
            #   **每条通道各自的状态**（实测同一晚 pack03 撞满 2 次后由另一条 key
            #   提交成功；国际池堵死时国内入口可能立刻就过）。旧逻辑一条 key 原地
            #   退避 60/120/180/240/300s（≈15 分钟）——换通道能绕开单池拥堵。
            key_idx, key = pool.claim()
            try:
                if shot_mode == "reference":
                    r = providers.submit_video(vp, mode="reference", images=[first],
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

def _seam_preview(out_dir: Path, groups: list, stills: dict, log=print) -> Path | None:
    """相邻组交界静帧并排预检图（修法①，默认档：纯拼图、零模型调用）。

    组内接戏由打包内部保证；**跨组接缝**才是剩余风险点 —— 每对相邻组拼一行：
    左=上一组末镜静帧，右=下一组首镜静帧。人眼 30 秒扫完全部组对（21 对 vs
    2.7h 渲染 <3% 成本），把"渲完才发现接不上"的返工挪到提交前。
    返回拼图路径；无 PIL 或组数 <2 时返回 None（不阻断）。
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:  # noqa: BLE001
        log("[video] 无 PIL，跳过接缝预检图")
        return None
    pairs = []
    for k in range(len(groups) - 1):
        pairs.append((k + 1, groups[k][0][-1],        # 上一组末镜
                      k + 2, groups[k + 1][0][0]))    # 下一组首镜
    if not pairs:
        return None

    def _open(name: str):
        rec = stills.get(name) or {}
        for key in ("path", "local", "file"):       # 本地字段优先（url 是图床地址）
            p = rec.get(key)
            if p and Path(p).exists():
                try:
                    return Image.open(p).convert("RGB")
                except Exception:  # noqa: BLE001
                    break
        return None

    THUMB_H, LABEL_W, GAP = 256, 240, 8
    rows: list = []
    for pk, ls, nk, rs in pairs:
        li, ri = _open(ls["name"]), _open(rs["name"])
        w_l = int(li.width * THUMB_H / li.height) if li else THUMB_H
        w_r = int(ri.width * THUMB_H / ri.height) if ri else THUMB_H
        row_w = LABEL_W + w_l + GAP + w_r + 20
        row = Image.new("RGB", (row_w, THUMB_H + 8), (24, 24, 24))
        d = ImageDraw.Draw(row)
        d.text((8, THUMB_H // 2 - 20),
               "pack%02d -> pack%02d\n%s | %s" % (pk, nk, ls["name"], rs["name"]),
               fill=(230, 230, 230))
        x = LABEL_W
        for im, w in ((li, w_l), (ri, w_r)):
            if im:
                row.paste(im.resize((w, THUMB_H)), (x, 4))
            else:
                d.rectangle([x, 4, x + w, 4 + THUMB_H], fill=(60, 60, 60))
                d.text((x + 8, THUMB_H // 2), "缺静帧", fill=(255, 120, 120))
            x += w + GAP
        rows.append(row)
    total_w = max(r.width for r in rows)
    canvas = Image.new("RGB", (total_w, sum(r.height + 6 for r in rows)), (12, 12, 12))
    y = 0
    for r in rows:
        canvas.paste(r, (0, y))
        y += r.height + 6
    out_dir.mkdir(parents=True, exist_ok=True)
    p = out_dir / "seam_preview.jpg"
    canvas.save(p, "JPEG", quality=85)
    log("[video] 接缝预检图已生成：%s（%d 对相邻组，人眼 30 秒扫一遍 —— "
        "看交界两侧人物/服装/场景是否接得上；发现问题先修静帧再提交，省 2.7h 渲染）"
        % (p, len(pairs)))
    return p


def submit_packs(project_root: Path, shots: list[dict], stills: dict, planned: list[dict],
                 ep: int = 1, log=print, only: list[str] | None = None,
                 max_group: int | None = None) -> dict:
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
    # ★ 分组切点优先落在分镜声明的切换点（2026-09-23）：planned 里每镜的
    #   frame_plan.relation 描述它与**前镜**的关系（cut=视角/状态切换）。
    #   continuous 链尽量同组（组内多拍共享一次生成，状态连贯）；
    #   cut 处切组（组边界与叙事切换对齐，跨组衔接交给静帧链锚帧）。
    _rel = {p.get("name"): (p.get("frame_plan") or {}).get("relation")
            for p in (planned or [])}
    groups = video_plan.group_shots(shots, max_group, cut_when=_rel)
    log("[video] pack 档：%d 镜 → %d 组（max_group=%d）"
        % (len(shots), len(groups), max_group or config.VIDEO_PACK_MAX_GROUP))

    # 修法①（默认档预检）：提交前生成相邻组交界静帧并排图，人眼扫。
    # 预检图是**辅助判断**，不自动阻断——但缺静帧会在下面提交层被拦。
    _seam_preview(out_dir, groups, stills, log=log)

    pool = keypool.KeyPool.of()
    log("[video] 提交配速：%d 条 key × 间隔 %ss" % (len(pool), pool.interval_s))
    prev_last_name = None          # 跨组静帧链：上一组末镜名（提交时追加其静帧）
    for k, (g, declared) in enumerate(groups, 1):
        pname = "pack%02d" % k
        names = [s["name"] for s in g]
        total = sum(declared)
        dest = clip_dir / (pname + ".mp4")
        if _only is not None and not (set(names) & _only):
            continue                      # 补渲：只动包含目标镜的组
        if _only is None and jobs_mod.done(jobs, pname, clip_dir):
            continue                      # 幂等：组产物在盘且状态 completed
        if _only is not None:
            # 重渲该组：作废旧产物（防 jobs_mod.done 因文件在盘而跳过提交）
            if dest.exists():
                dest.unlink()
            jobs_mod.mark(jobs, pname, "pending", error="")
        rec = jobs.setdefault(pname, {"state": "pending", "attempts": 0})
        if rec.get("state") == "submitted" and rec.get("video_id"):
            log("[video] %s 续跑认领已提交任务（poll_all 接管）" % pname)
            continue
        urls = [(stills.get(n) or {}).get("url") for n in names]
        missing = [n for n, u in zip(names, urls) if not u]
        if missing:
            log("[video] %s 缺静帧：%s → 标 failed（先补静帧）" % (pname, missing))
            jobs_mod.mark(jobs, pname, "failed", error="缺静帧:" + ",".join(missing))
            jobs_mod.save(out_dir, jobs)
            continue
        # ★ 跨组静帧链（2026-09-23）：把**上一组末镜的静帧**追加到参考图末尾
        #   （Picture n+1，prompt 里声明为"上一片段结束画面"）。组与组独立生成
        #   互不知情（灯下棋实测：柳娘坐/站组间跳变、玉佩位置漂移）——这张图给
        #   模型一个状态衔接锚点。ref_max=5：组内已有 5 张时放弃追加（保组内完整）。
        prev_url = None
        if prev_last_name:
            pu = (stills.get(prev_last_name) or {}).get("url")
            if pu and pu not in urls and len(urls) < 5:
                prev_url = pu
                urls = urls + [prev_url]
        # 项目风格块（style-block / 项目 style.md）一次加载，逐组复用——
        # 2026-09-22：替换 build_pack_prompt 里硬编码的「国风古装」句（题材污染）。
        prompt = prompt_mod.build_pack_prompt(
            g, declared, total,
            style_block=style_mod.wrap(style_mod.load(project_root)),
            prev_shot_name=prev_last_name if prev_url else None)
        log("[video] %s：%s 合计 %ds，prompt=%d 字，images=%d%s"
            % (pname, "+".join(names), total, len(prompt), len(urls),
               "（含前组末镜锚帧）" if prev_url else ""))
        # 提交：队列满时**换 key 重试**（2026-09-22 改造，同 submit_all 的理由：
        # 队列满是每条通道各自的状态，跨入口换通道能绕开单池拥堵）。
        r = None
        for q_try in range(config.VIDEO_QUEUE_RETRIES + 1):
            key_idx, key = pool.claim()
            try:
                r = providers.submit_video(prompt, mode="reference",
                                           images=[u for u in urls if u],
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
                log("[video] %s 429（%s 拉黑一轮）：跳过本组，其余组继续"
                    % (pname, pool.label(key_idx)))
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
                           first_frame_kind="reference_pack",
                           shots=names, declared_seconds=declared, total_seconds=total,
                           key=key)
        jobs_mod.save(out_dir, jobs)
        log("[video] %s submitted（%d 镜打包，%ds，%s）"
            % (pname, len(names), total, pool.label(key_idx)))
        prev_last_name = names[-1]     # 下一组的状态衔接锚（无条件更新：静帧链按分镜序）
    if len(pool) > 1:
        log("[video] key 用量：%s" % pool.stats())
    jobs_mod.save(out_dir, jobs)
    return jobs


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


def poll_all(project_root: Path, jobs: dict, ep: int = 1, rounds: int = 40, log=print) -> dict:
    """轮询 + 落盘（并铺式提交的收尾）。返回 {name: local_path}。"""
    out_dir = project_root / "media" / ("ep" + str(ep))
    clip_dir = out_dir / "clips"
    clip_dir.mkdir(parents=True, exist_ok=True)
    jobs = jobs_mod.migrate(jobs or jobs_mod.load(out_dir))
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
                with httpx.Client(timeout=120, trust_env=False) as c:  # CDN 直连
                    dest.write_bytes(c.get(r["url"]).content)
                jobs_mod.mark(jobs, name, "completed", local=str(dest), error="")
                done[name] = str(dest)
                log("[video] %s done" % name)
            elif r.get("status") in ("failed", "error"):
                log("[video] %s FAILED: %s" % (name, str(r.get("error"))[:80]))
                jobs_mod.mark(jobs, name, "failed", error=str(r.get("error"))[:200])
        jobs_mod.save(out_dir, jobs)
        time.sleep(30)
    for name in jobs:
        if name not in done and jobs[name].get("state") == "submitted":
            jobs_mod.mark(jobs, name, "expired", error="轮询超窗")
    jobs_mod.save(out_dir, jobs)
    log("[video] 任务状态：%s" % jobs_mod.summary(jobs))
    return done
