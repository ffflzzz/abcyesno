# -*- coding: utf-8 -*-
"""LLM access: 厂商档（`v5/vendors.py`）→ LangChain ChatOpenAI（复用生态，不自研）。

★ 2026-09-18 统一：本模块原先走 `config.LLM_PROVIDERS`（与媒体侧**并行的第二套注册表**），
现并入 `v5/vendors.py` ⇒ 三条链（文本 / 生图 / 视频）一个机制、一种错误风格。
选择变量：`SHORTDRAMA_CHAT_VENDOR`（旧名 `NEWDEEP_LLM_PROVIDER` 仍兼容）。
"""
from __future__ import annotations

from langchain_openai import ChatOpenAI

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
    return ChatOpenAI(
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
