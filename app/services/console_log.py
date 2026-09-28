"""结构化控制台日志 —— 让前端右栏能显示「哪个 Agent 在做什么」。

后端各处在关键节点调用 :func:`console_log`，事件经 EventBus 推到
`/api/events/stream`，前端按 ``app`` 落到对应 Agent 的日志缓冲区。

与 ``loguru`` 的分工：loguru 写文件（``logs/*.log``）供排查，本模块面向 UI，
payload 保持扁平、可直接渲染，不做任何格式化。
"""

from datetime import datetime
from typing import Any, Dict, Optional

from app.services.event_bus import publish
from app.services.event_types import EventType

# 与前端 apps store 的缓冲区名一致
APPS = ("review", "competitor", "trend", "forum", "report")

# debug / info / success / warning / error
LEVELS = ("debug", "info", "success", "warning", "error")

# 事件来源标签（前端把 source 映射成中文名）
SOURCES = ("review", "competitor", "trend", "forum", "report", "system")


def console_log(
    app: str,
    text: str,
    *,
    level: str = "info",
    source: Optional[str] = None,
    highlight: bool = False,
    **extra: Any,
) -> None:
    """发布一条结构化控制台日志（绝不抛异常，UI 日志不能影响主流程）。"""
    try:
        message = (text or "").strip()
        if not message:
            return
        target = app if app in APPS else "report"
        payload: Dict[str, Any] = {
            "app": target,
            "level": level if level in LEVELS else "info",
            "source": source or target,
            "text": message,
            "highlight": bool(highlight),
            "timestamp": datetime.now().strftime("%H:%M:%S"),
        }
        payload.update(extra)
        publish(EventType.CONSOLE_LOG, payload)
    except Exception:
        # 日志失败不能中断引擎/报告主流程
        pass
