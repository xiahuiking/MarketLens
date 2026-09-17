"""
可视化模块 — 数据驱动图表 + 方面级情感分析（ABSA）。

为 ReportEngine 提供真实电商数据的图表（评分分布 / 情感分布 / 评论趋势 /
竞品对比 / 方面情感 / 价格带），把结构化数据注入报告，替代 LLM 凭空生成的图表。
"""

from .chart_builder import (
    build_rating_distribution_widget,
    build_sentiment_distribution_widget,
    build_review_trend_widget,
    build_competitor_comparison_widget,
    build_aspect_sentiment_widget,
    build_price_band_widget,
    build_all_widgets,
)
from .data_provider import VisualizationProvider, get_visualization_provider

__all__ = [
    "build_rating_distribution_widget",
    "build_sentiment_distribution_widget",
    "build_review_trend_widget",
    "build_competitor_comparison_widget",
    "build_aspect_sentiment_widget",
    "build_price_band_widget",
    "build_all_widgets",
    "VisualizationProvider",
    "get_visualization_provider",
]
