# -*- coding: utf-8 -*-
"""Per-shot stills: 分镜 → 确诊静帧（带参考图）→ 这就是生产首帧。

与旧架构的根本差别：这些静帧**不是一次性质检产物**，而是喂给视频模型的
first_frame / last_frame。因此：
  - 生图必须带参考图（人物/道具身份在此锁定）
  - 静帧与其 public URL 一起持久化（视频 API 吃 URL）
  - 重生成单个静帧 = 30-60s，比"重渲一镜（65s+3min+配额）"便宜一个量级
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx

from .. import config
from . import prompt as prompt_mod
from . import providers
from . import style
from .. import vendors


def _with_still_tail(project_root: Path, shots: list[dict]) -> list[dict]:
    """逐镜注入静帧尾缀档位（pack 级；文本在 `prompt.STILL_TAIL_PRESETS`）。

    为什么在 stills 层兜底、而不是只在上游 pipeline 注入：
      `ensure()` / `ensure_tails()` 各有多个入口（首次生成 / QC 重画 / 落幅预生成），
      任何入口漏传都会让该批静帧退回默认档。守卫写在最窄处——这里没有
      第二个地方可写。`_style_block` 是上游注入的，本函数只补尾缀档位。
    """
    kind = style.still_tail_kind(project_root)
    return [{**s, "_still_tail": kind} for s in shots]


def stills_dir(project_root: Path, ep: int = 1) -> Path:
    d = project_root / "media" / ("ep" + str(ep)) / "stills"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _manifest(stills: Path) -> Path:
    return stills.parent / "stills.json"


def load(stills: Path) -> dict:
    p = _manifest(stills)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _save(stills: Path, data: dict) -> None:
    _manifest(stills).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _download(url: str, dest: Path) -> None:
    with httpx.Client(timeout=60, trust_env=False) as c:  # CDN 直连
        dest.write_bytes(c.get(url).content)


# ─── 落幅帧（尾帧图）预生成 ──────────────────────────────────────────────────
#
# 用途：把"连续镜首帧 = 上一镜真实尾帧"的串行依赖，换成"上一镜的预生成落幅图"，
# 从而让视频阶段回到平铺提交。详细取舍见 config.TAIL_PREGEN 的说明。
#
# 单独一个 manifest（`tails.json`）而不是塞进 stills.json：
# 落幅图是**可选加速产物**，不是生产首帧；混在一起会让 stills.json 的语义
# （每镜一个首帧 URL）变模糊，也容易在清理时误删首帧。

def tails_dir(project_root: Path, ep: int = 1) -> Path:
    d = project_root / "media" / ("ep" + str(ep)) / "tails"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _tails_manifest(project_root: Path, ep: int = 1) -> Path:
    return project_root / "media" / ("ep" + str(ep)) / "tails.json"


def load_tails(project_root: Path, ep: int = 1) -> dict:
    p = _tails_manifest(project_root, ep)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _save_tails(project_root: Path, ep: int, data: dict) -> None:
    _tails_manifest(project_root, ep).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def tail_needed(planned: list[dict]) -> list[str]:
    """哪些镜需要落幅图 = **被下一镜承接**的镜。

    只看 `use_prev_last`（continuous / match）：只有这类镜的尾帧会被当作
    下一镜的 first_frame。其余镜（cut）生成落幅图纯属浪费配额。
    """
    need: list[str] = []
    for i, p in enumerate(planned):
        if i + 1 >= len(planned):
            continue
        nxt = planned[i + 1].get("frame_plan") or {}
        if nxt.get("use_prev_last"):
            need.append(p.get("name"))
    return [n for n in need if n]


def _with_ref_rule(prompt: str, refs, names=None) -> str:
    """绑了参考图时，在提示词**最前面**声明"以这张图为准"；没绑就原样返回。

    ★ 2026-09-15 实测缺口（用户反馈「为什么样子都变了，不是和我给的照片完全一样的」）：
      这条声明**一直只存在于视频提示词**里（`prompt.REF_USAGE_*`，见 `build_video_prompt`），
      **静帧路径一个字都没有** —— 模型不知道那张图是什么、该不该以它为准，于是只按文字走：
      "面部棱角清楚/线条硬朗"把脸改老、"头戴@草帽"直接加帽子。
      ⇒ 补上，并把**冲突优先级**（原先完全没有的一条规则）写清。

    ★ 2026-09-19 增强：`names` 给出每张参考图对应的资产名时，**逐张点名角色**
      （官方多图合成结构 [参考图角色]+[目标场景]+[图间关系]，示例明写
      "preserves the character identity"）。不给 names 时返回历史常量，行为不变。

    位置与视频路径一致：**放最前面**（官方 reference 示例就是占位符声明打头）。

    **两处 `gen_image` 调用点共用本函数**（主静帧 + 尾缀预生成）—— 同一件事只写一份。
    """
    if not refs:
        return prompt
    return prompt_mod.still_ref_rule(names) + prompt


def ensure_tails(project_root: Path, shots: list[dict], planned: list[dict],
                 ep: int = 1, refs_by_shot: dict[str, list[str]] | None = None,
                 log=print, retries: int = 2,
                 ref_names_by_shot: dict[str, list[str]] | None = None) -> dict:
    """为"将被下一镜承接"的镜预生成落幅帧图。幂等（按文件存在跳过）。

    返回 {镜名: {"path":..., "url":...}}。
    """
    need = set(tail_needed(planned))
    if not need:
        return {}
    td = tails_dir(project_root, ep)
    data = load_tails(project_root, ep)
    plan_by_name = {p.get("name"): p for p in planned}
    changed = False
    shots_ = _with_still_tail(project_root, prompt_mod.resolve_styles(shots))
    for s in shots_:
        name = s["name"]
        if name not in need:
            continue
        cur = data.get(name) or {}
        local = td / (name + ".tail.jpg")
        if cur.get("url") and local.exists():
            continue
        refs = (refs_by_shot or {}).get(name) or []
        prompt = _with_ref_rule(
            prompt_mod.build_tail_prompt(s, plan_by_name.get(name)), refs,
            (ref_names_by_shot or {}).get(name))
        url = ""
        for attempt in range(retries + 1):
            try:
                _, url = providers.gen_image(prompt, refs=refs, ratio=config.STILL_RATIO)
                if url:
                    break
            except Exception as e:  # noqa: BLE001
                url = ""
                log("[tail] %s FAILED(%d/%d): %s"
                    % (name, attempt + 1, retries + 1, str(e)[:100]))
        if not url:
            continue
        try:
            _download(url, local)
        except Exception as e:  # noqa: BLE001 -- 落盘失败不覆盖/回滚
            log("[tail] %s download FAILED: %s" % (name, str(e)[:100]))
            continue
        data[name] = {"path": str(local), "url": url, "prompt": prompt,
                      "image_vendor": vendors.current("image")}
        changed = True
        log("[tail] %s ok" % name)
    if changed:
        _save_tails(project_root, ep, data)
    return data


def ensure(project_root: Path, shots: list[dict], refs_by_shot: dict[str, list[str]] | None = None,
           ep: int = 1, force: bool = False, log=print, retries: int = 2,
           extra: str = "", planned: list[dict] | None = None,
           ref_names_by_shot: dict[str, list[str]] | None = None) -> dict:
    """为每镜生成/复用静帧。返回 {name: {"path":..., "url":...}}。

    retries: 生图是概率性长任务（实测偶发 read timeout）——单次失败不能让
    该镜永久无首帧（否则视频阶段整镜被跳过）。失败重试，仍失败才放弃。
    extra: 追加到 prompt 的强化约束（如硬伤重生成时的强化反烧字指令）。
    planned: 镜间关系计划（用于组装提示词；缺省则按 cut 处理）。
    提示词由 prompt.build_still_prompt 组装（镜头语言+风格+内容+落幅+反烧字），
    而不是直接丢 visual 原文——旧实现浪费了景别/角度/运镜等已解析字段。
    """
    sd = stills_dir(project_root, ep)
    data = load(sd)
    changed = False
    plan_by_name = {p.get("name"): p for p in (planned or [])}
    shots = _with_still_tail(project_root, prompt_mod.resolve_styles(shots))
    for s in shots:
        name = s["name"]
        cur = data.get(name) or {}
        local = sd / (name + ".jpg")
        if (not force) and cur.get("url") and local.exists():
            continue
        # ★ **边车兜底：json 还没写但图与 url 都在盘 → 直接复用，别重烧**
        # （2026-09-13 实测事故）：`_save` 只在**整批跑完之后**调一次，而
        # `[still] LNxx ok` 每张都会立刻写 `LNxx.jpg.url` 边车 —— 于是
        # "静帧跑到一半进程被杀"会留下这种状态：**19 张 jpg + 19 个 .url 边车在盘、
        # `stills.json` 却是空的**。重启后 `cur = {}` → 幂等判据不成立 →
        # **整批重烧**（配额 + 分钟级时间全白费）。
        # clip 侧没有这个问题：`jobs_mod.save` 每次提交都落盘。
        # 判据用"图 + 边车都在盘"，与 json 的 url 语义等价（边车就是写 json 前的暂存）。
        sidecar = sd / (name + ".jpg.url")
        if (not force) and local.exists() and sidecar.exists():
            try:
                _u = sidecar.read_text(encoding="utf-8").strip()
            except Exception:  # noqa: BLE001
                _u = ""
            if _u:
                data[name] = {"path": str(local), "url": _u,
                              "seconds": s.get("seconds", 0), "prompt": "",
                              # 复用盘上静帧：**它的来源已不可考**（json 没落盘、
                              # 边车里只有 url）⇒ 显式记 UNKNOWN，不要伪造成当前厂商。
                              "image_vendor": vendors.UNKNOWN}
                changed = True
                log("[still] %s 复用盘上静帧与 url 边车（json 未落盘，免重烧）" % name)
                continue
        refs = (refs_by_shot or {}).get(name) or []
        base = _with_ref_rule(
            prompt_mod.build_still_prompt(s, plan_by_name.get(name)), refs,
            (ref_names_by_shot or {}).get(name))
        prompt = base + (extra or "")
        url = ""
        for attempt in range(retries + 1):
            try:
                _, url = providers.gen_image(prompt, refs=refs, ratio=config.STILL_RATIO)
                if url:
                    break
            except Exception as e:  # noqa: BLE001
                url = ""
                log("[still] %s FAILED(%d/%d): %s"
                    % (name, attempt + 1, retries + 1, str(e)[:100]))
        if not url:
            continue
        try:
            _download(url, local)
        except Exception as e:  # noqa: BLE001 -- 落盘失败不覆盖已有静帧
            log("[still] %s download FAILED: %s" % (name, str(e)[:100]))
            continue
        (sd / (name + ".jpg.url")).write_text(url, encoding="utf-8")
        data[name] = {"path": str(local), "url": url, "seconds": s.get("seconds", 0),
                      "prompt": prompt,
                      "image_vendor": vendors.current("image")}
        changed = True
        log("[still] %s ok" % name)
    if changed:
        _save(sd, data)
    return data
