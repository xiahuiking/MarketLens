"""
Chart.js widget 构建器（纯函数，无 IO / 无模型依赖）。

把结构化的电商数据（评分分布、情感分布、评论趋势、竞品对比、方面情感、价格带）
转成 ReportEngine IR 的 `widget` block，交给 HTMLRenderer / ChartValidator 渲染。

每个函数都返回符合 IR schema 的 widget dict：
    {
      "type": "widget",
      "widgetId": "...",
      "widgetType": "chart.js/<bar|line|doughnut>",
      "props": {"type": "...", "title": "..."},
      "data": {"labels": [...], "datasets": [{"label": ..., "data": [...]}]}
    }
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


# 与主题色保持一致的调色板
PALETTE = {
    "bar": "#007bff",
    "line": "#17a2b8",
    "positive": "#28a745",
    "neutral": "#ffc107",
    "negative": "#dc3545",
    "mixed": "#6c757d",
    "rating": ["#dc3545", "#fd7e14", "#ffc107", "#28a745", "#198754"],
}

_WIDGET_SEQ = {"counter": 0}


def _next_id(prefix: str) -> str:
    _WIDGET_SEQ["counter"] += 1
    return f"{prefix}-{_WIDGET_SEQ['counter']}"


def _widget(
    chart_type: str,
    widget_id: str,
    labels: List[str],
    datasets: List[Dict[str, Any]],
    title: str,
) -> Dict[str, Any]:
    """组装标准的 widget block。"""
    return {
        "type": "widget",
        "widgetId": widget_id,
        "widgetType": f"chart.js/{chart_type}",
        "props": {"type": chart_type, "title": title},
        "data": {"labels": list(labels), "datasets": datasets},
    }


def _clean_num(value: Any) -> Any:
    """把 Decimal/None 等归一化为 int/float，保证 Chart.js 可渲染。"""
    if value is None:
        return 0
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0
    return int(f) if f.is_integer() else round(f, 2)


def _short_label(text: Optional[str], limit: int = 24) -> str:
    """截断过长的商品标题，保证坐标轴标签可读。"""
    text = (text or "").strip()
    if len(text) <= limit:
        return text or "未命名"
    return text[: limit - 1] + "…"


# ---------------------------------------------------------------------------
# 各图表构建函数
# ---------------------------------------------------------------------------

def build_rating_distribution_widget(
    distribution: Dict[Any, Any],
    title: str = "评分分布",
) -> Optional[Dict[str, Any]]:
    """
    评分分布柱状图。

    Args:
        distribution: {1: 计数, 2: 计数, ...}（1-5 星）
    """
    if not distribution:
        return None
    labels = ["1★", "2★", "3★", "4★", "5★"]
    counts = [_clean_num(distribution.get(star, 0)) for star in (1, 2, 3, 4, 5)]
    if sum(counts) == 0:
        return None
    return _widget(
        "bar",
        _next_id("viz-rating"),
        labels,
        [{"label": "评论数", "data": counts, "backgroundColor": PALETTE["rating"]}],
        title,
    )


def build_sentiment_distribution_widget(
    distribution: Dict[str, Any],
    title: str = "评论情感分布",
) -> Optional[Dict[str, Any]]:
    """
    文档级情感分布圆环图。

    Args:
        distribution: {"正面": 计数, "中性": 计数, "负面": 计数, ...}
    """
    if not distribution:
        return None
    # 只保留常见情感标签，顺序稳定
    order = ["正面", "非常正面", "中性", "负面", "非常负面", "褒贬不一"]
    labels = [k for k in order if k in distribution] + [
        k for k in distribution if k not in order
    ]
    counts = [_clean_num(distribution.get(k, 0)) for k in labels]
    if sum(counts) == 0:
        return None

    color_map = {
        "正面": PALETTE["positive"], "非常正面": "#1e7e34",
        "中性": PALETTE["neutral"],
        "负面": PALETTE["negative"], "非常负面": "#a71d2a",
        "褒贬不一": PALETTE["mixed"],
    }
    colors = [color_map.get(k, PALETTE["bar"]) for k in labels]
    return _widget(
        "doughnut",
        _next_id("viz-sentiment"),
        labels,
        [{"label": "评论数", "data": counts, "backgroundColor": colors}],
        title,
    )


def build_review_trend_widget(
    trend: List[Dict[str, Any]],
    title: str = "评论量趋势",
) -> Optional[Dict[str, Any]]:
    """
    评论量随时间变化的折线图。

    Args:
        trend: [{"day": "2023-01-01", "count": 12, "avg_rating": 4.3}, ...]
    """
    if not trend:
        return None
    labels = [str(t.get("day", "")) for t in trend]
    counts = [_clean_num(t.get("count", 0)) for t in trend]
    if not labels or sum(counts) == 0:
        return None

    datasets = [{
        "label": "评论量",
        "data": counts,
        "borderColor": PALETTE["line"],
        "backgroundColor": "rgba(23,162,184,0.2)",
        "fill": False,
        "tension": 0.3,
    }]
    return _widget("line", _next_id("viz-trend"), labels, datasets, title)


def build_competitor_comparison_widget(
    products: List[Dict[str, Any]],
    title: str = "竞品平均评分对比",
) -> Optional[Dict[str, Any]]:
    """
    竞品平均评分对比柱状图。

    Args:
        products: [{"title": ..., "average_rating": ..., "review_count": ..., "price": ...}, ...]
    """
    if not products:
        return None
    labels = [_short_label(p.get("title"), 20) for p in products]
    ratings = [_clean_num(p.get("average_rating")) for p in products]
    if not labels or all(r == 0 for r in ratings):
        return None
    return _widget(
        "bar",
        _next_id("viz-competitor"),
        labels,
        [{"label": "平均评分", "data": ratings, "backgroundColor": PALETTE["bar"]}],
        title,
    )


def build_aspect_sentiment_widget(
    aspect_summary: List[Dict[str, Any]],
    title: str = "方面情感分布",
) -> Optional[Dict[str, Any]]:
    """
    方面级情感（ABSA）分组柱状图。

    Args:
        aspect_summary: [{"aspect": "质量", "positive": 10, "negative": 3,
                          "neutral": 1, "mixed": 2}, ...]
    """
    if not aspect_summary:
        return None
    labels = [str(a.get("aspect", "")) for a in aspect_summary]
    if not labels:
        return None

    keys = [("positive", "正面"), ("negative", "负面"), ("neutral", "中性"), ("mixed", "褒贬不一")]
    color_map = {"正面": PALETTE["positive"], "负面": PALETTE["negative"],
                 "中性": PALETTE["neutral"], "褒贬不一": PALETTE["mixed"]}
    datasets = []
    for field, display in keys:
        values = [_clean_num(a.get(field, 0)) for a in aspect_summary]
        if any(values):
            datasets.append({"label": display, "data": values, "backgroundColor": color_map[display]})
    if not datasets:
        return None
    return _widget("bar", _next_id("viz-aspect"), labels, datasets, title)


def build_price_band_widget(
    bands: List[Dict[str, Any]],
    title: str = "价格带分布",
) -> Optional[Dict[str, Any]]:
    """
    价格带分布柱状图。

    Args:
        bands: [{"band": "0-20", "count": 5}, ...]
    """
    if not bands:
        return None
    labels = [str(b.get("band", "")) for b in bands]
    counts = [_clean_num(b.get("count", 0)) for b in bands]
    if not labels or sum(counts) == 0:
        return None
    return _widget(
        "bar",
        _next_id("viz-price"),
        labels,
        [{"label": "商品数", "data": counts, "backgroundColor": PALETTE["line"]}],
        title,
    )


def build_all_widgets(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    根据收集到的结构化数据一次性构建所有非空图表。

    Args:
        data: VisualizationProvider.collect() 的返回字典，字段可选：
              rating_distribution / sentiment_distribution / review_trend /
              competitor_comparison / aspect_sentiment / price_bands
    """
    widgets: List[Dict[str, Any]] = []

    builder_map = [
        (build_rating_distribution_widget, data.get("rating_distribution"), {}),
        (build_sentiment_distribution_widget, data.get("sentiment_distribution"), {}),
        (build_review_trend_widget, data.get("review_trend"), {}),
        (build_competitor_comparison_widget, data.get("competitor_comparison"), {}),
        (build_aspect_sentiment_widget, data.get("aspect_sentiment"), {}),
        (build_price_band_widget, data.get("price_bands"), {}),
    ]
    for builder, payload, kwargs in builder_map:
        if not payload:
            continue
        try:
            widget = builder(payload, **kwargs)
        except Exception:
            widget = None
        if widget:
            widgets.append(widget)
    return widgets


__all__ = [
    "build_rating_distribution_widget",
    "build_sentiment_distribution_widget",
    "build_review_trend_widget",
    "build_competitor_comparison_widget",
    "build_aspect_sentiment_widget",
    "build_price_band_widget",
    "build_all_widgets",
]
