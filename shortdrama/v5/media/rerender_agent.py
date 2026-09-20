# -*- coding: utf-8 -*-
r"""`media_rerender` 子代理：把"人/监制说要改某一镜"变成**一次确定性的单镜重渲**。

## 它是谁

spec §3.2 说"两个入口，同一实现"：

| 入口 | 形态 |
|---|---|
| CLI | `python -m v5.series <项目> --rerender LN03 [--from still]` |
| 对话 | Studio 里跟 director 说「LN03 重做」→ `task("media_rerender", …)` |

**两者都落到 `pipeline.rerender()`**（= `run(only=[...])` 的受限调用）。
本模块只做"对话入口"那一侧：解析任务简报 → 调 `pipeline.rerender` → 回话。

## 为什么节点里**一次 LLM 都不调**

spec 的设计原则：「**"决策"交给 LLM（要不要重做哪一镜），"执行"留给代码
（确定性的 pipeline）**」。"要不要、哪几镜、为什么"由 director 在派发时想清楚并
写进简报；子代理只解析 + 执行。每步再包一层 agent 会多一轮 LLM 调用
（本机实测 20–60 秒/次），而媒体链是确定性流水线，不需要判断能力。
项目已有同类教训：「**跑满角色不能靠提示词，要靠静态边**」（`cast.py` 头部）。

## 镜号解析是**故意 fail-closed** 的

解析规则（`parse_instruction`）：

1. 简报里有 **`【重渲】LN03,LN05 from_still`** 这样的头行 → 只认头行里的镜号；
   （正文随便写，人话）
2. 没有头行 → 全篇找 `LN\d+`：**恰好只有一个** → 认；**≥2 个不同镜号** → 判定为
   **歧义**，拒绝执行（"LN03 的窗帘没开，不像 LN05 那样" 这种解释性提及会把
   无辜的镜也重渲掉，白烧配额）；
3. 都失败 → **什么都不做**，把本片可用镜号回给 director，让它重写简报。

**为什么必须这么严**：解析错 = 重渲了错的镜（3 分钟 + 一份视频配额）；
猜测镜号还可能提交一个不存在的镜。spec §3.4 风险 #6 要的正是
"在烧配额之前拦下"。宁可不动，也不要动错。
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState

#: 唯一节点名（与 `_build_role_graph` 同一约定：LangGraph 的命名空间按调用序分配，
#: 重名在图结构变化时会串状态，趁零成本先给唯一名）。
NODE_NAME = "media_rerender"

#: 头行：`【重渲】LN03` / `[rerender] LN03 from_still`（中英文都认）
_HEADER_RE = re.compile(r"[【\[]\s*(?:重渲|重做|rerender|media_rerender)\s*[】\]]\s*([^\n]*)",
                        re.IGNORECASE)
#: 头行**字段**的语法单元：镜号 / `from_still` / 分隔符。
#:
#: 为什么不能直接对头行字段做"全文找镜号"（2026-09-13 实测）：模型有时把换行写成
#: **转义的 `\n`**（不是真换行）→ `_HEADER_RE` 的 `[^\n]*` 会把整段理由都吃进
#: "头行"，于是理由里提到的**别的镜号**也会被当成重渲目标（重渲错的镜 = 白烧配额）。
#: 故改为**从左往右按语法单元扫描，遇到第一个不是镜号/标记/分隔符的东西就停**。
_HEAD_ITEM_RE = re.compile(r"\s*(?:LN\s*0*(\d+)|from_still|[,，、;；/|]+)", re.IGNORECASE)
#: 镜号：`LN03` / `ln3`（分镜的 name 就长这样）
_SHOT_RE = re.compile(r"\bLN\s*0*(\d+)\b", re.IGNORECASE)
#: 中文镜序：`第3镜` / `镜头 3` / `镜号3`
_NTH_RE = re.compile(r"(?:第\s*(\d+)\s*镜|镜[头号]\s*(\d+))")

#: 「连静帧一起重做」的显式标记（**不接受裸「静帧」**——那句话常是原因描述而不是指令，
#: 例如"静帧里窗帘没开"；误开的代价是白画一张图 + 白等一次串行）。
_STILL_MARKERS = ("from_still", "from still", "--from still", "连静帧",
                  "重画静帧", "重新画静帧", "重出静帧", "重做静帧", "重画首帧")


def has_still_marker(text: str) -> bool:
    low = str(text or "").lower()
    return any(k in low for k in _STILL_MARKERS)


def _shot_ids(text: str, known: list[str]) -> list[str]:
    """从一段文本里抠镜号（按出现顺序、去重）。`known` 非空时只认**真实存在**的镜号。"""
    up = {k.upper(): k for k in known}
    out: list[str] = []
    for m in _SHOT_RE.finditer(str(text or "")):
        n = "LN%02d" % int(m.group(1))
        if up and n not in up:
            continue                    # 分镜里没这个镜 → 不当镜号（风险 #6）
        name = up.get(n, n)
        if name not in out:
            out.append(name)
    return out


def _head_targets(field: str, known: list[str]) -> list[str]:
    """头行字段 → 镜号列表：**从左往右按语法单元扫描，第一个不匹配就停**。

    见 `_HEAD_ITEM_RE` 的说明：模型可能把换行写成转义 `\\n`，那时"头行"实际上是
    整段文本 —— 不做这个截断，理由里提到的别的镜号会被误当成重渲目标。
    """
    up = {k.upper(): k for k in known}
    out: list[str] = []
    pos = 0
    while True:
        m = _HEAD_ITEM_RE.match(field, pos)
        if not m:
            break
        pos = m.end()
        if m.group(1) is None:
            continue
        n = "LN%02d" % int(m.group(1))
        if up and n not in up:
            continue
        name = up.get(n, n)
        if name not in out:
            out.append(name)
    return out


def _nth_ids(text: str, known: list[str]) -> list[str]:
    """`第3镜` → `known[2]`。**必须有分镜才能映射**，映射不出就不给（不猜）。"""
    if not known:
        return []
    out: list[str] = []
    for m in _NTH_RE.finditer(str(text or "")):
        i = int(m.group(1) or m.group(2))
        if 1 <= i <= len(known) and known[i - 1] not in out:
            out.append(known[i - 1])
    return out


def parse_instruction(text: str, known: list[str] | None = None) -> dict:
    """任务简报 → `{"shots": [...], "from_still": bool, "note": str, "problem": str}`。

    `shots` 为空 = **解析失败，调用方必须什么都不做**（见模块 docstring 的 fail-closed）。
    `problem` 非空时是给 director 看的"为什么没法执行 + 该怎么写"。
    """
    raw = str(text or "")
    known = [str(k) for k in (known or [])]
    hint = ("本片可用镜号：" + "、".join(known[:12])) if known else "（分镜未就绪，镜号无从校验）"

    head = _HEADER_RE.search(raw)
    if head:
        field = head.group(1)
        shots = _head_targets(field, known) or _nth_ids(field, known)
        source = "头行"
    else:
        shots = _shot_ids(raw, known) or _nth_ids(raw, known)
        source = "正文"
        if len(shots) > 1:
            # 歧义：解释性提及很容易带出别的镜号，重渲错的镜 = 白烧配额
            return {"shots": [], "from_still": False, "note": raw.strip()[:300],
                    "problem": ("简报里出现了 %d 个不同镜号（%s），无法判断要重做哪一个。"
                                "请在简报开头加一行头行指明目标，例如：\n"
                                "【重渲】%s [from_still]\n%s"
                                % (len(shots), "、".join(shots), shots[0], hint))}
    if not shots:
        return {"shots": [], "from_still": has_still_marker(raw),
                "note": raw.strip()[:300],
                "problem": ("没能在简报里识别出镜号。请在简报开头加一行头行，例如：\n"
                            "【重渲】LN03 [from_still]\n理由…\n%s" % hint)}
    return {"shots": shots, "from_still": has_still_marker(raw),
            "note": raw.strip()[:300], "source": source, "problem": ""}


def _last_human_text(state: MessagesState) -> str:
    msgs = (state or {}).get("messages") or []
    for m in reversed(msgs):
        if isinstance(m, HumanMessage):
            return str(m.content or "")
    return "\n".join(str(getattr(m, "content", "") or "") for m in msgs)


def format_result(shots: list[str], res: dict, from_still: bool) -> str:
    """把 `pipeline.rerender` 的返回值写成**给 director 看的一段话**。

    必须包含"成没成、动了哪些镜、有没有带伤出厂"，否则弱模型会顺着"已重渲"的语气
    编出"修好了"——而 status 可能是 incomplete/residual 非空。
    """
    st = str((res or {}).get("status") or "?")
    if st == "ok":
        head = "✅ 单镜重渲完成：%s" % "、".join(shots)
    elif st == "blocked":
        head = "⛔ 单镜重渲**未执行**（被守卫挡住）：%s" % "、".join(shots)
    else:
        head = "⚠️ 单镜重渲**未成功**（status=%s）：%s" % (st, "、".join(shots))
    lines = [head]
    if res.get("gate"):
        lines.append("· 挡在哪：%s —— 原因：%s" % (res["gate"], res.get("reason") or ""))
    elif st != "ok" and res.get("reason"):
        lines.append("· 原因：%s" % res["reason"])
    if res.get("final"):
        lines.append("· 成片：%s" % res["final"])
    if res.get("seconds"):
        lines.append("· 成片时长：%ss（%s 镜）"
                     % (res["seconds"], res.get("clips") or res.get("shots") or "?"))
    if res.get("requeued"):
        lines.append("· 期间被复核判不合格并自动重做了：%s" % "、".join(res["requeued"]))
    if res.get("residual"):
        lines.append("· ⚠️ 这些镜**带伤出厂**（重做未达上限/未回来，已保留旧片）：%s"
                     % "、".join(res["residual"]))
    if res.get("still_residual"):
        lines.append("· ⚠️ 静帧层未通过的镜：%s" % "、".join(res["still_residual"]))
    if from_still:
        lines.append("· 静帧已**一起重做**（问题若在人物/道具/场景本身，只有这才修得掉）")
    elif st == "ok":
        # ★ 这一行必须显眼（2026-09-13 探针实测）：director 对"问题在静帧层还是
        # 成片层"的判断是**不稳的**（三次探测里这类请求都派发失败）。而"静帧没改"
        # 是个**静默前提**——不说破的话，人看到"✅ 完成"会以为人物长相也修了，
        # 实际那类问题**只在视频层重渲是修不掉的**，于是白等一轮再来一次。
        lines.append("· ⚠️ 静帧**未改动**（本次只重渲视频）。如果问题是"
                     "「人物长相 / 衣着 / 道具 / 场景本身画错了」，这次修不掉 —— "
                     "请向人确认，必要时带 `from_still` 再派一次。")
    else:
        lines.append("· 静帧：复用盘上静帧（未重做）")
    lines.append("· 其余镜未受影响（同一入口的受限调用，gate/记账照常）")
    return "\n".join(lines)


def build_rerender_graph(root: Path, runner=None):
    """编译 `media_rerender` 图。`runner` 可注入（测试用），缺省 = `pipeline.rerender`。

    **用 `asyncio.to_thread` 跑**：媒体链是**同步**流水线（`time.sleep` 轮询 +
    `subprocess` 拼片），单镜重渲实测 **3–10 分钟**。直接在协程里调它会把
    dev server 的事件循环**冻住**整个时长（连 `/ok` 探活都不响应）。
    """
    from . import pipeline as pipeline_mod

    def _run(shots: list[str], note: str, from_still: bool) -> dict:
        fn = runner or pipeline_mod.rerender
        return fn(root, shots, note=note, from_still=from_still)

    async def node(state: MessagesState) -> dict:
        text = _last_human_text(state)
        from . import pipeline as _p
        # 只能读**分镜**来校验镜号（不依赖 supervisor 传参，与 role 图同一纪律）
        known = _p._known_shots(root)
        plan = parse_instruction(text, known)
        if plan["problem"]:
            return {"messages": [AIMessage(content=(
                "⛔ media_rerender 未执行（避免烧错配额）。\n%s\n"
                "解析到：shots=%s from_still=%s"
                % (plan["problem"], plan["shots"] or "—",
                   plan["from_still"])))]}
        shots, from_still = plan["shots"], plan["from_still"]
        res = await asyncio.to_thread(_run, shots, plan["note"], from_still)
        return {"messages": [AIMessage(content=format_result(shots, res, from_still))]}

    g = StateGraph(MessagesState)
    g.add_node(NODE_NAME, node)
    g.add_edge(START, NODE_NAME)
    g.add_edge(NODE_NAME, END)
    return g.compile()
