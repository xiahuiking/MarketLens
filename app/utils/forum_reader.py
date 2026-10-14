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

# host 回灌开关 + 发言版本号 + 读取 trace。
# 评测系统（evals/）用它们做 factor separation：host=off 时引擎输出彼此独立，
# `V` 才是干净的集合函数；版本号 trace 让「哪个节点读到了哪一版 host 发言」可观测。
_host_feedback_enabled: bool = True
_host_speech_version: int = 0
_host_trace: list = []

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
    global _latest_host_speech, _host_speech_version
    if event_type == EventType.FORUM_MESSAGE and data.get("type") == "host":
        content = data.get("content", "")
        if content:
            with _cache_lock:
                _latest_host_speech = content
                _host_speech_version += 1


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
        最新的HOST发言内容，如果没有则返回None。
        当 host 回灌被关闭（set_host_feedback_enabled(False)）时恒返回 None。
    """
    with _cache_lock:
        if not _host_feedback_enabled:
            return None
        return _latest_host_speech


def set_host_feedback_enabled(enabled: bool) -> None:
    """开/关 host 发言回灌到引擎段落提示词。评测系统子集族实验固定关闭它。"""
    global _host_feedback_enabled
    with _cache_lock:
        _host_feedback_enabled = enabled


def get_host_feedback_enabled() -> bool:
    with _cache_lock:
        return _host_feedback_enabled


def get_latest_host_speech_with_version() -> Optional[tuple]:
    """返回 (speech, version)；回灌关闭或暂无发言时返回 None。"""
    with _cache_lock:
        if not _host_feedback_enabled or _latest_host_speech is None:
            return None
        return (_latest_host_speech, _host_speech_version)


def read_host_speech_for_node(node_name: str) -> Optional[tuple]:
    """
    引擎 summary 节点专用入口：读取最新 host 发言并记录 (node, host_version) trace。

    返回 (speech, version) 或 None。它同时封装了回灌开关与版本 trace，取代原先
    直接调用 get_latest_host_speech() 的 6 处调用点。
    """
    result = get_latest_host_speech_with_version()
    if result is not None:
        with _cache_lock:
            _host_trace.append({"node": node_name, "host_version": result[1]})
    return result


def get_host_trace() -> list:
    """返回 host 发言读取 trace：[{"node": ..., "host_version": N}, ...]。"""
    with _cache_lock:
        return list(_host_trace)


def reset_host_trace() -> None:
    """清空读取 trace（版本号单调递增，不在此重置）。"""
    global _host_trace
    with _cache_lock:
        _host_trace = []


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

