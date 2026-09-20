# -*- coding: utf-8 -*-
"""多 key 池 + **per-key 提交闸门**（唯一入口）。

【为什么存在 —— 2026-09-16 实测，全部由 `scripts/probe_multikey.py` 取得】
  · 同一条 key 在 60s 内第二次提交 → **429**（闸门真实存在，不是"已经没有限速"）；
  · **不同** key 间隔 2s 提交 → **全部通过** ⇒ 1rpm 是 **per-key** 而非 per-account；
  · 被拒的 key 等过一个窗口后立即恢复 ⇒ 是**间隔闸门**，不是配额耗尽；
  · 盘上真实 `video_id` 用任意一条 key 都能 `query_video` 到 ⇒ **账号维度**，
    故轮询**不需要**记 `key_id`（`video_jobs.json` 的 schema 因此不用动）。
  ⇒ 「多 key 能把 65s 的提交节流按 key 数并行化」这件事**成立**。

【设计边界（别越界）】
  · **只服务"提交"**（`providers.submit_video`）。查询与下载不限速，不需要领 key。
  · 闸门**按 key 独立计时**，值取 `config.video_submit_interval_per_key()`（实时读，
    不固化 —— 测试会把全局闸门 patch 成 0）。
  · 线程安全：`claim()` 会被多个提交线程并发调用。
  · 池里只有 1 条 key 时，行为**退化为"串行 + 原闸门"**，与加多 key 之前一致 ——
    所以串行与并行两条路径可以共用同一份实现（见 `video._submit_flat_item`）。
"""
from __future__ import annotations

import threading
import time

from .. import config


class KeyPool:
    """按 key 独立配速的令牌池。

    用法：
        pool = KeyPool.of()
        idx, key = pool.claim()          # 阻塞到某条 key 放开，并**立即**记账
        ...providers.submit_video(..., key=key)...
        pool.note_rate_limited(idx)      # 撞 429：把这条 key 的窗口推后

    **`claim()` 返回即已占用**（这是防止两个线程同时选中同一条 key 的关键）；
    调用方必须把 key 用掉，不能领了不用。
    """

    def __init__(self, keys: list[str], interval_s: float, *,
                 monotonic=time.monotonic, sleep=None):
        # 空池**不抛异常**：测试环境与无 .env 的干净 clone 里 `AGNES_API_KEYS` 是空的，
        # 而闸门逻辑与"key 是什么"无关。用一条空串占位，`providers` 侧的 `key=""`
        # 与 `config.AGNES_API_KEY` 为空时的行为一致（历史行为就是发空 Bearer）。
        self._keys = [k for k in keys if k] or [""]
        self._interval = max(0.0, float(interval_s))
        self._monotonic = monotonic
        # **不缓存 `time.sleep`**：必须在调用时查模块属性，否则 `mock.patch.object(
        # video.time, "sleep")` 这类测试打桩会失效（默认参数在 def 期就固化了）。
        self._sleep = sleep
        self._last = [float("-inf")] * len(self._keys)
        self._used = [0] * len(self._keys)
        self._rl = [0] * len(self._keys)
        self._cv = threading.Condition()

    def _sleep_for(self, s: float) -> None:
        (self._sleep or time.sleep)(s)

    # ── 构造 ──
    @classmethod
    def of(cls, keys: list[str] | None = None, interval_s: float | None = None,
           rotate: bool | None = None, **kw) -> "KeyPool":
        """从配置构造。`keys` 缺省 = `config.AGNES_API_KEYS`（已含单 key 回落）。

        `rotate=False` 时**只用第一条 key**（= 改造前的单 key 行为，逐字节等价）；
        `rotate` 缺省读 `config.VIDEO_KEY_ROTATE`。
        `interval_s` 缺省**实时**取 `config.video_submit_interval_per_key()` ——
        不缓存成常量，否则测试把全局闸门 patch 成 0 时这里仍会 sleep。
        """
        if keys is None:
            keys = list(config.AGNES_API_KEYS)
        if rotate is None:
            rotate = config.VIDEO_KEY_ROTATE
        keys = [k for k in keys if k]
        if not rotate:
            keys = keys[:1]          # 单 key：闸门与旧行为一致
        if interval_s is None:
            interval_s = config.video_submit_interval_per_key()
        return cls(list(keys), interval_s, **kw)

    # ── 只读 ──
    @property
    def keys(self) -> list[str]:
        return list(self._keys)

    @property
    def interval_s(self) -> float:
        return self._interval

    def __len__(self) -> int:
        return len(self._keys)

    def label(self, idx: int) -> str:
        """`k2(sk-AbCdEfG…)` —— 日志用。**绝不打印完整 key**（连片段也只取前 11 字符）。"""
        k = self._keys[idx]
        return "k%d(%s…)" % (idx + 1, k[:11]) if k else "k1(<空>)"

    def stats(self) -> str:
        return " ".join("k%d=%d%s" % (i + 1, self._used[i],
                                      ("/429x%d" % self._rl[i]) if self._rl[i] else "")
                        for i in range(len(self._keys)))

    # ── 核心 ──
    def _reserve_locked(self, idx: int) -> tuple[int, str]:
        self._last[idx] = self._monotonic()
        self._used[idx] += 1
        return idx, self._keys[idx]

    def claim(self, warm: bool = False) -> tuple[int, str]:
        """阻塞直到某条 key 的闸门放开，**占用它**并返回 `(idx, key)`。

        选取规则：取"最久没用过"的那条（`min(last)`），等它的窗口放开。
        多条 key 因此会自然轮转，而不是死盯第一条。

        `warm=True` 时立刻返回当前最空闲的 key（**不记账**）—— 给"只想探一下
        供应商状态"的调用方用（探测脚本）；正常提交**必须**用默认值。
        """
        waited = 0.0
        for _ in range(300):
            with self._cv:
                now = self._monotonic()
                idx = min(range(len(self._keys)), key=lambda i: self._last[i])
                gap = self._interval - (now - self._last[idx])
                if warm or gap <= 0:
                    return self._reserve_locked(idx) if not warm else (idx, self._keys[idx])
            # 让出锁再睡（不在持锁状态下阻塞别的线程）
            t0 = self._monotonic()
            self._sleep_for(gap)
            waited += gap
            if self._monotonic() - t0 <= 0:
                # 时钟一点没走 ⇒ `sleep` 被换成了 no-op（测试里 `patch("...time.sleep")`
                # 的常规手法）。**失败开放**：这种环境下闸门无法生效，硬等会把整条链
                # 挂死。真实运行时 monotonic 必然前进，走不到这一支。
                with self._cv:
                    idx = min(range(len(self._keys)), key=lambda i: self._last[i])
                    return self._reserve_locked(idx)
        raise RuntimeError("KeyPool.claim 等待超限（累计 %.0fs）" % waited)

    def note_rate_limited(self, idx: int) -> None:
        """某条 key 撞了 429：把它**多停一轮**，别马上再试。

        精确语义（别读错）：`claim()` 在占用时已把 `last` 记成当时时刻，
        所以本条**额外**再加一个 `interval` ⇒ 该 key 要到 **now + 2×interval**
        才会被再次选中（一个 interval 抵掉刚才那次被拒，一个给供应商窗口清场）。
        `interval == 0`（测试环境）时等价于"不额外惩罚"。

        为什么值得多停一轮：429 说明**供应商的窗口没被我们的 interval 满足** ——
        原地重试只会再撞一次。多停一轮的代价是"少一条 key 可用一个窗口"，
        换来的是不把 429 甩回来回弹；池里 3 条 key 时另两条照常顶上。
        """
        with self._cv:
            self._last[idx] = self._monotonic() + self._interval
            self._rl[idx] += 1
            self._cv.notify_all()
