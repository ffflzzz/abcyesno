# -*- coding: utf-8 -*-
"""创作链的**步级 HITL 信道**（Human-in-the-loop）。

【它是什么】
  `create_deep_agent(interrupt_on={"task": True})` 开启后，supervisor 每次调用
  `task` 派发子代理**之前**会 `interrupt` —— 图挂起，等人给决策再继续。
  规范出处：`langchain-dev-guide / middleware.md` Issue 3
  （resume 值是**复数数组** `{"decisions": [{"type": "approve"}]}`）。

【与媒体链三道审批门的区别 —— 别混】
  · `media/approvals.py`：**阶段之间**的门（storyboard / stills / media），
    检查产物是否合格才放行，带产物指纹、上游一变审批自动作废。
  · 本模块：**图运行中**的 interrupt，粒度是"每一步派发之前"。

【三种决定（2026-09-18 扩到三种）】
  · `approve` —— 继续派下一步。
  · `reject`  —— **中止整条链**（链路侧 break，不 resume）。
  · `redo`    —— **打回**：重跑"上一步角色 + 它的全部下游"。
    链路侧先 `guards.reset_from()`（清本集 phases + 旧产物移 `.rerun_backup/`），
    再把决定翻成一条带 `message` 的 **`reject`** resume 回同一 thread
    —— 中间件会**跳过这次工具执行**并把消息交给模型（见
    `langchain/agents/middleware/human_in_the_loop.py:337-354`），
    于是导演收到"用户打回了 X，请只重派 X 并补齐下游"这条指令。

【为什么决定要带"戳"（stamp）】
  没有戳时：一个残留的 `decision.json`（上次等待超时 / 进程被杀留下）会在
  **下一次挂起时被立刻消费** ⇒ **静默跳过一次人工审核**，日志上也看不出来。
  所以 `pending.json` 带 `stamp`、决定带回同一个戳、`read_decision(stamp=…)` 校验；
  `decide()` 还额外拒绝"没有挂起时写决定"。**宁可让人重新点一次，也不静默放行。**

【为什么用文件做信道，而不是交互式 input】
  链路跑在**后台**（`scripts/run_project.sh` 起的进程，stdout 重定向到日志），
  没有终端可交互；而批准动作由**另一个进程**（CLI）发起。
  两个进程之间最简单的可靠信道就是项目下两个文件：

      .tmp/hitl/pending.json    链路侧写 —— stamp / thread_id / run_id /
                                已完成角色 / prev_role / next_role
      .tmp/hitl/decision.json   人工侧写 —— decision(approve|reject|redo) /
                                stamp / target(仅 redo) / by / note

  链路轮询到 decision.json 就 resume，并**删掉它**（一次决定只消费一次）。

【为什么 pending 里不带 interrupt 的载荷】
  `action_requests` 需要额外调 `threads.get_state()` 才拿得到；而"停在哪一步"
  从**产物目录**就能直接看出来（`done_roles` → `next_role`）—— 简单且够用。
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from .guards import PREREQ

DIR_NAME = ".tmp/hitl"
_PENDING = "pending.json"
_DECISION = "decision.json"

#: 允许的决定类型（与 `Command(resume={"decisions":[{"type": ...}]})` 对应）。
#:
#: `approve` / `reject` 直接映射到中间件的同名决定；**`redo` 是我们自己的语义**
#: （"打回上一步，重跑它和它的下游"），链路侧把它翻成一条**带消息的 `reject`** ——
#: 实测 `.venv/.../langchain/agents/middleware/human_in_the_loop.py:337-354`：
#: `reject` 会**跳过工具执行**并把 `message` 交回模型，正是"打回"需要的形态。
#: （所以 `redo` 不需要自己造一套中断机制，也不用 `edit`。）
#: `edit` 仍未暴露 —— 在"派发子代理"这个语义下没有意义。
DECISIONS = ("approve", "reject", "redo")

#: `redo` 决定里"打回哪个角色"的字段名
TARGET_KEY = "target"


#: 「制作规格」那个角色（`director/director.md`）。
#: ★ 2026-09-19：它**不是被派发的角色**（supervisor 自己写），但它是**整条链的输入**
#:   —— 片长 / 画幅 / 音频模式 / 视觉基准都锁在那份文档里。所以它：
#:     · 出现在「刚产出的是谁」里（第一停时人看到的就是它）；
#:     · **可以被打回**（规格错了，打回任何下游角色都救不回来）。
DIRECTOR = "director"


def redo_targets_of(root: Path, done_roles) -> list[str]:
    """允许打回的目标 = `director`（**规格文档在盘才给**）+ 已完成的那几位角色。

    ## 为什么把 director 也放进来（2026-09-19）

    链路**第一停**发生在「派发 worldbuilder 之前」，那一刻人刚看到的就是
    `director/director.md`。在此之前它**既不能确认、也不能打回** ——
    人只能一路往下走，等到 reviewer 之后才发现"规格本身就错了"，
    而那时打回任何单个角色都救不回来（它们是照那份规格写的）。

    ⛔ 判据是**文件在不在盘**，不是"阶段表里有没有"：`director` 不是被派发的角色，
      `phases["director"]` 永远不存在（见 `guards.GATE_ROLES` 的说明）。
      文件不在 ⇒ 不给这个选项（**不假装**它已完成）。
    """
    out: list[str] = []
    try:
        from .guards import OUTPUTS
        rel = str(OUTPUTS.get(DIRECTOR) or "")
        if rel and (Path(root) / rel).exists():
            out.append(DIRECTOR)
    except Exception:            # noqa: BLE001 —— 读不到就别给这个选项，不阻断
        pass
    out.extend(str(x) for x in (done_roles or []))
    return out


def prev_role_of(done_roles) -> str:
    """"用户刚看到的那个产物"属于哪个角色 = 已完成角色里**按依赖序最靠后**的那个。

    ★ 2026-09-19：**一个都没完成时返回 `director`** —— 链路的第一停就发生在这个
      时刻，而人刚看到的是 supervisor 写下的 `director/director.md`（制作规格）。
      旧实现返回空串 ⇒ 前端确认条显示"刚产出：—"，且**没有默认打回目标**
      （`decide` 的 `redo` 默认值取自它）⇒ 明明规格写错了却挑不出可打回的对象。

    ⚠️ 必须按 `PREREQ`（**7 个键**、不含 `director`）排序，**不能**按传入列表的顺序，
    也**不能**用 `guards.ROLES`（**8 个**、`director` 排第一）—— 两者顺序不同，
    混用会算错"上一步是谁" ⇒ 打回打错人，而**下游角色无权改上游文件**
    ⇒ 那一轮整轮白跑（本项目已有实测：误填下游白烧 13 分钟）。
    """
    done = {str(x) for x in (done_roles or [])}
    last = ""
    for r in PREREQ:                      # = v5.guards.PREREQ（依赖序，7 个）
        if r in done:
            last = r
    return last or DIRECTOR


def dir_of(root: Path) -> Path:
    d = root / DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def pending_path(root: Path) -> Path:
    return dir_of(root) / _PENDING


def decision_path(root: Path) -> Path:
    return dir_of(root) / _DECISION


def _read_json(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def record_pending(root: Path, *, thread_id: str, run_id: str,
                   done_roles: list | None = None, note: str = "") -> Path:
    """链路侧：图已挂起 → 写下待批准信息（含**下一个待派发的角色**与**上一个角色**）。

    两个 2026-09-18 新增的字段：

    · `prev_role` —— "用户刚刚看到的那个产物"属于谁。前端据此显示
      "刚审的是 X"，并把它当 `redo` 的**默认目标**（`prev_role_of`）。
    · `stamp` —— **这一次挂起的唯一标识**。决定必须带回同一个戳才被认账，否则
      一个残留的 `decision.json`（上次等待超时 / 进程被杀留下）会在**下一次挂起时
      被立刻消费** ⇒ **静默跳过一次人工审核**，而日志上看不出来。
      这是本项目最贵的一类 bug；宁可让用户重新点一次，也不能静默放行。
    """
    done = [str(x) for x in (done_roles or [])]
    nxt = next((r for r in PREREQ if r not in done), "")
    data = {
        "state": "pending",
        "stamp": uuid.uuid4().hex[:8],
        "thread_id": thread_id,
        "run_id": run_id,
        "done_roles": done,
        "prev_role": prev_role_of(done),
        # ★ 2026-09-19：**允许打回的目标随挂起一起落盘**（含 director）。
        #   为什么写进文件、而不是让 `decide` 与前端各自算一遍：
        #   "谁能被打回"是一条判据，写两处必然漂移（本项目最忌的一类）。
        #   `decide` 读它做校验、`webmap.hitl_state` 读它给前端下拉 —— 同一份。
        "redo_targets": redo_targets_of(root, done),
        "next_role": nxt,
        "note": note,
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    p = pending_path(root)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def read_pending(root: Path) -> dict | None:
    return _read_json(pending_path(root))


def read_decision(root: Path, stamp: str = "") -> dict | None:
    """链路侧：有没有人来批。非法/缺失返回 None（不抛，免得链路因脏文件崩）。

    `stamp` 非空时**校验时效**：戳与当前挂起不一致的决定**不算数**。
    调用方（`drive_chain`）**必须据此响亮告警**，不能静默丢弃 ——
    "你的决定被忽略了"这件事必须让人看见，否则用户会一直以为自己在等链路，
    而链路在等一个永远不来的（有效）决定。
    """
    d = _read_json(decision_path(root))
    if not d:
        return None
    if str(d.get("decision") or "").strip().lower() not in DECISIONS:
        return None
    if stamp and str(d.get("stamp") or "") != str(stamp):
        return None
    return d


def clear_decision(root: Path) -> None:
    """消费掉决定 —— 一次决定只 resume 一次。"""
    try:
        decision_path(root).unlink()
    except FileNotFoundError:
        pass


def clear_pending(root: Path) -> None:
    """**决定已被消费、链路正在往下跑** ⇒ 撤掉挂起标记。

    ## 为什么必须在消费那一刻就清（2026-09-18 验收实测的坑）

    `pending.json` 只在**下次挂起时**才被覆盖（`record_pending`）。所以从
    "决定被消费"到"下一次挂起"之间（**这段是链路的执行期，分钟级**），
    磁盘上一直留着**上一次的** pending ⇒ 前端读到 `pending=true`（还是旧 stamp）
    ⇒ **弹出一条过期的"等你确认"**。用户点了"继续"，而我的戳校验会正确地忽略它
    （决定是给上一步的）—— **安全，但人完全不知道为什么点了没反应**。

    ⇒ 清掉它：执行期理应"没有待批"，这才是真相。
    """
    try:
        pending_path(root).unlink()
    except FileNotFoundError:
        pass


def decide(root: Path, decision: str, *, by: str = "", note: str = "",
           target: str = "", stamp: str = "") -> Path:
    """CLI / 前端侧：写下决定。链路轮询到就会 resume。

    ## 三条校验（防止"静默生效"，不是防"权限"）

    1. **必须真有挂起**。没有挂起时写下的决定会一直躺在盘上，等**下一次**挂起时被
       立刻消费 ⇒ 静默跳过一次人工审核。宁可在这里拒绝。
       （这是**新增**的校验：旧实现无条件写盘。CLI 与 HTTP 都经这里，判据只写一份。）
    2. **`redo` 必须有合法目标** —— 目标只能取自 `pending.done_roles`（本步之前
       已完成的角色）。填一个没做过的角色 = 打回一个不存在的产物。
    3. **带戳** —— 写入当前挂起的 `stamp`，让链路能判断"这个决定是不是给这一步的"。
       调用方自带 `stamp` 时以它为准（前端从 `GET /hitl` 取到，能防"取完状态后
       链路又往前走了一步"）。
    """
    dec = (decision or "").strip().lower()
    if dec not in DECISIONS:
        raise ValueError("decision 只能是 %s，收到 %r" % ("/".join(DECISIONS), decision))

    pd = read_pending(root) or {}
    if not pd:
        # ⚠️ 这句话会**原样显示给用户**（400 → 前端的 message → toast）
        #    ⇒ 不许出现 markdown 标记（`**`，会照字面显示出来）。
        raise ValueError(
            "当前没有等待批准的一步，不能写决定（链路未挂起 / 未开启步级 HITL）。"
            "写下去会被下一次挂起当作本次的决定立刻消费 —— 等于静默跳过"
            "一次人工审核，故拒绝。")

    tgt = ""
    if dec == "redo":
        # ★ 2026-09-19：**优先读挂起时写下的 `redo_targets`**（它含 director）。
        #   旧 pending 文件没有这个字段 ⇒ 回落 `done_roles`（行为与改造前一致）。
        allowed = [str(x) for x in (pd.get("redo_targets")
                                    or pd.get("done_roles") or [])]
        want = str(target or "").strip() or str(pd.get("prev_role") or "").strip()
        if want not in allowed:
            # 同上：这句会原样显示给用户 ⇒ 不带 markdown 标记
            raise ValueError(
                "打回目标 %r 无效：只能打回「本步之前已完成」的角色（%s）"
                % (want or "(空)", "、".join(allowed) or "无"))
        tgt = want

    data = {"decision": dec, "by": by, "note": note,
            "stamp": str(stamp or pd.get("stamp") or ""),
            "at": time.strftime("%Y-%m-%d %H:%M:%S")}
    if tgt:
        data[TARGET_KEY] = tgt
    p = decision_path(root)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def status(root: Path) -> str:
    """人类可读的一行状态（CLI 用）。

    ★ 有决定文件但**戳不对**时必须**说出来**（不能假装没看见）：那意味着
      "你的批准不生效"，而你不知道这件事就会一直干等。
    """
    pd = read_pending(root)
    if not pd:
        # 三种可能都要说出来 —— 只说"没开 HITL"会把人引到错的方向
        return ("无待批准（链路未开启步级 HITL / 尚未跑到第一个 interrupt / "
                "上一次的决定已被消费、链路正在往下跑）")
    tid = str(pd.get("thread_id") or "-")
    line = "[%s] thread=%s… 已完成 %d/%d，下一个待派发：%s（可打回：%s）" % (
        pd.get("state") or "?", tid[:8],
        len(pd.get("done_roles") or []), len(PREREQ),
        pd.get("next_role") or "—", pd.get("prev_role") or "—")
    dc = read_decision(root, stamp=str(pd.get("stamp") or ""))
    if dc:
        line += "　｜ 已有决定：%s（by %s）" % (dc.get("decision"), dc.get("by") or "—")
        return line
    stale = read_decision(root)          # 有文件、但戳不匹配 ⇒ 已失效
    if stale:
        line += ("　｜ ⚠️ 磁盘上有个**已失效**的决定（%s，stamp=%s ≠ 当前 %s）"
                 "—— 它不会被消费，请重新确认"
                 % (stale.get("decision") or "?", stale.get("stamp") or "无",
                    pd.get("stamp") or "无"))
        return line
    line += ("　｜ **等待决定**（`--hitl-approve` / `--hitl-reject` / "
             "`--hitl-redo --target <角色>`）")
    return line
