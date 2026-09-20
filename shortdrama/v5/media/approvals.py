# -*- coding: utf-8 -*-
"""人工审批门（人在环的**显式**机制）。

**为什么需要它**（2026-09-10 决策）：
    在此之前，"人工验收"只是一种**纪律**：`--stills-only` 出完静帧停下、
    人看过之后设 `SHORTDRAMA_STILL_QC=0` 继续——注释里写着"静帧已人工验收"，
    但**没有任何证据**能说明是谁、在什么时候、依据哪一版产物批准的。
    纪律不是架构：换个人跑、隔天再跑、外部 Agent 代跑，这条链就断了。

本模块把它变成显式状态：审批写入 `media/ep<N>/approvals.json`，
每条记录谁批的、何时、备注、以及**当时产物的指纹**。

**关键设计：审批带指纹，上游一变即作废。**
    如果只记一个 `approved: true`，那它很快会退化成橡皮图章——静帧被重画了
    十轮，审批还挂着。所以每条审批都记下**当时产物的内容哈希**，
    `is_approved()` 每次重新计算并比对；不一致即视为**未批准**，
    必须重新走一遍人审。这让"批准"永远对应一个**具体版本**。

三道门的位置：
    storyboard  分镜定稿   → 指纹 = scenedesigner.md      （拦"分镜还没定稿就烧配额"）
    stills      静帧验收   → 指纹 = stills.json           （拦"静帧有缺陷就进视频"）
    media       成片放行   → 指纹 = stills.json + 分镜      （拦"没验收就拼接出片"）

注意：**默认不启用**（`config.REQUIRE_APPROVAL=0`）。
启用后 `check()` 返回 False 会阻断对应阶段——这是有意的强制力。
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

GATES = ("storyboard", "stills", "media")

# 每道门要盯住哪些产物。指纹 = 这些文件内容的 sha256 前缀，任何一个变了都算"产物已变"。
# 每项可以是**角色名**（走 `guards.resolve_path`，兼容 M1 的集级路径与历史旧路径）
# 或**含 {ep} 的相对路径**。
_GATE_SOURCES: dict[str, tuple[str, ...]] = {
    "storyboard": ("scenedesigner",),
    "stills": ("media/ep{ep}/stills.json",),
    # 成片放行盯的是"静帧 + 分镜"——视频阶段吃这两样；分镜改了同样要重批。
    "media": ("media/ep{ep}/stills.json", "scenedesigner"),
}


def _resolve_source(project_root: Path, spec: str, ep: int) -> Path:
    """把 `_GATE_SOURCES` 里的一项解析成路径。

    ⚠️ **审批门的指纹源必须与产物路径同源**：分镜在 M1 后是**集级**的
    （`scenedesigner_ep{N}.md`）。若这里仍硬编旧名 → **指纹算错 → 上游产物变了批文却没作废**
    —— 那正是审批门要防的事。故走 `guards.resolve_path`（新名优先、旧名回退）。
    """
    from .. import guards
    if spec in guards.OUTPUTS:
        return guards.resolve_path(project_root, spec, ep)
    return project_root / spec.format(ep=ep)


def _fingerprint(project_root: Path, gate: str, ep: int) -> str:
    """当前产物的指纹（缺失的文件记为 missing，不参与哈希）。"""
    h = hashlib.sha256()
    for rel in _GATE_SOURCES.get(gate, ()):
        p = _resolve_source(project_root, rel, ep)
        h.update(rel.encode("utf-8"))
        if p.exists():
            try:
                h.update(p.read_bytes())
            except Exception:  # noqa: BLE001
                h.update(b"<unreadable>")
        else:
            h.update(b"<missing>")
    return h.hexdigest()[:16]


def fingerprint(project_root: Path, gate: str, ep: int = 1) -> str:
    """公开入口：某道门所盯产物的当前指纹。

    除审批以外还有一处用途（2026-09-10）：`pipeline.run` 用它把
    `media_loop.rendered` 从**单向闩锁**变成**版本感知闸门**——
    输入（静帧/分镜）一变，指纹就变，于是"已渲染无需重渲"自动失效。
    """
    return _fingerprint(project_root, gate, ep)


def path(project_root: Path, ep: int = 1) -> Path:
    return project_root / "media" / ("ep" + str(ep)) / "approvals.json"


def load(project_root: Path, ep: int = 1) -> dict:
    p = path(project_root, ep)
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _save(project_root: Path, ep: int, data: dict) -> None:
    p = path(project_root, ep)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def approve(project_root: Path, gate: str, *, by: str = "", note: str = "",
            evidence: dict | None = None, ep: int = 1, now: str | None = None) -> dict:
    """批准某道门。记录批准人、时间、备注与**当时产物的指纹**。

    `by` 不要留空：审批的全部意义就是"谁为这一步负责"。空值时记为 `<unattributed>`，
    不阻断（工具不该替人做身份管理），但会在 `summary()` 里显眼地暴露出来。
    """
    if gate not in GATES:
        raise ValueError("未知审批门：%s（可选：%s）" % (gate, ", ".join(GATES)))
    data = load(project_root, ep)
    rec = {
        "approved": True,
        "by": (by or "").strip() or "<unattributed>",
        "at": now or time.strftime("%Y-%m-%dT%H:%M:%S"),
        "note": note or "",
        "fingerprint": _fingerprint(project_root, gate, ep),
    }
    if evidence:
        rec["evidence"] = dict(evidence)
    data[gate] = rec
    _save(project_root, ep, data)
    return rec


def revoke(project_root: Path, gate: str, ep: int = 1, *, by: str = "",
           note: str = "") -> None:
    """撤销某道门（保留一条撤销记录，不静默抹掉历史）。"""
    data = load(project_root, ep)
    data[gate] = {"approved": False, "by": by or "", "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                  "note": note or "revoked"}
    _save(project_root, ep, data)


def check(project_root: Path, gate: str, ep: int = 1) -> tuple[bool, str]:
    """返回 (是否已批准, 原因)。**产物指纹不一致 = 视为未批准。**

    这是本模块最重要的一条：审批只对**当时那一版产物**有效。
    """
    rec = load(project_root, ep).get(gate) or {}
    if not rec.get("approved"):
        return False, "未批准"
    cur = _fingerprint(project_root, gate, ep)
    if rec.get("fingerprint") != cur:
        return False, ("产物已变更（批准时 %s，现在 %s）→ 审批作废，需重新验收"
                       % (rec.get("fingerprint"), cur))
    return True, "已批准（%s @ %s）" % (rec.get("by"), rec.get("at"))


def is_approved(project_root: Path, gate: str, ep: int = 1) -> bool:
    return check(project_root, gate, ep)[0]


def summary(project_root: Path, ep: int = 1) -> str:
    """一行摘要（日志/收尾报告用）。"""
    parts = []
    for g in GATES:
        ok, why = check(project_root, g, ep)
        parts.append("%s=%s" % (g, "OK" if ok else "-"))
        if not ok and why != "未批准":
            parts.append("(%s)" % why[:40])
    data = load(project_root, ep)
    unattributed = [g for g, r in data.items()
                    if isinstance(r, dict) and r.get("approved")
                    and r.get("by") == "<unattributed>"]
    if unattributed:
        parts.append("⚠️ 未署名：%s" % ",".join(unattributed))
    return " ".join(parts)
