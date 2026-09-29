# -*- coding: utf-8 -*-
"""全量强制重生成静帧（不跑 QC）。

为什么单独写这个脚本（教训）：
  pipeline 的 `--stills-only` 内置 QC 自动重滚，但本片的视觉 QC 对**小字**
  概率性漏报（实测 LN12 满墙汉字仍被判"通过"）→ 该镜因此被跳过、从未用新
  提示词重新生成。风格块/尾缀改动后必须**全量重生成**才能验证修复是否生效。

用法：python scripts/gen_all_stills.py <项目名> [--ep=N] [--only=LN21,LN22]

⚠️ 全量重画 = **每镜烧一张图**（`force=True` 不看盘）。只想补几镜就用 `--only=`。
⚠️ 镜数受 `AGNES_VIDEO_MAX_SHOTS` 约束（默认 20）：长片必须调大，否则只重画前 20 镜
   （脚本会打印截断警告，但别指望自己记得）。
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v5 import config  # noqa: E402
from v5.guards import resolve_path  # noqa: E402
from v5.media import assets, relations, stills, storyboard, style  # noqa: E402


def main() -> None:
    # ⚠️ 原默认值 `bootleg99-full` 已随 2026-09-14 的老架构清理删除；请显式传项目名。
    project = next((a for a in sys.argv[1:] if not a.startswith("--")),
                   "<请显式传项目名>")
    ep = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--ep=")), "1")
    root = config.PROJECTS_DIR / project
    # ★ 分镜是**集级**产物（`scenedesigner_ep{N}.md`），必须走 `resolve_path`（旧名回退）。
    #   这里原先硬写 `scenedesigner/scenedesigner.md` ⇒ 2026-09-19 之后的新项目一律
    #   `FileNotFoundError`，而这个脚本正是「风格块改动后必须全量重生成」的唯一入口
    #   —— 入口坏了就等于修复链断在最贵的一环（静帧还留在盘上，看起来一切正常）。
    sb = resolve_path(root, "scenedesigner", int(ep))
    if not sb.exists():
        raise SystemExit("[gen] ⛔ 找不到分镜：%s（项目名/集号对不对？）" % sb)
    shots = storyboard.parse(sb.read_text(encoding="utf-8"))
    # ★ **截断必须说出来**（同 `pipeline._run_impl` 的纪律，2026-09-28 在本脚本上重犯）：
    #   原先无条件 `shots[:VIDEO_MAX_SHOTS]`，而该上限**默认 20** ⇒ 32 镜的片子只重画了
    #   前 20 镜，末尾还打印「完成 20/20」——看起来像"全片重画完了"。
    #   本脚本的唯一用途就是"改完风格块后全量重生成"，漏一半 = 后半片仍是旧审美，
    #   而盘上 32 张 jpg 全在，肉眼扫目录发现不了。
    n_parsed = len(shots)
    shots = shots[: config.VIDEO_MAX_SHOTS]
    if len(shots) < n_parsed:
        print("[gen] ⚠️ 分镜 %d 镜 > 上限 %d → **已截断**，只重画前 %d 镜。"
              "要全片重画请设 AGNES_VIDEO_MAX_SHOTS=%d。"
              % (n_parsed, config.VIDEO_MAX_SHOTS, len(shots), n_parsed))
    only = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--only=")), "")
    if only:
        want = {x.strip() for x in only.replace(",", " ").split() if x.strip()}
        shots = [s for s in shots if s["name"] in want]
        missing = want - {s["name"] for s in shots}
        if missing:
            raise SystemExit("[gen] ⛔ --only 里这些镜号不在分镜中：%s"
                             % "、".join(sorted(missing)))
        print("[gen] 只重画 %d 镜：%s" % (len(shots), "、".join(s["name"] for s in shots)))
def prepare_shots(root, shots, log=print):
    """把 `pipeline._run_impl` 的**逐镜注入**原样做一遍，返回
    `(shots, refs_by_shot, ref_names, ref_types)`。

    单独成函数（2026-09-29）：注入项必须与媒体链**一字不差**，否则"用脚本重画"
    会悄悄画出比 pipeline 更弱的静帧。回归测试
    `tests_flow.TestGenAllStillsParity` 直接调本函数比对提示词。
    """
    blk = style.wrap(style.load(root))
    if blk:
        shots = [{**s, "_style_block": blk} for s in shots]
        log("[gen] 风格块 %d 字" % len(blk))
    else:
        log("[gen] ⚠️ **无风格块**（brief.json 缺 pack 或解析失败）→ 提示词朴素，"
            "重画出来的静帧没有本包审美。先修 brief 再跑")
    idl = assets.identity_lines(root, shots)
    shots = [{**s, "_identity_line": idl.get(s["name"], "")} for s in shots]
    log("[gen] 身份锚点 %d/%d 镜" % (len(idl), len(shots)))
    # ★ **场景锚点 + 出场角色数**（2026-09-29 补的第二处漏注入）：
    #   本脚本原先只注 `_style_block` / `_identity_line`，于是画出来的静帧提示词
    #   比 `pipeline._run_impl` 的**弱**（同一镜实测差 87 字）：
    #     · 丢 `_cast_n` ⇒ 多人镜被注「画面中只有一个人物，且仅出现一次」
    #       （这正是 2026-09-15 pipeline 修过的事故，脚本把它**重新引入**了）；
    #     · 丢 `_scene_line` ⇒ 场景只剩一个名字「场景：华山金顶论剑石台」，
    #       注册表里的陈设/光线描述全丢 ⇒ 场景漂移。
    sl = assets.scene_lines(root, shots, log=log)
    if sl:
        shots = [{**s, "_scene_line": sl.get(s["name"], "")} for s in shots]
        log("[gen] 场景锚点 %d/%d 镜" % (len(sl), len(shots)))
    cn = assets.cast_counts(root, shots)
    if cn:
        shots = [{**s, "_cast_n": cn.get(s["name"], 0)} for s in shots]
        log("[gen] 出场角色数 %d 镜（多人镜 %d）"
            % (len(cn), sum(1 for v in cn.values() if v >= 2)))
    # ★ **参考图绑定必须和 `pipeline._run_impl` 一模一样**（2026-09-28 补）：
    #   本脚本原先只传 `planned` + `_identity_line`，`refs_by_shot` 永远是空 ⇒
    #   重画出来的静帧**没有定妆照锚点**（提示词里连「第 1 张参考图=…」都没有），
    #   身份一致性归零。而它正是"改完风格块后全量重画"的唯一入口：
    #   用它重画 = 把 pipeline 修好的绑定规则**整批抹掉**，
    #   且 32 张 jpg 一张不少、日志全绿，事后根本看不出来。
    ref_names: dict = {}
    ref_types: dict = {}
    if style.still_refs_enabled(root):
        refs_by_shot = assets.bind(root, shots, names_out=ref_names, types_out=ref_types)
        log("[gen] 参考图绑定 %d/%d 镜" % (len(refs_by_shot), len(shots)))
    else:
        refs_by_shot = {}
        log("[gen] pack 关闭参考图（still-refs=false）→ 风格由风格块+文字锚点锁定")
    return shots, refs_by_shot, ref_names, ref_types


def main() -> None:
    # ⚠️ 原默认值 `bootleg99-full` 已随 2026-09-14 的老架构清理删除；请显式传项目名。
    project = next((a for a in sys.argv[1:] if not a.startswith("--")),
                   "<请显式传项目名>")
    ep = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--ep=")), "1")
    root = config.PROJECTS_DIR / project
    # ★ 分镜是**集级**产物（`scenedesigner_ep{N}.md`），必须走 `resolve_path`（旧名回退）。
    #   这里原先硬写 `scenedesigner/scenedesigner.md` ⇒ 2026-09-19 之后的新项目一律
    #   `FileNotFoundError`，而这个脚本正是「风格块改动后必须全量重生成」的唯一入口
    #   —— 入口坏了就等于修复链断在最贵的一环（静帧还留在盘上，看起来一切正常）。
    sb = resolve_path(root, "scenedesigner", int(ep))
    if not sb.exists():
        raise SystemExit("[gen] ⛔ 找不到分镜：%s（项目名/集号对不对？）" % sb)
    shots = storyboard.parse(sb.read_text(encoding="utf-8"))
    # ★ **截断必须说出来**（同 `pipeline._run_impl` 的纪律，2026-09-28 在本脚本上重犯）：
    #   原先无条件 `shots[:VIDEO_MAX_SHOTS]`，而该上限**默认 20** ⇒ 32 镜的片子只重画了
    #   前 20 镜，末尾还打印「完成 20/20」——看起来像"全片重画完了"。
    n_parsed = len(shots)
    shots = shots[: config.VIDEO_MAX_SHOTS]
    if len(shots) < n_parsed:
        print("[gen] ⚠️ 分镜 %d 镜 > 上限 %d → **已截断**，只重画前 %d 镜。"
              "要全片重画请设 AGNES_VIDEO_MAX_SHOTS=%d。"
              % (n_parsed, config.VIDEO_MAX_SHOTS, len(shots), n_parsed))
    only = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--only=")), "")
    if only:
        want = {x.strip() for x in only.replace(",", " ").split() if x.strip()}
        shots = [s for s in shots if s["name"] in want]
        missing = want - {s["name"] for s in shots}
        if missing:
            raise SystemExit("[gen] ⛔ --only 里这些镜号不在分镜中：%s"
                             % "、".join(sorted(missing)))
        print("[gen] 只重画 %d 镜：%s" % (len(shots), "、".join(s["name"] for s in shots)))
    shots, refs_by_shot, ref_names, ref_types = prepare_shots(root, shots)
    planned = relations.plan_frames(shots)
    started = time.time()
    stills.ensure(root, shots, refs_by_shot=refs_by_shot, ep=int(ep), force=True,
                  planned=planned, log=print,
                  ref_names_by_shot=ref_names, ref_types_by_shot=ref_types)
    # ★ **成功数按"本轮真的产出了新图"算，不按"文件在不在盘上"算**（2026-09-28）。
    #   原写法 `exists(jpg)` 会把**上一轮的旧图**也计成成功：实测国内生图入口 504
    #   打挂了 6 镜（重试 3 次全败），脚本照样打印「完成 32/32」，
    #   而那 6 张里 4 张还是**没有风格块**的降级图——正是本脚本要消灭的东西。
    sd = root / "media" / ("ep" + str(ep)) / "stills"
    fresh = {s["name"] for s in shots
             if (sd / (s["name"] + ".jpg")).exists()
             and (sd / (s["name"] + ".jpg")).stat().st_mtime >= started}
    stale = [s["name"] for s in shots if s["name"] not in fresh]
    print("[gen] 本轮新产出 %d/%d 镜（%s 上还有旧图）"
          % (len(fresh), len(shots), "以下镜" if stale else "无"))
    if stale:
        print("[gen] ⛔ **失败未重画**：%s" % "、".join(stale))
        print("[gen]    这些镜仍在使用**上一轮的静帧**（本轮生图 504/超时，重试 3 次全败）。"
              "先重跑本脚本补画（`--only=` 只补这几镜），**别直接进视频**——"
              "旧图的风格与参考图锚点可能都不是本轮的。")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
