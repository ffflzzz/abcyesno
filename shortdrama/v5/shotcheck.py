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
# ★ 2026-10-04 修订：**删掉「地面」「石台」**。
#   这两个是**通用承载面**而非地形特征 —— 任何打戏的起手都是「右脚蹬地面」「落在台面上」，
#   于是 `ENV_HIT` 只要看到「劈/斩 + 地面」就命中，把仙侠青铜台斗法误判成「砍环境」。
#   实测 xianxia-shanmen-1004：21 镜里 2 镜被这条误伤（内容其实是「对手横移闪开剑罡」，
#   地面只是伴随状语），分镜反复改不掉。
#   判据要抓的是**破坏地形本体**，所以只留有辨识度的地形名词。
#   ⚠️ 同理，如果将来出现「青铜台/石板/砖地」这类**人造场地被当环境破坏**的误伤，
#   正确修法是往这里补该题材的地形 noun，而不是把通用词加回来。
ENV_HIT = ("崖壁", "岩缝", "岩石", "铁索", "山石", "石阶", "青石", "栈道板", "树干", "巨石")
ENV_VERB = ("劈", "斩", "犁", "削", "击碎", "崩落", "掀翻", "插下", "刺入", "扫过")
SILENT_MARK = ("（无声", "(无声", "环境音", "无台词")

# ── 语义判据（模型判，必须逐字引用）──────────────────────────────────────────

def duplicate_prose(shots: list, threshold: float = 0.95) -> list:
    """两镜的正文**几乎整行一样**（同一段话被抄了两遍）。返回 `[(先出现的镜, 重复的镜)]`。

    ★ 2026-10-08 实测（`yuxuan-duanfeng-1007`）：一次重写里 LN02..LN11 的开头
    **与 LN01 一字不差**（模型一口气写 5 万字长表，写到后半段开始抄自己开头），
    而当时**没有任何判据抓这件事** ⇒ 每轮打回的理由都是别的条目 ⇒ 每轮整表重写、
    每轮在同一个地方犯同样的错，迭代到上限收工。判据必须**点名重复**，
    检查员才有"只改这几行"的可执行修法。

    ⚠️ 两条都按实测定：
      · 先剥 `@名（衣装…）` 锚点再比 —— 锚点复述是**契约要求**，不比就等于诬告合规写法；
      · 阈值 **0.95**（2026-10-08 定，按真实样本量出来的）：实测"整行照抄"= 0.99；
        "同一招式换目标重演"= 0.59~0.72；"同一句式只换一个词"≈ 0.88~0.92。
        后两类是**质量口味**（他看片判），不是抄错 —— 判在 0.95 才只抓真抄，
        否则连合规的套路化写法都会被打回。
    """
    import difflib
    import re as _re

    def _norm(v: str) -> str:
        v = _re.sub(r"@[^（(\s]{1,10}[（(][^）)]{0,120}[）)]", "", v or "")
        return _re.sub(r"[\s。，、；：,.;:!？?]", "", v)

    seen: list = []
    out = []
    for s in shots:
        v = _norm(str(s.get("visual") or ""))
        # ⚠️ 短正文不比：30 来个字里只差一个字，相似度也有 0.97 ——
        #   那是个**句子模板**，不是抄（真实分镜每镜 100~300 字，远超这个门槛）。
        if len(v) < 40:
            continue
        for name, prev in seen:
            if difflib.SequenceMatcher(None, v, prev).ratio() >= threshold:
                out.append((name, s.get("name")))
                break
        else:
            seen.append((s.get("name"), v))
    return out


def table_truncated(md: str) -> str:
    """表是不是**写断了**（末行不闭合）。返回原因串，空 = 没问题。

    判据只取可数的两条：① 最后一行表格行必须以 `|` 收尾（闭合）；② 末镜的单元格里
    不能以「：」结尾（那是半句话）。实测那次末行停在「镜头10落幅：」。
    """
    lines = [l for l in (md or "").splitlines()
             if l.strip().startswith("|") and not set(l.strip()) <= set("|-: ")]
    if not lines:
        return ""
    last = lines[-1].rstrip()
    if not last.endswith("|"):
        return "最后一行没闭合（表格行必须以 | 收尾）—— 表被写断了"
    tail = last.rstrip("|").split("|")[-1].strip()
    if tail.endswith(("：", ":")) or (tail and tail[-1] in "，、；,"):
        return "末镜最后一个单元格停在半句（…%s）—— 表被写断了" % tail[-12:]
    return ""

CODES = {
    "opponent_as_background":
        "这一镜里**应该出场的对手被写成了不动的背景**（例如"
        "「另一人作远景虚化剪影无动作」「站在远处没有动作」）。"
        "⛔ 若这一镜**对手根本没有出场**（单人演武、空镜、只有一个人在动），"
        "这条**不适用** —— 没有对手可写，就谈不上「被写成背景」。"
        "注意：「逆光剪影」「人物剪影勾边」这类**光位**描述不算；"
        "结尾拉远定格里两人都小也不算（那是构图，不是一人动一人不动）。",
    "rock_chopping_as_beat":
        "这一镜的**主要动作是破坏环境而不是打对方**（劈崖壁、斩铁索、砍山石、"
        "插进石头），整镜里对方没有被攻击、也没有应招。"
        "注意：作为**双方交招后果**顺带写出的碎石、裂纹不算。"
        "⛔ 若这一镜**对手根本没有出场**（单人演武／练功／收势镜），这条**不适用** ——"
        "2026-10-08 实测：一条 5 集仙侠把「第一集开场她独自演武」判成了这一条，"
        "分镜被重写 6 次、反空转闸把整条链掐掉（35 分钟零推进）。",
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

# ─── 摄影与光学两条（`camera-light-physics` 技法开启时才判，2026-10-03）────────
#
# 出处是当晚的五臂探针（`scripts/probe_prompt_detail.py`）：同一条 12 秒素材、
# 画面描述一字不动，只把「运镜」列从 `缓推` 换成带速度/行程/终点/静止段的写法，
# 末镜最后两秒的帧间差就从 10.9 掉到 6.7 与 5.7（同文本两次的抖动带宽只有 2.8），
# 而**没声明运镜**的那一臂停在 10.8 与老写法一致 ⇒ 这句写法是有效的那一句。
#
# ★ 为什么判据放在这里、且**默认不生效**：
#   · 这是可数的（列里有没有那些字），按本文件既有的纪律，可数的事不该写进提示词
#     让模型自觉；
#   · ⛔ 但反质量包（牛来要的是僵硬、锁定机位、线性起停）会把它当噪声——
#     所以整块由调用方传的 `camera_light` 开关控制，**没开技法就一条不判**，
#     其余项目的行为与改造前一字不变；
#   · 只进**退回清单**（`punch_list`），不进分镜契约门——不新增拦片的判据。
CAM_MOVE_WORDS = ("推", "拉", "摇", "横移", "移镜", "跟", "甩", "升", "降", "环绕", "轨")
#: 出现任一项 = 这条运镜写清了"去哪、多快、什么时候停"。
CAM_SPEC_WORDS = ("m/s", "米/秒", "速度", "行程", "终点", "停在", "静止", "不动", "保持")
#: 光落点词表（物理描述，不是画质参数）。⚠️ 故意不收 `4K`/`fps`/`无噪点` 那一类——
#: 它们与类型包风格块方向相反（写实风格块有意保留轻微噪点与真实光学瑕疵）。
LIGHT_WORDS = ("高光", "亮边", "反光", "光斑", "轮廓光", "透光", "受光", "背光",
               "吃光", "哑光", "阴影", "逆光")


def countable(shots: list[dict], target_seconds: int = 0,
              chars: list[str] | None = None,
              target_shots: tuple[int, int] | None = None,
              audio_mode: str = "dialogue-led",
              camera_light: bool = False,
              single_at_law: bool = False,
              markdown: str = "") -> list[dict]:
    """能数的判据。返回 `[{name, check, detail}]`，空列表 = 全过。

    ★ 这一层的存在意义（2026-09-29）：**凡程序能确定的，就别写进提示词让模型自觉**。
      第 1 集为了"一拍一镜"这类可数律，分镜角色逐镜自查自改，
      一条链白跑 2 小时（撞满 9000 秒预算、rc=0 静默收工）。
      这些判据从这里开始由代码出，角色的提示词里相应条款同时删掉。
    """
    out = []
    n = len(shots) or 1
    #: ★ 2026-10-05：秒数按 **float** 读。旧写法 `int(...)` 会把 0.5 秒的快切镜读成 0，
    #:   于是"每场总秒数"少算、单镜判据把它当越界 —— 而场口径下 0.5 秒一镜是合法写法。
    def _f(s):
        try:
            return float(s.get("seconds") or 0)
        except (TypeError, ValueError):
            return 0.0

    secs = [_f(s) for s in shots]
    total = round(sum(secs), 2)

    def need(ok, check, detail, names=()):
        if not ok:
            # 镜号要**列全**：只给前 8 个会让角色以为"其余的没问题"（退回清单是给
            # 它照做的，不是给人看的摘要）。
            out.append({"name": "、".join(names[:24]), "check": check, "detail": detail})

    # —— 结构类（逐镜可数，零歧义）——
    # ★ 镜内时间轴按**该镜自己的秒数**判，不看 brief 声明了几镜。
    #   2026-10-03 废弃「镜数 = 目标秒数 ÷ 4」之后，镜长归分镜师、brief 通常**不再**写镜数，
    #   旧写法"由 brief 推导单镜秒数"会整条失灵 ⇒ 长镜反而没人管了。
    #   ≥8 秒必须写满时间轴：一拍写完 12 秒 = 把事件排布整个交给模型，实测成片
    #   "节点太少、整段拖"（1003c：11 镜里 7 镜末段明显安静）。
    #   <8 秒**不判拍数** —— 0929 那条「每镜恰好一拍」随除法基线一起废弃；
    #   仙侠包要它，写在该包自己的契约里（已确认 scenedesigner 与 reviewer 两份都写着）。
    _mid = ((target_shots[0] + target_shots[1]) / 2.0) if target_shots else 0.0
    _exp = (target_seconds / _mid) if (target_seconds and _mid) else 0.0
    # ⚠️ `秒` 与 `：` 之间可能带一个短括注（模型真实写法：`9-12秒（结束态）：…`）。
    #   只认紧挨着的 `秒：` 会把这一整段漏掉 ⇒ 12 秒镜数出 3 段、被判"没写满时间轴"
    #   ——1003d 实测 11 镜**全部**因此被点名（退回清单让模型重写整张表）。
    _BEATS = (r"\d+(?:\.\d+)?\s*-\s*\d+(?:\.\d+)?\s*秒(?:（[^）]{0,10}）)?\s*[:：]"
              r"|\d+(?:\.\d+)?\s*秒(?:（[^）]{0,10}）)?\s*[:：]")
    thin = [s["name"] for s in shots
            if int(s.get("seconds") or 0) >= 8
            and len(re.findall(_BEATS, s.get("visual") or ""))
            < max(3, int(s.get("seconds") or 0) // 3)]
    need(not thin,
         "≥8 秒的镜必须写满**镜内时间轴**（12 秒 ⇒ ≥4 段 `0-3秒：` 式分段，每段换一个事件："
         "位移／易手／进出画／机位变化，不许两段写同一件事）",
         "命中 %d 镜 —— 分段要从 0 起、首尾相接、终于本镜秒数" % len(thin), names=thin)
    # ★ 判的是**同一个角色**在一镜里被 @ 了两次以上（AGENTS 那条实测病：后续拍重复
    #   `@名（衣装）` ⇒ 多画一个人）。旧实现数的是"这一镜里 `@名（` 一共几次"，
    #   于是两类**正常写法**一起被误判：① 道具也带括注（`@豆绿色瑜伽垫（180 厘米…）`）；
    #   ② 双人/三人同框**每个角色各 @ 一次**——那恰恰是"别把对手写成背景"要的写法。
    #   1003d 实测：4 镜因"三个角色各 @ 一次"被点名，评审顺着它判 fail、48 分钟零出片。
    #   ⇒ 改成数"**同一个名字**被 @ 了两次以上"：有角色名表时只数人（道具重复不算），
    #     没有名表时退化成"同一个 @名 重复"——两种情况下都不会再误伤"不同角色各 @ 一次"。
    rep = []
    for s in shots:
        got = re.findall(r"@([\u4e00-\u9fa5A-Za-z0-9_]{1,8})（", s.get("visual") or "")
        if sorted({x for x in got if got.count(x) > 1 and (not chars or x in chars)}):
            rep.append(s["name"])
    need(not rep, "同一角色在一镜里被 @ 了两次以上（重复会多画一个人）",
         "命中 %d 镜 —— 只在**首段**写 `@名（衣装）`，后面各段用「她／他／对方」"
         % len(rep), names=rep)
    # ⛔ 只判**写得下两拍**的镜：一拍的镜（收势、定格、短切）里"位移动词 ≥2"是
    #    一条**没人能满足**的条目 —— 2026-10-08 实测：第 1 集收尾镜 LN19 因此被退回，
    #    而那条链同时被另一条误判（见 `rock_chopping_as_beat`）拖着重写了 6 次。
    from .media import storyboard as _sb_ck
    # ── 表级两条（2026-10-08）：重复 / 写断 ──────────────────────────────
    _dup = duplicate_prose(shots)
    if _dup:
        need(False, "两镜正文成段重复（多半是表太长、写到后面抄了自己开头）",
             "重复对：%s —— 修法：**只重写后出现的那些镜**，其余原样保留"
             % "、".join("%s→%s" % (a, b2) for a, b2 in _dup[:6]),
             names=[b2 for _a, b2 in _dup[:12]])
    _tr = table_truncated(markdown)
    if _tr:
        need(False, "分镜表写断了", _tr)
    low_move = [s["name"] for s in shots
                if len(_sb_ck.split_beats(s.get("visual") or "")) >= 2
                and sum(1 for w in MOVE_VERBS if w in (s.get("visual") or "")) < 2]
    need(not low_move, "每镜位移动词 ≥2（**只判两拍以上的镜**）",
         "命中 %d 镜" % len(low_move), names=low_move)
    no_join = [s["name"] for s in shots[1:] if not (s.get("join_note") or "").strip()]
    need(not no_join, "「承接」列必填（首镜除外）",
         "空 %d 镜" % len(no_join), names=no_join)

    # —— 摄影与光学（⛔ 只在 `camera-light-physics` 技法打开时判，见上面的词表注释）——
    if camera_light:
        vague = [s["name"] for s in shots
                 if any(w in (s.get("camera") or "") for w in CAM_MOVE_WORDS)
                 and not any(w in (s.get("camera") or "") for w in CAM_SPEC_WORDS)]
        need(not vague, "「运镜」写了位移就得带速度/行程/终点/静止段",
             "命中 %d 镜 —— 例：缓慢向前推近，速度0.3m/s，行程0.5米，终点停在她手边，"
             "全程保持近景（静止要写到秒、终点要落在实体、景别别在运镜里改）"
             % len(vague), names=vague)
        nolight = [s["name"] for s in shots
                   if (s.get("visual_style") or "").strip()
                   and "同上" not in (s.get("visual_style") or "")
                   and not any(w in (s.get("visual_style") or "") for w in LIGHT_WORDS)]
        need(not nolight, "「视觉风格」缺光落点（高光在哪、阴影在哪、什么材质吃光）",
             "命中 %d 镜 —— 在原四要素后追加一句、约 60 字内；只写物理不写画质参数"
             "（4K/fps/无噪点 与风格块方向相反），也不要写负面句" % len(nolight),
             names=nolight)

    # —— 打戏密度类 ——
    # ★ 「兵刃接触 ≥8 镜」这条**已删**（2026-09-29 16:9 六臂探针 + 官方范例逐帧全量）：
    #   范例 30 秒里贴身互搏只有 3-4 秒、金属相碰**零次**，我们却把接触数当阻断判据
    #   ⇒ 比参考片还严。三项目实测接触律越逼、真打镜数越少（12→4→1）。
    #   `contact` 仍要算，因为下面「砍环境」那条拿它当参照。
    contact = [s["name"] for s in shots if any(w in (s.get("visual") or "") for w in CONTACT)]
    dodge = [s["name"] for s in shots if any(w in (s.get("visual") or "") for w in DODGE)]
    need(len(dodge) >= max(2, n // 10), "有应招（闪/退/被荡开）", "%d 镜" % len(dodge))
    # ★ 「台词镜 ≥50%」**只在 dialogue-led 判**（2026-09-30 修）。
    #   原先它无条件生效 ⇒ `silent` 与 `narration-led` 的项目**每一镜**都不合格：
    #   silent 的对白列按契约写「（无声，环境音）」，narration-led 的旁白写在**音效**列、
    #   对白列同样统一「（无声，环境音）」（两者都是 AGENTS 明写的契约写法）。
    #   后果不是报错而是**白烧一轮重派**：退回清单会要求分镜"把台词补到一半以上"，
    #   而那正好违反本包自己的音频模式契约。
    if audio_mode == "dialogue-led":
        spoken = [s for s in shots
                  if (s.get("dialogue") or "").strip()
                  and not any(w in (s.get("dialogue") or "") for w in SILENT_MARK)]
        need(len(spoken) >= n * 0.5, "台词镜 ≥50%", "%d/%d = %.0f%%" % (len(spoken), n,
                                                                        100.0 * len(spoken) / n))
    # —— 单镜时长：规范**从 brief 推导**，不写死 ——
    # 旧实现把 0929 那批短镜项目的节奏当成了硬判据（≤5s 常态、6-8s 至多 2 镜）。
    # 1003 实测反例：brief 明写「11 镜 × 每镜 12 秒」的长镜方案时，这条让 11 镜**全部**不合格
    # ⇒ 退回清单会逼着模型把每一镜改短，**判据反过来扼杀 brief 要的东西**。
    # 现在：brief 同时给了总时长与镜数 ⇒ 期望单镜秒数 = 总时长 ÷ 镜数，按 −50%/+34% 收
    #   （上限再被供应商硬约束 12 秒截住）；brief 没声明镜数时才回落旧规范。
    if _exp:
        exp = _exp
        lo, hi = exp * 0.5, min(12.0, exp * 1.34)
        off = [s["name"] for s, x in zip(shots, secs) if not (lo <= x <= hi)]
        need(not off,
             "单镜时长要贴近 brief 声明的 %.0f 秒（合格区间 %.0f-%.0f 秒；12 秒是供应商硬上限）"
             % (exp, lo, hi),
             "偏离 %d 镜 —— 例：把该镜「时长(秒)」改成区间内的数，或按同一步长重排全表"
             % len(off), names=off)
    else:
        # ★ 2026-10-05：**判据跟着单位走**。有「场次」列 ⇒ 一条请求 = 一场，
        #   供应商 [4,12] 管的是**请求**，不是镜 —— 旧写法把 4 秒地板挂在每镜上，
        #   快切正反打（1 秒甚至 0.5 秒一镜）会被整批点名退回，而媒体层其实照收
        #   （`video_plan` 的注释早就写着"[4,12] 管的是整条请求时长"）。
        #   没有场次列时"一镜 = 一条请求"，地板照旧成立 ⇒ 原样保留，不误伤老项目。
        if any(int(s.get("act") or 0) for s in shots):
            over12 = [s["name"] for s, x in zip(shots, secs) if x > 12]
            need(not over12, "单镜不得超过 12 秒（一条请求的上限）",
                 "越界 %d 镜 —— 超过 12 秒会被媒体层等比压缩；"
                 "要快切请把同场的镜写成小秒数，不是把一镜拉长"
                 % len(over12), names=over12)
        else:
            # brief 没声明镜数 ⇒ **不再拿"≤5s 常态"当规范**（那是 ÷4 时代的节奏口径，
            # 2026-10-03 随除法基线一起废弃）。只守供应商硬区间：越界会被媒体层改写
            # （>12 秒等比压缩、<4 秒直接拒），这一条与"镜长归谁定"无关，是硬事实。
            out_of_band = [s["name"] for s, x in zip(shots, secs) if not (4 <= x <= 12)]
            need(not out_of_band,
                 "每镜秒数必须在供应商硬区间 **4–12 秒**内（区间内怎么排由你定）",
                 "越界 %d 镜 —— 超过 12 秒会被媒体层等比压缩，低于 4 秒会被接口拒"
                 % len(out_of_band), names=out_of_band)
    # ★ 本包 10b：**非宽景不许两个角色同时 @ 同框**（实测那样会多画一个人）。
    #   这条完全数得出来 —— 不必等审稿角色绕一轮重派（2026-09-29 实测：它抓到了，
    #   但代价是一整轮分镜重派 + 20 分钟起）。
    # ⚠️ 2026-10-03 收窄成**按包生效**：这条是 `xianxia-vfx-action` 在 0927 由 advisory
    #   升为阻断的**包内**律（写在该包的 scenedesigner/reviewer SKILL 里），不是跨包通则。
    #   无条件用在都市情感剧上时，"双人近景各 @ 一次"这种**本来就该两个 @** 的镜
    #   全被点名为"会多画人"，评审据此把 `yoga-affair-1003d` 判停（48 分钟零出片）。
    #   ⇒ 谁写了这条律才对谁判（判据 = 该包分镜契约里有没有那句话）。
    if chars and single_at_law:
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
    # ── 场级判据（2026-10-05）：场 = 一次生成 = 一条 ≤12 秒的请求 ──
    # 口径是「场锁死、镜自由」：每场总长不超过一条请求的上限，场内至少 2 镜，
    # 每镜几秒**不由程序规定**（那是分镜师的节拍决定权，2026-10-03 定的）。
    # ★ 只在表里**真有「场次」列**时才判 —— 旧项目与别的包不写这列 ⇒ 一条都不判，
    #   行为与改造前一字不变（同 `camera_light` 那条的生效方式）。
    acts = [int(s.get("act") or 0) for s in shots]
    if any(acts):
        units: dict[int, list[dict]] = {}
        seen_acts: set[int] = set()
        prev_a = None
        for s in shots:
            a = int(s.get("act") or 0)
            if a == prev_a and a in units:
                units[a].append(s)
            elif a in seen_acts:
                need(False, "同一场必须连写（场号不许断续出现）",
                     "场 %d 在 %s 处又出现 —— 同场被拆开会让打包档把它切成两条，"
                     "接戏又回到跨请求" % (a, s["name"]))
                units.setdefault(a, []).append(s)
            else:
                seen_acts.add(a)
                units[a] = [s]
            prev_a = a
        over = [(a, round(sum(_f(s) for s in g), 2))
                for a, g in sorted(units.items())
                if round(sum(_f(s) for s in g), 2) > 12]
        need(not over, "每场总秒数 ≤12（一条请求的上限）",
             "超了 %d 场：%s —— 修法：**把超出的镜拆成下一场**，或压场内某镜秒数"
             % (len(over), "、".join("场%d=%ss" % (a, t) for a, t in over[:6])))
        short = [(a, round(sum(_f(s) for s in g), 2))
                 for a, g in sorted(units.items())
                 if 0 < round(sum(_f(s) for s in g), 2) < 4]
        need(not short, "每场总秒数 ≥4（低于 4 秒这条请求会被接口拒）",
             "太短 %d 场：%s —— 修法：与相邻同地点的场并成一场，或给场内镜补足秒数"
             % (len(short), "、".join("场%d=%ss" % (a, t) for a, t in short[:6])))
        single = [(a, len(g)) for a, g in sorted(units.items()) if len(g) < 2]
        need(not single, "每场 ≥2 镜（场是「一段戏多镜头」，单镜不成场）",
             "命中 %d 场：%s —— 修法：与相邻同地点的镜并成一场，或把这场拆出第二镜"
             % (len(single), "、".join("场%d=%d镜" % x for x in single[:6])))
        if target_seconds:
            lo_acts = max(2, int(target_seconds / 13))
            need(len(units) >= lo_acts, "场数达目标时长要求（每场 ≤12 秒 ⇒ 场数下限）",
                 "%d 场 / %d 秒（目标 %ds ⇒ 至少 %d 场；场数不够就是每场写太长）"
                 % (len(units), total, target_seconds, lo_acts))
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
               target_shots: tuple[int, int] | None = None,
               audio_mode: str = "dialogue-led",
               camera_light: bool = False,
               single_at_law: bool = False,
               markdown: str = "") -> list[str]:
    """给角色看的**退回清单**（一镜一行，带镜号与逐字原文）。

    为什么要有这个形状：分镜角色拿到的如果是"你自己检查一遍"，它会逐镜重读整张表
    （实测 2 小时）；拿到"这 5 镜、这几条、原文在此"，它只需要改那 5 镜。
    """
    hard = countable(shots, target_seconds, chars, target_shots, audio_mode,
                     camera_light=camera_light, single_at_law=single_at_law,
                     markdown=markdown)
    out = []
    for h in hard:
        out.append("【%s】%s（%s）" % (h["check"], h["name"] or "全表", h["detail"]))
    if use_judge:
        r = check(shots, target_seconds=target_seconds, use_judge=True,
                  workers=workers, log=log, chars=chars, target_shots=target_shots,
                  audio_mode=audio_mode, camera_light=camera_light)
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


def pack_requires_single_at(root) -> bool:
    """该包的**分镜契约**里有没有「非宽景只能 @ 一个角色」这条律（10b）。

    为什么要有这个探测：`shotcheck` 是跨包共用的程序体检，而 10b 是
    `xianxia-vfx-action` 在 0927 由 advisory **升为该包阻断**的包内判据
    （原文在 `packs/xianxia-vfx-action/{scenedesigner,reviewer}/SKILL.md`）。
    把它当通则用，就会误伤"双人近景各 @ 一次"这种本来正确的写法
    ——1003d 实测：评审据此判停，48 分钟零出片。
    ⇒ 谁写了这条律才对谁判。不新增 `pack.json` 字段（后端不消费新字段，
      加了也是死配置），直接读该包契约里的这句话。
    """
    try:
        from .media.style import pack_of
        from .roles import _role_skill          # 懒加载：roles 反过来 import 本模块
        txt = _role_skill(pack_of(Path(root)), "scenedesigner") or ""
        return "只能 @ 一个角色" in txt
    except Exception:  # noqa: BLE001 -- 读不到契约就不判这条，绝不瞎判
        return False


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
          target_shots: tuple[int, int] | None = None,
          audio_mode: str = "dialogue-led",
          camera_light: bool = False,
          single_at_law: bool = False) -> dict:
    """两层体检的总入口。返回 `{countable, semantic, unverifiable, errors, blocking}`。

    `llm` 可注入（离线单测用）—— 缺省才去建真实客户端。
    `camera_light` = 本项目开了 `camera-light-physics` 技法（调用方从
    `media.style.script_craft_of(root)` 判），关掉时摄影/光学两条**一条不判**。
    """
    hard = countable(shots, target_seconds, chars, target_shots, audio_mode,
                     camera_light=camera_light, single_at_law=single_at_law)
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


def duration_band(root, ep: int = 1) -> dict | None:
    """程序自己对**本集分镜表总时长**的确定性读数：`{shots, total, target, lo, hi, ok}`。

    读不出（无表 / brief 没写目标秒数 / 表里没有可数的秒数列）⇒ 返回 `None`，
    调用方**必须**当成"未知"，不许据此放行任何东西。

    ★ 为什么要把它单独露出来（2026-10-03 实测 `yoga-affair-1003g`）：
      评审以「总时长 88s < brief 硬边界 110–150s」为理由**阻断整张表**，
      而那张表 16 镜的「时长(秒)」列实际加总是 **120 秒、正好落在带内** ——
      88 = 8 镜×8 秒 + 2 镜×12 秒，它只加了长镜，把 2×4 秒与 4×6 秒整个漏掉。
      驱动器照这条假理由打回，分镜被重派 5 次、46 分钟零出片。
      「总时长」是**可数的**，代码自己就能数 ⇒ 它不该由模型的算术来定生死
      （见 [[feedback-code-over-contract]]、[[feedback-judge-model-with-quote-gate]]）。
    """
    try:
        from . import guards, validate
        from .media import storyboard as sb
        brief = guards.load_brief(Path(root))
        target = int(validate.parse_target_seconds(
            brief.get("target_duration"), ep=ep) or 0)
        if target <= 0:
            return None
        p = guards.resolve_path(Path(root), "scenedesigner", ep)
        if not p.exists():
            return None
        shots = sb.parse(p.read_text(encoding="utf-8"))
        secs = [int(s.get("seconds") or 0) for s in shots]
        if not secs or sum(secs) <= 0:
            return None
        lo = int(target * validate.TARGET_TOL_LOW)
        hi = int(target * validate.TARGET_TOL_HIGH)
        total = sum(secs)
        return {"shots": len(secs), "total": total, "target": target,
                "lo": lo, "hi": hi, "ok": lo <= total <= hi}
    except Exception:  # noqa: BLE001 -- 读数失败一律"未知"，绝不据此放行
        return None


#: 评审理由里"断言总时长不合格"的写法（可数的东西，按字面认，别扩）
_DUR_CLAIM_RE = r"(?:总时长|全片时长|片长|时长合计|合计时长)"


def filter_contradicted_blocks(root, ep: int = 1,
                               reasons=None) -> tuple[list, list]:
    """拿盘上事实核评审的**总时长类**阻断理由 → `(留下的, 被驳回的)`。

    只驳回**同时满足三条**的那一条理由（其余一律原样留下）：
      ① 它在说总时长不合格（含"不足/低于/＜/偏短/不够/未达/超/太长"之类判词）；
      ② 程序自己数出来的加总**在带内**（`duration_band().ok`）；
      ③ 它引用的秒数与程序读数**不是同一个数**（同数说明它看的是别的东西，不驳）。

    ⛔ 这不是"劝退评审"：驳回只针对这一类可数事实，且**逐条**处理——
      同一份判决里的其他理由照常打回。被驳回的条目会连程序读数一起打出来，
      人可以复核（见 [[feedback-judge-model-with-quote-gate]] 的"劝退措辞"教训）。
    """
    band = duration_band(root, ep)
    kept: list = []
    dropped: list = []
    if not band or not band.get("ok"):
        return list(reasons or []), dropped
    for r in (reasons or []):
        s = str(r)
        claim = re.search(_DUR_CLAIM_RE, s) and re.search(
            r"(不足|低于|＜|<|偏短|不够|未达|超过|超出|太长|>|硬边界)", s)
        nums = [int(x) for x in re.findall(r"(\d{2,3})\s*(?:秒|s\b)", s, re.I)]
        if claim and nums and band["total"] not in nums:
            dropped.append("%s ⇒ 程序读数：%d 镜 / %d 秒，在 %d–%d 秒带内"
                           % (s[:160], band["shots"], band["total"], band["lo"], band["hi"]))
        else:
            kept.append(r)
    return kept, dropped
