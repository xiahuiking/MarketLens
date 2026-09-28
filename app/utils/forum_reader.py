"""
Forum日志读取工具
通过 EventBus 订阅实时缓存 HOST 发言，避免每次读取文件。
"""

import threading
from typing import Optional

from loguru import logger

from app.services.event_bus import subscribe, unsubscribe
from app.services.event_types import EventType

# ── EventBus-backed cache ──────────────────────────────────────────

_latest_host_speech: Optional[str] = None
_cache_lock = threading.Lock()

# 主持人发言注入段落提示词时的护栏（见 format_host_speech_for_prompt）
HOST_SPEECH_MAX_CHARS = 1200
_HOST_SPEECH_PLACEHOLDER_MARKERS = (
    "测试段落",
    "占位",
    "todo",
    "lorem ipsum",
)


def _on_forum_message(event_type: str, data: dict):
    """EventBus subscriber: cache latest HOST speech in memory."""
    global _latest_host_speech
    if event_type == EventType.FORUM_MESSAGE and data.get("type") == "host":
        content = data.get("content", "")
        if content:
            with _cache_lock:
                _latest_host_speech = content


def init_forum_reader():
    """Register HOST speech cache subscriber."""
    subscribe(_on_forum_message)


def shutdown_forum_reader():
    """Unregister HOST speech cache subscriber."""
    global _latest_host_speech
    unsubscribe(_on_forum_message)
    with _cache_lock:
        _latest_host_speech = None


# ── Public API ─────────────────────────────────────────────────────

def get_latest_host_speech(log_dir: str = "logs") -> Optional[str]:
    """
    获取最新的HOST发言（从 EventBus 内存缓存读取）。

    Args:
        log_dir: 日志目录路径（保留参数兼容性，当前不再使用）

    Returns:
        最新的HOST发言内容，如果没有则返回None
    """
    with _cache_lock:
        return _latest_host_speech


def format_host_speech_for_prompt(host_speech: str) -> str:
    """
    格式化 HOST 发言，用于添加到段落提示词中。

    历史问题：主持人发言是「论坛元讨论」，动辄几千字地讲方法论（证据等级、锚点
    确认、流程诊断）。它被原样拼到段落提示词最前面后，模型会转而**回应主持人**，
    正文里出现「分析单元声明（回应主持人）」「按证据分级标准」这类内容，报告质量
    被彻底带偏。这里做三件事：

    1. 明显的占位/测试发言直接丢弃；
    2. 截断到 ``HOST_SPEECH_MAX_CHARS``，避免挤占提示词预算；
    3. 显式声明「仅作背景，不要在正文中回应」，并把正文写作纪律重申一次。

    Args:
        host_speech: HOST发言内容

    Returns:
        格式化后的内容；无可用的主持人发言时返回空串。
    """
    if not host_speech or not host_speech.strip():
        return ""

    speech = host_speech.strip()

    lowered = speech.lower()
    if any(marker in lowered for marker in _HOST_SPEECH_PLACEHOLDER_MARKERS):
        logger.debug("主持人发言疑似占位/测试内容，已丢弃：{}", speech[:80])
        return ""

    if len(speech) > HOST_SPEECH_MAX_CHARS:
        speech = speech[:HOST_SPEECH_MAX_CHARS].rstrip() + "…（已截断）"

    return f"""
### 论坛主持人最新总结（仅作背景参考）
以下是论坛主持人对各Agent讨论的最新总结。

**使用纪律（必须遵守）：**
- 不要在正文中回应、引用或复述主持人发言，禁止出现「回应主持人」「按主持人要求」之类的表述；
- 不要输出方法论、证据分级、锚点确认、口径声明、流程诊断等元讨论内容；
- 正文只以本段落实际检索到的数据与证据为准；主持人发言仅用于查漏补缺。

{speech}

---
"""

