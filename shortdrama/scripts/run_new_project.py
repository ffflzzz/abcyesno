# -*- coding: utf-8 -*-
"""新项目全链路编排（Python 版）—— 等价于 scripts/run_project.sh，但：

1. **不依赖 coreutils**：本机 Bash 工具的 PATH 坏了（`curl`/`grep`/`ls`/`seq`
   全 `command not found`），.sh 版本跑不起来。这里用 subprocess + 绝对路径。
2. **QC 可关**：`--no-qc` = `SHORTDRAMA_STILL_QC=0` + `SHORTDRAMA_CLIP_QC=0`
   （**两处都要关**：只把 `CLIP_QC_ROUNDS` 归零仍会审满一轮 —— 50 镜 × 5 帧
   = 250 次视觉调用）。
3. **跑完即退**（不像 .sh 结尾 `sleep 21600`），这样后台任务能正常收尾并汇报。

沿用 run_project.sh 里那些**踩出来的**教训：
  · 归档 `.langgraph_api/`（防陈旧 run 复活占槽位 / 按过期意图推进）；
  · 创作链跑完**逐个核对 7 个角色产物**，缺一个就不启动媒体链
    （否则只会被 media_gate 拦下空转）；
  · 成功判据是**成片在盘**（存在且 mtime ≥ 本轮启动时刻），**不是**日志里有没有 `RESULT:`；
  · 项目是**编译期绑定**的（`SHORTDRAMA_V5_PROJECT`），换项目必须重启 dev。

★★ **多项目并行是支持的**（2026-09-29 起）：每个项目在自己的 `.dev/<项目名>/` 里起服
   （进度目录按项目隔离），端口从 2080-2099 **自动挑空闲的**，所以两条链互不打扰。
   实测：`par-a-0929` / `par-b-0929` 同秒起跑（22:21:07），A 22:48 收工不影响 B 跑到 23:07。

   以前写的是"一个项目一个后台任务、绝不连跑多个项目"，根因是**所有项目共用 2024**：
   `dev.terminate()` 杀的是 `langgraph.exe` 外壳，真正监听端口的 uvicorn 子进程活着，
   且仍编译期绑着**上一个项目** → 下个项目的 `wait_ok()` 被"端口已开"骗过、
   `drive_chain` 把 run 发给旧 server → **产物写进上一个项目的目录**，自己的
   `done_roles()` 永远为空 → 白等 `--timeout 5400`（90 分钟），日志上却一切"正常"。
   ⇒ **改成每次起服换一个没人听的端口，这个失效面就整体消失了**（陈旧监听赖在 2024 上
   也骗不到我们）。⛔ 不要再往"清场/杀进程"的方向修：在真终端里 `taskkill` 打得到别的
   会话的 dev server，那等于**并行时互相拆台**（沙箱里反而打不到，见下）。
   ⚠️ 沙箱内 `netstat -ano` 报的持口 pid 用 `taskkill` 会回 `错误: 没有找到进程`
   （沙箱进程视图与 host 的 PID 不是同一套编号）——端口是**随任务树被回收**才释放的。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
LANGGRAPH = str(ROOT / ".venv" / "Scripts" / "langgraph.exe")
ROLES = ("worldbuilder", "assetdesigner", "plotdesigner", "scriptwriter",
         "dialogue", "scenedesigner", "reviewer")
NOPROXY = "127.0.0.1,localhost,agnes-ai.com,agnes-ai.space"

#: 产物路径的**唯一真相源**在 `guards.OUTPUTS`（含集号 `{N}` 展开）。
#: 为什么不在这里自己拼路径：本文件里已经有一处踩过"写死 `reviewer/review.md`"的坑
#: （第 2 集会读第 1 集的判定）。判据只能有一份。
def out_path(role: str, ep: int = 1) -> str:
    import sys as _s
    if str(ROOT) not in _s.path:
        _s.path.insert(0, str(ROOT))
    from v5.guards import out_path as _op
    return _op(role, ep)


def log(path: Path, msg: str) -> None:
    line = "[%s] %s" % (time.strftime("%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _pack_of(project: str) -> str:
    """项目用哪个类型包 —— **唯一真相源是 `brief.json` 的 `pack` 字段**。

    ★ 2026-09-14 修（真实缺陷）：这里原先**硬编码 `"shortdrama"`**，而媒体链的包
      来自 `brief.json.pack`（`style.pack_of`）→ 一旦跑牛来/3D 包就会**两包混用**：
      **创作链**用短剧的角色契约（`roles._role_skill` 读 `SHORTDRAMA_STUDIO_PACK`），
      **媒体链**却用牛来的风格块/尾缀档位 —— 风格与契约互相矛盾，且日志上看不出来。

    环境变量 `SHORTDRAMA_STUDIO_PACK` 仍可**显式覆盖**（调试单包行为用）。
    """
    env = os.environ.get("SHORTDRAMA_STUDIO_PACK")
    if env:
        return env
    p = ROOT / "projects" / project / "brief.json"
    try:
        return str(json.loads(p.read_text(encoding="utf-8")).get("pack") or "") or "shortdrama"
    except Exception:  # noqa: BLE001 -- brief 还没写/坏了都回落到默认包
        return "shortdrama"


def _env(project: str, *, qc_mode: str, ep: int = 1) -> dict:
    """QC 策略：

    · `full`  —— 什么都不设（静帧 QC 开、clipqc 跑满 `CLIP_QC_ROUNDS` 轮）
    · `stills`—— **静帧 QC 开 + clipqc 全关**（推荐的人工验收档）：
      静帧 QC 是"便宜的早筛"（1 张图一次判定、重画 30–60 秒），而且静帧是视频的
      输入 —— **在源头修掉的问题视频层不会再犯**；clipqc 要抽 3 帧、修一次 3 分钟+，
      实测比视频生成贵 10 倍，交给人看片更划算。
    · `off`   —— 两处全关（只保留确定性检查：镜数/时长/画幅/分镜契约）

    ★ 注意 `SHORTDRAMA_FAST=1` **不等于**"clipqc 关"：它只把 `CLIP_QC_ROUNDS` 归零，
      循环仍会**审满一轮**（50 镜 × 5 帧 = 250 次视觉调用）。要真关必须 `CLIP_QC=0`。
    ★ 注意 docstring 旧版把「音轨」列入确定性检查 —— **代码里没有音轨检查**（实测 2026-09-14），
      已从上面那句删掉，别再照着它以为音轨有人管。
    """
    e = dict(os.environ)
    e.update({
        "SHORTDRAMA_V5_PROJECT": project,
        "SHORTDRAMA_STUDIO_PACK": _pack_of(project),
        # ★ M1（2026-09-16）：本项目跑**第几集**。角色 system prompt 里的
        #   【本角色产物路径】在起服时固化，所以集号必须在这里给定 —— 否则角色
        #   会把第 2 集的产物写到第 1 集的路径上（静默串集）。
        "SHORTDRAMA_V5_EPISODE": str(int(ep)),
        "SHORTDRAMA_OPEN_CHAIN": "1",
        "SHORTDRAMA_ALLOW_RESUME": "1",
        # ★ 派发目标必须跟 dev **实际**端口走（2026-09-21）：端口现在每次起服自动挑
        #   （`pick_dev_port`），config 默认的 2024 会在换端口后**静默派发失败**，
        #   所以这里读的是 `RUN_PORT`（本次真正用的那个），不是常量。
        "SHORTDRAMA_V5_AGENT_URL": "http://127.0.0.1:%d" % RUN_PORT,
        "NO_PROXY": NOPROXY, "no_proxy": NOPROXY,
        "PYTHONIOENCODING": "utf-8",
    })
    if qc_mode == "off":
        e["SHORTDRAMA_STILL_QC"] = "0"
        e["SHORTDRAMA_CLIP_QC"] = "0"
        e["SHORTDRAMA_FAST"] = "1"
    elif qc_mode == "stills":
        e["SHORTDRAMA_STILL_QC"] = "1"
        e["SHORTDRAMA_CLIP_QC"] = "0"      # 真关（不是靠 FAST 归零轮数）
    return e


def wait_ok(timeout: float = 180.0) -> bool:
    """探活：不依赖 curl（PATH 坏），用 socket 直连。"""
    import socket
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = socket.socket()
        s.settimeout(2)
        try:
            s.connect(("127.0.0.1", RUN_PORT))
            s.close()
            return True
        except Exception:  # noqa: BLE001
            s.close()
            time.sleep(2)
    return False


#: 显式给了 `SHORTDRAMA_DEV_PORT` 就**只用那一个**（被占了也绝不换、更不去杀占口的）；
#: 没给则从 `AUTO_PORTS` 里自动挑一个空闲口 —— 这是多项目并行的前提。
DEV_PORT_PINNED = bool(os.environ.get("SHORTDRAMA_DEV_PORT"))
DEV_PORT = int(os.environ.get("SHORTDRAMA_DEV_PORT") or 2024)
#: 自动挑口范围。刻意**不含 2024**：前端 `webchain.py` 和手工 `langgraph dev` 都默认
#: 粘在它上面，避开它就等于避开了一切陈旧监听（见模块 docstring）。
AUTO_PORTS = tuple(range(2080, 2100))
#: 本次运行**真正**使用的端口，由 `pick_dev_port()` 定；下面所有探活/清理都读它。
RUN_PORT = DEV_PORT
TASKKILL = r"C:/Windows/System32/taskkill.exe"
NETSTAT = r"C:/Windows/System32/netstat.exe"


def _p(port: "int | None") -> int:
    """默认参数在 def 时就求值了，所以"跟随本次端口"必须走这里。"""
    return RUN_PORT if port is None else port


def port_free(port: "int | None" = None) -> bool:
    """端口是否空闲（不依赖 curl / netstat）。"""
    import socket
    port = _p(port)
    s = socket.socket()
    s.settimeout(2)
    try:
        s.connect(("127.0.0.1", port))
        s.close()
        return False
    except Exception:  # noqa: BLE001
        s.close()
        return True


def pids_on_port(port: "int | None" = None) -> list[int]:
    """占着该端口且处于 LISTENING 的 pid（`netstat` 输出是 GBK，必须显式解码）。"""
    port = _p(port)
    r = subprocess.run([NETSTAT, "-ano"], capture_output=True)
    txt = r.stdout.decode("gbk", "replace")
    pids = []
    for line in txt.splitlines():
        p = line.split()
        if len(p) < 5 or p[0] != "TCP" or p[3] != "LISTENING":
            continue
        if p[1].endswith(":%d" % port):
            try:
                pids.append(int(p[4]))
            except ValueError:
                continue
    return sorted(set(pids))


def _kill_tree(pid: int) -> str:
    """`taskkill /F /T`；返回可读结果（**失败时必须可见**，不能静默吞掉）。"""
    r = subprocess.run([TASKKILL, "/F", "/T", "/PID", str(pid)],
                       capture_output=True)
    out = (r.stdout or b"").decode("gbk", "replace").strip()
    err = (r.stderr or b"").decode("gbk", "replace").strip()
    return (err or out or ("rc=%d" % r.returncode)).replace("\n", " ")[:160]


def release_port(port: "int | None" = None, budget: float = 60.0) -> list[str]:
    """反复尝试杀掉占口的进程，直到端口释放（或超预算）。返回**去重后**的诊断信息。

    ⛔ **只在收自己起的 dev server 时调用**（`stop_dev`）。起服前不要再清场 ——
       多项目并行时那个"占口的"就是**另一条正在跑的链**，杀它 = 互相拆台。
       换口才是正解，见 `pick_dev_port`。

    ★ 为什么"反复"：持口 pid 会变（实测 20644 → 24076 → 5048），单杀一次必然漏。
    ★ 为什么预算**不能给太长**：**沙箱内打不到持口进程** —— `netstat` 报
      `127.0.0.1:2024 LISTENING <pid>`，`taskkill` 却回 `错误: 没有找到进程 "<pid>"`
      （沙箱进程视图与 host 的 PID 不是同一套编号，2026-09-16 实测）。
      那一轮 150s 预算里同一句被打了 **45 遍**，把 `run.log` 撑到不可读、
      还让 3 个项目各白等 2.5 分钟。故：预算收到 60s，且**每个 pid 只记一条诊断**。
      端口实际是**随任务树被回收**才释放（约 84 秒后）。
    """
    notes: list[str] = []
    seen: set[int] = set()
    port = _p(port)
    t0 = time.time()
    while time.time() - t0 < budget:
        if port_free(port):
            return notes
        pids = pids_on_port(port)
        if not pids:
            time.sleep(2)          # connect 通但 netstat 看不到：多半正在关，等一轮
            continue
        for pid in pids:
            if pid in seen:
                _kill_tree(pid)    # 重试但不再记诊断（防刷屏）
                continue
            seen.add(pid)
            notes.append("pid=%d → %s" % (pid, _kill_tree(pid)))
        time.sleep(3)
    if not port_free(port):
        notes.append("超预算仍未释放，持口 pid=%s（沙箱内多半打不到，等任务回收）"
                     % (pids_on_port(port) or "?"))
    return notes


def stop_dev(dev: "subprocess.Popen | None") -> tuple[bool, list[str]]:
    """**真正**停掉 dev server 并确认端口已释放。返回 (ok, 诊断)。

    ★ 为什么不能只用 `dev.terminate()`（2026-09-16 实测事故，第一轮 0/5 出片）：
      `terminate()` 杀的是 `langgraph.exe` 外壳，而真正监听 2024 的 uvicorn 是它的
      **子进程**，会留下来继续跑，且仍编译期绑着**上一个项目**
      （`SHORTDRAMA_V5_PROJECT` 是起服时绑定的）。
      后果（连续跑多个项目时暴露）：下一个项目的 `wait_ok()` 因为端口已开
      **立刻返回**，`drive_chain` 于是把 run 发给了**上一个项目的 dev server**
      → 产物全部写进上一个项目的目录，而本项目的 `done_roles()` 永远看到空
      → 白等到 `--timeout 5400`（90 分钟），日志上却一切"正常"。
      实测：ep1 的 dev server 从 00:16 活到 04:14，ep2–ep5 四条创作链全写进了 ep1 的目录。
      ⇒ `taskkill /F /T` 连子树一起收，再用 `release_port` 反复确认释放。
    """
    notes: list[str] = []
    if dev is not None:
        try:
            dev.terminate()
            dev.wait(timeout=15)
        except Exception:  # noqa: BLE001
            pass
        notes.append("self pid=%d → %s" % (dev.pid, _kill_tree(dev.pid)))
    notes += release_port()
    return port_free(), notes


def pick_dev_port(project: str) -> "int | None":
    """定下本次起服用的端口，写进 `RUN_PORT`。返回 None = 挑不到，调用方**响亮终止**。

    ★ 为什么是"换口"而不是"清场"（2026-09-29，多项目并行的前提）：
      原来的 `ensure_port_free()` 起服前会 `taskkill` 掉占口进程。串行时那是对的
      （清的是**上一个项目**留下的陈旧 server）；并行时"占口的"就是**另一条正在跑的链**，
      它的 dev server 编译期绑着自己的项目，杀掉它 = 那条链的 run 全部派发失败
      —— 等于一边并行一边互相拆台。⇒ 起服前一律**不杀**，改挑一个没人听的口。

    ★ 顺带把"陈旧监听骗 `wait_ok()`"这个老失效面整体消掉了：那个病的前提是我们去挤
      同一个端口（2024）。`AUTO_PORTS` 刻意避开 2024（前端 `webchain.py` 与手工
      `langgraph dev` 都默认粘在上面），所以别人留下的 server 再也骗不到我们。

    扫描起点按项目名错开，避免两条链同一秒起跑时都从 2080 开始试。
    ⚠️ 挑口与 `langgraph dev` 真去绑定之间仍有竞态窗口，故调用方在 `wait_ok()` 之后
       还要回看 `dev.poll()`：起服进程自己退了 = 口被抢，不静默。
    """
    global RUN_PORT
    if DEV_PORT_PINNED:                      # 人显式指定的口：照用，被占就终止，不抢不换
        return DEV_PORT if port_free(DEV_PORT) else None
    off = sum(project.encode("utf-8")) % len(AUTO_PORTS)
    for p in AUTO_PORTS[off:] + AUTO_PORTS[:off]:
        if port_free(p):
            RUN_PORT = p
            return p
    return None



def run_one_episode(project: str, proj: Path, run_log: Path, ep: int,
                    qc_mode: str, chain_only: bool) -> int:
    """跑**一集**：创作链 → 三道输入门 → 媒体链。返回 0 成功 / 1 失败。

    ★ M3（2026-09-17）：抽出来的唯一理由是让 `--episodes` 能在**同一次起服**内
    连续跑多集 —— 集号已改为**运行期注入**（`roles.role_input` 的
    【本集产物路径】），所以一个 dev server 可以服务任意集。

    为什么这件事值得做（spec M3）：N 集在同一项目里 ⇒ **只需起服一次** ⇒
    顺带规避当前最大的运维坑（"一项目一起服、沙箱内杀不掉持口进程"，
    见 git 历史）。
    """
    # 3) 创作链（supervisor 7 角色）
    log(run_log, "创作链开始")
    # ★ 超时 5400 → 9000（2026-09-16 实测 ep4）：
    #   评审判 fail 时 supervisor 会**在同一个 run 内**重派上游（plotdesigner → dialogue
    #   → scenedesigner → reviewer 再评），这条恢复链比一次顺跑长得多。
    #   实测 ep4：review 10:04 判 fail → plotdesigner 10:41 重跑 → dialogue 10:42 重跑
    #   → scenedesigner/reviewer 还没跟上，就在 **5403s** 撞上 5400s 超时被腰斩。
    #   即"只差几分钟就能收敛"。宁可给足预算，也不要腰斩半程恢复。
    with (proj / "chain.log").open("w", encoding="utf-8") as f:
        # ★ M3：`--ep` 必须传 —— `drive_chain` 用它判"本集产物齐了没"并绑定集号。
        #   不传的话第 2 集会被第 1 集的产物判"已完成"（白跑一轮）。
        # ★★ 2026-09-18：**必须接住返回码**（旧实现把它丢掉了）。
        #   `drive_chain` 现在会在两种"看起来成功"的情形下返回 rc=3：
        #     ① run 自身 status=error/timeout —— 多为**账号级 API 限流（429）**或请求超时；
        #     ② 产物齐全但**角色节点一次都没执行**（supervisor **代写**产物）。
        #   旧实现只看"产物是否齐全" ⇒ 这两类都被误判成功。实测 laofuzi-shop：
        #   209 限流 → supervisor 代写 7 个角色产物 → 判"齐全" → 又白烧 56 分钟，
        #   且盘上留下时序错乱的假产物（review 比它评审的分镜早 40 分钟、镜数 14 vs 16）。
        _chain = subprocess.run([PY, "-u", "scripts/drive_chain.py", project,
                                 "--ep", str(ep),
                                 "--timeout", os.environ.get("SHORTDRAMA_CHAIN_TIMEOUT",
                                                             "9000")],
                                cwd=str(ROOT), env=_env(project, qc_mode=qc_mode, ep=ep),
                                stdout=f, stderr=subprocess.STDOUT)
    log(run_log, "第 %d 集创作链进程结束（rc=%d）" % (ep, _chain.returncode))

    if _chain.returncode != 0:
        # 诊断细节已在 chain.log 末尾（含限流 / 代写的排查指引）。
        log(run_log, "!! 创作链**判定失败**（drive_chain rc=%d）→ 不进入媒体链。"
            % _chain.returncode)
        log(run_log, "!! 详见 chain.log 末尾。**重跑必须加 `--fresh`**"
                     "（盘上可能是半程态或代写产物）")
        return 1

    # ★ M2：必须查**本集**的产物路径（`glob("*.md")` 在第 2 集会被第 1 集的
    #   `scriptwriter_ep1.md` 满足 → 第 2 集缺产物也报"齐全" → 白跑一轮媒体链）。
    missing = [r for r in ROLES
               if not (proj / out_path(r, ep)).exists()]
    if missing:
        log(run_log, "!! 缺第 %d 集角色产物：%s → 不启动媒体链（会被门拦下空转）"
            % (ep, missing))
        log(run_log, "!! 若缺 reviewer，按顺序查 dev.log："
                     "GraphRecursionError（图递归超限）→ "
                     "OpenAIRateLimitError(429)（**账号级限流**，常见于**并行任务抢同一账号额度**）→ "
                     "OpenAITimeoutError（请求超时）")
        return 1
    log(run_log, "第 %d 集创作链产物齐全：%s" % (ep, "、".join(ROLES)))

    # ★ **产物齐全 ≠ 链合格**（2026-09-16 实测 ep4）：reviewer 可能判 fail 并要求重派，
    #   此时 7 个文件都在盘上，旧实现照样报"齐全"→ 进媒体链 → 被 media_gate 挡下
    #   （挡得对，但白跑一轮、且日志误导）。这里把判定**显式读出来**。
    # ★ M2：判定文件走 `guards.resolve_path`（**集级路径 + 旧名回退**）——
    #   写死 `reviewer/review.md` 在多集下会读**第 1 集**的判定（或读不到而误报
    #   「读不出判定块 → media_gate 会拦」），全是最难查的那类误导。
    rev_dec = None
    try:
        import sys as _sys
        _sys.path.insert(0, str(ROOT))
        from v5 import decision as _decision
        from v5 import guards as _guards
        _rv = _guards.resolve_path(proj, "reviewer", ep)
        if _rv.exists():
            rev_dec = _decision.parse_decision(_rv.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 -- 解析不了按"无判定"处理，下面会告警
        log(run_log, "⚠️ reviewer 判定解析异常：%s" % str(e)[:120])
    if rev_dec is None:
        log(run_log, "⚠️ 读不出 reviewer 判定块（第 %d 集）→ media_gate 会判「未通过」"
                     "并拦下媒体链。请检查本集 review 文件末尾的 ```yaml 判定块。"
                     % ep)
    else:
        try:
            from v5 import decision as _decision2
            _passed = _decision2.normalize_pass(rev_dec)
        except Exception:  # noqa: BLE001
            _passed = bool(rev_dec.get("pass"))
        if not _passed:
            # ★ 2026-09-29：这里**不再自己决定出不出片**，改问媒体门（判据只留一份）。
            #   门按集累计"渲染被评审拦下几次"，超过 `SHORTDRAMA_MAX_REVISIONS`（默认 2）
            #   就记 `review.force_passed` 并返回可渲染 ⇒ 全自动链路终于有出口，
            #   不再是"反复重派到撞墙钟预算、rc=0 静默收工、缺的角色没人补"
            #   （0929 ep2 就是这么丢的）。旧实现在这里 return 1，等于把出口堵死。
            _g_ok, _g_why = False, "门未被调用"
            try:
                from v5 import guards as _guards2
                # ★ 必须先 `reconcile_manifest` 再问门（2026-09-29 我自己踩的坑）：
                #   这条路径上 `.agent_state.json` 的 `phases` 是空的（实测：产物齐全
                #   但清单只有 episode_index），直接喂给门会得到「创作链未完成（缺 7 个角色）」
                #   —— 于是**根本走不进"评审未通过"那条分支**，`review_blocks` 不累计、
                #   `force_passed` 永远不会写 ⇒ 出口照旧堵死。pipeline 早就用
                #   `reconcile_manifest` 治过这个（其 docstring 记着 2026-09-12 同型事故），
                #   我新加的这次调用漏了它，等于把老 bug 重新引入。
                _m = _guards2.reconcile_manifest(
                    proj, _guards2.load_manifest(proj), ep=ep)
                _g_ok, _g_why = _guards2.media_gate("render", _m, ep=ep, root=proj)
            except Exception as e:  # noqa: BLE001 -- 调用异常按"不渲染"处理，但要报出来
                _g_why = "media_gate 调用异常：%s" % str(e)[:140]
            if not _g_ok:
                log(run_log, "!! 创作链产物**齐全但 reviewer 判定 fail**（rerun=%s）→ 不启动媒体链。"
                             "门说：%s" % (rev_dec.get("rerun"), _g_why[:220]))
                log(run_log, "   首条原因：%s"
                             % ((rev_dec.get("reasons") or [""])[0])[:300])
                log(run_log, "   处理：盘上是**半程恢复**的中间态（chain.log 末尾若停在 timeout "
                             "即为腰斩）。**重跑必须加 --fresh**，否则物化守卫看到 .md 就判"
                             " complete、整链空转。")
                return 1
            log(run_log, "!! reviewer 仍判 fail，但门已按上限（SHORTDRAMA_MAX_REVISIONS）"
                         "记 force_passed → **放行渲染**。上面列出的缺陷未消化，成片须人工复核。")
        else:
            log(run_log, "reviewer 判定：pass ✓")

    if chain_only:
        log(run_log, "=== --chain-only：到此结束 ===")
        return 0

    # 4) 门预检（跑媒体链前先把确定性判据过一遍，省得跑一半被拦）
    # ★ M2：显式传集号 —— 第 2 集必须按**本集**的分镜/对白过门，不能按 manifest 猜。
    r = subprocess.run([PY, "-c",
                        "import sys;sys.path.insert(0,'.');"
                        "from pathlib import Path;from v5 import series;"
                        "series._input_gates(Path('projects/%s'),None,ep=%d);"
                        "print('GATES-OK')" % (project, ep)],
                       cwd=str(ROOT), env=_env(project, qc_mode=qc_mode, ep=ep),
                       capture_output=True, text=True)
    (proj / "gates.log").write_text((r.stdout or "") + (r.stderr or ""),
                                    encoding="utf-8")
    if "GATES-OK" not in (r.stdout or ""):
        log(run_log, "!! 输入门未过 → 不启动媒体链。详见 gates.log")
        return 1
    log(run_log, "输入门通过（brief / 分镜契约 / 资产契约）")

    # 5) 媒体链
    log(run_log, "媒体链开始（qc=%s）" % qc_mode)
    t_media = time.time()
    with (proj / "media.log").open("w", encoding="utf-8") as f:
        subprocess.run([PY, "-u", "-m", "v5.series", project,
                        "--resume-media", "--ep", str(ep)],
                       cwd=str(ROOT), env=_env(project, qc_mode=qc_mode, ep=ep),
                       stdout=f, stderr=subprocess.STDOUT)
    text = (proj / "media.log").read_text(encoding="utf-8", errors="replace")
    # ★ **成功判据 = RESULT 出现 或 成片是本次新写的**（2026-09-14 实测两次）：
    #   沙箱的批量删除保护会拦下 `clips/_concat.txt` 与 `media/ep<N>/.running`
    #   的清理，并抛**非 Exception 的异常** → 进程在 `print("RESULT:")` **之前**
    #   结束 → 只看 RESULT 会把**已经出片**的一轮判成失败
    #   （paper-crane 与 lost-and-found 各命中一次，成片都在盘上且正常）。
    #   本项目的原则：**验收以磁盘事实为准**。
    # ★ **「有 RESULT」≠「成功」**（2026-09-14 实测）：
    #   `RESULT` 的 `status` 可能是 `incomplete`（缺镜 → 按设计**不拼接** →
    #   `final` 为空）或 `blocked`（门没过）。原实现只看"文本里有没有 RESULT:"
    #   → 把 `status=incomplete, final=""` 的一轮**报成了「媒体链成功」**
    #   （village-scale 实测：24/26 clip、2 镜生成失败、**根本没有成片**，
    #   编排层却打了"媒体链成功"，我据此差点给用户报成功）。
    #   **判据必须是"成片在盘"** —— 本项目铁律：验收以磁盘事实为准。
    # ★ M3：判据收窄到**本集**的成片（`media/ep{N}/`）。用 `media/ep*/` 通配 + mtime
    #   在第 2 集时会把"第 1 集刚补渲出来的成片"误当成第 2 集的成果。
    _final = proj / "media" / ("ep%d" % ep) / "episode_final.mp4"
    finals = [_final] if (_final.exists() and _final.stat().st_mtime >= t_media) else []
    res = None
    for l in text.splitlines():
        if l.startswith("RESULT:"):
            try:
                res = json.loads(l.split("RESULT:", 1)[1].strip())
            except Exception:  # noqa: BLE001 -- RESULT 不是 JSON 时按"无结构化结果"处理
                res = None
    if finals:
        log(run_log, "媒体链出片成功：%s（status=%s）"
            % (finals[0], (res or {}).get("status") or "?"))
        return 0
    if res is not None:
        log(run_log, "!! 媒体链结束但**没有成片**：status=%s missing=%s residual=%s "
                     "still_residual=%s"
            % (res.get("status"), (res.get("missing") or [])[:6],
               (res.get("residual") or [])[:6],
               (res.get("still_residual") or [])[:8]))
        log(run_log, "   注：缺镜时按设计**不拼接**（不覆盖已成片）→ 需补渲缺失镜后再出片。")
        return 1
    log(run_log, "!! 媒体链未产出 RESULT 且没有成片 —— 失败。最后 5 条有效日志：")
    keep = [l for l in text.splitlines()
            if l.strip() and "trace=" not in l and "multipart" not in l]
    for l in keep[-5:]:
        log(run_log, "   " + l[:200])
    return 1


def main() -> int:
    project = sys.argv[1] if len(sys.argv) > 1 else ""
    if not project:
        print("用法: python scripts/run_new_project.py <项目名> "
              "[--stills-qc|--no-qc] [--chain-only] [--fresh] [--ep N] "
              "[--episodes 1-4]")
        return 2
    qc_mode = ("off" if "--no-qc" in sys.argv
               else "stills" if "--stills-qc" in sys.argv else "full")
    chain_only = "--chain-only" in sys.argv
    # ★ M3（2026-09-17）：集号解析。两种写法：
    #   `--ep N`        单集（老写法，完全兼容）
    #   `--episodes A-B` **一次起服**连续跑 A..B（`1-4` / `3` / `1,3,5`）
    # M1 时"多集必须逐集各起一次 dev"（集号编译期固化）；M3 把产物路径改成
    # **运行期注入**（roles.role_input 的【本集产物路径】）⇒ 同一个 dev server
    # 可以服务任意集，于是"一次起服跑 N 集"成立，顺带绕开"一项目一起服、
    # 沙箱内杀不掉持口进程"那个运维坑。
    _ep = 1
    if "--ep" in sys.argv:
        try:
            _ep = max(1, int(sys.argv[sys.argv.index("--ep") + 1]))
        except (IndexError, ValueError):
            print("--ep 需要一个 ≥1 的整数")
            return 2
    _eps = [_ep]
    if "--episodes" in sys.argv:
        try:
            _spec = sys.argv[sys.argv.index("--episodes") + 1]
        except IndexError:
            print("--episodes 需要一个集号写法（如 1-4 / 3 / 1,3,5）")
            return 2
        _sys_path = str(ROOT)
        if _sys_path not in sys.path:
            sys.path.insert(0, _sys_path)
        from v5.series import parse_episodes as _parse_eps
        _eps = _parse_eps(_spec)
        _ep = _eps[0]
    # ★ `--fresh` + 多集是**危险组合**，直接拒绝而不是静默损坏。
    #   原因：`--fresh` 归档的是**整个项目的创作链产物目录**（`scriptwriter/` /
    #   `dialogue/` / `scenedesigner/` …），而这些目录里同时住着
    #   `scriptwriter_ep1.md` 与 `scriptwriter_ep2.md` —— 只要跑的不是第 1 集，
    #   就会把**别的集的产物一起归档掉**（那些集就再也续不上媒体链了）。
    #   新的一集本身是**新产物**，不需要 `--fresh`；它只用于"**同一集**上一轮被打断"。
    if "--fresh" in sys.argv and (_eps != [1]):
        print("!! 拒绝执行：`--episodes %s` 与 `--fresh` 不能同时用。\n"
              "   `--fresh` 归档的是整个项目的产物目录，会把**别的集**的产物一起挪走。\n"
              "   跑新的一集**不要**加 `--fresh`；只有「同一集上一轮被打断、要重跑」才用它。\n"
              "   确需只重跑某一集：手工把它的 `*_ep<N>.md` 移走后重跑。"
              % " ".join(str(e) for e in _eps))
        return 2
    proj = ROOT / "projects" / project
    run_log = proj / "run.log"
    proj.mkdir(parents=True, exist_ok=True)

    log(run_log, "=== %s 全链路启动（qc=%s chain_only=%s episodes=%s）==="
        % (project, qc_mode, chain_only, _eps))

    # 0) --fresh：归档上一次的创作链产物（**保留** brief/assets/images）
    #    为什么必须归档而不是直接重跑：物化守卫看到盘上的 `.md` 就判 complete，
    #    上一次被打断留下的**半截产物**会被当成已完成 → 整条链空转、下游读残缺内容。
    #    （`guards.stash_artifacts` 是运行期机制，这里是编排层兜底。）
    if "--fresh" in sys.argv:
        bak = proj / ".rerun_backup" / time.strftime("%m%d-%H%M%S")
        keep = {"brief.json", "assets.json", "images", "run.log", "chain.log",
                "media.log", "dev.log", "gates.log"}
        moved = []
        for p in sorted(proj.iterdir()):
            if p.name in keep or p.name.startswith(".rerun_backup"):
                continue
            bak.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(bak / p.name))
            moved.append(p.name)
        log(run_log, "已归档旧创作链产物（保留 brief/assets/images）：%s" % (moved or "无"))

    # 1) 归档**本项目自己**的 .langgraph_api：防陈旧 run 复活
    # ★ 2026-09-29：状态目录改成按项目隔离。langgraph 的内存运行时把 `.langgraph_api/`
    #   写在**进程 cwd** 下，目录名写死（`langgraph_runtime_inmem/checkpoint.py:59`、
    #   `store.py:83`、`database.py:99` 三处），CLI 没有参数能改、也没有环境变量能改
    #   （`langgraph dev --help` 全量选项已查）。所以以前"所有项目都从仓库根起服"=
    #   共用同一份进度，第二条链启动会把第一条正在用的整个搬走 —— 症状是
    #   「轮数一路涨、产物不新增、日志零错误」，极难归因。
    #   解法：每个项目在自己的 `.dev/<项目名>/` 里起服（探针实测：换 cwd 后 9 张图照常
    #   加载、状态目录落在各自目录、不再碰仓库根那份）。前端路径早就用这个约定
    #   （`webchain.py` 的 `cwd=RUNTIME_ROOT` + 绝对 config），这里是把 CLI 并过来。
    runtime = ROOT / ".dev" / project
    runtime.mkdir(parents=True, exist_ok=True)
    # ★ 配置里的 `dependencies` 与 `env` 也是**跟着进程 cwd 解析**的（仓库里那份写的是
    #   `".."` / `"..\\.env"`，从仓库根启动时刚好指对）。cwd 一改，就必须换成本项目专属的
    #   一份**绝对路径**配置 —— 探针实测：绝对 dependencies 能让 9 张图照常加载。
    cfg = runtime / "langgraph.json"
    try:
        import json as _json
        _base = _json.loads((ROOT / "v5" / "langgraph.json").read_text(encoding="utf-8"))
        _envf = ROOT / ".env"
        if not _envf.exists():
            _envf = ROOT.parent / ".env"
        _base["dependencies"] = [str(ROOT)]
        _base["env"] = str(_envf)
        cfg.write_text(_json.dumps(_base, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001 -- 生不成配置就别静默退回旧路径（那正是并行相撞的根因）
        log(run_log, "!! 无法为项目生成绝对路径的 langgraph 配置：%s → 终止"
                     "（不退回仓库根启动：那会与其他项目共用进度目录）" % str(e)[:140])
        return 1
    api = runtime / ".langgraph_api"
    if api.exists():
        shutil.move(str(api), str(runtime / (".langgraph_api.bak-" + time.strftime("%m%d%H%M"))))
        log(run_log, "已归档本项目旧的 .langgraph_api（其他项目不受影响）")

    # 2) 起 dev server（项目编译期绑定）
    # ★ 端口**只挑空闲的、起服前一律不清场**：并行时"占着口的那个"就是另一条正在跑的链，
    #   杀它 = 把对方的 run 全打断（旧 `ensure_port_free` 的行为，见 `pick_dev_port`）。
    port = pick_dev_port(project)
    if port is None:
        if DEV_PORT_PINNED:
            log(run_log, "!! 显式指定的端口 %d 已被占用（持口 pid=%s）→ 终止。"
                         "**不去杀它**（可能是别的会话或另一条链的 dev server）；"
                         "要并行就别钉 SHORTDRAMA_DEV_PORT。"
                % (DEV_PORT, pids_on_port(DEV_PORT) or "?"))
        else:
            log(run_log, "!! 自动端口段 %d-%d 全被占用 → 终止（并行的条数超过了端口段）"
                % (AUTO_PORTS[0], AUTO_PORTS[-1]))
        return 1
    dev = subprocess.Popen(
        # `--config` 必须是**绝对路径**：进程 cwd 已改成本项目专属目录，
        #   相对的 `v5/langgraph.json` 解析不到。
        # `--no-reload`：uvicorn 的 WatchFiles 监视整个仓库根，链在跑时改任何 `.py`
        #   都会重载 worker、打断运行中的 run（2026-09-29 两次实测）。关掉后
        #   "边跑边改"才成立；代价是改动要重启才生效 —— 换项目本来就要重启（编译期绑定），
        #   所以这个代价等于零。
        [LANGGRAPH, "dev", "--config", str(cfg),
         "--host", "127.0.0.1", "--port", str(port), "--no-browser", "--no-reload"],
        cwd=str(runtime), env=_env(project, qc_mode=qc_mode),
        stdout=(proj / "dev.log").open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT)
    log(run_log, "dev server 启动（pid=%d，SHORTDRAMA_V5_PROJECT=%s，端口=%d，运行目录=%s）"
                 % (dev.pid, project, port, runtime.relative_to(ROOT)))
    if not wait_ok():
        log(run_log, "!! dev server 未就绪 → 终止（见 dev.log）")
        stop_dev(dev)
        return 1
    if dev.poll() is not None:
        # 挑口与真去绑定之间有竞态：口被别人抢先占了，`langgraph dev` 自己退出去，
        # 而 `wait_ok()` 探到的可能是**那个抢口的人**。端口不再是我们的判据，进程才是。
        log(run_log, "!! 端口 %d 有应答但起服进程（pid=%d）已退出 → 终止：应答的不是本项目 "
                     "的 server，继续跑就会把 run 发到别的项目去。见 dev.log"
            % (port, dev.pid))
        stop_dev(dev)
        return 1
    log(run_log, "dev server 就绪（端口 %d）" % port)

    try:
        rc = 0
        for _cur_ep in _eps:
            rc = run_one_episode(project, proj, run_log, _cur_ep, qc_mode, chain_only)
            if rc != 0:
                log(run_log, "!! 第 %d 集未成功（rc=%d）→ **停止**，不再继续后面的集"
                    "（静默跳过会让「出了 3 集」看起来像「出了 4 集」）"
                    % (_cur_ep, rc))
                break
        return rc
    finally:
        log(run_log, "=== 全链路结束，关掉 dev server ===")
        stop_ok, stop_notes = stop_dev(dev)
        if not stop_ok:
            # 不静默：端口没释放 = 下一个项目必然把 run 发错地方
            log(run_log, "!! dev server 的端口 %d 未释放（pid=%d）—— 下一个项目会因此把 run "
                         "发到本项目。诊断：%s。请人工清理后再跑。"
                % (RUN_PORT, dev.pid, "；".join(stop_notes)))
        else:
            log(run_log, "dev server 已停，端口 %d 已释放" % RUN_PORT)


if __name__ == "__main__":
    raise SystemExit(main())
