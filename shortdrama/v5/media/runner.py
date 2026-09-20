# -*- coding: utf-8 -*-
"""Web 侧的任务运行器：**spawn 子进程跑长任务 + runs 台账**。

## 为什么必须 spawn 子进程（而不是在请求里同步跑）

媒体链一次 30–60 分钟（生图 + 生视频 + QC），HTTP 请求扛不住；
而且本机沙箱**会在命令结束时回收整个进程树**，所以常驻/长任务必须是独立进程。

## 台账放哪

`<runtime>/.tmp/web-runs/<run_id>.json` —— **刻意不放项目目录内**：
`projects/<pid>/` 的内容是有契约的（角色产物 + media/），
塞运行记录进去会污染"按目录结构判产物"的既有判据。

## 父子分工（关键）

- **父（server）**：校验参数 → 写台账（status=queued）→ spawn → 立即返回 run_id。
- **子（`python -m v5.media.runner --run-id X`）**：读台账 → 执行 → 把结果写回台账。

台账是**唯一真相源**，子进程是**无状态执行器** —— 这样取消/超时/崩溃后
状态仍然可读（子进程意外死掉时 `status` 停在 running，由 `reconcile()` 判死）。

## ⛔ 硬护栏：`only` 必须非空

`pipeline.run()` 不带 `only` 就是**整片渲染**（小时级 + 大量配额）。
从 UI 来的动作永远是"这一镜"或"这几镜"，
所以这里**拒绝空 `shots`**（`ValueError`），避免误触整片渲染。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from .. import config
from .. import vendors

RUNS_SUBDIR = ".tmp/web-runs"

#: 允许的 kind → 说明（也是签名校验的白名单）
KINDS = {
    "keyframe": "重画指定镜的静帧（不烧视频配额）",
    "video": "为指定镜生成/重生成视频（烧视频配额；受媒体门约束）",
    "assets": "为角色/道具/场景生成参考图（烧生图配额；可续跑）",
    #: P2b：跑创作链（supervisor 7 角色）。**不吃 shots**；需 dev server 服务于本片（D6）。
    "chain": "跑创作链（worldbuilder→assetdesigner∥plotdesigner→scriptwriter→dialogue"
             "→scenedesigner→reviewer），产出分镜",
    #: D（2026-09-19）：**整片出片** —— 静帧（缺的补画）→ 视频（缺的补渲）→ 拼接成片。
    #: ★ 它是**唯一**允许不带 `shots` 的任务类型（见本模块开头的硬护栏说明）：
    #:   护栏拒绝的是"**参数漏传**导致的整片渲染"，而"人显式要求整片出片"是另一回事
    #:   —— 两者必须能用**类型**区分开，不能靠"shots 是不是空"猜。
    "episode": "整片出片（静帧 → 视频 → 拼接成 mp4；烧大量配额，必须由人显式触发）",
    #: ★ 2026-09-19：**只跑到剧本正文**（创作链前 4 个角色：worldbuilder →
    #: assetdesigner∥plotdesigner → scriptwriter）。前端「确认简介 → 生成剧本内容」
    #: 那一步用它；产出正文后由人确认，再决定要不要往下跑资产/分镜。
    "script": "生成剧本正文（创作链跑到 scriptwriter 为止；不产出分镜）",
}

#: 视为"已结束"的状态
TERMINAL = ("ok", "failed", "incomplete", "blocked", "cancelled", "lost")


# ─────────────────────────────────────────────────────────── 路径与台账

def runs_dir() -> Path:
    d = config.RUNTIME_ROOT / RUNS_SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def ledger_path(run_id: str) -> Path:
    return runs_dir() / ("%s.json" % run_id)


def log_path(run_id: str) -> Path:
    return runs_dir() / ("%s.log" % run_id)


def _write_ledger(rec: dict) -> None:
    p = ledger_path(rec["run_id"])
    p.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")


def read_ledger(run_id: str) -> dict | None:
    p = ledger_path(run_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:               # noqa: BLE001
        return None


def new_run_id(pid: str, kind: str) -> str:
    return "%s-%s-%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), pid, kind,
                            uuid.uuid4().hex[:6])


# ─────────────────────────────────────────────────────────── 启动

def _qc_flag(v) -> str:
    """请求里的质检开关 → `"1"` / `"0"`。

    ⛔ **不能直接 `bool(v)`**：字符串 `"0"` 在 Python 里是**真值** ⇒ 把"关"读成"开"。
       （同型事故在本项目踩过：`os.environ.get(...) != "0"` 这种写法之所以到处都是，
       就是因为 `"0"` 与 `0` 的语义必须显式对齐。）
    `None` / 空串 = **人工模式默认：关**（见 `start` 的文档说明）。
    """
    if v is None or v == "":
        return "0"
    if isinstance(v, str) and v.strip().lower() in ("0", "false", "no", "off", "none"):
        return "0"
    return "1" if v else "0"


def _validate(pid: str, kind: str, shots, ep: int = 1) -> list:
    """参数校验。**两端都防**：kind 白名单 + only 非空。

    ## ★★ `ep` 必须传进来（2026-09-19 实测抓到的跨集 bug）

    旧签名没有 `ep`，于是下面 `resolve_path(root, "scenedesigner")` 的 **`ep=None`
    会回落到 `manifest.episode_index`**（= **最近一次跑的那一集**）。
    实测事故：跑完第 2 集的创作链后（`episode_index=2`），回到**第 1 集**的分镜页
    点「批量生成图片」→ 19 个镜号被拿**第 2 集的分镜（15 镜）**去校验 ⇒

        E400「镜号不在分镜里：LN16、LN17、LN18、LN19（本片共 15 镜）」

    ⇒ 前端报的是一句**看起来像"分镜写错了"的错**，而真实原因是**串集**。
    ⇒ 而且此时**第 1 集的媒体操作全部被拒**（生成图片 / 生成视频都过不去），
      与 payload 无关 —— 只要 manifest 停在别的集就必现。
    """
    if kind not in KINDS:
        raise ValueError("未知的任务类型 %r（可选：%s）" % (kind, "、".join(KINDS)))
    root = config.PROJECTS_DIR / pid
    if not (root / "brief.json").exists():
        raise ValueError("项目不存在或缺 brief.json：%s" % pid)
    if kind in ("keyframe", "video"):
        shots = [str(s).strip() for s in (shots or []) if str(s).strip()]
        if not shots:
            # ⛔ 这是**最重要的一条护栏**：空 shots 会让 `pipeline.run` 整片渲染
            raise ValueError(
                "keyframe/video 任务必须指定 shots（本次为空）。"
                "**空 shots 等于整片渲染**（小时级 + 大量配额），故拒绝。")
        # 镜号必须真在分镜里 —— 在**烧配额之前**拦下（复用 pipeline 的既有口径）
        from . import storyboard as sb_mod
        from ..guards import resolve_path          # M1：分镜是集级产物，读走兼容解析
        # ★ 必须按**请求的那一集**读分镜（不能回落 manifest 的 episode_index，
        #   那是"最近跑过的集"，会导致跨集校验 —— 见 _validate 的文档）
        md = resolve_path(root, "scenedesigner", int(ep or 1))
        if md.exists():
            try:
                known = {s["name"] for s in
                         sb_mod.parse(md.read_text(encoding="utf-8"))}
            except Exception:       # noqa: BLE001
                known = set()
            unknown = [s for s in shots if known and s not in known]
            if unknown:
                raise ValueError("镜号不在分镜里：%s（本片共 %d 镜）"
                                 % ("、".join(unknown), len(known)))
        return shots
    return []


def start(pid: str, kind: str, ep: int = 1, shots=None, from_still: bool = False,
          image_vendor: str = "", video_vendor: str = "",
          still_qc=None, clip_qc=None, log=print) -> dict:
    """写台账 → spawn 子进程 → 返回台账记录。**不等任务结束**。

    `image_vendor` / `video_vendor`（2026-09-18）：**这一次运行**用哪个厂商。
    空串 ⇒ 用环境变量 / 缺省（agnes）⇒ 行为与改造前**逐字节一致**。

    ★ **生效方式：只写子进程 env，不动全局 `os.environ`** ——
      于是"每个生成页面各选各的厂商"能直接落地（per-run 生效、不影响并发与其它 run）。
      这也解释了为什么不需要把厂商做成"贯穿全链的请求参数"（见 spec §4.6）。
    台账里同时留一份（产地记录：前端显示 + 事后归因）。

    `still_qc` / `clip_qc`（2026-09-19）：**质检自愈**（静帧判硬伤自动重画 /
    成片抽帧复核自动重拍）开关。`None` = **人工模式的默认：关**。
    为什么默认关：那两条环路是"机器替人判断画面合不合格并直接烧配额改"——
    与"判断权在人"最不一致的一处（静帧重画 30–60 秒/次，clipqc 一轮抽帧复核实测
    比重生成还贵）。人要它，就在前端把开关打开，值随请求传下来。
    ⛔ CLI / 外部 agent 不走这里 ⇒ 它们仍是 `config` 的默认（**开**），行为不变。
    """
    shots = _validate(pid, kind, shots, ep)

    # 生效厂商 = 显式传参 > 环境变量 > 缺省 agnes
    _img = str(image_vendor or "").strip() or vendors.current("image")
    _vid = str(video_vendor or "").strip() or vendors.current("video")

    # ★ 厂商校验收在**父进程**：非法值要**立刻**回 400，而不是等子进程跑起来
    #   才在日志里报（"早失败一次，胜过白排一个 run"）。
    #   未注册 ⇒ `UnknownVendor`（`ValueError` 子类）⇒ server 侧既有的
    #   `except ValueError → 400` 自动接住。
    vendors.get(_img)
    vendors.get(_vid)

    # ★ 项目自选画幅（前端「视频比例」选择器写进 brief 的 `ratio`）。
    # 空 = 没选过 ⇒ 不写 env，沿用 `config` 的缺省（行为与改造前一致）。
    from ..guards import load_brief
    _ratio = str(load_brief(config.PROJECTS_DIR / pid).get("ratio") or "").strip()

    rid = new_run_id(pid, kind)
    rec = {
        "run_id": rid,
        "pid": pid,
        "ep": int(ep or 1),
        "kind": kind,
        "shots": shots,
        "from_still": bool(from_still),
        "status": "queued",
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ended_at": None,
        "os_pid": None,
        "returncode": None,
        "result": None,
        "log": log_path(rid).name,
        "note": KINDS[kind],
        # ★ 产地记录（2026-09-18）：台账里记下**这一轮实际用哪家**。
        #   前端 `/runs/{run_id}` 轮询时因此天然能看到产地 —— 用户"自己对比"要用。
        #   注意：**不改流水线行为**，只是记账（只写不读）。
        "vendors": {"image": _img, "video": _vid},
        # 这一轮实际用的画幅（产地记录，同 vendors 只写不读）
        "ratio": _ratio or config.ASPECT_RATIO,
    }
    _write_ledger(rec)

    argv = [sys.executable, "-m", "v5.media.runner", "--run-id", rid]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(config.PROJECT_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    # ★ per-run 生效：**只给这个子进程**设，不动全局 os.environ
    #   （前端每个生成页面各选各的厂商，靠的就是这一层隔离）
    env[vendors.ENV_KEY["image"]] = _img
    env[vendors.ENV_KEY["video"]] = _vid
    # ★ per-run 画幅：为什么走 env 而不是把 `ratio` 当参数一路传下去 ——
    #   `config.STILL_RATIO` / `ASPECT_RATIO` 被 `cast` / `providers` 在**导入期**
    #   读，改成参数要动四五个模块；而媒体链本来就是 runner 起的**独立子进程**，
    #   只写这个子进程的 env 即可 per-run 生效，且不碰全局（CLI / 外部 agent 不变）。
    #   ⚠️ 两个必须一起设：只改静帧会让静帧与成片画幅不一致（拼接时裁边或留黑边）。
    if _ratio:
        env["SHORTDRAMA_STILL_RATIO"] = _ratio
        env["SHORTDRAMA_ASPECT"] = _ratio
    # ★★ 2026-09-19：**前端路径 = 人工模式**（判断权在人）。
    #
    #   `runner.start` 只被 `server.py`（前端）调用 ⇒ 在这里设 env 就等于
    #   "只对前端生效"，CLI / 外部 agent 拿不到 ⇒ 行为逐字节不变。
    #   效果（详见 `config.HUMAN_IN_CHARGE`）：媒体门的「必须 reviewer.passed」
    #   与分镜契约门的硬伤**降级为报告**，不拦；reviewer 照跑。
    #
    #   为什么不写进 `.env`：那会让**外部 agent 的全自动链路**也失去唯一的质量保护
    #   （教训：`.env` 是全进程共享的，开关必须跟着**路径**走，不能跟着**机器**走）。
    env["SHORTDRAMA_HUMAN_IN_CHARGE"] = "1"
    # ★★ 2026-09-19：**质检自愈**（静帧判硬伤自动重画 / 成片抽帧复核自动重拍）。
    #   `None`（前端没传）= 人工模式的默认：**关** —— 判断权在人，别替他改画面。
    #   打开 = 值随请求传下来（`{"still_qc": 1}` / `{"clip_qc": 1}`）。
    #   ⛔ 必须**显式写 env**（不能只靠"不设"）：子进程会继承本进程的 env，
    #      而本进程（shim）可能从 `.env` 里读到过 `=1` —— 不覆盖就等于漏开关。
    env["SHORTDRAMA_STILL_QC"] = _qc_flag(still_qc)
    env["SHORTDRAMA_CLIP_QC"] = _qc_flag(clip_qc)
    with log_path(rid).open("w", encoding="utf-8") as lf:
        lf.write("[runner] %s %s pid=%s ep=%d shots=%s\n"
                 % (time.strftime("%H:%M:%S"), kind, pid, rec["ep"], shots or "—"))
        lf.write("[runner] %s\n" % vendors.describe())
        lf.flush()
        proc = subprocess.Popen(argv, cwd=str(config.PROJECT_ROOT),
                                stdout=lf, stderr=subprocess.STDOUT, env=env)
    rec["os_pid"] = proc.pid
    rec["status"] = "running"
    _write_ledger(rec)
    log("[runner] 已启动 %s（os pid %d）" % (rid, proc.pid))
    return rec


# ─────────────────────────────────────────────────────────── 查询 / 取消

def log_tail(run_id: str, n: int = 40) -> str:
    p = log_path(run_id)
    if not p.exists():
        return ""
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:               # noqa: BLE001
        return ""
    return "\n".join(lines[-n:])


def _alive(pid: int | None) -> bool:
    """进程是否还在。用 `tasklist` 而不是 `os.kill(pid,0)` —— 后者在 Windows
    对已退出但句柄未释放的 pid 会误判。"""
    if not pid:
        return False
    try:
        out = subprocess.run(["tasklist", "/FI", "PID eq %d" % int(pid), "/NH"],
                             capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=15).stdout or ""
    except Exception:               # noqa: BLE001
        return True                 # 查不到就**当作还活着**（宁可保守）
    return str(int(pid)) in out


def reconcile(rec: dict) -> dict:
    """把"台账说 running、进程却没了"的状态判死。

    ★ 为什么需要：子进程可能被沙箱回收 / 被 OOM 杀掉 / 用户关掉终端 ——
    那时台账永远停在 running，前端会一直转圈。**不修这条就是"永远不结束"**。
    """
    if not rec or rec.get("status") in TERMINAL:
        return rec
    if _alive(rec.get("os_pid")):
        return rec
    rec["status"] = "lost"
    rec["ended_at"] = rec.get("ended_at") or time.strftime("%Y-%m-%d %H:%M:%S")
    rec["note"] = "执行进程已消失（被回收/被杀/崩溃）—— 见日志尾部"
    _write_ledger(rec)
    return rec


def status(run_id: str) -> dict | None:
    rec = read_ledger(run_id)
    if rec is None:
        return None
    return reconcile(rec)


def list_runs(pid: str | None = None, limit: int = 30) -> list:
    out = []
    for p in sorted(runs_dir().glob("*.json"), reverse=True):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except Exception:           # noqa: BLE001
            continue
        if pid and rec.get("pid") != pid:
            continue
        rec = reconcile(rec)
        rec.pop("result", None)     # 列表不带大结果
        out.append(rec)
        if len(out) >= limit:
            break
    return out


def cancel(run_id: str, log=print) -> dict | None:
    """取消：杀**整棵进程树**。

    ⚠️ 必须带 `/T`：媒体链会 spawn ffmpeg 等子进程，只杀父会留孤儿（本项目有实测记录）。
    """
    rec = read_ledger(run_id)
    if rec is None:
        return None
    if rec.get("status") in TERMINAL:
        return rec
    pid = rec.get("os_pid")
    if pid:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(int(pid))],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30)
        except Exception as e:      # noqa: BLE001
            log("[runner] 取消失败：%s" % str(e)[:120])
    rec["status"] = "cancelled"
    rec["ended_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    rec["note"] = "被用户取消"
    _write_ledger(rec)
    return rec


# ─────────────────────────────────────────────────────────── 子进程入口

def _exec_keyframe(root: Path, ep: int, shots: list, log) -> dict:
    """重画静帧：`stills_only=True` → **不烧视频配额**，也不受 render 门约束。"""
    from . import pipeline
    return pipeline.run(root, ep=ep, only=shots, from_still=True,
                        stills_only=True, log=log)


def _exec_video(root: Path, ep: int, shots: list, log) -> dict:
    """生成视频：**受 media_gate 约束**（8 角色 complete + 评审通过），这是设计而非障碍。"""
    from . import pipeline
    return pipeline.run(root, ep=ep, only=shots, from_still=False, log=log)


def _exec_assets(root: Path, ep: int, shots: list, log) -> dict:
    """生成资产参考图（可续跑：已在盘上的会跳过）。"""
    from . import cast
    cast.ensure(root, log=log)
    return {"status": "ok", "stage": "assets"}


def _storyboard_warnings(root: Path, ep: int, log) -> list:
    """跑一次**分镜契约门**，返回它的判决（人工模式下只记不拦）。

    为什么前端路径要主动跑它（2026-09-19）：
        分镜契约门原先只挂在 CLI 的 `--resume-media` 路径（`series._input_gates`），
        而**前端路径一个门都没有** ⇒ 同一份分镜，命令行硬拦、前端照渲 ——
        **两个入口两套准入**。本项目最忌"同一判据写两份"，次忌"同一件事两个口径"。
        现在两个入口**同判据、同落盘**（`media/ep{N}/gates.json`），
        差别只剩**处置**：人工模式只出报告，全自动模式照旧阻断。

    判据**只有一份**：直接调 `series.storyboard_gate`（→ `validate.check_storyboard`），
    这里绝不另写一套。
    """
    from .. import guards
    from .. import series as series_mod
    try:
        series_mod.storyboard_gate(root, None, ep=int(ep or 1))
    except SystemExit as e:
        # 非人工模式（或调用方自己没设 HUMAN_IN_CHARGE）⇒ 门会 raise。
        # 这里**只记录、不再抛**：本函数是"顺手出报告"，不是媒体链的准入点
        # （真正的准入点在 `pipeline.run` 内的 `media_gate`）。
        log("[runner] 分镜契约门阻断：%s" % str(e)[:300])
    except Exception as e:  # noqa: BLE001
        # 门自己坏了**必须说出来**：静默返回空列表会被读成"分镜没问题"。
        log("[runner] ⚠️ 分镜契约门执行失败（%s: %s）—— 本次**没有判决**，"
            "不等于分镜合格" % (type(e).__name__, str(e)[:200]))
        return []
    rep = guards.gate_report(root, int(ep or 1)) or {}
    out = [str(x) for x in (rep.get("fatal") or [])] + \
          [str(x) for x in (rep.get("tips") or [])]
    for w in out:
        log("[runner] ⚠️ 分镜契约：" + w)
    return out


def _exec_episode(root: Path, ep: int, shots: list, log) -> dict:
    """**整片出片**（D，2026-09-19）：前端「生成最终视频」按钮的真实实现。

    它调的是 `pipeline.run(root, ep=ep)` —— **不带 `only`**，也就是媒体链唯一入口的
    "全片"形态：静帧缺的补画、静帧 QC、视频缺的补渲、成片抽帧复核、拼接
    `media/ep<N>/episode_final.mp4`。门（`media_gate`）与记账都在里面，绕不过。

    在此之前，前端的「生成最终视频」是一个**演示按钮**（弹窗写着"离线版不执行真实
    编码"，只展示一条时间线）—— 也就是说**前端根本出不了片**，"把判断权交回给人"
    只停留在纸面上（人得有"点一下就出片"的能力，判断权才成立）。
    """
    from . import pipeline
    res = pipeline.run(root, ep=ep, log=log)
    # ★ 成片的**可播地址**（前端要拿它把片子摆出来给人看）。
    #   URL 拼装只有一处真相源（`webmap.media_url`，它认 `MEDIA_BASE`）——
    #   前端绝不自己拼路径（实测事故：手拼少了一层 `ep{N}` → 全都 404）。
    _fin = root / "media" / ("ep%d" % int(ep or 1)) / "episode_final.mp4"
    if _fin.exists():
        from .. import webmap as wm
        res["final_url"] = wm.media_url(root.name,
                                        "media/ep%d/episode_final.mp4" % int(ep or 1))
        res["final_bytes"] = _fin.stat().st_size
    # 出片后附一份分镜契约判决：人看完片若要说"哪里不对"，这份报告是第一条线索。
    res["warnings"] = _storyboard_warnings(root, ep, log)
    return res


def _run_chain(root: Path, ep: int, shots: list, log, until: str = "") -> dict:
    """跑创作链 —— **复用 `scripts/drive_chain.py`**（生产驱动）。

    为什么不自己写 SDK 调用（2026-09-15 的决定）：那个脚本已经处理了三件容易错的事 ——
    **批次循环**（supervisor 一次 run 只做一个批次）、
    **`interrupted` → `command={"resume":{"decisions":[...]}}` 的 HITL 闭环**（同 thread）、
    以及**轮询替代 `runs.stream`**（后者在本机稳定 404）。重写一份必然漂移。
    """
    from .. import config as cfg
    from .. import guards
    from .. import webchain as wc

    st = wc.devserver_status()
    if not (st.get("alive") and st.get("ok")):
        raise RuntimeError(
            "dev server 未在服务本项目（D6）：%s。"
            "换项目必须重启 dev（项目目录是编译期绑定的）。"
            "见 /v1/pixa/short-drama/devserver" % json.dumps(st, ensure_ascii=False)[:300])

    py = str(cfg.PROJECT_ROOT / ".venv" / "Scripts" / "python.exe")
    driver = cfg.PROJECT_ROOT / "scripts" / "drive_chain.py"
    if not driver.exists():
        raise RuntimeError("找不到创作链驱动：%s" % driver)

    env = dict(os.environ)
    env.update({
        "SHORTDRAMA_V5_PROJECT": root.name,
        "SHORTDRAMA_OPEN_CHAIN": "1",
        "SHORTDRAMA_ALLOW_RESUME": "1",
        "NO_PROXY": "127.0.0.1,localhost,agnes-ai.com,agnes-ai.space",
        "no_proxy": "127.0.0.1,localhost,agnes-ai.com,agnes-ai.space",
        "PYTHONIOENCODING": "utf-8",
    })
    log("[runner] 创作链开始（项目 %s，第 %d 集）" % (root.name, int(ep or 1)))
    t0 = time.time()
    # ★★ 2026-09-19 修：**必须把 `--ep` 传下去**。
    #
    # 事故形态：本函数收了 `ep`，但旧命令行只有 `--timeout` ⇒ `drive_chain` 用它自己的
    # 缺省值 `--ep 1` ⇒ **点第 2 集的「生成分镜」，实际重跑了第 1 集**
    # （`bind_episode(root,1)` + 产物写 `*_ep1.md`），而下面的产物核对却按 `ep` 查
    # ⇒ 最终报"缺角色"，但**第 1 集已被白跑一遍**。
    # 这正是本项目最忌的「选了 A 实际跑 B」——只是这次的"B"是另一集。
    _cmd = [py, "-u", str(driver), root.name,
            "--ep", str(int(ep or 1)), "--timeout", "5400"]
    if until:
        # ★ 2026-09-19：**只跑到某角色为止**（`drive_chain --until`）。
        #   那条路径下 `drive_chain` 会在目标角色落盘后**取消当前 run** 并收工，
        #   所以这里不会等到 reviewer —— 它的取舍见 drive_chain 的那段注释。
        _cmd += ["--until", until]
    rc = subprocess.run(_cmd, cwd=str(cfg.PROJECT_ROOT), env=env).returncode
    log("[runner] 创作链进程结束 rc=%s（%.1f 秒）" % (rc, time.time() - t0))

    # ★★ 2026-09-18：**人工结束**（`drive_chain` 的 `rc=4`）⇒ `cancelled`。
    #
    # 必须**先于**下面的"缺角色"判据：人中止时角色**必然**是缺的，于是旧口径会报
    # 「创作链未产出这些角色的契约产物：…」—— 把"人主动停"说成"链路坏了"，
    # 排查方向被带偏。**成功 / 失败 / 人停是三态，不能混成两态。**
    # `cancelled` 已在 `TERMINAL` 里（本文件 `:55`），前端会正确收尾。
    if rc == 4:
        return {"status": "cancelled", "stage": "chain", "returncode": rc,
                "reason": "人工中止（步级人工确认）—— 详见本任务的日志尾部"}

    # 产物核对：**以磁盘事实为准**（不是"进程退出了"）。
    # 用 `guards.out_path` 查每个角色的**契约产物**（比"目录里有 .md"更准）。
    #
    # ⛔ **必须用 `GATE_ROLES`（7 个），不能用 `ROLES`（8 个，含 director）**：
    #    supervisor 架构里 `director` **就是 supervisor 本身**、不产出角色产物 →
    #    用 ROLES 判据会**永远报告缺 director**（本项目已因此白跑两轮的既有事故）。
    # 2026-09-19：`--until` 时只核对**到目标角色为止**（否则会把"下游还没跑"
    # 误报成"缺角色" ⇒ 一次正常的阶段产出被判 failed）。
    _check = list(guards.GATE_ROLES)
    if until and until in _check:
        _check = _check[:_check.index(until) + 1]
    missing = []
    for role in _check:
        rel = guards.out_path(role, ep)
        if not rel or not (root / rel).exists():
            missing.append(role)
    if missing:
        return {"status": "failed", "stage": "chain", "returncode": rc,
                "missing_roles": missing,
                "reason": "创作链未产出这些角色的契约产物：%s" % "、".join(missing)}
    # 分镜契约判决只在**分镜已产出**时才算（`--until scriptwriter` 时还没有分镜）
    return {"status": "ok", "stage": "chain", "returncode": rc,
            "until": until,
            "seconds": round(time.time() - t0, 1),
            # ★ 2026-09-19：创作链一跑完就顺手把**分镜契约判决**算出来带回给前端。
            #   为什么要在这里（而不是等出片）：分镜刚出炉时人正要决定"这一步要不要
            #   继续"——报告必须**赶在决策前**到达。人工模式下它只报不拦。
            "warnings": [] if until else _storyboard_warnings(root, ep, log)}


def _exec_chain(root: Path, ep: int, shots: list, log) -> dict:
    """跑**完整**创作链（7 个角色 → 分镜）。"""
    return _run_chain(root, ep, shots, log)


def _exec_script(root: Path, ep: int, shots: list, log) -> dict:
    """只跑到 **scriptwriter（剧本正文）** 为止。

    前端「确认简介 → 生成剧本内容」用它（2026-09-19）。为什么不是"跑完整条链再等正文"：
    正文是链的第 4 步，后面的资产卡 / 分镜 / 评审在"正文还没被人确认"时是**白跑**
    （每个角色都是真金白银的 LLM 调用 + 分钟级耗时）。
    """
    return _run_chain(root, ep, shots, log, until="scriptwriter")


_EXEC = {"keyframe": _exec_keyframe, "video": _exec_video, "assets": _exec_assets,
         "chain": _exec_chain, "episode": _exec_episode, "script": _exec_script}


def execute(run_id: str) -> int:
    """子进程主逻辑：读台账 → 执行 → 回写。返回退出码。"""
    rec = read_ledger(run_id)
    if rec is None:
        print("[runner] 台账不存在：%s" % run_id)
        return 2

    def log(msg):
        print(msg, flush=True)

    root = config.PROJECTS_DIR / str(rec["pid"])
    kind = str(rec["kind"])
    try:
        log("[runner] 开始 %s %s ep=%s shots=%s" % (kind, rec["pid"], rec["ep"],
                                                  rec["shots"] or "—"))
        t0 = time.time()
        res = _EXEC[kind](root, int(rec.get("ep") or 1), rec.get("shots") or [], log)
        rec["result"] = res
        # 把 pipeline 的 status 直接映射过来（ok / incomplete / failed / blocked）
        st = str((res or {}).get("status") or "ok")
        rec["status"] = st if st in TERMINAL else "ok"
        log("[runner] 结束 status=%s（%.1f 秒）" % (rec["status"], time.time() - t0))
        rc = 0
    except Exception as e:          # noqa: BLE001 —— 子进程必须把异常写回台账
        import traceback
        traceback.print_exc()
        rec["status"] = "failed"
        rec["result"] = {"status": "failed", "error": "%s: %s" % (type(e).__name__, e)}
        rc = 1
    finally:
        rec["ended_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        _write_ledger(rec)
    return rc


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="web 侧任务执行器（由 server spawn）")
    ap.add_argument("--run-id", required=True)
    a = ap.parse_args(argv)
    return execute(a.run_id)


if __name__ == "__main__":
    raise SystemExit(main())
