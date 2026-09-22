"""
Shared OpenAI-compatible LLM client for all engines.

所有 LLM 调用都会经过这里，因此也是 token / 成本核算的**唯一埋点处**：
``invoke`` / ``stream_invoke`` / ``stream_invoke_to_string`` / ``structured_invoke``
会调用 :mod:`engines.common.usage` 记录模型、token、耗时与估算成本。
"""

import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, Optional, Generator

from loguru import logger
from openai import OpenAI

from . import usage as usage_ledger

# Ensure app/utils/ (containing retry_helper) is importable
_current_dir = os.path.dirname(os.path.abspath(__file__))
_app_utils = os.path.join(os.path.dirname(os.path.dirname(_current_dir)), "app", "utils")
if _app_utils not in sys.path:
    sys.path.insert(0, _app_utils)


def _with_retry(config=None):
    """Simplified with_retry decorator — pass-through if retry_helper unavailable."""
    def decorator(func):
        return func
    return decorator


try:
    from app.utils.retry_helper import with_retry, LLM_RETRY_CONFIG  # noqa: F811
except ImportError:
    with_retry = _with_retry
    LLM_RETRY_CONFIG = None


def _stream_options_unsupported(exc: Exception) -> bool:
    """判断异常是否因为网关不认 ``stream_options``（而非真实请求失败）。"""
    message = str(exc).lower()
    return "stream_options" in message or "include_usage" in message


def _unpack_structured(raw: Any) -> tuple[Any, Any, Any]:
    """拆解 LangChain ``include_raw=True`` 的返回，兼容不带 raw 的实现。"""
    if isinstance(raw, dict) and "parsed" in raw:
        return raw.get("parsed"), raw.get("raw"), raw.get("parsing_error")
    return raw, None, None


def _langchain_usage(message: Any) -> Optional[dict[str, int]]:
    """从 LangChain 的 AIMessage 中提取 usage（不同版本字段名不同）。"""
    if message is None:
        return None
    meta = getattr(message, "usage_metadata", None)
    if meta:
        return meta if isinstance(meta, dict) else dict(meta)
    response_metadata = getattr(message, "response_metadata", None) or {}
    for key in ("token_usage", "usage"):
        value = response_metadata.get(key) if isinstance(response_metadata, dict) else None
        if value:
            return value
    return None


class LLMClient:
    """Unified OpenAI-compatible chat completion API wrapper."""

    def __init__(self, api_key: str, model_name: str,
                 base_url: Optional[str] = None,
                 engine_name: str = "Engine"):
        if not api_key:
            raise ValueError(f"{engine_name} API key is required.")
        if not model_name:
            raise ValueError(f"{engine_name} model name is required.")

        self.api_key = api_key
        self.base_url = base_url
        self.model_name = model_name
        self.provider = model_name
        self.engine_name = engine_name
        # TODO:最后兜底备选方案，使用structured output来实现，但是这样的话，没办法使用通用的llm_client，每次调用前，还需要重新构建一个LLM
        
        prefix = engine_name.upper().replace(" ", "_")
        timeout_fallback = (
            os.getenv("LLM_REQUEST_TIMEOUT")
            or os.getenv(f"{prefix}_REQUEST_TIMEOUT")
            or "1800"
        )
        try:
            self.timeout = float(timeout_fallback)
        except ValueError:
            self.timeout = 1800.0

        client_kwargs: Dict[str, Any] = {
            "api_key": api_key,
            "max_retries": 0,
        }
        if base_url:
            client_kwargs["base_url"] = base_url
        self.client = OpenAI(**client_kwargs)

    # ── 成本核算 ────────────────────────────────────────────────────────────

    def _record(
        self,
        method: str,
        prompt_text: str = "",
        completion_text: str = "",
        usage: Any = None,
        started: Optional[float] = None,
        ok: bool = True,
        error: str = "",
        billable: Optional[bool] = None,
    ) -> None:
        """把一次调用交给核算账本；异常绝不影响主流程。"""
        try:
            duration_ms = (time.perf_counter() - started) * 1000.0 if started else 0.0
            usage_ledger.record_llm_call(
                engine=self.engine_name,
                model=self.model_name,
                method=method,
                base_url=self.base_url or "",
                prompt_text=prompt_text,
                completion_text=completion_text,
                usage=usage,
                duration_ms=duration_ms,
                ok=ok,
                error=error,
                billable=billable,
            )
        except Exception:
            logger.exception("LLM 用量记录失败（不影响调用结果）")

    @with_retry(LLM_RETRY_CONFIG)
    def invoke(self, system_prompt: str, user_prompt: str, json_output:bool=False,**kwargs) -> str:
        """Non-streaming LLM call, returns the full response."""
        current_time = datetime.now().strftime("%Y年%m月%d日%H时%M分")

        time_prefix = f"今天的实际时间是{current_time}，用户输入:"
        
        user_prompt = f"{time_prefix}\n{user_prompt}"
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        allowed_keys = {"temperature", "top_p", "presence_penalty", "frequency_penalty", "stream"}
        extra_params = {key: value for key, value in kwargs.items() if key in allowed_keys and value is not None}

        timeout = kwargs.pop("timeout", self.timeout)
        estimate_prompt = f"{system_prompt}\n{user_prompt}"
        started = time.perf_counter()
        try:
            if json_output:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    timeout=timeout,
                    response_format={
                        "type":"json_object"
                    },
                    **extra_params,
                )
            else:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    timeout=timeout,
                    # response_format={
                    #     "type":"json_object"
                    # }
                    **extra_params,
                )
        except Exception as exc:
            self._record("invoke", prompt_text="", started=started, ok=False,
                         error=str(exc), billable=False)
            raise

        if response.choices and response.choices[0].message:
            content = response.choices[0].message.content.strip()
            self._record("invoke", prompt_text=estimate_prompt, completion_text=content,
                         usage=getattr(response, "usage", None), started=started)
            return content
        self._record("invoke", prompt_text=estimate_prompt, completion_text="",
                     usage=getattr(response, "usage", None), started=started)
        return ""

    def stream_invoke(self, system_prompt: str, user_prompt: str, **kwargs) -> Generator[str, None, None]:
        """Streaming LLM call, yields response chunks."""
        current_time = datetime.now().strftime("%Y年%m月%d日%H时%M分")
        time_prefix = f"今天的实际时间是{current_time}"
        if user_prompt:
            user_prompt = f"{time_prefix}\n{user_prompt}"
        else:
            user_prompt = time_prefix
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        allowed_keys = {"temperature", "top_p", "presence_penalty", "frequency_penalty"}
        extra_params = {key: value for key, value in kwargs.items() if key in allowed_keys and value is not None}
        extra_params["stream"] = True

        timeout = kwargs.pop("timeout", self.timeout)

        started = time.perf_counter()
        text_parts: list[str] = []
        usage_obj: Any = None
        error_message = ""

        try:
            try:
                # 请求网关在最后一块返回 usage；部分网关不认这个参数，需降级重试
                stream = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    timeout=timeout,
                    stream_options={"include_usage": True},
                    **extra_params,
                )
            except Exception as stream_options_error:
                if not _stream_options_unsupported(stream_options_error):
                    raise
                logger.debug(
                    f"网关不支持 stream_options.include_usage，改用字符数估算 token: "
                    f"{stream_options_error}"
                )
                stream = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=messages,
                    timeout=timeout,
                    **extra_params,
                )

            for chunk in stream:
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage is not None:
                    usage_obj = chunk_usage
                if chunk.choices and len(chunk.choices) > 0:
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        text_parts.append(delta.content)
                        yield delta.content
        except Exception as e:
            error_message = str(e)
            logger.error(f"流式请求失败: {str(e)}")
            raise
        finally:
            # 生成器被提前 close / 抛 GeneratorExit 时也会走到这里，保证不漏记
            self._record(
                "stream",
                prompt_text=f"{system_prompt}\n{user_prompt}",
                completion_text="".join(text_parts),
                usage=usage_obj,
                started=started,
                ok=not error_message,
                error=error_message,
                billable=None if not error_message else False,
            )

    # TODO: 把代码里面所有有stream_invoke_to_string的这部分，全部都去掉

    # 空流重试：思考模型（如 deepseek-v4-pro）在网关提前断流时，可能在 reasoning
    # 阶段就被截断，一个 content chunk 都没吐出来（实测 4 轮中 1 轮如此）。
    # 这种失败不抛异常，属于瞬时故障，用短而有界的重试挽救即可。
    # 刻意不使用上面的 LLM_RETRY_CONFIG：它 initial_delay=60s、最多 6 次，
    # 持续失败要白等约 35 分钟，对"空返回"这种场景过重。
    EMPTY_STREAM_MAX_RETRIES: int = 2
    EMPTY_STREAM_RETRY_DELAY: float = 3.0

    @with_retry(LLM_RETRY_CONFIG)
    def stream_invoke_to_string(self, system_prompt: str, user_prompt: str, **kwargs) -> str:
        """Streaming LLM call, safely concatenated into a single string.

        空返回按失败处理并做短重试；重试耗尽仍为空则返回 ""，
        由调用方决定如何兜底（如 format_report 会用段落摘要直接拼装报告）。

        每次尝试都会由 ``stream_invoke`` 单独计入核算（重试确实会产生多次计费）。
        """
        for attempt in range(self.EMPTY_STREAM_MAX_RETRIES + 1):
            byte_chunks = []
            for chunk in self.stream_invoke(system_prompt, user_prompt, **kwargs):
                byte_chunks.append(chunk.encode('utf-8'))

            if byte_chunks:
                return b''.join(byte_chunks).decode('utf-8', errors='replace')

            if attempt < self.EMPTY_STREAM_MAX_RETRIES:
                logger.warning(
                    f"流式调用返回空内容"
                    f"（第 {attempt + 1}/{self.EMPTY_STREAM_MAX_RETRIES + 1} 次），"
                    f"{self.EMPTY_STREAM_RETRY_DELAY}s 后重试"
                )
                time.sleep(self.EMPTY_STREAM_RETRY_DELAY)

        logger.warning("流式调用连续返回空内容，已放弃重试，交由调用方兜底")
        return ""


    def structured_invoke(self, system_prompt: str, user_prompt: str,
                          output_model: type, **kwargs):
        """Use LangChain with_structured_output to get a Pydantic model directly.

        Args:
            system_prompt: System prompt string.
            user_prompt: User prompt string.
            output_model: Pydantic BaseModel subclass defining the expected output.
            **kwargs: Passed through (timeout, temperature, etc.).

        Returns:
            An instance of output_model populated by the LLM.
        """
        from langchain_deepseek import ChatDeepSeek

        current_time = datetime.now().strftime("%Y年%m月%d日%H时%M分")
        user_prompt = f"今天的实际时间是{current_time}\n{user_prompt}"

        # 注意：langchain-deepseek 真正生效的字段是 api_base；传 base_url 只会被塞进
        # 未被使用的 openai_api_base，请求仍会打到官方 api.deepseek.com，用网关/中转
        # 的 key 认证必然 401。且下面的 json_mode 回退复用同一个对象，同样 401，
        # 导致 structured_invoke 永远失败。
        # api_base 不接受 None，因此仅在配置了 base_url 时才传，否则沿用官方默认值。
        llm_kwargs: Dict[str, Any] = {
            "model": self.model_name,
            "api_key": self.api_key,
            "timeout": kwargs.pop("timeout", self.timeout),
        }
        if self.base_url:
            llm_kwargs["api_base"] = self.base_url

        llm = ChatDeepSeek(**llm_kwargs)
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        estimate_prompt = f"{system_prompt}\n{user_prompt}"

        def _attempt(msgs: list[dict[str, str]], method: str):
            """执行一次结构化调用并记账；返回解析后的 Pydantic 对象。"""
            started = time.perf_counter()
            # include_raw=True 才能拿到底层 AIMessage，从中读取真实 token usage，
            # 否则 with_structured_output 只返回解析后的对象，usage 无从获取。
            structured = llm.with_structured_output(output_model, method=method, include_raw=True)
            try:
                raw = structured.invoke(msgs)
            except Exception as exc:
                # 400 之类的失败通常不计费：不估算 token，也不计入「价格未知」
                self._record(f"structured:{method}", prompt_text="", started=started,
                             ok=False, error=str(exc), billable=False)
                raise
            parsed, raw_message, parse_error = _unpack_structured(raw)
            self._record(
                f"structured:{method}",
                prompt_text=estimate_prompt,
                usage=_langchain_usage(raw_message),
                started=started,
                ok=True,
            )
            if parsed is None:
                raise ValueError(f"结构化输出解析失败: {parse_error}")
            return parsed

        # 优先使用 function calling（schema 约束更严格、更可靠）。
        # 但部分"思考模式"模型（如 deepseek-v4-pro / deepseek-reasoner）不支持
        # tool_choice，会返回 400，此时回退到 json_mode（JSON 输出模式）。
        try:
            return _attempt(messages, "function_calling")
        except Exception as func_call_error:
            logger.warning(
                f"[structured_invoke] function calling 失败（{func_call_error}），"
                f"回退到 json_mode"
            )

        # json_object 模式要求 prompt 中必须包含 "json" 字样，且需明确告诉模型输出结构，
        # 因此这里把 JSON Schema 一并注入 system prompt。
        import json as _json
        schema = _json.dumps(output_model.model_json_schema(), ensure_ascii=False)
        json_system_prompt = (
            f"{system_prompt}\n\n"
            f"请以 JSON 格式输出结果，JSON 对象必须符合以下 JSON Schema：\n{schema}\n"
            f"只输出 JSON，不要包含任何多余文字或解释。"
        )
        json_messages = [
            {"role": "system", "content": json_system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return _attempt(json_messages, "json_mode")

    def get_model_info(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model_name,
            "api_base": self.base_url or "default",
        }
