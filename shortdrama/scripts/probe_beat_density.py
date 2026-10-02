# -*- coding: utf-8 -*-
"""探针：**亚秒节拍到底能不能被模型执行**（只读，不接任何链路）。

起因（2026-10-02 与用户对话）：用户的判断是"agnes 能按提示词逐秒安排叙事"，
并提出"一场戏 3 段 12 秒、36 秒 60 拍"。对账结果：
· 提示词侧的逐秒声明**已经在了**（`build_pack_prompt` + `_remap_beats`，实测可见）；
· 但**节拍标记此前只认整数秒** ⇒ 每拍 ≥1 秒 ⇒ 一条 12 秒请求最多 12 拍、
  36 秒最多 36 拍，60 拍在当前语法里表达不出来；
· 今天顺手修了两件事：`split_beats` 现在如实解析小数端点（此前 `0.5-1秒：`
  会被认成起点 5 终点 1 的倒挂拍），`_remap_beats` 不再让时间戳溢出到下一镜
  （commit 0634671）。
**代码侧"写得到"已经解决；模型听不听得懂 0.5 秒/拍，我们没有任何一次实测读数。**
本探针只为回答这一句，不建判据、不闸门（同提示词两轮结果本就不同 ⇒ 只当报告）。

四臂（**同组、同参考图、同总秒数，只改时间切分**；动作文本一字不增不减，
`assert` 内容守恒，防"密度高了顺手多写了动作"这种自证）：

    PROD    盘上分镜原文（2-3 拍/镜）          生产事实
    PROSE   剥掉所有时间戳，只留逗号散文        反向臂：时间戳本身有没有用
    HALF    每个子句一个 0.5 秒时间戳           ★ 要问的那条：亚秒粒度
    REPEAT  与 PROD 同一份文本再提交一次        噪声地板：判 HALF 要看它超出多少

用法：
    python scripts/probe_beat_density.py --dry-run          # 只看四臂提示词与守恒断言
    python scripts/probe_beat_density.py [--group pack01] [--out tmp]
产物（全部落 tmp/，**不进 projects/**）：
    tmp/BD_<项目>_<臂>.mp4        四臂成片
    tmp/BD_<项目>_<臂>_f%.3fs.jpg 0.5 秒/帧的抽帧
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from v5 import config                                              # noqa: E402
from v5.media import prompt as prompt_mod                          # noqa: E402
from v5.media import providers, storyboard, style, video           # noqa: E402

CLAUSE_SPLIT = re.compile(r"[，、；;]")

#: 「无台词（环境音）」「（无声，环境音）」这类是**无声标记**，不是台词。
#: 探针要挑无台词的组——人眼读帧时台词会干扰"这一拍做没做"的判断。
SILENT_MARK = re.compile(r"^\s*[（(]?\s*(无台词|无声|无对白|无)\s*[^)）]*[)）]?\s*$")


def has_speech(shot: dict) -> bool:
    d = (shot.get("dialogue") or "").strip()
    return bool(d) and not SILENT_MARK.match(d)


def _plain(visual: str) -> str:
    """去掉时间戳、**保留全部正文**（含第一个标记之前的总起句）。

    ⚠️ 时间戳的剥离**不自己写正则**——直接走 `storyboard.split_beats` /
    `beat_prefix` 拿正文。本仓库刚为"文档抄一份判据、代码改了文案没跟着改"付过账
    （"静帧取最后一拍"）。实测本项目写的是 `0-2s：`（拉丁 s），
    照"秒"字写正则会把标记当成子句留下。
    """
    beats = storyboard.split_beats(visual)
    if not beats:
        return visual
    segs = ([p] if (p := storyboard.beat_prefix(visual)) else [])
    segs += [t for _a, _b, t in beats if t]
    return "；".join(segs)


def _clauses(visual: str) -> list[str]:
    """把镜内正文按子句切开（时间戳剥掉后按逗号/顿号/分号），**顺序不变**。"""
    return [c.strip(" 。\n\t") for c in CLAUSE_SPLIT.split(_plain(visual or ""))
            if len(c.strip(" 。\n\t")) >= 2]


def strip_marks(visual: str) -> str:
    """PROSE 臂：同样的子句、同样的顺序，**只是没有时间戳**。"""
    return _plain(visual or "").strip()


def to_half(visual: str, span: float, step_want: float = 0.5) -> str:
    """每个子句一个时间戳，步长 `step_want`（默认 0.5 秒）。

    子句多到窗口装不下这个步长时，**降到恰好装得下的步长**（不丢子句、不越界）——
    所以打印出来的实际密度可能比 0.5 秒/拍更密，报告里按实际读数说。
    """
    cs = _clauses(visual)
    if not cs:
        return visual
    step = min(step_want, round(float(span) / len(cs), 2)) or 0.01
    stamps, cur = [], 0.0
    for c in cs:
        nb = min(round(cur + step, 2), float(span))
        stamps.append([cur, nb, c])
        cur = nb
    stamps[-1][1] = float(span)                   # 末拍收到本镜右界，不留缝隙
    # 不再单独前置总起句：`_clauses` 走 `_plain`，**已经把标记前的锚点收进子句序列**
    # （再拼一次会让锚点在同一镜里出现两遍 —— 而"同一个人被描述两次会多画一个人"
    #  正是本仓库记过的实测病）。
    return "；".join("%s-%s秒：%s" % (storyboard.beat_label(a),
                                      storyboard.beat_label(b), c)
                     for a, b, c in stamps)


def rewrite(group: list[dict], declared: list[int], arm: str,
            step_want: float = 0.5) -> list[dict]:
    out = []
    for s, span in zip(group, declared):
        v = s.get("visual") or ""
        if arm == "PROD" or arm == "REPEAT":
            nv = v
        elif arm == "PROSE":
            nv = strip_marks(v)
        elif arm == "HALF":
            nv = to_half(v, span, step_want)
        else:
            raise SystemExit("未知臂：%s" % arm)
        out.append(dict(s, visual=nv))
    return out


def conservation(group: list[dict], new_group: list[dict]) -> tuple[list[str], int, int]:
    """内容守恒：各臂的**子句集合必须逐条相同**，只许时间戳不同。"""
    a = [c for s in group for c in _clauses(s.get("visual") or "")]
    b = [c for s in new_group for c in _clauses(s.get("visual") or "")]
    return ([c for c in a if c not in b] + [c for c in b if c not in a], len(a), len(b))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="luanzhen-xue-1001")
    ap.add_argument("--ep", type=int, default=1)
    ap.add_argument("--group", default="", help="packNN；缺省取第一个**无台词**的组")
    ap.add_argument("--arms", default="PROD,PROSE,HALF,REPEAT")
    ap.add_argument("--step", type=float, default=0.5,
                    help="HALF 臂每拍的期望步长（秒），默认 0.5")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default="tmp")
    a = ap.parse_args()

    root = ROOT / "projects" / a.project
    epdir = root / "media" / ("ep%d" % a.ep)
    md = (root / "scenedesigner" / ("scenedesigner_ep%d.md" % a.ep)).read_text(encoding="utf-8")
    by = {s["name"]: s for s in storyboard.parse(md)}
    jobs = json.loads((epdir / "video_jobs.json").read_text(encoding="utf-8"))
    jobs = jobs.get("jobs", jobs)

    gname = a.group
    if not gname:
        # 优先挑**台词最少**的组：人眼读帧时台词会干扰"这一拍做没做"的判断。
        # 实测本仓库最近的连载每集都有台词镜（`luanzhen-xue-1001` 六组无一全无声），
        # 所以这里**不硬要求全无声**，取最少的那一组即可。
        ranked = sorted(
            ((sum(has_speech(by.get(n) or {}) for n in (jobs[k].get("shots") or [])),
              k) for k in sorted(jobs) if jobs[k].get("shots")),
            key=lambda t: (t[0], t[1]))
        gname = ranked[0][1] if ranked else ""
    if not gname or gname not in jobs:
        raise SystemExit("找不到合适的组（--group 指定一个）")
    rec = jobs[gname]
    names, declared = rec["shots"], rec["declared_seconds"]
    total = sum(declared)
    group = [by[n] for n in names]

    stills_raw = json.loads((epdir / "stills.json").read_text(encoding="utf-8"))
    items = stills_raw.get("items") if isinstance(stills_raw.get("items"), dict) else stills_raw
    own = {n: (items.get(n) or {}).get("url") for n in names}
    if not all(own.values()):
        raise SystemExit("缺静帧 URL：%s" % [n for n, u in own.items() if not u])
    # 参考图走**生产那一份槽位判据**（A 臂图序），探针不换变量
    urls, roles = video.pack_ref_images(root, group, own, prev_url=None, ep=a.ep)
    sb = style.wrap(style.load(root))

    print("[bd] 项目 %s / 组 %s = %s 分配秒 %s 合计 %ds；参考图 %d 张"
          % (a.project, gname, "+".join(names), declared, total, len(urls)))
    for i, r in enumerate(roles, 1):
        print("[bd]   第%d张 = %s" % (i, r[1]))

    base_beats = sum(len(storyboard.split_beats(s.get("visual") or "")) for s in group)
    arms = {}
    for arm in [x.strip().upper() for x in a.arms.split(",") if x.strip()]:
        g2 = rewrite(group, declared, arm, a.step)
        missing, n0, n1 = conservation(group, g2)
        if missing:
            raise SystemExit("[%s] 内容不守恒（不该发生）：%s" % (arm, missing[:4]))
        beats = sum(len(storyboard.split_beats(s.get("visual") or "")) for s in g2)
        text = prompt_mod.build_pack_prompt(g2, declared, total, style_block=sb,
                                            ref_roles=roles)
        arms[arm] = (text, beats)
        print("[bd] %-7s 拍数 %2d（生产 %d）密度 %.2f 拍/秒  文字 %d 字  子句 %d 条（守恒 ✓）"
              % (arm, beats, base_beats, beats / total, len(text), n1))

    if a.dry_run:
        print("\n===== HALF 臂正文（前 900 字）=====\n")
        print(arms["HALF"][0][:900])
        return 0

    out = ROOT / a.out
    out.mkdir(exist_ok=True)
    keys = list(config.AGNES_API_KEYS) or [None]
    ar = config.ASPECT_RATIO
    print("[bd] key %d 条、画幅 %s；提交间隔 %ss ⇒ 四臂预计 %d-%d 分钟"
          % (len(keys), ar, config.video_submit_interval_per_key(),
             len(arms) * 3, len(arms) * 9))
    ids = {}
    for i, (arm, (text, _b)) in enumerate(arms.items()):
        for attempt in range(10):
            key = keys[(i + attempt) % len(keys)]
            try:
                r = providers.submit_video(text, mode="reference", images=urls,
                                           seconds=total, aspect_ratio=ar,
                                           timeout=300, key=key)
                ids[arm] = (r.get("video_id"), time.time(), key)
                print("[bd] %s submitted id=%s key=%s…" % (arm, r.get("video_id"),
                                                            str(key)[:8]))
                break
            except Exception as e:                                    # noqa: BLE001
                wait = min(90, 20 * (1 + attempt // max(1, len(keys))))
                print("[bd] %s 提交失败（%d/10）：%s → %ds 后换 key 重试"
                      % (arm, attempt + 1, str(e)[:70], wait))
                time.sleep(wait)
        time.sleep(int(config.video_submit_interval_per_key()))

    pending = dict(ids)
    deadline = time.time() + 60 * 40
    while pending and time.time() < deadline:
        time.sleep(15)
        for arm, (vid, t0, key) in list(pending.items()):
            try:
                q = providers.query_video(vid, key=key)
            except Exception as e:                                    # noqa: BLE001
                print("[bd] %s 查询异常：%s" % (arm, str(e)[:60]))
                continue
            st = q.get("status")
            if st == "completed" and q.get("url"):
                import httpx
                dest = out / ("BD_%s_%s.mp4" % (a.project, arm))
                try:
                    with httpx.Client(timeout=180, trust_env=False) as c:
                        resp = c.get(q["url"])
                        resp.raise_for_status()
                        dest.write_bytes(resp.content)
                    print("[bd] %s done → %s（%.1f MB，%.0fs）"
                          % (arm, dest.name, dest.stat().st_size / 1e6, time.time() - t0))
                    pending.pop(arm)
                except Exception as e:                                # noqa: BLE001
                    print("[bd] %s 下载失败：%s" % (arm, str(e)[:80]))
            elif st in ("failed", "error"):
                print("[bd] %s FAILED：%s" % (arm, str(q.get("error"))[:120]))
                pending.pop(arm)
    print("RESULT: %d/%d 臂落盘" % (len(arms) - len(pending), len(arms)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
