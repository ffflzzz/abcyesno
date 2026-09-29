# -*- coding: utf-8 -*-
"""分镜**可数契约**体检（渲染前跑，不烧任何配额）。

为什么需要（2026-09-29 逐帧全量实测的教训）：第 1 集 120 秒里
「两人同帧且都在动」只有约 15 秒、真正兵刃接触约 3 秒 —— 而**门全绿**。
因为分镜把对手写成了背景（19 处「远景虚化…无动作」），
媒体层没有任何一道判据看的是"打没打"。

本脚本把 brief 里那几条可数律**直接量分镜表**，在烧视频配额之前给出结论。

用法：python scripts/audit_storyboard.py <项目名> [--ep=N]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v5 import config  # noqa: E402
from v5.guards import resolve_path  # noqa: E402
from v5.media import storyboard  # noqa: E402

# 词表全部取自**历史真实产出**（第 1 集分镜与成片），不是"我以为它会这么写"
CONTACT = ("格开", "格挡", "交剑", "相交", "对撞", "撞开", "磕", "荡开", "碰上",
           "相抵", "架住", "接下", "硬接", "互撞", "剑锋相", "双剑相")
DODGE = ("侧身避", "闪开", "掠开", "退半步", "后撤", "被震退", "被荡开", "翻身避")
ENV_HIT = ("崖壁", "岩缝", "岩石", "铁索", "山石", "石阶", "青石", "栈道板", "地面", "石台")
ENV_VERB = ("劈", "斩", "犁", "削", "击碎", "崩落", "掀翻", "插下", "刺入", "扫过")
BACKGROUND_PHRASES = ("远景虚化", "无动作", "缩为", "十分之一", "五分之一", "作远景", "虚化")
# ⚠️「剪影」**不能进上表**（2026-09-29 实测假阳性）：ep2 的 LN14-LN19 全部命中
#   「逆光剪影」——那是**光位词**（本包场景锚点自带的措辞），不是把对手写成剪影。
#   只有「作/为/成…剪影」「剪影…无动作」才是"对手当背景"。
BG_SILHOUETTE = ("作剪影", "为剪影", "成剪影", "剪影无动作", "剪影不动", "背景剪影")
FREEZE = ("定住", "定格", "保持", "悬停", "静止")
SILENT_MARK = ("（无声", "(无声", "环境音", "无台词")


PROXY = ("对方",)
# ⚠️ 代称只认「对方」，**不认「他／她」**（2026-09-29 自查）：单 @ 写法里「她」常常指
# **被点名的那一个**（LN02 原文「@阮青 …；对方出画在下缘…；她左手自胸前横推」——
# 三个「她」全是阮青自己）。把「她」当第二个人会把"只有一人在动"的镜判成"两人都在动"，
# 那是往放松尺子的方向错。


def _both_active(shot, names):
    """两人是否**都**在动：场上有两个参与方（两个名字，或一个名字＋「对方」），
    且**每一方**在正文里有自己的动作（该方任一次出现后 60 字内出现动作词）。

    ★ 2026-09-29 修了两处口径（都在"漏检/误报"这一侧，不是放松标准）：
      ① 本包判据 10b 要求非宽景镜**只 @ 一个人**、第二位写「对方」，旧实现要求两个
         名字都出现才往下判 ⇒ 合规的表恒等于 0%。
      ② 旧实现只看每方**第一次**出现后的 60 字。实测 LN05/LN12 那种"第一处是被攻的
         部位（斜刺对方出画处右肋）、第二处才是她自己的动作"被误判成"只有一人在动"；
         改为**任一处**有动作即算该方在动。
    """
    v = shot.get("visual") or ""
    # ★ 词表补的这几个串**取自本表真实产出**（LN05「自剑柄斜握改为正握、沧浪剑身自右下
    #   斜刺上」、LN10「她的右脚在栈道板上横移一记短步」、LN12「沉步前移两步蹬地」），
    #   不是为了让某张表过关现编——判据改完必须重跑病表（huashan ep1 仍须 ≈31%）
    #   与好表（ep2 仍须 ≥90%），见 [[按真实产出措辞写判据]]。
    acts = CONTACT + DODGE + ENV_VERB + ("冲", "掠", "跃", "踏", "旋", "蹬",
                                         "斜刺", "斜入", "横移", "沉步", "前移",
                                         "横端", "握")

    def moves(marker):
        i = v.find(marker)
        while i >= 0:
            if any(w in v[i:i + 60] for w in acts):
                return True
            i = v.find(marker, i + 1)
        return False

    parties = [n for n in names if n in v]
    if len(parties) == 1 and any(p in v for p in PROXY):
        parties.append(PROXY[0])
    if len(parties) < 2:
        return False
    return all(moves(p) for p in parties[:2])


def main() -> None:
    project = next((a for a in sys.argv[1:] if not a.startswith("--")), "<请显式传项目名>")
    ep = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--ep=")), "1")
    root = config.PROJECTS_DIR / project
    p = resolve_path(root, "scenedesigner", int(ep))
    if not p.exists():
        raise SystemExit("⛔ 找不到分镜：%s" % p)
    shots = storyboard.parse(p.read_text(encoding="utf-8"))
    if not shots:
        raise SystemExit("⛔ 分镜解析出 0 镜（表头/列名不对？）：%s" % p)

    import json
    from v5 import validate
    try:
        brief = json.loads((root / "brief.json").read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        raise SystemExit("⛔ brief.json 读不出来（%s）——先修 brief 再体检" % e)
    tgt = int(validate.parse_target_seconds(brief.get("target_duration")) or 0)
    _rng = validate.parse_shot_range(brief.get("target_duration"))   # brief 的镜数区间
    if not tgt:
        print("⚠️ brief.target_duration 解析不出秒数 → **片长这条无法体检**，"
              "请写成「约 120 秒」这种形式")
    names = []
    try:
        reg = json.loads((root / "assets.json").read_text(encoding="utf-8"))
        names = [a.get("name") for a in reg.get("assets", [])
                 if a.get("type") == "character"]
    except Exception:  # noqa: BLE001
        pass
    if not names:
        # ★ `assets.json` 是**媒体链**的 cast.ensure 才写的，而本体检跑在渲**前** ⇒
        #   只读注册表会拿到空，"两人同帧都在动"这条于是**静默不判**、还显示成 0%
        #   （2026-09-29 实测：补丁表 17 镜全被判成"只一人在动"）。回退到角色卡。
        from v5 import shotcheck
        names = shotcheck.character_names(root)
    if not names:
        print("⚠️ 注册表与角色卡都认不出角色名 →「两人同帧且都在动」这条**没有判**，"
              "不等于通过")

    total = sum(int(s.get("seconds") or 0) for s in shots)
    n = len(shots)
    contact_shots = [s["name"] for s in shots
                     if any(w in (s.get("visual") or "") for w in CONTACT)]
    dodge_shots = [s["name"] for s in shots
                   if any(w in (s.get("visual") or "") for w in DODGE)]
    both = [s["name"] for s in shots if _both_active(s, names)] if names else []
    bg = []
    for s in shots:
        v = s.get("visual") or ""
        st = s.get("shot_type") or ""
        # 「缩为…一个点」在**打斗镜**里 = 把人拍小（病）；但 brief 的「结局」本身就
        # 要求"镜头缓缓拉远至群峰全景、画面定格" —— 那是**收尾定格**，不是病。
        # 所以只在"这一镜有接触/应招动作"时才算命中，避免逼分镜改掉 brief 要的结尾。
        fighting = any(w in v for w in CONTACT + DODGE + ENV_VERB)
        words = BACKGROUND_PHRASES + BG_SILHOUETTE
        if fighting:
            if any(w in v for w in words):
                bg.append(s["name"])
        else:
            # 非打斗镜（空镜/收尾）只禁"把**对手**写成背景"的措辞，不禁拉远变小
            if any(w in v for w in ("远景虚化", "作远景", "无动作", "作剪影",
                                    "为剪影", "成剪影", "背景剪影", "剪影无动作")):
                bg.append(s["name"])
    env_only = [s["name"] for s in shots
                if any(v in (s.get("visual") or "") for v in ENV_VERB)
                and any(w in (s.get("visual") or "") for w in ENV_HIT)
                and not any(w in (s["visual"] or "") for w in CONTACT)]
    silent = [s["name"] for s in shots
              if not (s.get("visual") or "").strip()
              or any(w in (s.get("dialogue") or "") for w in SILENT_MARK)
              or not (s.get("dialogue") or "").strip()]
    spoken = n - len(silent)
    secs = [int(s.get("seconds") or 0) for s in shots]
    per10 = len(contact_shots) / max(1, total / 10.0)

    print("=== %s 第 %s 集分镜体检（%d 镜 / %d 秒）" % (project, ep, n, total))
    rows = [
        ("兵刃接触镜数 ≥8", len(contact_shots) >= 8,
         "%d 镜（每 10 秒 %.1f 次）" % (len(contact_shots), per10)),
        ("有应招（闪/退/被荡开）", len(dodge_shots) >= max(2, n // 10),
         "%d 镜" % len(dodge_shots)),
        ("两人同帧且都在动 ≥60%%", names and len(both) >= n * 0.6,
         "%d/%d 镜 = %.0f%%" % (len(both), n, 100.0 * len(both) / max(1, n))),
        ("⛔ 禁「对手当背景」措辞", len(bg) == 0,
         "命中 %d 镜：%s" % (len(bg), "、".join(bg[:8]) or "无")),
        ("⛔ 禁「砍环境」为一镜主内容", len(env_only) <= 1,
         "命中 %d 镜：%s" % (len(env_only), "、".join(env_only[:8]) or "无")),
        ("台词镜 ≥50%", spoken >= n * 0.5,
         "%d/%d = %.0f%%" % (spoken, n, 100.0 * spoken / max(1, n))),
        ("单镜时长合规（≤5s 常态；6-8s 只许 2 镜；>8s 零）",
         sum(1 for x in secs if x > 8) == 0 and sum(1 for x in secs if x > 5) <= 2,
         "最长 %ds，>5s 的有 %d 镜（brief 允许起手/终招各一处 6-8s）"
         % (max(secs or [0]), sum(1 for x in secs if x > 5))),
        # 镜数下限**按目标秒数推**，不写死（2026-09-29 复修：这里一直挂着 `n >= 25`，
        # 是把 120 秒片子的口径套到了 60 秒片子上，17 镜/73 秒的表因此被误判不合格）。
        # 公式与 `v5/shotcheck.countable` 取一致——同一把尺子不许有两套数。
        # ★ brief 明写区间（"共 15-18 镜"）时按区间判：派生下限只有 7 镜，
        #   同日晚间一条链交 12 镜 / 54 秒却被这条判成合格（漏检）。
        ("片长与镜数达 brief 要求",
         tgt * 0.85 <= total <= tgt * 1.35
         and ((_rng[0] <= n <= _rng[1]) if _rng else n >= max(6, int(tgt / 8))),
         "%d 镜 / %d 秒（brief %s，目标 %ds）" % (
             n, total, ("%d-%d 镜" % _rng) if _rng else "镜数下限 %d" % max(6, int(tgt / 8)),
             tgt)),
    ]
    bad = 0
    for label, ok, detail in rows:
        print("  %s %-34s %s" % ("✅" if ok else "❌", label, detail))
        bad += 0 if ok else 1
    print("=== 结论：%s（%d/%d 条过）" % ("可以渲" if not bad else "先改分镜再渲",
                                          len(rows) - bad, len(rows)))
    if bad:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
