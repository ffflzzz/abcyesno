# -*- coding: utf-8 -*-
"""`mode` 模块：剧本直出模式（from_script）的**名单判据**。

## 为什么有这个模块

创作链原本是**固定 7 个角色**：`guards.PREREQ` / `guards.GATE_ROLES` /
`orchestrator.PREREQ` 三处各写一份，`media_gate("render")` 查这 7 个 phase 全
complete。于是「brief 里已经带完整剧本、只想跑 3 环」这件事**物理上做不到**
——记忆里早有结论：「想跳过创作链必须补占位产物，没有第三条路」。

本模块给出第三条路：**角色一个都不删，只是按模式返回不同的名单。**

## 三条硬设计约束（改错了会出事，逐条说明）

1. **默认必须是 `full`，且返回值与 `guards.PREREQ` 逐字节相等。**
   为什么：全仓有 3 处测试**硬编码**了「7」——
   `tests_flow.py:2951`（`len(GATE_ROLES) == 7`）、`tests_hitl.py:199`
   （`len(PREREQ) == 7`）、`tests_webchain.py:736`（`missing_roles == 7`）。
   只要默认路径不变，这 3 处一个字都不用改 ⇒ **「没改坏」有了客观判据**。

2. **名单是加法，不是减法。**
   `from_script` 下 7 个角色图**仍然全部注册**（`langgraph.json` 不改、
   `orchestrator.make_sync_subagents` 不改）——不派发就是不使用，没有副作用。
   若反过来「从注册表里删角色」，`tests_roles.py:80` 那条「7 个角色一个都不能少」
   会立刻红，且前端 `role_*` 图会缺失。

3. **`scriptwriter` 的产物由 `brief.script` 预置落盘，而不是取消。**
   为什么保留这一个产物：`dialogue` 的**逐字门**（`validate.check_dialogue_verbatim_files`）
   与 `narration` 的旁白表都要读它。取消它 ⇒ 逐字门 `return None`（静默不适用）、
   narration-led 直接失效。用户明确要求「存」⇒ 预置。

## 时间码：不需要映射层（2026-10-04 用户反问后定案）

分镜师现有的活就是「把一个时长数字变成一串镜头」（`target_duration: 120` →
20 个 6 秒镜），给它剧本时间码是同一类任务；且 `brief.json` 全文注入给每个角色
⇒ 剧本一定看得到。**唯一要加的是一句提醒**（`BARE_INT_HINT`）：
`storyboard.parse` 那一格是 `re.search(r"(\\d+(?:\\.\\d+)?)")` **抓第一个数字**
（`media/storyboard.py:288`）⇒ 抄 `0:45` 会被读成 0 → `pack_clamp_sec` 兜底成
**4 秒、静默不报错**。逃不掉（总时长会不对 ⇒ `_duration_gap` 片长门拦下），
但**诊断误导**（报「总时长不合格」而不是「你抄了时间码」）。
"""
from __future__ import annotations

from pathlib import Path

#: 合法模式。**第一个是默认**（`full`）——顺序即 `roles_of` 未指定时的兜底。
MODES: tuple[str, ...] = ("full", "from_script")

#: `from_script` 下**省掉 LLM 调用**的角色。
#:
#: ⚠️ `scriptwriter` 不在名单里，但它的**产物仍要落盘**（见 `preseed`）——
#: `dialogue` 的逐字门与 `narration` 的旁白表都读它，取消它这两条能力静默失效。
SKIPPED_LLM: tuple[str, ...] = ("plotdesigner", "scriptwriter")

#: `from_script` 下**由 brief 预置产物、因此必须判定为 complete** 的角色。
#:
#: 为什么走 `reconcile_manifest`（磁盘事实优先）而不是手工写 `phases`：
#: 那个函数是全仓**唯一**按磁盘事实补记的地方，手写 `phases` 会与它打架。
PRESEEDED: tuple[str, ...] = ("scriptwriter",)

#: 注入给 scenedesigner 的**格式提醒**。见模块 docstring「时间码」一节。
#: ⚠️ 这类系统级规则**必须由代码注入**（`roles.role_input`），不能只写进包 SKILL ——
#: `_role_skill` 对自带该角色 SKILL 的包**不回退** ⇒ 只改一个包，别的包收不到。
#: 同型教训见 `roles.py` 的「片长 / 镜数硬指令」注释。
BARE_INT_HINT = (
    "【格式硬要求·「时长(秒)」列】这一列只填**裸整数**（如 `8`），"
    "**不要**填时间码或带单位的写法（`0:45` / `8s` / `8秒` / `约8秒` 都不行）。\n"
    "为什么：程序读这一格是**抓第一个数字**——写 `0:45` 会被读成 `0`、"
    "再被静默兜底成 **4 秒**，且**全程不报错**，最后只表现为「总时长不合格」，"
    "把你引向错误的方向。\n"
    "★ 剧本里的时间码（如 `0-1.0秒` / `14.0-15.5秒`）是**单拍节奏参考**，"
    "不是本列的填法。**总时长由你按叙事节拍自己决定**："
    "每镜 4–12 秒（供应商硬区间），全片总时长要落在 `target_duration` 的 85%–130% 内，"
    "镜头数与每镜秒数由剧情决定、系统不给除法基线。"
)

#: `from_script` 下给 worldbuilder 的**模式切换指令**。
#:
#: 为什么不能让它自由创作：from_script 的全部意义就是「剧本已经定稿」⇒
#: 世界观/剧情/结构**都是既定的**，它只负责**把剧本里的人与场景抠出来变成卡片**。
#: 让它「创作世界观」会产出一套与剧本冲突的设定 ⇒ 资产卡形制正确但内容对不上
#: ⇒ 静帧画错人物（同类事故：方案 D 撤销，角色卡被写成表格 → 解析出 0 个角色，
#: 全片按无人物处理 → 构图与人数系统性跑偏，见 `orchestrator.make_async_subagents`）。
EXTRACT_ONLY_HINT = (
    "【本片是**剧本直出模式**】`brief.mode = from_script`，"
    "完整剧本在 `brief.json` 的 `script` 字段。\n"
    "你的职责因此收窄为**抽取**，不是创作：\n"
    "· **不要**创作世界观、剧情、人物小传 —— 那些剧本里已经定好了，"
    "你写一套新的会和剧本打架；\n"
    "· 只做一件事：把剧本里**出现的**人物与场景，"
    "按下面的格式抠成 `## 角色卡：<名>` / `## 场景卡：<名>` 两类卡片；\n"
    "· 外形/材质要**具体**（它是媒体链出图的唯一提示词来源），"
    "但**不得改写剧本给定的年龄、身份、武器、服饰形制**；\n"
    "· 剧本没写的外形细节，你**补一个合理的默认值**并保持全片一致 —— "
    "留空会让每镜各自发挥，同一个人长成 18 个人。\n"
    "⚠️ 卡片标题**必须**是 `## 角色卡：<名>` / `## 场景卡：<名>` 这种写法。"
    "写成别的（例如 `### 大罗（爸爸，35 岁）`）会让下游解析出 **0 个角色** —— "
    "参考图不生成、全片按「无人物」处理，而**日志上看不出原因**。"
)


def mode_of(brief: dict | None) -> str:
    """本片模式（读不到/不认识 → 一律 `full`）。

    ⚠️ 未知模式**不报错、回落 full**：本项目的链是长任务，
    一个拼错的字段让整条链起不来（实测记录里这类事故最多）。
    但回落必须是**看得见的** —— `mode.warn_unknown` 负责响亮。
    """
    if not isinstance(brief, dict):
        return "full"
    m = str(brief.get("mode") or "").strip().lower()
    return m if m in MODES else "full"


def warn_unknown(brief: dict | None) -> str:
    """模式字段存在但不认识 ⇒ 返回一句**响亮**的告警（正常返回空串）。

    为什么必须有：静默回落 `full` 会让用户以为跑的是剧本直出，
    实际跑的是 8 环全量 —— 慢一倍且**日志完全正常**。
    """
    if not isinstance(brief, dict):
        return ""
    m = str(brief.get("mode") or "").strip().lower()
    if not m or m in MODES:
        return ""
    return ("[mode] ⚠️ brief.mode=%r **不认识**，已按 %r（8 环全量）处理。"
            "合法值只有：%s。你若想要剧本直出模式，字段拼错就是跑全量——"
            "慢一倍但日志看不出异常。" % (m, "full", "、".join(MODES)))


def script_of(brief: dict | None) -> str:
    """`brief.script` 的正文（读不到 → 空串）。

    ⚠️ **不读文件、不猜路径**：剧本必须在 brief 里（唯一真相源），
    否则 `must_have`（分镜门的覆盖率判据来源）与剧本会分家。
    """
    if not isinstance(brief, dict):
        return ""
    return str(brief.get("script") or "").strip()


def has_script(brief: dict | None) -> bool:
    return bool(script_of(brief))


def roles_of(brief: dict | None = None, mode: str | None = None) -> tuple[str, ...]:
    """本模式**要派发**的角色（依赖序）。

    `full` → 与 `guards.PREREQ` 的键**逐字节相等**（默认路径不变的保证）。
    `from_script` → 7 个里去掉 `plotdesigner` / `scriptwriter` 两次 LLM 调用。

    ⚠️ 本函数**不 import guards**（guards 若 import 本模块会成环）：
    调用方拿不到 `PREREQ` 时传 `mode` 即可，两者都只认 `mode`。
    """
    from .guards import PREREQ          # 局部导入：避开循环依赖
    m = mode or mode_of(brief)
    if m != "from_script":
        return tuple(PREREQ)
    skip = set(SKIPPED_LLM)
    return tuple(r for r in PREREQ if r not in skip)


def gate_roles_of(brief: dict | None = None, mode: str | None = None) -> tuple[str, ...]:
    """本模式**媒体门要查**的角色（依赖序）。

    ⚠️ 与 `roles_of` 的**区别**：`from_script` 下 `scriptwriter` 的产物由
    `preseed` 从 `brief.script` 落盘，所以门**照查**它 ——
    查它是为了让「产物被人手删了」能被拦下，**不是**为了让门放行。
    ⇒ from_script 下：派工 5 个、门查 6 个（多的那个由预置满足）。
    `full` 下两者恒等（返回 `PREREQ` 全部 7 个）。
    """
    from .guards import PREREQ          # 局部导入：避开循环依赖
    m = mode or mode_of(brief)
    if m != "from_script":
        return tuple(PREREQ)
    skip = set(SKIPPED_LLM) - set(PRESEEDED)
    return tuple(r for r in PREREQ if r not in skip)


def preseed(brief: dict | None = None, mode: str | None = None) -> dict[str, str]:
    """本模式**从 brief 预置落盘**的产物：`{角色名: 正文}`（正常返回 `{}`）。

    为什么走这条路而不是「不产这个文件」：
      · `dialogue` 的**逐字门**（`validate.check_dialogue_verbatim_files`）读它 ——
        缺了它返回 `None` ⇒ 静默「不适用」⇒ **失去一道防「分镜改台词」的检查**；
      · `narration` 的**旁白表**从它解析 ⇒ 缺了旁白功能直接失效。

    正文取剧本全文，**逐字不动**（本项目铁律：台词以剧本为唯一真相源，
    资产卡只做搬运不做改写）。
    """
    m = mode or mode_of(brief)
    if m != "from_script":
        return {}
    s = script_of(brief)
    if not s:
        return {}
    return {"scriptwriter": s}


def write_preseed(root: Path, ep: int, brief: dict | None = None,
                  log=print) -> list[str]:
    """把 `preseed` 的产物**真正写盘**，返回落盘的角色名列表（正常 `[]`）。

    ## 为什么不「只在内存里算 complete」

    因为**下游真的读那个文件**：
      · `dialogue` 的逐字门（`validate.check_dialogue_verbatim_files`）读它，
        缺文件 ⇒ 返回 `None` ⇒ **静默「不适用」** ⇒ 失去一道防「分镜改台词」的检查；
      · `narration` 的旁白表从它解析 ⇒ 缺文件旁白功能直接失效；
      · `guards.post_validate` 是**磁盘事实优先**的：文件不在 ⇒ 判 failed ⇒
        `media_gate` 报「缺 scriptwriter」。

    ⇒ 所以必须是**真文件**，不是打个标记。

    ## 幂等与「不许覆盖」

    ⚠️ **已有非空文件时一律不覆盖**。理由与 `guards.post_validate` 那条
    「物化守卫看到文件还在就判 complete」是同一条原则的反面：
    正向是「盘上有就认」，所以**盘上已有的东西不该被 brief 覆盖** ——
    否则一次手改过的剧本正文会被下一次跑**静默冲掉**。
    正常路径下 `write_preseed` 在创作链**之前**跑，盘上不会有东西；
    真有 ⇒ 说明是重跑 → 保留人工版本并**响亮**说出来。

    ## 失败绝不静默

    写盘失败（目录只读、磁盘满、安全策略）抛给调用方 —— 因为它一旦静默失败，
    下游会一路跑到媒体门才报「缺 scriptwriter」，那时已经烧了几十分钟。
    """
    from .guards import load_brief, out_path   # 局部导入：避开循环依赖
    b = brief if brief is not None else load_brief(root)
    todo = preseed(b)
    written: list[str] = []
    for role, body in todo.items():
        rel = out_path(role, int(ep or 1))
        if not rel:
            continue
        p = root / rel
        try:
            if p.exists() and len(p.read_text(encoding="utf-8").strip()) > 0:
                log("[mode] ⚠️ %s 已在盘（非空）→ **不覆盖**，保留盘上这一份。"
                    "若那是旧剧本请手工处理后再跑。" % rel)
                continue
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body, encoding="utf-8")
            written.append(role)
            log("[mode] 预置产物：%s（%d 字，逐字取自 brief.script）"
                % (rel, len(body)))
        except Exception as e:  # noqa: BLE001
            log("[mode] ⛔ 预置产物 %s 写盘失败：%s: %s —— **不静默**。"
                "继续跑会在媒体门报「缺 scriptwriter」，而那时已烧了几十分钟。"
                % (rel, type(e).__name__, str(e)[:160]))
            raise
    return written
