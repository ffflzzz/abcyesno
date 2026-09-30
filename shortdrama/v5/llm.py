# -*- coding: utf-8 -*-
"""LLM access: 厂商档（`v5/vendors.py`）→ LangChain ChatOpenAI（复用生态，不自研）。

★ 2026-09-18 统一：本模块原先走 `config.LLM_PROVIDERS`（与媒体侧**并行的第二套注册表**），
现并入 `v5/vendors.py` ⇒ 三条链（文本 / 生图 / 视频）一个机制、一种错误风格。
选择变量：`SHORTDRAMA_CHAT_VENDOR`（旧名 `NEWDEEP_LLM_PROVIDER` 仍兼容）。
"""
from __future__ import annotations

import re

from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from . import config
from . import vendors


# 供应商限速文案（免费额度）：实测 429 正文为
# "You've reached the API rate limit for free users. Upgrade to a Token Plan..."
# 判定放在这里（模型调用的所有者），供 pipeline / qc / video 共用——
# 避免各处在自己的 except 分支里重复写 `"429" in str(e)` 而漏掉某一处
# （2026-09-12 事故：qc.review_shot_type 自己兜底吞掉 429 → 静默漏检）。
_RATE_LIMIT_MARKS = ("429", "rate limit", "ratelimit", "限速", "too many requests")


def is_rate_limit(err: BaseException) -> bool:
    """是否为限速类异常（429 / rate limit 文案，中英文都认）。"""
    s = str(err).lower()
    return any(m in s for m in _RATE_LIMIT_MARKS)


# ── 文本通道的 key 轮转（2026-09-30）─────────────────────────────────────────
# 事故：`xiaoman-workshop-1030` 第 2 集起服 20 秒就 `status=error`，正文是
# 「已达到 API 用量上限，请在 2026-09-30 19:00 之后重试」。逐条实测三条 key 的
# 文本额度：第 1 条 429、第 2 条可用、第 3 条可用 —— 而文本通道**只会用一条**
# （`config._chat_endpoint_of()` 死盯"第一条带专属地址的 key"，`config.py:97`
# 那句"池的消费方另行接线"至今没接）。额度是按 key 算的，所以换一条就是出口。
#
# 复用 `media.keypool.KeyPool` 的同一份闸门（`claim_nowait` / `cool`），
# 不在此另写一套限速计数。

_CHAT_POOL = None
_CHAT_CANDS: list = []


def chat_pool():
    """文本通道的 key 池（进程内共享冷却状态）。闸门=0：文本不需要配速，只要换 key。"""
    global _CHAT_POOL, _CHAT_CANDS
    from .media.keypool import KeyPool
    cands = config.chat_key_pool()
    if not config.CHAT_KEY_ROTATE:
        cands = cands[:1]
    if _CHAT_POOL is None or [c[0] for c in cands] != [c[0] for c in _CHAT_CANDS]:
        _CHAT_CANDS = cands
        _CHAT_POOL = KeyPool([c[0] for c in cands] or [""], 0.0)
    return _CHAT_POOL


_RESET_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})[ T](\d{1,2}):(\d{2})")


def cooldown_seconds(err: BaseException) -> float:
    """该冷却多久：**供应商说了就听它的**，没说用默认值。

    为什么要解析文案：额度耗尽不是"间隔太短"，`note_rate_limited()` 那种
    "多停一轮"对它无效（聊天侧 interval=0 ⇒ 等于不罚），会立刻拿同一条死 key 再撞。
    """
    m = _RESET_RE.search(str(err) or "")
    if m:
        try:
            import datetime as _dt
            target = _dt.datetime(*[int(x) for x in m.groups()])
            secs = (target - _dt.datetime.now()).total_seconds()
            if 0 <= secs:
                return min(secs + 30, 8 * 3600)      # 留 30s 余量，最长记 8 小时
        except Exception:  # noqa: BLE001 -- 文案变了就回落默认
            pass
    return float(config.CHAT_COOLDOWN_SEC)


class RotatingChatOpenAI(ChatOpenAI):
    """撞 429 就换下一条 key 重试；被拒的 key 按供应商给的窗口冷却。

    只覆盖 `invoke` / `ainvoke`（链上所有文本调用都走这两个）。
    `astream` 未覆盖 —— 走父类实现，用**最后一次领到的** key，不轮转；
    这条链上没人对文本用流式，写在这里是为了别以后误以为全覆盖。
    """

    def _apply(self, idx: int, key: str) -> None:
        # ★ 必须写**字段名** `openai_api_key` / `openai_api_base`：`api_key` 只是
        #   构造时的别名，事后既读不到也赋不上（pydantic 会直接抛
        #   "object has no field api_key"）。测试第一次跑就撞在这上面。
        self.openai_api_key = SecretStr(key)
        base = (_CHAT_CANDS[idx][1] if idx < len(_CHAT_CANDS) else "") or ""
        if base:
            self.openai_api_base = base

    def _next(self, err):
        """领一条放开窗口的 key（并记账）。全在冷却 ⇒ 抛错，**不原地干等**。"""
        pool = chat_pool()
        got = pool.claim_nowait()
        if got is None:
            raise RuntimeError(
                "文本通道 %d 条 key 全在冷却（最早 %.0f 秒后放开）→ 不再原地重试。"
                "上一条错误：%s" % (len(pool), pool.earliest_free_s(), str(err)[:200]))
        idx, key = got
        self._apply(idx, key)
        return pool, idx

    def _penalize(self, pool, idx: int, err: BaseException) -> None:
        secs = cooldown_seconds(err)
        pool.cool(idx, secs)
        print("[llm] ⚠️ %s 文本通道撞 429 → 冷却 %ds，换下一条 key 重试"
              % (pool.label(idx), int(secs)), flush=True)

    def invoke(self, *a, **kw):
        pool, idx = self._next(None)
        for _ in range(max(1, len(pool))):
            try:
                return ChatOpenAI.invoke(self, *a, **kw)
            except Exception as e:  # noqa: BLE001 -- 只认限流，其余照抛
                if not is_rate_limit(e):
                    raise
                self._penalize(pool, idx, e)
                pool, idx = self._next(e)
        raise RuntimeError("文本通道所有 key 均被拒")

    async def ainvoke(self, *a, **kw):
        pool, idx = self._next(None)
        for _ in range(max(1, len(pool))):
            try:
                return await ChatOpenAI.ainvoke(self, *a, **kw)
            except Exception as e:  # noqa: BLE001
                if not is_rate_limit(e):
                    raise
                self._penalize(pool, idx, e)
                pool, idx = self._next(e)
        raise RuntimeError("文本通道所有 key 均被拒")


def chat_for(provider: str = "", max_tokens: int = 8192,
             temperature: float = 0.1) -> ChatOpenAI:
    """provider 空 = 用当前选中的厂商（`SHORTDRAMA_CHAT_VENDOR`，缺省 agnes）。

    取值走 `vendors.chat_spec()`（与生图/视频**同一个机制**）。
    旧名 `NEWDEEP_LLM_PROVIDER` 仍兼容（主变量未设时回落）。

    ⚠️ 两种失败都**响亮报错**，**不再静默回退 agnes**：
    · 厂商未注册 ⇒ `UnknownVendor`
    · 已注册但文本模型名为空（如 `.env` 里 `DEEPSEEK_MODEL=` 还是空的）⇒ `VendorConfigError`

    temperature 可调：创作类角色（director/scriptwriter…）保留 0.1 的多样性；
    **评判类**（QC / clipqc / 分镜校验）必须传 0 —— 判据是概率性的，
    温度不为 0 时同一张图连审会给出不同结论（2026-09-10 实测 LN08 判 1/1/2、
    LN10 判 0/1/0），会让"复核→重拍"环路永远不收敛、白烧配额。
    """
    name = provider or ""
    # ★ 2026-09-18 统一：取值改走 `v5/vendors.py`（与生图/视频**同一个机制**）。
    #   两种失败都**响亮**：未注册 ⇒ `UnknownVendor`；模型名为空 ⇒ `VendorConfigError`。
    #   **刻意去掉**原先的"查不到就静默回退 agnes" ——
    #   那会让"以为在跑别家、其实一直是 agnes"完全不可见（本项目最忌）。
    p = vendors.chat_spec(name)
    # ★ 只有"agnes 且候选 key 多于一条"才套轮转 —— 单 key 时必须返回**父类对象**，
    #   否则 429 会被包成 `RuntimeError`，错误文案与改造前不一致（本项目要求
    #   "开关关掉 ⇒ 行为逐字节不变"是可验证的承诺，不是口头说法）。
    _rot = (vendors.current("chat") == "agnes" and config.CHAT_KEY_ROTATE
            and len(config.chat_key_pool()) > 1)
    cls = RotatingChatOpenAI if _rot else ChatOpenAI
    return cls(
        base_url=p["base_url"],
        api_key=p["api_key"],
        model=p["model"],
        temperature=temperature,
        max_tokens=max_tokens,
        # max_retries=2（原 6）：重试只在**抛异常**时触发——挂死的请求不抛异常
        # 就永远不会重试，6 次重试形同虚设。降到 2 是让"持续故障"在几分钟内
        # 快速失败（角色节点异常 → 评审兜底/强制放行），而不是整链无限停摆。
        max_retries=2,
        # 请求级总超时（秒，2026-09-11 last-bus 实测）：stream_chunk_timeout 只挡
        # **流式块间**间隔，非流式调用完全不受控——单个挂死请求让 director 卡了
        # 1h55m 零报警（llm.py 无超时缺陷第二次实锤）。300s 覆盖最慢的正常单轮
        # 输出，挂死请求 5 分钟内必然抛错。
        timeout=300,
        stream_chunk_timeout=300,
    )


def role_chat(role: str, max_tokens: int = 8192) -> ChatOpenAI:
    return chat_for(config.ROLE_PROVIDER.get(role, ""), max_tokens)
