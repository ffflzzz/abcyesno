# -*- coding: utf-8 -*-
"""资产图出片前的**查字闸门**（2026-10-07，配合 `config.VIDEO_REF_SOURCE=sheets`）。

为什么必须有它：视频请求改成直接喂资产图之后，**静帧那一步的反烧字清洗与硬伤质检
整段被绕过了**。10-07 实测把这条因果钉死了：

  · `madfate-abc-1005-nostill`：场景卡「天台水塔间」自己带一排红字招牌 ⇒ 直接喂视频后
    成片 ≥4 镜背景原样出现整排可读汉字。提示词末尾那句 `no on-screen text` **压不过图**
    （本项目铁律：负面提法反而诱发）。
  · `xianxia-zhongzhui-1007-nostill`：八张资产图逐张人眼查过无字 ⇒ 60 秒成片一帧无字。

⇒ 这条臂的红利完全取决于"喂出去的图干不干净"。以前这道检查长在静帧上，现在搬到图上，
  而且搬的是**唯一真正进请求的那批图**。

分工照本仓库既有纪律：**模型只报观察，判不判由代码定**（见 `qc.is_hard_issue`）。
模型抄不出逐字原文的一律不算带字 —— 白底设定表上"疑似有字"就拦图，代价是每张定妆照
都进不了请求。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .. import config
from . import assets as assets_mod
from . import qc

_SEEN = "sheet_text_seen.json"

#: 文字类的实际措辞键（同 `qc.COUNT_ISSUE_KEYS` 的教训：**按 QC 真会写出的句子取**，
#: 不是我以为它会怎么写）。加键必须连反向对照一起加，见 `tests_sheettext`。
TEXT_KEYS = ("文字", "字符", "字幕", "招牌", "匾额", "铭文", "刻字", "门牌",
             "标语", "题字", "字样")
#: ★ `qc.NEG_CONTEXT` **不含**「无字幕 / 无文字」这类写法：静帧通道上 QC 不会把"没问题"
#:   单独报成 issue，而资产图这条通道上「画面干净，无字幕、无水印」确实会以 P0 条目的
#:   形式回来（10-07 写反向对照测试时撞上）。只在本轴补一条窄否定表，
#:   ⛔ 不动 `qc.NEG_CONTEXT` —— 那张表被静帧 QC 共用，往里加"干净"这类泛词会把真报告
#:   一起压掉（本项目有劝退措辞压掉真报的前例）。
TEXT_NEG = ("无字幕", "无文字", "无可见文字", "没有文字", "未见文字", "不含文字",
            "不出现文字", "无任何文字")


def _seen_path(root: Path, ep) -> Path:
    return root / "media" / ("ep%d" % (ep or 1)) / _SEEN


def load_seen(root: Path, ep) -> dict:
    p = _seen_path(root, ep)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:                                    # noqa: BLE001
        return {}


def save_seen(root: Path, ep, data: dict) -> None:
    p = _seen_path(root, ep)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def _fp(path: Path) -> str:
    st = path.stat()
    return "%d:%d" % (int(st.st_mtime), st.st_size)


def has_text_issue(issues: list) -> tuple[bool, str]:
    """QC 报回来的条目里有没有**真·带字**。返回 `(带字, 依据句)`。

    只认过了 `qc.is_hard_issue` 三道闸门的条目（P0 + 非否定语境 + 命中词表），
    再要求句子里出现文字类键 —— 与静帧通道同一份判据，不另起一套。
    """
    for i in (issues or []):
        if not isinstance(i, dict) or not qc.is_hard_issue(i):
            continue
        desc = str(i.get("desc") or "")
        if any(n in desc for n in TEXT_NEG):
            continue
        if any(k in desc for k in TEXT_KEYS):
            return True, desc[:120]
    return False, ""


def scan(root: Path, names: list[str], ep=None, log=print, images: dict | None = None):
    """扫这批资产图有没有可读文字。返回 `{资产名: {"text": bool, "why": str}}`。

    幂等：按**文件指纹**（mtime+size）缓存，图没换就不重复问模型。
     扫描失败（无视觉额度/模型格式怪）不阻断 —— 那种情况按"没查出字"处理并**响亮**
      打一行：闸门失效必须是看得见的，不能变成"日志全绿但其实没查"。
    """
    seen = load_seen(root, ep)
    out: dict[str, dict] = {}
    todo = []
    for nm in names:
        p = (images or {}).get(nm) or assets_mod.local_ref_path(root, nm)
        if not p or not Path(p).exists():
            out[nm] = {"text": False, "why": "无本地文件可查"}
            continue
        fp = _fp(Path(p))
        rec = seen.get(nm) or {}
        if rec.get("fp") == fp:
            out[nm] = {"text": bool(rec.get("text")), "why": rec.get("why") or "",
                       "cached": True}
            continue
        todo.append((nm, str(p), fp))
    if not todo:
        return out
    if not config.SHEET_TEXT_GATE:
        log("[sheettext] ⚠️ 闸门已关（SHORTDRAMA_SHEET_TEXT_GATE=0）⇒ %d 张要喂给视频的"
            "资产图**没有查过有没有字**。带字的场景卡会原样进成片（10-07 天台实测）。"
            % len(todo))
        return out
    log("[sheettext] 查 %d 张资产图有没有可读文字（视觉模型，按文件指纹缓存）" % len(todo))
    for nm, path, fp in todo:
        try:
            r = qc.review(path, on_screen_text="forbid")
            hit, why = has_text_issue(r.get("issues") or [])
        except Exception as e:                           # noqa: BLE001
            log("[sheettext] ⚠️ %s 查不了（%s）⇒ 按不带字放行，这一张是**没查过**的"
                % (nm, str(e)[:60]))
            hit, why = False, "查询失败"
        out[nm] = {"text": hit, "why": why}
        seen[nm] = {"fp": fp, "text": hit, "why": why,
                    "at": time.strftime("%Y-%m-%d %H:%M:%S")}
        log("[sheettext]   %s：%s%s" % (nm, "带字 ⛔" if hit else "干净",
                                        ("｜" + why) if (hit and why) else ""))
    save_seen(root, ep, seen)
    return out


def flagged_urls(root: Path, ep=None, log=print) -> dict:
    """注册表里**带可读文字**的那些图 → `{url: 资产名}`。视频提交前拿它筛掉。

    一次查全表（≤ 十几张）而不是逐镜查：同一张定妆照会被几十镜复用，逐镜查就是
    几十次视觉调用。缓存按文件指纹，图没换不再问模型。
    """
    reg = assets_mod.auto_sync(root)
    names, url_of = [], {}
    for a in reg.get("assets", []):
        nm = str(a.get("name") or "").strip()
        u = assets_mod._resolve_one(a, root)
        if not nm or not u:
            continue
        names.append(nm)
        url_of.setdefault(nm, u)
    res = scan(root, names, ep=ep, log=log)
    bad = {url_of[nm]: nm for nm, r in res.items() if r.get("text")}
    if bad:
        log("[sheettext] ⛔ %d 张资产图带可读文字，本次**不进视频请求**：%s"
            % (len(bad), "、".join(sorted(bad.values()))))
    return bad
