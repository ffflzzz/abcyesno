# -*- coding: utf-8 -*-
"""和导演**直接对话** —— 一段被记住的会话，聊清楚了再开工。

## 它补的是什么

在这之前，机器上**没有任何东西在跟 supervisor 说话**：唯一的驱动是
`drive_chain` 那个"点火器" —— 它每次新建一段一次性会话、丢一句开工指令、跑完就丢。
所以人在工作台上说的话无处可去（`v5/inbox.py` 那条信道只送到"下一个被派发的角色"）。

本模块把那段会话**留下来**、并且让人能一句一句地跟导演说：

    聊（`ask` / `poll`） → 满意了 → 开工（`runner.start(kind="chain", chain_thread=<这段>)`）

## 为什么"聊"不会误开工

导演的提示词是「你是监制，按依赖序派活」，**没有"只回答不做事"的模式** ——
所以"发一句过去他会不会直接开始写文件"是这条路上的唯一未知项。实测（临时项目
`chatprobe-1007`，跑完即删）：消息带上 `CHAT_PREFIX` 之后，他回了一大段正经回答、
**盘上新增文件 0 个**。所以按住他的办法就是**在消息里说清这是对话阶段**，
⛔ 不去改他的提示词（那是共享的、编译期生效的东西）。

⚠️ **2026-10-08 改了一档**：那条律原本连 `brief.json` 都不许写，结果"聊清楚"这件事
**没有出口** —— 用户口述完整条故事、导演两次说"最终版如下"，盘上仍是出厂那份空 brief，
点开工时他读到空 brief 就反问、21 秒零产物收工。现在 `CHAT_PREFIX` 放开**唯一这一个文件**，
"不派发、不写创作产物"照旧。换档后要重新验一次"他只写 brief、不动别的"（见 `v5/tests_director_chat.py`）。

## 三条边界

1. **不写产物、不碰黑板**：本模块只读写自己的一张小状态文件
   （`<项目>/.tmp/director_chat.json`）和 thread。作废/回退一律走 `guards`。
2. **单集单线程**：thread 是**项目级**的（不是集级）—— 换集不换对话，
   因为人在聊的是"这部片子"，集号只影响开工时产物落哪。
3. **读不到就降级**：dev server 没起、thread 被归档（`ensure_devserver` 会归档
   `.langgraph_api`）、SDK 报错 —— 一律返回一句人话，绝不抛出去把工作台弄崩。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import threading
import time
from pathlib import Path

DIR_NAME = ".tmp"
FILE = "director_chat.json"

#: 临时文件名的序号（配合 pid 保证不同写者永不撞名，见 `_write`）
_SEQ = 0
_SEQ_LOCK = threading.Lock()

#: 对话阶段的**前缀**。这一行的作用就是"按住他"：见模块文档的实测记录。
#: ⚠️ 措辞别删这两句：① 只回话 ② 等"开工"再做。少任何一句，实测会开始动手。
#: ★ 2026-10-08 更正：这条从"什么都不许写"改成"**只许写 `brief.json`**"。
#: 实测代价（`paste-1008-2204`）：用户在对话里把整条故事口述完了（题材/主角/冲突/
#: 关键道具/四幕反转/禁忌/结局/片长），导演两次说"brief 最终版如下"却**一个字都落不了盘**
#: —— 因为这条律禁止他调用任何工具。于是点开工时他读到的是出厂那份**空 brief**，
#: 他当场停下来反问"A 还是 B"，`drive_chain` 两轮零产物被反空转闸收工：**21 秒、0 个文件**。
#: 聊天里谈得再好，盘上没有 = 全部作废 —— 开工时生产链读的是 `brief.json`，**不读聊天**。
CHAT_PREFIX = (
    "【对话阶段 · 只回话，外加一件事：把 brief 落盘】\n"
    "现在还不是开工的时候 —— 用户想先跟你把事聊清楚。\n"
    "**本轮不要派发任何子代理**，也不要写任何创作产物"
    "（worldbuilder / 剧本 / 台词 / 分镜 / 评审报告都不是这一步的事）。\n"
    "★ **唯一允许你写的文件是 `/brief.json`**：你们俩谈定的题材、主角、核心冲突、\n"
    "关键道具、`must_have`、`禁忌`、`结局`、片长、`audio_mode`，\n"
    "**必须**用 `write_file` 落到 `/brief.json`（整份覆盖，但**保留**盘上已有的\n"
    "`topic` / `pack` / `episodes` / `ratio`），不要只在聊天里贴一遍 ——\n"
    "开工时生产链读的是盘上那份，读不到这段聊天。\n"
    "还没聊定的字段就**留空**，别替用户编。\n\n"
)

#: 单轮最多等多久（秒）。超时不算失败 —— 只是这一轮还没答完，界面继续转。
ASK_TIMEOUT = 600.0

_TERMINAL = ("success", "error", "failed", "timeout", "interrupted")

#: 哪些话等于「开工」。
#: ★ 2026-10-08 用户原话：「为什么要发送和开工这样机械分开？我和导演聊天，聊好了，
#   我对话让他开工不行吗？」—— 对，那就让他**打字开工**。这比多一个按钮自然。
#: ⛔ 判定**不是**"含没含这几个字"，而是**把开工词抠掉之后还剩不剩话**：
#:   长句里出现「开工」多半是在布置任务（"开工前先把三个空间定下来"），
#:   而真正的开工是**光秃秃一句**（"开工" / "开始吧" / "go"）。
#:   第一版按"长度 ≤12 且含开工词"判，把上面那句布置任务判成了开工。
GO_WORDS = ("开工", "开拍", "开始吧", "开始生产", "开始", "动手吧", "开干",
            "开始干活", "go", "start")
#: 抠掉开工词与标点后，剩下**这么多字以内**才算"就是在说开工"。
GO_LEFTOVER_MAX = 2


def looks_like_go(text: str) -> bool:
    """这句话是不是"让他开工"。纯函数，便于测试。"""
    t = "".join(str(text or "").split()).lower()
    if not t or len(t) > 24:
        return False
    hit = False
    # 长的先抠，免得「开始吧」被「开始」拆成「吧」
    for w in sorted(GO_WORDS, key=len, reverse=True):
        if w in t:
            t = t.replace(w, "")
            hit = True
    if not hit:
        return False
    rest = re.sub(r"[\s,，。.!！?？:：;；、~～…\-—_]+", "", t)
    return len(rest) <= GO_LEFTOVER_MAX


def _live_chain_run(root: Path) -> dict | None:
    """本项目有没有正在跑的创作链（⛔ 不许起第二条）。"""
    try:
        from .media import runner as _runner
        live = [x for x in _runner.list_runs(Path(root).name, 5)
                if str(x.get("kind")) in ("chain", "script")
                and str(x.get("status")) not in _runner.TERMINAL]
        return live[0] if live else None
    except Exception:  # noqa: BLE001
        return None


def _digest(root: Path, ep: int) -> str:
    """盘上现状的**速览**，随每句话喂给导演。

    ★ 为什么必须给他：对话阶段他不派活、也不该自己去翻目录（见 `CHAT_PREFIX`，
      那里只放开 `brief.json` 一个写点）。实测（2026-10-07）：
      不喂的话他会老实说"我还没拿到 brief"，只能给通用套话；喂了才答得具体。
      ⛔ 所以"按住他"和"喂饱他"是配套的两件事，缺一个这条道就不能用。
    只读，不写任何东西。
    """
    from . import guards

    out = ["【开工前必读 · 盘上现状（自动附上，不必自己去读文件）】"]
    try:
        brief = guards.load_brief(root)
        if brief:
            keep = ("topic", "pack", "genre", "episodes", "target_duration", "audio_mode",
                    "protagonist", "second_character", "key_props", "must_have", "禁忌",
                    "tone", "结局", "空间数要求", "visual-style")
            slim = {k: brief[k] for k in keep if k in brief}
            out.append("· brief.json：\n" + json.dumps(slim, ensure_ascii=False, indent=1))
    except Exception as e:  # noqa: BLE001
        out.append("· brief.json 读不到（%s）" % str(e)[:80])
    try:
        roles = list(getattr(guards, "ROLES", ()))
        done, miss = [], []
        for r in roles:
            p = guards.resolve_path(root, r, int(ep))
            if p.exists():
                done.append("%s（%d 字）" % (r, len(p.read_text(encoding="utf-8"))))
            else:
                miss.append(r)
        out.append("· 第 %d 集产物：已落盘 = %s；还没有 = %s"
                   % (ep, "、".join(done) or "（无）", "、".join(miss) or "（无）"))
    except Exception as e:  # noqa: BLE001
        out.append("· 产物清单读不到（%s）" % str(e)[:80])
    try:
        from .media import storyboard as _sb
        md = guards.resolve_path(root, "scenedesigner", int(ep))
        if md.exists():
            shots = _sb.parse(md.read_text(encoding="utf-8"))
            out.append("· 第 %d 集分镜表：%d 镜" % (ep, len(shots)))
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(out) + "\n\n"



def path_of(root: Path) -> Path:
    return Path(root) / DIR_NAME / FILE


def _read(root: Path) -> dict:
    try:
        obj = json.loads(path_of(root).read_text(encoding="utf-8"))
        if isinstance(obj, dict):
            obj.setdefault("thread_id", "")
            obj.setdefault("run_id", "")
            obj.setdefault("seen", 0)
            obj.setdefault("turns", [])
            return obj
    except Exception:  # noqa: BLE001
        pass
    return {"thread_id": "", "run_id": "", "seen": 0, "turns": []}


def _write(root: Path, obj: dict) -> None:
    """原子写。**临时名必须唯一，而且失败要重试**。

    ★ 2026-10-07 实测事故（用户界面上直接弹红字）：
      `[WinError 32] 另一个程序正在使用此文件… director_chat.json.tmp`
      前端每 3 秒轮询一次 `GET .../director/chat`，而每轮都可能 `_pull_new` → 重写
      这张状态文件；FastAPI 的同步处理器跑在**线程池**里，两次轮询会真的并发 ⇒
      两个写者撞在**同一个临时文件名**上。第一版就是 `p.with_suffix(".json.tmp")`。
    两层一起修：① 临时名带 pid+序号（不同写者永不撞名）；
    ② `os.replace` 失败重试（Windows 上文件被读着的一瞬间也会拒绝替换）。
    真正的互斥在 `_Locked` —— 见 `_edit`。
    """
    global _SEQ
    p = path_of(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    with _SEQ_LOCK:
        _SEQ += 1
        seq = _SEQ
    tmp = p.with_name("%s.%d.%d.tmp" % (p.name, os.getpid(), seq))
    data = json.dumps(obj, ensure_ascii=False, indent=1)
    for attempt in range(8):
        try:
            tmp.write_text(data, encoding="utf-8")
            os.replace(str(tmp), str(p))
            return
        except OSError:
            if attempt == 7:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise
            time.sleep(0.05 * (attempt + 1))


class _Locked:
    """跨**线程/进程**互斥（`mkdir`/`O_EXCL` 在 POSIX 与 Windows 上都是原子的）。

    ⚠️ 与 `v5/inbox.py` 的 `_Locked` 是**同一个惯用法**（那边也是 shim 线程池 +
    dev server 两个进程读写同一张文件）。两份都留着：谁也不比谁更"权威"，
    而这一点点重复好过把一个只在这两处用到的锁抽成第三个模块。
    拿不到锁时**降级为不阻塞** —— ⛔ 一条辅助信道不该把工作台卡死。
    """

    def __init__(self, root: Path, timeout: float = 3.0):
        self.root = Path(root)
        self.timeout = timeout
        self.handle = None

    def __enter__(self):
        d = path_of(self.root).parent
        d.mkdir(parents=True, exist_ok=True)
        lock = d / (FILE + ".lock")
        deadline = time.time() + self.timeout
        while True:
            try:
                self.handle = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                return self
            except FileExistsError:
                try:                       # 陈旧锁：持有者崩了没删，超 10 秒就抢
                    if time.time() - lock.stat().st_mtime > 10:
                        lock.unlink()
                        continue
                except OSError:
                    pass
                if time.time() >= deadline:
                    return self
                time.sleep(0.05)
            except OSError:
                return self

    def __exit__(self, *exc):
        if self.handle is not None:
            try:
                os.close(self.handle)
                (path_of(self.root).parent / (FILE + ".lock")).unlink()
            except OSError:
                pass
            self.handle = None
        return False


@contextlib.contextmanager
def _edit(root: Path):
    """加锁读改写的唯一入口。**所有写者都必须走它**，否则并发就丢更新。"""
    with _Locked(root):
        obj = _read(root)
        yield obj
        _write(root, obj)



def turns(root: Path) -> list[dict]:
    return list(_read(root)["turns"])


def _append(root: Path, role: str, text: str, ep: int = 1) -> None:
    with _edit(root) as obj:
        obj["turns"].append({"role": role, "text": text,
                             "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "ep": int(ep)})
        obj["turns"] = obj["turns"][-400:]


# ─────────────────────────────────────────── 线程

def _client(url: str):
    from langgraph_sdk import get_client
    return get_client(url=url)


async def _ensure_thread(root: Path, url: str) -> tuple[str, bool]:
    """返回 (`thread_id`, `是否新建`)。旧线程被归档了就换一条新的。"""
    obj = _read(root)
    tid = str(obj.get("thread_id") or "")
    c = _client(url)
    if tid:
        try:
            await c.threads.get(tid)
            return tid, False
        except Exception:  # noqa: BLE001 —— 归档/删掉了，换一条
            pass
    th = await c.threads.create()
    tid = th["thread_id"]
    with _edit(root) as o:
        o["thread_id"] = tid
        o["seen"] = 0
    return tid, True


def _text_of(m: dict) -> str:
    body = m.get("content")
    if isinstance(body, list):
        body = " ".join(str(b.get("text", "")) if isinstance(b, dict) else str(b) for b in body)
    return str(body or "").strip()


async def _pull_new(root: Path, tid: str, url: str, ep: int) -> int:
    """把 thread 里**还没读过的**人/机发言搬进本地时间线，返回新增条数。

    ★ 这一条同时把**开工过程**接进来了：生产跑在同一段对话上，导演每推进一步
      都会在会话里说话 ⇒ 这里顺手就抓到了，界面那栏因此能看到进度。
    """
    c = _client(url)
    st = await c.threads.get_state(tid)          # ⚠️ await 在锁**外面**做
    msgs = ((st or {}).get("values") or {}).get("messages") or []
    added = 0
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    with _edit(root) as obj:
        seen = int(obj.get("seen") or 0)
        for m in msgs[seen:]:
            role = str(m.get("type") or m.get("role") or "")
            txt = _text_of(m)
            if not txt:
                continue
            # ★ **只搬他的话，不搬我们发出去的**。人那一侧由 `submit` 自己记
            #   （记的是人真正打的字，不含 `_digest` / `CHAT_PREFIX` 那一大坨）。
            #   第一版两边都记 ⇒ 每条自己的话在界面上出现两次（一次干净、一次带前缀）。
            if "ai" in role or role == "assistant":
                obj["turns"].append({"role": "director", "text": txt,
                                     "ep": int(ep), "at": stamp})
                added += 1
            # human / 其它类型一律跳过（`seen` 照样推到底，别把它们卡在队头）
        obj["seen"] = len(msgs)
        obj["turns"] = obj["turns"][-400:]
    return added


async def _status(root: Path, url: str) -> str:
    obj = _read(root)
    tid, rid = str(obj.get("thread_id") or ""), str(obj.get("run_id") or "")
    if not (tid and rid):
        return ""
    try:
        r = await _client(url).runs.get(tid, rid)
        return str((r or {}).get("status") or "")
    except Exception:  # noqa: BLE001
        return ""


# ─────────────────────────────────────────── 对外三件事

def submit(root: Path, text: str, ep: int = 1) -> dict:
    """说一句。**不等回答** —— 界面靠 `poll` 拿结果（和这个应用其它地方一个节奏）。

    返回 `{ok, thread_id, run_id, error?}`。
    """
    body = str(text or "").strip()
    if not body:
        return {"ok": False, "error": "说点什么"}
    return asyncio.run(_submit(root, body, ep))


async def _submit(root: Path, body: str, ep: int) -> dict:
    from . import webchain
    try:
        st = webchain.ensure_devserver(Path(root).name, wait_s=120)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "起不了服务：%s" % str(e)[:160]}
    url = st.get("agent_url") or webchain.agent_url()
    try:
        tid, fresh = await _ensure_thread(root, url)

        # ★★ 打字即开工：这句话等于「开工」就不再当成聊天。
        #    ⛔ 不在这里自己建 run —— 必须走 `runner.start`（= drive_chain），
        #    否则步级确认、打回、反空转那些闭环一个都不在（那是驱动器的活）。
        if looks_like_go(body):
            _append(root, "user", body, ep)
            live = _live_chain_run(root)
            if live:
                return {"ok": False, "started": False,
                        "error": "已经有一条链在跑了（%s）—— 别起第二条"
                                 % str(live.get("run_id"))[-12:]}
            from .media import runner as _runner
            rec = _runner.start(Path(root).name, "chain", ep=int(ep), chain_thread=tid)
            return {"ok": True, "started": True, "thread_id": tid, "run": rec}

        # 开场白：新线程的第一句带上"在给哪个项目干活"
        head = "" if not fresh else "【项目】%s · 第 %d 集\n\n" % (Path(root).name, int(ep))
        # ⛔ 每句都附盘上现状：他不能用工具，不喂就只能空谈（见 `_digest` 的说明）
        run = await _client(url).runs.create(
            tid, "supervisor",
            input={"messages": [{"role": "user",
                                 "content": head + _digest(root, ep) + CHAT_PREFIX + body}]})
        rid = str(run.get("run_id") or "")
        with _edit(root) as o:
            o["run_id"] = rid
            o["ep"] = int(ep)
            o["turns"].append({"role": "user", "text": body, "ep": int(ep),
                               "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
            o["turns"] = o["turns"][-400:]
        return {"ok": True, "thread_id": tid, "run_id": rid}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}


def poll(root: Path) -> dict:
    """看看答完没有。答完了就把新发言搬进时间线。

    返回 `{busy, status, added, thread_id, turns, error?}`。
    """
    return asyncio.run(_poll(root))


def _chain_verdict(root: Path, ep: int) -> None:
    """创作链**收工**后，把它的判决写进这段对话的时间线。

    ★ 为什么必须有它（实测 `paste-1008-2204`，2026-10-08）：点开工 → 界面弹「已开工」
      → 导演回一句反问 → 然后什么都没发生。真相是那一轮 **21 秒、7 个角色零产物**、
      被 `drive_chain` 的反空转闸收工、`status=failed` —— 可这个判决**只写在日志里**，
      对话与画布上都没露过面，用户只能问"点了开工怎么不开工"。
      报失败必须是**响亮**的，不是等人去翻 `.tmp/web-runs/*.log`。
    ⛔ 一个 run 只报一次（按 `run_id` 记账）：前端每 3 秒轮询一次，重复报会刷屏。
    """
    try:
        from .media import runner as _runner
        done = [x for x in _runner.list_runs(Path(root).name, 8)
                if str(x.get("kind")) in ("chain", "script")
                and str(x.get("status")) in _runner.TERMINAL]
        rid = str(done[0].get("run_id") or "") if done else ""
        if not rid:
            return
        with _edit(root) as o:
            if rid == str(o.get("chain_run") or ""):
                return
            o["chain_run"] = rid
            rec = done[0]
            reason = str((rec.get("result") or {}).get("reason") or "").strip()
            o["turns"].append({
                "role": "system", "ep": ep,
                "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "text": ("**这一轮创作链收工：%s**（%s → %s）\n%s"
                         % (str(rec.get("status") or ""),
                            str(rec.get("started_at") or "")[-8:],
                            str(rec.get("ended_at") or "")[-8:],
                            reason or "（记录里没写理由）"))})
            o["turns"] = o["turns"][-400:]
    except Exception:  # noqa: BLE001 —— 报不出判决不能把轮询弄崩
        pass


async def _poll(root: Path) -> dict:
    from . import webchain
    obj = _read(root)
    tid = str(obj.get("thread_id") or "")
    ep = int(obj.get("ep") or 1)
    url = webchain.agent_url()
    if not tid:
        return {"busy": False, "status": "", "added": 0, "turns": obj["turns"]}

    # ① **先把新发言搬进来**，不管有没有 run_id。
    #    ⛔ 第一版只在"自己发起的 run 收工了"时才搬，于是**开工那条永远搬不到**：
    #      `director/start` 走的是 `runner.start`（不经过 `submit`），本模块压根不知道
    #      有 run 在跑；而生产正是跑在同一段对话上 —— 结果就是"角色产物都有了，
    #      对话里却一句话都没多"。开工过程的进度全靠这一步。
    added = 0
    try:
        added = await _pull_new(root, tid, url, ep)
    except Exception as e:  # noqa: BLE001 —— thread 被归档等；下一轮再试
        return {"busy": False, "status": "", "added": 0,
                "turns": _read(root)["turns"], "thread_id": tid, "error": str(e)[:160]}

    # ② busy：本模块发起的这一轮还在跑，**或者**有一轮创作链在跑（开工路径）
    status = await _status(root, url)
    busy = bool(status) and status not in _TERMINAL
    if not busy:
        try:
            from .media import runner as _runner
            live = [x for x in _runner.list_runs(Path(root).name, 5)
                    if str(x.get("kind")) in ("chain", "script")
                    and str(x.get("status")) not in _runner.TERMINAL]
            if live:
                busy, status = True, str(live[0].get("status"))
        except Exception:  # noqa: BLE001
            pass
    if status and status in _TERMINAL:
        with _edit(root) as o2:
            o2["run_id"] = ""

    # ③ 创作链收工 ⇒ 把**它的判决**落进这段对话（见 `_chain_verdict`）
    _chain_verdict(root, ep)

    return {"busy": busy, "status": status, "added": added,
            "turns": _read(root)["turns"], "thread_id": tid}


def thread_id(root: Path) -> str:
    """给开工用：把生产接着聊过的那段对话跑（`runner.start(chain_thread=...)`）。"""
    return str(_read(root).get("thread_id") or "")


def reset(root: Path) -> None:
    """忘掉这段对话（下次从新线程开始）。**不删盘上任何产物。**"""
    with _edit(root) as obj:
        obj["thread_id"] = ""
        obj["run_id"] = ""
        obj["chain_run"] = ""
        obj["seen"] = 0
