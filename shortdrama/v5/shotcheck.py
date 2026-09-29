# -*- coding: utf-8 -*-
"""分镜表**两层体检**：能数的用代码判，要读懂句子的用小模型判。

为什么分两层（2026-09-29 实测）
--------------------------------
第 1 集 120 秒成片逐帧全量看完，真正的病是**分镜层**的：19 处「另一人作远景虚化
剪影无动作」把对手写成了背景 ⇒ 每镜只有一人动 ⇒ 全片"两人同帧且都在动"只有 15 秒、
真正兵刃接触只有 3 秒。而**所有门全绿**。

于是先写了纯关键词的量表（`scripts/audit_storyboard.py`），它确实拦住了 ep1（1/8），
但立刻暴露两个假阳性：
  · 「逆光**剪影**」是本包场景锚点的**光位词**，不是"把对手写成剪影"；
  · 结尾两镜「缩为一个点」是 brief 的**结局**明写的拉远定格，不是病。
词表要修到既拦住真病又不误伤，成本会一直涨（而且每次改包措辞都要重校）。

⇒ 语义那部分交给模型，但**判定的权力不放给模型**：
  ① 模型只能报「哪一镜 + 哪一类 + 逐字原文」，不许下"过/不过"的结论；
  ② 程序**回头核验那句原文真在本镜的格子里** —— 抄不出来就降级为提醒。
     这条是硬要求：2026-09-28 审稿角色编造了一条不存在的阻断理由，
     白烧一整轮创作链（68 分钟），见 pack reviewer SKILL 的「反幻觉硬要求」。
  ③ 温度 0（`llm.chat_for(temperature=0)`，与静帧 QC 同一条纪律）。

作用域：**本模块目前只是工具**（`scripts/shotcheck.py` 调它），
没有接进 `pipeline` / `series` 的任何生产路径 —— 先拿历史表验判得准不准。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

# ── 可数判据（代码判，零成本、确定性）────────────────────────────────────────
CONTACT = ("格开", "格挡", "交剑", "相交", "对撞", "撞开", "磕", "荡开", "碰上",
           "相抵", "架住", "接下", "硬接", "互撞", "剑锋相", "双剑相")
DODGE = ("侧身避", "闪开", "掠开", "退半步", "后撤", "被震退", "被荡开", "翻身避")
ENV_HIT = ("崖壁", "岩缝", "岩石", "铁索", "山石", "石阶", "青石", "栈道板", "地面", "石台")
ENV_VERB = ("劈", "斩", "犁", "削", "击碎", "崩落", "掀翻", "插下", "刺入", "扫过")
SILENT_MARK = ("（无声", "(无声", "环境音", "无台词")

# ── 语义判据（模型判，必须逐字引用）──────────────────────────────────────────
CODES = {
    "opponent_as_background":
        "这一镜里**应该出场的对手被写成了不动的背景**（例如"
        "「另一人作远景虚化剪影无动作」「站在远处没有动作」）。"
        "注意：「逆光剪影」「人物剪影勾边」这类**光位**描述不算；"
        "结尾拉远定格里两人都小也不算（那是构图，不是一人动一人不动）。",
    "rock_chopping_as_beat":
        "这一镜的**主要动作是破坏环境而不是打对方**（劈崖壁、斩铁索、砍山石、"
        "插进石头），整镜里对方没有被攻击、也没有应招。"
        "注意：作为**双方交招后果**顺带写出的碎石、裂纹不算。",
    "frozen_vfx_pose":
        "这一镜把能量特效写成**定格不动的道具**（举着巨剑/剑罡定住数秒、"
        "光柱里站着不动），而不是一个会推进到对方身上的动作。",
}

JUDGE_SYSTEM = (
    "你是分镜表质检员。只判断给定这一镜有没有下面列出的毛病，"
    "有就报，没有就返回空列表。**必须逐字抄出原文那一段**（不许改写、不许概括、"
    "不许凭印象编）。判不准就不要报。\n"
    "毛病类别与定义：\n"
    + "\n".join("%s：%s" % (k, v) for k, v in CODES.items())
    + "\n\n只输出 JSON，形如 "
      '{"violations":[{"code":"...","quote":"逐字原文","why":"一句话"}]}；'
      "没有问题就输出 {\"violations\":[]}。不要输出别的字。"
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def shot_cells(shot: dict) -> str:
    """一镜所有文本格拼起来（模型看这个，程序也用它核验原文）。"""
    return "\n".join(str(shot.get(k) or "") for k in
                     ("visual", "dialogue", "sfx", "join_note", "tail",
                      "shot_type", "angle", "camera", "scene"))


def quote_verifiable(quote: str, cell_text: str) -> bool:
    """程序那道闸：**逐字引用必须真的在本镜格子里**（只忽略空白差异）。

    引不出原文的判定一律不算阻断 —— 这是审稿角色编造理由那次的直接教训。
    """
    q = _norm(quote)
    return bool(q) and q in _norm(cell_text)


MOVE_VERBS = ("蹬地", "前冲", "掠", "翻", "劈", "踏", "扑", "甩", "崩", "掀", "坠",
              "疾", "猛", "骤", "弹", "撞", "斩", "刺", "削", "扫", "跃", "滑步",
              "横移", "旋身", "拧身", "压上", "逼上", "犁", "撕开", "踩碎", "插下",
              "拖", "炸开")


WIDE_WORDS = ("大全景", "全景", "远景", "空镜")


def countable(shots: list[dict], target_seconds: int = 0,
              chars: list[str] | None = None,
              target_shots: tuple[int, int] | None = None) -> list[dict]:
    """能数的判据。返回 `[{name, check, detail}]`，空列表 = 全过。

    ★ 这一层的存在意义（2026-09-29）：**凡程序能确定的，就别写进提示词让模型自觉**。
      第 1 集为了"一拍一镜"这类可数律，分镜角色逐镜自查自改，
      一条链白跑 2 小时（撞满 9000 秒预算、rc=0 静默收工）。
      这些判据从这里开始由代码出，角色的提示词里相应条款同时删掉。
    """
    out = []
    n = len(shots) or 1
    total = sum(int(s.get("seconds") or 0) for s in shots)
    secs = [int(s.get("seconds") or 0) for s in shots]

    def need(ok, check, detail, names=()):
        if not ok:
            # 镜号要**列全**：只给前 8 个会让角色以为"其余的没问题"（退回清单是给
            # 它照做的，不是给人看的摘要）。
            out.append({"name": "、".join(names[:24]), "check": check, "detail": detail})

    # —— 结构类（逐镜可数，零歧义）——
    multi_beat = [s["name"] for s in shots
                  if len(re.findall(r"\d+\s*-\s*\d+\s*秒[:：]|\d+\s*秒[:：]",
                                    s.get("visual") or "")) > 1]
    need(not multi_beat, "每镜恰好一拍（出现第二个 `N-M秒：` 即不合格）",
         "命中 %d 镜" % len(multi_beat), names=multi_beat)
    anchor_rep = [s["name"] for s in shots
                  if len(re.findall(r"@[\u4e00-\u9fa5A-Za-z0-9_]+（", s.get("visual") or "")) > 1]
    need(not anchor_rep, "整镜 `@名（` 至多一次（重复会多画一个人）",
         "命中 %d 镜" % len(anchor_rep), names=anchor_rep)
    low_move = [s["name"] for s in shots
                if sum(1 for w in MOVE_VERBS if w in (s.get("visual") or "")) < 2]
    need(not low_move, "每镜位移动词 ≥2",
         "命中 %d 镜" % len(low_move), names=low_move)
    no_join = [s["name"] for s in shots[1:] if not (s.get("join_note") or "").strip()]
    need(not no_join, "「承接」列必填（首镜除外）",
         "空 %d 镜" % len(no_join), names=no_join)

    # —— 打戏密度类 ——
    # ★ 「兵刃接触 ≥8 镜」这条**已删**（2026-09-29 16:9 六臂探针 + 官方范例逐帧全量）：
    #   范例 30 秒里贴身互搏只有 3-4 秒、金属相碰**零次**，我们却把接触数当阻断判据
    #   ⇒ 比参考片还严。三项目实测接触律越逼、真打镜数越少（12→4→1）。
    #   `contact` 仍要算，因为下面「砍环境」那条拿它当参照。
    contact = [s["name"] for s in shots if any(w in (s.get("visual") or "") for w in CONTACT)]
    dodge = [s["name"] for s in shots if any(w in (s.get("visual") or "") for w in DODGE)]
    need(len(dodge) >= max(2, n // 10), "有应招（闪/退/被荡开）", "%d 镜" % len(dodge))
    spoken = [s for s in shots
              if (s.get("dialogue") or "").strip()
              and not any(w in (s.get("dialogue") or "") for w in SILENT_MARK)]
    need(len(spoken) >= n * 0.5, "台词镜 ≥50%", "%d/%d = %.0f%%" % (len(spoken), n,
                                                                    100.0 * len(spoken) / n))
    over5 = [x for x in secs if x > 5]
    need(sum(1 for x in secs if x > 8) == 0 and len(over5) <= 2,
         "单镜时长（≤5s 常态，6-8s 至多 2 镜）",
         "最长 %ds，>5s 的 %d 镜" % (max(secs or [0]), len(over5)))
    # ★ 本包 10b：**非宽景不许两个角色同时 @ 同框**（实测那样会多画一个人）。
    #   这条完全数得出来 —— 不必等审稿角色绕一轮重派（2026-09-29 实测：它抓到了，
    #   但代价是一整轮分镜重派 + 20 分钟起）。
    if chars:
        both_at = [s["name"] for s in shots
                   if not any(w in (s.get("shot_type") or "") for w in WIDE_WORDS)
                   and len({n for n in chars if ("@" + n) in (s.get("visual") or "")}) >= 2]
        need(not both_at, "非宽景不许双人同时 @（会多画一个人）",
             "命中 %d 镜 —— 修法：**这一镜只 @ 一个人**，另一个用「他／对方／她」写"
             "（两人都要在动是内容要求，不等于两个都要 @）" % len(both_at),
             names=both_at)
    env_only = [s["name"] for s in shots
                if any(v in (s.get("visual") or "") for v in ENV_VERB)
                and any(w in (s.get("visual") or "") for w in ENV_HIT)
                and not any(w in (s["visual"] or "") for w in CONTACT)]
    need(len(env_only) <= 1, "禁「砍环境」为一镜主内容",
         "命中 %d 镜" % len(env_only), names=env_only)
    if target_seconds:
        # 镜数下限**按目标秒数推**，不写死（2026-09-29 实测：写死 25 镜把一部
        # 60 秒 / 17 镜的片子误判成不合格 —— 判据按"我以为片子多长"写，就是错的）。
        # ★ 但 brief 自己写了区间（"共 15-18 镜"）时**必须按区间判**：同日晚间一条链
        #   交出 12 镜 / 54 秒却"镜数合格"，因为派生公式的下限只有 7 —— 那是漏检。
        floor = max(6, int(target_seconds / 8))
        ok_n = n >= floor
        detail = "%d 镜 / %d 秒（目标 %ds，镜数下限 %d）" % (n, total, target_seconds, floor)
        if target_shots:
            lo, hi = target_shots
            ok_n = lo <= n <= hi
            detail = "%d 镜 / %d 秒（brief 明写 %d-%d 镜，目标 %ds）" % (
                n, total, lo, hi, target_seconds)
        need(total >= target_seconds * 0.85 and total <= target_seconds * 1.35
             and ok_n, "片长与镜数达 brief 要求", detail)
    return out


CODE_LABELS = {"opponent_as_background": "对手被写成不动的背景",
               "rock_chopping_as_beat": "主要动作是砍环境而不是打对方",
               "frozen_vfx_pose": "光效当定格道具"}


PUNCH_NAME = "shotcheck_ep%d.json"
PUNCH_TTL_MIN = 240      # 超过 4 小时的清单不再当"当前问题"喂给角色


def punch_path(root, ep: int) -> Path:
    return Path(root) / "scenedesigner" / (PUNCH_NAME % int(ep or 1))


def save_punch(root, ep: int, items: list[str]) -> None:
    """把退回清单钉在盘上。

    ★ 为什么必须存文件（2026-09-29）：系统正规的"打回重做"会**先把旧分镜表移进
      `.rerun_backup/`**，于是重派时"表还在盘上"这个前提不成立，清单就注入不了。
      清单本身要活过那次移动。
    """
    import time
    punch_path(root, ep).write_text(json.dumps(
        {"saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "items": items},
        ensure_ascii=False, indent=1), encoding="utf-8")


def load_punch(root, ep: int) -> list[str]:
    """读回未过期的清单；没有 / 过期 / 读不动 → 空列表。"""
    import time
    p = punch_path(root, ep)
    if not p.exists():
        return []
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        age = (time.time() - time.mktime(time.strptime(
            d.get("saved_at") or "", "%Y-%m-%dT%H:%M:%S"))) / 60.0
        if age > PUNCH_TTL_MIN:
            p.unlink()
            return []
        return [str(x) for x in (d.get("items") or [])]
    except Exception:  # noqa: BLE001
        return []


def clear_punch(root, ep: int) -> None:
    try:
        punch_path(root, ep).unlink()
    except OSError:
        pass


def punch_list(shots: list[dict], *, target_seconds: int = 0, use_judge: bool = True,
               workers: int = 8, log=print, chars: list[str] | None = None,
               target_shots: tuple[int, int] | None = None) -> list[str]:
    """给角色看的**退回清单**（一镜一行，带镜号与逐字原文）。

    为什么要有这个形状：分镜角色拿到的如果是"你自己检查一遍"，它会逐镜重读整张表
    （实测 2 小时）；拿到"这 5 镜、这几条、原文在此"，它只需要改那 5 镜。
    """
    hard = countable(shots, target_seconds, chars, target_shots)
    out = []
    for h in hard:
        out.append("【%s】%s（%s）" % (h["check"], h["name"] or "全表", h["detail"]))
    if use_judge:
        r = check(shots, target_seconds=target_seconds, use_judge=True,
                  workers=workers, log=log, chars=chars, target_shots=target_shots)
        for s in r["semantic"]:
            out.append("【%s】镜 %s：「%s」—— %s"
                       % (CODE_LABELS.get(s["code"], s["code"]), s["name"],
                          s["quote"], s["why"]))
    return out


def _parse_json(text: str) -> dict:
    """从模型回复里抠出 JSON（它常把 JSON 包在 ``` 或前后加一句废话）。"""
    t = (text or "").strip()
    t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t)
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j <= i:
        raise ValueError("回复里没有 JSON：%r" % t[:80])
    return json.loads(t[i:j + 1])


def judge_shot(shot: dict, llm=None) -> dict:
    """一镜的语义判定。返回 `{violations:[…], unverifiable:[…]}`。

    `llm` 可注入（测试用假模型）；缺省走 `llm.chat_for(temperature=0)`
    —— 评判类必须温度 0，否则同一张表两次判会给出不同结论（静帧 QC 实测过）。
    """
    if llm is None:
        from v5 import llm as _llm
        llm = _llm.chat_for(max_tokens=700, temperature=0.0)
    cells = shot_cells(shot)
    head = "镜号 %s（%s·%s·%ss）" % (shot["name"], shot.get("shot_type"),
                                    shot.get("angle"), shot.get("seconds"))
    msg = llm.invoke([("system", JUDGE_SYSTEM), ("user", head + "\n" + cells)])
    raw = getattr(msg, "content", msg) or ""
    if isinstance(raw, list):          # 多模态风格的分段回复
        raw = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in raw)
    try:
        data = _parse_json(raw)
    except Exception as e:  # noqa: BLE001 -- 判不了要响亮，不能当"没问题"
        return {"name": shot["name"], "error": "解析失败：%s｜原文:%s" % (str(e)[:60], raw[:120]),
                "violations": [], "unverifiable": []}
    ok, bad = [], []
    for v in data.get("violations") or []:
        code = str(v.get("code") or "")
        if code not in CODES:
            bad.append({**v, "reason": "类别不在白名单"})
            continue
        if quote_verifiable(str(v.get("quote") or ""), cells):
            ok.append({"code": code, "quote": str(v.get("quote"))[:120],
                       "why": str(v.get("why") or "")[:80]})
        else:
            bad.append({**v, "reason": "引不出逐字原文（降级为提醒，不算阻断）"})
    return {"name": shot["name"], "violations": ok, "unverifiable": bad}


def character_names(root) -> list[str]:
    """本片角色名（10b 那条判据要区分"@ 的是人"还是"@ 的是场景/道具"）。

    ⚠️ **不能只读 `assets.json`**（2026-09-29 实错）：注册表是**媒体链**的
    `cast.ensure` 才写的，而本模块跑在**创作链**阶段 —— 那时它根本不存在，
    于是返回空 → 10b 判据静默不判，我还在报告"已经抓到了"。
    所以按可用性依次回退：注册表 → worldbuilder.md 的角色卡 → 空。
    """
    root = Path(root)
    try:
        reg = json.loads((root / "assets.json").read_text(encoding="utf-8"))
        names = [str(a.get("name")) for a in reg.get("assets", [])
                 if isinstance(a, dict) and a.get("type") == "character" and a.get("name")]
        if names:
            return names
    except Exception:  # noqa: BLE001 -- 注册表还没有是常态，继续往下找
        pass
    try:
        from .guards import resolve_path
        from .media import cast
        wb = resolve_path(root, "worldbuilder", 1)
        if wb.exists():
            return [str(c.get("name")) for c in cast.parse_characters(
                wb.read_text(encoding="utf-8")) if c.get("name")]
    except Exception:  # noqa: BLE001
        pass
    return []


def check(shots: list[dict], *, target_seconds: int = 0, use_judge: bool = True,
          workers: int = 8, log=print, llm=None,
          chars: list[str] | None = None,
          target_shots: tuple[int, int] | None = None) -> dict:
    """两层体检的总入口。返回 `{countable, semantic, unverifiable, errors, blocking}`。

    `llm` 可注入（离线单测用）—— 缺省才去建真实客户端。
    """
    hard = countable(shots, target_seconds, chars, target_shots)
    for h in hard:
        log("[shotcheck] ❌ %s —— %s%s"
            % (h["check"], h["detail"], ("（%s）" % h["name"]) if h["name"] else ""))
    sem, unver, errs = [], [], []
    if use_judge:
        from concurrent.futures import ThreadPoolExecutor

        def _one(s):
            return judge_shot(s, llm=llm) if llm is not None else judge_shot(s)

        with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
            for r in ex.map(_one, shots):
                if r.get("error"):
                    errs.append(r)
                for v in r["violations"]:
                    sem.append({"name": r["name"], **v})
                for v in r["unverifiable"]:
                    unver.append({"name": r["name"], **v})
        for s in sem:
            log("[shotcheck] ⚠️ %s %s：%s｜「%s」" % (s["name"], s["code"], s["why"], s["quote"]))
        if unver:
            log("[shotcheck] 其中 %d 条**引不出原文**，已降级为提醒（不阻断）" % len(unver))
        if errs:
            log("[shotcheck] ⛔ %d 镜判定失败（解析异常）——**不算通过**，请重跑或看 error" % len(errs))
    blocking = bool(hard) or bool(sem) or bool(errs)
    log("[shotcheck] 结论：%s（可数 %d 条 / 语义 %d 条 / 引不出原文 %d 条 / 判定失败 %d 条）"
        % ("**先改分镜**" if blocking else "可以渲",
           len(hard), len(sem), len(unver), len(errs)))
    return {"countable": hard, "semantic": sem, "unverifiable": unver,
            "errors": errs, "blocking": blocking}
