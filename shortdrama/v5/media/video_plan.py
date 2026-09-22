# -*- coding: utf-8 -*-
"""视频生成的**模式决策收敛点**（单一真相源）。

【为什么需要它 —— 2026-09-13 的实测教训】
  把 `VIDEO_MODE` 从 `keyframe` 切到 `reference` 时，有 **3 处消费点漏改**：
    · `video.submit_all` 没传 mode → 平铺路径静默退回 keyframe（同一部片两种模式）；
    · `video.extract_last_frame` 三处仍在抽帧 → 抽出来没人消费，白解码；
    · `pipeline._parallel_ok` 的判据没考虑模式 → reference 下仍然串行提交（白等 75%）。
  根因不是"粗心"，而是**一个模式被拆成了散落各处的 `if mode ==`** ——
  改一处、漏两处是必然的。
  本模块把"这个模式下：用什么图 / 走哪条提交路径 / 要不要抽尾帧 / 能不能平铺 /
  提示词要不要写占位符声明"**一次算清**，消费点只读字段，不再各自判断。

【两种模式的事实依据（官方文档，不是我们的取舍）】
  · `keyframe`：必需 `first_frame`/`last_frame` 至少一个，**不允许** `images`/`audios`
  · `reference`：必需 `images`/`audios` 至少一类，**不允许** `first_frame`/`last_frame`
  两者**互斥** —— 所以"用哪种图"一个决定就决定了其余全部行为，这正是可以收敛的原因。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .. import config

#: 支持的四种模式（`text` 我们不使用：短剧必须有画面控制）。
#: `mixed`（2026-09-20，osmanthus-vow 三镜对照实验后立项）：**逐镜**在
#: reference / keyframe 之间选择 —— 承接=continuous/match 的镜走 keyframe
#: （首帧=上一镜落幅图、尾帧=本镜静帧，接戏），cut 镜走 reference（自由运镜 + 角色参考图）。
#: 官方文档明确"每个 endpoint 调用独立，同一项目可逐次换工作流"。
#: `pack`（2026-09-22，12s 打包法落管线）：相邻**同场景**镜打包成一条 ≤12s 的
#: reference 请求（每镜一张静帧、逐拍 `<Picture i>` 点名+时间边界）——接戏从
#: "跨请求问题"变成"单请求内部问题"。本质仍是 reference（图用法相同、无首帧锁定），
#: 差别只在**提交粒度**：一个 job = 一组镜（详见 `group_shots`）。
MODES = ("reference", "keyframe", "mixed", "pack")

# ─── pack 档：压缩式分组算法（自 scripts/pack_render.py 搬入，判据逐字节一致）──
# 旁路脚本（pack_render.py）保留为独立验证入口；两处判据若有改动必须同步。

PACK_MAX_SECONDS = 12    # 单条请求硬上限（供应商 seconds ∈ [4, 12]）
PACK_MIN_KEEP_RATIO = 0.6  # 压缩后每镜至少保留原声明的 60%


def pack_clamp_sec(s: dict) -> int:
    """分镜声明时长：解析失败按 4s 兜底，硬区间 4-12。"""
    v = int(s.get("seconds") or 0) or 4
    return max(4, min(12, v))


def pack_speech_need(s: dict) -> int:
    """镜最短可行秒数：台词语音（5 字/秒）+ 1s 余量；无声镜 2s 起步、下限 4s。"""
    chars = len(re.sub(r"[^一-龥]", "", s.get("dialogue") or ""))
    need = chars / 5.0 + 1.0 if chars else 2.0
    return max(4, math.ceil(need))


def _pack_fit(declared: list[int], mins: list[int]) -> list[int] | None:
    """把 declared 等比压到 ≤12s；保每镜 ≥mins 且 ≥60% 原声明。失败返回 None。"""
    total = sum(declared)
    if total <= PACK_MAX_SECONDS:
        return declared
    scaled = [d * PACK_MAX_SECONDS / total for d in declared]
    out = [max(m, round(x)) for x, m in zip(scaled, mins)]
    while sum(out) > PACK_MAX_SECONDS:
        idx = max(range(len(out)), key=lambda k: out[k] - mins[k])
        if out[idx] - 1 < mins[idx] or out[idx] - 1 < declared[idx] * PACK_MIN_KEEP_RATIO:
            return None
        out[idx] -= 1
    if any(o < declared[idx] * PACK_MIN_KEEP_RATIO for idx, o in enumerate(out)):
        return None
    return out if sum(out) <= PACK_MAX_SECONDS else None


def group_shots(shots: list[dict], max_group: int | None = None) -> list[tuple[list[dict], list[int]]]:
    """pack 档分组：**同场景**相邻镜贪心合并，返回 [(镜列表, 每镜分配秒)]。

    规则（v2 压缩式，与旁路脚本实测闭环版本一致）：
    - 仅同场景相邻镜合并（跨场景切换是分镜语义，不交给模型即兴）；
    - 声明时长之和 ≤12s 直接合并；
    - 超限时等比压缩到 12s，但每镜不得低于 `pack_speech_need`（台词时长下限），
      且压幅不得超原声明 40% —— 否则放弃合并、该镜独立成组。
    """
    mg = int(max_group or config.VIDEO_PACK_MAX_GROUP)
    groups: list[tuple[list[dict], list[int]]] = []
    i, n = 0, len(shots)
    while i < n:
        cur = [shots[i]]
        declared = [pack_clamp_sec(shots[i])]
        while len(cur) < mg and i + len(cur) < n:
            nxt = shots[i + len(cur)]
            sc_cur = (cur[-1].get("scene") or "").strip()
            sc_nxt = (nxt.get("scene") or "").strip()
            if not sc_cur or sc_cur != sc_nxt:
                break
            trial_d = declared + [pack_clamp_sec(nxt)]
            mins = [pack_speech_need(s) for s in cur + [nxt]]
            fitted = _pack_fit(trial_d, mins)
            if sum(trial_d) <= PACK_MAX_SECONDS:
                cur.append(nxt)
                declared = trial_d
                continue
            if fitted:
                cur.append(nxt)
                declared = fitted
                break        # 压缩组 12s 已满，不再吞镜
            break
        groups.append((cur, declared))
        i += len(cur)
    return groups


@dataclass(frozen=True)
class VideoPlan:
    """一次视频生成的模式决策。**所有 `mode` 相关的判断只在这里做一次。**"""

    mode: str
    # ── 图怎么给（两者互斥，由 mode 唯一决定）──
    use_images: bool           # 静帧进 `images` 数组（reference）
    use_keyframes: bool        # 传 `first_frame` / `last_frame`（keyframe）
    # ── 渲完之后 ──
    needs_tail_extract: bool   # 要不要抽真实尾帧（只有 keyframe 会消费它）
    # ── 提交方式 ──
    can_submit_flat: bool      # 各镜互相独立 → 可平铺提交（提交与等待解耦）
    # ── 提示词 ──
    announce_picture: bool     # 是否写 `<Picture 1>` 用途声明（reference 要求）

    @classmethod
    def of(cls, mode: str | None = None, *,
           still_chain: bool | None = None,
           tail_pregen: bool | None = None,
           n_tails: int = 0,
           n_need: int = 0) -> "VideoPlan":
        """唯一的构造入口。

        `mode` 缺省读 `config.VIDEO_MODE`；`still_chain` / `tail_pregen` 缺省读配置
        （后两者只在 keyframe 下参与"能否平铺"的判断）。
        """
        m = (mode or config.VIDEO_MODE or "reference").strip().lower()
        if m not in MODES:
            m = "reference"        # 未知模式回落 reference（与 config 默认一致）

        if m == "reference":
            # reference 不允许 first_frame → 各镜之间**没有任何可承接的依赖**
            # → 平铺是唯一合理选择（串行只是白等；40 镜时差 4 倍）。
            return cls(mode=m, use_images=True, use_keyframes=False,
                       needs_tail_extract=False, can_submit_flat=True,
                       announce_picture=True)

        if m == "pack":
            # 打包档（2026-09-22）：本质是 reference（静帧进 images、无首帧锁定），
            # 差别只在提交粒度 —— 一个 job = 一组同场景相邻镜（≤12s）。
            # 组与组之间互不依赖 ⇒ 照样平铺（提交与等待解耦）。
            # announce_picture=False：打包 prompt 由 `prompt.build_pack_prompt`
            # 自行做 `<Picture i>` 逐拍声明，不走单镜六段式组装器。
            return cls(mode=m, use_images=True, use_keyframes=False,
                       needs_tail_extract=False, can_submit_flat=True,
                       announce_picture=False)

        if m == "mixed":
            # 逐镜决策（见 `mode_for`）。串行依赖全靠"预生成落幅图"解除 ⇒ 必须平铺；
            # 不抽真实尾帧（那是 submit_chain 的机制，mixed 不走链式）。
            return cls(mode=m, use_images=True, use_keyframes=True,
                       needs_tail_extract=False, can_submit_flat=True,
                       announce_picture=False)

        # keyframe：各镜可能靠"上一镜真实尾帧"承接 → 平铺取决于依赖是否已解除。
        sc = config.STILL_CHAIN if still_chain is None else still_chain
        tp = config.TAIL_PREGEN if tail_pregen is None else tail_pregen
        flat = (not sc) or (bool(tp) and n_tails >= n_need)
        return cls(mode=m, use_images=False, use_keyframes=True,
                   needs_tail_extract=True, can_submit_flat=flat,
                   announce_picture=False)

    def wants_prev_tail(self, relation: str, use_prev_last: bool,
                        has_prev_tail: bool) -> bool:
        """**本镜**要不要用上一镜的真实尾帧当首帧（keyframe 专用）。

        这是"本镜决策"而非"模式决策"，所以留在方法里：只有 keyframe 且
        本镜关系是连续/匹配、且上一镜尾帧确实在手时，才成立。
        reference 下恒 False（不允许 first_frame）。
        """
        if not self.use_keyframes:
            return False
        return bool(use_prev_last) and bool(has_prev_tail)

    def mode_for(self, relation: str) -> str:
        """mixed 的**逐镜**决策：被承接关系连住的镜走 keyframe（帧锁接戏），
        其余走 reference（自由运镜 + 角色参考图）。非 mixed 模式恒返回全局 mode。

        ⚠️ 正反打镜走 keyframe 的前提是 first_frame=**本镜静帧**（画面里必须有
        本镜该出现的人）；"上一镜尾帧当首帧"只适用于**同主体连续镜**——
        把只出现甲的尾帧交给该拍乙的镜，乙没有图像依据 ⇒ 凭空摇第三人
        （osmanthus-vow LN17 实测事故）。所以 mixed 的 keyframe 镜优先
        首帧=上一镜**落幅图**（分镜落幅列预生成，含本镜主体的构图），缺省回本镜静帧。
        """
        if self.mode != "mixed":
            return self.mode
        return "keyframe" if relation in ("continuous", "match") else "reference"
