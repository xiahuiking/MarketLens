"""引擎侧结构化控制台日志 —— 把「Agent 正在做什么」推到前端日志栏。

设计：不改动三个引擎各自的 `*Context` 字段，而是复用已经接好的
``ctx.progress_callback``（由 ``app/services/search_service.py`` 注入）。
回调收到带 ``kind="console"`` 的 payload 时，会把它转成 ``console_log`` 事件，
而不是当成进度更新。

用法（节点内）::

    from engines.common.engine_console import log_event, summarize_results

    log_event(self.ctx, f"开始处理段落 {idx + 1}/{total}：{para['title']}")
    log_event(self.ctx, summarize_results(results, label="检索"), highlight=True)
"""

from typing import Any, Callable, Dict, List, Optional


def _emit(ctx: Any, text: str, *, level: str, highlight: bool) -> None:
    callback: Optional[Callable] = getattr(ctx, "progress_callback", None)
    if callback is None:
        return
    message = (text or "").strip()
    if not message:
        return
    engine = getattr(ctx, "engine_name", "") or "engine"
    try:
        callback({
            "kind": "console",
            "engine": engine,
            "source": engine,
            "level": level,
            "text": message,
            "highlight": bool(highlight),
        })
    except Exception:
        # 控制台日志绝不能影响引擎主流程
        pass


def log_event(
    ctx: Any,
    text: str,
    *,
    level: str = "info",
    highlight: bool = False,
) -> None:
    """发布一条属于当前 Agent 的结构化日志。"""
    _emit(ctx, text, level=level, highlight=highlight)


def summarize_results(
    results: Optional[List[Dict[str, Any]]],
    *,
    label: str = "检索",
    preview: int = 3,
    title_chars: int = 60,
) -> str:
    """把结果列表压成多行摘要：条数 + 前几条标题。

    找不到结果时给出明确提示（而不是静默），方便用户判断是关键词问题还是数据问题。
    """
    items = [r for r in (results or []) if isinstance(r, dict)]
    if not items:
        return f"{label}未返回结果"

    lines = [f"{label}命中 {len(items)} 条结果"]
    for item in items[:preview]:
        title = str(item.get("title") or item.get("name") or "").strip()
        url = str(item.get("url") or "").strip()
        title = " ".join(title.split())
        if len(title) > title_chars:
            title = title[:title_chars] + "…"
        if not title:
            title = "(无标题)"
        domain = ""
        if url:
            try:
                from urllib.parse import urlparse

                domain = urlparse(url).netloc
            except Exception:
                domain = ""
        lines.append(f"  · {title}" + (f"  — {domain}" if domain else ""))
    if len(items) > preview:
        lines.append(f"  · …另有 {len(items) - preview} 条")
    return "\n".join(lines)


def describe_metadata(metadata: Optional[Dict[str, Any]]) -> str:
    """把情感分析 / 聚类等增强管线的元信息压成一行，供日志展示。"""
    if not metadata:
        return ""
    parts: List[str] = []

    clustering = metadata.get("clustering")
    if isinstance(clustering, dict) and clustering.get("enabled"):
        if clustering.get("performed"):
            parts.append(
                f"聚类 {clustering.get('original_count')}→{clustering.get('sampled_count')} 条"
            )
        else:
            parts.append("聚类未触发")

    sentiment = metadata.get("sentiment_analysis")
    if isinstance(sentiment, dict):
        if sentiment.get("available") is False:
            parts.append("情感分析未执行")
        else:
            analyzed = sentiment.get("total_analyzed") or 0
            if analyzed:
                parts.append(f"情感分析 {analyzed} 条")

    return "；".join(parts)
