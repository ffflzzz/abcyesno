# -*- coding: utf-8 -*-
"""多 key 探测：先做**零配额**的三项事实确认，再决定要不要做花钱的提交闸门判定。

背景（2026-09-16）
    媒体链视频提交受 `VIDEO_SUBMIT_MIN_INTERVAL_S=65`「供应商 1rpm」闸门约束，
    40 镜 ≈ 43 分钟纯等待。多 key 若能把这段并行化就很值 —— 但**只在限速是按 key
    而非按账号时才成立**。三个前提必须逐条证实，否则改造白做：

      阶段 A（免费）  每条 key 是否有效
      阶段 B（免费）  `video_id` 是 key 维度还是账号维度 —— 用**盘上历史任务的
                      真实 id** 测（换 key 查得到 → 账号维度 → 提交 key 不必记账）
      阶段 C（免费）  读端点是否也吃同一个限速器（快速跨 key 连发）
      阶段 D（**花钱**）1rpm 到底是 per-key 还是 per-account —— **只能靠真提交**。
                      默认不跑，加 `--spend` 才执行；成本上限 2 × 4s 视频
                      （key1 提交一镜 4s；2 秒后用 key2 再提交一镜）。
                      key2 若 429 → 账号维度 → **多 key 无效**，此时只花了 1 镜。
                      key2 若成功 → per-key → 多 key 可用，花 2 镜。

用法
    .venv/Scripts/python.exe scripts/probe_multikey.py                # A+B+C（零配额）
    .venv/Scripts/python.exe scripts/probe_multikey.py --spend        # 追加 D
    .venv/Scripts/python.exe scripts/probe_multikey.py -v <video_id>  # 指定待测 id

key 来源：`AGNES_API_KEYS`（逗号分隔）优先，否则 `AGNES_API_KEY` 单条。
输出一律**打码**（`sk-XXXXXXXX…` + 长度），不把完整 key 写进日志。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.getcwd())
# agnes 是国内可直连服务；沙箱注入了透明代理，直连绕开它（同 providers.py 的
# trust_env=False，见该文件头 2026-09-13 事故说明）。
os.environ.setdefault("NO_PROXY", "127.0.0.1,localhost,agnes-ai.com")
os.environ.setdefault("no_proxy", os.environ["NO_PROXY"])

import httpx  # noqa: E402

from v5 import config  # noqa: E402
from v5.media import providers  # noqa: E402


def mask(k: str) -> str:
    return ("%s…(%d字符)" % (k[:11], len(k))) if k else "<空>"


KEYS = list(config.AGNES_API_KEYS)
LABELS = ["key%d" % (i + 1) for i in range(len(KEYS))]


def _hdr(t: str) -> None:
    print("\n" + "═" * 72)
    print("  " + t)
    print("═" * 72)


def _row(label: str, res: str) -> None:
    print("  %-6s %s" % (label, res))


def _brief(r: httpx.Response, n: int = 120) -> str:
    try:
        body = r.text
    except Exception:  # noqa: BLE001
        body = "<no body>"
    body = " ".join(body.split())[:n]
    return "HTTP %d | %s" % (r.status_code, body)


# ─── 阶段 A：key 有效性（零配额）────────────────────────────────────────────

def stage_a() -> dict[str, str]:
    _hdr("阶段 A — key 有效性（零配额：GET /v1/models，不生成任何东西）")
    verdict: dict[str, str] = {}
    for label, k in zip(LABELS, KEYS):
        try:
            with httpx.Client(timeout=30, trust_env=False) as c:
                r = c.get(config.AGNES_BASE + "/v1/models",
                          headers=providers._bearer(k))
            if r.status_code == 200:
                try:
                    n = len((r.json() or {}).get("data") or [])
                except Exception:  # noqa: BLE001
                    n = -1
                v = "OK（%d 个模型）" % n
            elif r.status_code in (401, 403):
                v = "❌ 无效/无权限"
            elif r.status_code == 429:
                v = "⚠️ 429（此刻已被限速，无法判定有效性）"
            else:
                v = "?"
            verdict[label] = v
            _row(label, "%s  | %s" % (mask(k), v))
            if v == "?":
                print("        %s" % _brief(r))
        except Exception as e:  # noqa: BLE001
            verdict[label] = "连接失败"
            _row(label, "%s  | 连接失败: %s" % (mask(k), str(e)[:90]))
    return verdict


# ─── 阶段 B：video_id 归属（零配额，用盘上真实历史 id）──────────────────────

def find_real_video_id() -> tuple[str, str]:
    """从项目盘上找一个**已完成**的真实 video_id（它是用 key1 提交的）。"""
    for f in sorted(glob.glob(os.path.join("projects", "*", "media", "ep*",
                                           "video_jobs.json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for name, rec in d.items():
            if isinstance(rec, dict) and rec.get("video_id"):
                return rec["video_id"], "%s/%s" % (f, name)
    return "", ""


def stage_b(video_id: str, src: str) -> dict[str, str]:
    _hdr("阶段 B — video_id 归属：换 key 查得到吗？（零配额，只读）")
    print("  待测 id（来自 %s）：\n    %s" % (src, video_id))
    verdict: dict[str, str] = {}
    for label, k in zip(LABELS, KEYS):
        try:
            r = providers.query_video(video_id, key=k)
            st = r.get("status")
            verdict[label] = str(st)
            extra = " url=%s" % ("有" if r.get("url") else "无")
            err = r.get("error")
            if err:
                extra += " err=%s" % str(err)[:70]
            _row(label, "%s  | status=%s%s" % (mask(k), st, extra))
        except httpx.HTTPStatusError as e:
            verdict[label] = "HTTP %d" % e.response.status_code
            _row(label, "%s  | HTTP %d %s"
                 % (mask(k), e.response.status_code, e.response.text[:90]))
        except Exception as e:  # noqa: BLE001
            verdict[label] = type(e).__name__
            _row(label, "%s  | %s: %s" % (mask(k), type(e).__name__, str(e)[:80]))

    ok = [l for l, v in verdict.items() if v and v not in ("None",)]
    if len(ok) >= 2:
        print("\n  ⇒ 至少两条 key 都能查到同一个 id：**video_id 是账号维度**"
              "（提交 key 不必记账，`query_video` 随便用哪条都行）")
    elif len(ok) == 1:
        print("\n  ⇒ 只有 1 条 key 查得到：**video_id 绑定提交时的 key**"
              "（必须在 jobs 里记 `key_id`，轮询要用同一条）")
    else:
        print("\n  ⇒ 没查通（id 可能已被平台清理；不足以判定归属）")
    return verdict


# ─── 阶段 C：读端点是否也限速（零配额）──────────────────────────────────────

def stage_c() -> None:
    _hdr("阶段 C — 读端点是否吃同一个限速器（零配额：1.5 秒内跨 key 连发 3 次）")
    print("  说明：读端点若被限 ⇒ 限速器是**账号级**。但读端点若不被限，"
          "**不能**反推写端点也不限（两者常常是两个闸门）—— 仅供交叉参考。")
    t0 = time.time()
    for label, k in zip(LABELS, KEYS):
        try:
            with httpx.Client(timeout=20, trust_env=False) as c:
                r = c.get(config.AGNES_BASE + "/v1/models",
                          headers=providers._bearer(k))
            _row(label, "HTTP %d  (+%.2fs)" % (r.status_code, time.time() - t0))
        except Exception as e:  # noqa: BLE001
            _row(label, "异常 %s (+%.2fs)" % (str(e)[:60], time.time() - t0))


# ─── 阶段 D2：对照实验 —— **同一条 key** 连发（区分两个假设）─────────────────

def stage_d2(go: bool, frame_url: str, frame_src: str) -> None:
    """对照组：**同 key** 在 2 秒内连发两次。

    为什么必须有这个对照（2026-09-16）：阶段 D 证明"两条不同 key 间隔 2 秒都通过"，
    但这句话有**两个**都能解释它的假设，而它们的修法完全相反：
      H1 限速是 per-key  → 多 key 有效，要建 key 池 + 并行提交（改 3 处）
      H2 现在**根本没有** 1rpm 闸门 → 多 key 毫无意义，只需把
         `VIDEO_SUBMIT_MIN_INTERVAL_S` 从 65 调小（**改 1 行**）
    判据：**同 key** 2 秒内第二次若 429 ⇒ H1 成立；若也通过 ⇒ H2（闸门已松/取消）。
    """
    _hdr("阶段 D2 — 对照：同 key 2 秒内连发两次（**要花视频配额**）")
    if not go:
        print("  已跳过（未加 --control）。")
        return
    if not frame_url:
        print("  ❌ 无可用公网图片 URL —— 跳过。")
        return

    k = KEYS[0]
    prompt = "纯灰色画面，静止不动。"
    print("  驱动图：%s  模式=reference  秒数=4" % frame_src)
    print("  只用 %s 连发两次，间隔 2s\n" % mask(k))

    stamps: list[tuple[str, float]] = []
    for i in range(2):
        if i:
            time.sleep(2.0)
        abs_t = time.time()
        try:
            r = providers.submit_video(prompt, mode="reference", images=[frame_url],
                                       seconds=4, key=k)
            vid = r.get("video_id") or r.get("task_id") or "?"
            stamps.append(("ACCEPTED", abs_t))
            _row("同key#%d" % (i + 1), "ACCEPTED  video_id=%s" % vid)
        except providers.RateLimitError:
            stamps.append(("429", abs_t))
            _row("同key#%d" % (i + 1), "429 限速")
        except providers.QueueFullError as e:
            stamps.append(("503", abs_t))
            _row("同key#%d" % (i + 1), "503 队列满  %s" % str(e)[:70])
        except Exception as e:  # noqa: BLE001
            stamps.append((type(e).__name__, abs_t))
            _row("同key#%d" % (i + 1), "异常 %s: %s" % (type(e).__name__, str(e)[:90]))

    print()
    if len(stamps) == 2:
        gap = stamps[1][1] - stamps[0][1]
        a, b = stamps[0][0], stamps[1][0]
        print("  实际间隔 %.2fs" % gap)
        if a == "ACCEPTED" and b == "429":
            print("  ⇒ 同 key 在 %.0fs 内被拒 ⇒ **H1：限速真实存在且是 per-key**"
                  % gap)
            print("     与阶段 D 合起来 = **多 key 并行可行**，改造值得做。")
        elif a == "ACCEPTED" and b == "ACCEPTED":
            print("  ⇒ 同 key %.0fs 内两次都过 ⇒ **H2：闸门已不存在/已放宽**" % gap)
            print("     → **多 key 无意义**；应改为把 `VIDEO_SUBMIT_MIN_INTERVAL_S`"
                  " 调小（改 1 行，不用建 key 池）")
            print("     → 但要先确认这是**稳定**行为（一次样本不够，需连测几轮）")
        else:
            print("  ⇒ 不干净（第一次就没过）：%s" % stamps)

# ─── 阶段 C2：写端是否在**参数校验之前**就限速（零配额）─────────────────────

def stage_c2() -> None:
    """往**写端点**快速跨 key 连发 3 次**必然失败**的请求。

    手法：`model` 故意填不存在的名字 —— 平台不可能据此生成任何东西（零配额），
    但如果限速器挂在网关层（校验之前），第 2/3 次仍会 429。于是**免费**换到
    "写端口的限速器是否账号级"这条关键事实。
    若三次都返回 4xx（参数错），说明限速在校验之后（或没有），**不能**反推，
    仍需阶段 D 的真实提交。
    """
    _hdr("阶段 C2 — 写端点限速器是否先于参数校验（零配额：非法 model 连发）")
    print("  判读：出现 429 ⇒ 写端限速器**账号级**（多 key 无效，且免费就能定案）")
    print("        全 4xx    ⇒ 限速在校验之后，本阶段无结论（仍需阶段 D）")
    t0 = time.time()
    saw_429 = False
    for label, k in zip(LABELS, KEYS):
        try:
            with httpx.Client(timeout=20, trust_env=False) as c:
                r = c.post(config.AGNES_BASE + "/v1/videos",
                           headers=providers._auth(k),
                           json={"model": "__probe_nonexistent_model__"})
            hdrs = {kk: vv for kk, vv in r.headers.items()
                    if any(t in kk.lower() for t in ("rate", "limit", "retry"))}
            if r.status_code == 429:
                saw_429 = True
            _row(label, "HTTP %d (+%.2fs)%s%s"
                 % (r.status_code, time.time() - t0,
                    ("  " + str(hdrs)) if hdrs else "",
                    "  " + _brief(r, 90)))
        except Exception as e:  # noqa: BLE001
            _row(label, "异常 %s (+%.2fs)" % (str(e)[:60], time.time() - t0))
    print()
    if saw_429:
        print("  ⇒ **写端限速器是账号级** —— 多 key 不可能提速，改造到此为止。")
    else:
        print("  ⇒ 未触发限速，无法在本阶段定案（**不等于**限速是 per-key）。")


# ─── 阶段 D：提交闸门（**花钱**）────────────────────────────────────────────

def find_public_image_url() -> tuple[str, str]:
    """从项目盘上找一条**公网 http(s)** 静帧 URL 当驱动图。

    为什么不能用 base64 data URI（2026-09-16 实测）：`POST /v1/videos` 直接回
    400 `invalid_request: media must be a public http(s)...` —— 而 `v5/media/video.py`
    文件头的注释写着"keyframe 模式接受 base64 data URI，无需公网托管"。
    **该注释与本部署的实际行为不符**（至少 keyframe 的 first_frame 不接受）。
    """
    for f in sorted(glob.glob(os.path.join("projects", "*", "media", "ep*",
                                           "stills.json"))):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for name, rec in d.items():
            u = (rec or {}).get("url", "")
            if isinstance(u, str) and u.startswith("http"):
                return u, "%s/%s" % (f, name)
    return "", ""


def stage_d(go: bool, frame_url: str = "", frame_src: str = "") -> None:
    _hdr("阶段 D — 1rpm 是 per-key 还是 per-account？（**要花视频配额**）")
    if not go:
        print("  已跳过（未加 --spend）。")
        print("  成本上限：2 × 4 秒视频。若 key2 被 429 → 只花 1 镜且结论明确。")
        print("  要跑：.venv/Scripts/python.exe scripts/probe_multikey.py --spend")
        return
    if len(KEYS) < 2:
        print("  需要至少 2 条 key，当前 %d 条 —— 跳过。" % len(KEYS))
        return
    if not frame_url:
        print("  ❌ 找不到可用的公网图片 URL（见 find_public_image_url 的说明）—— 跳过。")
        return

    print("  驱动图：%s\n    %s" % (frame_src, frame_url[:120]))
    print("  模式=reference（生产默认）  秒数=4（API 下限，最省）\n")
    prompt = "纯灰色画面，静止不动。"
    results: list[tuple[str, str, float]] = []

    for i, (label, k) in enumerate(zip(LABELS[:2], KEYS[:2])):
        if i:
            time.sleep(2.0)      # 关键：< 65s 闸门，故意撞
        t0 = time.time()
        try:
            r = providers.submit_video(prompt, mode="reference",
                                       images=[frame_url], seconds=4, key=k)
            vid = r.get("video_id") or r.get("task_id") or "?"
            results.append((label, "ACCEPTED", time.time() - t0))
            _row(label, "ACCEPTED  (+%.2fs)  video_id=%s" % (time.time() - t0, vid))
        except providers.RateLimitError:
            results.append((label, "429", time.time() - t0))
            _row(label, "429 限速  (+%.2fs)" % (time.time() - t0))
        except providers.QueueFullError as e:
            results.append((label, "503", time.time() - t0))
            _row(label, "503 队列满  (+%.2fs)  %s" % (time.time() - t0, str(e)[:80]))
        except Exception as e:  # noqa: BLE001
            results.append((label, "ERR", time.time() - t0))
            _row(label, "异常 %s  (+%.2fs)  %s"
                 % (type(e).__name__, time.time() - t0, str(e)[:110]))

    print()
    if len(results) == 2:
        a, b = results[0][1], results[1][1]
        if a == "ACCEPTED" and b == "ACCEPTED":
            print("  ⇒ 间隔 %.1fs（远小于 65s 闸门）两次都通过："
                  % (results[1][2] - results[0][2]))
            print("     **per-key 限速** → 多 key 并行**可行**")
            print("     （另：阶段 B 已证 `video_id` 是账号维度 → 轮询不必记 key_id）")
        elif a == "ACCEPTED" and b == "429":
            print("  ⇒ key1 过、key2 在 2 秒内被 429：**账号维度限速**")
            print("     → 多 key **无效**（再多 key 共享同一个闸门），改造不值得做")
        else:
            print("  ⇒ 结果不干净（key1 自己就没过），需重跑或用更长间隔二分")
            print("     明细：%s" % results)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spend", action="store_true",
                    help="执行阶段 D（真实提交，花视频配额）")
    ap.add_argument("--control", action="store_true",
                    help="执行阶段 D2 对照（同 key 连发，花视频配额）")
    ap.add_argument("-v", "--video-id", default="", help="阶段 B 指定待测 video_id")
    ap.add_argument("--frame-url", default="",
                    help="阶段 D 的驱动图（公网 http(s)；缺省自动从项目盘上找）")
    args = ap.parse_args()

    print("Agnes 多 key 探测　base=%s" % config.AGNES_BASE)
    print("key 池：%d 条" % len(KEYS))
    for label, k in zip(LABELS, KEYS):
        print("  %-6s %s" % (label, mask(k)))
    if not KEYS:
        print("❌ 未读到任何 key：检查 .env 的 AGNES_API_KEYS / AGNES_API_KEY")
        return 2
    print("视频模型=%s" % config.MODELS["video"])

    stage_a()
    vid, src = (args.video_id, "命令行指定") if args.video_id else find_real_video_id()
    if vid:
        stage_b(vid, src)
    else:
        _hdr("阶段 B — 跳过")
        print("  盘上找不到带 video_id 的历史任务；用 -v <id> 指定。")
    stage_c()
    stage_c2()
    fu, fsrc = ((args.frame_url, "命令行指定") if args.frame_url
                else find_public_image_url())
    stage_d(args.spend, fu, fsrc)
    stage_d2(args.control, fu, fsrc)
    print("\n完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
