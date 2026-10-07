# -*- coding: utf-8 -*-
"""和导演**直接对话** —— 一段被记住的会话，聊清楚了再开工。

## 它补的是什么

在这之前，机器上**没有任何东西在跟 supervisor 说话**：唯一的驱动是
`drive_chain` 那个"点火器" —— 它每次新建一段一次性会话、丢一句开工指令、跑完就丢。
所以人在工作台上说的话无处可去（`v5/inbox.py` 那条信道只送到"下一个被派发的角色"）。

本模块把那段会话**留下来**、并且让人能一句一句地跟导演说：

    聊（`ask` / `poll`） → 满意了 → 开工（`runner.start(kind="chain", chain_thread=<这段>)`）

## 为什么"聊"不会误开工（2026-10-07 实测）

导演的提示词是「你是监制，按依赖序派活」，**没有"只回答不做事"的模式** ——
所以"发一句过去他会不会直接开始写文件"是这条路上的唯一未知项。实测（临时项目
`chatprobe-1007`，跑完即删）：消息带上 `CHAT_PREFIX` 之后，他回了一大段正经回答、
**盘上新增文件 0 个**。所以按住他的办法就是**在消息里说清这是对话阶段**，
⛔ 不去改他的提示词（那是共享的、编译期生效的东西）。

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
import json
import os
import time
from pathlib import Path

DIR_NAME = ".tmp"
FILE = "director_chat.json"

#: 对话阶段的**前缀**。这一行的作用就是"按住他"：见模块文档的实测记录。
#: ⚠️ 措辞别删这两句：① 只回话 ② 等"开工"再做。少任何一句，实测会开始动手。
CHAT_PREFIX = (
    "【对话阶段 · 本轮只回话】\n"
    "现在还不是开工的时候 —— 用户想先跟你把事聊清楚。\n"
    "**本轮请只回答问题**：不要调用任何工具、不要写任何文件、不要派发任何子代理。\n"
    "等用户明确说「开工」时，你才开始落规格、派活。\n\n"
)

#: 单轮最多等多久（秒）。超时不算失败 —— 只是这一轮还没答完，界面继续转。
ASK_TIMEOUT = 600.0

_TERMINAL = ("success", "error", "failed", "timeout", "interrupted")


def _digest(root: Path, ep: int) -> str:
    """盘上现状的**速览**，随每句话喂给导演。

    ★ 为什么必须给他：对话时我们**禁止他调用工具**（见 `CHAT_PREFIX`），
      所以他读不到 `/brief.json`、也读不到产物目录。实测（2026-10-07）：
      不喂的话他会老实说"我还没拿到 brief"，只能给通用套话；喂了才答得具体。
      ⛔ 所以"按住他"和"喂饱他"是配套的两件事，缺一个这条道就不能用。
    只读，不写任何东西。
    """
    from . import guards

    out = ["【开工前必读 · 盘上现状（自动附上，你不必也不能去读文件）】"]
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
    p = path_of(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(str(tmp), str(p))


def turns(root: Path) -> list[dict]:
    return list(_read(root)["turns"])


def _append(root: Path, role: str, text: str, ep: int = 1) -> None:
    obj = _read(root)
    obj["turns"].append({"role": role, "text": text,
                         "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "ep": int(ep)})
    obj["turns"] = obj["turns"][-400:]
    _write(root, obj)


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
    obj["thread_id"] = tid
    obj["seen"] = 0
    _write(root, obj)
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
    st = await c.threads.get_state(tid)
    msgs = ((st or {}).get("values") or {}).get("messages") or []
    obj = _read(root)
    seen = int(obj.get("seen") or 0)
    added = 0
    for m in msgs[seen:]:
        role = str(m.get("type") or m.get("role") or "")
        txt = _text_of(m)
        if not txt:
            continue
        # ★ **只搬他的话，不搬我们发出去的**。人那一侧由 `submit` 自己记（记的是
        #   人真正打的字，不含 `_digest` / `CHAT_PREFIX` 那一大坨）。
        #   第一版两边都记 ⇒ 每条自己的话在界面上出现两次（一次干净、一次带前缀）。
        if "ai" in role or role == "assistant":
            obj["turns"].append({"role": "director", "text": txt, "ep": int(ep),
                                 "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        elif "human" in role or role == "user":
            continue
        else:
            continue
        added += 1
    obj["seen"] = len(msgs)
    obj["turns"] = obj["turns"][-400:]
    _write(root, obj)
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
        # 开场白：新线程的第一句带上"在给哪个项目干活"
        head = "" if not fresh else "【项目】%s · 第 %d 集\n\n" % (Path(root).name, int(ep))
        # ⛔ 每句都附盘上现状：他不能用工具，不喂就只能空谈（见 `_digest` 的说明）
        run = await _client(url).runs.create(
            tid, "supervisor",
            input={"messages": [{"role": "user",
                                 "content": head + _digest(root, ep) + CHAT_PREFIX + body}]})
        obj = _read(root)
        obj["run_id"] = str(run.get("run_id") or "")
        obj["ep"] = int(ep)
        obj["turns"].append({"role": "user", "text": body, "ep": int(ep),
                             "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
        obj["turns"] = obj["turns"][-400:]
        _write(root, obj)
        return {"ok": True, "thread_id": tid, "run_id": obj["run_id"]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}


def poll(root: Path) -> dict:
    """看看答完没有。答完了就把新发言搬进时间线。

    返回 `{busy, status, added, thread_id, turns, error?}`。
    """
    return asyncio.run(_poll(root))


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
        o2 = _read(root)
        o2["run_id"] = ""
        _write(root, o2)
    return {"busy": busy, "status": status, "added": added,
            "turns": _read(root)["turns"], "thread_id": tid}


def thread_id(root: Path) -> str:
    """给开工用：把生产接着聊过的那段对话跑（`runner.start(chain_thread=...)`）。"""
    return str(_read(root).get("thread_id") or "")


def reset(root: Path) -> None:
    """忘掉这段对话（下次从新线程开始）。**不删盘上任何产物。**"""
    obj = _read(root)
    obj["thread_id"] = ""
    obj["run_id"] = ""
    obj["seen"] = 0
    _write(root, obj)
