# -*- coding: utf-8 -*-
"""video_jobs.json 的显式状态机。

旧格式是"有名有 video_id 就算提交过"的隐式约定，靠 `dest.exists()` 猜进度：
失败任务永远停在"有 video_id"状态，续跑时既不重提也不再轮询，只能靠人肉删文件。
本模块把每镜的任务生命周期写成显式状态，并且**拒绝非法跃迁**：

    pending ──提交成功──→ submitted ──轮询到 completed──→ completed
       ↑                     │                                │
       │                     ├──供应商报 failed──→ failed ─────┤
       └──────重渲───────────┴──轮询超窗──→ expired ────────────┘

- `pending`   ：尚未提交（或需要重提）
- `submitted` ：已提交，等待轮询
- `completed` ：成片已落盘（**唯一代表"这镜不用再动"的状态**）
- `failed`    ：供应商明确报错
- `expired`   ：轮询窗口内未完成（可重提，不当作失败丢弃）

只有 `completed` 且本地文件存在才算完成；其余状态续跑时会被重新推进。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .. import vendors

STATES = ("pending", "submitted", "completed", "failed", "expired")

# 合法跃迁表（值 = 允许从该状态去往的状态集合）
ALLOWED: dict[str, set[str]] = {
    "pending": {"submitted"},
    "submitted": {"completed", "failed", "expired", "pending"},
    "completed": {"pending", "submitted"},      # 重渲
    "failed": {"pending", "submitted"},         # 重提
    "expired": {"pending", "submitted"},        # 重提
}


def jobs_path(out_dir: Path) -> Path:
    return out_dir / "video_jobs.json"


def load(out_dir: Path) -> dict:
    """读取任务表，并把旧格式（隐式）迁移成显式状态机。"""
    p = jobs_path(out_dir)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return migrate(data)


def migrate(data: dict) -> dict:
    """旧记录 → 补 state 字段。幂等，可反复调用。"""
    for name, rec in list(data.items()):
        if not isinstance(rec, dict):
            data[name] = {"state": "pending", "note": str(rec)[:120]}
            continue
        if rec.get("state") in STATES:
            rec.setdefault("attempts", int(rec.get("attempts") or 0))
            continue
        rec["state"] = "submitted" if rec.get("video_id") else "pending"
        rec.setdefault("attempts", 1 if rec.get("video_id") else 0)
    return data


def save(out_dir: Path, jobs: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    jobs_path(out_dir).write_text(
        json.dumps(jobs, ensure_ascii=False, indent=2), encoding="utf-8")


def mark(jobs: dict, name: str, state: str, **fields) -> dict:
    """推进某镜状态。非法跃迁**不抛异常**（生产不能被日志格式拖垮），
    而是记录一次 `state_warnings` 并强制落到目标状态——磁盘实况优先。
    """
    if state not in STATES:
        raise ValueError("未知状态 " + str(state))
    rec = jobs.setdefault(name, {"state": "pending", "attempts": 0})
    cur = rec.get("state") or "pending"
    if cur != state and state not in ALLOWED.get(cur, set()):
        rec.setdefault("state_warnings", []).append(
            "%s→%s@%s" % (cur, state, time.strftime("%H:%M:%S")))
    rec["state"] = state
    rec["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    rec.update(fields)
    # ★ 2026-09-18：**产地记录** —— 这一镜是哪个视频厂商产的。
    #
    # 落点与下面的 `fail_history` 同理：选 `mark()` 而非 `video.py` 的 19 处调用点，
    # **一处即全覆盖**，任何调用点都不可能漏盖（同一判据绝不写两份）。
    #
    # 为什么需要：用户要在不同厂商之间试效果、**自己对比**。没有这个字段，
    # 改一次 `.env` 之后，历史产物"谁产的"就永久不可考，成片之间互相不可区分。
    #
    # 写入方式：**覆盖**（不是 setdefault）—— 重渲换了厂商时，要反映**现在的产出方**。
    rec["video_vendor"] = vendors.current("video")
    # ★ 2026-09-18：**失败留痕（append-only，永不删除）**。
    #
    # 为什么必须有（实测 laofuzi-shop 三集）：三集**各缺一镜**（LN09 / LN11 / LN18），
    # 想归因时却发现**原始失败记录已被补渲覆盖** —— 补渲成功后 `state` 变回
    # `completed`、`error` 被清空 ⇒ 只剩"每集缺一镜"这个现象，
    # **无法判断是供应商偶发、还是与提示词特征相关**（样本量归零）。
    # 本项目原则是「失败必须可见」「验收以磁盘事实为准」，而这里恰恰把事实弄丢了。
    # ⇒ 失败信息**只追加、不清除**：后续补渲成功也不动它，跨轮次累积成可统计的样本。
    #
    # 落点选 `mark()` 而非 `video.py` 的 5 处调用点 —— 那 5 处都只是 `mark failed`，
    # 改这里一处即全覆盖（同一判据绝不写两份）。
    if state in ("failed", "expired"):
        rec.setdefault("fail_history", []).append({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "state": state,
            "attempts": int(rec.get("attempts") or 0),
            "error": str(fields.get("error") or "")[:300],
        })
    return rec


def submitted(jobs: dict, name: str, video_id: str, **fields) -> dict:
    rec = jobs.setdefault(name, {"state": "pending", "attempts": 0})
    rec["attempts"] = int(rec.get("attempts") or 0) + 1
    return mark(jobs, name, "submitted", video_id=video_id, error="", **fields)


def done(jobs: dict, name: str, clip_dir: Path) -> bool:
    """完成 = 状态 completed **且** 本地成片存在（防"记录说完成、文件没了"）
    **且** 成片是**当前视频厂商**产的（2026-10-05 补的第三维）。

    ## 为什么补产地这一维

    `mark()` 从 2026-09-18 起就往每镜写 `video_vendor`，但**没有任何判据读它**。
    后果在接了本机 ComfyUI 之后才真的咬人：云端跑完 10 镜、切本地再续跑，
    旧判据只看「completed + 文件在」⇒ 那 10 镜的**云端片段**被原样拼进成片，
    而这次的名义厂商是本机 H3 ⇒ 混血片、日志全绿、没人知道哪几镜来自谁。
    本项目最贵的就是这一类「失败不可见」，所以产地**必须进判据**，
    而且只在 `done()` 这一处进（19 个调用点各查一遍迟早漏一处）。

    ## 三条边界

    · **旧记录没有 `video_vendor`**（2026-09-18 之前写下的 jobs）⇒ 产地不可考，
      按盘上事实**复用**、不报错 —— 不能因为字段缺失就把人家跑好的整集重烧。
    · 判 False 时**打一行点名**，同一镜一轮内只打一次（见 `_STALE_ORIGIN`）。
    · 判定用 `vendors.current("video")` **实时读**，不是 import 期常量 ——
      per-run 换厂商走的是子进程 env（`media/runner.py`），常量会读成启动时的值。
    """
    rec = jobs.get(name) or {}
    if rec.get("state") != "completed":
        return False
    if not (clip_dir / (name + ".mp4")).exists():
        return False
    produced = str(rec.get("video_vendor") or "").strip()
    cur = str(vendors.current("video") or "").strip()
    if produced and produced != cur:
        if name not in _STALE_ORIGIN:
            _STALE_ORIGIN[name] = produced
            print("[jobs] ⚠ %s 盘上的成片是「%s」产的，本次视频厂商是「%s」"
                  " ⇒ 不算完成，重渲" % (name, produced, cur), flush=True)
        return False
    return True


#: 本轮被判「产地不符」的镜（`done()` 写、收尾报告读，见 `stale_origin_report`）
_STALE_ORIGIN: dict[str, str] = {}


def stale_origin_report() -> dict[str, str]:
    """本轮有哪些镜因产地不符被重渲（镜名 → 旧产地）。"""
    return dict(_STALE_ORIGIN)


def reset_stale_origin() -> None:
    """清一轮计数 —— 一次媒体链开始时装，别让上一轮的名单串进来。"""
    _STALE_ORIGIN.clear()


def local_clip(clip_dir: Path, name: str) -> Path:
    return clip_dir / (name + ".mp4")


def summary(jobs: dict) -> str:
    """一行状态摘要（日志/收尾报告用）。"""
    counts: dict[str, int] = {}
    for rec in jobs.values():
        counts[rec.get("state", "?")] = counts.get(rec.get("state", "?"), 0) + 1
    return " ".join("%s=%d" % (k, counts[k]) for k in sorted(counts)) or "空"


def pending_names(jobs: dict, clip_dir: Path) -> list[str]:
    """续跑时要推进的镜（未 completed 或成片丢失）。"""
    return [n for n in jobs if not done(jobs, n, clip_dir)]
