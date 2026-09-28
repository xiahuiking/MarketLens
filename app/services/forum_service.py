"""
Forum service — in-memory forum message store and start/stop control.

Core flow uses EventBus; forum.log is a plain log file (not part of data flow).
"""

import re
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger

from app.services.event_bus import subscribe, unsubscribe
from engines.ForumEngine.handler import ForumEventHandler

LOG_DIR = Path('logs')
LOG_DIR.mkdir(exist_ok=True)

_forum_handler: Optional[ForumEventHandler] = None

# In-memory message store (replaces file-based polling)
MAX_FORUM_MESSAGES = 2000
_forum_messages: List[Dict[str, Any]] = []


_KNOWN_SENDERS = {'口碑 Agent', '竞品 Agent', '趋势 Agent', 'Forum Host'}

def _on_forum_message(event_type: str, data: Dict[str, Any]):
    """Listen to FORUM_MESSAGE events and accumulate in memory."""
    sender = data.get('sender', '')
    if sender not in _KNOWN_SENDERS:
        return
    timestamp = datetime.now().strftime('%H:%M:%S')
    msg = {
        'type': data.get('type', 'agent'),
        'sender': sender,
        'content': data.get('content', ''),
        'timestamp': timestamp,
        'source': data.get('source', ''),
    }
    _forum_messages.append(msg)
    if len(_forum_messages) > MAX_FORUM_MESSAGES:
        _forum_messages[:] = _forum_messages[-MAX_FORUM_MESSAGES:]

    # 同步一份到前端日志栏：Agent 发言进各自引擎的日志，主持人发言单独一条
    try:
        from app.services import console_log

        content = msg['content']
        is_host = msg['type'] == 'host'
        console_log.console_log(
            'forum' if is_host else (msg['source'] or 'review'),
            f"[{sender}] {content}",
            level='success' if is_host else 'info',
            source='forum' if is_host else (msg['source'] or 'review'),
            highlight=is_host,
            title=content,
        )
    except Exception:
        logger.exception("ForumEngine: 写入前端控制台日志失败")


def init_forum_log():
    """Initialize forum.log with a header line and subscribe to FORUM_MESSAGE events."""
    forum_log_file = LOG_DIR / "forum.log"
    start_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    with open(forum_log_file, 'w', encoding='utf-8') as f:
        f.write(f"=== ForumEngine 系统初始化 - {start_time} ===\n")
    subscribe(_on_forum_message)
    logger.info("ForumEngine: forum.log 已初始化，EventBus 消息订阅已注册")


def shutdown_forum_service():
    """Stop ForumEngine and unregister EventBus subscriptions."""
    stop_forum_engine()
    unsubscribe(_on_forum_message)


def start_forum_engine():
    """Start the ForumEventHandler."""
    global _forum_handler
    try:
        if _forum_handler is not None:
            return True

        _forum_handler = ForumEventHandler()
        _forum_handler.start()
        logger.info("ForumEngine: 论坛事件处理器已启动")
        return True
    except Exception as e:
        logger.exception(f"ForumEngine: 启动论坛失败: {e}")
        return False


def stop_forum_engine():
    global _forum_handler
    try:
        if _forum_handler is not None:
            _forum_handler.stop()
            _forum_handler = None
            logger.info("ForumEngine: 论坛已停止")
    except Exception as e:
        logger.exception(f"ForumEngine: 停止论坛失败: {e}")


def get_forum_log() -> Dict[str, Any]:
    """Return accumulated messages from in-memory store."""
    return {
        'log_lines': [],  # deprecated, kept for API compatibility
        'parsed_messages': list(_forum_messages),
        'total_lines': len(_forum_messages),
    }


# ── 下游输入净化 ────────────────────────────────────────────────────────────
#
# ReportEngine 会把 logs/forum.log 原文塞进提示词。该文件一旦混入测试用例的
# 占位发言（tests/test_*_engine_e2e.py 的「## 测试段落\n这是初始总结。」）或被
# 重复写坏的发言，报告就会变成「回应主持人 / 证据等级」的方法论散文。这里做
# 输入侧净化：只保留合法的 Agent/主持人发言，丢弃占位、过短与重复内容。

_FORUM_LINE_RE = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\]\s*\[([A-Z]+)\]\s*(.*)$")
_FORUM_KEEP_SOURCES = {"REVIEW", "COMPETITOR", "TREND", "HOST"}
_FORUM_PLACEHOLDER_MARKERS = (
    "测试段落",
    "这是初始总结",
    "这是反思后的总结",
    "占位",
    "todo",
    "lorem ipsum",
)
_FORUM_MIN_CONTENT_CHARS = 40
_FORUM_DEDUP_PREFIX_CHARS = 120
_FORUM_MAX_CHARS = 12000


def sanitize_forum_log_text(text: str, max_chars: int = _FORUM_MAX_CHARS) -> str:
    """净化论坛日志原文，供 ReportEngine 等下游消费。

    - 仅保留 ``[时间] [REVIEW|COMPETITOR|TREND|HOST]`` 形式的发言；
    - 丢弃 SYSTEM 行、占位/测试文本与过短内容；
    - 按内容前 N 字去重（论坛常出现同一条发言被反复写入）；
    - 结果按 ``max_chars`` 截尾，避免超长日志挤占提示词预算。
    """
    if not text:
        return ""

    kept: List[str] = []
    seen: set[str] = set()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = _FORUM_LINE_RE.match(line)
        if not match:
            continue
        _ts, source, content = match.groups()
        if source not in _FORUM_KEEP_SOURCES:
            continue

        plain = content.replace("\\n", " ").strip()
        if len(plain) < _FORUM_MIN_CONTENT_CHARS:
            continue
        lowered = plain.lower()
        if any(marker in lowered for marker in _FORUM_PLACEHOLDER_MARKERS):
            continue

        fingerprint = plain[:_FORUM_DEDUP_PREFIX_CHARS]
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        kept.append(line)

    if not kept:
        return ""

    joined = "\n".join(kept)
    if len(joined) <= max_chars:
        return joined

    tail = joined[-max_chars:]
    newline = tail.find("\n")
    return tail[newline + 1:] if newline != -1 else tail


def parse_forum_log_line(line: str) -> Optional[Dict[str, Any]]:
    """Parse a forum.log line (utility for log analysis, not core flow)."""
    pattern = r'\[(\d{2}:\d{2}:\d{2})\]\s*\[([^\]]+)\]\s*(.*)'
    match = re.match(pattern, line)
    if not match:
        return None

    timestamp, raw_source, content = match.groups()
    source = raw_source.strip().upper()

    if source == 'SYSTEM' or not content.strip():
        return None
    if source not in ['REVIEW', 'COMPETITOR', 'TREND', 'HOST']:
        return None

    cleaned_content = content.replace('\\n', '\n').replace('\\r', '').strip()

    if source == 'HOST':
        message_type = 'host'
        sender = 'Forum Host'
    else:
        message_type = 'agent'
        sender = {'REVIEW': '口碑 Agent', 'COMPETITOR': '竞品 Agent', 'TREND': '趋势 Agent'}[source]

    return {
        'type': message_type,
        'sender': sender,
        'content': cleaned_content,
        'timestamp': timestamp,
        'source': source,
    }
