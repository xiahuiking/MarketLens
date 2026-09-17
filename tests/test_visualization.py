"""可视化模块单元测试（chart_builder + data_provider）。"""

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from engines.ReportEngine.visualization.chart_builder import (
    build_all_widgets,
    build_aspect_sentiment_widget,
    build_competitor_comparison_widget,
    build_price_band_widget,
    build_rating_distribution_widget,
    build_review_trend_widget,
    build_sentiment_distribution_widget,
)
from engines.ReportEngine.visualization.data_provider import VisualizationProvider
from engines.ReportEngine.utils.chart_validator import create_chart_validator


class FakeResponse:
    def __init__(self, error_message=None, parameters=None, results=None):
        self.error_message = error_message
        self.parameters = parameters or {}
        self.results = results or []


class FakeDB:
    """替代 ProductReviewDB，返回可控数据，避免真实数据库与重模型依赖。"""

    def __init__(self):
        self.products = []
        self.rating_distribution = {}
        self.trend = []
        self.reviews = []

    def _execute_query(self, sql, params=None):
        if "FROM product" in sql:
            return self.products
        if "FROM review" in sql and "MIN(review_time)" in sql:
            return [{"min_ts": 1_675_200_000_000, "max_ts": 1_701_648_000_000}]
        return []

    def get_rating_distribution(self, query):
        return FakeResponse(
            parameters={"rating_distribution": self.rating_distribution}
            if self.rating_distribution else {}
        )

    def get_review_trend(self, query, start_date, end_date):
        return FakeResponse(parameters={"trend": self.trend} if self.trend else {})

    def get_product_reviews(self, query, limit=100):
        if not self.reviews:
            return FakeResponse(error_message="未找到商品")
        return FakeResponse(results=self.reviews)


class _ReviewItem:
    def __init__(self, text):
        self.title_or_content = text


class TestChartBuilder:
    def setup_method(self):
        self.validator = create_chart_validator()

    def _assert_valid_widget(self, widget):
        assert widget is not None
        result = self.validator.validate(widget)
        assert result.is_valid, result.errors
        return widget

    def test_rating_distribution(self):
        w = self._assert_valid_widget(build_rating_distribution_widget({1: 2, 2: 3, 3: 10, 4: 50, 5: 35}))
        assert w["data"]["labels"] == ["1★", "2★", "3★", "4★", "5★"]
        assert w["data"]["datasets"][0]["data"] == [2, 3, 10, 50, 35]

    def test_sentiment_distribution(self):
        w = self._assert_valid_widget(build_sentiment_distribution_widget({"正面": 85, "中性": 10, "负面": 5}))
        assert w["widgetType"] == "chart.js/doughnut"

    def test_review_trend(self):
        w = self._assert_valid_widget(build_review_trend_widget([
            {"day": "2023-01-01", "count": 5}, {"day": "2023-01-02", "count": 8},
        ]))
        assert w["widgetType"] == "chart.js/line"

    def test_competitor_comparison(self):
        w = self._assert_valid_widget(build_competitor_comparison_widget([
            {"title": "A Headphones Pro", "average_rating": 4.5},
            {"title": "B Buds", "average_rating": 4.1},
        ]))
        assert w["data"]["datasets"][0]["label"] == "平均评分"

    def test_aspect_sentiment(self):
        w = self._assert_valid_widget(build_aspect_sentiment_widget([
            {"aspect": "质量", "positive": 10, "negative": 2, "neutral": 1, "mixed": 0},
            {"aspect": "价格", "positive": 3, "negative": 8, "neutral": 0, "mixed": 1},
        ]))
        assert len(w["data"]["datasets"]) >= 2

    def test_price_band(self):
        w = self._assert_valid_widget(build_price_band_widget([
            {"band": "0-20", "count": 5}, {"band": "20-40", "count": 3},
        ]))
        assert w["data"]["labels"] == ["0-20", "20-40"]

    def test_empty_inputs_return_none(self):
        assert build_rating_distribution_widget({}) is None
        assert build_sentiment_distribution_widget({}) is None
        assert build_review_trend_widget([]) is None
        assert build_competitor_comparison_widget([]) is None
        assert build_aspect_sentiment_widget([]) is None
        assert build_price_band_widget([]) is None

    def test_build_all_widgets(self):
        data = {
            "rating_distribution": {1: 2, 2: 3, 3: 10, 4: 50, 5: 35},
            "sentiment_distribution": {"正面": 85, "中性": 10, "负面": 5},
            "review_trend": [{"day": "2023-01-01", "count": 5}],
            "competitor_comparison": [{"title": "A", "average_rating": 4.5}],
            "aspect_sentiment": [{"aspect": "质量", "positive": 10, "negative": 2, "neutral": 1, "mixed": 0}],
            "price_bands": [{"band": "0-20", "count": 5}],
        }
        widgets = build_all_widgets(data)
        assert len(widgets) == 6
        for w in widgets:
            assert self.validator.validate(w).is_valid


class TestVisualizationProvider:
    def test_collect_empty_db_is_graceful(self):
        provider = VisualizationProvider(db=FakeDB())
        data = provider.collect("headphone")
        assert data["rating_distribution"] == {}
        assert data["sentiment_distribution"] == {}
        assert data["review_trend"] == []
        assert data["competitor_comparison"] == []
        assert data["aspect_sentiment"] == []
        assert data["price_bands"] == []

    def test_collect_with_data(self):
        db = FakeDB()
        db.products = [
            {"parent_asin": "B0A", "title": "A Headphones", "price": 25.0, "average_rating": 4.5, "rating_number": 100},
            {"parent_asin": "B0B", "title": "B Buds", "price": 60.0, "average_rating": 4.1, "rating_number": 80},
        ]
        db.rating_distribution = {1: 2, 2: 3, 3: 10, 4: 50, 5: 35}
        db.trend = [{"day": "2023-01-01", "count": 5, "avg_rating": 4.2}]
        provider = VisualizationProvider(db=db)

        data = provider.collect("headphone")
        assert data["rating_distribution"] == {1: 2, 2: 3, 3: 10, 4: 50, 5: 35}
        assert data["sentiment_distribution"] == {"正面": 85, "中性": 10, "负面": 5}
        assert data["review_trend"] == db.trend
        assert len(data["competitor_comparison"]) == 2
        assert data["price_bands"]
        # 无评论数据时 ABSA 应为空（不触发重模型）
        assert data["aspect_sentiment"] == []

    def test_build_widgets_and_bundles(self):
        db = FakeDB()
        db.products = [{"parent_asin": "B0A", "title": "A Headphones", "price": 25.0, "average_rating": 4.5, "rating_number": 100}]
        db.rating_distribution = {1: 2, 2: 3, 3: 10, 4: 50, 5: 35}
        db.trend = [{"day": "2023-01-01", "count": 5, "avg_rating": 4.2}]
        provider = VisualizationProvider(db=db)

        widgets = provider.build_widgets("headphone")
        bundles = provider.build_bundles("headphone")
        assert widgets, "有数据时应生成图表"
        assert bundles, "有数据时应生成 bundles"
        assert any(b["type"] == "rating_distribution" for b in bundles)

    def test_empty_query(self):
        provider = VisualizationProvider(db=FakeDB())
        assert provider.collect("") == {}
        assert provider.build_widgets("") == []
        assert provider.build_bundles("") == []
