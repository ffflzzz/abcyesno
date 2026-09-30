"""分龄与新增角色的**资产锚点派生**（2026-09-30）。

要修的两个缺口，都在 `shiguan-series-0926`（全剧 3 集、已出片）上实测确证：

① **主角跨集长大，但全剧只有一张定妆照。** 第 2 集分镜写 `@阿旺（14 岁，瘦高身形…）`、
   第 3 集写 `@阿旺（16 岁…）`，而 `assets.json` 里「阿旺」只有一条 identity=10 岁孩童，
   并且那句 identity 明写「全片每镜必须完全一致」。冲突时**图赢**（静帧提示词还要求
   "以它锁定该角色的长相、发型、**体型**与服装形制"）⇒ 实拍第 2、3 集仍是六七岁小孩，
   而旁白在念"十六岁那年我辍学了"。服装/道具/膝上淤青都跟上了（文字层赢），年龄没跟上。

② **后面集新登场的人根本没进注册表。** 第 3 集 `@狮艺店老板娘（40 岁，花白短发，
   围裙上有旧污渍）` 被点名 29 次，`assets.json` 12 条里一条没有 ⇒ 她的脸每镜自由发挥。
   （`assets.validate_assets` 能抓到，但默认只告警不拦，且抓到了也没人给她补卡。）

为什么从**分镜的 `@名（括注）`** 派生，而不是再派 assetdesigner 写一份增量卡：
括注里本来就写着长相（分镜契约要求"第一拍写全角色锚点 `@名（衣装）`"），
**盘上已有的事实不该再要求模型写一遍**（AGENTS.md「能代码做的别写成契约」）。

⚠️ 三条**必须存在**的过滤规则（第一版没有，实测把道具和镜头术语当成了新角色，
   一集白烧 6 张图；而第二版规则写反了又把该派生的漏掉）：
   · 中文没有词边界，`@` 到标点之间是"名字+镜头术语"粘在一起的一坨
     （`@阿旺侧脸近景（左脸3/4）`）。所以**先认人再归并**：只有「带年龄段的括注」
     和「对白列的说话人」算人的证据，用它们当键把其它 token 收进来；
   · 已知资产名**只在它真有角色卡、或这条点名不带年龄时**才收下 ——
     否则 `@阿力队长（25 岁…）` 会因为他在注册表里被登记成 `type=prop`
     而永远没有脸（实测数据错误）；
   · 道具/场景命中 ⇒ 判 `known-not-character` 直接跳过（`@牛皮大鼓（鼓面磨痕）`
     不是人）。派生出来的是**角色卡**，把道具画成人才是真事故。
"""
from __future__ import annotations

import re

#: 与 `assets._AT` 同族的"资产名"字符集（**刻意不含括号**——括注是描述，不是名字）
_NAME_CHARS = r"[^\s，。；、,;：:！？!?（）()\[\]【】「」\"'|/]+"
_NAME_RE = re.compile(r"@(" + _NAME_CHARS + r")")
_AGE_RE = re.compile(r"(\d{1,3})\s*岁")


def age_of(text: str) -> str:
    """文本里的年龄段，归一成 `"14岁"`（无年龄 → `""`）。

    归一是必需的：同一集里会混写「14岁」「14 岁」，要并成一组。
    （中文数字不认——实测产物一律用阿拉伯数字写年龄。）
    """
    m = _AGE_RE.search(str(text or ""))
    return ("%s岁" % m.group(1)) if m else ""


def mentions(texts) -> list:
    """抽出 `[(点名串, 紧跟其后的圆括注)]`，保序、不去重。

    只认**紧跟名字**的括注：`@阿旺（14岁，瘦高）` → ("阿旺", "14岁，瘦高")；
    `@阿旺，视线…` → ("阿旺", "")。
    """
    out: list = []
    if isinstance(texts, str):
        texts = [texts]
    for text in texts:
        t = str(text or "")
        for m in _NAME_RE.finditer(t):
            name, tail = m.group(1), t[m.end():m.end() + 240]
            paren = ""
            for opener, closer in (("（", "）"), ("(", ")")):
                if tail.startswith(opener):
                    end = tail.find(closer)
                    if end > 0:
                        paren = tail[len(opener):end].strip()
                    break
            out.append((name, paren))
    return out


def collect(shots: list, known_names=(), speakers=(), char_names=()) -> dict:
    """按**本集分镜**汇总每个**人**的点名：出场镜数、各年龄段、每段最长的括注。

    `known_names` —— 注册表/资产清单里的所有名字（角色 + 道具 + 场景）；
    `char_names`  —— 其中有**角色卡**的那部分（`base_cards` 的键）；
    `speakers`    —— 本集「对白」列解析出的说话人。

    ★ 为什么要先建「人名键」再归并，而不是直接拿 `@` 后面的整串当名字（第一版的错）：
      分镜写 `@阿旺近侧脸（左脸3/4）`、`@老板娘确认价格→M12笑他疯` —— `@` 到标点之间
      是**名字 + 镜头术语/动作短语**粘在一起的一坨（`_NAME_CHARS` 只挡标点，挡不住汉字）。
      直接当名字会把同一个人拆成十几个假资产，还会把真名（`老板娘`）并进假名里
      ⇒ **该派生的没派生**。只有「带年龄段的括注」和「说话人」才是**人的证据**，
      用它们当键去归并其它 token。
    """
    from .assets import _shot_text

    known = [str(k or "") for k in known_names if k]
    chars = {str(k or "") for k in char_names if k}
    spk = {str(s or "") for s in speakers if s}
    rows: list = []
    for si, s in enumerate(shots):
        seen: set = set()
        text = "%s %s" % (_shot_text(s), s.get("scene") or "")
        for name, paren in mentions(text):
            tag = age_of(paren)
            if (name, tag) in seen:      # 同镜重复点名只算一次（一镜一人不是一镜十人）
                continue
            seen.add((name, tag))
            rows.append((si, name, tag, paren))
    # 人名键 = 带年龄段的点名 ∪ 说话人。同一个人写全名和简称都算同一个键（取最长）。
    person_keys = sorted({n for _, n, tag, _ in rows if tag} | spk, key=len, reverse=True)

    def canonical(token: str, tag: str):
        """把一坨 `@xxx` 归到**人**上：先认已知资产名，再认人名键；都不是 ⇒ 丢弃。

        ★ 已知名**只在"它真有角色卡"或"这条点名不带年龄"时**才收下。否则
          `@阿力队长（25 岁，白色狮队服束腰…）` 会因为他在注册表里被登记成
          `type=prop`（实测 shiguan ep2 的数据错误）而**永远没有脸** ——
          带年龄的点名必须穿透它，去走"新增角色卡"这条路。

        人名键**双向**匹配（`key in token` 或 `token in key`）：实测同一人写全名只 1 次
        （`@狮艺店老板娘（40 岁…）`，带年龄），写简称 8 次（`@老板娘`，不带年龄）。
        只做单向会把那 8 次丢掉 ⇒ 全名那条没过出场门槛 ⇒ **该派生的没派生**。
        """
        hit = [k for k in known if k in token]
        if hit:
            kbest = max(hit, key=len)
            if kbest in chars or not tag:
                return kbest, True                    # 已知角色，或本条不带年龄 ⇒ 按资产算
        hit = [k for k in person_keys if k in token or token in k]
        if hit:
            return max(hit, key=len), False         # 新人（最长写法当正名）
        return "", False

    agg: dict = {}
    for si, name, tag, paren in rows:
        key, is_known = canonical(name, tag)
        if not key:
            continue
        g = agg.setdefault(key, {"total": 0, "tags": {}, "known": is_known,
                                 "human": bool(tag) or key in spk, "_shots": set(),
                                 "_alias": set()})
        # `total` 数的是**出现在几镜**，不是被 @ 几次 —— 门槛的语义是"这人在本集
        # 出场够多"（实测同一镜里 `@阿旺` 连写 5 次，按次数计门槛就是摆设）。
        g["_shots"].add(si)
        g["_alias"].add(name)
        if tag:
            g["human"] = True
        t = g["tags"].setdefault(tag, {"count": 0, "appearance": ""})
        t["count"] += 1
        if len(paren) > len(t["appearance"]):
            t["appearance"] = paren
    for key, g in agg.items():
        g["total"] = len(g.pop("_shots"))
        # 只把**简称**记成别名（`老板娘` ⊂ `狮艺店老板娘`）。反向不记：
        # `阿旺侧脸近景`、`阿旺嘴部闭合` 是"名字+镜头术语"粘在一起的写法，
        # 不是这个人的别名 —— 全记进注册表会攒出几十条垃圾（实测第一版就是 56 条）。
        g["aliases"] = sorted(x for x in g.pop("_alias") if x != key and x in key)
    return agg


def plan(shots: list, base_cards: dict, known_names=(), speakers=(),
         min_mentions: int = 2, max_new: int = 6):
    """出**决定清单**（不生成任何东西，便于测试与日志）。

    `base_cards`: `{角色名: 角色卡文本}` —— 全剧级角色卡（worldbuilder / 资产清单）。
    `known_names`: 注册表里**所有**资产名（角色/道具/场景），用来排除非人物点名。

    返回 `(需要派生, 不派生)`。每项 `{"name","card_name","tag","why","appearance",
    "alias_of","count"}`，`why` 取值：

      · `no-base-card`   —— 本集有这个人出场，但全剧没有卡 ⇒ **新增角色**；
      · `age-variant`    —— 有卡，但本集主流年龄段不在卡的年龄段里 ⇒ **分龄变体**，
                            卡名 `本名（14岁版）`，`alias_of` 指向基础卡（出图时锁脸用）；
      · `already-anchored` —— 有卡且年龄段对得上 ⇒ 什么都不做；
      · `known-not-character` —— 命中的是道具/场景名 ⇒ 不归本模块管（另有资产校验）；
      · `not-a-person`   —— 没有任何带年龄段的点名、也不是说话人；
      · `below-threshold` —— 出场镜数不足 `min_mentions`（路人，不值得烧一张图）；
      · `over-cap`       —— 判据过了、但被 `max_new`（每集派生上限）砍掉；
      · `no-appearance`  —— 想派但没有任何外观文字可画（只能报出来，不能瞎编）。
    """
    agg = collect(shots, known_names, speakers, char_names=(base_cards or {}).keys())
    need: list = []
    rest: list = []
    for name, g in sorted(agg.items(), key=lambda kv: -kv[1]["total"]):
        def row(why, tag="", ap="", card="", alias=""):
            return {"name": name, "card_name": card or name, "tag": tag, "why": why,
                    "appearance": ap, "alias_of": alias, "count": g["total"],
                    "aliases": g.get("aliases") or []}

        if g["total"] < min_mentions:
            rest.append(row("below-threshold"))
            continue
        if g["known"] and name not in (base_cards or {}):
            rest.append(row("known-not-character"))
            continue
        if not g["human"]:
            rest.append(row("not-a-person"))
            continue
        tags = {k: v for k, v in g["tags"].items() if k}
        tag = max(tags, key=lambda k: (tags[k]["count"], len(tags[k]["appearance"]))) \
            if tags else ""
        ap = (tags.get(tag) or {}).get("appearance") or ""
        if not ap:      # 主流年龄段没括注 ⇒ 退而取该名字最长的括注
            ap = max([v["appearance"] for v in g["tags"].values()] or [""], key=len)
        if not ap:
            rest.append(row("no-appearance", tag))
            continue
        base = str((base_cards or {}).get(name) or "")
        base_ages = {"%s岁" % m for m in _AGE_RE.findall(base)}
        if not base:
            need.append(row("no-base-card", tag, ap))
        elif tag and tag not in base_ages:
            need.append(row("age-variant", tag, ap,
                            "%s（%s版）" % (name, tag), name))
        else:
            rest.append(row("already-anchored", tag))
    _kept = need[:max_new]
    _cut = [dict(r, why="over-cap") for r in need[max_new:]]
    # 被上限砍掉的必须单独报因：否则日志里它顶着 `no-base-card` 出场，
    # 读起来像"判定过但不该派生"，而真实原因是配额上限（本项目最忌的看不出原因）。
    return _kept, (_cut + rest)


def derive_cards(shots: list, base_cards: dict, known_names=(), speakers=(),
                 min_mentions: int = 2, max_new: int = 6, log=print) -> list:
    """把 `plan()` 的结论变成 `cast` 能直接吃的**角色卡字典**。

    带 `alias_of` 的项（分龄变体）由 `cast` 拿基础角色的定妆照当源图做 img2img ——
    这样"14 岁的阿旺"和"10 岁的阿旺"是**同一张脸长大**，而不是重新抽一个人。
    新增角色没有源图，走纯文生图。
    """
    need, rest = plan(shots, base_cards, known_names, speakers,
                      min_mentions=min_mentions, max_new=max_new)
    out: list = []
    for r in need:
        c = {"name": r["card_name"], "appearance": r["appearance"],
             "keywords": [r["name"], r["card_name"]], "type": "character",
             "alias_names": list(r.get("aliases") or []), "derived": True}
        if r["alias_of"]:
            c["alias_of"] = r["alias_of"]
            c["age_tag"] = r["tag"]
            c["identity_scope"] = "本阶段（%s）" % r["tag"]
            c["appearance"] = ("%s。脸型、五官、发型与%s一致（同一个人，只按本阶段"
                               "改身高体型与服装）" % (r["appearance"], r["alias_of"]))
            log("[variants] 分龄变体：%s ← 以 %s 的定妆照锁脸（本集出场 %d 镜，年龄段 %s）"
                % (c["name"], r["alias_of"], r["count"], r["tag"]))
        else:
            log("[variants] 新增角色卡：%s（本集出场 %d 镜）：%s"
                % (c["name"], r["count"], r["appearance"][:60]))
        out.append(c)
    for r in rest:      # 没派生东西也要说清为什么，否则"没派生"=不可见
        log("[variants] 不派生 %s：%s（出场 %d 镜%s）"
            % (r["name"], r["why"], r["count"],
               "，年龄段 " + r["tag"] if r["tag"] else ""))
    return out
