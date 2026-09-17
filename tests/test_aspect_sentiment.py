"""方面级情感分析（ABSA）单元测试。"""

import sys
from pathlib import Path

import pytest

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from engines.ReviewEngine.tools.aspect_sentiment import (
    ASPECTS,
    AspectSentimentAnalyzer,
    analyze_aspect_sentiment,
)


class TestAspectSentimentAnalyzer:
    def setup_method(self):
        self.analyzer = AspectSentimentAnalyzer()

    def test_positive_quality_negative_price(self):
        r = self.analyzer.analyze_text(
            "The quality is great and very durable, but the price is too expensive."
        )
        assert r["质量"] == "正面"
        assert r["价格"] == "负面"

    def test_positive_shipping(self):
        r = self.analyzer.analyze_text("shipping was fast, it arrived early and well packaged")
        assert r["物流"] == "正面"

    def test_negation_flips_sentiment(self):
        r = self.analyzer.analyze_text("The build is not durable at all")
        # "not durable" 应被否定词处理为负面
        assert r["质量"] == "负面"

    def test_unmentioned_aspect(self):
        r = self.analyzer.analyze_text("just a note with no aspect words")
        assert r["质量"] == "未提及"
        assert r["服务"] == "未提及"

    def test_chinese_text(self):
        r = self.analyzer.analyze_text("质量很好很耐用，但是价格太贵了")
        assert r["质量"] == "正面"
        assert r["价格"] == "负面"

    def test_analyze_batch_summary(self):
        summary = analyze_aspect_sentiment([
            "great quality, very durable",
            "poor quality, broke after a week",
            "fast shipping and delivery",
        ])
        d = summary.to_dict()
        assert d["total_reviews"] == 3
        aspects = {a["aspect"]: a for a in d["aspects"]}
        assert aspects["质量"]["positive"] == 1
        assert aspects["质量"]["negative"] == 1
        assert aspects["物流"]["positive"] == 1

    def test_empty_batch(self):
        summary = analyze_aspect_sentiment([])
        assert summary.total_reviews == 0
        assert all(a.total == 0 for a in summary.aspects)

    def test_lexicon_has_required_aspects(self):
        for aspect in ("质量", "价格", "物流", "体验", "外观", "服务"):
            assert aspect in ASPECTS
            assert ASPECTS[aspect]["positive"]
            assert ASPECTS[aspect]["negative"]
