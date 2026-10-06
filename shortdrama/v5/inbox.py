# -*- coding: utf-8 -*-
"""人 → 创作链的**入站信道**：导演信箱 + 画布改动台账。

## 为什么必须有它

`v5/hitl.py` 是**唯一**能把人的自由文字送进链路的通道，而它要求链**正挂在某个
步级门上**（`decide` 在没有 pending 时直接 400）。`webchain` 的 `manual_steps`
默认**关**（`SHORTDRAMA_WEB_MANUAL_STEPS`，见 `webchain.py:116`）⇒ 链一路跑到底时
根本没有挂起点 ⇒ **人在生产中途说的话无处可去**。

本模块补上这条信道，并顺带解决第二个缺口：`webwrite` 改分镜表**完全不碰链的
失效机制**（phases 不动、`reviewer.passed` 照样绿），所以"画布上改了、导演却不知道"。

## 两种条目（同一个文件、同一套投递语义）

  · `message` —— 人在对话框里打的字。
  · `edit`    —— 画布/网页改分镜表时由 `webwrite` **自动**记的账（带镜号、列名、
                 改前→改后）。

## 投递规则（★ 两条不一样，因为它们的"作废"条件不一样）

`message`：**一次派发消费一次**。投给下一个匹配的角色，记 `delivered_to`。
  · `to=""` = 谁先被派发就给谁；`to="scenedesigner"` = 只给那个角色，别人跳过。
  · 为什么不广播给全部角色：一条"把 LN03 的道具换成走马灯"被 `worldbuilder`
    读走就再也到不了分镜师，而广播会让每个角色都重复处理同一句话。
    要广播就重发一次 —— 显式、可解释，比隐式行为好。

`edit`：**跟着那张表活，表一重写就作废**。只投给拥有分镜表的两个角色
  （`scenedesigner` / `reviewer`），且每次比对 `sb_fp`（表文本指纹）：
  指纹变了 ⇒ 说明分镜师已经重写过整张表，人改的那几行**已经不在盘上了**，
  继续注入会让它去"修正"一个已经不存在的差异 ⇒ 当场剪掉。
  ⚠️ 不做"消费一次"：改了三镜，这三镜的账在表被重写前必须**每次**都在场，
  否则 reviewer 那一轮看不到、下一轮 scenedesigner 又看不到。

## 三条纪律

1. **不新增第二判据**：本模块只搬运文字，不判断人对不对；门照旧在 `guards`。
2. **绝不碰黑名单**：不写 `.agent_state.json`、不动 `video_quota.json`。
   作废素材由调用方（`webwrite`）用它自己的原语做。
3. **读不到就当没有**：任何异常都降级为"不注入"，绝不让一条信道把创作链弄死
   （`role_input` 在 dev server 进程里跑，抛出去 = 整条链 rc≠0）。
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path

DIR_NAME = ".tmp/inbox"
_FILE = "inbox.json"
_LOCK = ".lock"

#: 一条 `message` 允许的最长文字（超出截断并在注入里标明）。
#: 信箱会随连载越积越长，而 `role_input` 拼起来的注入总量才是真 token 预算
#: （AGENTS.md「brief 规范」第 1 条：brief 侧无截断，所以预算必须管在这里）。
MAX_TEXT = 1500

#: 单次派发最多注入几条 message（再多说明人在刷屏，挤掉上游产物不值）。
MAX_PENDING_MESSAGES = 12

#: 只有这两个角色拥有分镜表 —— 改动台账只投给它们（见模块文档）。
TABLE_OWNERS = ("scenedesigner", "reviewer")

#: `to` 允许的角色（含 `director` = supervisor 自己，走 HITL 那条通道时用到）。
KNOWN_TARGETS = ("", "director", "worldbuilder", "assetdesigner", "plotdesigner",
                 "scriptwriter", "dialogue", "scenedesigner", "reviewer")

KINDS = ("message", "edit")


def dir_of(root: Path) -> Path:
    d = Path(root) / DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def path_of(root: Path) -> Path:
    return dir_of(root) / _FILE


# ─────────────────────────────────────────────────────────── 锁与读写

class _Locked:
    """跨进程互斥（mkdir 在 POSIX 与 Windows 上都是原子的）。

    ★ 为什么必须锁：`append` 跑在 **shim 进程**（HTTP 写入），
      `take_for_role` 跑在 **dev server 进程**（`roles.role_input`），
      两个进程读改同一个 JSON ⇒ 不加锁会丢写。
    拿不到锁时**降级为不阻塞**（照旧读写，最坏丢一条信箱），
    ⛔ 绝不让一条辅助信道把创作链卡死。
    """

    def __init__(self, root: Path, timeout: float = 3.0):
        self.root = Path(root)
        self.timeout = timeout
        self.handle = None

    def __enter__(self):
        d = dir_of(self.root)
        lock = d / _LOCK
        deadline = time.time() + self.timeout
        while True:
            try:
                self.handle = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                # 陈旧锁：持有者崩了没删。超过 10 秒就抢。
                try:
                    if time.time() - lock.stat().st_mtime > 10:
                        lock.unlink()
                        continue
                except OSError:
                    pass
                if time.time() >= deadline:
                    return self
                time.sleep(0.05)
            except OSError:
                return self

    def __exit__(self, *exc):
        if self.handle is not None:
            try:
                os.close(self.handle)
                (dir_of(self.root) / _LOCK).unlink()
            except OSError:
                pass
            self.handle = None
        return False


def _read(root: Path) -> dict:
    try:
        obj = json.loads(path_of(root).read_text(encoding="utf-8"))
        if isinstance(obj, dict) and isinstance(obj.get("items"), list):
            obj["items"] = [x for x in obj["items"] if isinstance(x, dict)]
            return obj
    except Exception:  # noqa: BLE001 —— 文件不存在/半截 JSON 一律当空信箱
        pass
    return {"seq": 0, "items": []}


def _write(root: Path, obj: dict) -> None:
    p = path_of(root)
    tmp = p.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(str(tmp), str(p))
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


# ─────────────────────────────────────────────────────────── 分镜表指纹

def storyboard_fingerprint(root: Path, ep: int) -> str:
    """本集分镜表**当前文本**的短指纹（`edit` 条目的作废判据）。

    读不到表返回 `""` —— 调用方按"没有可比对的表"处理，不抛。
    """
    from . import guards  # 局部导入：避免 roles → inbox → guards → roles 成环

    try:
        p = guards.resolve_path(Path(root), "scenedesigner", ep=int(ep))
        if not p.exists():
            return ""
        return hashlib.sha1(p.read_bytes()).hexdigest()[:12]
    except Exception:  # noqa: BLE001
        return ""


# ─────────────────────────────────────────────────────────── 写入

def append(root: Path, kind: str, text: str, ep: int = 1,
           by: str = "", to: str = "", meta: dict | None = None) -> dict:
    """记一条入站条目并返回它。`kind` ∈ `KINDS`。

    `to` 只允许 `KNOWN_TARGETS`（空串 = 谁先派发谁收）。非法值**响亮报错**，
    ⛔ 不静默降级成空串 —— 那会让一条本来定向的话被随便一个角色吃掉。
    """
    if kind not in KINDS:
        raise ValueError("未知信箱条目类型：%r（可用：%s）" % (kind, "、".join(KINDS)))
    if to not in KNOWN_TARGETS:
        raise ValueError("未知的投递目标：%r（可用：%s）"
                         % (to, "、".join(x or "(任意角色)" for x in KNOWN_TARGETS)))
    body = str(text or "").strip()
    if not body:
        raise ValueError("信箱内容不能为空")
    clipped = len(body) > MAX_TEXT
    if clipped:
        body = body[:MAX_TEXT]

    rec = {"id": 0, "kind": kind, "ep": int(ep), "text": body,
           "by": str(by or "")[:40], "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "to": to, "clipped": clipped,
           "delivered_to": "", "delivered_at": "",
           "meta": meta or {}}
    if kind == "edit":
        rec["sb_fp"] = str((meta or {}).get("sb_fp") or storyboard_fingerprint(root, ep))

    with _Locked(root):
        obj = _read(root)
        rec["id"] = int(obj.get("seq") or 0) + 1
        obj["seq"] = rec["id"]
        obj["items"].append(rec)
        _write(root, obj)
    return rec


def append_message(root: Path, text: str, ep: int = 1, by: str = "",
                   to: str = "") -> dict:
    return append(root, "message", text, ep=ep, by=by, to=to)


def append_edit(root: Path, shot: str, field: str, old: str, new: str,
                ep: int = 1, action: str = "edit", by: str = "",
                source: str = "canvas", position: int | None = None) -> dict:
    """`webwrite` 改表时调用 —— 把"人改过哪一格"记成一条 `edit`。

    `action` ∈ edit / add / delete。文本写成**人能看懂、模型能对上镜号**的形状。

    ⚠️ `delete` 必须带 `position`：镜名 `LNxx` 是 `storyboard.parse` 按**行序**编的，
    删掉中间一镜后**后面每一镜的名字全体前移**（`renumber` 因此要重挂素材）。
    所以"删了 LN03"这句话在删完之后指的是**另一镜** —— 只有位置不会说谎。
    """
    label = {"edit": "改了", "add": "新增了", "delete": "删除了"}.get(action, "改了")
    src = "画布" if source == "canvas" else "网页"
    if action == "delete":
        text = "【人工编辑·%s】第 %s 集：人在表里**删除了第 %s 位那一镜**（删除时它叫 %s）。" % (
            src, ep, position if position is not None else "?", shot)
    elif action == "add":
        text = "【人工编辑·%s】第 %s 集 %s：人%s一镜（%s = %s）。" % (
            src, ep, shot, label, field, str(new)[:120])
    else:
        text = "【人工编辑·%s】第 %s 集 %s 的「%s」列：人把它从「%s」改成「%s」。" % (
            src, ep, shot, field, str(old)[:80], str(new)[:200])
    meta = {"shot": shot, "field": field, "action": action,
            "source": source, "old": str(old)[:200], "new": str(new)[:400]}
    if position is not None:
        meta["position"] = int(position)
    return append(root, "edit", text, ep=ep, by=by, to="", meta=meta)


# ─────────────────────────────────────────────────────────── 读取

def items(root: Path, ep: int | None = None, kind: str = "") -> list[dict]:
    out = []
    for x in _read(root)["items"]:
        if kind and x.get("kind") != kind:
            continue
        if ep is not None and int(x.get("ep") or 0) not in (int(ep), 0):
            continue
        out.append(dict(x))
    return out


def history(root: Path, ep: int | None = None, limit: int = 80) -> list[dict]:
    """给对话框回显用的近期条目（含已投递的），按 id 升序、取最后 `limit` 条。"""
    xs = items(root, ep=ep, kind="message")
    return xs[-int(limit):]


def pending_messages(root: Path, ep: int | None = None) -> list[dict]:
    return [x for x in items(root, ep=ep, kind="message") if not x.get("delivered_to")]


def live_edits(root: Path, ep: int) -> list[dict]:
    """仍然有效的改动台账（表指纹没变的那些）。"""
    fp = storyboard_fingerprint(root, ep)
    out = []
    for x in items(root, ep=ep, kind="edit"):
        if not fp or x.get("sb_fp") == fp:
            out.append(x)
    return out


def prune_stale_edits(root: Path, ep: int) -> int:
    """表被重写后把作废的 `edit` 条目清掉，返回删了几条。"""
    fp = storyboard_fingerprint(root, ep)
    if not fp:
        return 0
    doomed = {x["id"] for x in items(root, ep=ep, kind="edit")
              if x.get("sb_fp") and x.get("sb_fp") != fp}
    if not doomed:
        return 0
    with _Locked(root):
        obj = _read(root)
        before = len(obj["items"])
        obj["items"] = [x for x in obj["items"] if x.get("id") not in doomed]
        _write(root, obj)
    return before - len(obj["items"])


# ─────────────────────────────────────────────────────────── 投递

def take_for_role(root: Path, role: str, ep: int) -> list[dict]:
    """取本次派发该角色**应当看到**的条目，并把 message 标成已投递。

    返回顺序：先改动台账（那是盘上事实，角色必须知道），后信箱消息。
    """
    ep = int(ep or 1)
    out: list[dict] = []
    if role in TABLE_OWNERS:
        prune_stale_edits(root, ep)
        out.extend(live_edits(root, ep))

    hits = []
    for x in pending_messages(root, ep=ep):
        to = str(x.get("to") or "")
        if to and to != role and to != "director":
            # 定向给别的角色的，跳过（⛔ 不许被它吃掉）
            continue
        hits.append(x)
    hits = hits[:MAX_PENDING_MESSAGES]
    if hits:
        ids = {int(x["id"]) for x in hits}
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        with _Locked(root):
            obj = _read(root)
            for x in obj["items"]:
                if x.get("id") in ids and not x.get("delivered_to"):
                    x["delivered_to"] = role
                    x["delivered_at"] = stamp
            _write(root, obj)
        for x in hits:
            x["delivered_to"] = role
        out.extend(hits)
    return out


def render_block(root: Path, role: str, ep: int) -> str:
    """`roles.role_input` 的注入块。没有条目时返回**空串**（一行都不加）。

    ⚠️ 措辞纪律：这里只**陈述事实**（人改了什么、人说了什么），
    ⛔ 不写"不要/必须"这类指令 —— 指令归 brief 与包契约，信箱归人。
    """
    try:
        got = take_for_role(root, role, ep)
    except Exception:  # noqa: BLE001 —— 信道故障绝不能升级成链故障
        return ""
    if not got:
        return ""
    edits = [x for x in got if x.get("kind") == "edit"]
    msgs = [x for x in got if x.get("kind") == "message"]
    lines = ["\n【人工入站 —— 生产过程中人从工作室界面送进来的东西】"]
    if edits:
        lines.append("· 分镜表已被**人手工改动** %d 处（画布/网页）。这些是盘上事实，"
                     "以表里的当前内容为准，⛔ 不要改回去、也不要「顺手优化」：" % len(edits))
        for x in edits[:24]:
            lines.append("  - " + str(x.get("text") or ""))
        if len(edits) > 24:
            lines.append("  - …另有 %d 处同类改动" % (len(edits) - 24))
    if msgs:
        lines.append("· 人留下的话（按送进来的先后）：")
        for x in msgs:
            who = str(x.get("by") or "").strip()
            tag = ("[%s %s%s]" % (x.get("kind"), x.get("at") or "",
                                  " " + who if who else ""))
            lines.append("  - %s %s" % (tag, str(x.get("text") or "")))
    return "\n".join(lines)


def status(root: Path, ep: int | None = None) -> dict:
    """给前端显示的信箱状态。"""
    all_items = items(root, ep=ep)
    msgs = [x for x in all_items if x.get("kind") == "message"]
    return {
        "messages": len(msgs),
        "pending": sum(1 for x in msgs if not x.get("delivered_to")),
        "edits_live": len(live_edits(root, ep)) if ep else 0,
        "edits_total": sum(1 for x in all_items if x.get("kind") == "edit"),
    }
