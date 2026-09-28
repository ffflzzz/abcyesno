# -*- coding: utf-8 -*-
"""探针：动作密度文案 vs 现文案，同一组、同一批参考图、同样秒数。

起因（2026-09-28 用户逐帧看完 v3 成片）：「每个镜头之间的割裂感很严重，人物动作又慢，
像摆拍。只有 LN01 最满意。」

量出来的事实（不是猜）：
- 帧间运动（12fps、96×54 灰度相邻帧差）：全片只有 7% 的时间画面几乎静止 ⇒ **画面在动**；
- 但文案统计与运动量相关性很弱（位移动词≥3 的镜运动均值 14.4，≤1 的 12.2）
  ⇒ 动的多半是**镜头与光效**，不是人物位移；
- LN01 是唯一"一拍 = 一个不可逆事件 + 6 秒 + 位移动词 6 个 + 镜头有明确运动"的镜；
- 反面对照 LN23-26（运动量最低的一段）文案里全是「各自站稳」「剑尖微微下垂」
  「视线落在对方方向」「对峙线形成」——**静态属性**，而且 40+ 字的锚点串占满第一拍。

⇒ 要验的假设：**把"每镜两拍 + 站位朝向视线"换成"每镜一拍 + 一个不可逆位移事件"，
摆拍感是否消失**。只动文案：图、秒数、镜序、风格块全部走生产链同一套代码。

⚠️ 一次只测一个变量：本探针**不**改锚帧、不改图序（那两处已单独修/验过）。

用法：
    python scripts/probe_action_density.py [--group pack08] [--dry-run]
产物：
    tmp/AD_<组>_base.mp4   现文案（生产链原样组装）
    tmp/AD_<组>_act.mp4    改写文案（一拍一事件、锚点串移出、每拍给位移与速度）
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
from v5.media import providers, storyboard, style, video, jobs as jobs_mod  # noqa: E402
from v5.media import prompt as prompt_mod                          # noqa: E402

PROJ = ROOT / "projects" / "huashan-duel-v2-0927"
EP = "ep1"
OUT = ROOT / "tmp"

# 改写文案：每镜**一拍**、一个不可逆事件、给位移量与速度、不写站位/朝向/视线，
# ★ 完全去掉锚点串（A 臂下身份由设定表锁，锚点串正是占掉第一拍的东西）。
ACT_TEXT = {
    "LN23": "0-3秒：@谢潮生 自空中倒坠插下、沧浪长剑剑尖先入地半尺，她借坠势横拖长剑"
            "扫出一圈深蓝白弧光；@裴烛 同时侧身贴地滑进、赤焰长剑在石面犁出一道焦痕，"
            "孤松被两人交错的劲风拦腰压弯；镜头自高空 1 秒俯冲落到平台平视。",
    "LN24": "0-3秒：@谢潮生 蹬地前冲三步、每一步踩碎一块石板，沧浪长剑自右下撩起、"
            "深蓝白雷光撕开半人高的裂口，她拧身回斩、剑风把孤松松针成片削落。",
    "LN25": "0-3秒：@裴烛 矮身闪过撩击、左肩擦着孤松斜干旋身，赤焰长剑自下反挑、"
            "暗赤雷光撞在石面炸开一圈焦黑星点，他落地时右脚踏裂一块石板。",
    "LN26": "0-3秒：两人同时蹬地相向疾冲、在孤松两侧交错擦身，两色长剑在交错瞬间"
            "互斩对方后背、擦出一道横贯画面的双色裂光，孤松被余势连根拔起；"
            "镜头急推到两人交错的瞬间。",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--group", default="pack08")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    md = (PROJ / "scenedesigner" / ("scenedesigner_%s.md" % EP)).read_text(encoding="utf-8")
    by = {s["name"]: s for s in storyboard.parse(md)}
    j = json.loads((PROJ / "media" / EP / "video_jobs.json").read_text(encoding="utf-8"))
    rec = (j.get("jobs", j)).get(a.group)
    if not rec or not rec.get("shots"):
        raise SystemExit("记账里没有 %s（用 --group packNN）" % a.group)
    names, declared, total = rec["shots"], rec["declared_seconds"], rec["total_seconds"]

    stills = json.loads((PROJ / "media" / EP / "stills.json").read_text(encoding="utf-8"))
    stills = stills.get("stills", stills)
    own = {n: (stills.get(n) or {}).get("url") for n in names}
    urls, roles = video.pack_ref_images(PROJ, [by[n] for n in names], own)
    sb = style.wrap(style.load(PROJ))

    arms = []
    for tag, fix in (("base", False), ("act", True)):
        g = []
        for n in names:
            s = dict(by[n])
            if fix:
                s["visual"] = ACT_TEXT[n]
                s["camera"] = "镜头随动作走位，不冻结"
            g.append(s)
        text = prompt_mod.build_pack_prompt(g, declared, total, style_block=sb,
                                            ref_roles=roles)
        arms.append(("%s_%s" % (a.group, tag), text))

    for tag, text in arms:
        print("[ad] %-14s 文字 %d 字" % (tag, len(text)))
    print("[ad] 参考图 %d 张：%s（两臂同一批，不重生成）"
          % (len(urls), "、".join(k for k, _l in roles)))
    if a.dry_run:
        return 0

    OUT.mkdir(exist_ok=True)
    keys = list(config.AGNES_API_KEYS) or [None]
    ids = {}
    for i, (tag, text) in enumerate(arms):
        for attempt in range(14):
            key = keys[(i + attempt) % len(keys)]
            try:
                r = providers.submit_video(text, mode="reference", images=urls,
                                           seconds=total, aspect_ratio="16:9",
                                           timeout=300, key=key)
                ids[tag] = (r.get("video_id"), key)
                print("[ad] %s submitted id=%s" % (tag, r.get("video_id")))
                break
            except Exception as e:                                    # noqa: BLE001
                wait = min(90, 20 * (1 + attempt // max(1, len(keys))))
                print("[ad] %s 提交失败（%d/14）：%s → %ds 后换 key"
                      % (tag, attempt + 1, str(e)[:70], wait))
                time.sleep(wait)
        time.sleep(int(config.video_submit_interval_per_key()))

    pending = dict(ids)
    deadline = time.time() + 60 * 25
    while pending and time.time() < deadline:
        time.sleep(15)
        for tag, (vid, key) in list(pending.items()):
            try:
                q = providers.query_video(vid, key=key)
            except Exception:                                         # noqa: BLE001
                continue
            st = q.get("status")
            if st == "completed" and q.get("url"):
                import httpx
                dest = OUT / ("AD_%s.mp4" % tag)
                try:
                    with httpx.Client(timeout=180, trust_env=False) as c:
                        resp = c.get(q["url"])
                        resp.raise_for_status()
                        dest.write_bytes(resp.content)
                    print("[ad] %s done → %s" % (tag, dest.name))
                    pending.pop(tag)
                except Exception as e:                                # noqa: BLE001
                    print("[ad] %s 下载失败：%s" % (tag, str(e)[:70]))
            elif st in ("failed", "error"):
                print("[ad] %s FAILED: %s" % (tag, str(q.get("error"))[:100]))
                pending.pop(tag)
    return 1 if pending else 0


if __name__ == "__main__":
    sys.exit(main())
