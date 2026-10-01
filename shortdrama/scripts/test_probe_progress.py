# -*- coding: utf-8 -*-
"""probe_progress 自检：造一个临时项目树，把当天那几种病各装一遍。

为什么不用真项目当夹具：真项目现在恰好是"健康"的（两集都有成片），拿它测不出
检测逻辑有没有生效 —— 0 条告警会让所有判据"全过"。所以这里显式造病样本：
缺 5 镜、审片报告比驱动日志旧、force_passed 蹭到隔壁集。

用法：python scripts/test_probe_progress.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe_progress import build_report, main  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s%s" % (name, (" — " + str(detail)[:200]) if detail else ""))
        FAILURES.append(name)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    io.open(str(path), "w", encoding="utf-8").write(text)


def make_tree(root, project, ep, jobs, clips, review_passed, review_age_s=0,
              force=False, stills=None, final=True):
    pdir = Path(root) / "projects" / project
    _write(pdir / ".agent_state.json", json.dumps({
        "episode_index": ep,
        "phases": {str(ep): {"reviewer": "complete"}},
        "review": {"passed": review_passed, "rerun": [], "reasons": [],
                   "force_passed": force},
    }))
    _write(pdir / "reviewer" / ("review_ep%d.md" % ep),
           "verdict: %s\n" % ("pass" if review_passed else "fail"))
    (pdir / "reviewer" / ("review_ep%d.md" % ep)).touch()
    if review_age_s:
        old = time.time() - review_age_s
        os.utime(str(pdir / "reviewer" / ("review_ep%d.md" % ep)), (old, old))
    media = pdir / "media" / ("ep%d" % ep)
    _write(media / "video_jobs.json", json.dumps(
        {k: {"state": "completed"} for k in jobs}))
    _write(media / "stills.json", json.dumps(
        {k: {"file": k + ".png"} for k in (stills if stills is not None else jobs)}))
    for c in clips:
        _write(media / "clips" / (c + ".mp4"), "x")
    if final:
        _write(media / "episode_final.mp4", "x")
    log = Path(root) / ".tmp" / "recover.log"
    _write(log, "[t] 阶段开始\n")
    return pdir


def report(root, project, eps=None):
    return build_report(Path(root), project, eps)


def case_healthy(tmp):
    ids = ["LN01", "LN02", "LN03"]
    make_tree(tmp, "ok-proj", 1, ids, ids, True)
    rep = report(tmp, "ok-proj")
    e = rep["episodes"][0]
    check("健康集：镜数取自任务表而不是分镜 md", e["shots"] == ids, e["shots"])
    check("健康集：无缺镜", e["clips_missing"] == [], e["clips_missing"])
    check("健康集：一条告警都不该有", e["notes"] == [], e["notes"])
    check("健康集：退出码 0", main(["ok-proj", "--root", tmp]) == 0)


def case_missing_clips(tmp):
    jobs = ["LN%02d" % i for i in range(1, 17)]
    clips = [j for j in jobs if j not in ("LN07", "LN08", "LN10", "LN11", "LN13")]
    make_tree(tmp, "miss-proj", 1, jobs, clips, False, force=True)
    e = report(tmp, "miss-proj")["episodes"][0]
    check("缺 5 镜时算出 5 个（当天真实形状）", e["clips_missing"] ==
          ["LN07", "LN08", "LN10", "LN11", "LN13"], e["clips_missing"])
    check("缺镜要写进告警", any("缺 5 镜" in n for n in e["notes"]), e["notes"])
    check("审片不过且 force 在本集 → 标强制放行", e["force_passed"] is True)
    check("缺镜时退出码非 0", main(["miss-proj", "--root", tmp]) == 1)


def case_review_age_is_a_fact_not_a_verdict(tmp):
    """审片报告的时间只印出来，不据此判"没重写"。

    反向对照必须留在这里：早先版本拿"报告比驱动日志旧"当判据，结果任何
    跑完的正常回合都被判成可疑（驱动最后写的是 DRIVER-DONE，必然晚于报告）。
    误报两次之后这条告警就没人看了 —— 比没有更糟。
    """
    ids = ["LN01"]
    make_tree(tmp, "stale-proj", 1, ids, ids, False, review_age_s=3600)
    e = report(tmp, "stale-proj")["episodes"][0]
    check("报告 1 小时没动 → 时间要印出来", abs((time.time() - e["review"]["mtime"]) - 3600) < 5,
          e["review"]["mtime"])
    check("但不据此报「未重写」", not any("未重写" in n for n in e["notes"]), e["notes"])
    check("健康完成态一条告警都不该有（除 force 外）",
          [n for n in e["notes"] if "缺" not in n] == [], e["notes"])
    from probe_progress import render_text
    text = render_text(report(tmp, "stale-proj"))
    check("人话输出里能看到报告时间", "报告 1 小时" in text, text)


def case_force_not_leaked(tmp):
    root = Path(tmp)
    jobs = ["LN01"]
    make_tree(tmp, "leak-proj", 1, jobs, jobs, False, force=True)
    # ep2 自己判过，但状态里的 force_passed 是 ep1 留下的
    pdir = root / "projects" / "leak-proj"
    _write(pdir / "reviewer" / "review_ep2.md", "verdict: pass\n")
    e2 = [x for x in report(tmp, "leak-proj", [2])["episodes"] if x["ep"] == 2][0]
    check("ep2 自己判过 → 不蹭 ep1 的强制放行", e2["force_passed"] is False, e2)


def case_namespaces_differ_no_bogus_note(tmp):
    """静帧与任务表用不同 id 命名空间时，不许报"镜集不一致"。

    形状取自 luanzhen-xue-1001 实测：媒体链按 pack01..pack06 组织，静帧是逐镜的
    LN01..LN12。差集永远非空，据此告警就是误报机器 —— 同一天在"报告比日志旧"
    那条上已经栽过一次，这条反向对照就是防止它被加回来。
    """
    packs = ["pack%02d" % i for i in range(1, 7)]
    stills = ["LN%02d" % i for i in range(1, 13)]
    make_tree(tmp, "pack-proj", 1, packs, packs, True, stills=stills)
    e = report(tmp, "pack-proj")["episodes"][0]
    check("pack 命名空间下不缺件 → 零告警", e["notes"] == [], e["notes"])
    check("任务数按任务表算，不被静帧数污染", e["shots"] == packs, e["shots"])
    # 反过来：真缺件时仍然要报，且报的是任务表里的 id
    make_tree(tmp, "pack-bad", 1, packs, packs[:4], True, stills=stills)
    e2 = report(tmp, "pack-bad")["episodes"][0]
    check("真缺 pack05/06 时照样报出来", e2["clips_missing"] == ["pack05", "pack06"],
          e2["clips_missing"])


def case_no_final(tmp):
    ids = ["LN01"]
    make_tree(tmp, "nofinal-proj", 1, ids, ids, True, final=False)
    e = report(tmp, "nofinal-proj")["episodes"][0]
    check("没成片要直说", any("无成片" in n for n in e["notes"]), e["notes"])
    check("没成片时退出码非 0", main(["nofinal-proj", "--root", tmp]) == 1)


def run():
    for case in (case_healthy, case_missing_clips, case_review_age_is_a_fact_not_a_verdict,
                 case_namespaces_differ_no_bogus_note, case_force_not_leaked, case_no_final):
        tmp = tempfile.mkdtemp(prefix="probe-progress-")
        try:
            case(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILURES:
        print("%d 项失败：%s" % (len(FAILURES), ", ".join(FAILURES)))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(run())
