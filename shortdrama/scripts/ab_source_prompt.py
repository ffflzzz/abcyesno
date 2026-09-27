# -*- coding: utf-8 -*-
"""A/B 探针：源提示词原文 vs 本系统组装的提示词，同一个视频模型、同一批参考图。

起因（2026-09-27）：用户拿两条 Ref2VA 提示词在**同款模型**上生成的片子明显好过本系统
产出，问是不是过度工程化削弱了模型。要定位就得把变量拆开，一次只动一个：

    X 格 = 源提示词原文 + **本系统产出的静帧**当 @image3
           → 与本链路只差「提示词怎么写」（构图锁、图序、画幅全部对齐）
    Y 格 = 源提示词原文 + **只用角色设定表与场景空镜**（不喂静帧）
           → 在 X 之上再放开构图锁

对照基准是现成的 `projects/xianxia-duel-v3-0927/media/ep1/clips/pack01.mp4`
（同场景、同两把剑、同衣装色系，由本链路产出），不重复生成。

⚠️ 这不是"纯净复现"：源片的三张 @image 没随附件给出，只能拿本项目的图替代。
   所以 X/Y 之间可比、与源片绝对值不可比。

用法：
    python scripts/ab_source_prompt.py [--src <提示词txt>] [--seconds 12] [--dry-run]
产物：
    tmp/AB_src_X.mp4 / tmp/AB_src_Y.mp4
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from v5 import config                                            # noqa: E402
from v5.media import providers                                    # noqa: E402

PROJ = ROOT / "projects" / "xianxia-duel-v3-0927"
OUT = ROOT / "tmp"
DEFAULT_SRC = Path(r"C:/Users/Administrator/xwechat_files/"
                   "wxid_8wsl6n3drc5k_8e0f/temp/RWTemp/2026-09/"
                   "2bde3c1d4060f65b3830c0379714ed18/0-15.txt")


def _url_of(p: Path) -> str:
    """静帧/设定图的公开 URL 存在同名 `.url` 侧文件里（生产链就是这么传的）。"""
    u = p.with_suffix(p.suffix + ".url")
    if not u.exists():
        raise SystemExit("缺少参考图 URL 侧文件：%s（先跑一次媒体链生成资产图）" % u)
    return u.read_text(encoding="utf-8").strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(DEFAULT_SRC))
    ap.add_argument("--seconds", type=int, default=12)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    prompt = Path(a.src).read_text(encoding="utf-8").strip()
    s1 = _url_of(PROJ / "images" / "绛雪.png")
    s2 = _url_of(PROJ / "images" / "沧月.png")
    scene = _url_of(PROJ / "images" / "云海之上的双塔朱塔石台.png")
    still = _url_of(PROJ / "media" / "ep1" / "stills" / "LN01.jpg")

    variants = [("X", [s1, s2, still]), ("Y", [s1, s2, scene])]
    print("[ab] 源提示词 %d 字符 / %d 英文词；秒数=%d；画幅=16:9"
          % (len(prompt), len(prompt.split()), a.seconds))
    for tag, imgs in variants:
        print("[ab] %s 格参考图 %d 张%s" % (tag, len(imgs),
              "（含本链路静帧=构图锁）" if tag == "X" else "（无静帧=放开构图锁）"))
    if a.dry_run:
        return 0

    ids = {}
    # ★ 必须**跨 key 轮转 + 长退避**（2026-09-27 实测）：`video_queue_full` 是 per-key 的，
    #   第一版探针只用默认那条 key、退避 8~40 秒 ⇒ 10 次提交全被挡。生产链路走
    #   `keypool.KeyPool`（4 条 key × 65s 间隔），这里照抄同一套配速。
    keys = list(config.AGNES_API_KEYS) or [None]
    print("[ab] 可用 key %d 条" % len(keys))
    for tag, imgs in variants:
        for attempt in range(14):
            key = keys[attempt % len(keys)]
            try:
                r = providers.submit_video(prompt, mode="reference", images=imgs,
                                           seconds=a.seconds, aspect_ratio="16:9",
                                           timeout=300, key=key)
                ids[tag] = (r.get("video_id"), time.time(), key)
                print("[ab] %s submitted id=%s（key=%s…）"
                      % (tag, r.get("video_id"), str(key)[:6]))
                break
            except Exception as e:                                    # noqa: BLE001
                wait = min(90, 20 * (1 + attempt // max(1, len(keys))))
                print("[ab] %s 提交失败（%d/14）：%s → %ds 后换 key 重试"
                      % (tag, attempt + 1, str(e)[:70], wait))
                time.sleep(wait)
        time.sleep(int(config.video_submit_interval_per_key()))       # 提交配速

    OUT.mkdir(exist_ok=True)
    pending = dict(ids)
    deadline = time.time() + 60 * 25
    while pending and time.time() < deadline:
        time.sleep(15)
        for tag, (vid, _, key) in list(pending.items()):
            try:
                q = providers.query_video(vid, key=key)
            except Exception as e:                                    # noqa: BLE001
                print("[ab] %s 查询异常：%s" % (tag, str(e)[:60]))
                continue
            st = q.get("status")
            if st == "completed" and q.get("url"):
                import httpx
                dest = OUT / ("AB_src_%s.mp4" % tag)
                try:
                    with httpx.Client(timeout=180, trust_env=False) as c:
                        resp = c.get(q["url"])
                        resp.raise_for_status()
                        dest.write_bytes(resp.content)
                    print("[ab] %s done → %s（%.1f MB，%.0fs）"
                          % (tag, dest, dest.stat().st_size / 1e6,
                             time.time() - ids[tag][1]))
                    pending.pop(tag)
                except Exception as e:                                # noqa: BLE001
                    print("[ab] %s 下载失败：%s" % (tag, str(e)[:80]))
            elif st in ("failed", "error"):
                print("[ab] %s FAILED: %s" % (tag, str(q.get("error"))[:120]))
                pending.pop(tag)
    if pending:
        print("[ab] !! 仍在等待：%s" % list(pending))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
