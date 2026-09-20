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
    for role in GATE_ROLES:
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
                          % (out_path("reviewer"), rev["passed"]))
            else:
                # ★ 产物存在却解析不出判定 → 媒体门必然报「评审未通过（无 pass: true）」，
                #   而报告里可能明明写着 `pass: true`（自相矛盾、极难排查）。
                #   原先这里是**完全静默**的（连异常都被 `except: pass` 吞掉）—— 必须显式告警。
                print("[guards] ⚠️ %s 存在但解析不出判定块（pass/rerun/reasons）"
                      "→ 评审门会判「未通过」，媒体链会被拦下。请检查该文件末尾的判定块格式。"
                      % out_path("reviewer"))
        except Exception as e:  # noqa: BLE001
            print("[guards] ⚠️ %s 判定解析异常：%s"
                  % (out_path("reviewer"), str(e)[:120]))
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

def media_gate(action: str, m: dict, ep: int | None = None) -> tuple[bool, str]:
    """动作是否合法。返回 (ok, why)。

    ★ M2（2026-09-17）：**按集读状态**。改造前读的是一维 `phases` / `media_loop`，
    于是第 2 集会拿**第 1 集**的 `complete` 放行 —— 正是 M2 的验收负样本。
    `ep` 不传则取 manifest 的 `episode_index`（既有调用方零改动）。
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
        missing = [r for r in GATE_ROLES if phase_of(m, r, ep) != "complete"]
        if missing:
            return False, ("第 %d 集创作链未完成（缺 %s），不能渲染" % (ep, "、".join(missing))
                           if ep > 1 else
                           "创作链未完成（缺 %s），不能渲染" % "、".join(missing))
        # ★ 2026-09-19：**人工模式**（前端路径，`config.HUMAN_IN_CHARGE`）下这条不生效
        #   —— 判断权在人：reviewer 照跑、照出报告，但**不拿它当闸**。
        #   理由与边界见 `config.HUMAN_IN_CHARGE` 的注释：`media_gate` 是唯一入口，
        #   全局放开会把**全自动**（外部 agent，没有人看）唯一的质量保护一起拆掉，
        #   所以它是一个由前端显式打开的模式开关，默认关。
        if not config.HUMAN_IN_CHARGE and not (rev.get("passed")
                                              or rev.get("force_passed")):
            return False, "评审未通过（无 pass: true），不能渲染"
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

def post_validate(role: str, m: dict, root: Path,
                  wrote: list[str] | None = None,
                  ep: int | None = None) -> tuple[bool, str, str]:
    """返回 (ok, why, path)。账本没记但盘上有非空产物 → 承认（不假失败）。

    `ep` 不传则取 manifest 的 `episode_index`（既有调用方零改动）。
    """
    if ep is None:
        ep = int(m.get("episode_index", 1) or 1)
    for w in (wrote or []):
        p = root / w
        try:
            if p.exists() and len(p.read_text(encoding="utf-8").strip()) > 0:
                return True, "", w
        except UnicodeDecodeError:
            return True, "", w          # 二进制产物：存在即算
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
