"""
Shared OpenAI-compatible LLM client for all engines.
"""

import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, Optional, Generator
from uuid import uuid4

from loguru import logger
from openai import OpenAI

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

    @with_retry(LLM_RETRY_CONFIG)
    def invoke(self, system_prompt: str, user_prompt: str, json_output:bool=False,**kwargs) -> str:
        """Non-streaming LLM call, returns the full response."""
        call_uuid = uuid4()
        current_time = datetime.now().strftime("%Y年%m月%d日%H时%M分")

        time_prefix = f"今天的实际时间是{current_time}，用户输入:"
        
        user_prompt = f"{time_prefix}\n{user_prompt}"
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        llm_invoke_input = {
            "uuid":str(call_uuid),
            "messages":messages
        }
        # logger.debug(f"LLM调用，入参：\n {llm_invoke_input}")

        allowed_keys = {"temperature", "top_p", "presence_penalty", "frequency_penalty", "stream"}
        extra_params = {key: value for key, value in kwargs.items() if key in allowed_keys and value is not None}

        timeout = kwargs.pop("timeout", self.timeout)
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

        if response.choices and response.choices[0].message:
            content = response.choices[0].message.content.strip()
            llm_invoke_output = {
            "uuid":str(call_uuid),
            "content":content
            }
            # logger.debug(f"LLM调用，出参：\n {llm_invoke_output}")
            return content
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

        try:
            stream = self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                timeout=timeout,
                **extra_params,
            )

            for chunk in stream:
                if chunk.choices and len(chunk.choices) > 0:
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        yield delta.content
        except Exception as e:
            logger.error(f"流式请求失败: {str(e)}")
            raise e

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

        # 优先使用 function calling（schema 约束更严格、更可靠）。
        # 但部分"思考模式"模型（如 deepseek-v4-pro / deepseek-reasoner）不支持
        # tool_choice，会返回 400，此时回退到 json_mode（JSON 输出模式）。
        try:
            structured = llm.with_structured_output(output_model, method="function_calling")
            return structured.invoke(messages)
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
        structured = llm.with_structured_output(output_model, method="json_mode")
        return structured.invoke(json_messages)

    def get_model_info(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model_name,
            "api_base": self.base_url or "default",
        }
