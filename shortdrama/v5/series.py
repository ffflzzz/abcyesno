# -*- coding: utf-8 -*-
"""v5 媒体链入口（创作链已迁至 supervisor 架构，见 orchestrator.py）。

用法：
  # 唯一被允许的常规调用：全链路（创作链 → 评审 → 媒体链）
  python -m v5.series <project> --brief projects/<p>/brief.json [--pack shortdrama]

  # 只读旁监控（调试期外部 Agent 唯一可用的模式）
  python -m v5.series <project> --monitor [--events N] [--seconds S]

  # 断点续跑媒体链（**仅内部恢复用**，需显式设 SHORTDRAMA_ALLOW_RESUME=1）
  SHORTDRAMA_ALLOW_RESUME=1 python -m v5.series <project> --resume-media

  # 单镜重渲（人工看完片子后定向修某一镜；**不需要**上面的放行）
  python -m v5.series <project> --rerender LN03 [--from still] [--note "理由"]

## 为什么 `--rerender` 不需要放行

`--resume-media` 要放行，是因为它是**第二个入口**：它直接调媒体链，绕过了
创作链的三道输入门（brief / 分镜契约 / 资产契约）—— 2026-09-10 的血案是
"8 角色没跑完 + 评审没通过照样渲染"，且 `media_loop.rendered` 记账被绕过 →
重复渲染烧配额。

`--rerender` 不是第二个入口：它是**媒体链唯一入口的受限调用**
（`pipeline.run(only=[...])`），`media_gate("render")`、三道输入门的产出、
记账、幂等、限流全部照常，只是把作用域收窄到指定镜。而且它**不引入新输入**
（分镜是冻结的），所以没有"绕门"这个风险面。设计依据见
`../docs-archive-20260918/spec-qc-modes-and-rerender.md` §3。

## 调试期铁律：外部 Agent 只旁监控，不插手、不代跑

外部编排 Agent（WorkBuddy / Codex / 其他）**禁止**绕过创作链插手系统：
- 不许手喂 `scenedesigner.md` 后走 `--resume-media` 直接渲染；
- 不许代跑单角色节点、手改 `.agent_state.json`、手工删 clip 逼重渲；
- 唯一允许的动作是 `--monitor`：只读观察，发现问题**停下来报告**，由人来决定。

因此 `--media-only`（旧名）**已删除**；`--resume-media` / `--stills-only` 默认
拒绝，仅当环境变量 `SHORTDRAMA_ALLOW_RESUME=1` 时才放行——这个变量由人显式设置，
外部 Agent 不应设置它。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from v5 import config  # noqa: E402
# ★ 模块级导入 `guards`（2026-09-17）：**根除"门里用了未导入的名字"这一类 bug**。
#   真机事故：`_storyboard_gate` / `_assets_gate` 在 M1 改成 `guards.resolve_path(...)`
#   之后**没有导入 guards** ⇒ `--resume-media` 的自检直接 NameError。表面症状只是
#   「输入门未过 → 不启动媒体链」（Traceback 只在 gates.log 里），
#   而 `--chain-only` **不走门预检**、也没有任何测试真的调过 `_input_gates`
#   ⇒ 一路漏到真机才炸。`guards` 只依赖 `config`，模块级导入**无循环风险**。
from v5 import guards  # noqa: E402

# 旁路放行开关：只有人显式设置才允许 resume/stills-only。
#
# 2026-09-10 策略变更（用户决策）：`--monitor` 只读铁律是**调试期纪律**，
# 不是架构——外部 Agent 本是系统的主入口，长期锁死会限制可用性。
# 现改为「开放 + 三道门守输入」：
#   · `config.OPEN_CHAIN`（SHORTDRAMA_OPEN_CHAIN=1）→ 直接放行，靠三道门把关
#   · 否则维持旧行为：需显式设 SHORTDRAMA_ALLOW_RESUME=1
# 默认关是稳妥的迁移策略：机制先就位，由人按项目逐个放开。
RESUME_ENV = "SHORTDRAMA_ALLOW_RESUME"


def _resume_allowed() -> bool:
    import os
    return bool(config.OPEN_CHAIN) or os.environ.get(RESUME_ENV, "") == "1"


def bind_episode(root: Path, ep: int) -> dict:
    """绑定「本集」：写 `episode_index`。**`episode_index` 的唯一写入方。**

    改造前 `episode_index` 是 **5 处读、0 处写**（恒为 1）⇒ 集级产物永远落在第 1 集，
    "第 2 集覆盖第 1 集"。M1 把它变成真值。

    ⚠️ **M2 起 `phases` 是「二维」（按集隔离）** → 换集**不再清空全账**
    （第 1 集的名册保留，回头重渲第 1 集仍认得自己跑完了）。
    仍要在换集时清零的只有两项，且都是**语义上"每集重新开始"**：
      · `revision_counts` —— 每集的回退预算必须独立，否则第 1 集用光后第 2 集不再回退；
      · `token_usage`     —— 每集是一次新的运行，否则跑几集就撞上 run 级熔断。
    对 `review` / `media_loop`：两者仍是**一维**，但都由**按集**的读/写路径消费
    （`reconcile_manifest(ep)` 从 `review_ep{N}.md` 重解析补记；
    `media_loop_set(m, ep)` 取本集子表）—— 所以第 2 集不会读到第 1 集的结论。

    ★ 为什么抽成函数并在 **CLI 上暴露 `--bind-only`**：集号在**起服前**就必须确定
    （角色 system prompt 里的产物路径是编译期固化的，见 `guards.boot_episode`），
    而 `run_new_project.py` 需要在**起 dev 之前**把 `episode_index` 落盘 ——
    否则角色按第 N 集写盘、`post_validate` 却按第 1 集校验 → 记账全判 `failed`
    （静默、且日志看不出原因）。抽成一个函数，保证**只有一个写入方**。
    """
    from v5.guards import load_manifest, save_manifest
    m = load_manifest(root)
    prev = int(m.get("episode_index", 1) or 1)
    if prev != int(ep):
        m["revision_counts"] = {}
        m["token_usage"] = {"run": 0, "roles": {}}
        # `review` 一维 ⇒ 换集必须清（第 2 集不能拿第 1 集的 pass）：媒体门前
        # 一定会用**本集**的 review 文件重新解析补记，清掉是安全的。
        m.pop("review", None)
        m.pop("media_loop", None)
        print("[series] 切到第 %d 集（原 %d）→ 已重置本轮回退计数/token 账、清上一集评审"
              "（phases 按集隔离，保留）" % (int(ep), prev))
    m["episode_index"] = int(ep)
    save_manifest(root, m)
    return m


def parse_episodes(spec: str) -> list[int]:
    """把 `--episodes` 的写法解析成集号列表。

    支持 `"3"`（只第 3 集）/ `"1-4"`（1..4）/ `"1,3,5"`（离散）/ `"1-3,7"`（混写）。
    **升序、去重、全部 ≥1**；解析不了就**响亮报错**（不静默跑成默认值 —— 那会
    在无人察觉的情况下跑错集）。
    """
    out: list[int] = []
    for part in str(spec or "").replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, _, b = part.partition("-")
            try:
                lo, hi = int(a), int(b)
            except ValueError:
                raise SystemExit("--episodes 无法解析：%r（形如 1-4 / 3 / 1,3,5）" % spec)
            if lo > hi:
                raise SystemExit("--episodes 区间反了：%s" % part)
            out.extend(range(lo, hi + 1))
        else:
            try:
                out.append(int(part))
            except ValueError:
                raise SystemExit("--episodes 无法解析：%r（形如 1-4 / 3 / 1,3,5）" % spec)
    out = sorted({e for e in out if e >= 1})
    if not out:
        raise SystemExit("--episodes 没解析出任何集号：%r" % spec)
    return out


async def run_media_episodes(project: str, spec: str,
                             stills_only: bool = False) -> dict:
    """**M3**：一条命令按集跑媒体链（一次进程、串行、逐集过门）。

    为什么放在这里而不是让调用方自己循环：
      · 每集都要先 `bind_episode(ep)`（`episode_index` 的唯一写入方）+ 过三道输入门
        + 才进 `pipeline.run(ep=ep)` —— 这套顺序是本函数的**唯一职责**，
        散到调用方去就等于把"门"变成可选步骤；
      · 串行是硬约束：视频配额与限速是全局的（并发会撞 `video_queue_full`）。

    **任何一集被门拦下就停止**（不"跳过继续"）—— 静默跳过会让"出了 3 集"
    看起来像"出了 4 集"。要跳集请显式给离散集号（`--episodes 1,4`）。
    """
    root = config.PROJECTS_DIR / project
    root.mkdir(parents=True, exist_ok=True)
    from v5.media import pipeline

    eps = parse_episodes(spec)
    print("[series] 按集跑媒体链：%s（%d 集，串行）" % (eps, len(eps)))
    results: list[dict] = []
    for ep in eps:
        bind_episode(root, ep)
        data = None
        bp = root / "brief.json"
        if bp.exists():
            try:
                data = json.loads(bp.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                data = None
        print("[series] ── 第 %d 集：过三道输入门" % ep)
        _input_gates(root, data, ep=ep)
        r = pipeline.run(root, stills_only=stills_only, ep=ep)
        st = (r or {}).get("status")
        print("[series] ── 第 %d 集完成：status=%s" % (ep, st))
        results.append({"ep": ep, "status": st,
                        "final": (r or {}).get("final"),
                        "reason": (r or {}).get("reason")})
        if st != "ok":
            print("[series] !! 第 %d 集未出片（status=%s）→ **停止**，不静默跳过后续集"
                  % (ep, st))
            break
    return {"status": "ok" if all(x["status"] == "ok" for x in results) else "incomplete",
            "episodes": eps, "results": results}


async def run(project: str, brief: str | None = None, pack: str = "shortdrama",
              resume_media: bool = False,
              stills_only: bool = False, ep: int = 1) -> dict:
    from v5 import validate
    from v5 import guards
    from v5.guards import load_manifest, save_manifest

    root = config.PROJECTS_DIR / project
    root.mkdir(parents=True, exist_ok=True)

    data = json.loads(Path(brief).read_text(encoding="utf-8")) if brief else None

    # ★ M1（2026-09-16）：绑定「本集」（`episode_index` 的**唯一写入方**）。
    #   改造前它 **5 处读、0 处写**（恒为 1），于是集级产物永远落在第 1 集。
    #   写在**三道门之前**：门与媒体链都要按本集的产物路径校验。
    bind_episode(root, ep)

    if resume_media or stills_only:
        # 全链路开放前需放行；开放后由下方三道门把关（不再一刀切拒绝）。
        if not _resume_allowed():
            raise SystemExit(
                "[RESUME-BLOCK] --resume-media / --stills-only 尚未放行。\n"
                "  二选一：\n"
                "    · 由人显式设 %s=1（单次内部恢复）；或\n"
                "    · 设 SHORTDRAMA_OPEN_CHAIN=1 开放全链路——此时输入由\n"
                "      brief 门 / 分镜契约门 / 资产契约门三道守住。\n"
                "  只读观察随时可用：python -m v5.series <project> --monitor"
                % RESUME_ENV)
        # 分镜已就绪，但质量不可信——开工前先过**三道输入门**，
        # 否则问题会一路带进静帧/视频，返工成本极高。
        _input_gates(root, data, ep=ep)
        from v5.media import pipeline
        # 注：`stills` 审批门与 `media_gate("render")` 都已收进 `pipeline.run`
        # （媒体链唯一入口）——这里不再重复，避免两处分居导致被绕过。
        #
        # ★★ 真机事故（2026-09-17，三集首跑）：这里原来**漏了 `ep=ep`** ⇒
        #    `pipeline.run` 用默认 `ep=1` ⇒ **每一集的媒体链都渲进 `media/ep1/`**，
        #    后一集把前一集的静帧/成片目录**整个覆盖**（实测 ep2 的 run 把 ep1 的
        #    `stills.json` 与 9 张静帧重画掉、并把 `episode_final.mp4` 重拼了一遍）。
        #    而编排层按 `media/ep{N}/` 找成片 ⇒ 找不到 ⇒ 报"没有成片"。
        #    同一条路径上我给 `_input_gates` 加了 `ep=ep`、却**漏了这一个** ——
        #    与 `_storyboard_gate` 漏导入 `guards` 是同一类：**主路径没有测试盯着**。
        return pipeline.run(root, stills_only=stills_only, ep=ep)

    if data:
        # 智能截断：按字段优先级组装，核心字段放不下就报错（不静默丢内容）
        text, dropped = validate.pack_brief(data)
        if dropped:
            print("[brief] 预算 %d 字符，已省略低价值字段：%s"
                  % (validate.DEFAULT_BUDGET, "、".join(dropped)))
        # brief 落到项目根：角色节点开工前自己 read_file /brief.json
        (root / "brief.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        # 注入 brief 后**立刻**校验（此时只能校验 brief 自身）
        _brief_gate(data)

    # 每轮开跑前清空**本集**的 phases/review：**上一次运行的状态不能放行这一次的媒体链**。
    # ★ M2：只清本集（`phases` 已二维）——改造前 `m["phases"] = {}` 会把**别的集**
    #   的名册一起抹掉。这段代码在 `--resume-media` 路径上**不会**执行（那条在前面
    #   已经 return），但留着是给"将来恢复图内创作链"用的，不能带 bug。
    m = load_manifest(root)
    _e = int(m.get("episode_index", 1) or 1)
    _ph = m.setdefault("phases", {})
    if isinstance(_ph.get(str(_e)), dict):
        _ph.pop(str(_e), None)
    else:
        m["phases"] = {}
    m.pop("review", None)
    save_manifest(root, m)

    # 静态链创作链已废弃（2026-09-12）：改由 supervisor 架构承担。
    # 保留媒体链入口（--resume-media / --monitor / 审批 CLI）不受影响。
    raise SystemExit(
        "[deprecated] v5 静态链创作链已废弃——请改用 supervisor 架构：\n"
        "  1) SHORTDRAMA_V5_PROJECT=<项目> langgraph dev --config v5/langgraph.json\n"
        "  2) 在 SDK/Studio 里驱动 supervisor 图（9 图：supervisor + role_* ×7 + media_rerender）\n"
        "  媒体链入口不变：python -m v5.series <项目> --resume-media")

    if data:
        fid = _fidelity_report(root, data)
        if fid:
            print("[fidelity] 产物覆盖不足：" + fid)

    # ─── 媒体链：在图外调用（2026-09-10 变更）────────────────────────────
    #
    # 以前它是图里的一个节点。移出来的两个理由：
    #   ① 它是**确定性流水线**而不是 agent——做成节点会让 `ainvoke` 阻塞
    #      30-60 分钟，"图跑完了吗"这个问题变得含糊（本机常态性回收进程时尤甚）；
    #   ② 它的守卫写在节点里时，`--resume-media` 那条入口**整个绕过**了
    #      `media_gate` 与 `media_loop` 记账（实测：8 角色没跑完也能渲染、
    #      记账永不落盘 → "已渲染无需重渲"闸门形同虚设）。
    #
    # **是否真的渲染由 `pipeline.run` 内的 `media_gate("render")` 判定**，
    # 所以这里无需再判 `passed`：不通过时它自己返回 blocked。
    # 那条门认 `passed or force_passed`，与移动前节点的行为一致。
    media = None
    from v5.media import pipeline
    media = pipeline.run(root, ep=int(load_manifest(root).get("episode_index", 1) or 1))

    return {"status": "graph_done",
            "passed": res.get("passed"),
            "rerun": res.get("rerun"),
            "phases": res.get("phases"),
            "media": media}


def _brief_gate(data: dict) -> None:
    """brief 质量门：缺必填字段 → 阻断；其余问题 → 警告。

    为什么缺字段要阻断：缺了 protagonist / must_have 之类，创作链会自由发挥，
    问题要到渲染完才暴露，白烧一整轮配额。
    """
    from v5 import validate

    r = validate.validate_brief(data)
    if r["missing_fields"]:
        raise SystemExit(
            "[BRIEF-REJECT] brief 缺少必填字段：%s\n补齐后重试（详见 AGENTS.md 字段表）"
            % "、".join(r["missing_fields"]))
    try:
        _epn = int(data.get("episodes") or 1)
    except (TypeError, ValueError):
        _epn = 1
    if _epn > 1:
        # ★ 连带改动（M5，2026-09-17）：**节拍数启发式在多集下失效，必须抑制**。
        #   它的口径是「`len(must_have)` ÷ 目标秒数」（实测秒/节拍 19–33）——
        #   那是"must_have 就是全片四幕"时代的判据。连载下 `must_have` 已改成
        #   「每集共有的硬要求」（2-3 条），条数**不再代表节拍数** ⇒ 继续按它告警
        #   就是**系统性误报**（每条多集 brief 都被 nag，人就会开始忽略所有告警）。
        #   ⇒ 抑制它，并把真正该遵守的要求**说出来**（不静默）。
        #   ⚠️ 抑制必须在**打印之前**完成，否则等于"一边 nag 一边说已抑制"。
        _kept = [p for p in r["problems"] if "节拍" not in str(p)]
        _suppressed = len(r["problems"]) - len(_kept)
        for p in _kept:
            print("[brief] ⚠️ " + p)
        if _suppressed:
            print("[brief] （已抑制 %d 条**按 must_have 条数**推节拍数的告警 —— 连载下"
                  " must_have 是「每集共有要求」、条数不再代表节拍数；节拍数改由 "
                  "plotdesigner 的目录决定，请把「每集几拍」写进 target_duration）"
                  % _suppressed)
        print("[brief] 连载模式（episodes=%d）：`must_have` 必须是「**每集都能覆盖**的"
              "硬要求」（如「每集至少一个笑点」），**不是全剧四幕** —— FIDELITY 门按"
              "**单集分镜**校验覆盖率。全剧起承转合与结局请写进分集目录的卷首。" % _epn)
        _mh = [str(x) for x in (data.get("must_have") or [])]
        _bad = [x for x in _mh
                if any(k in x for k in ("结局", "全剧", "最终", "大结局"))]
        if _bad:
            print("[brief] ⚠️ must_have 里出现**全剧级**措辞（%s）—— 多集下这类要求会让"
                  "**每一集**都被判「未覆盖 must_have」，请移到目录卷首。"
                  % "；".join(x[:24] for x in _bad[:3]))
    else:
        # 单集：原样打印（**行为完全不变** —— 这是 M5 改动最大的风险面）
        for p in r["problems"]:
            print("[brief] ⚠️ " + p)


def _input_gates(root: Path, data: dict | None, ep: int | None = None) -> None:
    """**三道输入门**：brief → 分镜契约 → 资产契约。

    开放全链路（`config.OPEN_CHAIN`）后，这三道门就是**唯一**的输入把关点：
    外部调用方可以直达媒体链，但过不了门就起不来。
    这也是"放开 `--monitor` 锁"的前提——不是无门槛开放，而是把门槛
    从"不许调"换成"输入必须过关"。

    ★ M2：`ep` 显式传下去（不传则各门按 manifest 的 `episode_index` 取）——
    M3 的集循环要**逐集**过门，不能靠隐式的"当前集"。
    """
    if data:
        _brief_gate(data)
    _storyboard_gate(root, data, ep=ep)
    _assets_gate(root, ep=ep)
    _dialogue_gate(root, ep=ep)


def _dialogue_gate(root: Path, ep: int | None = None) -> None:
    """对白**逐字门**（第 4 道输入门）：清单里每句台词必须逐字来自剧本。

    为什么要门、而不是只改契约（2026-09-14 实测事故）：
        `dialogue` 角色的旧契约明写「负责**对白润色与优化**」「**重写问题台词**」，
        产物标题是「对白优化报告」—— 它**契约上就是改写者**。而 `scriptwriter_ep1.md`
        本身也带镜级台词 → **同一句台词两个版本** → 分镜配镜无从取舍，
        下游配音/口型与画面对不上。paper-crane 实测同一晚连续 3 次，
        每次都由 supervisor 亲自比对剧本改回（**靠提示词喊不住**）。
    契约已改成"逐字提取"，这里补的正是另一半：**可机械校验的确定性判据**
    （纯字符串比对，不花一分钱、不会误判）。

    默认**只警告不拦**（存量项目的旧 dialogue 产物多是改写版，直接拦会把它们全卡住）；
    `SHORTDRAMA_DIALOGUE_VERBATIM_STRICT=1` 时阻断。
    """
    from v5 import validate

    r = validate.check_dialogue_verbatim_files(root, ep=ep)
    if r is None:
        return                      # 产物不全（如 media-only 项目）→ 本门不适用
    if r["checked"] == 0:
        print("[dialogue] 清单里没解析出任何台词（全片无对白？）——跳过逐字校验")
        return
    if r["ok"]:
        print("[dialogue] 逐字门通过（%d 句台词均能在剧本里原样找到）" % r["checked"])
        return
    detail = "；".join(("「%s」" % t[:24]) for t in r["offenders"][:4])
    msg = ("清单里 %d/%d 句台词**不在剧本原文里**（逐字率 %.0f%%）：%s —— "
           "台词与剧本不一致会让分镜配镜与配音/口型对不上画面。"
           "修法：`dialogue` 只做**逐字提取**（契约见 packs/<pack>/dialogue/SKILL.md），"
           "要改台词请回到 scriptwriter。"
           % (len(r["offenders"]), r["checked"], r["ratio"] * 100, detail))
    if config.DIALOGUE_VERBATIM_STRICT:
        raise SystemExit("[DIALOGUE-REJECT] " + msg)
    print("[dialogue] ⚠️ " + msg)


def _assets_gate(root: Path, ep: int | None = None) -> None:
    """资产契约门（第三道）：分镜点名的角色必须在资产注册表里有身份锚点。

    为什么必须拦：角色没有参考图/文本身份锚点时，模型会**自己编服装与长相**，
    同一个角色在 18 个镜头里长成 18 个人——问题要到成片才暴露，返工成本最高。
    只在"注册表为空**且**分镜确有命名角色"时阻断（纯道具/空镜项目不适用）。

    ★ M2：按 `ep` 读**本集**的分镜（不传则按 manifest）。
    """
    from v5 import guards          # ★ 必须局部导入（本模块顶层只 import config）
    from v5.media import assets as assets_mod
    from v5.media import cast as cast_mod
    from v5.media import storyboard as sb_mod

    sb = guards.resolve_path(root, "scenedesigner", ep)   # M1/M2：集级路径，读走兼容解析
    if not sb.exists():
        print("[assets] 无分镜，跳过资产契约门")
        return
    try:
        shots = sb_mod.parse(sb.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        shots = []
    if not shots:
        print("[assets] 分镜解析为 0 镜，跳过资产契约门")
        return

    wb = root / "worldbuilder" / "worldbuilder.md"
    chars: list[str] = []
    if wb.exists():
        try:
            chars = [c.get("name") for c in
                     cast_mod.parse_characters(wb.read_text(encoding="utf-8"))]
        except Exception:  # noqa: BLE001
            chars = []
    text = " ".join(str(s.get("visual") or "") for s in shots)
    named = [c for c in chars if c and c in text]
    if not named:
        print("[assets] 本片无命名角色出场，资产契约门通过")
        return

    reg = assets_mod.load_registry(root)
    # ★ 判"有没有资产"必须看**列表**，不能看整个 dict
    #   （`load_registry` 永远返回 `{"assets": [...]}`，`if not reg` **恒为假**
    #    → 旧的"硬拦"从未生效过。2026-09-12 实测）。
    entries = reg.get("assets") or []
    reg_names = [str(a.get("name") or "") for a in entries]
    # 本门跑在 `cast` **之前** —— 此时注册表必然还是空的，所以**不硬拦**（否则每个
    # 新项目第一次跑都会被卡死），只明确提示。真正的完整性校验在 **cast 之后**
    # （`pipeline.run` 里的 `assets.validate_assets`，按磁盘事实查"有没有图"）。
    if not entries:
        print("[assets] 注册表为空（cast 尚未运行）→ 本门跳过；"
              "资产完整性将在 cast 之后按磁盘事实校验")
        return

    # ★ 按**资产名**匹配，不能遍历 dict 的顶层键
    #   （`for k in reg` 只会拿到 `"assets"` 这一个键 → `missing` 恒为全部命名角色
    #    → 那句警告 100% 误报。2026-09-12 实测：就是这个假警告把排查带偏过）。
    missing = [c for c in named if not any(c == n or c in n for n in reg_names)]
    if missing:
        print("[assets] ⚠️ 角色未在注册表命中：%s（将退化为文本身份锚点）"
              % "、".join(missing[:4]))
    else:
        print("[assets] 资产契约门通过（%d 个命名角色均有注册资产）" % len(named))


def _require_approval(root: Path, gate: str) -> None:
    """审批门（默认关）：未批准就阻断该阶段。

    只在 `config.REQUIRE_APPROVAL` 打开时生效——默认关保证现有流程不受影响。
    """
    if not config.REQUIRE_APPROVAL:
        return
    from v5.media import approvals

    ok, why = approvals.check(root, gate)
    if not ok:
        raise SystemExit(
            "[APPROVAL-BLOCK] 「%s」门未批准：%s\n"
            "  这是**产物版本级**的审批：记下的是批准时产物的指纹，"
            "上游产物一变审批就自动作废，必须重新验收。\n"
            "  批准示例：\n"
            "    python -c \"import sys; sys.path.insert(0,'.');"
            " from pathlib import Path; from v5.media import approvals;"
            " approvals.approve(Path(r'%s'), '%s', by='你的名字', note='依据')\"\n"
            "  关闭审批门：SHORTDRAMA_REQUIRE_APPROVAL=0（默认即关）"
            % (gate, why, root, gate))
    print("[approvals] %s 门通过：%s" % (gate, why))


def storyboard_gate(root: Path, brief: dict | None = None,
                    ep: int | None = None) -> None:
    """分镜契约门的**公开入口**（判据 = `_storyboard_gate`，**只此一份**）。

    为什么要有这个薄壳（2026-09-19）：`media/runner` 的前端路径也要顺手判一次
    （否则同一个分镜，命令行硬拦、前端不查 —— 两个入口两套准入）。
    跨模块调 `_私有名` 会让"这是个内部实现"的读法失效，故给个公开名字；
    它**不做任何额外处置**，纯粹转发，避免第二份判据。
    """
    return _storyboard_gate(root, brief, ep=ep)


def _storyboard_gate(root: Path, brief: dict, ep: int | None = None) -> None:
    """分镜契约门（--resume-media 路径）：确定性硬伤 → 阻断，建议项 → 警告。

    阻断项（无误判风险）：缺列 / 镜序错乱 / 空对白 / 要求画内文字。
    阻断项（FIDELITY 门核心）：must_have 覆盖不足。
    阻断项（片长契约，2026-09-13 补）：分镜总时长与 brief 的 target_duration 不符。
    警告项（节奏与风格建议，不该阻断生产）：uniform_pacing / style_drift。

    ★ M2：按 `ep` 读**本集**的分镜（不传则按 manifest 的 `episode_index`）。
    """
    from v5 import guards          # ★ 必须局部导入（本模块顶层只 import config）
    from v5 import validate

    # ★ 2026-09-19：把 `ep=None` **显式归一**成 manifest 的当前集。
    #   为什么必须在这里归一（而不是继续靠 `resolve_path` 内部回落）：
    #   本门现在还负责**落盘判决**（`media/ep{N}/gates.json`），落错集 =
    #   第 2 集的红字扣在第 1 集头上（本项目最忌的"串集"，见 M1/M2）。
    #   归一后的取值与 `resolve_path` 的回落**同一口径**，故读分镜的行为不变。
    if ep is None:
        ep = int(guards.load_manifest(root).get("episode_index", 1) or 1)

    sb = guards.resolve_path(root, "scenedesigner", ep)   # M1/M2：集级路径，读走兼容解析
    if not sb.exists():
        return
    # ★ brief 兜底从磁盘读（2026-09-13 修的真实缺口）：
    #   `--resume-media` 通常**不带** `--brief`（创作链早就把 brief.json 写进项目了），
    #   于是 `brief=None` 一路传进来 —— 而 **must_have 覆盖率**与**片长契约**
    #   两条判据都依赖 brief 里的字段。不兜底的话这两条门是**静默失效**的
    #   （看起来门在、其实什么都没查）。brief.json 就在项目根，读它即可。
    if brief is None:
        bp = root / "brief.json"
        if bp.exists():
            try:
                import json as _json
                brief = _json.loads(bp.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                print("[storyboard] ⚠️ brief.json 解析失败 → 覆盖率与片长两条判据跳过")
    r = validate.check_storyboard(sb.read_text(encoding="utf-8"), brief)

    fatal = []
    if r["missing_cols"]:
        fatal.append("分镜缺列：%s" % "、".join(r["missing_cols"]))
    if not r["order_ok"]:
        fatal.append("镜序错乱（镜头号未升序）")
    if r["empty_dialog"]:
        fatal.append("%d 镜对白为空（会让模型把画面描述念成旁白）" % r["empty_dialog"])
    if r["text_dependency"]:
        fatal.append("要求渲染画内文字的镜：%s（该信息应走对白）"
                     % "、".join(r["text_dependency"][:6]))
    if r["coverage_missing"]:
        # ★ 这里**不再二次截断**（2026-09-14）：`coverage_missing` 的字符串已经
        #   `str(item)[:40]` 截过一遍，并在尾部带「（覆盖 0.xx／阈值 0.26）」。
        #   原实现又 `x[:30]` 切一刀 —— 结果是消息只剩一条**半截句子**、看不到覆盖率
        #   （现场：`未覆盖 must_have：老周自己先开车出村口就陷进泥坑，后轮空转甩泥；他冲后面招手，`），
        #   既不知道差多少、也不知道差在哪。判据的可诊断性不能靠运气。
        fatal.append("未覆盖 must_have：%s"
                     % "；".join(r["coverage_missing"][:4]))
    # 片长契约（2026-09-13 补）：分镜总时长必须对得上 brief 的 target_duration。
    # 依据在 `validate._duration_gap` 的注释里 —— 一句话：算得出来却没人比的判据，
    # 等于没有；不接这根线，片长不对要等整条媒体链跑完（小时级）才暴露。
    if r.get("duration_off"):
        fatal.append(r["duration_off"])
    # ★ **解析不出镜头 = 阻断**（2026-09-14 实测，fail-closed）。
    #   事故：门只认 `LN`/纯数字、不认 `S` 前缀 → 牛来分镜解析出 0 镜 → 片长 0s →
    #   走 `_duration_gap` 的 `seconds_total <= 0` 分支**静默放行**；而媒体链
    #   解析出 26 镜照常渲染。**判据看不见的东西不能算通过** ——
    #   否则门会"批准"任何它读不懂的输入，这比没有门更危险。
    if r.get("unparsed"):
        fatal.append(r["unparsed"])
    # ★ 列**值**规范（2026-09-14 实测）：镜头号重号 / 时长非数字 → 阻断。
    #   这两条都是**渲染层硬依赖**，且纯确定性、零误判风险（详见 `check_storyboard`）。
    for _v in (r.get("row_violations") or []):
        fatal.append(_v)
    # ★ 台词**长度**契约（2026-09-15 实测，用户反馈"对白太短、不能表达剧情"）：
    #   `DIALOGUE_MIN_RATIO` 只查"有多少镜有台词"，**不查每句多长** →
    #   「沉。」「开。」这类残句天然合法（实测 village-bridge 22 句平均 7.0 字，占比却达标）。
    #   **按占比拦截，不按单句**：实测全部既有项目的残句占比都在 50% 以上，单句一刀切会
    #   把每次运行都拦死（详见 `validate.DIALOGUE_SHORT_FATAL_RATIO` 的标定说明）。
    #   上限（念不完）与秒数挂钩，理论上不会误伤长台词（实测全项目 0 例）。
    _sp = r.get("spoken_shots") or 0
    _sh = r.get("short_lines") or []
    _sh_ratio = (len(_sh) / _sp) if _sp else 0.0
    _lg = r.get("long_lines") or []
    _lg_ratio = (len(_lg) / _sp) if _sp else 0.0
    if _sh and _sh_ratio > validate.DIALOGUE_SHORT_FATAL_RATIO:
        fatal.append(
            "台词过短：**%d/%d 句**（%.0f%%，容忍度 %.0f%%）低于 %d 字 —— %s ｜ "
            "台词必须能独立承载剧情，禁止「沉。」「开。」这类残句；"
            "要改就回 **scriptwriter** 改剧本（dialogue 是逐字搬运器，不会替你写长）"
            % (len(_sh), _sp, _sh_ratio * 100,
               validate.DIALOGUE_SHORT_FATAL_RATIO * 100,
               validate.DIALOGUE_MIN_CHARS, "、".join(_sh[:8])))
    # 过长：**与"过短"同一套逻辑 —— 按占比**（2026-09-15 改）。
    #   原先单句一票否决，实测代价很重：village-honey 因 **2 句超长**拦下整条链，
    #   创作链白跑 50 分钟。契约里同类的两个判据不该一个按占比、一个按单句。
    if _lg and _lg_ratio > validate.DIALOGUE_LONG_FATAL_RATIO:
        fatal.append(
            "台词过长：**%d/%d 句**（%.0f%%，容忍度 %.0f%%）超过上限 min(%d 字, 本镜秒数×%.0f) "
            "—— %s ｜ 中文口播约 5–6 字/秒，超了念不完、会挤掉画面动作；"
            "要改就回 **scriptwriter**（或把该镜时长调长）"
            % (len(_lg), _sp, _lg_ratio * 100,
               validate.DIALOGUE_LONG_FATAL_RATIO * 100,
               validate.DIALOGUE_MAX_CHARS, validate.DIALOGUE_CHARS_PER_SEC,
               "、".join(_lg[:6])))
    # 音频模式契约（确定性，与 reviewer 节点同一判据）
    from v5.roles import _audio_mode_defect
    defect = _audio_mode_defect(root)
    if defect:
        fatal.append(defect)

    if fatal:
        _msg = "[STORYBOARD-REJECT] " + " ｜ ".join(fatal)
        # ★ 2026-09-19 人工模式（`config.HUMAN_IN_CHARGE`）：**判断权在人** ⇒ 只报不拦。
        #   边界与理由见 `config.HUMAN_IN_CHARGE`：门是"判据"，拦不拦是"处置"；
        #   人工模式下处置 = 出报告（落 `media/ep{N}/gates.json`，前端可读）。
        #   全自动路径（外部 agent / CLI 默认）**照旧硬拦**，行为逐字节不变。
        if not config.HUMAN_IN_CHARGE:
            guards.record_gate_report(root, ep, fatal, [], blocked=True,
                                      where="storyboard_gate")
            raise SystemExit(_msg)
        print("[storyboard] ⚠️ " + _msg
              + "\n  → 人工模式：**不阻断**。门与 reviewer 的判断都只是报告，"
                "要不要出片由你决定（判决已落到 media/ep%d/gates.json）。"
              % int(ep or 1))

    tips = []
    if r.get("target_seconds"):
        tips.append("片长 %ds / brief 目标 %ds（%.0f%%，容差 %.0f%%–%.0f%%）"
                    % (round(r["seconds_total"]), round(r["target_seconds"]),
                       (r.get("duration_ratio") or 0) * 100,
                       validate.TARGET_TOL_LOW * 100,
                       validate.TARGET_TOL_HIGH * 100))
    elif r.get("seconds_total"):
        tips.append("⚠️ brief 的 target_duration 解析不出目标秒数 → 本门**无法校验片长**，"
                    "建议写成「约 330 秒」这种形式")
    if r.get("uniform_pacing"):
        tips.append("全表等长，节奏会呆板（建议按剧情节拍 4-12s 分配）")
    if r.get("deduped_rows"):
        # 「总表 / 时长校验表」重复列出同样的镜号是**正常写法** → 只提示不阻断；
        # 但**必须说出来**：静默去重会让"少渲了镜"这类问题失去线索。
        tips.append("分镜里有 %d 行重复镜头号（含「分镜总表 / 时长校验」等汇总表）"
                    "→ 已按**首次出现**去重，实际镜数以主表为准" % r["deduped_rows"])
    if r["style_drift"]:
        tips.append("画面描述缺风格锚点：%s" % "、".join(r["style_drift"][:6]))
    if r.get("spoken_shots"):
        tips.append("台词镜 %d/%d（%s 模式）"
                    % (r["spoken_shots"], r.get("n_shots", 0), r.get("audio_mode")))
    # 台词长度：未达阻断线但已明显偏短 → 告警（可见但不拦，见 DIALOGUE_SHORT_*_RATIO）
    if _sh and validate.DIALOGUE_SHORT_WARN_RATIO < _sh_ratio \
            <= validate.DIALOGUE_SHORT_FATAL_RATIO:
        tips.append("台词偏短：%d/%d 句（%.0f%%）低于 %d 字（阻断线 %.0f%%）——"
                    "建议回 scriptwriter 把残句写成完整句：%s"
                    % (len(_sh), _sp, _sh_ratio * 100, validate.DIALOGUE_MIN_CHARS,
                       validate.DIALOGUE_SHORT_FATAL_RATIO * 100, "、".join(_sh[:5])))
    # 台词过长但未达阻断线 → 同样只告警（单句超长不该杀掉整条链）
    if _lg and _lg_ratio <= validate.DIALOGUE_LONG_FATAL_RATIO:
        tips.append("有 %d 句台词可能念不完（未达阻断线 %.0f%%）：%s —— "
                    "中文口播约 5–6 字/秒，建议回 scriptwriter 缩短或把该镜时长调长"
                    % (len(_lg), validate.DIALOGUE_LONG_FATAL_RATIO * 100,
                       "、".join(_lg[:5])))
    if r.get("dialogue_thin"):
        tips.append("台词镜占比偏低（%.0f%%），dialogue-led 下建议多给台词"
                    % (r["dialogue_ratio"] * 100))
    for t in tips:
        print("[storyboard] ⚠️ " + t)
    # ★ 2026-09-19：**每次过门都覆盖写一次判决**（含"干净通过"，fatal 为空）。
    #   覆盖写是有意的：前端读到的永远是最新一次判决 ⇒ 不存在"上一轮的红字
    #   留在页面上"。见 `guards.GATE_REPORT_NAME` 的说明。
    guards.record_gate_report(root, ep, fatal, tips, blocked=False,
                              where="storyboard_gate")


# 叙事覆盖率只适用于**承担叙事**的角色产物。其余角色不该用这把尺子：
#   dialogue —— 只写台词清单（几百字），本来就不复述剧情；
#   assetdesigner —— 只写资产卡（名称/类型/用途）；
#   reviewer —— 是评审报告，引用剧情只为打分。
# 实测（nightshift-45）：dialogue 0.09~0.12、reviewer 0.06~0.20 被判"未覆盖"，
# 但它们的产物本身是合格的——**判据错配，不是角色偷工**。
_FIDELITY_ROLES = ("director", "worldbuilder", "plotdesigner", "scriptwriter",
                   "scenedesigner")


def _fidelity_report(root: Path, brief: dict) -> str:
    """汇总**叙事角色**产物对 brief must_have 的覆盖情况。

    只查叙事角色（见 _FIDELITY_ROLES 的说明）；其余角色的产物形态不同，
    套同一把覆盖率尺子只会产生稳定的误报，把真正的覆盖问题淹没掉。
    """
    from v5 import validate
    from v5.guards import OUTPUTS

    lines = []
    for role in _FIDELITY_ROLES:
        rel = OUTPUTS.get(role) or ""
        p = root / rel.replace("{N}", "1")
        if not p.exists():
            continue
        try:
            r = validate.check_brief_fidelity(p.read_text(encoding="utf-8"), brief)
        except Exception:  # noqa: BLE001 -- 校验失败不得阻断主流程
            continue
        if not r["ok"]:
            lines.append("%s 未覆盖：%s" % (role, "；".join(r["coverage_missing"])))
    return " | ".join(lines)


async def monitor(project: str, pack: str = "shortdrama",
                  max_events: int = 1, max_seconds: float = 0.0) -> dict:
    """**只读旁监控**：只打印状态快照与产物一致性，绝不写盘、绝不干预。

    这是调试期外部 Agent 唯一被允许的动作。设计约束：
    - **不 ainvoke、不 astream**（那会真的推进图）——只读 manifest 与产物 mtime；
    - 不写任何文件、不改 manifest、不删 clip、不代跑角色；
    - 发现异常（角色失败 / 产物缺失 / 评审不通过）只**打印并停止**，由人决定。

    默认只做**一次快照**（不阻塞）；需要持续盯，传 `--seconds` 或 `--events`。
    """
    import time

    from v5 import guards as _guards
    from v5.guards import ROLES, load_manifest

    root = config.PROJECTS_DIR / project
    if not root.exists():
        raise SystemExit("[MONITOR] 项目不存在：%s（不要代替系统创建）" % root)

    t0 = time.time()
    n = 0
    problems: list[str] = []
    m: dict = {}
    print("[MONITOR] 只读观察（不写盘、不推进图）project=%s pack=%s" % (project, pack))

    while True:
        if max_events and n >= max_events:
            break
        if max_seconds and (time.time() - t0) >= max_seconds:
            print("[MONITOR] 达到时长上限 %.0fs，退出" % max_seconds)
            break
        n += 1
        m = load_manifest(root)
        _ep = int(m.get("episode_index", 1) or 1)
        # ★ M2：按**本集**读名册（`phases` 已是二维）。直读 `m["phases"]` 在多集下
        #   会把"有没有 complete"读成"表里有没有这个键"—— 一维兼容分支只对第 1 集成立。
        done = [r for r in ROLES if _guards.phase_of(m, r, _ep) == "complete"]
        failed = [r for r in ROLES if _guards.phase_of(m, r, _ep) == "failed"]
        rev = m.get("review") or {}
        print("[MONITOR #%d] ep%d phases %d/%d complete%s | review=%s"
              % (n, _ep, len(done), len(ROLES),
                 (" failed=" + ",".join(failed)) if failed else "",
                 ("pass=" + str(rev.get("passed"))) if rev else "未评审"))
        for role in ROLES:
            # ★ 用 `guards.resolve_path`（新名优先、旧名回退）：历史项目的产物是旧名，
            #   直接拼新名会让 monitor 把正常的旧项目报成"记为 complete 但产物缺失"。
            if _guards.phase_of(m, role, _ep) == "complete" and not \
                    _guards.resolve_path(root, role, _ep).exists():
                problems.append("%s 记为 complete 但产物缺失" % role)
        if failed:
            problems.append("角色失败：" + "、".join(failed))
        if rev and not (rev.get("passed") or rev.get("force_passed")):
            problems.append("评审未通过：%s" % "；".join(rev.get("reasons") or [])[:200])
        if problems:
            print("[MONITOR][STOP] 发现问题，停止观察（不干预，交给人工）：")
            for x in dict.fromkeys(problems):
                print("  - " + x)
            break
        if max_events and n >= max_events:
            break
        await asyncio.sleep(5)

    if not problems:
        print("[MONITOR] 未发现异常（%d 次快照）" % n)
    return {"status": "monitor_done", "project": project,
            "phases": m.get("phases"), "review": m.get("review"),
            # ★ M2：本集的扁平名册（`phases` 是二维，排障时看这个更直接）
            "phases_ep": {r: _guards.phase_of(m, r, int(m.get("episode_index", 1) or 1))
                          for r in ROLES},
            "problems": list(dict.fromkeys(problems)), "events": n}


def main() -> None:
    ap = argparse.ArgumentParser(description="v5 series (single-graph pipeline)")
    ap.add_argument("project")
    ap.add_argument("--brief", default=None)
    ap.add_argument("--pack", default="shortdrama")
    ap.add_argument("--monitor", "--watch", action="store_true", dest="monitor",
                    help="只读旁监控：不写盘、不推进图（可用，但不再是唯一允许的模式）")
    ap.add_argument("--events", type=int, default=1,
                    help="配合 --monitor：最多快照次数（默认 1 次，不阻塞）")
    ap.add_argument("--seconds", type=float, default=0.0, help="配合 --monitor：最长观察秒数")
    ap.add_argument("--resume-media", action="store_true",
                    help="断点续跑媒体链（需 SHORTDRAMA_OPEN_CHAIN=1 或 "
                         "SHORTDRAMA_ALLOW_RESUME=1）")
    ap.add_argument("--stills-only", action="store_true",
                    help="配合 --resume-media：只跑静帧并停下（同样需放行）")
    ap.add_argument("--ep", type=int, default=1, metavar="N",
                    help="第几集（默认 1）。★ M1 起是 `episode_index` 的唯一写入方："
                         "集级产物落在 `*_ep{N}.md` / `media/ep{N}/`；"
                         "**换集会自动清空上一集的 phases/review**（M1 阶段 phases 仍是一维）")
    ap.add_argument("--bind-only", action="store_true", dest="bind_only",
                    help="只绑定 `--ep N`（写 episode_index）然后退出，不跑任何链路。"
                         "★ 给 `run_new_project.py` 用：集号必须在**起 dev 之前**落盘，"
                         "否则角色按第 N 集写盘、记账却按第 1 集校验 → 全判 failed")
    ap.add_argument("--episodes", default=None, metavar="SPEC",
                    help="★ M3：**按集跑媒体链**（一次进程、串行、逐集过门）："
                         "`1-4` / `3` / `1,3,5`。需配合 `--resume-media`。"
                         "任何一集被门拦下即**停止**（不静默跳过）。"
                         "注意：这是**媒体侧**的集循环；含创作链的全链路循环见 "
                         "`scripts/run_new_project.py --episodes`")
    # ─── 单镜重渲（对话式迭代；spec §3）──────────────────────────────────────
    # **不走 `--resume-media` 那条放行**：它不是"第二个入口"，而是媒体链唯一
    # 入口（`pipeline.run`）的**受限调用** —— `media_gate("render")` 与记账
    # 照常执行，只是把作用域收窄到指定镜。反过来说，`--resume-media` 那条
    # 放行是为了防止"绕过三道输入门直接渲染"，而重渲**不引入新输入**
    # （分镜是冻结的），故无需它。
    ap.add_argument("--rerender", metavar="SHOT", default=None,
                    help="单镜重渲：镜号（逗号/空格分隔可传多个），如 --rerender LN03")
    ap.add_argument("--from", dest="from_stage", default="video",
                    choices=["video", "still"],
                    help="配合 --rerender：still = 连静帧一起重做（静帧层的问题"
                         "在视频层修不掉）")
    # ─── 人工审批门（人在环的显式机制）────────────────────────────────────
    ap.add_argument("--approve", metavar="GATE", default=None,
                    choices=["storyboard", "stills", "media"],
                    help="批准某道门（storyboard/stills/media），记录批准人+时间+产物指纹")
    ap.add_argument("--revoke", metavar="GATE", default=None,
                    choices=["storyboard", "stills", "media"], help="撤销某道门的批准")
    ap.add_argument("--by", default="", help="配合 --approve/--revoke：批准人")
    ap.add_argument("--note", default="", help="配合 --approve/--revoke：依据/备注")
    ap.add_argument("--approvals", action="store_true", help="显示各审批门状态")
    # ─── 创作链的**步级 HITL**（每次派发子代理前挂起，等人批复）──────────────
    # 与上面三道审批门的区别：那是"阶段之间"的门（查产物合格才放行）；
    # 这是"图运行中"的 interrupt，粒度是每一步。信道见 v5/hitl.py。
    # 需 SHORTDRAMA_APPROVE_EACH_ROLE=1 才会产生中断（默认关，否则全自动链路会挂住）。
    ap.add_argument("--hitl", action="store_true",
                    help="显示步级 HITL 的待批准状态")
    ap.add_argument("--hitl-approve", action="store_true",
                    help="批准当前挂起的这一步（配合 --by/--note）")
    ap.add_argument("--hitl-reject", action="store_true",
                    help="拒绝当前挂起的这一步 → 链路终止（配合 --note 写原因）")
    ap.add_argument("--hitl-redo", action="store_true",
                    help="**打回**当前这一步：重跑「上一步角色 + 它的全部下游」"
                         "（默认目标 = 刚产出的那个角色，可用 --target 指定更上游的）")
    ap.add_argument("--target", default="",
                    help="配合 --hitl-redo：打回哪个角色（只能是**本步之前已完成**的角色）")
    a = ap.parse_args(sys.argv[1:])

    if a.bind_only:
        if a.episodes:
            raise SystemExit("--bind-only 与 --episodes 是两种意图，不能同时用："
                             "--bind-only 只写集号后退出；--episodes 是按集跑媒体链。")
        root = config.PROJECTS_DIR / a.project
        root.mkdir(parents=True, exist_ok=True)
        m = bind_episode(root, a.ep)
        print("[series] 已绑定第 %d 集（episode_index=%d）"
              % (a.ep, int(m.get("episode_index", 1) or 1)))
        return

    if a.episodes:
        # ★ M3：媒体侧的集循环。**放行纪律不变** —— 仍然是 `pipeline.run` 唯一入口，
        #   仍然要人显式放行（否则就成了"绕过三道门直接批量渲染"的第二个入口）。
        if not (a.resume_media or a.stills_only):
            raise SystemExit(
                "--episodes 必须配合 --resume-media（或 --stills-only）。\n"
                "  理由：媒体链只有 `pipeline.run` 一个入口，而 `--resume-media` 是"
                "它唯一的放行开关；不带上就等于开了第二个入口。")
        if not _resume_allowed():
            raise SystemExit(
                "--episodes 需要放行：设 %s=1 或 SHORTDRAMA_OPEN_CHAIN=1"
                % RESUME_ENV)
        out = asyncio.run(run_media_episodes(a.project, a.episodes,
                                             stills_only=a.stills_only))
        print("RESULT:", json.dumps(out, ensure_ascii=False))
        return

    if a.monitor:
        out = asyncio.run(monitor(a.project, a.pack, a.events, a.seconds))
        print("RESULT:", json.dumps(out, ensure_ascii=False))
        return

    if a.approve or a.revoke or a.approvals:
        from v5.media import approvals

        root = config.PROJECTS_DIR / a.project
        if a.approve:
            rec = approvals.approve(root, a.approve, by=a.by, note=a.note)
            print("[approvals] 已批准 %s：by=%s fingerprint=%s"
                  % (a.approve, rec["by"], rec["fingerprint"]))
        if a.revoke:
            approvals.revoke(root, a.revoke, by=a.by, note=a.note)
            print("[approvals] 已撤销 %s" % a.revoke)
        print("[approvals] 当前状态：%s" % approvals.summary(root))
        print("  审批门生效需设 SHORTDRAMA_REQUIRE_APPROVAL=1（默认关）")
        return

    if a.hitl or a.hitl_approve or a.hitl_reject or a.hitl_redo:
        from v5 import hitl

        root = config.PROJECTS_DIR / a.project
        if a.hitl_approve or a.hitl_reject or a.hitl_redo:
            dec = ("redo" if a.hitl_redo else
                   "reject" if a.hitl_reject else "approve")
            # ★ `decide` 现在会**拒绝**三种情况（判据只在 `v5/hitl.py` 一份）：
            #   没有挂起时写决定（否则会被**下一次**挂起立刻消费 = 静默跳步）、
            #   非法决定类型、`redo` 目标不是"已完成角色"。
            #   这里是**可预期的输入问题** ⇒ 打一行中文原因，不要甩 traceback。
            try:
                p = hitl.decide(root, dec, by=a.by, note=a.note, target=a.target)
            except ValueError as e:
                print("[hitl] ✗ 未写入决定：%s" % e)
                print("[hitl] 当前状态：%s" % hitl.status(root))
                return
            print("[hitl] 已写入决定 %s%s → %s"
                  % (dec, ("（目标 %s）" % a.target) if a.target else "", p))
            print("[hitl] 链路轮询到就会 %s"
                  % {"approve": "继续（resume）",
                     "redo": "重跑该角色及其下游（旧产物会移入 .rerun_backup/）",
                     "reject": "终止（不再 resume）"}[dec])
        print("[hitl] %s" % hitl.status(root))
        if not config.APPROVE_EACH_ROLE:
            # ⚠️ 注意：`config.APPROVE_EACH_ROLE` 读的是**本进程**的环境。
            #   前端那条路会由**dev server 进程**带上这个开关（`webchain` 里设），
            #   本进程看不到 —— 所以这句只是提示，不代表 dev server 没开。
            print("[hitl] 提示：**本进程**未见 SHORTDRAMA_APPROVE_EACH_ROLE=1。"
                  "（前端起的 dev server 会自己带上它，故本提示不代表链路没开）")
        return

    if a.rerender:
        # 单镜重渲：**同一入口的受限调用**（gate / 记账 / 幂等 / 限流全在
        # `pipeline.run` 里），落点与 Studio 里 director 派发 `media_rerender`
        # 子代理完全一致（spec §3.2）。
        from v5.media import pipeline

        root = config.PROJECTS_DIR / a.project
        names = [x for x in a.rerender.replace(",", " ").split() if x]
        out = pipeline.rerender(root, names, note=a.note,
                                from_still=(a.from_stage == "still"))
        print("RESULT:", json.dumps(out, ensure_ascii=False))
        return

    out = asyncio.run(run(a.project, a.brief, a.pack, a.resume_media,
                          a.stills_only, a.ep))
    print("RESULT:", json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
