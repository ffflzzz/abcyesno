# -*- coding: utf-8 -*-
"""探针：定妆照直喂视频模型（跳过静帧）vs 生产链的静帧。

起因（2026-09-28 逐帧看完 v2 华山成片）：静帧层本身带伤——抽查 9 张静帧里 6 张画了
第三人，且 `style-block.md:28-29` 把**第一部 demo 的道具名与衣装色**（紫晶长剑／
薰衣草粉珍珠渐变）固化进了"逐镜注入"的位置，于是成片里剑光偏紫、女角色变黑发粉裙。
静帧是视频的参考图，这些伤会被带到视频段。用户的判断是"静帧压倒了一切"。

矩阵（一次只动一个变量）：

    图            风格块          出处
    ------------  --------------  ------------------------------------
    静帧（生产）   含 demo 专名     盘上现成 clip，**不重烧**
    定妆照         含 demo 专名     A1/A3 → 单独回答"去掉静帧有没有用"
    定妆照         已修正           A2/A4 → 两个修法叠加的最好情况

组与秒数**照抄生产记账**（pack02 = LN04+LN05 8s；pack08 = LN28+LN29+LN30 12s），
文字除"参考图是哪几张"那一句外与 `build_pack_prompt` 逐字节相同。

⚠️ 不动任何生产代码：风格块的"修正版"在本脚本里做字符串替换，不改包文件。

用法：
    python scripts/probe_costume_direct.py [--mode costume|mixed] [--groups pack02,pack08] [--dry-run]
产物：
    tmp/CD_<组>_raw.mp4 / tmp/CD_<组>_fix.mp4   （mode=costume）
    tmp/MIX_<组>_raw.mp4 / tmp/MIX_<组>_fix.mp4 （mode=mixed）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from v5 import config                                              # noqa: E402
from v5.media import prompt as prompt_mod                          # noqa: E402
from v5.media import providers, storyboard, style                  # noqa: E402

PROJ = ROOT / "projects" / "huashan-duel-v2-0927"
EP = "ep1"
OUT = ROOT / "tmp"

# 每组的参考图条数上限 = 5（reference 档硬约束）：2 人物 + 2 兵刃 + 1 场景
CHARS = ["裴烛", "谢潮生"]
BLADES = ["赤焰长剑", "沧浪长剑"]

# 改包前那两句（把 demo 的道具名与衣装色带进了逐镜注入位）↔ 修正后。
# 包文件已于 2026-09-28 修正，raw 臂靠这张表把旧文本贴回去当对照。
REVERT = [
    ("两人的兵刃各按自己设定表的配色——一方兵刃及其",
     "一方的紫晶长剑及其"),
    ("另一方兵刃及其光效全为深蓝白；两人的衣装保持各自设定表的布料本色与形制、不随能量色改变，",
     "另一方的錾花长剑及其光效全为深蓝白；两人的裙装保持各自的布料本色——白金色丝绸、"
     "薰衣草粉珍珠渐变，"),
]


def _url_of(name: str) -> str:
    """定妆照的公开 URL 存在同名 `.url` 侧文件里（生产链就是这么传的）。"""
    p = PROJ / "images" / ("%s.png.url" % name)
    if not p.exists():
        raise SystemExit("缺少定妆照 URL：%s（先跑一次媒体链生成资产图）" % p)
    return p.read_text(encoding="utf-8").strip()


def ref_header(scene: str) -> str:
    """替掉 build_pack_prompt 的第一段（它按"每镜一张静帧"点名）。"""
    names = CHARS + BLADES + [scene]
    roles = ["角色「%s」的人物设定表" % CHARS[0], "角色「%s」的人物设定表" % CHARS[1],
             "道具「%s」（%s 的兵刃）" % (BLADES[0], CHARS[0]),
             "道具「%s」（%s 的兵刃）" % (BLADES[1], CHARS[1]),
             "场景「%s」的空镜（只锁建筑与地貌）" % scene]
    return ("、".join("第 %d 张参考图=%s" % (i + 1, r) for i, r in enumerate(roles))
            + "。参考图只用于锁定身份——长相、发型、服装形制与兵刃形制、场景地貌；"
              "构图、景别、机位与镜头运动一律按下面的文字描述执行。"
              "各节拍画面与本片段时间边界严格对应。")


def _still_url(shot_name: str) -> str:
    p = PROJ / "media" / EP / "stills" / ("%s.jpg.url" % shot_name)
    if not p.exists():
        raise SystemExit("缺少静帧 URL：%s" % p)
    return p.read_text(encoding="utf-8").strip()


def ref_header_mixed(scene: str, prev_shot: str) -> str:
    """混合档：身份由定妆照锁，静帧只留**上一片段结束画面**当接续锚点。"""
    roles = ["角色「%s」的人物设定表" % CHARS[0],
             "角色「%s」的人物设定表" % CHARS[1],
             "场景「%s」的空镜（只锁建筑与地貌）" % scene,
             "**上一片段的结束画面**（仅用于衔接人物姿态、道具位置与场景连续性，"
             "不对应本片段任何节拍）"]
    return ("、".join("第 %d 张参考图=%s" % (i + 1, r) for i, r in enumerate(roles))
            + "。参考图只用于锁定身份——长相、发型、服装形制与兵刃形制、场景地貌；"
              "构图、景别、机位与镜头运动一律按下面的文字描述执行。"
              "各节拍画面与本片段时间边界严格对应。")


def ref_header_hybrid(scene: str) -> str:
    """A 臂：身份=定妆照，场景实现=场景空镜+本组首镜静帧，接续=前组末镜静帧。
    ★ 必须写清**优先级**——两张静帧里的人物是画错的（黑发粉裙），不声明"冲突以设定表
      为准"就等于把错的身份又喂回去了。"""
    roles = ["角色「%s」的人物设定表" % CHARS[0],
             "角色「%s」的人物设定表" % CHARS[1],
             "场景「%s」的空镜" % scene,
             "本片段**第一拍的画面实现**（只取它的场景地貌、光线与人物站位）",
             "**上一片段的结束画面**（只取它的场景连续性与人物站位）"]
    return ("、".join("第 %d 张参考图=%s" % (i + 1, r) for i, r in enumerate(roles))
            + "。★ 优先级：**两人的长相、发型、服装形制与兵刃一律以第 1、2 张设定表为准**；"
              "第 4、5 张里若人物长相与设定表不一致，忽略它们的人物、只沿用其场景与站位。"
              "构图、景别、机位与镜头运动一律按下面的文字描述执行。"
              "各节拍画面与本片段时间边界严格对应。")


def build(group_names, declared, total, sb, mode="costume", prev_shot=None):
    """返回 (prompt, images)。文字除首段外与生产 pack 档逐字节相同。"""
    md = (PROJ / "scenedesigner" / ("scenedesigner_%s.md" % EP)).read_text(encoding="utf-8")
    by = {s["name"]: s for s in storyboard.parse(md)}
    group = [by[n] for n in group_names]
    scene = (group[0].get("scene") or "").strip()
    text = prompt_mod.build_pack_prompt(group, declared, total, style_block=sb)
    segs = text.split("\n\n")
    if mode == "hybrid":
        segs[0] = ref_header_hybrid(scene)
        imgs = ([_url_of(n) for n in CHARS + [scene]]
                + [_still_url(group_names[0]), _still_url(prev_shot)])
    elif mode == "mixed":
        segs[0] = ref_header_mixed(scene, prev_shot)
        imgs = [_url_of(n) for n in CHARS + [scene]] + [_still_url(prev_shot)]
    else:
        segs[0] = ref_header(scene)
        imgs = [_url_of(n) for n in CHARS + BLADES + [scene]]
    return "\n\n".join(segs), imgs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--groups", default="pack02,pack08")
    ap.add_argument("--mode", choices=("costume", "mixed", "hybrid"), default="costume")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    fixed_block = style.wrap(style.load(PROJ))
    # 对照臂要的是**改包之前**那段（含 demo 专名）。盘上文件已修正，这里按原样贴回，
    # 使 raw/fix 两臂只差这两句——不然两臂会一模一样，白跑。
    raw_block = fixed_block
    for new, old in REVERT:
        if new not in raw_block:
            print("[cd] !! 找不到待还原的修正句，raw 臂将与 fix 臂相同：%s" % new[:24])
        raw_block = raw_block.replace(new, old)
    jobs = json.loads((PROJ / "media" / EP / "video_jobs.json").read_text(encoding="utf-8"))
    jobs = jobs.get("jobs", jobs)
    order = sorted(jobs)

    arms = []
    for g in [x.strip() for x in a.groups.split(",") if x.strip()]:
        rec = jobs.get(g)
        if not rec or not rec.get("shots"):
            print("[cd] !! 记账里没有 %s，跳过" % g)
            continue
        names, declared, total = rec["shots"], rec["declared_seconds"], rec["total_seconds"]
        prev_shot = None
        if a.mode in ("mixed", "hybrid"):
            gi = order.index(g)
            if gi == 0:
                print("[cd] !! %s 是第一组，没有前组末镜可当锚点，跳过" % g)
                continue
            prev_shot = jobs[order[gi - 1]]["shots"][-1]
        # hybrid 只跑一臂（文本已修正；色偏已证成是生成方差，再开 raw 臂等于白烧配额）
        arms_spec = (("fix", fixed_block),) if a.mode == "hybrid" else \
                    (("raw", raw_block), ("fix", fixed_block))
        for tag, sb in arms_spec:
            text, imgs = build(names, declared, total, sb, mode=a.mode, prev_shot=prev_shot)
            arms.append(("%s_%s" % (g, tag), names, declared, total, text, imgs))

    print("[cd] 风格块原长 %d 字 / 修正后 %d 字" % (len(raw_block), len(fixed_block)))
    for tag, names, declared, total, text, imgs in arms:
        print("[cd] %-12s %s 合计%ds  文字%d 字  参考图%d 张"
              % (tag, "+".join(names), total, len(text), len(imgs)))
    if a.dry_run:
        return 0

    OUT.mkdir(exist_ok=True)
    keys = list(config.AGNES_API_KEYS) or [None]
    print("[cd] 可用 key %d 条 × 间隔 %ss" % (len(keys), config.video_submit_interval_per_key()))
    ids = {}
    for i, (tag, _n, _d, total, text, imgs) in enumerate(arms):
        for attempt in range(14):
            key = keys[(i + attempt) % len(keys)]
            try:
                r = providers.submit_video(text, mode="reference", images=imgs,
                                           seconds=total, aspect_ratio="16:9",
                                           timeout=300, key=key)
                ids[tag] = (r.get("video_id"), time.time(), key)
                print("[cd] %s submitted id=%s（key=%s…）"
                      % (tag, r.get("video_id"), str(key)[:8]))
                break
            except Exception as e:                                    # noqa: BLE001
                wait = min(90, 20 * (1 + attempt // max(1, len(keys))))
                print("[cd] %s 提交失败（%d/14）：%s → %ds 后换 key 重试"
                      % (tag, attempt + 1, str(e)[:70], wait))
                time.sleep(wait)
        time.sleep(int(config.video_submit_interval_per_key()))

    pending = dict(ids)
    deadline = time.time() + 60 * 30
    while pending and time.time() < deadline:
        time.sleep(15)
        for tag, (vid, _, key) in list(pending.items()):
            try:
                q = providers.query_video(vid, key=key)
            except Exception as e:                                    # noqa: BLE001
                print("[cd] %s 查询异常：%s" % (tag, str(e)[:60]))
                continue
            st = q.get("status")
            if st == "completed" and q.get("url"):
                import httpx
                dest = OUT / ("%s_%s.mp4" % ({"mixed": "MIX", "hybrid": "HYB"}.get(a.mode, "CD"), tag))
                try:
                    with httpx.Client(timeout=180, trust_env=False) as c:
                        resp = c.get(q["url"])
                        resp.raise_for_status()
                        dest.write_bytes(resp.content)
                    print("[cd] %s done → %s（%.1f MB，%.0fs）"
                          % (tag, dest.name, dest.stat().st_size / 1e6,
                             time.time() - ids[tag][1]))
                    pending.pop(tag)
                except Exception as e:                                # noqa: BLE001
                    print("[cd] %s 下载失败：%s" % (tag, str(e)[:80]))
            elif st in ("failed", "error"):
                print("[cd] %s FAILED: %s" % (tag, str(q.get("error"))[:120]))
                pending.pop(tag)
    if pending:
        print("[cd] !! 仍在等待：%s" % list(pending))
        return 1
    print("[cd] 对照基准（盘上现成，不重烧）：" + "、".join(
        "clips/%s.mp4" % g for g in [x.strip() for x in a.groups.split(",")]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
