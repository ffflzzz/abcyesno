# -*- coding: utf-8 -*-
"""reviewer 结论的**机器可读部分**：解析与规范化。

契约（见 skills/packs/<pack>/reviewer/SKILL.md「结构化判定」段）：
评分报告之后追加一个围栏块，YAML 或 JSON 二选一：

```yaml
pass: false
rerun: [scriptwriter]
reasons:
  - 第二幕缺 brief 要求的「老人递上纸人」动作
```

- 人看评分报告，机器只读这个块，两者不冲突；
- `rerun` 只填**最上游**那个角色，下游由图的静态边自动重跑；
- `reason_owners`（可选）：每条 reason 的责任角色，与 `reasons` 同序。编排层用
  `resolve_target()` 拿它与 `rerun` 交叉校验——防止"问题在上游、`rerun` 却填下游"
  导致整轮白跑（2026-09-10 实测：一次误填 `plotdesigner`，白烧 13 分钟）；
- 契约的**唯一真源**是 `skills/packs/<pack>/reviewer/SKILL.md`（各包可不同）；
- 本模块只做解析与规范化，不做主观判断（主观判断留给 reviewer 模型）。
"""
from __future__ import annotations

import json
import re

from .guards import ROLES

# 允许被回退的角色：8 个角色里除 reviewer 自己（reviewer 回退到自己 = 死循环）
RERUN_ROLES: tuple[str, ...] = tuple(r for r in ROLES if r != "reviewer")

_FENCE_RE = re.compile(r"```(?:ya?ml|json)?\s*(.*?)```", re.S)

_TRUE = {"true", "yes", "pass", "passed", "是", "通过", "1"}
_FALSE = {"false", "no", "fail", "failed", "否", "不通过", "打回", "0"}


def _as_bool(v) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in _TRUE:
            return True
        if s in _FALSE:
            return False
    return None


def _as_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        # 支持 "scriptwriter" / "scriptwriter, dialogue" / "[a, b]"
        s = v.strip().strip("[]")
        parts = re.split(r"[,，、\s]+", s) if s else []
        return [p for p in (x.strip().strip("\"'") for x in parts) if p]
    if isinstance(v, (list, tuple, set)):
        out = []
        for x in v:
            if isinstance(x, str) and x.strip():
                out.append(x.strip().strip("\"'"))
            elif isinstance(x, dict):
                # 旧格式 issues: [{level, desc}, ...] → 取可读描述
                d = x.get("desc") or x.get("reason") or x.get("message")
                if isinstance(d, str) and d.strip():
                    lvl = x.get("level")
                    out.append(("%s " % lvl if lvl else "") + d.strip())
        return out
    return []


def _norm_role(r: str) -> str:
    """归一化角色名：容忍 / 前缀、大小写、中划线/下划线、中文别名。"""
    s = str(r).strip().strip("/").lower().replace("-", "").replace("_", "")
    alias = {
        "导演": "director", "世界观": "worldbuilder", "世界观构建": "worldbuilder",
        "角色设计": "assetdesigner", "资产": "assetdesigner",
        "剧情": "plotdesigner", "编剧": "scriptwriter", "剧本": "scriptwriter",
        "对白": "dialogue", "分镜": "scenedesigner", "评审": "reviewer",
    }
    if s in alias:
        return alias[s]
    for role in ROLES:
        if s == role.lower():
            return role
    return ""


def normalize_rerun(rerun, allowed: tuple[str, ...] = RERUN_ROLES) -> list[str]:
    """去重 + 归一化 + 按图的角色顺序排序，只保留允许的角色。"""
    got: set[str] = set()
    for raw in _as_list(rerun):
        role = _norm_role(raw)
        if role and role in allowed:
            got.add(role)
    return [r for r in ROLES if r in got]


def _as_bool_verdict(v) -> bool | None:
    """`verdict:` 那一行的取值词表（模型爱写 pass/fail 而不是 true/false）。"""
    if isinstance(v, bool):
        return v
    s = str(v or "").strip().lower()
    if s in ("pass", "passed", "ok", "true", "yes", "通过", "判通过", "合格"):
        return True
    if s in ("fail", "failed", "false", "no", "reject", "不通过", "未通过", "不合格"):
        return False
    return None


def _from_mapping(data) -> dict | None:
    """把已解析成映射的判定块转成标准结果；**两种格式都不成立时返回 None**。

    单一入口：围栏块与裸块两条路径**共用同一套规范化**，避免两份判据漂移
    （本项目已有的教训：同一判据两份实现必然漂移）。
    兼容旧格式（`needs_revision` + `revision_target` + `issues`）。
    """
    if not isinstance(data, dict):
        return None
    keys = {str(k).strip().lower(): v for k, v in data.items()}
    # ── 同义键归一（2026-09-30 实测）：xianxia-vfx-action 的 reviewer 连着两份产物写成
    #   `verdict: pass` / `blocking: []` / `rerun_role: null`，而程序只认
    #   `pass` / `reasons` / `rerun` ⇒ **一份判了"通过"的评审被读成"读不出判定块"**，
    #   媒体链被假拦（比误放更难查，因为日志只说"未通过"）。
    #   两侧都补：解析器认同义词（打 `synonym` 标记以便如实告警），包契约里把块形写死。
    syn = False
    if "pass" not in keys and "verdict" in keys:
        v = _as_bool_verdict(keys.get("verdict"))
        if v is not None:
            keys["pass"] = v
            syn = True
    if "reasons" not in keys and "blocking" in keys:
        keys["reasons"] = keys.get("blocking")
        syn = True
    if "rerun" not in keys and "rerun_role" in keys:
        keys["rerun"] = keys.get("rerun_role")
        syn = True
    if not ({"pass", "rerun", "reasons"} & set(keys)):
        # 兼容旧格式（历史产物 / 其他类型包）：needs_revision + revision_target
        if "needs_revision" in keys:
            need = _as_bool(keys.get("needs_revision"))
            return {
                "pass": not bool(need),
                "rerun": normalize_rerun(keys.get("revision_target")),
                "reasons": _as_list(keys.get("issues")) or
                           _as_list(keys.get("reasons")),
                "raw": data,
                "legacy": True,
            }
        return None
    passed = _as_bool(keys.get("pass"))
    if passed is None:
        # pass 缺失：有 rerun 视为不通过，否则无法判定 → 交给调用方
        passed = not _as_list(keys.get("rerun"))
    if syn and _as_list(keys.get("reasons")):
        # 同义键这一路 `verdict` 与 `blocking` 是配对着写的：有阻断项就不算通过。
        # （标准词表那一支不动 —— 它按契约把 `pass` 当最终裁决，改它会波及老项目。）
        passed = False
    res = {
        "pass": bool(passed),
        "rerun": normalize_rerun(keys.get("rerun")),
        # 每条 reason 的责任角色（与 reasons 同序）。可选字段：老产物没有它，
        # 此时 resolve_target 完全退化为旧行为（只认 rerun），零风险。
        "owners": normalize_rerun(keys.get("reason_owners")
                                 or keys.get("reasonowners")
                                 or keys.get("owners")),
        # 阻断性问题（会让成片出错）
        "reasons": _as_list(keys.get("reasons")),
        # 非阻断建议（不影响成片正确性，不触发回退）
        "advisory": _as_list(keys.get("advisory")
                             or keys.get("suggestions")
                             or keys.get("notes")),
        "raw": data,
    }
    if syn:
        res["synonym"] = True
    return res


# 裸块（无代码围栏）行式解析：只认 ASCII 冒号，中文全角「：」不匹配
# ——正文里的「评分：9/10」这类行因此不会污染判定块。
_BARE_KV_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$")
_BARE_ITEM_RE = re.compile(r"^\s*-\s+(.*)$")

#: 判定块里程序认识的键。**只有走"加粗/行内代码"这条宽松路径的键要在白名单内**
#: ——散文里 `**注意: xxx**` 这种行不能被判成键，否则等于让解析器猜。
_VERDICT_KEYS = ("pass", "rerun", "reasons", "reason_owners", "owners", "advisory",
                 "suggestions", "notes", "verdict", "blocking", "rerun_role",
                 "needs_revision", "revision_target", "issues")


def _bare_kv(ln: str) -> tuple[str, str] | None:
    """把一行"键行"归一化成 `(小写键, 值)`；认不出返回 None（**不猜**）。

    两种写法（都是实测，见 `_bare_block_lines` 的事故记录）：
      · 标准裸行 `rerun: [scenedesigner]` —— 走 `_BARE_KV_RE`，**行为与旧版一字不变**；
      · 散文里加粗/行内代码写的键 `**pass: false**`、`` `pass = false` `` ——
        剥掉行首项目符号与两端 `*`/反引号后再试，且**键必须在 `_VERDICT_KEYS` 白名单里**。
    """
    s = ln.strip()
    m = _BARE_KV_RE.match(s)
    if m:
        return m.group(1).lower(), m.group(2).strip()
    t = re.sub(r"^[-*+]\s+", "", s).strip(" `*")
    m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*(.*)$", t)
    if not m or m.group(1).lower() not in _VERDICT_KEYS:
        return None
    return m.group(1).lower(), m.group(2).strip()


def _bare_block_lines(text: str) -> list[str]:
    """收集**文末的裸 `key: value` 块行**（没有代码围栏时的兜底）。

    ## 为什么需要（2026-09-14 实测事故）

    契约要求判定块写在 ```yaml 围栏里，但模型**偶发漏写围栏**：22 个真实项目的
    `reviewer/review.md` 里，**20 个有围栏、2 个一个反引号都没有**（只有 `---`
    加裸行）。旧实现只扫围栏 → `parse_decision` 返回 `None` → `reconcile_manifest`
    的 `if dec:` 整段被跳过 → **manifest 里根本没有 `review` 键** →
    `media_gate("render")` 报「评审未通过（无 pass: true）」→
    **创作链 20 分钟全绿、四道输入门全过，却不出片**，而且报告与判定自相矛盾
    （review.md 里明明写着 `pass: true`）。

    ★ 2026-10-08 补两条（实测 `yuxuan-duanfeng-1007` ep2，代价是**丢了评审的 4 条打回**）：
      ① **块内允许空行**。那一轮的判定写成形散落在文末：
         `rerun: [scenedesigner]` / 空行 / `reasons:` / 空行 / `advisory:`，
         旧实现"遇空行即停"⇒ 只收到最后那组 advisory ⇒ `_from_mapping` 里
         pass/rerun/reasons 一个都没有 ⇒ **整份判决读不出来**（返回 None）
         ⇒ 驱动器不重派、媒体门只报"未通过"。
      ② **加粗/行内代码的键也认**（`**pass: false**`，同一轮里 pass 就是这么写的；
         旧版 `huashan-duel-v4-0928` ep2 的 `**pass = false**` 同形）。

    从文末往上扫。⛔ 不做任何猜测——非空且不匹配的行照旧**立即停**，
    找不到合格块仍返回空列表，由 `_from_mapping` 再判一次（键不成立同样返回 None）。
    """
    lines = text.splitlines()
    i = len(lines)
    while i > 0 and not lines[i - 1].strip():
        i -= 1
    block: list[str] = []
    blanks = 0
    while i > 0:
        ln = lines[i - 1]
        if not ln.strip():
            # ★ 空行**跳过但不终止**（旧实现在这里 break，把散落多行的块砍成半截）。
            blanks += 1
            i -= 1
            continue
        if _bare_kv(ln) is not None or _BARE_ITEM_RE.match(ln):
            block.extend([""] * blanks)      # 保住原块的相对形状（解析器忽略空行）
            block.append(ln)
            blanks = 0
            i -= 1
            continue
        break
    while block and not block[-1].strip():
        block.pop()
    block.reverse()
    return block


def _parse_bare_kv(lines: list[str]) -> dict:
    """极简 YAML 子集解析：`key: value` / `key:` + 缩进 `- item`。

    不依赖 PyYAML（它是可选依赖），也**不引入第二套语义**——产物交回
    `_from_mapping` 走与围栏块完全相同的规范化。
    """
    data: dict = {}
    cur: str | None = None
    for ln in lines:
        kv = _bare_kv(ln)
        if kv:
            key, val = kv
            if val:
                data[key] = val
                cur = None
            else:
                data[key] = []
                cur = key
            continue
        m2 = _BARE_ITEM_RE.match(ln)
        if m2 and cur:
            data[cur].append(m2.group(1).strip())
    return data


def _bare_json_block(text: str, limit: int = 5) -> dict | None:
    """文末几行里找**裸 JSON 判定对象**（模型漏围栏、且把块写成一行 JSON）。

    实测来源：`projects/birthday/reviewer/review.md` **整个文件就是一行 JSON**，
    连 `---` 都没有 → 围栏路径与裸 `key: value` 路径**都取不到**（它用的是 legacy
    的 `needs_revision` / `revision_target` / `issues` 字段）。
    只收 `{` 开头且能 `json.loads` 成 dict 的候选，键的合法性仍由 `_from_mapping` 把关。
    """
    tail = [ln for ln in text.splitlines() if ln.strip()][-limit:]
    for c in [text.strip()] + list(reversed(tail)):
        if not c.startswith("{"):
            continue
        try:
            data = json.loads(c)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, dict):
            return data
    return None


def parse_decision(text: str) -> dict | None:
    """从 reviewer 产物文本里抽出结构化判定。

    返回 {"pass": bool, "rerun": list[str], "reasons": list[str], "raw": dict}
    找不到任何可解析块时返回 None（**不猜测**——由调用方决定回退或重试）。
    """
    if not text:
        return None
    for block in _FENCE_RE.findall(text):
        data = None
        try:
            data = json.loads(block)
        except Exception:  # noqa: BLE001
            try:
                import yaml  # 可选依赖；缺了就只支持 JSON
                data = yaml.safe_load(block)
            except Exception:  # noqa: BLE001
                data = None
        if not isinstance(data, dict):
            # ★ **围栏在、内容 YAML 读不出来**时，先把这个块本身交给行解析兜一遍。
            #   2026-10-06 实测（`ice-spring-bridge-duel` 第 3 集）：评审在双引号字符串里
            #   引用了正则 `(\d+)`，YAML 认 `\d` 是非法转义 ⇒ **整块**读不出来 ⇒
            #   `media_gate` 判「未通过」⇒ 创作链 32 分钟、七件产物齐全、
            #   两道门全过，**零出片**。判定本身是清楚的（`pass: false` + 两条理由），
            #   废在一个转义字符上。行解析不认转义、只认 `key: value` 与 `- 项`，
            #   正好吃得下这种写法；键的合法性仍由 `_from_mapping` 把关。
            lenient = _parse_bare_kv(block.splitlines())
            res = _from_mapping(lenient) if lenient else None
            if res:
                res["lenient"] = True      # 调用方可据此如实告警，但不阻断
                return res
            continue
        res = _from_mapping(data)
        if res:
            return res
    # ── 兜底：文末**裸 key: value 块**（模型漏写代码围栏时，见 `_bare_block_lines`）。
    #   顺序在围栏之后 —— 契约路径优先，兜底只**增加**可解析性，不改既有行为。
    #   解析成功时打 `unfenced` 标记：调用方可据此**如实告警**（模型没按契约写围栏），
    #   但**不因此阻断**——判定本身是有效的。
    bare = _parse_bare_kv(_bare_block_lines(text))
    if bare:
        res = _from_mapping(bare)
        if res:
            res["unfenced"] = True
            return res
    data = _bare_json_block(text)
    if data:
        res = _from_mapping(data)
        if res:
            res["unfenced"] = True
            return res
    return None


def route_target(rerun: list[str]) -> str:
    """回退目标 = rerun 里**最上游**的角色（下游由静态边自动重跑）。"""
    for role in ROLES:
        if role in rerun:
            return role
    return ""


def resolve_target(dec: dict) -> str:
    """回退目标：综合 `rerun` 与 `reason_owners`，取**更上游**的那个。

    ## 为什么需要交叉校验（2026-09-10 实测事故）

    评审把矛盾描述成 `director` 表格里 S04/S05 光列的问题，`rerun` 却填了
    `plotdesigner`（下游）。而下游角色**没有权限修改上游的文件**——它只能在自己的
    产物里写一段"FIX-1：请把 S04 光列改成…"的说明。于是那一轮 13 分钟**完全白跑**：
    `director` 的文件纹丝未动，下一轮评审发现原样未改，才回头填 `director`。

    根因是 SKILL 的判据只按"问题类型"映射角色，没定义"问题所在产物的所有者"。
    SKILL 已补上该判据；本函数是**确定性兜底**——防止模型再次填错。

    规则（保守，避免过度回退）：
      · 没有 `owners` → 完全保持旧行为（只认 `rerun`），零风险兼容老产物；
      · 两者都有 → 取 `ROLES` 顺序里**更靠前**（更上游）的那个；
      · 只有 `owners` → 用 `owners` 的最上游。
    """
    rerun_target = route_target(dec.get("rerun") or [])
    owner_target = route_target(dec.get("owners") or [])
    if not owner_target:
        return rerun_target
    if not rerun_target:
        return owner_target
    return (rerun_target
            if ROLES.index(rerun_target) <= ROLES.index(owner_target)
            else owner_target)


def normalize_pass(dec: dict) -> bool:
    """规范化 `pass`：模型填 false、却**没给出任何阻断理由**时，视为通过。

    ## 为什么需要（2026-09-10 缺陷分级）

    评审有时把"建议"也当成打回理由（称呼不统一、措辞不一致这类）。这类问题写进
    `advisory` 就不阻断；但模型可能仍填 `pass: false`。此时若照旧回退，会为一句
    文案统一白烧一整轮（实测单轮 13–28 分钟）。

    ## 规则（保守，不会误放行）

      · `pass: true`                → 通过
      · 有 `reasons`（阻断理由）    → 照常打回（尊重模型判定）
      · 无 `reasons`、有 `advisory` → **视为通过**（只有建议，不阻断）
      · 两者都空且 `pass: false`    → 仍打回（说不通过却没给理由，保守处理）

    **调用约定**：确定性缺陷检查必须在它之前，并且**必须一起把 `pass` 翻下来**。
    只往 `dec["reasons"]` 里写一条是**无效**的（2026-10-08 更正）——本函数见
    `pass: true` 第一行就返回 True，reasons 一个字都不读。
    旧文档写着"它会把问题写进 reasons，于是本函数不会误放行"，实测那次
    （`yuxuan-duanfeng-1007` ep2：reviewer 判 pass、分镜 0/8 镜有台词）正是靠
    `pass: true` 把违规放了过去。实现见 `roles.enforce_deterministic_verdict`。
    """
    if dec.get("pass"):
        return True
    if (dec.get("reasons") or []):
        return False
    return bool(dec.get("advisory") or [])


# 注（2026-09-10）：此处原有一份 `DECISION_CONTRACT` 契约文本副本，因**零引用**
# 已删除。它比无用更危险——改了它以为生效，实际从未注入给模型（死代码）。
# 结构化判定的**唯一真源**是 `skills/packs/<pack>/reviewer/SKILL.md`；
# 改判据请改 SKILL.md，不要在本模块复制第二份。
