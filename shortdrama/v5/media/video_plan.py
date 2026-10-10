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

from .. import config, validate
from . import storyboard


def pack_transport(group: list[dict], relation: str, same_cast: bool,
                   *, has_prop_reference: bool = False) -> str:
    """Lock a continuation only when doing so does not discard a prop reference.

    Keyframe accepts only its starting frame, not the registered asset images.
    A small or occluded prop in that frame cannot substitute for its shape reference.
    """
    return ('keyframe' if len(group) == 1 and not storyboard.cut_offsets(group[0].get('visual') or '')
            and not has_prop_reference
            and same_cast and relation in ('continuous', 'match')
            and not any(validate._has_line(s.get('dialogue') or '') for s in group)
            else 'reference')

#: 支持的四种模式（`text` 我们不使用：短剧必须有画面控制）。
#: `mixed`（2026-09-20，osmanthus-vow 三镜对照实验后立项）：**逐镜**在
#: reference / keyframe 之间选择 —— 承接=continuous/match 的镜走 keyframe
#: （首帧=上一镜落幅图、尾帧=本镜静帧，接戏），cut 镜走 reference（自由运镜 + 角色参考图）。
#: 官方文档明确"每个 endpoint 调用独立，同一项目可逐次换工作流"。
#: `pack`（2026-09-22，12s 打包法落管线）：相邻**同场景**镜打包成一条 ≤12s 的
#: reference 请求（每镜一张静帧、逐拍 `<Picture i>` 点名+时间边界）——接戏从
#: "跨请求问题"变成"单请求内部问题"。本质仍是 reference（图用法相同、无首帧锁定），
#: 差别只在**提交粒度**：一个 job = 一组镜（详见 `group_shots`）。
#:
#: ★ 这份名单是 `config.VIDEO_MODES` 的**别名**而非第二份真相（2026-10-04）——
#:   此前本模块自己写了一份，两份可以各自漂移。现在 config 是唯一判定点
#:   （含未知档位的响亮告警），这里只做兼容导出。
MODES = config.VIDEO_MODES

# ─── pack 档：压缩式分组算法（自 scripts/pack_render.py 搬入，判据逐字节一致）──
# 旁路脚本（pack_render.py）保留为独立验证入口；两处判据若有改动必须同步。

PACK_MAX_SECONDS = 12    # 单条请求硬上限（供应商 seconds ∈ [4, 12]）
PACK_MIN_KEEP_RATIO = 0.6  # 压缩后每镜至少保留原声明的 60%
# ★ 2026-10-05：**取消每镜下限**（原 2s）。单位换成「场」之后，供应商 [4,12] 管的是
#   **一条请求**（= 一场），镜内秒数只是提示词里的时间边界，不该再有地板。
#   09-25 那次「4→2」的放权仍嫌保守：用户实测**单条 clip 内的时间分段可精确到零点几秒**，
#   快切正反打（1 秒甚至 0.5 秒一镜）正是短剧的节奏手段，被地板卡住等于扼杀 brief 要的东西。
#   唯一保留的补时是**单镜成组补到 4s**（供应商请求级下限，见 `group_shots`）——
#   那是一条请求发不出去的硬拒，不是审美。
PACK_MIN_SHOT_SECONDS = 0.0

# ─── 一次请求能带几张图、按什么优先级带（**单一真相源**）─────────────────────
#   为什么做成常量而不是散在 `video.pack_ref_images` 的字面量里：
#   这些数字 2026-10-02 起还要交给**创作链的分镜师看**（`roles._container_facts`
#   把它写进"出片容器事实"注入）。写在文案里再抄一份 = 必然漂移
#   ——同一批文档里"静帧取最后一拍"就是这么漂了 6 天的（见
#   `tests_roles.TestStillBeatClaimMatchesPipeline`）。
#: 供应商 `images` 数组上限：**一条请求最多 5 张参考图**（官方硬约束）。
REF_SLOTS = 5
#: pack 档槽位优先级里「人物定妆照」的张数上限 ⇒ **三人同镜时第三人没有表**
#: （`video.pack_ref_images`；这是实测值，不是审美偏好）。
PACK_REF_MAX_CHARS = 2
#: pack 档「场景空镜」的张数上限（多绑会互相拉扯地貌）。
PACK_REF_MAX_LOCS = 1
#: **逐镜档**（`VIDEO_REF_SOURCE=sheets`）一条请求最多喂几张资产图。
#: 3 不是审美选择，是实测上限：10-06 桥上决斗在视频通道量过
#: 「图的张数 > 分镜要求的人数 ⇒ 多画一个人」（5 张画三人、3 张全对）。
#: 10-07 两轮实跑（命案 15 镜 / 仙侠 6 镜）都在 3 张这一档，没出现多画。
SHEET_SLOTS = 3


def _sec(v) -> float:
    """分镜声明的秒数 → float（**保留小数**，0.5 秒一镜是合法写法）。"""
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def pack_clamp_sec(s: dict) -> float:
    """分镜声明时长：解析失败按 4s 兜底，上限 12；**不设下限**（2026-10-05 场口径）。

    旧写法 `int(s["seconds"])` 会把 0.5 秒直接截成 0 → 再被"或 4"兜成 4 秒，
    于是**快切镜在分组阶段被悄悄拉长**，与分镜声明的时间边界不符。
    """
    v = _sec(s.get("seconds")) or 4.0
    return min(float(PACK_MAX_SECONDS), v)


def pack_speech_need(s: dict) -> float:
    """台词镜的最短可行秒数（5 字/秒 + 1s 余量）；**无声镜给 0，不再兜 2 秒**。

    台词那条是物理（把一句话说完要那么多时间），保留；无声镜的 2 秒是审美，删掉。
    """
    chars = len(re.sub(r"[^一-龥]", "", s.get("dialogue") or ""))
    return round(chars / 5.0 + 1.0, 2) if chars else 0.0


def _pack_fit(declared: list[float], mins: list[float]) -> list[float] | None:
    """把 declared 等比压到 ≤12s；保每镜 ≥mins 且 ≥60% 原声明。失败返回 None。

    ★ 2026-10-05 改成**小数**运算：原来 `round(x)` + 步进 1 秒，会把 0.5 秒的快切镜
    一压就弹回 1 秒 —— 分镜声明的节拍边界与实际下单秒数脱节（同型病：
    `storyboard.parse` 的 `int(float(...))` 截小数）。
    """
    total = sum(declared)
    if total <= PACK_MAX_SECONDS:
        return declared
    scaled = [d * PACK_MAX_SECONDS / total for d in declared]
    out = [round(max(m, x), 2) for x, m in zip(scaled, mins)]
    while sum(out) > PACK_MAX_SECONDS + 1e-9:
        idx = max(range(len(out)), key=lambda k: out[k] - mins[k])
        if (out[idx] - 0.1 < mins[idx]
                or out[idx] - 0.1 < declared[idx] * PACK_MIN_KEEP_RATIO):
            return None
        out[idx] = round(out[idx] - 0.1, 2)
    if any(o < d * PACK_MIN_KEEP_RATIO - 1e-9 for o, d in zip(out, declared)):
        return None
    return out if sum(out) <= PACK_MAX_SECONDS + 1e-9 else None


def same_unit(a: dict, b: dict) -> bool:
    """两镜能不能并进**同一条请求**：有「场次」就按场次，没有才退回场景名。

    为什么不能只看场景名（2026-10-05）：场景名只是地点。同一个地点的第 1 场和
    第 3 场是两段不同的戏（时间不同、剧情不同），旧判据会把它们并成一条 ——
    madfate-abc-1005 实测：15 镜只有「验尸房 / 天台水塔间」两个值，而剧本写了 4 场，
    分镜层根本没有"场"这个容器，切出来的组与场无关。
    """
    aa = int(a.get("act") or 0)
    bb = int(b.get("act") or 0)
    if aa and bb:
        return aa == bb
    sc = (a.get("scene") or "").strip()
    return bool(sc) and sc == (b.get("scene") or "").strip()


def group_shots(shots: list[dict], max_group: int | None = None) -> list[tuple[list[dict], list[int]]]:
    """pack 档分组：**同场景**相邻镜贪心合并，返回 [(镜列表, 每镜分配秒)]。

    规则（v2 压缩式，与旁路脚本实测闭环版本一致）：
    - 仅**同一场**的相邻镜合并（有「场次」列按场次，没有才按场景名，见 `same_unit`）；
    - 声明时长之和 ≤12s 直接合并；
    - 超限时等比压缩到 12s，但每镜不得低于 `pack_speech_need`（台词时长下限），
      且压幅不得超原声明 40% —— 否则放弃合并、该镜独立成组。
    - ★ 单镜成组补到 ≥4s（2026-09-25）：供应商请求级 seconds ∈ [4,12]，
      组内每拍可以 2s，但**独立成组的镜**整条请求就是它自己 → 不足 4s 会直接
      被供应商拒（组内两拍 2s+2s=4s 合法，单拍 2s 不合法）。

    ⚠️ 2026-09-23 撤销记录：曾试过「分镜声明 cut 的镜不并入本组」（组边界对齐
    叙事切换点）——**实测错误**：写实主包的分镜把普通镜头切换都标 cut（几乎
    每两镜一个），20 镜被切成 18 组（每组 1-2 镜），合并省时的意义全失。
    "镜间关系 cut" ≠ "状态切换"，正反打对切也标 cut——组切分只看场景与时长。
    """
    mg = int(max_group or config.VIDEO_PACK_MAX_GROUP)
    groups: list[tuple[list[dict], list[int]]] = []
    i, n = 0, len(shots)
    while i < n:
        cur = [shots[i]]
        declared = [pack_clamp_sec(shots[i])]
        while len(cur) < mg and i + len(cur) < n:
            nxt = shots[i + len(cur)]
            if not same_unit(cur[-1], nxt):
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
        if len(cur) == 1 and declared[0] < 4:
            declared[0] = 4     # 单镜成组：请求级供应商下限
        groups.append((cur, declared))
        i += len(cur)
    return groups


@dataclass(frozen=True)
class VideoPlan:
    """一次视频生成的模式决策。**所有 `mode` 相关的判断只在这里做一次。**"""

    mode: str
    # ── 图怎么给（两者互斥，由 mode 唯一决定）──
    use_images: bool           # 走 `images` 数组（reference/pack/mixed 的 reference 镜）
                               # 里面装的是资产图还是静帧，看下面的 `from_sheets`
    use_keyframes: bool        # 传 `first_frame` / `last_frame`（keyframe）
    # ── 渲完之后 ──
    needs_tail_extract: bool   # 要不要抽真实尾帧（只有 keyframe 会消费它）
    # ── 提交方式 ──
    can_submit_flat: bool      # 各镜互相独立 → 可平铺提交（提交与等待解耦）
    # ── 提示词 ──
    announce_picture: bool     # 是否写 `<Picture 1>` 用途声明（reference 要求）
    # ── 图的来源（2026-10-07 新默认，判据见 `config.VIDEO_REF_SOURCE`）──
    from_sheets: bool = False  # True ⇒ 逐镜喂资产图（定妆照/场景/道具），⛔不喂静帧
    # ★ 这一条是上面那条的**必要约束**，不是同义词：`from_sheets` 说的是"逐镜请求吃什么"，
    #   `needs_stills` 说的是"这一档**离不离得开**静帧"。两者在 pack / mixed 上**不相等**：
    #     · pack —— `video.pack_ref_images` 仍把「本组首镜静帧」当场景实现、
    #       `seam_anchor` 抽不到成片末帧时退回「前组末镜静帧」（09-28 实测：完全去掉静帧，
    #       同一处场景在相邻两组里长成两种样子）。
    #     · mixed —— 被承接关系连住的镜走 keyframe，`first_frame` 就是本镜静帧
    #       （`video.py:432`）；只有 cut 镜走 reference。
    #   媒体链"要不要画静帧"只看这一条。看错了的后果是**整档出不了片**：
    #   静帧不画 → mixed 的承接镜拿到 `first=None` → 逐镜被判"无首帧，跳过"。
    needs_stills: bool = True  # True ⇒ 这一档仍要静帧，媒体链不得跳过绘制

    @classmethod
    def of(cls, mode: str | None = None, *,
           still_chain: bool | None = None,
           tail_pregen: bool | None = None,
           n_tails: int = 0,
           n_need: int = 0) -> "VideoPlan":
        """唯一的构造入口。

        `mode` 缺省读 `config.VIDEO_MODE`；`still_chain` / `tail_pregen` 缺省读配置
        （后两者只在 keyframe 下参与"能否平铺"的判断）。

        ⚠️ 显式传入的未知 `mode` 也**不静默**：`config` 只在自己 import 时校验环境变量，
        这里收到的是调用点（如 `probe_*` 脚本、测试）塞进来的值，同样必须喊一声 ——
        静默回落 reference 会让「我以为在跑打包档」变成逐镜档，产物节奏完全不同。
        """
        m = (mode or config.VIDEO_MODE or config.VIDEO_MODE_DEFAULT).strip().lower()
        if m not in MODES:
            print("[video_plan] ⚠️ 未知视频档位 %r（合法值：%s）⇒ 回落到系统默认档 %r"
                  % (mode, "/".join(MODES), config.VIDEO_MODE_DEFAULT))
            m = config.VIDEO_MODE_DEFAULT

        sheets = (config.VIDEO_REF_SOURCE == "sheets")
        if m == "reference":
            # reference 不允许 first_frame → 各镜之间**没有任何可承接的依赖**
            # → 平铺是唯一合理选择（串行只是白等；40 镜时差 4 倍）。
            return cls(mode=m, use_images=True, use_keyframes=False,
                       needs_tail_extract=False, can_submit_flat=True,
                       announce_picture=True, from_sheets=sheets,
                       needs_stills=not sheets)

        if m == "pack":
            # 打包档（2026-09-22）：本质是 reference（静帧进 images、无首帧锁定），
            # 差别只在提交粒度 —— 一个 job = 一组同场景相邻镜（≤12s）。
            # 组与组之间互不依赖 ⇒ 照样平铺（提交与等待解耦）。
            # announce_picture=False：打包 prompt 由 `prompt.build_pack_prompt`
            # 自行做 `<Picture i>` 逐拍声明，不走单镜六段式组装器。
            # pack 的图序由 `video.pack_ref_images` 自己定（设定表→场景→首镜静帧→
            # 前组末帧→道具），**不看 `VIDEO_REF_SOURCE`** —— 它本来就同时吃设定表和静帧。
            return cls(mode=m, use_images=True, use_keyframes=False,
                       needs_tail_extract=False, can_submit_flat=True,
                       announce_picture=False, from_sheets=True,
                       needs_stills=False)

        if m == "mixed":
            # 逐镜决策（见 `mode_for`）。串行依赖全靠"预生成落幅图"解除 ⇒ 必须平铺；
            # 不抽真实尾帧（那是 submit_chain 的机制，mixed 不走链式）。
            return cls(mode=m, use_images=True, use_keyframes=True,
                       needs_tail_extract=False, can_submit_flat=True,
                       announce_picture=False, from_sheets=sheets,
                       needs_stills=True)

        # keyframe：各镜可能靠"上一镜真实尾帧"承接 → 平铺取决于依赖是否已解除。
        sc = config.STILL_CHAIN if still_chain is None else still_chain
        tp = config.TAIL_PREGEN if tail_pregen is None else tail_pregen
        flat = (not sc) or (bool(tp) and n_tails >= n_need)
        return cls(mode=m, use_images=False, use_keyframes=True,
                   needs_tail_extract=True, can_submit_flat=flat,
                   announce_picture=False)

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
