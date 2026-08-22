"""LLM 客户端 — 封装小米 MiMo API（OpenAI 兼容格式）

注意：MiMo模型的reasoning_content和content是分开的。
reasoning_content是思考过程，content是最终回复。
max_tokens控制的是content部分的token数，reasoning不计入。
"""
from __future__ import annotations
import json
import traceback
from openai import OpenAI
from .config import LLM_BASE_URL, LLM_API_KEY, LLM_MODEL


def _log(msg: str):
    import time as _t
    with open("pipeline.log", "a", encoding="utf-8") as f:
        f.write(f"[{_t.strftime('%H:%M:%S')}] [LLM] {msg}\n")


_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=LLM_BASE_URL,
            api_key=LLM_API_KEY,
            timeout=300.0,  # 5分钟总超时（MiMo推理模型需要更长时间）
        )
    return _client


def llm_call(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.7,
    max_tokens: int = 8192,
    timeout: float = 300.0,
    max_retries: int = 2,
) -> str:
    """同步 LLM 调用，返回文本（content字段）。带超时保护和重试。"""
    import threading
    client = get_client()
    last_error = None

    for attempt in range(max_retries + 1):
        result = [None]
        error = [None]

        def _do_call():
            try:
                resp = client.chat.completions.create(
                    model=LLM_MODEL,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout,
                )
                content = resp.choices[0].message.content or ""
                if not content:
                    reasoning = getattr(resp.choices[0].message, 'reasoning_content', None)
                    if reasoning:
                        content = reasoning
                result[0] = content

                # ETCLOVG: 记录token使用量
                if resp.usage:
                    try:
                        from etclovg.governance import record_usage
                        record_usage(
                            model=LLM_MODEL,
                            input_tokens=resp.usage.prompt_tokens or 0,
                            output_tokens=resp.usage.completion_tokens or 0,
                            node="pipeline",
                        )
                    except Exception:
                        pass
            except Exception as e:
                error[0] = e

        _log(f"调用开始 (timeout={timeout}s, attempt={attempt+1})")
        t = threading.Thread(target=_do_call, daemon=True)
        t.start()
        t.join(timeout=timeout)

        if t.is_alive():
            _log(f"调用超时 ({timeout}s)")
            last_error = f"超时({timeout}s)"
            if attempt < max_retries:
                continue
            return f"[LLM调用超时: {timeout}s]"

        if error[0]:
            _log(f"调用失败: {error[0]}")
            last_error = str(error[0])
            if attempt < max_retries:
                import time as _sleep
                _sleep.sleep(3)
                continue
            return f"[LLM调用失败: {error[0]}]"

        _log(f"调用完成, {len(result[0] or '')} 字")
        return result[0] or ""

    return f"[LLM调用失败(重试{max_retries}次): {last_error}]"


def llm_call_stream(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.7,
    max_tokens: int = 8192,
    on_token=None,
    on_reasoning=None,
) -> str:
    """流式 LLM 调用。
    on_token(token_str) — 每收到一个content token时回调
    on_reasoning(token_str) — 每收到一个reasoning token时回调
    返回最终content文本。
    """
    client = get_client()
    try:
        stream = client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            stream_options={"include_usage": True},
        )
        content_parts = []
        reasoning_parts = []
        usage_input = 0
        usage_output = 0
        for chunk in stream:
            # ETCLOVG: 捕获usage信息（最后一个chunk）
            if hasattr(chunk, 'usage') and chunk.usage:
                usage_input = chunk.usage.prompt_tokens or 0
                usage_output = chunk.usage.completion_tokens or 0
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            # MiMo reasoning_content
            rc = getattr(delta, 'reasoning_content', None)
            if rc:
                reasoning_parts.append(rc)
                if on_reasoning:
                    on_reasoning(rc)
            # 正文content
            if delta.content:
                content_parts.append(delta.content)
                if on_token:
                    on_token(delta.content)
        content = "".join(content_parts)
        if not content and reasoning_parts:
            content = "".join(reasoning_parts)

        # ETCLOVG: 记录流式调用的token使用量
        if usage_input > 0 or usage_output > 0:
            try:
                from etclovg.governance import record_usage
                record_usage(
                    model=LLM_MODEL,
                    input_tokens=usage_input,
                    output_tokens=usage_output,
                    node="pipeline_stream",
                )
            except Exception:
                pass

        return content
    except Exception as e:
        try:
            print(f"[LLM STREAM ERROR] {e}")
        except OSError:
            pass
        try:
            traceback.print_exc()
        except OSError:
            pass
        return f"[LLM调用失败: {e}]"


def llm_call_json(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3,
) -> dict:
    """调用 LLM 并解析 JSON 返回"""
    raw = llm_call(system_prompt, user_prompt, temperature=temperature)
    if raw.startswith("[LLM调用失败"):
        raise RuntimeError(raw)
    # 尝试提取 JSON 块
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0]
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0]
    return json.loads(raw.strip())
