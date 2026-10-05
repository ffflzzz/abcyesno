# -*- coding: utf-8 -*-
"""守卫层：状态机 / 派发前置 / 物化对账 / token 熔断 / 收尾对账。

这一层是旧架构付了两次学费换来的（40M tokens 事故 + scriptwriter 假失败），
新架构从第一天就带上，且修掉了旧版的两个已知缺陷：
  1. 物化守卫的 {N} 占位符**直接展开**（旧版从未展开 → 永远判失败）
  2. token 熔断（旧版完全没有 → 跑飞没人拦）
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import config

# ─── 角色依赖与产物契约 ──────────────────────────────────────────────────────
PREREQ: dict[str, list[str]] = {
    "worldbuilder": ["director"],
    "assetdesigner": ["worldbuilder"],
    "plotdesigner": ["director", "worldbuilder"],
    "scriptwriter": ["plotdesigner", "worldbuilder"],
    "dialogue": ["scriptwriter"],
    "scenedesigner": ["scriptwriter", "dialogue", "assetdesigner"],
    "reviewer": ["scenedesigner"],
}

OUTPUTS: dict[str, str] = {
    # ── 全剧级（一次锁定，全剧复用）────────────────────────────────────────
    "director": "director/director.md",
    "worldbuilder": "worldbuilder/worldbuilder.md",
    "assetdesigner": "assetdesigner/assets.md",
    "plotdesigner": "plotdesigner/episodes.md",      # 名字是 episodes（复数）→ 本来就规划 N 集
    # ── 集级（每集独立；2026-09-16 M1 把后三个也加了 {N}）──────────────────
    "scriptwriter": "scriptwriter/scriptwriter_ep{N}.md",
    "dialogue": "dialogue/dialogue_ep{N}.md",
    "scenedesigner": "scenedesigner/scenedesigner_ep{N}.md",
    "reviewer": "reviewer/review_ep{N}.md",
}
# ⚠️ 改这里的路径**必须同步改两处**，否则静默失败（实测事故）：
#   · 各包 `SKILL.md` 里的产物路径文案（角色按它写盘；不改 → 角色写到旧路径
#     → out_path 找不到 → 物化守卫判「产物缺失」→ 阶段永不 complete，**且日志看不出原因**）
#   · `media/approvals.py` 的 `_SRC`（审批门**产物指纹源**；不改 → 指纹算错
#     → 上游产物变了批文却**没作废**）
# 读取一律走 `resolve_path()`（新名优先、旧名回退），兼容历史项目。

ROLES = tuple(OUTPUTS)
# 媒体门要检查的角色 = **被实际派发的那 7 个**（`PREREQ` 的键），**不含 `director`**。
# 为什么（2026-09-12 口径对齐）：supervisor 架构里 `director` 就是 supervisor 自己、
# 不是被派发的角色节点 —— **没有任何代码路径**会把 `phases["director"]` 记为
# complete，于是旧口径（8 个）会让 media_gate **永远拦住媒体链**。
# 旧静态链时代 director 是图上一个真实节点，8 个口径在当时是对的。
GATE_ROLES: tuple[str, ...] = tuple(PREREQ)


# ─── 按模式取名单（剧本直出模式，2026-10-04）──────────────────────────────
#
# ⚠️ **上面那个常量一个字都没改**，它仍是默认（`full`）模式的唯一真相源。
# 本节只加**按项目 brief 取名单**的入口 —— 判据仍在 `v5/mode.py` 一份。
#
# 为什么必须按项目取而不是全局 env 开关：多项目并行（2026-09-29 起支持）是本项目
# 的既定能力，而模式是**项目属性**。全局开关会让两条链互相污染
# （A 带剧本、B 没带，B 的门被 A 的名单判）。
#
# ⚠️ **`reconcile_manifest` 必须走模式化名单**：from_script 下 `scriptwriter`
# 的产物由 `mode.preseed` 落盘，若这里仍按 7 个查、而预置产物又被
# `post_validate` 判成没问题，那没事；但若**预置失败**（剧本为空），
# 对账必须把它**记成 failed** 而不是静默跳过 —— 否则门会放行一个缺台词的片。


def gate_roles_for(root: Path, ep: int | None = None) -> tuple[str, ...]:
    """本项目媒体门要查的角色（默认模式 = `GATE_ROLES`，7 个）。

    读 `brief.json` 的 `mode` 字段决定；**读不到 / 不认识一律按 full**
    （回落由 `mode.warn_unknown` 响亮告警，不静默）。
    """
    from . import mode as _mode          # 局部导入：mode 会 import guards（循环）
    brief = load_brief(root)
    w = _mode.warn_unknown(brief)
    if w:
        print(w, flush=True)
    if _mode.mode_of(brief) == "full":
        return GATE_ROLES
    return _mode.gate_roles_of(brief)


def prereq_for(root: Path, ep: int | None = None) -> dict[str, list[str]]:
    """本项目的依赖序（默认模式 = `PREREQ`，逐字节相同）。

    ⚠️ from_script 下**不删边、只删点**：被省掉的角色（plotdesigner /
    scriptwriter）从「要派发的键」里消失，于是它们作为**前置**的那些边
    （`scriptwriter: [plotdesigner, worldbuilder]`）自然不再被读到。
    """
    from . import mode as _mode
    brief = load_brief(root)
    if _mode.mode_of(brief) == "full":
        return PREREQ
    keep = set(_mode.roles_of(brief))
    return {r: [d for d in deps if d in keep] for r, deps in PREREQ.items()
            if r in keep}


def reconcile_manifest(root: Path, m: dict | None = None,
                       ep: int | None = None) -> dict:
    """**物化对账**：按磁盘事实补齐 `phases`，返回更新后的 manifest。

    为什么需要（2026-09-12 实测）：`phases` 原本只靠角色节点里的**后置记账**写入。
    实测出现过「7 个角色产物全部在盘、`phases` 却是空的」——媒体门因此拦住媒体链，
    而且**查不出原因**。同类事故历史上已发生过一次（见 `record_phase` 的文档字符串：
    旧版记账挂在中间件钩子里，钩子没被调 → phases 不落盘）。

    → 结论：**不要依赖脆弱的后置钩子**。在媒体门之前按磁盘事实对账一次。
    这正是本项目一贯的原则：**验收必须以磁盘事实为准**。
    裁决逻辑复用 `post_validate`（物化对账），**不另立一套判据**。

    **语义是「只补不改」（repair-only），这一点很重要**：
      · 产物在盘 → 记 `complete`（这就是我们要修的场景：产物全在盘、账本却是空的）
      · 产物不在盘 **且账本也没有记录** → 如实记 `failed`
      · 产物不在盘 **但账本已记 complete** → **不动**
    为什么不降级：账本里的 `complete` 可能对应"产物曾经合法存在"（测试与
    部分流程会先 seed 账本再造产物）。若这里按磁盘覆盖，会把已成立的
    `complete` 改坏 —— 实测这么写会让 6 个既有测试从通过变失败。
    **对账的职责是"补记"，不是"重判"。**
    """
    m = load_manifest(root) if m is None else m
    if ep is None:
        ep = int(m.get("episode_index", 1) or 1)
    for role in gate_roles_for(root, ep):
        ok, _why, _p = post_validate(role, m, root, ep=ep)
        if ok:
            set_phase(m, role, "complete", ep)
        elif not phase_of(m, role, ep):
            # 「账本也没有记录」在二维下 = 本集这一格为空（不是"整张表里没这个键"）
            set_phase(m, role, "failed", ep)
    # ★ 评审判定同样按磁盘事实补记（2026-09-12，与 phases 同一类缺口）：
    #   全仓库**没有任何代码**写 `m["review"]` —— reviewer 的判定只活在
    #   `reviewer/review.md` 末尾的围栏判定块里。于是 media_gate 的
    #   「评审未通过（无 pass: true）」会一直拦着，即使 reviewer 明明 pass 了。
    #   这里复用 `decision.parse_decision` 解析（**不另立判据**）。
    #   只在 review.md 存在时覆盖；不存在则保持账本原样（repair-only 的同一条原则）。
    rev_path = resolve_path(root, "reviewer", ep)
    if rev_path.exists():
        try:
            from . import decision
            dec = decision.parse_decision(rev_path.read_text(encoding="utf-8"))
            if dec:
                rev = m.setdefault("review", {})
                rev["passed"] = decision.normalize_pass(dec)
                rev["rerun"] = dec.get("rerun") or []
                rev["reasons"] = dec.get("reasons") or []
                if dec.get("unfenced"):
                    # 判定块**漏写代码围栏**（模型偶发，2026-09-14 实测 22 个真实项目里 2 个）。
                    # 已由 `decision` 的兜底路径解析出来，判定**有效** → 不阻断；
                    # 但必须说出来，否则换个写法就会再次静默退化成「评审未通过」。
                    print("[guards] ⚠️ %s 的判定块没有代码围栏（```yaml）—— 契约要求围栏，"
                          "本次已按裸块兜底解析（pass=%s）"
                          % (out_path("reviewer", ep), rev["passed"]))
            else:
                # ★ 产物存在却解析不出判定 → 媒体门必然报「评审未通过（无 pass: true）」，
                #   而报告里可能明明写着 `pass: true`（自相矛盾、极难排查）。
                #   原先这里是**完全静默**的（连异常都被 `except: pass` 吞掉）—— 必须显式告警。
                #   ⚠️ 文件名必须带**本集集号**：这里原先写死 `out_path("reviewer")`（默认 ep=1），
                #   于是渲第 2 集时告警指着 `review_ep1.md` 报"解析不出判定块"，而那份文件
                #   的判定块是好的（2026-09-29 实测把我引去查了一个不存在的解析器 bug）。
                print("[guards] ⚠️ %s 存在但解析不出判定块（pass/rerun/reasons）"
                      "→ 评审门会判「未通过」，媒体链会被拦下。请检查该文件末尾的判定块格式。"
                      % out_path("reviewer", ep))
        except Exception as e:  # noqa: BLE001
            print("[guards] ⚠️ %s 判定解析异常：%s"
                  % (out_path("reviewer", ep), str(e)[:120]))
    save_manifest(root, m)
    return m


def out_path(role: str, ep: int = 1) -> str:
    """产物路径（**展开 {N}**——旧架构漏了这一步，导致物化守卫永远判失败）。"""
    return OUTPUTS.get(role, "").replace("{N}", str(ep))


# ─── 多集支持（M1，2026-09-16）────────────────────────────────────────────────
#
# 背景（详见 `../docs-archive-20260918/spec-m1-plan.md` 与 `../docs-archive-20260918/spec-series-and-screenwriting-craft.md`）：
#   改造前**只有 `scriptwriter` 带 `{N}`**，其余角色是固定路径 ⇒ **第 2 集覆盖第 1 集**。
#   M1 把 `dialogue` / `scenedesigner` / `reviewer` 改为**集级路径**。
#
# ⚠️ 写入一律用新路径（`out_path`）；**读取用 `resolve_path`**（新名优先、旧名回退），
#   这样 30 个历史项目（产物是旧名）仍可 `--resume-media` / `--rerender`。
_OUTPUTS_LEGACY = {
    "dialogue": "dialogue/dialogue.md",
    "scenedesigner": "scenedesigner/scenedesigner.md",
    "reviewer": "reviewer/review.md",
}


def resolve_path(root: Path, role: str, ep: int | None = None) -> Path:
    """**读**某角色的产物：新路径优先，**旧路径回退**（兼容历史项目）。

    为什么要兼容读：集号进路径后，**单集项目的产物名也变**
    （`scenedesigner.md` → `scenedesigner_ep1.md`）⇒ 不做回退，历史项目的
    `--resume-media` / `--rerender` 会因产物名不匹配而失败。

    ⚠️ **旧名回退只对第 1 集生效**（2026-09-16 实测抓到的 bug）：
    历史项目是**单集**的 → 那个旧名文件**就是**第 1 集。若对第 2 集也回退，
    `resolve_path(root, role, 2)` 会返回**第 1 集的旧文件** → 第 2 集把第 1 集覆盖掉，
    **而且绕过了集级路径的全部保护**（这正是 M1 要修的那个 bug 换了个入口回来）。
    """
    if ep is None:
        ep = int(load_manifest(root).get("episode_index", 1) or 1)
    p = root / out_path(role, ep)
    if p.exists():
        return p
    legacy = _OUTPUTS_LEGACY.get(role)
    if legacy and int(ep) == 1:
        lp = root / legacy
        if lp.exists():
            return lp
    return p


#: 显式指定**起服集号**的环境变量（多集连载）。
#:
#: ⚠️ **M3（2026-09-17）起它不再是必需品**：产物路径已改为在**每次开工**由
#: `roles.role_input` 给出（`【本集产物路径】`），system prompt 只给形状 ⇒
#: **一个 dev server 可以连续跑第 1..N 集**，不必换集重启。
#: 保留它只是为了：① 单集调试时显式固定集号；② 老脚本的兼容。
EPISODE_ENV = "SHORTDRAMA_V5_EPISODE"


def boot_episode(root: Path | None = None) -> int:
    """**起服时**的默认集号：`SHORTDRAMA_V5_EPISODE` > manifest 的 `episode_index` > 1。

    它现在只作为**回退**：真正决定"这一轮写哪一集"的是 manifest 的 `episode_index`
    （`roles.role_input` 每次开工都从它取），见上面 `EPISODE_ENV` 的说明。
    """
    import os
    raw = os.environ.get(EPISODE_ENV, "").strip()
    if raw.isdigit() and int(raw) >= 1:
        return int(raw)
    if root is not None:
        try:
            return int(load_manifest(root).get("episode_index", 1) or 1)
        except Exception:  # noqa: BLE001
            return 1
    return 1


#: `phases` 的**读访问器** —— 兼容一维（旧）与二维（新 `{ep: {role: state}}`）。
#
# 为什么先加访问器再升维（见 `../docs-archive-20260918/spec-m0-checklist.md` §M0④）：
#   `phases` 在**生产代码 45 处（8 文件）+ 测试 26 处（4 文件）**。
#   散改 = 一次高风险重构；收敛成"接口改动"后，每步都能独立跑测试，
#   且**旧项目的一维 `phases` 仍可读**（不需要迁移历史项目）。
def phase_of(m: dict, role: str, ep: int | None = None) -> str:
    """读某角色（某集）的阶段状态（**兼容旧的一维 `phases`**，只对第 1 集成立）。"""
    ph = m.get("phases") or {}
    if ep is None:
        ep = int(m.get("episode_index", 1) or 1)
    ep_map = ph.get(str(ep))
    if isinstance(ep_map, dict):                      # 新结构 {ep: {role: state}}
        return str(ep_map.get(role) or "")
    if ep == 1 and role in ph:                        # 旧结构 {role: state}
        return str(ph.get(role) or "")
    return ""


def set_phase(m: dict, role: str, state: str, ep: int | None = None) -> None:
    """**写**某角色（某集）的阶段状态（写新结构；旧的一维结构不再产生）。"""
    if ep is None:
        ep = int(m.get("episode_index", 1) or 1)
    ph = m.setdefault("phases", {})
    if not isinstance(ph.get(str(ep)), dict):
        # 旧的一维结构（`{role: state}`）→ 迁到第 1 集名下，再往下写。
        # ⚠️ **绝不能 `ph.clear()`**：那会连带删掉**别的集**已经写好的二维表
        #    （实测被测试抓到：先写第 1 集、再写第 2 集 → 第 1 集的名册被整个清空）。
        #    只摘掉"值是字符串"的那些旧条目 —— 那才是一维结构的特征。
        old = {k: v for k, v in ph.items() if isinstance(v, str)}
        for k in old:
            ph.pop(k, None)
        if old:
            ph.setdefault("1", {})
            ph["1"].update(old)
        ph.setdefault(str(ep), {})
    ph[str(ep)][role] = state


def media_loop_of(m: dict, ep: int | None = None) -> dict:
    """**读**本集的 `media_loop`（`rendered` / `pending_revision` / 指纹）。

    为什么要按集隔离（2026-09-17 M2）：`media_gate("render")` 有一条
    「**已渲染且无待修订 → 无需重渲**」。`media_loop` 若是一维，第 1 集渲染完
    `rendered=True` 之后，**第 2 集的渲染会被自己的门挡掉** —— 而且报的原因是
    「已渲染无需重渲」，看起来像"没必要渲"，实际是**串集**。M3 的集循环会立刻撞上它。

    与 `phase_of` 同型：兼容旧的一维结构（**只对第 1 集成立**）。
    """
    ml = m.get("media_loop") or {}
    if ep is None:
        ep = int(m.get("episode_index", 1) or 1)
    sub = ml.get(str(ep))
    if isinstance(sub, dict):
        return sub
    # 旧的一维结构：值是标量（rendered/pending_revision/指纹），不是子表
    if ep == 1 and not any(isinstance(v, dict) for v in ml.values()):
        return ml
    return {}


def media_loop_set(m: dict, ep: int | None = None) -> dict:
    """**写**本集的 `media_loop`：返回该集的子表（必要时创建）。

    与 `set_phase` 同型地把旧一维结构迁到第 1 集名下；**绝不 `clear()`**
    ——那会连带删掉别的集的子表（与 `set_phase` 踩过的同一个坑）。
    """
    if ep is None:
        ep = int(m.get("episode_index", 1) or 1)
    ml = m.setdefault("media_loop", {})
    if not isinstance(ml.get(str(ep)), dict):
        old = {k: v for k, v in ml.items() if not isinstance(v, dict)}
        for k in old:
            ml.pop(k, None)
        if old:
            ml.setdefault("1", {})
            ml["1"].update(old)
        ml.setdefault(str(ep), {})
    return ml[str(ep)]


# ─── 最小黑板（manifest）存取 ────────────────────────────────────────────────

def manifest_path(root: Path) -> Path:
    return root / ".agent_state.json"


def load_manifest(root: Path) -> dict:
    p = manifest_path(root)
    if p.exists():
        try:
            import json
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}
    return {}


def save_manifest(root: Path, m: dict) -> None:
    import json
    p = manifest_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")


def load_brief(root: Path) -> dict:
    """读项目根的 brief.json（角色能 read_file 到的那份）。

    为什么放这里：`audio_mode` 这类需求字段此前只有角色自己看得到，编排层拿不到，
    于是「brief 说要台词、分镜全无声」这种偏差没有任何一层能发现。
    """
    p = root / "brief.json"
    if not p.exists():
        return {}
    try:
        import json
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


# ─── 0. phases 记账（由角色自身调用，不依赖中间件钩子）────────────────────

def record_phase(root: Path, m: dict, role: str, count: bool = True,
                 ep: int | None = None) -> dict:
    """角色收工落账：物化对账 → phases / revision_counts，并**立即落盘**。

    旧版把记账放在中间件的 POST 钩子里，`save_manifest` 又只挂在两处异常分支上，
    于是"phases 没落盘"成了真事故：media_gate 看到空 phases，直接放行渲染。
    现在角色节点自己记账、自己落盘，链路不依赖任何中间件是否被调用。

    count=False：跳过重跑时用（达修订上限的角色直接沿用现有产物），
    此时**不再累加** revision_counts，否则计数会虚增。

    ★ M2（2026-09-17）：**按集写**（`set_phase`）。改造前写一维结构 ⇒ 第 2 集会覆盖
    第 1 集的名册。`ep` 不传则取 manifest 的 `episode_index`。

    ⚠️ `revision_counts` **保持一维**：它的语义是"本项目这一集回退了几次"，
    每集的预算必须独立 ⇒ 由 `bind_episode` 在**换集时清零**（不是靠升维）。
    """
    if role not in OUTPUTS:
        return m
    if ep is None:
        ep = int(m.get("episode_index", 1) or 1)
    okp, why, _path = post_validate(role, m, root, ep=ep)
    rc = m.setdefault("revision_counts", {})
    if okp:
        set_phase(m, role, "complete", ep)
    else:
        set_phase(m, role, "failed", ep)
        if count:
            rc[role] = int(rc.get(role, 0)) + 1
        m.setdefault("notes", []).append("ep%d %s: %s" % (ep, role, why))
    save_manifest(root, m)
    # ★ 收工记账**落盘后自报一行**（2026-09-17 真机验收发现的疑点）：
    #   `serial-smoke --chain-only` 跑完后 manifest 里**没有 phases**，而
    #   `projects/.tmp/role_fs_root.txt`（同一个角色节点里的调试写）时间戳还停在 09-12
    #   ⇒ 说明**角色节点的记账代码路径可能没被执行**（产物是别处写出来的）。
    #   影响有界（`pipeline.run` 里的 `reconcile_manifest` 会按磁盘事实补记 ⇒
    #   媒体链不受影响），但 `--monitor` 会把"产物全在盘"报成 0/N complete
    #   —— 那是**误导性观察**。这行日志让下一次运行能直接证实/排除。
    # flush=True：dev.log 是文件句柄，裸 print 会被**块缓冲**吞掉
    # （2026-09-17 三集验收就因此无法判定"记账到底跑没跑"）
    print("[guards] 记账 ep%d %s → %s" % (ep, role, phase_of(m, role, ep)),
          flush=True)
    return m


def reset_from(role: str, m: dict, root: Path | None = None,
               ep: int | None = None) -> list[str]:
    """把 role 及其所有下游角色的阶段状态清空（回退重跑用）。

    为什么连下游一起清：回退 scriptwriter 后，dialogue / scenedesigner 是基于
    **旧剧本**的产物，必须重跑，否则回退等于没回退。
    revision_counts **不清**——它是防死循环的计数，跨回退累计。

    传入 root 时，同时把待重跑角色的旧产物**移到 .rerun_backup/**：
    否则物化守卫看到"文件还在"就直接判 complete，重跑变成空转。

    ★ M2：**只清本集**（改造前清一维表 ⇒ 回退第 2 集会连带把第 1 集的名册清掉）。
    """
    if role not in OUTPUTS:
        return []
    if ep is None:
        ep = int(m.get("episode_index", 1) or 1)
    reset = list(ROLES[ROLES.index(role):])
    ph = m.setdefault("phases", {})
    ep_map = ph.get(str(ep))
    if isinstance(ep_map, dict):
        for r in reset:
            ep_map.pop(r, None)
    else:
        # 旧的一维结构：只在第 1 集语义下成立
        for r in reset:
            ph.pop(r, None)
    if root is not None:
        stash_artifacts(root, reset, ep)
    return reset


def stash_artifacts(root: Path, roles: list[str], ep: int = 1) -> list[str]:
    """把角色的旧产物移到 .rerun_backup/<序号>/，返回被移走的路径。

    ★ 用 `resolve_path` 定位（不只是拼新名）：历史项目的产物是**旧名**，
    只拼新名会"什么都没搬走" → 物化守卫看到文件还在 → 重跑变空转。
    """
    import time
    moved: list[str] = []
    if not roles:
        return moved
    base = root / ".rerun_backup" / time.strftime("%Y%m%d-%H%M%S")
    for role in roles:
        rel = out_path(role, ep)
        if not rel:
            continue
        p = resolve_path(root, role, ep)
        if not p.exists():
            continue
        # 归档路径按**实际文件**的目录/文件名落，便于人工回捞
        dest = base / p.relative_to(root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            p.replace(dest)
            moved.append(str(p.relative_to(root)))
        except Exception:  # noqa: BLE001 -- 备份失败不该阻断回退
            continue
    return moved


def restore_stashed(root: Path, roles: list[str], ep: int = 1) -> list[str]:
    """把 `stash_artifacts` 刚挪走的产物**放回原位**，返回被放回的相对路径。

    ★ 为什么需要它（2026-10-03 实测 `yoga-affair-1003g`，我自己引入的死路）：
      驱动器**先** `reset_from()` 把分镜与审稿挪进 `.rerun_backup/`，**再**起新一轮重派；
      若这一轮被反空转闸当场掐掉（计数器是跨轮累计的，被打回后必然超限），
      盘上就**一份产物都不剩** ⇒ `run_new_project` 报「缺 scenedesigner、reviewer →
      不进媒体链」⇒ 46 分钟、零出片。挪走产物却没有东西补回来，等于把出口一起搬走。

    两条硬边界（都是"不许弄丢工作"）：
      · **只在盘上当前没有该角色产物时**放回 —— 已经有新版本就绝不覆盖；
      · **只回捞最新一个** `.rerun_backup/<ts>/`（那是本次打回挪走的那一份），
        更早的历史版本留在原处供人工回捞。
    """
    import shutil
    base = root / ".rerun_backup"
    if not roles or not base.is_dir():
        return []
    dirs = sorted([d for d in base.iterdir() if d.is_dir()], reverse=True)
    if not dirs:
        return []
    newest = dirs[0]
    restored: list[str] = []
    for role in roles:
        rel = out_path(role, ep)
        if not rel:
            continue
        live = resolve_path(root, role, ep)
        if live.exists():
            continue                                    # 有新版本，绝不覆盖
        cand_dir = newest / Path(rel).parent
        cands = sorted(cand_dir.glob("*.md"), key=lambda p: p.stat().st_mtime) \
            if cand_dir.is_dir() else []
        if not cands:
            continue
        src = cands[-1]
        dest = root / rel
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
            restored.append(rel)
        except Exception:  # noqa: BLE001 -- 回捞失败要看得见，但不炸链
            continue
    return restored


def record_review_block(root: Path, m: dict, ep: int = 1) -> int:
    """把「评审拦下一次」记进**门那一份**台账，返回累计次数。

    ★ 为什么由驱动器也要记（2026-10-03）：`media_gate` 的 `review_blocks` 只在
      **有人来问门**时才 +1。而自动打回发生在创作链里、门一次都没被问到 ⇒
      台账永远是 0 ⇒ 门第一次被问就判「第 1/2 次拦截、还差两次」⇒
      同一份不合格产物要人被叫三次才放行（实测：闸触发后 rc=1，门根本没跑到）。
      驱动器执行的那次打回**就是**评审拦下的一次，由它落账，判据仍只有一份数字
      （`config.MAX_REVISIONS_PER_PHASE`），读台账的一侧（`reroll_budget`）不用改。
    """
    rb = m.setdefault("review_blocks", {})
    n = int(rb.get(str(int(ep))) or 0) + 1
    rb[str(int(ep))] = n
    if root is not None:
        save_manifest(Path(root), m)
    return n



# ─── 0. 分镜契约门的**报告落盘**（2026-09-19，人工模式配套）────────────────────
#
# 为什么需要（这是"门降级为警告"能不能成立的关键）：
#   人工模式（`config.HUMAN_IN_CHARGE`）下分镜契约门**不拦** —— 判断权在人。
#   但"不拦"**不等于"不查"**：判决必须有一个**能看见的落点**，否则
#   「把门降级为警告」实际等于「把门删掉」。落到 `media/ep{N}/gates.json`：
#     · 前端/排障直接读（`GET /projects/{pid}/progress` 的 `v5.gates`）；
#     · 每次过门都**覆盖写**（`blocked` 标志区分"拦了"与"放行"），
#       于是不存在"上一轮的红字留在页面上"这种陈旧告警。
#   判据本身**不在本模块**（`series._storyboard_gate` → `validate.check_storyboard`，
#   唯一一份）；这里只管**存**与**取**。
GATE_REPORT_NAME = "gates.json"


def gate_report_path(root: Path, ep: int | None = None) -> Path:
    """报告落在 `media/ep{N}/gates.json`（按集 —— 一维会让第 2 集顶掉第 1 集）。"""
    return root / "media" / ("ep%d" % int(ep or 1)) / GATE_REPORT_NAME


def record_gate_report(root: Path, ep: int | None, fatal, tips,
                       *, blocked: bool, where: str) -> dict:
    """写一次门判决。**任何失败都不许静默**（返回记录，写不进去就抛给调用方）。

    ⚠️ 2026-09-19 修：**写不进去也不许把调用方的判决吞掉**。
    这个函数挂在**所有路径**的门上（含 CLI / 外部 agent），而它是在
    `raise SystemExit("[STORYBOARD-REJECT] …")` **之前**调的 —— 早先直接让
    `write_text` 抛（项目目录只读、磁盘满、被安全策略拦下）会把
    **那句最该看到的诊断**换成一段 Python traceback（本项目最忌"诊断还是错的"）。
    ⇒ 现在：**告警 + 继续**（一条旁录不值得毁掉主判决），判决本身照旧走 stdout/异常。
    """
    rec = {
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ep": int(ep or 1),
        "where": str(where),                 # 哪个入口判的（排障要看得出）
        "blocked": bool(blocked),            # True = 拦下了；False = 放行（人工模式）
        "fatal": [str(x) for x in (fatal or [])],
        "tips": [str(x) for x in (tips or [])],
    }
    try:
        p = gate_report_path(root, ep)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:                   # noqa: BLE001
        # **必须响亮**：静默失败会让前端"看不到红字"，而人以为"这门没判"
        print("[gates] ⚠️ 判决无法落盘（%s: %s）—— 判决本身已在上面的日志里"
              % (type(e).__name__, str(e)[:200]))
    return rec


def gate_report(root: Path, ep: int | None = None) -> dict:
    """读最近一次门判决；没有/坏了 → `{}`（**绝不假报"通过"**）。"""
    p = gate_report_path(root, ep)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:                        # noqa: BLE001
        return {}


# ─── 1. media_gate：媒体阶段状态机 ───────────────────────────────────────────

def media_gate(action: str, m: dict, ep: int | None = None,
               root: Path | None = None) -> tuple[bool, str]:
    """动作是否合法。返回 (ok, why)。

    ★ M2（2026-09-17）：**按集读状态**。改造前读的是一维 `phases` / `media_loop`，
    于是第 2 集会拿**第 1 集**的 `complete` 放行 —— 正是 M2 的验收负样本。
    `ep` 不传则取 manifest 的 `episode_index`（既有调用方零改动）。
    `root` 只在**评审上限**那一条上用到（要落盘累计拦截次数）；不传则只在内存计数，
    旧调用方与测试行为零变化。
    """
    if ep is None:
        ep = int(m.get("episode_index", 1) or 1)
    ml = media_loop_of(m, ep)
    # `review` 仍是一维：它在媒体门之前由 `reconcile_manifest(ep)` 按**本集的**
    # `reviewer/review_ep{N}.md` 重新解析补记（见那里的注释）；换集时 `bind_episode`
    # 会清掉它，所以不会有"上一集的 pass 放行这一集"。
    rev = m.get("review") or {}
    if action in ("render", "render_episode"):
        # 8 个角色全绿才允许进媒体链。旧版只查 scenedesigner，
        # 于是"只跑了 2/8 就渲染"也能过闸——那是编排完整性事故的直接原因。
        # ⚠️ 名单**按项目模式取**（`gate_roles_for`）：默认模式恒等于 `GATE_ROLES`
        # （7 个，行为一字不变）；from_script 下 6 个（多的那个由预置产物满足）。
        # `root` 不传时回落常量 —— 既有调用方（部分测试）零改动。
        _gr = gate_roles_for(root, ep) if root is not None else GATE_ROLES
        missing = [r for r in _gr if phase_of(m, r, ep) != "complete"]
        if missing:
            return False, ("第 %d 集创作链未完成（缺 %s），不能渲染" % (ep, "、".join(missing))
                           if ep > 1 else
                           "创作链未完成（缺 %s），不能渲染" % "、".join(missing))
        # ★ 2026-09-19：**人工模式**（前端路径，`config.HUMAN_IN_CHARGE`）下这条不生效
        #   —— 判断权在人：reviewer 照跑、照出报告，但**不拿它当闸**。
        #   理由与边界见 `config.HUMAN_IN_CHARGE` 的注释：`media_gate` 是唯一入口，
        #   全局放开会把**全自动**（外部 agent，没有人看）唯一的质量保护一起拆掉，
        #   所以它是一个由前端显式打开的模式开关，默认关。
        #   ⛔ 这个开关**只给前端**：外部 agent 拿它去放行进渲染，等于把上面那句话作废。
        if not config.HUMAN_IN_CHARGE and not (rev.get("passed")
                                              or rev.get("force_passed")):
            # ★ 2026-09-29 人定口径（用户原话「超 2 次就写 force_passed 让门放行」）：
            #   渲染被评审门拦下**超过** `MAX_REVISIONS_PER_PHASE`（默认 2）次
            #   → 写 `review.force_passed` **放行**，并把"这一版是带着未通过评审渲的"
            #   连同评审仍未消化的条目一起打**响**。
            #   为什么必须补：这道保险原先**空转** —— `revision_exhausted()` 全仓只有
            #   测试调用、`force_passed` 有 5 处读却**零写入点**，于是评审反复不过没有出口，
            #   真实结局是一直重派到撞墙钟预算、`rc=0` 静默收工不出片（0929 ep2 就是这么丢的）。
            # ★ 计数源是**门自己**（`review_blocks`，按集、落盘累计）。不用 `revision_counts`：
            #   2026-09-29 实测三个真实项目（xianxia-60s-0929 / 60s2 / 60s3）它**全是 null** ——
            #   那要靠角色调 `record_phase(count=True)` 才累加，而实际重派是**调度器 LLM 自己**
            #   发起的，不经过 `guards.reset_from()`（那函数只有 HITL 手动调用）。照它判，新保险照样空转。
            cap = int(config.MAX_REVISIONS_PER_PHASE)
            rb = m.setdefault("review_blocks", {})
            n = int(rb.get(str(int(ep))) or 0) + 1
            rb[str(int(ep))] = n
            if root is not None:
                save_manifest(Path(root), m)   # 不落盘 = 跨进程不累计 ⇒ 又是空转
            if n > cap:
                m.setdefault("review", {})["force_passed"] = True
                if root is not None:
                    save_manifest(Path(root), m)
                print("[media-gate] !! 渲染已被评审拦下 %d 次 > 上限 %d → 记 force_passed "
                      "**放行**：这一版是带着未通过的评审渲染的。评审仍未消化的条目："
                      % (n, cap))
                for _r in (rev.get("reasons") or [])[:5]:
                    print("   · " + str(_r)[:200])
                if not (rev.get("reasons") or []):
                    # 「不许静默」的边界情形：判定块**根本没解析出来**时 reasons 是空的，
                    # 上面那行"仍未消化的条目："后面会一个字都不打 ⇒ 放行看起来像走了过场。
                    # 实测：huashan-duel-v4-0928 ep2 的 reviewer 只写了散文 `**pass = false**`。
                    print("   · （**评审报告里没有机器判定块**，程序读不出条目与 rerun 目标 —— "
                          "上面那句「未消化」只能按「整份判决都未消化」处理。"
                          "去补 reviewer 产物末尾的 ```yaml 判定块，或人工核一遍再放行。）")
            else:
                return False, ("评审未通过（无 pass: true），不能渲染 —— 本集第 %d/%d 次拦截，"
                               "超过 %d 次本门将记 force_passed 放行" % (n, cap, cap))
        if ml.get("rendered") and not ml.get("pending_revision"):
            return False, "已渲染且无待修订，无需重渲"
        return True, ""
    if action in ("review", "qc"):
        if not ml.get("rendered"):
            return False, "尚未渲染，无可审内容"
        return True, ""
    if action == "revise":
        if not (rev.get("p0") or []):
            return False, "无 P0，不需要重拍"
        return True, ""
    if action == "finalize":
        if not ml.get("rendered"):
            return False, "尚未渲染，不能收尾"
        return True, ""
    return False, "未知动作 " + str(action)


# ─── 2. pre_dispatch：派发前置 + reviewer 锁 ─────────────────────────────────

def pre_dispatch(role: str, m: dict, ep: int | None = None) -> tuple[bool, str]:
    if role not in OUTPUTS:
        return False, "未知角色 " + str(role)
    if ep is None:
        ep = int(m.get("episode_index", 1) or 1)
    for req in PREREQ.get(role, []):
        if phase_of(m, req, ep) != "complete":
            return False, "前置未满足：%s 未完成（%s 需要）" % (req, role)
    return True, ""


def revision_exhausted(role: str, m: dict) -> bool:
    """该角色是否已**用尽**重跑机会（超出上限才为真）。

    计数语义：`revision_counts[role]` = 已回退次数。达到上限（3）时仍允许最后
    跑一次，**超过**上限才跳过/强制放行——否则第 3 次回退会被跳过，产物反而
    落空（实测：scriptwriter 被 skip 后物化守卫判 failed，整轮卡在媒体门前）。
    """
    rc = m.get("revision_counts") or {}
    return int(rc.get(role, 0)) > config.MAX_REVISIONS_PER_PHASE


# ─── 3. post_validate：物化守卫（磁盘实况优先）───────────────────────────────

def artifact_fresh(path: Path, since: float | None) -> bool:
    """产物是不是 `since`（epoch 秒）之后写的。`since=None` → 不判，返回 True。

    为什么需要它（2026-10-01）：正规"打回重做"会先 `reset_from(root=...)` 把旧产物
    移进 `.rerun_backup/`，所以那条路上"文件还在"确实等于"本轮新写的"。但**没走那条
    路**的调用方（当天是外部 agent 临时写的恢复驱动）让角色重跑时，盘上留着上一轮的
    同名文件，`exists()` 照样成立 ⇒ 判 complete、判决照旧从旧文件解析、据此写
    force_passed 放行渲染。实测：`review_ep1.md` 的 mtime 停在 15:33:33、md5 未变，
    两轮 reviewer（175s / 137s）一次都没碰它，日志却两次都写 "OK"。

    1 秒容差是给文件系统时间粒度留的余量，不是放宽判据。
    """
    if since is None:
        return True
    try:
        return path.stat().st_mtime >= since - 1.0
    except OSError:
        return False


def post_validate(role: str, m: dict, root: Path,
                  wrote: list[str] | None = None,
                  ep: int | None = None,
                  since: float | None = None) -> tuple[bool, str, str]:
    """返回 (ok, why, path)。账本没记但盘上有非空产物 → 承认（不假失败）。

    `ep` 不传则取 manifest 的 `episode_index`（既有调用方零改动）。
    `since` 传了就必须是本轮写的（见 `artifact_fresh`）；不传行为一字不变。
    """
    if ep is None:
        ep = int(m.get("episode_index", 1) or 1)
    for w in (wrote or []):
        p = root / w
        try:
            if (p.exists() and len(p.read_text(encoding="utf-8").strip()) > 0
                    and artifact_fresh(p, since)):
                return True, "", w
        except UnicodeDecodeError:
            if artifact_fresh(p, since):
                return True, "", w          # 二进制产物：存在即算（但仍要本轮写的）
        except Exception:  # noqa: BLE001
            continue
    rel = out_path(role, ep)
    if not rel:
        return False, "角色无声明产物", ""
    p = root / rel
    if not p.exists():
        # 兜底：模型常把产物直接写到项目根（实测写成 /director.md，
        # 而非约定的 /director/director.md）。磁盘实况优先——找到就挪到
        # 约定路径，避免因为路径写歪就整角色重跑一遍。
        flat = root / Path(rel).name
        if flat.exists() and flat.stat().st_size > 0:
            p.parent.mkdir(parents=True, exist_ok=True)
            flat.replace(p)
    if not p.exists() and "{N}" not in OUTPUTS.get(role, ""):
        # ★ 更常见的第二种"名字写歪"（2026-09-17 真机验收抓到）：
        #   **全剧级**角色（worldbuilder / assetdesigner / plotdesigner / director）
        #   被模型擅自加了集号后缀 —— 实测产出 `worldbuilder/worldbuilder_ep1.md`，
        #   而声明路径是 `worldbuilder/worldbuilder.md` ⇒ 物化守卫判"未物化"
        #   ⇒ **整条链白跑 20 分钟**。
        #   根因是我在 system prompt 里把它无条件描述成"集级产物"（已修）；这里再加一层
        #   磁盘兜底：**声明路径不存在、但"加了本集后缀"的同名文件在盘 → 挪回来**。
        #   与上面 root-flat 兜底同一条原则（磁盘实况优先，不让一次改名毁掉一轮）。
        stray = p.parent / (p.stem + "_ep%d" % int(ep) + p.suffix)
        if stray.exists() and stray.stat().st_size > 0:
            print("[guards] ⚠️ %s 是全剧级产物，但模型写成了 %s → 已按磁盘事实挪回声明路径"
                  % (role, stray.name))
            p.parent.mkdir(parents=True, exist_ok=True)
            stray.replace(p)
    if not p.exists():
        # ★ **串集告警**（2026-09-17 M2）：第 N>1 集的声明路径不存在，但**旧名文件在盘**
        #   —— 这是"角色写错集"的特征签名（它按旧契约写回了第 1 集的文件）。
        #   后果极重：**第 1 集的产物被静默覆盖**，而这一集只报"产物缺失"。
        #   必须响亮说出来，否则日志里只看到"阶段未 complete"，查不出原因。
        legacy = _OUTPUTS_LEGACY.get(role)
        if legacy and int(ep) > 1 and (root / legacy).exists():
            print("[guards] ⚠️ %s：第 %d 集未物化（%s 不存在），但**旧名文件 %s 在盘** "
                  "→ 该角色很可能把第 %d 集的产物**写回了第 1 集的文件**（串集！）。"
                  "请检查它的产物路径文案是否写死旧名。"
                  % (role, int(ep), rel, legacy, int(ep)))
        return False, "未物化产物（%s 不存在），必须写盘" % rel, ""
    try:
        if len(p.read_text(encoding="utf-8").strip()) == 0:
            return False, "产物为空（%s）" % rel, ""
    except UnicodeDecodeError:
        pass
    if not artifact_fresh(p, since):
        # 判据要说清"怎么修"，不然调用方只会照着"未物化"去查一个存在的文件。
        return False, ("产物未被本轮改写（%s 的 mtime 早于本轮起点）"
                       "——重跑该角色前先调 reset_from(root=...) 把旧产物移进 "
                       ".rerun_backup/" % rel), ""
    if role == "plotdesigner":
        # ★ 全剧目录**集集要有条目**（2026-10-05 实测，判据与理由见
        #   `roles.catalog_missing_episodes`）：文件非空 ≠ 目录可用。第 4 轮那份
        #   10075 字的目录只数出 4 个集条目（要 10 集），旧判据照常放话"合格"，
        #   于是角色带着"我写完了"的反馈重写 45 分钟、整条链零出片。
        #   brief 读不到 / 单集项目 ⇒ 一律不判（行为与改造前一字不变）。
        try:
            from .roles import catalog_missing_episodes
            _n = int((load_brief(root) or {}).get("episodes") or 1)
            _miss = catalog_missing_episodes(p.read_text(encoding="utf-8"), _n)
        except Exception:      # noqa: BLE001 -- 判据本身坏不该把整条链判死
            _n, _miss = 1, []
        if _miss:
            return False, ("全剧目录缺集：brief 写的是 %d 集，目录里只数出 %d 个集条目，"
                           "**缺第 %s 集**（共缺 %d 集）。修法：按「全剧目录写法」的体量口径"
                           "（每集 150–300 字、全文 ≤7000 字、不超过 3 卷、"
                           "**不写场表与节拍表**）**一次 `write_file` 交全 %d 集**，"
                           "⛔ 不要逐集 `edit_file` 追加——那是上一轮空转 45 分钟的成因。"
                           % (_n, _n - len(_miss), "、".join(str(i) for i in _miss[:12]),
                              len(_miss), _n)), rel
    return True, "（账本未记但产物在盘，按声明路径对账承认）", rel


# ─── 4. TokenBreaker：run/role 预算 ──────────────────────────────────────────

class TokenBreaker:
    """超预算就停：今天两次事故（40M / 20M）都是因为没有它。

    计数持久化在 manifest，重启/续跑也认账。
    """

    def __init__(self, m: dict):
        self.m = m
        u = m.setdefault("token_usage", {"run": 0, "roles": {}})
        self.usage = u

    def add(self, role: str, tokens: int) -> tuple[bool, str]:
        if not tokens:
            return True, ""
        self.usage["run"] = int(self.usage.get("run", 0)) + tokens
        roles = self.usage.setdefault("roles", {})
        roles[role] = int(roles.get(role, 0)) + tokens
        if self.usage["run"] >= config.TOKEN_BUDGET_RUN:
            return False, ("[TOKEN-BREAKER] 本次运行已用 %d tokens（预算 %d）——停止派发，"
                           "请检查是否陷入循环" % (self.usage["run"], config.TOKEN_BUDGET_RUN))
        if roles[role] >= config.TOKEN_BUDGET_ROLE:
            return False, ("[TOKEN-BREAKER] 角色 %s 已用 %d tokens（预算 %d）——停止重跑该角色"
                           % (role, roles[role], config.TOKEN_BUDGET_ROLE))
        return True, ""


# ─── 5. reconcile：收尾前对账 ────────────────────────────────────────────────

def reconcile(m: dict, root: Path, ep: int | None = None) -> tuple[bool, list[str]]:
    """finalize 前的确定性对账：产物齐不齐、成片在不在、时长够不够。

    ★ M2：**按集读状态**（`ep` 不传则取 manifest 的 `episode_index`）。
    """
    problems: list[str] = []
    if ep is None:
        ep = int(m.get("episode_index", 1) or 1)
    for role in ROLES:
        if phase_of(m, role, ep) != "complete":
            problems.append("第 %d 集阶段未完成：%s" % (ep, role) if ep > 1
                            else "阶段未完成：%s" % role)
            continue
        # ★ 用 `resolve_path`（新名优先、**旧名回退**）：历史项目的集级产物仍是
        #   旧名（`scenedesigner.md` / `review.md`）—— 直接拼新名会把它们全判"缺失"。
        p = resolve_path(root, role, ep)
        if not p.exists():
            problems.append("产物缺失：%s" % out_path(role, ep))
    final = root / "media" / ("ep" + str(ep)) / "episode_final.mp4"
    if not final.exists():
        problems.append("成片不存在：media/ep%d/episode_final.mp4" % ep)
    return (not problems), problems
