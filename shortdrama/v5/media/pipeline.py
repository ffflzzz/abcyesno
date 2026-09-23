# -*- coding: utf-8 -*-
"""静态画面先行的媒体管线（新架构核心）。

分镜 → 镜间关系规划 → 逐镜静帧（带参考图）→ 静帧硬伤 QC → 图生视频 → 拼接

与旧架构的三处根本差别：
  1. 静帧是生产输入（first_frame），不是一次性质检产物
  2. 首帧来源由镜间关系决定（连续/匹配/跳切），镜间不再各自为政
  3. 硬伤在静帧阶段修（重画一张 30-60s），而不是渲完再改（65s+3min+配额）
"""
from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from pathlib import Path

from .. import config
from ..llm import is_rate_limit as _is_rate_limit
from .. import validate
from ..guards import load_brief
from .. import vendors
from . import (approvals, assets, cast, clipqc, compose, prompt, qc, relations,
               stills, storyboard, style, video, video_plan)

# 判据已**收敛到 `qc.py` 一处**（2026-09-13）：原先 pipeline 与 clipqc 各有一份，
# 而 clipqc 那份**少了 10 个词**（五官/无脸/面具/真人脸/面部特征/漂移/窗外/纯黑/
# 背景出现/背景里出现）—— 同一判据两份实现必然漂移。这里只做 re-export，
# 保持 `pipeline.HARD_KEYS` 的既有引用（含测试）不破。
HARD_KEYS = qc.HARD_KEYS

# 硬伤重生成时的强化约束：模型对"木牌/纸张上的字"有顽固倾向，
# 普通负面约束压不住 → 用更强的正向描述（把有字的面改成无字材质）。
# 注意两条铁律（实测）：
#   1. **不能提"文字/字幕/笔画"**——负面提法反而诱发文字渲染。
#   2. **不能提任何物件名词**（板子/布条/纸张/屏幕/墙面）——它们会被当成
#      "画面里该有的东西"，模型据此补出招牌/贴纸并烧字。只用渲染层描述。
#
# 2026-09-12：与静帧尾缀**同源分叉**。"表面是平涂色块"只对反质量/风格化包成立，
# 对写实/3D 包会把重画推向廉价平涂——而重画路径恰恰是写实镜最需要帮助的时候
# （noodle-night 的 LN04/LN06 就走这条路，两轮重画后带伤出厂）。
# 档位与 `prompt.STILL_TAIL_PRESETS` 保持一致，由 pack.json 的 "still-tail" 决定。
ANTI_TEXT_HARD_FLAT = ("，场景里所有表面都只是平涂纯色块或低分辨率重复贴图，"
                       "没有任何可辨认的图形细节。")
ANTI_TEXT_HARD_MATERIAL = "，" + prompt.STILL_TAIL_PRESETS["material"]
# 分屏重生成时的强化约束。
# **2026-09-10 修正**：原写法是"整幅画面是单一连续的完整构图，一次成像的单张
# 照片"——语义正确但不够"单一画面"的味道。与 NOSPLIT 同步改成正向的
# "单幅照片"表述（不出现分屏/格子/拼接等概念词，见 prompt.NOSPLIT 的说明）。
ANTI_SPLIT_HARD = ("，这是一张单幅完整照片，一次曝光成像，取景框内是一个连续的完整空间。")
# 无脸载体（面具/纸人/道具脸）长出真人五官时的强化约束。
# 只描述**材质与结构**，不给"眼睛/鼻子/嘴"这类部件名词——按本项目铁律，
# 提示词里出现部件名词就会被模型画出来（与"面部/手部"诱发凭空长脸同源）。
ANTI_FACELESS_HARD = ("，该载体表面是连续完整的平涂色块，只有整体的外轮廓与"
                      "浅凹陷起伏，表面没有任何独立的细节块面。")
# 背景/窗外场景漂移时的强化约束（2026-09-10 maskparade LN12 实测：
# 分镜要求夜间车窗为纯黑，实际画成荒漠沙丘+天空）。
# 只描述**该区域的材质与内容**（一整块连续的黑），不提任何"沙丘/街景/建筑"
# 类名词——按本项目铁律，提什么长什么。
ANTI_DRIFT_HARD = ("，取景框内每个被设定为纯黑的区域都是同一块连续的纯黑色平面，"
                   "表面只有均匀的低噪点颗粒，没有任何可辨认的形体或色块起伏。")


def _anti_text_hard(project_root: Path) -> str:
    """重画时的反烧字强化约束（档位跟静帧尾缀一致）。

    单一真相：文本取自 `prompt.STILL_TAIL_PRESETS`，这里只加前导逗号（追加到
    既有提示词尾部用）；档位取自 pack.json 的 "still-tail"。
    """
    kind = style.still_tail_kind(project_root)
    if kind == "flat":
        return ANTI_TEXT_HARD_FLAT
    return ANTI_TEXT_HARD_MATERIAL


# 景别跑偏时的量化提示（占比越具体，模型越容易命中）
# **不能写人物部件名词**（实测 2026-09-09 LN02）：原「特写」提示写的是
# "面部、手部或某个物件的局部占满画面"，模型把"面部/手部"当成"画面里该有的
# 元素"→ 前景凭空长出一张脸和一只手。与"不能提物件名词"是同一条教训：
# 提示词里出现名词，模型就会把它画出来。只说**占比**，主体由分镜正文决定。
_FRAMING_HINT = {
    "远景": "环境为主体，人物在画面中很小或不可辨",
    "大远景": "环境为主体，人物在画面中很小或不可辨",
    "全景": "人物全身可见，占画面主要部分，能看清人物与周围环境的关系",
    "中景": "人物腰部以上入画",
    "近景": "人物胸部以上入画",
    "特写": "被摄主体的局部占满整个画幅，画面里只有这一处局部",
    "大特写": "被摄主体的细节占满整个画幅",
}

# 累计重拍计数（跨进程轮次）。放在 media/ep<N>/ 下随项目走，不依赖内存。
_REQUEUE_FILE = "clip_requeue_tally.json"
# 静帧重生成的累计计数（跨进程）。**必须有**：`max_regen` 只在单进程内计数，
# 外部续跑器每重启一次就重置 → 静帧会被无限重画（2026-09-10 实测：4 轮驱动
# 各画一轮，硬伤数 9→7→10→9 震荡，纯烧配额）。与 clip 侧对称。
_STILL_REQUEUE_FILE = "still_requeue_tally.json"

#: 硬伤**类别** → 重画时的定向强化约束。`_defect_kind(desc)` 是**唯一**的类别判据，
#: 两处消费：① 选 `extra`（原实现在重画循环里内联了这套 if）；
#: ② 作为"同类问题"的签名，用于「连续两轮同类 → 停止重画」（2026-09-16）。
#:
#: 为什么要抽出签名这一层：原实现只把类别用于选 `extra`，**没有任何地方判断
#: "上一轮是不是也是这类问题"** → 同一镜反复报同一类问题时仍会一直重画到上限。
#: 实测 felt-bach：LN08「两个相似老头」/ LN16「缺风箱工」连续两轮落到「未归类」，
#: 原样重画（同提示词、换种子）到撞上限，问题照旧 —— 那两轮**纯白做**。
_DEFECT_KINDS = (
    ("text", ("文字", "字符", "字幕")),
    ("split", ("分屏", "多格", "拼接", "拼图", "上下两", "两幅")),
    ("faceless", ("五官", "无脸", "面具", "真人脸", "面部特征")),
    ("drift", ("漂移", "窗外", "纯黑", "背景出现", "背景里出现")),
    ("framing", ("景别不符",)),
)


def _defect_kinds(desc: str) -> list[str]:
    """**全部**命中的类别（供 `extra` 累积用 —— 一条描述可能同时命中多类）。"""
    d = str(desc or "")
    return [tag for tag, keys in _DEFECT_KINDS if any(k in d for k in keys)]


def _defect_kind(desc: str) -> str:
    """硬伤描述 → **类别签名**（取第一个命中；无命中 = "unclassified"）。

    "unclassified" 本身也是信息：它意味着**定向强化约束为空**、只能
    「按原提示词换种子重画」，也就是一次概率赌博。故连续两轮都落到它时，
    第三轮不再重画（见 `config.STILL_QC_STOP_REPEAT`）。
    """
    ks = _defect_kinds(desc)
    return ks[0] if ks else "unclassified"


#: 类别 → 重画时的定向强化约束（值）。"text" 因依赖 pack 档位（`anti_text`）单独处理，
#: "framing" 因需要本镜景别名也单独处理。
_DEFECT_EXTRA = {
    "split": ANTI_SPLIT_HARD,
    "faceless": ANTI_FACELESS_HARD,
    "drift": ANTI_DRIFT_HARD,
}



#: 静帧 QC 的**跨轮次已审记录**（2026-09-18）
#: `{镜名: {"mtime": <静帧文件 mtime>, "clean": <上次是否判干净>}}`
#:
#: 为什么需要：`--resume-media` 每轮都从静帧阶段重走，**第 0 轮重新全审所有镜**。
#: 实测 laofuzi-shop：为补 **1 镜视频**付了 **20 分 53 秒**，其中静帧 QC 重审
#: 18–19 镜占大头。而"图没变还复审"本就是纯开销 —— 上方注释已论证过：
#: 同一张图连审会**概率翻判**（LN12 实证 1/1/2 处不同结论），
#: 复审引入的是**噪声而非信号**，还会凭空制造重画、空烧生图配额。
#: ⇒ 图未变 **且** 上次判干净 ⇒ 跳过；其余（图变了 / 上次有问题）照常审。
_STILL_SEEN_FILE = "still_qc_seen.json"


def _load_still_seen(root: Path, ep: int) -> dict:
    import json
    p = root / "media" / ("ep" + str(ep)) / _STILL_SEEN_FILE
    if p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _save_still_seen(root: Path, ep: int, seen: dict) -> None:
    import json
    d = root / "media" / ("ep" + str(ep))
    d.mkdir(parents=True, exist_ok=True)
    (d / _STILL_SEEN_FILE).write_text(
        json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_tally(root: Path, ep: int, fname: str) -> dict:
    import json
    p = root / "media" / ("ep" + str(ep)) / fname
    if p.exists():
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return {k: int(v) for k, v in d.items() if str(v).isdigit()}
        except Exception:  # noqa: BLE001
            return {}
    return {}


def _save_tally(root: Path, ep: int, fname: str, tally: dict) -> None:
    import json
    d = root / "media" / ("ep" + str(ep))
    d.mkdir(parents=True, exist_ok=True)
    (d / fname).write_text(
        json.dumps(tally, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_requeue_tally(root: Path, ep: int) -> dict:
    return _load_tally(root, ep, _REQUEUE_FILE)


def _save_requeue_tally(root: Path, ep: int, tally: dict) -> None:
    _save_tally(root, ep, _REQUEUE_FILE, tally)


def _load_still_tally(root: Path, ep: int) -> dict:
    return _load_tally(root, ep, _STILL_REQUEUE_FILE)


def _save_still_tally(root: Path, ep: int, tally: dict) -> None:
    _save_tally(root, ep, _STILL_REQUEUE_FILE, tally)


def call_with_backoff(fn, *, attempts: int = 5, base: float = 5.0, sleep=None,
                      label: str = ""):
    """调用 `fn()`：**限速异常 → 指数退避重试**，其余异常立即抛。

    为什么必须"只退避、不兜底"（2026-09-12 事故）：
        供应商是免费额度，每分钟窗口很窄；**重生成一批静帧（图片调用）与 QC
        视觉调用叠在一起**就会撞 429（实测 clockmaker 17:00 撞一次，而事后
        单独连发 5 次调用全部正常 → 是突发限速，不是额度耗尽）。
        若把 429 兜底成"无硬伤"，就是**静默漏检**——硬伤镜直接进成片。
        所以只能退避等待，达 `attempts` 仍失败才抛，由上层如实记录。

    退避时长 = `base * 2**i`（默认 5/10/20/40s，共 75s）。
    `sleep` 可注入（测试用），默认 `time.sleep`。
    """
    if sleep is None:
        sleep = time.sleep
    last: BaseException | None = None
    for i in range(max(1, attempts)):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            if not _is_rate_limit(e):
                raise
            last = e
            if i == max(1, attempts) - 1:
                break
            if label:
                print("[retry] %s 撞限速，%.0fs 后重试（第 %d/%d 次）"
                      % (label, base * (2 ** i), i + 1, attempts))
            sleep(base * (2 ** i))
    raise last


# ─── 媒体链独占锁（2026-09-13，spec §3.4 风险 #2/#3）─────────────────────────
#
# `video_jobs.json` 是**单写入者假设**的状态机，`episode_final.mp4` 也是。
# 单镜重渲（`--rerender`）会与"正在跑的全链路"或"另一个重渲"并发 ——
# 两个写入者交错 save 会把状态机写坏（丢 job / 把 completed 覆盖成 pending），
# 拼接重入则可能产出半截成片。
#
# **陈旧判定靠 mtime 心跳，绝不靠 PID 存活探查**：CPython 在 Windows 上
# `os.kill(pid, 0)` 的实现是 `OpenProcess(PROCESS_ALL_ACCESS)` +
# `TerminateProcess(hProc, sig)` —— 拿它"探测"一个正在跑的媒体链，
# 会**真的把它杀掉**（本项目在 Windows 上跑）。故：持锁期间起一条每 60s
# `touch()` 的心跳线程，超过 `MEDIA_LOCK_STALE_S` 无心跳即视为残留锁
# （进程被沙箱回收后留下的），后来者接管。
_LOCK_NAME = ".running"
_LOCK_HEARTBEAT_S = 60.0


def _lock_path(project_root: Path, ep: int) -> Path:
    return project_root / "media" / ("ep" + str(ep)) / _LOCK_NAME


def _release_lock(p: Path, log=print) -> None:
    """删锁；**删不掉时把它的 mtime 归零**，避免"自己跑完的锁把自己拦下"。

    沙箱的批量删除保护会拦下 `unlink()` 并抛**非 `Exception`** 的异常
    （2026-09-14 实测：被评审门拦下的那一轮，`media/ep1/.running` 留在了盘上，
    mtime 正好是进程退出时刻）。调用方必须吞异常以免崩进程，但**不能就此不管**：

      · 锁的陈旧判据是 **mtime 心跳**（`MEDIA_LOCK_STALE_S`，默认 300s）；
      · 留着一把 mtime 新鲜的锁 → **下次运行在 TTL 内会被自己刚跑完的锁拒之门外**。

    `os.utime(p, (0, 0))` 只改元数据、不删文件，不受批量删除保护影响 →
    锁立刻被判陈旧、下次运行可直接接管。**绝不查 PID**（Windows 上
    `os.kill(pid, 0)` 会真的 `TerminateProcess` 杀掉在跑的媒体链）。
    """
    try:
        p.unlink()
        return
    except FileNotFoundError:
        return
    except BaseException:  # noqa: BLE001 -- 沙箱保护抛的不是 Exception
        pass
    try:
        os.utime(p, (0, 0))
        log("[media-lock] ⚠️ 锁文件删除被拦下 → 已把 mtime 归零（下次运行可直接接管）：%s" % p)
    except BaseException:  # noqa: BLE001
        log("[media-lock] ⚠️ 锁文件既删不掉也无法归零：%s（确认无并发后手工删除）" % p)


def _acquire_lock(p: Path, log=print) -> bool:
    """原子创建锁文件（O_CREAT|O_EXCL）。陈旧锁（无心跳）接管，返回是否拿到。"""
    p.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                age = time.time() - p.stat().st_mtime
            except OSError:
                age = 0.0
            if age <= config.MEDIA_LOCK_STALE_S:
                log("[media-lock] ⛔ 已有媒体链在跑（%s，%.0fs 前的心跳）→ 拒绝并发启动。"
                    % (p.name, age))
                log("[media-lock] 并发会写坏 video_jobs.json / 拼出半截成片。"
                    "若确认是残留锁，删除 %s 或调小 SHORTDRAMA_MEDIA_LOCK_TTL。"
                    % p)
                return False
            log("[media-lock] 陈旧锁（%.0fs 无心跳）→ 接管：%s" % (age, p))
            _release_lock(p, log=log)
            if p.exists():
                # 删除被沙箱拦下（批量删除保护）→ **就地接管**。
                # 原属主已由 mtime 心跳确认死亡，覆写不会与活进程冲突。
                # 这里放弃 `O_EXCL` 的原子性，是为了不出现"锁删不掉 → 媒体链永久起不来"
                # —— 那种僵局比极窄的接管竞态更糟。
                try:
                    with open(p, "w", encoding="utf-8") as f:
                        json.dump({"pid": os.getpid(), "at": time.time()}, f)
                    return True
                except BaseException:  # noqa: BLE001
                    log("[media-lock] ⛔ 陈旧锁无法接管：%s" % p)
                    return False
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"pid": os.getpid(), "at": time.time()}, f)
        return True
    return False


@contextlib.contextmanager
def media_lock(project_root: Path, ep: int = 1, log=print):
    """媒体链独占锁：yield `True`（持锁）/ `False`（被拒）。`SHORTDRAMA_MEDIA_LOCK=0` 可关。

    在**全链路**与**单镜重渲**上同样生效 —— 否则两者可以互相踩。
    """
    if not config.MEDIA_LOCK:
        yield True
        return
    p = _lock_path(project_root, ep)
    if not _acquire_lock(p, log=log):
        yield False
        return
    stop = threading.Event()

    def _beat():
        while not stop.wait(_LOCK_HEARTBEAT_S):
            try:
                p.touch()
            except BaseException:  # noqa: BLE001
                # 心跳线程**绝不能死**：它一停，锁就会被判成陈旧、被别的进程接管
                # → 两个媒体链同时写 `video_jobs.json`（那正是这把锁要防的事）。
                pass

    threading.Thread(target=_beat, name="media-lock-heartbeat", daemon=True).start()
    try:
        yield True
    finally:
        stop.set()
        # 释放必须**尽力而为但不留隐患**：沙箱批量删除保护拦下 `unlink()` 时抛的是
        # **非 `Exception` 的子类** → `except Exception` 抓不住 → 异常会从 `finally`
        # 冒到顶层 → **已经出片**的那一轮在 `print("RESULT:")` 之前结束 →
        # 编排层把实质成功判成失败（paper-crane、lost-and-found 各一次）。
        # `_release_lock` 既吞掉这类异常（不崩进程），又在删不掉时把 mtime 归零
        # （否则下次运行会在 TTL 内被这把刚跑完的锁拦下 —— 2026-09-14 实测）。
        _release_lock(p, log=log)


def run(project_root: Path, ep: int = 1, log=print, max_regen: int = 2,
        stills_only: bool = False, only: list[str] | None = None,
        from_still: bool = False) -> dict:
    """媒体链入口：**先取独占锁，再进 `_run_guarded`**（守卫与记账都在那里）。

    `only` / `from_still`（2026-09-13，单镜重渲；spec §3.1）：
        把"要动的镜"收窄到 `only` 里的镜号，其余镜**复用盘上产物**。
        **这是同一入口的受限调用，不是第二个入口** —— gate / 记账 / 幂等 /
        限流全部照常执行。2026-09-10 的 `--resume-media` 血案正因为它是
        "第二个入口"：绕过了 `media_gate` 与 `media_loop.rendered` 记账 →
        重复渲染烧配额（`_run_guarded` 的 docstring 有完整记录）。
        · `only` 里的镜会**先作废**（clip 移入 `clips/.clipqc_bad/` + 清 job 状态）
          —— 视频阶段的幂等判据是 `jobs.done()`，不作废的话重渲请求会被
          自己的幂等逻辑安静跳过（"看起来成功了，其实什么都没干"）。
        · 重渲没回来的镜会把旧 clip **放回原位**并记入 `residual`：
          宁要"有瑕疵但完整"的成片，不要"永远缺镜"（`clipqc.invalidate` 事故）。
        · `from_still=True` 连静帧一起重画（静帧层的问题在视频层修不掉）。
    """
    with media_lock(project_root, ep=ep, log=log) as got:
        if not got:
            return {"status": "blocked",
                    "reason": "同一 (项目, 集) 已有媒体链在运行——并发会写坏 "
                              "video_jobs.json 或拼接出半截成片",
                    "gate": "media-lock"}
        return _run_guarded(project_root, ep=ep, log=log, max_regen=max_regen,
                            stills_only=stills_only, only=only,
                            from_still=from_still)


def _known_shots(project_root: Path, ep: int = 1) -> list[str]:
    """**本集**镜号（分镜解析失败/缺失时返回空表 → 调用方不硬拦）。

    ★★ `ep` 必须传（2026-09-19 实测的跨集 bug）：不传时
    `resolve_path(..., ep=None)` 会回落 `manifest.episode_index` =
    **最近一次跑过的那一集** ⇒ 校验/渲染会**读错集**。
    实测：跑完第 2 集后在第 1 集页点「批量生成图片」，流水线打印「分镜 15 镜」
    而请求的是第 1 集的 19 镜 ⇒ 整轮渲染作废（30 秒 + 一轮资产图配额）。
    """
    from ..guards import resolve_path          # 局部 import：避免 guards ↔ media 的循环依赖
    p = resolve_path(project_root, "scenedesigner", int(ep or 1))   # 新名优先、旧名回退（M1 集级路径）
    if not p.exists():
        return []
    try:
        return [s["name"] for s in storyboard.parse(p.read_text(encoding="utf-8"))]
    except Exception:  # noqa: BLE001
        return []


def rerender(project_root: Path, names, *, note: str = "", from_still: bool = False,
             ep: int = 1, log=print) -> dict:
    """单镜重渲（对话式迭代）—— `run(only=[...])` 的薄封装 + **镜号校验**。

    两个入口都落到这里，落点都是 `run(only=[...])`：
      · CLI：`python -m v5.series <项目> --rerender LN03 [--from still]`
      · 对话：Studio 里跟 director 说"LN03 重做" → 派发 `media_rerender` 子代理
        （第二步，见 spec §3.3）
    **不新开渲染入口** —— gate / 记账 / 幂等 / 限流全在 `run` 里。
    """
    names = [str(n).strip() for n in (names or []) if str(n).strip()]
    if not names:
        return {"status": "failed", "reason": "未指定镜号（用法：--rerender LN03）"}
    known = _known_shots(project_root, ep)
    if known:
        # 风险 #6（spec §3.4）：director/人可能臆造镜号（"重渲 LN99"）——
        # 必须在**烧配额之前**拦下，而不是提交一个不存在的镜再报"无静帧"。
        unknown = [n for n in names if n not in known]
        if unknown:
            log("[rerender] ⛔ 镜号不在分镜里：%s（本片镜号：%s）"
                % (",".join(unknown), ",".join(known[:10])))
            return {"status": "failed",
                    "reason": "unknown shots: " + ",".join(unknown),
                    "known": known[:20]}
    else:
        log("[rerender] ⚠️ 无分镜文件，镜号无法校验（仍按给定镜号尝试）")
    if note:
        log("[rerender] 理由：%s" % note)
    # 提示（**不阻断**）：盘上一镜都没有时，重渲只会渲出目标镜而**拼不成成片**
    # （`missing_clips` 非空 → incomplete）。那不是"渲染入口"，但我们不拦——
    # "先只渲一镜看看"是合法的调试用法，且已渲好的镜会留在 jobs 里不浪费。
    if not list((project_root / "media" / ("ep" + str(ep)) / "clips").glob("LN*.mp4")):
        log("[rerender] ⚠️ 盘上还没有任何 clip —— 单镜重渲**不是**整片渲染入口；"
            "若想整片出片请跑媒体链（--resume-media）。本次仍会尝试，但缺镜时不拼接。")
    log("[rerender] 单镜重渲 %s（%s）"
        % (",".join(names),
           "连静帧一起重做" if from_still else "只重渲视频、复用盘上静帧"))
    return run(project_root, ep=ep, log=log, only=names, from_still=from_still)


def _run_guarded(project_root: Path, ep: int = 1, log=print, max_regen: int = 2,
                 stills_only: bool = False, only: list[str] | None = None,
                 from_still: bool = False) -> dict:
    """**媒体链唯一入口的实现体**：守卫与记账都收在这一层。

    为什么必须唯一（2026-09-10 实测漏洞）：
        `guards.media_gate("render")` 与 `media_loop.rendered` 记账原本写在
        `graph.make_media_node` 里。于是**第二条入口**——`--resume-media`
        （`series.run` 直接调 pipeline）——把两者**整个绕过**：
          · 8 角色没跑完 + 评审没通过，照样能渲染；
          · `media_loop.rendered` 永远不写，`media_gate` 的"已渲染就别重渲"
            闸门因此形同虚设 → 重复渲染烧配额。
        审批门（`stills`）也是同样的病：当时写在 `series.run` 里，跑全链路时被绕过。

    放在最窄处（这里）而不是"两个调用点各写一遍"：不是"记得同步"，
    而是**没有第二个地方可写**——任何调用方都绕不过。

    `stills_only=True` 只出图，不烧视频配额，故**不受 render 门约束**
    （静帧阶段本就用于"先验收风格再决定是否烧配额"）。

    `only` / `from_still`（2026-09-13）：见 `run` 的 docstring —— 单镜重渲的
    **作用域**只收窄"要动的事"（重画静帧 / 静帧 QC / 成片复核），
    **不收窄"要看的盘"**：视频阶段仍按全片走（非目标镜一律不提交，
    但盘上已有的 clip 会被**按磁盘事实**补进 `ok`），于是 keyframe 回退档
    仍能取到"上一镜真实尾帧"做承接，拼接也仍是完整成片。
    """
    from ..guards import (load_manifest, media_gate, media_loop_set,
                          reconcile_manifest, save_manifest)

    # ── 单镜重渲的前置动作：把目标镜**作废**（clip 移入暂存区 + 清 job 状态）──
    # 放在门**之前**：作废不烧配额（只是把"人已看过的那一镜"标成待重做），
    # 而门可能拒 —— 拒了就必须**回滚**（见 `_finish`），否则 gate 拒了、
    # clip 却已从 clips/ 消失，成片反而缺镜。
    stashed = {}
    if only and not stills_only:
        stashed = clipqc.invalidate(project_root, list(only), ep=ep, log=log)

    def _finish(res: dict) -> dict:
        """统一出口：把暂存区落地 —— 渲回来的丢弃，**没回来的放回原位**。

        这是"宁要有瑕疵但完整"的落地点（`clipqc.invalidate` 的事故记录）：
        重渲可能因为 503/429/被回收而不回来，那时必须把旧 clip 放回，
        并如实记入 `residual`。
        """
        if not stashed:
            return res
        got = set(res.get("clips_done") or [])
        back = {n: p for n, p in stashed.items() if n not in got}
        if back:
            restored = clipqc.restore(project_root, back, ep=ep, log=log)
            res["residual"] = sorted(set(res.get("residual") or []) | set(restored))
            res["restored"] = sorted(restored)
        clipqc.discard(project_root,
                       {n: p for n, p in stashed.items() if n in got}, log=log)
        return res

    if not stills_only:
        m = load_manifest(project_root)
        # **媒体门前先按磁盘事实对账一次**（物化对账，2026-09-12 实测补上）：
        # `phases` 原本只靠角色节点里的后置记账写入，实测出现过「7 个产物全在盘、
        # phases 却空」→ 媒体门拦住媒体链且查不出原因。与其依赖脆弱的后置钩子，
        # 不如在这里按磁盘事实裁决（本项目一贯原则：**验收以磁盘事实为准**）。
        # 必须在 media_gate **之前**，否则门读到的是过期的 phases。
        # ★ M2：按**本集**对账（per-ep 的产物路径 + per-ep 的名册）。
        m = reconcile_manifest(project_root, m, ep=ep)
        # **把"已渲染"从单向闩锁变成版本感知闸门**（2026-09-10）。
        # `media_gate` 里那条 "已渲染且无待修订 → 无需重渲" 依赖
        # `media_loop.pending_revision`，但全仓库**没有任何地方写它**——
        # 于是 `rendered=True` 一旦写下就是永久闩锁：改了静帧也渲不动。
        # 这个缺陷原本被"resume 路径不记账"掩盖着（maskparade 的 media_loop
        # 一直是 None），把记账搬到唯一入口后才暴露。
        # 修法：用与审批门同一套**产物指纹**——输入变了就视作有修订待渲。
        #
        # ★ M2：`media_loop` 改为**按集**子表（`media_loop_set`）。一维时第 1 集渲过
        #   就会让第 2 集撞上「已渲染且无待修订」——虽然指纹不同会歪打正着地放行，
        #   但那依赖巧合（`rendered` 的语义已经变成"某一集渲过"），必须改正。
        ml = media_loop_set(m, ep)
        fp_now = approvals.fingerprint(project_root, "media", ep)
        if only:
            # **单镜重渲 = 一次显式修订请求**：`media_gate` 那条
            # 「已渲染且无待修订，无需重渲」正是靠这个标记放行（成片出过一次后
            # `rendered=True`，不置标记的话重渲会被自己的门挡住）。
            # 必须**落盘**：进程若在中途被回收，下一次重渲会因为"输入指纹没变"
            # 再次被拦 —— 而这一版的修订请求本来就还没消化。
            ml["pending_revision"] = True
            save_manifest(project_root, m)
        elif ml.get("rendered") and ml.get("input_fingerprint") != fp_now:
            ml["pending_revision"] = True
        ok, why = media_gate("render", m, ep=ep)
        if not ok:
            log("[media-block] " + why)
            return _finish({"status": "blocked", "reason": why, "gate": "render"})
        # ★ 2026-09-19：人工模式下评审门不拦 —— **必须说出来**。
        #   放行一条机器判定为"不合格"的输入，是本项目最忌的"静默"：
        #   日志里若只留一句"开始渲染"，事后没人能看出"这一版是带着未通过的评审渲的"。
        _rev = m.get("review") or {}
        if config.HUMAN_IN_CHARGE and not (_rev.get("passed")
                                           or _rev.get("force_passed")):
            log("[media-gate] ⚠️ 人工模式：**评审门不拦**（reviewer 未 pass）——"
                " 放行渲染，责任在人。reviewer 报告仅作参考；"
                "不满意请自行点重跑（整片 `--resume-media` / 单镜重渲）。")
        # 静帧验收审批（默认关）：未批准就不许烧视频配额。
        if config.REQUIRE_APPROVAL:
            a_ok, a_why = approvals.check(project_root, "stills", ep=ep)
            if not a_ok:
                log("[approval-block] stills 门未过：" + a_why)
                return _finish({"status": "blocked", "reason": a_why,
                                "gate": "stills"})
            log("[approvals] stills 门通过：%s" % a_why)

    r = _run_impl(project_root, ep=ep, log=log, max_regen=max_regen,
                  stills_only=stills_only, only=only, from_still=from_still)

    if not stills_only:
        # 记账与闸门是**一对**：`media_gate` 靠 `rendered` 判断"已渲染无需重渲"。
        # 两者分居会出"渲染了但闸门说没渲染"→ 重复烧配额，故必须同处落点。
        # 同时记下**输入指纹**，供下次判断"这一版输入是否已渲过"。
        m = load_manifest(project_root)
        ml = media_loop_set(m, ep)
        ml["rendered"] = r.get("status") == "ok"
        if ml["rendered"]:
            ml["input_fingerprint"] = approvals.fingerprint(project_root, "media", ep)
            ml.pop("pending_revision", None)   # 渲成了 → 修订已消化
        save_manifest(project_root, m)
    return _finish(r)


# 说明（2026-09-13 收敛）：这里原本有个模块级 `_parallel_ok(still_chain,
# tail_pregen, n_tails, n_need, video_mode)`，用来判断"能否平铺提交"。
# 它已**并入 `media/video_plan.VideoPlan.of(...)` 的 `can_submit_flat` 字段** ——
# 因为"能不能平铺"本质上是"模式决策"的一部分（reference 下各镜无依赖，恒可平铺；
# keyframe 下取决于承接依赖是否解除），与"用什么图 / 要不要抽尾帧"是同一件事的几面。
# 分散在两处判断正是 2026-09-13 漏改的根因，故收敛到 `video_plan.py` 一处。


def _run_impl(project_root: Path, ep: int = 1, log=print, max_regen: int = 2,
              stills_only: bool = False, only: list[str] | None = None,
              from_still: bool = False) -> dict:
    from ..guards import resolve_path          # 局部 import：避免 guards ↔ media 的循环依赖
    # M1：分镜是**集级**产物（`scenedesigner_ep{N}.md`）；读取走 resolve_path（旧名回退）
    # ★★ `ep` 必须显式传（2026-09-19 实测）：不传会回落 `manifest.episode_index`
    #    （= 最近跑过的集）⇒ **渲染拿错集的分镜**。实测 ep=1 的请求读到了 ep2 的分镜。
    sb_path = resolve_path(project_root, "scenedesigner", int(ep or 1))
    if not sb_path.exists():
        return {"status": "failed", "reason": "no storyboard", "clips_done": []}
    shots = storyboard.parse(sb_path.read_text(encoding="utf-8"))
    if not shots:
        return {"status": "failed", "reason": "storyboard parsed 0 shots",
                "clips_done": []}
    n_parsed = len(shots)
    shots = shots[: config.VIDEO_MAX_SHOTS]
    if len(shots) < n_parsed:
        # ★ **绝不静默截断**（2026-09-13）。原写法是"先切片、再报数"——
        # 日志打印的是**截断后**的镜数，看起来像"这片本来就这么长"。
        # 实测后果：一部 127 镜的 15 分钟片会被砍成 40 镜 ≈ 4.7 分钟，
        # 而人要跑完全部媒体链（小时级）才发现片长不对，且**无从知道是被砍的**。
        # 截断本身可以存在（供应商/预算约束），但必须**说出来**。
        log("[media] ⚠️ 分镜 %d 镜 > 上限 %d → **已截断**，本片只渲前 %d 镜"
            "（约 %d 秒）。要跑长片请调 AGNES_VIDEO_MAX_SHOTS。"
            % (n_parsed, config.VIDEO_MAX_SHOTS, len(shots),
               sum(int(s.get("seconds") or 0) for s in shots)))
    log("[media] 分镜 %d 镜%s" % (len(shots),
                                "" if len(shots) == n_parsed else "（截断后）"))

    # ★ 产地记录（2026-09-18）：**开工就把"本轮用哪个厂商"说出来**。
    #
    # 为什么必须打印：用户要在不同厂商之间试效果、**自己对比**。若不打印，
    # 改一次 `.env` 之后**无法从日志判断这一轮到底跑的是谁** ——
    # 最坏情况是"以为在跑新厂商、其实一直在用 agnes"（本项目的「失败不可见」）。
    # 逐镜落盘记录见 `jobs.mark`（video_vendor）/ `stills`（image_vendor）。
    log("[media] %s" % vendors.describe())

    # ★ **单镜重渲的作用域**（2026-09-13，spec §3.1）：`only` 只收窄"要动的事"
    #   （重画静帧 / 静帧 QC / 成片复核），**不收窄"要看的盘"** ——
    #   视频阶段仍按全片走（非目标镜一律不提交，靠 `only` 过滤），
    #   这样 keyframe 回退档还能取到"上一镜真实尾帧"做承接，拼接也是完整成片。
    #
    # ⚠️ **`scope` 必须在下方「逐镜注入」全部做完之后再取**（2026-09-16 修，实测事故）。
    #   原先这里就是 `scope = list(shots)`，而注入（`_names` / `_style_block` /
    #   `_identity_line` / `_scene_line` / `_cast_n`）**全是重新绑定 `shots` 到新 dict**
    #   → `scope` 长期指向**未注入的旧对象**，而首轮静帧用的正是 `scope`
    #   （见下方 `stills.ensure(project_root, scope, …)`）→ **首轮每一镜都丢注入**：
    #     · 丢 `_style_block` → **类型包审美完全不生效**（退回模型默认审美）；
    #     · 丢 `_scene_line` → 场景锚点消失，光源只能靠分镜「视觉风格」列自由发挥；
    #     · 丢 `_identity_line` → 角色身份文字锚点消失；
    #     · 丢 `_cast_n` / `_names` → 多人镜被注「画面中只有一个人物」，
    #       模型把主体复制/重影（正是 QC 报的「主体被复制」硬伤）。
    #   ⚠️ 只有 **QC 重画路径**用的是注入后的 `shots`（`sub = [s for s in shots …]`）
    #   → 于是「被重画的镜是对的、没被重画的镜是错的」，现象极具误导性。
    #   为什么长期没被发现：默认包 `shortdrama` 是写实真人审美 ≈ 模型默认审美，
    #   且分镜多为单人镜 → 丢了也看不出来。**换非默认审美的包才引爆**
    #   （2026-09-16 wool-felt-story-short 实测：首轮 3 镜丢风格块 → 出写实照片级；
    #   同批 6 镜因人数声明丢失被判「主体被复制」硬伤重画，重画后才拿到正确提示词）。

    # 0) 角色姓名：供提示词层判断"本镜是否有人物出场"（决定是否加单人声明）。
    #    分镜写"林宇"而非"主角"时，没有名字表就判成空镜 → 声明漏加。
    char_names: list[str] = []
    try:
        wb_p = project_root / "worldbuilder" / "worldbuilder.md"
        if wb_p.exists():
            char_names = [c["name"] for c in
                          cast.parse_characters(wb_p.read_text(encoding="utf-8"))]
    except Exception as e:  # noqa: BLE001
        log("[media] 角色名解析失败：%s" % str(e)[:80])
    if char_names:
        shots = [{**s, "_names": char_names} for s in shots]
    else:
        # ★ **拿不到角色名必须说出来**（2026-09-14 实测）：`prompt._has_person()` 靠
        #   `_names` 判"本镜有真人"，漏注入时**25 镜里 24 镜**被判"无人" →
        #   全部改注「空镜：画面内容为场景与道具本身，环境静物」声明，
        #   而这与正文里的 `@周平` 直接矛盾（一边说"@周平 的手指"，一边说"环境静物"）
        #   → 构图与人数**系统性跑偏**（我自己的 A/B 脚本就踩了这个：
        #   三个变体都带错误的空镜声明，直到核对 `_has_person` 才发现）。
        #   原先这里是**静默**的（只有抛异常才打日志，"解析出 0 个角色"不打）。
        log("[media] ⚠️ 拿不到角色名（worldbuilder.md %s）→ 本片所有镜都会按"
            "「无人物」处理（不注入「只有一个人物」声明、改注入「空镜/环境静物」）。"
            "若本片确实有人物出场，这会让构图与人数系统性跑偏 —— 请检查 worldbuilder.md。"
            % ("文件缺失" if not (project_root / "worldbuilder" / "worldbuilder.md").exists()
               else "解析出 0 个角色"))
        log("[media]   诊断提示：**有 `# 角色卡：<名>` 标题却解析出 0 个角色 = 格式漂移**"
            "（2026-09-14 实测：牛来包把外貌段写成「外形与材质（出图提示词口径）：」且正文同行，"
            "而 `cast.parse_characters` 当时只认「外貌特征」→ 两个角色全丢 → 全片注入「空镜」"
            "→ 实测 5/26 镜退回写实照片级）。先目视确认角色卡里外貌段的**措辞**，"
            "再决定是补进 `cast.parse_characters` 的措辞表还是改包契约。")

    # 1) 类型包风格块：逐镜注入（否则模型退回默认审美：写实照片/精致低模）
    block = style.wrap(style.load(project_root))
    if block:
        shots = [{**s, "_style_block": block} for s in shots]
        log("[media] 风格块 %d 字（pack=%s）"
            % (len(block), style.pack_of(project_root) or "项目 style.md"))
    else:
        log("[media] 无风格块（%s）→ 提示词朴素"
            % (style.diagnose(project_root) or "未配置风格块"))

    # 1) 资产生产：角色三视图 / 道具场景参考图**确定性生成**（不能指望角色自决）。
    #    nightshift-45 事故：角色只写文本 assets.md，图从未生成 → 注册表 0 个资产
    #    → 参考图绑定 0/8 镜、身份锚点也空 → 每镜模型自由发挥，人物一致性归零。
    #    生图是配额敏感操作，故可续跑（已在盘上的图跳过）。
    if config.CAST_ENSURE:
        try:
            cast.ensure(project_root, log=log)
        except Exception as e:  # noqa: BLE001 -- 资产生成失败不得阻断生产
            log("[media] 资产生成异常：%s（退化为无参考图）" % str(e)[:100])
    else:
        log("[media] 跳过资产生成（SHORTDRAMA_CAST_ENSURE=0）")

    # 2) 参考图 / 身份锚点——资产层把角色三视图/道具图解析成 public URL
    #    反质量风格（如牛来）必须关参考图：模型会照抄参考图的渲染质量
    #    把成片拉回"精致低模"。见 style.still_refs_enabled 的实测记录。
    # ★ `ref_names`：逐镜记录"每张参考图是哪个资产"（2026-09-19），供静帧提示词
    #   **逐张点名角色**（官方多图合成结构要求点名角色才保身份）。放在分支外初始化，
    #   两条分支（开/关参考图）都能拿到，避免未定义。
    ref_names: dict = {}
    ref_types: dict = {}   # 2026-09-21：每张参考图的资产类型（character/prop），供点名措辞分流
    if style.still_refs_enabled(project_root):
        try:
            refs_by_shot = assets.bind(project_root, shots, names_out=ref_names,
                                       types_out=ref_types)
            log("[media] 参考图绑定 %d/%d 镜" % (len(refs_by_shot), len(shots)))
            # ★ **cast 之后的资产完整性校验**（2026-09-12 新增）。
            # 为什么必须在这里：资产契约门跑在 cast **之前**（那时注册表必然为空），
            # 只会误报；而全流程**没有任何一步**检查"分镜点名的资产是否真有图"。
            # 现在按磁盘事实查：分镜 @ 引用的资产，注册表要有条目 **且** 图要在盘。
            # 默认只强告警；`SHORTDRAMA_ASSET_GATE=1` 时硬拦。
            _probs = assets.validate_assets(project_root, shots, refs_by_shot)
            if _probs:
                log("[assets] ⚠️ 资产完整性 %d 项问题：" % len(_probs))
                for _p in _probs[:8]:
                    log("[assets]   · %s" % _p)
                if config.ASSET_GATE_STRICT:
                    return {"status": "blocked", "reason": "资产完整性未过",
                            "gate": "assets"}
            else:
                log("[assets] 资产完整性通过（分镜引用的资产均有图）")
        except Exception as e:  # noqa: BLE001 -- 资产层异常不得阻断生产
            log("[media] 资产层异常：%s（退化为无参考图）" % str(e)[:80])
            refs_by_shot = {}
    else:
        refs_by_shot = {}
        log("[media] pack 关闭参考图（still-refs=false）→ 风格完全由风格块锁定")
        # 身份改由文字锚点锁定（否则模型自行编服装/乱加招牌）
        try:
            idl = assets.identity_lines(project_root, shots)
            if idl:
                shots = [{**s, "_identity_line": idl.get(s["name"], "")} for s in shots]
                log("[media] 文本身份锚点 %d/%d 镜" % (len(idl), len(shots)))
        except Exception as e:  # noqa: BLE001
            log("[media] 身份锚点生成异常：%s" % str(e)[:80])

    # 1.5) **场景锚点**（2026-09-14 补的断线）：把资产注册表里的场景描述接进提示词。
    #      放在两条分支**之后**，让"有参考图"与"反质量无参考图"两种模式都生效。
    #      为什么必须有：分镜的「场景」列此前**完全不进提示词**（`build_still_prompt`
    #      无此段；`scene` 唯一使用点 `join_line` 对 cut 镜头直接返回空），于是模型
    #      只能靠逐镜自由写的「视觉风格」列定光线 → 成片场景漂移（实测漂成暖光室内）。
    #      见 `prompt.scene_line` 与 `assets.scene_lines` 的完整记录。
    try:
        # 空结果的原因由 `scene_lines` 自己告警（零命中 / 名字未匹配 / 描述歧义）——
        # 那是"锚点为什么没来"的**唯一**判据来源，这里不重复写第二份判断。
        sl = assets.scene_lines(project_root, shots, log=log)
        if sl:
            shots = [{**s, "_scene_line": sl.get(s["name"], "")} for s in shots]
            log("[media] 场景锚点 %d/%d 镜（来自资产注册表的场景描述）"
                % (len(sl), len(shots)))
        else:
            log("[media] 场景锚点 0 镜 —— 原因见上一条 [scene] 告警")
    except Exception as e:  # noqa: BLE001 -- 锚点生成失败不得阻断生产
        log("[media] 场景锚点生成异常：%s（该片回落裸场景名）" % str(e)[:80])

    # 1.6) **出场角色数**（2026-09-15 补的断线）：注入 `_cast_n`，让 `prompt` 能按
    #      人数声明「单人 / 多人」。此前它只判「有人 / 没人」→ **多人镜也被要求
    #      "画面中只有一个人物"** → 模型把同一人物复制满画布同时满足两边
    #      （village-tractor 实测：LN17 画出 9 张脸、LN21 多台拖拉机、LN23 人物重影）。
    #      与身份锚点**同源**（`cast_counts` 与 `identity_lines` 共用 `_shot_cast_lines`），
    #      所以"说要几个人"与"说谁出场"永远不会打架。
    #      放在两条分支之后 —— 有无参考图的包都要按人数声明。
    try:
        cn = assets.cast_counts(project_root, shots)
        if cn:
            shots = [{**s, "_cast_n": cn.get(s["name"], 0)} for s in shots]
            log("[media] 出场角色数 %d 镜（其中多人镜 %d）"
                % (len(cn), sum(1 for v in cn.values() if v >= 2)))
    except Exception as e:  # noqa: BLE001 -- 计数失败不得阻断生产
        log("[media] 出场角色数统计异常：%s（该片回落布尔人物判据）" % str(e)[:80])

    # ★ 单镜重渲的作用域 —— **必须在上面所有「逐镜注入」做完之后取快照**
    #   （2026-09-16 修；事故记录见本函数上方 ⚠️。注入是重新绑定 `shots`，
    #   早取快照会拿到未注入的旧 dict，首轮静帧就丢风格块/场景锚点/人数声明）。
    scope = list(shots)
    if only:
        have = {s["name"] for s in shots}
        unknown = [n for n in only if n not in have]   # 风险 #6：臆造镜号
        if unknown:
            return {"status": "failed",
                    "reason": "unknown shots: " + ",".join(unknown),
                    "known": sorted(have), "clips_done": []}
        _want = set(only)
        scope = [s for s in shots if s["name"] in _want]
        log("[media] 作用域收窄（单镜重渲）：%s —— 其余 %d 镜复用盘上产物"
            % (",".join(s["name"] for s in scope), len(shots) - len(scope)))

    planned = relations.plan_frames(shots)
    log("[media] 镜间关系: " + relations.explain(planned))

    # 单镜重渲：只对作用域内的镜动手（`force=from_still` 决定要不要重画静帧）。
    # 不传全片的理由：`stills.ensure` 的幂等是"URL 在盘就跳过"，对全片调用虽然
    # 不额外烧配额，但会把"注册表里没有的镜"重新生成一遍 —— 重渲不该扩面。
    st = stills.ensure(project_root, scope, refs_by_shot=refs_by_shot, ep=ep,
                       force=from_still, planned=planned, log=log,
                       ref_names_by_shot=ref_names,
                       ref_types_by_shot=ref_types)
    missing = [s["name"] for s in shots if not (st.get(s["name"]) or {}).get("url")]
    if missing:
        log("[media] 静帧缺失: %s" % ",".join(missing[:6]))

    # 2) 静帧硬伤 QC（审的就是成品首帧）+ 定向重生成
    #
    # 可关（SHORTDRAMA_STILL_QC=0）：18 镜一轮 QC 约 5-6 分钟，而本机环境会在
    # 长任务中途回收进程 → 每轮重启都白跑一遍 QC，视频阶段永远轮不到。
    # 静帧已人工验收（或本轮只关心视频阶段）时关掉，直取视频。
    if not config.STILL_QC:
        log("[media] 跳过静帧 QC（SHORTDRAMA_STILL_QC=0）")
    # 单镜重渲且**没重画静帧** → 跳过静帧 QC。图没变还复审 = 概率翻判
    # （同一张图连审会给出不同结论，实测 LN12 干净/干净/有硬伤），
    # 只会凭空制造重画、空烧生图配额 —— 与"每轮只复审上一轮实际重画过的镜"
    # 是同一条理由（见下方 todo 的说明）。
    skip_still_qc = bool(only) and not from_still
    if skip_still_qc:
        log("[media] 单镜重渲未重画静帧 → 跳过静帧 QC（复审未改动的图只会翻判）")
    # 跨进程累计重画计数：`max_regen` 只管单进程，续跑器每重启一次就重置，
    # 于是静帧会被反复重画（实测 4 轮震荡 9→7→10→9）。与 clip 侧对称设上限。
    still_tally = _load_still_tally(project_root, ep)
    still_residual: list[str] = []
    # 静帧 QC 并发化（2026-09-12）：每镜 2 次多模态 LLM 审定（硬伤 + 景别），
    # 40 镜串行约 32-50 分钟纯等待（实测 48s/次，重检轮更慢 1.5-2.5min/次）。
    # 改 ThreadPoolExecutor 并发（IO 密集，GIL 不影响）→ 同轮次降到分钟级。
    # workers 可调（SHORTDRAMA_QC_WORKERS，默认 5）：并发过高会撞供应商限速，
    # 而 qc.review 的容错会把 429 兜底成"无硬伤"（静默漏检）——故保持中低并发，
    # 且漏检的镜在下一轮复审中仍会被覆盖。
    # 并发默认 3（2026-09-12 实测：5 会撞供应商 429——"You've reached the API rate limit"，
    # 且当时 qc.review 未兜底 API 异常 → 整个 QC 崩掉）。配合下面的退避重试。
    _qc_workers = max(1, int(os.environ.get("SHORTDRAMA_QC_WORKERS", "3")))

    # 风格判据（**pack 级**，2026-09-15）：反质量包声明"风格是硬判据"后，QC 才会把
    # 「画风被拉向写实/精致」判成 P0。见 `qc.STYLE_CRITERION_HINT` 的实测事故
    # （village-tractor LN24 画成写实照片级 + 三个陌生人却全部放行）。
    # 写实包不声明 → 空串 → QC 行为与历史完全一致（风格仍不判）。
    _qc_style = style.style_criterion_spec(project_root)
    if _qc_style:
        log("[media] 风格轴已启用（pack=%s 声明「风格是硬判据」）"
            % (style.pack_of(project_root) or "?"))
    # 风格"**只记不改**"轴（2026-09-16）：给"追求真实质感"的包用的通道 ——
    # 它们不能用上面那条 P0 风格轴（判据方向相反），于是风格漂移长期无人管
    # （felt-frog 写实照片、felt-bach 大特写漂成"精细手办"都没被报）。
    _qc_watch = style.style_watch_spec(project_root)
    if _qc_watch:
        log("[media] 风格观察轴已启用（pack=%s 声明 style-watch）→ 只报 P1、不触发重画"
            % (style.pack_of(project_root) or "?"))
    # 硬伤关键词表（pack 级可覆盖，2026-09-16）：None = 用全局 `qc.HARD_KEYS`。
    # 全局表里的 `五官` / `面部特征` 等**子串**与"要求五官清晰可读"的包天然抵触。
    _qc_keys = style.hard_keys(project_root)
    if _qc_keys is not None:
        log("[media] 硬伤关键词表已按 pack 覆盖：%d 词（全局 %d 词）"
            % (len(_qc_keys), len(qc.HARD_KEYS)))

    _seen_p1: set = set()          # P1 去重（跨镜累积，同一句只打一次）

    # 「画面内文字」政策（2026-09-17，**默认 allow**）。
    # 只禁**烧录型**文字（字幕条 / 元指令文字）；**场景固有文字**（电梯面板读数、
    # 门牌、文件抬头、屏幕界面）属于正常电影语言，**不再判硬伤**。
    # 理由见 `validate.on_screen_text_of` 的注释：防烧录（模型行为）与画面文字
    # （画面内容）本是两件事，混在一起会逼分镜写反物理的描述、又让 QC 反复假警报。
    _qos_text = validate.on_screen_text_of(load_brief(project_root))
    qc.set_text_policy(_qos_text)   # 让 clipqc 等**间接**调用方吃到同一政策
    if _qos_text == "forbid":
        log("[media] 画面内文字政策 = forbid（brief 指定）→ 任何可读文字都判硬伤")

    def _review_one(s: dict):
        """审单镜：返回 (name, 硬伤摘要) 或 None（无硬伤/无静帧）。

        429/网络异常：交给 `call_with_backoff` **指数退避重试**，不把限速
        当成"无硬伤"（那会静默漏检）；退避耗尽才抛出，由上层记录。
        """
        info = st.get(s["name"]) or {}
        if not info.get("path"):
            return None

        def _one_pass():
            # 带上本镜分镜：QC 需要知道"本镜该有什么"，否则会把分镜本意
            # （六格监控画面 / 本体与分身同框）误判成硬伤。
            # 带上风格判据（pack 级）：反质量包下"画风写成写实"也要判 P0。
            # 带上风格观察轴：追求真实质感的包靠它**看见**漂移（只报 P1，不重画）。
            _rep = qc.review(info["path"], shot=s, style_spec=_qc_style,
                             style_watch_spec=_qc_watch,
                             on_screen_text=_qos_text)
            # 构图校验：景别跨档偏离（分镜写全景、实际特写）→ 必须重画，
            # 否则首帧就把视频钉死在错误构图上（用户反馈的"镜头跳切"）。
            _comp = qc.review_shot_type(info["path"], s)
            return _rep, _comp

        def _one_rep():
            """只重采"硬伤"那一路（复采用；构图校验是另一档判据，不重复采）。"""
            return qc.review(info["path"], shot=s, style_spec=_qc_style,
                             style_watch_spec=_qc_watch,
                             on_screen_text=_qos_text)

        rep, comp = call_with_backoff(_one_pass,
                                      attempts=config.QC_RETRY_ATTEMPTS,
                                      base=config.QC_RETRY_BASE_S,
                                      label="静帧QC %s" % s["name"])
        # ① P1 = 一致性/形态警告：**只打出来给人看**，绝不触发重画。
        #    风格观察轴落的正是这一档 —— 那是它的全部设计意图（看得见 ≠ 自动处置）。
        for _i in (rep.get("issues") or []):
            if str(_i.get("level", "")).startswith("P1"):
                _d = str(_i.get("desc") or "")[:120]
                if _d and _d not in _seen_p1:
                    _seen_p1.add(_d)
                    log("[media] %s 警告（P1，不重画）：%s" % (s["name"], _d))
        # 判定收敛到 `qc.is_hard_issue`（两道闸门 + 否定语境过滤）——
        # 原先这里只做 `level.startswith("P0") and 关键词命中`，会把
        # 「面部为正常写实五官」这类**模型自己说没问题**的条目判成硬伤。
        # `keys=_qc_keys`：pack 级关键词表（None = 全局默认）。
        hard_rep = [i for i in (rep.get("issues") or [])
                    if qc.is_hard_issue(i, keys=_qc_keys)]
        # ③ **负面判定复采确认**（2026-09-16）。只对"判负面"的镜复采（正面不复采，
        #    成本可控）；两次都判负面才算硬伤。偏置方向刻意选"宽松"——QC 判据是
        #    概率性的（实测翻判：LN12 干净/干净/有硬伤），而**翻判的成本全落在误报
        #    这一侧**（每次负面判定都要重画一张图 + 下一轮复审）。
        if hard_rep and config.QC_CONFIRM_NEGATIVE:
            try:
                _rep2 = call_with_backoff(_one_rep,
                                          attempts=config.QC_RETRY_ATTEMPTS,
                                          base=config.QC_RETRY_BASE_S,
                                          label="静帧QC %s 复采" % s["name"])
                _hard2 = [i for i in (_rep2.get("issues") or [])
                          if qc.is_hard_issue(i, keys=_qc_keys)]
            except Exception:  # noqa: BLE001 -- 复采失败：保留原判（宁可重画，不静默放行）
                _hard2 = None
            if _hard2 is not None and not _hard2:
                log("[media] %s 负面判定复采后翻判为干净（首次：%s）→ 不重画"
                    % (s["name"], str(hard_rep[0].get("desc") or "")[:60]))
                hard_rep = []
        hard = list(hard_rep)
        if not comp.get("ok"):
            hard.append({"level": "P0",
                         "desc": "景别不符：要求「%s」实际「%s」——%s"
                                 % (s.get("shot_type") or "?", comp.get("actual") or "?",
                                    comp.get("reason") or "")})
        if hard:
            return (s["name"], " ".join(str(i.get("desc")) for i in hard)[:160])
        return None

    from concurrent.futures import ThreadPoolExecutor

    # **只复审"上一轮被重画过"的镜**（2026-09-12 实测改进）。
    #
    # 旧行为：每轮都 `_ex.map(_review_one, shots)` —— `shots` 是全部 40 镜，
    # 含上一轮**根本没被改动**的图。而 QC 判据是概率性的，对同一张未改动的图
    # 连审会翻判：LN12 原图从 14:52 起没变过，却被连审 3 次给出
    # clean / clean / **defect** → 第三次白画一张（tally=1）。
    # 该轮判的 10 镜里有 4 镜（LN12/LN20/LN27/LN36）属此类。
    #
    # 双重危害：
    #   ① 调用量：3 轮 × 40 镜 × 2 次 = 240 次；真正该审的只有 40+19+10 = 69 镜
    #      → 138 次。**约 42% 是复审无改动图**。
    #   ② 更坏：凭空**制造重画**（概率翻判），吃掉重画预算 → 更多镜撞上限、
    #      被迫带硬伤放行。**过度复审反而降低成片质量。**
    #
    # 故第 0 轮审全部；此后每轮只审上一轮实际重画过的镜（`todo` 随重画收窄）。
    #
    # **已知取舍**：收窄后不再复查"已通过且图未变"的镜，理论上会漏掉 A 轮的
    # 假阴性。但旧做法并不能可靠补上这个洞——同一个概率判据对同一张图会翻判
    # （LN12 实证），复查引入的是噪声而非信号，代价是白烧重画配额。取舍后
    # 以"少烧配额 + 消除振荡"为准；真要全量复查，把下面的 `todo` 换回 `shots`
    # 即可（但要知道为什么当初改掉它）。
    # 单镜重渲：只审作用域内的镜（`scope == shots` 时行为与原来完全一致）。
    todo = list(scope)
    # ★ 2026-09-18：**跨轮次跳过「图未变且上次已判干净」的镜**（补渲瘦身）。
    #   见 `_STILL_SEEN_FILE` 的说明。只在正式 QC 轮生效（单镜重渲已单独跳过 QC）。
    _seen = _load_still_seen(project_root, ep)
    _mt: dict[str, float] = {}
    for _s in scope:
        _p = (st.get(_s["name"]) or {}).get("path")
        if _p:
            try:
                _mt[_s["name"]] = Path(_p).stat().st_mtime
            except Exception:  # noqa: BLE001
                pass
    if config.STILL_QC and not skip_still_qc and _mt:
        _skip = sorted(
            n for n, m in _mt.items()
            if (_seen.get(n) or {}).get("clean")
            and abs(float((_seen.get(n) or {}).get("mtime") or -1) - m) < 1e-6)
        if _skip:
            log("[media] 静帧未变且上次已判干净 → 跳过复审 %d 镜：%s"
                % (len(_skip), _skip[:8]))
            todo = [s for s in todo if s["name"] not in set(_skip)]
    # 上一轮各镜的**硬伤类别**（`_defect_kind` 签名）——供「同类连续两轮 → 停止重画」判据。
    prev_kind: dict[str, str] = {}

    for attempt in ([] if (not config.STILL_QC or skip_still_qc)
                    else range(max_regen + 1)):
        if attempt == 0 and config.STILL_QC:
            log("[media] 静帧 QC 并发审片：%d 镜 / %d workers" % (len(todo), _qc_workers))
        elif config.STILL_QC:
            log("[media] 静帧 QC 复审 %d 镜（仅上一轮重画过的）" % len(todo))
        with ThreadPoolExecutor(max_workers=_qc_workers) as _ex:
            bad = [r for r in _ex.map(_review_one, todo) if r]
        # ★ 2026-09-18：记录本轮结论，供**下次跨轮次跳过**（`_STILL_SEEN_FILE`）。
        #   只记录**本轮实际审过**的镜（`todo`）—— 跳过的镜沿用其已有记录。
        _bad_names = {b[0] for b in bad}
        for _s in todo:
            _n = _s["name"]
            if _n in _mt:
                _seen[_n] = {"mtime": _mt[_n], "clean": _n not in _bad_names}
        if _seen:
            _save_still_seen(project_root, ep, _seen)
        if not bad or attempt == max_regen:
            if bad:
                log("[media] 仍有硬伤（达重生成上限）: %s" % [b[0] for b in bad][:6])
                still_residual = sorted(set(still_residual) | {b[0] for b in bad})
            break
        # 本轮各镜的**类别快照**（在过滤之前取，供下一轮判"同类"）。
        round_kind = {b[0]: _defect_kind(b[1]) for b in bad}
        # ② **同类问题连续两轮 → 停止重画**（2026-09-16）。
        #
        # 为什么必须停：硬伤类别只用于**选强化约束**，原实现在任何地方都不判断
        # "上一轮是不是也是这类问题" → 同一镜反复报同一类时仍一直重画到上限。
        # 而类别为 `unclassified` 时**强化约束为空**，只能「按原提示词换种子重画」——
        # 那是概率赌博。实测 felt-bach：LN08「两个相似老头」/ LN16「缺风箱工」
        # 连续两轮都落 unclassified，原样重画到撞上限，问题照旧（最终 6 镜带伤放行，
        # 这两轮**纯白做**：既没修好，又烧了图）。根因在提示词/结构层时，重画解决不了。
        # 类别**变了**仍允许重画（说明上一轮强化起了作用、只是又冒出别的问题）。
        stop_repeat = ([n for n, k in round_kind.items() if prev_kind.get(n) == k]
                       if config.STILL_QC_STOP_REPEAT else [])
        prev_kind = round_kind
        # 累计重画已达上限的镜：**不再重画**，如实记 residual。
        # QC 是概率性的（同一张图连审会给出 1/1/2 处不同结论），"零硬伤"不可达；
        # 没有这个上限就会跨进程无限重画，纯烧生图配额。
        over = [b[0] for b in bad
                if still_tally.get(b[0], 0) >= config.STILL_QC_MAX_REGEN]
        if over:
            log("[media] %d 镜累计重画达上限 %d，保留现有静帧：%s"
                % (len(over), config.STILL_QC_MAX_REGEN, over[:6]))
            still_residual = sorted(set(still_residual) | set(over))
            bad = [b for b in bad if b[0] not in set(over)]
        if stop_repeat:
            log("[media] %d 镜连续两轮报**同一类**问题（原样重画解决不了，不再重画）：%s"
                % (len(stop_repeat), stop_repeat[:6]))
            still_residual = sorted(set(still_residual) | set(stop_repeat))
            bad = [b for b in bad if b[0] not in set(stop_repeat)]
        if not bad:
            break
        log("[media] 静帧硬伤 %d 镜 → 重生成: %s" % (len(bad), [b[0] for b in bad]))
        names = [b[0] for b in bad]
        sub = [s for s in shots if s["name"] in names]
        # 重生成时身份锁定：有参考图就带参考图；反质量风格（still-refs=false）
        # 无参考图，身份由 _identity_line 文字锚点承担（分镜字段已在 sub 里）。
        sub_refs = {n: refs_by_shot[n] for n in names if refs_by_shot.get(n)}
        # 按硬伤类型给定向强化约束（正向描述，不给负面提法）
        # 尾缀档位在循环外算一次（读 pack 配置，别每镜读一次盘）
        anti_text = _anti_text_hard(project_root)
        for s in sub:
            desc = next((b[1] for b in bad if b[0] == s["name"]), "")
            # 类别**只有一处判据**（`_DEFECT_KINDS` / `_defect_kinds`）——原先这段是
            # 内联的一串 `if any(k in desc ...)`，与"同类判断"会各写一份必然漂移。
            kinds = _defect_kinds(desc)
            extra = ""
            if "text" in kinds:
                extra += anti_text
            for _k, _txt in _DEFECT_EXTRA.items():
                if _k in kinds:
                    extra += _txt
            if "framing" in kinds:
                # 景别跑偏：把要求的景别**再说一遍**并给占比量化，比泛泛重试有效
                st_name = str(s.get("shot_type") or "").strip()
                if st_name:
                    extra += ("，本镜必须是%s：%s"
                              % (st_name, _FRAMING_HINT.get(st_name, "严格按要求的取景范围")))
            if not extra:
                # 硬伤不属于任何已知类别（如"缺少关键道具"、"多出人脸"、"血腥"）。
                # 原实现把重生成放在 `if extra:` 里 —— 于是这类镜**根本不重画**，
                # `bad` 永远非空、循环空转到 max_regen，白白烧掉一轮确认，
                # 末了还报"仍有硬伤（达重生成上限）"。现在照常重生成（换种子），
                # 并如实记下未分类硬伤，便于后续补类别。
                log("[media] %s 硬伤未归类，按原提示词重生成：%s"
                    % (s["name"], desc[:80]))
            st = stills.ensure(project_root, [s], refs_by_shot=sub_refs, ep=ep,
                               force=True, extra=extra,
                               planned=[p for p in planned if p.get("name") == s["name"]],
                               log=log, ref_names_by_shot=ref_names,
                               ref_types_by_shot=ref_types)
        # 记满这一轮的重画次数（跨进程持久化——上限依据，见上方 still_tally 注释）
        for b in bad:
            still_tally[b[0]] = still_tally.get(b[0], 0) + 1
        _save_still_tally(project_root, ep, still_tally)
        # 下一轮只复审这一轮**实际重画过**的镜（其余镜像已通过、图未变，复审只会
        # 引入概率翻判；理由见循环入口注释）。`over`（撞上限的镜）已从 bad 剔除，
        # 自然不会进 todo —— 它们已记 residual，不再被复审。
        todo = sub

    # 分段跑片：只出静帧（先验收风格，再决定是否烧视频配额）。
    # 注意必须放在 QC 之后——否则硬伤镜（文字/缺人物）不会被重生成。
    if stills_only:
        ok_still = sum(1 for s in shots if (st.get(s["name"]) or {}).get("url"))
        log("[media] --stills-only 结束：%d/%d 镜有静帧" % (ok_still, len(shots)))
        return {"status": "ok", "stage": "stills_only", "stills": ok_still,
                "expected": len(shots), "missing": missing,
                "still_residual": still_residual, "clips_done": []}

    # 3) 图生视频：串行链式（连续镜首帧 = 上一镜真实尾帧，避免画面重复）
    #    CHAIN=0 时退化为并铺式（各镜用自己的静帧，快但连续镜会重复）
    # 落幅帧预生成：把"连续镜首帧 = 上一镜**真实**尾帧"的串行依赖，换成
    # "上一镜的**预生成**落幅图" → 各镜之间再无依赖，可以整批平铺提交。
    # 只对"会被下一镜承接"的镜生成（cut 镜生成了也没人用，白烧配额）。
    tails: dict = {}
    # mixed 逐镜模式**依赖落幅图**做连续镜的首帧 ⇒ 自动开启预生成（不必手动配 TAIL_PREGEN）
    # pack 档（2026-09-22）不消费落幅图（打包 prompt 以每镜静帧为节拍锚点）→ 显式跳过，
    # 防止 TAIL_PREGEN 开着时白烧生图配额。
    if config.VIDEO_MODE == "pack":
        log("[media] pack 档：跳过落幅帧预生成（打包请求以每镜静帧为节拍锚点）")
    elif config.TAIL_PREGEN or config.VIDEO_MODE == "mixed":
        need = stills.tail_needed(planned)
        if need:
            log("[media] 落幅帧预生成：%d 镜将被下一镜承接 → 预生成落幅图" % len(need))
            tails = stills.ensure_tails(project_root, shots, planned, ep=ep,
                                       refs_by_shot=refs_by_shot, log=log,
                                       ref_names_by_shot=ref_names,
                                       ref_types_by_shot=ref_types)
            log("[media] 落幅帧就绪 %d/%d" % (len(tails), len(need)))
        else:
            log("[media] 落幅帧预生成已开，但本片无连续镜（全 cut）→ 无需生成")
    # 串行依赖是否已解除（2026-09-12 修复判据）：
    #   ① 显式关了链式（CHAIN=0，接受连续镜重复）；或
    #   ② 开了落幅预生成，且**所有需要承接的镜**都已拿到落幅图。
    # 旧判据是 `bool(tails)`——而 TAIL_PREGEN 只给"会被下一镜承接"的镜生成落幅，
    # 于是**全 cut 的片 tails 恒为空 → 永远走串行**（明明没有任何尾帧依赖）。
    # 新判据用 need_tails 对齐：全 cut 时 0 >= 0 成立 → 正确走并铺式。
    need_tails = (stills.tail_needed(planned)
                  if config.VIDEO_MODE != "pack"
                  and (config.TAIL_PREGEN or config.VIDEO_MODE == "mixed") else [])
    # 「视频怎么提交 / 用什么图 / 要不要抽尾帧」的**唯一决策点** —— 见 media/video_plan.py。
    # 这里只问一件事：能不能平铺（提交与等待解耦）。
    vplan = video_plan.VideoPlan.of(n_tails=len(tails), n_need=len(need_tails))
    parallel = vplan.can_submit_flat
    if vplan.mode == "pack":
        # pack 档（2026-09-22）：相邻同场景镜打包 ≤12s reference 请求。
        # 提交与等待解耦（与并铺式同构）：submit_packs 只提交 → poll_all 统一轮询
        # → expand_packs 把组级结果展开成 {镜名: 所在组成片}，下游缺镜判定/
        # 拼接逻辑零改动（每镜都拿得到"本镜成片"）。
        # 跨组接缝风险在提交前由 seam_preview.jpg 静帧并排预检前置（人眼扫）。
        jobs_d = video.submit_packs(project_root, shots, st, planned, ep=ep, log=log,
                                    only=only)
        _raw = video.poll_all(project_root, jobs_d, ep=ep, log=log)
        done = video.expand_packs(project_root, ep, _raw, log=log)
        jobs = len(jobs_d)
    elif parallel:
        jobs_d = video.submit_all(project_root, shots, st, planned, ep=ep, log=log,
                                  tails=tails, only=only)
        done = video.poll_all(project_root, jobs_d, ep=ep, log=log)
        jobs = len(jobs_d)
    else:
        done = video.submit_chain(project_root, shots, st, planned, ep=ep, log=log,
                                  only=only)
        jobs = len(done)
    ok = {k: v for k, v in done.items() if v}
    # ★ **拼接前补渲一轮**（2026-09-14 实测，用户拍板"做 1"）：
    #
    # 实测：`LN03`/`LN24` 首轮被供应商报 `generation failed`，而 `video.py:427-429`
    #   **对该状态零重试**（当终态）→ 24/26 → `compose.concat` 缺镜时**按设计不拼接**
    #   → **整轮无成片**；而补渲时**同样两镜一次就成功** ⇒ **它不是终态，是偶发故障**。
    #   同一晚 4 次撞它（LN03/LN24 废一整轮 + 17 张静帧白重画、音频 A/B/C 实验 B/C 首轮丢失）。
    #
    # 所以这里对**未渲出的镜**再提交一轮（同一提示词、同一首帧），然后才走拼接判定。
    # · **复用既有机制**：`submit_all(only=...)` + `poll_all` —— **不新开入口**，
    #   也不绕过 gate / 记账 / 幂等（`submit_all` 只跳过 `submitted`，`failed` 会重新提交）。
    # · **单镜重渲**（调用方给了 `only`）时**跳过**：那是受限调用，不该扩面到别的镜。
    # · **只补一轮**：`jobs.submitted` 会 `attempts += 1`，一轮足以覆盖偶发故障；
    #   真·持续失败不该被无限重试掩盖（那会变成"永远出不了片也不报警"）。
    if not only:
        _todo = [s["name"] for s in shots if s["name"] not in ok]
        if _todo:
            log("[media] %d 镜未渲出（%s）→ **补渲一轮**"
                "（供应商偶发 `generation failed`；实测补渲一次即成功）"
                % (len(_todo), ",".join(_todo[:8])))
            if vplan.mode == "pack":
                # pack 档补渲：only 传**镜名**，submit_packs 把含目标镜的组整组重渲
                # （打包单位不可拆；组产物作废→重提→轮询→重新展开）。
                _jobs2 = video.submit_packs(project_root, shots, st, planned, ep=ep,
                                            log=log, only=_todo)
                _raw2 = video.poll_all(project_root, _jobs2, ep=ep, log=log)
                _done2 = video.expand_packs(project_root, ep, _raw2, log=log)
            elif parallel:
                _jobs2 = video.submit_all(project_root, shots, st, planned, ep=ep,
                                          log=log, tails=tails, only=_todo)
                _done2 = video.poll_all(project_root, _jobs2, ep=ep, log=log)
            else:
                _done2 = video.submit_chain(project_root, shots, st, planned, ep=ep,
                                            log=log, only=_todo)
            _back = {k: v for k, v in (_done2 or {}).items() if v}
            if _back:
                ok.update(_back)
                log("[media] 补渲成功 %d 镜：%s" % (len(_back), ",".join(sorted(_back))))
            else:
                log("[media] ⚠️ 补渲一轮仍未成功 —— 按缺镜处理（不拼接成片）")
    if only:
        # 非目标镜：**按磁盘事实**补进 `ok`（本项目一贯原则：验收以磁盘事实为准）。
        # 为什么必须补：`only` 让提交层跳过了非目标镜，于是 `done` 里只剩目标镜 ——
        # 不补的话 `missing_clips` 非空 → 不拼接 → **重渲等于白跑**。
        # 也不看 job 状态：`expired`/`failed` 但 clip 明明在盘的镜会遇到（历史遗留），
        # 而重渲本来就不该扩面到它们身上。
        _clip_dir = project_root / "media" / ("ep" + str(ep)) / "clips"
        for s in shots:
            _n = s["name"]
            if _n in ok:
                continue
            _f = _clip_dir / (_n + ".mp4")
            if _f.exists():
                ok[_n] = str(_f)
    if not ok:
        return {"status": "failed", "reason": "no renders", "jobs": jobs,
                "clips_done": []}

    # 3.5) 成片抽帧复核（首帧合规 ≠ 成片合规）+ 单镜重拍
    #      video 模型会自己加东西（nightshift-45 的 42s 帧烧着乱码字幕，静帧却干净）。
    #      不合格镜作废 clip + job 状态 → 重渲该镜（链式下会顺带重算后续承接）。
    #
    #      **必须循环复核（2026-09-10 实测事故）**：此前只审一轮——重拍后不再复核
    #      就拼接，于是重渲的 clip 带着同样的硬伤直接进成片。LN06 重拍后仍烧着
    #      「林宇决定亲自查看 / 影视效果 请勿模仿」字幕，却因"只审一轮"而未被拦下。
    #      自愈环路必须有收敛判定：每轮重拍后再审，直到通过或达 CLIP_QC_ROUNDS。
    requeued: list[str] = []
    residual: list[str] = []
    if config.CLIP_QC and vplan.mode == "pack":
        # pack 档（2026-09-22）：组产物是**多镜合并**的一条视频，逐镜 clipqc 的
        # 抽帧/判据都不适用（它按镜名找分镜）。pack 的质量闸门另有三道：
        # 静帧 QC（上游）→ seam_preview 跨组接缝预检（提交前）→ 组级零拒绝统计。
        # 逐镜复核在这条路径上是"检查比生成贵 10 倍"的纯开销，显式跳过。
        log("[media] pack 档跳过逐镜成片复核（组产物多镜合并，逐镜判据不适用；"
            "接缝风险已由 seam_preview 预检前置）")
    elif config.CLIP_QC:
        # 崩溃恢复：上一次运行若在"暂存了旧 clip 但重渲还没回来"时被单轮上限
        # 杀掉，暂存区里会留着 clip。先全部放回——**这正是原实现永久丢片的路径**。
        #
        # **单镜重渲时跳过**：那条路径的暂存区是本次调用**自己刚刚创建**的
        # （`_run_guarded` → `invalidate`），所有权归 `_run_guarded._finish`
        # （渲回来的丢弃、没回来的放回并记 residual）。这里再"恢复残留"是多余
        # 且有害的：刚渲好的新 clip 会被旧片覆盖，重渲安静地等于没做。
        if not only:
            clipqc.restore_leftovers(project_root, ep=ep, log=log)
        # 累计重拍计数（跨进程轮次）：见 config.CLIP_QC_MAX_REQUEUE 的死锁说明。
        tally = _load_requeue_tally(project_root, ep)
        # 第 0 轮审全部；此后**只审上一轮重拍过的镜**（与静帧 QC 同一理由，
        # 2026-09-12）：clip 的 QC 判据同样是概率性的，重审"未重拍的旧 clip"
        # 会翻判 → 凭空制造重拍、空烧视频配额（视频配额比生图贵得多）。
        #
        # 单镜重渲：只审**目标镜**。否则一次重渲要付全片 clipqc 的代价
        # （实测 6 镜 60 秒的片：视频生成 1.6 分，clipqc 18 分且未完 ——
        #  检查比生成贵 10 倍，而那正是本 spec 要消灭的开销）。
        audit_set = (dict(ok) if not only
                     else {n_: v for n_, v in ok.items() if n_ in set(only)})
        if only:
            log("[media] 成片复核作用域收窄：%s" % ",".join(sorted(audit_set)))
        try:
            for rnd in range(config.CLIP_QC_ROUNDS + 1):
                bad = clipqc.audit(audit_set, shots,
                                   project_root / ".tmp" / "clipqc", log=log,
                                   hard_keys=style.hard_keys(project_root))
                if not bad:
                    break
                names = list(bad)
                # 已达累计上限的镜：**不再作废**，保留 clip 并标 residual。
                # 否则"渲出来→被作废→重渲失败→下轮再作废"会无限空转，
                # 成片永远缺镜。
                over = [n_ for n_ in names
                        if tally.get(n_, 0) >= config.CLIP_QC_MAX_REQUEUE]
                if over:
                    log("[media] %d 镜累计重拍达上限 %d，保留 clip 并记 residual：%s"
                        % (len(over), config.CLIP_QC_MAX_REQUEUE, over[:6]))
                    residual = sorted(set(residual) | set(over))
                    names = [n_ for n_ in names if n_ not in set(over)]
                if not names:
                    break
                if rnd == config.CLIP_QC_ROUNDS:
                    residual = sorted(set(residual) | set(names))
                    log("[media] 复核重拍达上限（%d 轮），仍有硬伤：%s"
                        % (config.CLIP_QC_ROUNDS, names[:6]))
                    break
                # 暂存（非删除）：重渲若没回来，下面会 restore 放回原位。
                stashed = clipqc.invalidate(project_root, names, ep=ep, log=log)
                for n_ in names:
                    tally[n_] = tally.get(n_, 0) + 1
                _save_requeue_tally(project_root, ep, tally)
                log("[media] 单镜重拍（第 %d 轮）：%s" % (rnd + 1, names[:6]))
                # 先摘掉旧路径：重渲若失败，拼接不能再引用已被暂存的 clip
                for n_ in names:
                    ok.pop(n_, None)
                st2 = stills.load(stills.stills_dir(project_root, ep))
                if parallel:
                    jd = video.submit_all(project_root, shots, st2, planned, ep=ep,
                                          log=log, tails=tails, only=only)
                    done2 = video.poll_all(project_root, jd, ep=ep, log=log)
                else:
                    done2 = video.submit_chain(project_root, shots, st2, planned,
                                               ep=ep, log=log, only=only)
                for k, v in done2.items():
                    if v:
                        ok[k] = v
                # 收敛保证：重渲没回来的镜，把暂存的旧 clip 放回并记 residual。
                # 宁要"有瑕疵但完整"的成片，不要"永远缺镜"的空转。
                clip_dir_ = project_root / "media" / ("ep" + str(ep)) / "clips"
                back = {n_: p for n_, p in stashed.items() if n_ not in ok}
                if back:
                    got = clipqc.restore(project_root, back, ep=ep, log=log)
                    for n_ in got:
                        ok[n_] = str(clip_dir_ / (n_ + ".mp4"))
                    residual = sorted(set(residual) | set(got))
                clipqc.discard(project_root,
                               {n_: p for n_, p in stashed.items() if n_ in ok},
                               log=log)
                requeued = sorted(set(requeued) | set(names))
                # 下一轮只审这一轮**真正重渲回来**的镜（其余镜像未变，重审只会
                # 引入概率翻判）。没重渲回来的镜已被 restore + 记 residual。
                audit_set = {n_: ok[n_] for n_ in names
                             if n_ in ok and done2.get(n_)}
        except Exception as e:  # noqa: BLE001 -- 复核失败不得阻断拼接
            log("[media] 成片复核异常：%s（直接拼接）" % str(e)[:100])
    else:
        log("[media] 跳过成片复核（SHORTDRAMA_CLIP_QC=0）")
    if not ok:
        return {"status": "failed", "reason": "no clips after clipqc", "jobs": jobs,
                "clips_done": []}

    # 4) 拼接——**缺镜绝不覆盖已成片**（2026-09-10 事故）
    #    当时 503 队列满致 3 镜渲染失败，`compose.concat` 是按目录 glob 拼接的，
    #    于是 5 镜的残缺片覆盖了完整的 8 镜成片，还返回 status=ok —— 静默丢镜。
    #    宁可保留旧片并如实报 incomplete，也不能让"少几镜"被当成成功。
    out = project_root / "media" / ("ep" + str(ep)) / "episode_final.mp4"
    missing_clips = [s["name"] for s in shots if s["name"] not in ok]
    if missing_clips:
        log("[media] 缺镜 %d 个（未渲出）：%s → 不拼接、不覆盖已成片"
            % (len(missing_clips), missing_clips[:6]))
        return {"status": "incomplete", "shots": len(ok), "expected": len(shots),
                "missing": missing_clips, "requeued": requeued, "residual": residual,
                "still_residual": still_residual, "clips_done": sorted(ok),
                "final": str(out) if out.exists() else ""}
    # 审批门（默认关）：`media` 门放行才拼接出片。
    # 与 `stills` 门的区别：stills 门管"要不要烧视频配额"，media 门管
    # "这一版静帧定稿了没有"——它的指纹同时盯 stills.json 与分镜，
    # 两者任一变动即作废，避免"静帧又改了几轮、却拿旧批文出片"。
    if config.REQUIRE_APPROVAL:
        m_ok, m_why = approvals.check(project_root, "media", ep=ep)
        if not m_ok:
            log("[approval-block] media 门未过：%s → 不拼接" % m_why)
            return {"status": "incomplete", "shots": len(ok), "expected": len(shots),
                    "missing": [], "requeued": requeued, "residual": residual,
                    "still_residual": still_residual, "clips_done": sorted(ok),
                    "blocked_by": "media", "reason": m_why,
                    "final": str(out) if out.exists() else ""}
        log("[approvals] media 门通过：%s" % m_why)

    n = compose.concat(Path(ok[next(iter(ok))]).parent, out)
    # ★ 旁白音轨（2026-09-23，half-narrated-live-action 包）：audio_mode=narration-led
    #   时按真实时间轴生成旁白配音并混入成片（edge-tts + ffmpeg amix）。
    #   失败不挡链（保留原声成片）；audio_mode 从 brief 现读。
    try:
        import json as _json
        _b = _json.loads((project_root / "brief.json").read_text(encoding="utf-8"))
        if str(_b.get("audio_mode") or "") == "narration-led" and out.exists():
            from v5.media import narration
            narration.attach(project_root, ep, out, log=log)
    except Exception as _ne:
        log("[narration] 旁白音轨失败（保留原声成片）：%s" % str(_ne)[:120])
    return {"status": "ok", "shots": len(ok), "expected": len(shots),
            "seconds": round(compose.duration(out), 1) if out.exists() else 0.0,
            "final": str(out), "clips": n, "requeued": requeued,
            "residual": residual, "still_residual": still_residual,
            "clips_done": sorted(ok)}
