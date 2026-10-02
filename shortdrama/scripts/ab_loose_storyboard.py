# -*- coding: utf-8 -*-
"""A/B：**把创作约束从分镜师身上摘掉，会交出什么**（2026-10-02）。

用户的要求：除「一次生成 4–12 秒」「一次最多 5 张参考图」这两条供应商硬墙之外，
节奏/镜长/景别/切镜密度全部交回类型包 SKILL 与分镜自己，**不限制也不建议**。

本脚本把这件事做成一次可比的对读，而不是永久删契约——那些硬性要求里有多条是拿事故
换来的（锚点断链→大衣漂红、道具名漂移→一张图没绑、图比人多→多画一个人）。

    A 臂（tight） 现行：注入 8 条「硬性要求」+ 出片容器事实 + 程序体检
    B 臂（loose） `SHORTDRAMA_LOOSE_STORYBOARD=1`：这些**一条都不给**

两臂只差这一个开关：同一个项目、同一份上游、同一个包的分镜 SKILL、同一个模型、
**同一个角色图**（走 `orchestrator._build_role_graph`，带文件工具，产物由它自己写盘）。

⚠️ 为什么必须走角色图而不是"直接问模型一次"（第一版就是这么写的，两臂都交出 0 镜）：
· 分镜角色是**带文件工具的代理**，直接单轮调用时它会输出 `<tool_call>ls</function>`
  这种文本，或者只回一段【分析】——生产 input 里带着"上游产物 + 体检退回清单"，
  它以为自己是来改表的，不是来写表的；
· 所以两臂都跑在**没有分镜产物的项目副本**上（`tmp/AB/proj_<臂>/`），
  让它从零写一张，才是"分镜师拿到这份输入会怎么排"的真读数。

用法：
    python scripts/ab_loose_storyboard.py --project luanzhen-xue-1001 --ep 1 [--render]
产物（全部落 tmp/AB/，**不写 projects/ 本体**）：
    tmp/AB/proj_<臂>/scenedesigner/scenedesigner_ep<N>.md   两臂的分镜表
    tmp/AB/BD_<项目>_<臂>.mp4                                 --render 时各渲第一组
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

WIDE = ("全景", "远景", "大全景", "空镜")
#: 「（无声，环境音）」「无台词」这类是**无声标记**，不算有台词的镜。
SILENT = re.compile(r"^\s*[（(]?\s*(无台词|无声|无对白|无)\s*[^)）]*[)）]?\s*$")
COPY_DIRS = ("images", "worldbuilder", "assetdesigner", "plotdesigner",
             "scriptwriter", "dialogue", "reviewer", "director")
COPY_FILES = ("brief.json", "assets.json", ".agent_state.json", "style.md")


def has_speech(shot: dict) -> bool:
    d = (shot.get("dialogue") or "").strip()
    return bool(d) and not SILENT.match(d) and len(d) > 4


def prepare_scratch(src: Path, dst: Path, ep: int) -> Path:
    """项目副本：**删掉本集分镜产物** + **把 manifest 的集号钉到本集**。"""
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    for rel in COPY_FILES:
        f = src / rel
        if f.exists():
            shutil.copy2(f, dst / rel)
    for d in COPY_DIRS:
        s = src / d
        if s.exists():
            shutil.copytree(s, dst / d)
    (dst / "scenedesigner").mkdir(exist_ok=True)
    # ★ 集号必须改写：角色节点用的是 `load_manifest(root)['episode_index']`（不是我们
    #   传的参数）。实测 `luanzhen-xue-1001` 的账本停在第 2 集 ⇒ 不改写的话分镜师会
    #   去写 `scenedesigner_ep2.md`，本脚本回头找 ep1 的文件，两臂都"没产物"。
    st = dst / ".agent_state.json"
    if st.exists():
        d = json.loads(st.read_text(encoding="utf-8"))
        d["episode_index"] = ep
        st.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return dst


def run_arm(project: str, ep: int, arm: str, out: Path) -> tuple[Path, int, int]:
    """跑一臂：绑定副本 → 起分镜角色图 → 返回（分镜表路径, 注入字符数, 少掉多少字）。"""
    from langchain_core.messages import HumanMessage

    from v5 import config, orchestrator, roles

    src = ROOT / "projects" / project
    scratch = prepare_scratch(src, out / ("proj_%s" % arm), ep)
    pack = json.loads((src / "brief.json").read_text(encoding="utf-8")).get("pack") or "shortdrama"

    old_root, old_ep = orchestrator._root, orchestrator._EP
    old_loose = config.LOOSE_STORYBOARD
    orchestrator._root, orchestrator._EP = scratch, ep
    config.LOOSE_STORYBOARD = (arm == "loose")
    try:
        # 开工契约的字符数差 = 这一臂到底少看了多少字（先算，别等产物）
        tight_len = len(roles.role_input("scenedesigner", scratch, {"episode_index": ep}))
        graph = orchestrator._build_role_graph("scenedesigner", pack)
        asyncio.run(graph.ainvoke({"messages": [HumanMessage(
            content="按开工契约写第 %d 集分镜表，写完即停。" % ep)]},
            config={"recursion_limit": 60}))
        loose_len = len(roles.role_input("scenedesigner", scratch, {"episode_index": ep}))
    finally:
        orchestrator._root, orchestrator._EP = old_root, old_ep
        config.LOOSE_STORYBOARD = old_loose
    md = scratch / "scenedesigner" / ("scenedesigner_ep%d.md" % ep)
    n_loose, n_tight = (loose_len, tight_len) if arm == "loose" else (tight_len, loose_len)
    return md, n_loose if arm == "loose" else n_tight, abs(n_loose - n_tight)


def stats(md: Path) -> dict:
    from v5.media import storyboard, video_plan

    shots = storyboard.parse(md.read_text(encoding="utf-8"))
    secs = [video_plan.pack_clamp_sec(s) for s in shots]
    beats = [len(storyboard.split_beats(s.get("visual") or "")) for s in shots]
    return {"shots": len(shots), "sec_total": sum(secs),
            "sec_min": min(secs) if secs else 0, "sec_max": max(secs) if secs else 0,
            "sec_med": statistics.median(secs) if secs else 0,
            "uniform": len(set(secs)) == 1 and len(secs) >= 4,
            "beats": sum(beats),
            "beat_per_sec": (sum(beats) / sum(secs)) if secs and sum(secs) else 0.0,
            "wide": sum(1 for s in shots
                        if any(w in (s.get("shot_type") or "") for w in WIDE)),
            "scenes": len({(s.get("scene") or "").strip()
                           for s in shots if (s.get("scene") or "").strip()}),
            "dial_shots": sum(1 for s in shots if has_speech(s)),
            "dial_chars": sum(len(re.findall(r"[一-鿿]", s.get("dialogue") or ""))
                              for s in shots),
            "no_beat_shots": sum(1 for b in beats if b == 0),
            "rows": shots}


def render_first_group(scratch: Path, ep: int, arm: str, out: Path, project: str):
    """在**同一个副本**里用真实媒体链渲第一组（≤12 秒）。

    ⚠️ 不手拼静帧/提交：`pipeline.run` 那侧有一串逐镜注入（风格块/场景锚点/身份线/
    人数声明/参考图绑定），手抄一份必然漏项（2026-09-16 就是这么烧过一轮）。
    走同一入口的受限调用：`only=` 第一组、`from_still=True` 连静帧重画。
    """
    from v5.media import pipeline, storyboard, video_plan

    shots = storyboard.parse(
        (scratch / "scenedesigner" / ("scenedesigner_ep%d.md" % ep)).read_text(encoding="utf-8"))
    if not shots:
        print("[ab] %s 臂解析不出镜 → 不渲" % arm, flush=True)
        return None
    group, declared = video_plan.group_shots(shots)[0]
    names = [s["name"] for s in group]
    print("[ab] %s 臂渲第一组 %s = %s 秒" % (arm, "+".join(names), sum(declared)),
          flush=True)
    r = pipeline.run(scratch, ep=ep, only=names, from_still=True,
                     log=lambda *x, **k: print(*x, **k, flush=True))
    print("[ab] %s 臂 run → %s" % (arm, str(r)[:160]), flush=True)
    cd = scratch / "media" / ("ep%d" % ep) / "clips"
    for cand in ("pack01.mp4", names[0] + ".mp4"):
        if (cd / cand).exists():
            dest = out / ("BD_%s_%s.mp4" % (project, arm))
            shutil.copy2(cd / cand, dest)
            print("[ab] %s 臂成片 → %s" % (arm, dest), flush=True)
            return dest
    print("[ab] !! %s 臂没有成片" % arm, flush=True)
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", default="luanzhen-xue-1001")
    ap.add_argument("--ep", type=int, default=1)
    ap.add_argument("--arms", default="tight,loose")
    ap.add_argument("--render", action="store_true", help="各渲第一组出片对比")
    ap.add_argument("--out", default="tmp/AB")
    a = ap.parse_args()

    src = ROOT / "projects" / a.project
    if not (src / "brief.json").exists():
        raise SystemExit("项目不存在：%s" % src)
    os.environ.setdefault("SHORTDRAMA_V5_PROJECT", a.project)   # 别让 import 建出 studio/
    out = ROOT / a.out
    out.mkdir(parents=True, exist_ok=True)

    res, inputs = {}, {}
    for arm in [x.strip() for x in a.arms.split(",") if x.strip()]:
        print("[ab] %s 臂开工（loose=%s）…" % (arm, arm == "loose"), flush=True)
        md, in_len, delta = run_arm(a.project, a.ep, arm, out)
        inputs[arm] = (in_len, delta)
        if not md.exists():
            print("[ab] !! %s 臂没写产物：%s" % (arm, md), flush=True)
            continue
        st = stats(md)
        res[arm] = st
        print("[ab] %s → 开工契约 %d 字（与另一臂差 %d）；解析出 %d 镜 / %s 秒"
              % (arm, in_len, delta, st["shots"], st["sec_total"]), flush=True)

    keys = [("shots", "镜数"), ("sec_total", "总秒数"), ("sec_min", "最短镜"),
            ("sec_med", "中位镜长"), ("sec_max", "最长镜"), ("beats", "节拍数"),
            ("beat_per_sec", "拍/秒"), ("wide", "宽景镜"), ("scenes", "场景数"),
            ("dial_shots", "有台词镜"), ("dial_chars", "台词总字"),
            ("no_beat_shots", "无节拍镜"), ("uniform", "全表等长")]
    lab = lambda v: ("是" if v is True else "否" if v is False
                     else ("%.2f" % v) if isinstance(v, float) else str(v))
    t = res.get("tight", {})
    l = res.get("loose", {})
    print("\n| 指标 | A 现行（有约束） | B 放开（无约束） |")
    print("|---|---|---|")
    for k, name in keys:
        print("| %s | %s | %s |" % (name, lab(t.get(k, "—")), lab(l.get(k, "—"))))
    if t.get("rows") and l.get("rows"):
        from v5.media import video_plan
        print("\n[ab] 每镜秒数  A=%s\n           B=%s" % (
            [video_plan.pack_clamp_sec(s) for s in t["rows"]][:24],
            [video_plan.pack_clamp_sec(s) for s in l["rows"]][:24]))
        gt = video_plan.group_shots(t["rows"])
        gl = video_plan.group_shots(l["rows"])
        print("[ab] 分组 A=%d 组 %s  B=%d 组 %s"
              % (len(gt), [len(g) for g, _ in gt], len(gl), [len(g) for g, _ in gl]))
    if a.render:
        for arm in [x.strip() for x in a.arms.split(",") if x.strip()]:
            if res.get(arm, {}).get("shots"):
                render_first_group(out / ("proj_%s" % arm), a.ep, arm, out, a.project)
    print("RESULT: A/B 完成（%d/%d 臂有产物%s）" % (
        len(res), len(a.arms.split(",")), "，含渲片" if a.render else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
