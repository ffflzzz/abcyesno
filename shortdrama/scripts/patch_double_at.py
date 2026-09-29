# -*- coding: utf-8 -*-
"""把非宽景镜里「后出现的那个角色名」换成「对方」——只动称呼，不动内容。

为什么需要（2026-09-29 实测）：本包判据 10b 要求非宽景镜（中景/近景/特写）只 @ 一个人，
两个都 @ 会让模型多画一个人。上一轮把这件事交给分镜角色重写整张表，结果它把名字和**动作
一起删了**（兵刃接触 12→6 镜、台词 13→8、连景别都改了）——产出比改前更差。这条改动可以用
一条确定性字符串规则描述，所以由代码做，不重跑角色。

规则：一镜内**最先出现**的角色名保留，另一个角色的**所有** `@名` 换成「对方」；
只改「画面描述」这一格，动作、秒数、括号锚点、别的列一律不碰。
宽景镜（全景/远景/大全景/空镜）放行不动——那里双人同框是本包允许的，
也保住"两个角色都在表里被 @ 到过"这个绑图入口。

★ 宽景判定用解析出来的**景别列**，不用整行找关键词：旧表近景镜里写着「远景虚化剪影」，
  按行匹配会把它们误当成宽景放走（2026-09-29 自查到此坑）。

写盘前有地板自检：改前那四项指标就是下限，**任何一项下降就不写盘、直接报错退出**。

用法：
    python scripts/patch_double_at.py <项目名> [--ep=1] [--src=<分镜md路径>]
                                      [--out=<输出路径>] [--apply]
    默认读盘上当前分镜表，输出到 `<同名>.patched.md`（不覆盖正式表）。
    `--apply` 才写回正式表，且先把正式表备份到 `.patch_backup/<时间戳>/`。
"""
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v5 import config, shotcheck  # noqa: E402
from v5.guards import resolve_path  # noqa: E402
from v5.media import storyboard  # noqa: E402

REPL = "对方"
FLOORS = ("兵刃接触", "应招", "位移动词≥2", "台词镜")


def metrics(shots, names):
    """四项地板指标 + 两条要清零的违规计数。口径照 shotcheck 的词表（真实产出措辞）。"""
    hit = lambda ss, words: [s for s in ss
                             if any(w in (s.get("visual") or "") for w in words)]
    con = hit(shots, shotcheck.CONTACT)
    dod = hit(shots, shotcheck.DODGE)
    move = [s for s in shots
            if sum(1 for w in shotcheck.MOVE_VERBS if w in (s.get("visual") or "")) >= 2]
    talk = [s for s in shots if (s.get("dialogue") or "").strip()
            and not any(w in (s.get("dialogue") or "") for w in shotcheck.SILENT_MARK)]
    dbl = [s for s in shots
           if not any(w in (s.get("shot_type") or "") for w in shotcheck.WIDE_WORDS)
           and len({n for n in names if ("@" + n) in (s.get("visual") or "")}) >= 2]
    anchor_rep = [s for s in shots
                  if len(re.findall(r"@[\u4e00-\u9fa5A-Za-z0-9_]+（", s.get("visual") or "")) > 1]
    return {"镜": len(shots), "秒": sum(int(s.get("seconds") or 0) for s in shots),
            "兵刃接触": len(con), "应招": len(dod), "位移动词≥2": len(move),
            "台词镜": len(talk), "非宽景双@": len(dbl), "整镜锚点串重复": len(anchor_rep)}


def patch_text(md, names):
    """返回 (新文本, [(镜号, 保留的名字, 替换处数)])。只在「画面描述」格内做替换。"""
    lines = md.splitlines()
    changed = []
    for s in storyboard.parse(md):
        v = s.get("visual") or ""
        if any(w in (s.get("shot_type") or "") for w in shotcheck.WIDE_WORDS):
            continue                                   # 宽景放行
        present = [n for n in names if ("@" + n) in v]
        if len(present) < 2:
            continue
        keep = min(present, key=lambda n: v.find("@" + n))
        new_v, hits = v, 0
        for n in present:
            if n == keep:
                continue
            # 连一个紧跟的半角空格一起吃掉，避免留下「对方 侧身」这种断口
            new_v, k = re.subn("@" + re.escape(n) + r" ?", REPL, new_v)
            hits += k
        if not hits:
            continue
        i = int(s.get("line") or 0)
        if v not in lines[i]:
            print("!! %s：第 %d 行里找不到原画面描述，跳过该镜（不改写以免错位）"
                  % (s["name"], i + 1))
            continue
        lines[i] = lines[i].replace(v, new_v, 1)
        changed.append((s["name"], keep, hits))
    out = "\n".join(lines)
    return (out + "\n" if md.endswith("\n") else out), changed


def main():
    project = next((a for a in sys.argv[1:] if not a.startswith("--")), "")
    if not project:
        raise SystemExit(__doc__)
    ep = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--ep=")), "1")
    root = config.PROJECTS_DIR / project
    src_arg = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--src=")), None)
    src = Path(src_arg) if src_arg else resolve_path(root, "scenedesigner", int(ep))
    if not src.exists():
        raise SystemExit("⛔ 找不到分镜：%s" % src)

    names = shotcheck.character_names(root)
    if len(names) < 2:
        raise SystemExit("⛔ 只认出 %s 个角色名（取自注册表/角色卡），这张表不需要改" % len(names))

    md = src.read_text(encoding="utf-8")
    before = storyboard.parse(md)
    if not before:
        raise SystemExit("⛔ 解析出 0 镜（列头不对？）：%s" % src)

    new_md, changed = patch_text(md, names)
    after = storyboard.parse(new_md)
    if len(after) != len(before):
        raise SystemExit("⛔ 改后解析出 %d 镜、改前 %d 镜 —— 镜数不该变，不写盘"
                         % (len(after), len(before)))
    m0, m1 = metrics(before, names), metrics(after, names)

    print("读的是：%s" % src)
    print("角色名：%s" % "、".join(names))
    print("改动：%d 镜 / 共替换 %d 处  保留的名字：%s"
          % (len(changed), sum(c[2] for c in changed),
             "、".join(sorted({c[1] for c in changed})) or "—"))
    print()
    print("%-14s %6s %6s %s" % ("指标", "改前", "改后", "判定"))
    bad = []
    for k in ("镜", "秒") + FLOORS + ("非宽景双@", "整镜锚点串重复"):
        if k in FLOORS and m1[k] < m0[k]:
            bad.append("%s %d→%d" % (k, m0[k], m1[k]))
        if k == "非宽景双@" and m1[k] != 0:
            bad.append("非宽景双@ 未清零：%d 镜" % m1[k])
        flag = "⛔ 降了" if k in bad_keys(bad) else ("地板" if k in FLOORS else "")
        print("%-14s %6s %6s %s" % (k, m0[k], m1[k], flag))

    if bad:
        raise SystemExit("\n⛔ 有指标下降，不写盘：%s" % "；".join(bad))

    if "--apply" in sys.argv[1:]:
        # ★ 写入目标**永远是盘上正式分镜表**，`--src` 只是读取来源（2026-09-29 踩过：
        #   用 --src 指备份目录里的旧表 + --apply，把补丁写进了归档，正式表一字未动，
        #   而归档是证据、不该被改）。归档目录一律拒绝写。
        live = resolve_path(root, "scenedesigner", int(ep))
        if ".rerun_backup" in live.parts or ".patch_backup" in live.parts:
            raise SystemExit("⛔ 正式表路径落在归档里（%s），拒绝写" % live)
        if live.exists():
            bak = live.parent / ".patch_backup" / time.strftime("%m%d-%H%M%S")
            bak.mkdir(parents=True, exist_ok=True)
            shutil.copy2(live, bak / live.name)
        else:
            bak = None
            live.parent.mkdir(parents=True, exist_ok=True)
        live.write_text(new_md, encoding="utf-8")
        print("\n✅ 四项地板全部守住，已写回正式表：%s%s" % (
            live, "\n   改前那份备份在：%s" % (bak / live.name) if bak else ""))
    else:
        out = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--out=")),
                   str(src.with_suffix(".patched.md")))
        Path(out).write_text(new_md, encoding="utf-8")
        print("\n✅ 四项地板全部守住，已写候选文件（正式表一字未动）：%s" % out)


def bad_keys(bad):
    """把报错串里被点名的指标挑出来，只用于打表时的标记列。"""
    return {b.split()[0] for b in bad}


if __name__ == "__main__":
    main()
