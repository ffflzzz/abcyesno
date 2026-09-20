# -*- coding: utf-8 -*-
"""Shot relations → first-frame source.

新架构的"镜间关系"层：镜头不是孤立生成的，首尾帧由分镜之间的关系决定。

关系类型：
  continuous  同幕内连续动作 → 首帧承接上一镜的尾帧（保持空间/人物/光照连续）
  match       匹配剪辑（同一构图/主体不同尺度）→ 首帧沿用上一镜首帧的构图锚
  cut         跳切/换场 → 独立首帧（自己的静帧）

判定是**确定性的**（读分镜的幕/场景标记），不交给模型——平台纪律。
"""
from __future__ import annotations

import re

# 新脚手架：## 第1幕｜纸扎铺-日｜S3 / 6s
# 旧格式兼容：## S1 / 6s（无幕场信息 → 只能判 cut）
_HEAD_RE = re.compile(r"^#+\s*(.*?)\s*/\s*\d")
_ACT_RE = re.compile(r"第\s*([一二三四五六七八九十\d]+)\s*幕")
_SCENE_SPLIT = re.compile(r"[｜|]")


def _act_of(heading: str) -> str | None:
    m = _ACT_RE.search(heading or "")
    return m.group(1) if m else None


def _scene_of(heading: str) -> str:
    """从标题里取场景名：'第1幕｜纸扎铺-日｜S3 / 6s' → '纸扎铺-日'。"""
    m = _HEAD_RE.match((heading or "").strip())
    core = (m.group(1) if m else (heading or "")).strip()
    parts = [p.strip() for p in _SCENE_SPLIT.split(core) if p.strip()]
    # 去掉开头的"第N幕"与结尾的 "S3"
    parts = [p for p in parts if not _ACT_RE.match(p) and not re.fullmatch(r"S\d+", p)]
    return parts[0] if parts else core


def plan_frames(shots: list[dict]) -> list[dict]:
    """为每镜决定首帧来源，返回带 frame_plan 的新列表（不改动输入）。

    每镜 frame_plan:
      {"relation": continuous|match|cut, "use_prev_last": bool,
       "use_prev_first": bool, "own_still": bool}
    """
    out = []
    prev: dict | None = None
    for s in shots:
        heading = s.get("heading") or ""
        scene = _scene_of(heading)
        act = _act_of(heading)
        rel = "cut"
        if prev is not None:
            same_act = act is not None and act == prev.get("act")
            same_scene = scene and scene == prev.get("scene")
            if same_act and same_scene:
                rel = "continuous"
            elif same_act:
                rel = "match"
            else:
                # 标题兜底（2026-09-20，osmanthus-vow 实测）：国风分镜写
                # `## 第N场：老桂花树下 · 日（秋雨）`，无「第N幕」⇒ 标题判据全灭、
                # 整片误判 cut（承接列明明写了却没人读）。改用**表列**判：
                # 本镜「场景」列与上一镜相同 且「承接」列非空 ⇒ continuous。
                # 跨场的承接句（"承接上一镜…树下落点"但换了场景）**不**升级——
                # 那属于叙事呼应，画面空间已变，锁帧反而出错。
                col_scene = (s.get("scene") or "").strip()
                col_join = (s.get("join_note") or "").strip()
                if col_scene and col_join and col_scene == (prev.get("scene_col") or ""):
                    rel = "continuous"
        # 承接锚点：优先上一镜的落幅（截图范本「承接上一镜…的落点」），
        # 没有落幅时退到上一镜的场景，保证承接句可读。
        anchor = ""
        if prev is not None and rel in ("continuous", "match"):
            anchor = (prev.get("tail") or "").strip() or (prev.get("scene") or "").strip()
        out.append({
            **s,
            "act": act,
            "scene": scene,
            "scene_col": (s.get("scene") or "").strip(),   # 表列原值（`scene` 会被标题派生覆写）
            "prev_tail_note": anchor,
            "frame_plan": {
                "relation": rel,
                # 连续动作：首帧承接上一镜尾帧（上一镜尾帧缺省则用其静帧）
                "use_prev_last": rel in ("continuous", "match"),
                "use_prev_first": rel == "match",
                "own_still": rel == "cut",
            },
        })
        prev = out[-1]
    return out


def explain(planned: list[dict]) -> str:
    """人读的关系说明（进流/回执，便于排查）。"""
    parts = []
    for s in planned:
        parts.append("%s:%s" % (s.get("name"), s.get("frame_plan", {}).get("relation")))
    return " ".join(parts)
