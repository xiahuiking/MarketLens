"""
方面级情感分析（ABSA - Aspect-Based Sentiment Analysis）

轻量级、无重模型依赖的方面级情感分析器，基于方面词典 + 情感词典 + 否定词处理。
面向电商评论（Amazon 英文评论为主，兼容中文），用于把一条评论拆解到
“质量 / 价格 / 物流 / 体验 / 外观 / 服务”等具体方面，并给出每个方面的情感倾向。

与 sentiment_analyzer.py（整条评论的文档级情感）互补：本模块回答“用户在哪个方面
满意 / 不满意”，输出可直接用于可视化（方面情感分布图）与差评归因。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# 方面词典：每个方面由“触发词”（用于识别评论是否提及该方面）与
# “正向 / 负向情感词”组成。触发词本身不带情感，仅用于方面识别。
# ---------------------------------------------------------------------------

ASPECTS: Dict[str, Dict[str, List[str]]] = {
    "质量": {
        "keywords": [
            "quality", "build", "material", "materials", "made", "craftsmanship",
            "durable", "durability", "sturdy", "solid", "reliable", "robust",
            "well made", "well-made", "workmanship", "做工", "质量", "材质", "耐用",
        ],
        "positive": [
            "durable", "sturdy", "solid", "reliable", "robust", "premium",
            "well made", "well-made", "high quality", "good quality", "great quality",
            "excellent quality", "lasts", "lasting", "long lasting", "long-lasting",
            "built to last", "heavy duty", "heavy-duty", "well built", "well-built",
            "做工好", "质量好", "很耐用", "结实",
        ],
        "negative": [
            "broke", "broken", "flimsy", "defective", "defect", "fell apart",
            "falling apart", "poor quality", "low quality", "bad quality",
            "cheaply made", "cheap made", "stopped working", "stop working",
            "doesn't work", "does not work", "didn't work", "did not work",
            "malfunction", "faulty", "cracked", "wore out", "worn out",
            "broke after", "broke within", "poorly made", "terrible quality",
            "做工差", "质量差", "坏了", "不耐用", "有瑕疵",
        ],
    },
    "价格": {
        "keywords": [
            "price", "pricing", "value", "worth", "cost", "expensive", "cheap",
            "overpriced", "affordable", "bargain", "价格", "性价比", "便宜", "贵", "值得",
        ],
        "positive": [
            "good value", "great value", "worth it", "worth the money",
            "worth every penny", "affordable", "reasonable price", "well priced",
            "well-priced", "bargain", "cheap", "inexpensive", "great price",
            "good price", "性价比高", "很划算", "物超所值", "便宜", "值得买",
        ],
        "negative": [
            "overpriced", "expensive", "too expensive", "too costly", "not worth",
            "not worth it", "not worth the money", "waste of money", "pricey",
            "over priced", "cost too much", "rip off", "rip-off", "a rip off",
            "性价比低", "太贵", "不值", "不值这个价", "坑钱",
        ],
    },
    "物流": {
        "keywords": [
            "shipping", "delivery", "delivered", "arrived", "arrive", "package",
            "packaging", "shipped", "快递", "物流", "发货", "包装", "配送", "送达",
        ],
        "positive": [
            "fast shipping", "fast delivery", "quick shipping", "quick delivery",
            "arrived early", "arrived quickly", "arrived on time", "on time",
            "well packaged", "well packed", "well-protected", "well protected",
            "prompt delivery", "shipped fast", "shipped quickly", "delivery fast",
            "发货快", "物流快", "包装好", "送货快", "准时到",
        ],
        "negative": [
            "late delivery", "slow shipping", "slow delivery", "arrived late",
            "arrived broken", "arrived damaged", "damaged in shipping",
            "damaged during shipping", "damaged in transit", "never arrived",
            "never received", "didn't arrive", "did not arrive", "lost in transit",
            "poor packaging", "bad packaging", "package damaged", "broken on arrival",
            "发货慢", "物流慢", "包装差", "包装破损", "没收到", "快递损坏",
        ],
    },
    "体验": {
        "keywords": [
            "easy to use", "ease of use", "setup", "set up", "instructions",
            "works", "working", "performance", "battery", "batteries", "function",
            "features", "user friendly", "user-friendly", "intuitive", "好用", "体验",
            "操作", "安装", "电池", "续航", "功能",
        ],
        "positive": [
            "easy to use", "easy to set up", "easy setup", "works great",
            "works well", "works perfectly", "works flawlessly", "great performance",
            "good performance", "long battery", "long battery life", "battery life",
            "user friendly", "user-friendly", "intuitive", "simple to use",
            "easy to install", "works as expected", "works as advertised",
            "easy to operate", "好用", "操作简单", "安装方便", "续航长", "体验好",
        ],
        "negative": [
            "hard to use", "difficult to use", "difficult to set up", "hard to set up",
            "confusing", "doesn't work", "does not work", "didn't work", "stopped working",
            "poor performance", "bad performance", "short battery", "short battery life",
            "battery died", "battery drains", "not user friendly", "not intuitive",
            "instructions unclear", "unclear instructions", "didn't work as expected",
            "not work as expected", "不好用", "操作复杂", "安装麻烦", "续航差", "体验差",
        ],
    },
    "外观": {
        "keywords": [
            "look", "looks", "design", "color", "colour", "appearance", "size",
            "fit", "style", "beautiful", "ugly", "外观", "设计", "颜色", "尺寸", "颜值",
        ],
        "positive": [
            "beautiful", "gorgeous", "stylish", "elegant", "sleek", "looks great",
            "looks good", "looks nice", "looks amazing", "nice design", "great design",
            "beautiful design", "good looking", "good-looking", "perfect size",
            "fits perfectly", "fits well", "great color", "nice color", "cute",
            "外观好看", "颜值高", "设计好看", "颜色好看", "尺寸合适",
        ],
        "negative": [
            "ugly", "unattractive", "looks cheap", "cheap looking", "cheap-looking",
            "poor design", "bad design", "wrong color", "color different", "too big",
            "too small", "doesn't fit", "does not fit", "didn't fit", "not as pictured",
            "not as described", "different from picture", "different from the picture",
            "mismatched color", "外观差", "不好看", "设计差", "颜色不对", "尺寸不对",
        ],
    },
    "服务": {
        "keywords": [
            "customer service", "support", "return", "refund", "warranty",
            "replacement", "seller", "客服", "售后", "退换", "退货", "退款", "保修",
        ],
        "positive": [
            "great customer service", "good customer service", "helpful support",
            "quick response", "fast response", "responsive", "easy return",
            "easy to return", "hassle free", "hassle-free", "full refund",
            "refunded", "quick refund", "great support", "helpful customer service",
            "客服好", "售后好", "退款快", "响应快", "服务好",
        ],
        "negative": [
            "bad customer service", "poor customer service", "terrible customer service",
            "no response", "no reply", "unresponsive", "won't refund", "wouldn't refund",
            "refused refund", "no refund", "hard to return", "difficult to return",
            "no warranty", "warranty not honored", "unhelpful", "rude",
            "ignored my email", "客服差", "售后差", "不退款", "不回复", "服务差",
        ],
    },
}


NEGATION_WORDS: set = {
    "not", "no", "never", "hardly", "barely", "without", "isn't", "isnt",
    "aren't", "arent", "wasn't", "wasnt", "weren't", "werent", "don't", "dont",
    "doesn't", "doesnt", "didn't", "didnt", "won't", "wont", "wouldn't", "wouldnt",
    "can't", "cant", "couldn't", "couldnt", "不", "没", "没有", "别",
}

_WORD_BOUNDARY_CACHE: Dict[str, re.Pattern] = {}


def _has_cjk(phrase: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in phrase)


def _pattern_for(phrase: str) -> re.Pattern:
    """按短语构造正则。

    - 英文短语：加词边界，避免 "fit" 误命中 "profit" 之类；
    - 中文短语：不加词边界（中文无空格分词，词边界会误伤连续文本）。
    """
    if phrase not in _WORD_BOUNDARY_CACHE:
        if _has_cjk(phrase):
            _WORD_BOUNDARY_CACHE[phrase] = re.compile(re.escape(phrase), re.IGNORECASE)
        else:
            _WORD_BOUNDARY_CACHE[phrase] = re.compile(
                r"(?<![A-Za-z0-9])" + re.escape(phrase) + r"(?![A-Za-z0-9])",
                re.IGNORECASE,
            )
    return _WORD_BOUNDARY_CACHE[phrase]


@dataclass
class AspectSentiment:
    """单个方面的情感统计。"""

    aspect: str
    positive: int = 0
    negative: int = 0
    neutral: int = 0
    mixed: int = 0

    @property
    def total(self) -> int:
        return self.positive + self.negative + self.neutral + self.mixed

    @property
    def net_score(self) -> float:
        """净情感得分，范围 [-1, 1]。1 表示全正面，-1 表示全负面。"""
        if self.total == 0:
            return 0.0
        return round((self.positive - self.negative) / self.total, 4)

    @property
    def sentiment(self) -> str:
        """方面级整体情感标签。"""
        if self.total == 0:
            return "未提及"
        if self.net_score > 0.2:
            return "正面"
        if self.net_score < -0.2:
            return "负面"
        if self.positive and self.negative:
            return "褒贬不一"
        return "中性"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "aspect": self.aspect,
            "positive": self.positive,
            "negative": self.negative,
            "neutral": self.neutral,
            "mixed": self.mixed,
            "total": self.total,
            "net_score": self.net_score,
            "sentiment": self.sentiment,
        }


@dataclass
class AspectSentimentSummary:
    """批量评论的方面级情感汇总。"""

    aspects: List[AspectSentiment] = field(default_factory=list)
    total_reviews: int = 0
    mention_rate: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_reviews": self.total_reviews,
            "mention_rate": self.mention_rate,
            "aspects": [a.to_dict() for a in self.aspects],
        }

    def distribution(self) -> List[Dict[str, Any]]:
        """返回适合直接作图 / 喂给 LLM 的方面分布列表。"""
        return [a.to_dict() for a in self.aspects]


class AspectSentimentAnalyzer:
    """方面级情感分析器（词典法，无重模型依赖）。"""

    def __init__(self, aspects: Optional[Dict[str, Dict[str, List[str]]]] = None):
        self.aspects = aspects or ASPECTS

    def _count_polarity(
        self, text: str, positive: List[str], negative: List[str]
    ) -> Tuple[int, int]:
        """
        统计方面情感的正/负向命中数（考虑否定词翻转）。

        - 正向词未否定 → 正向 +1；正向词被否定 → 负向 +1
        - 负向词未否定 → 负向 +1；负向词被否定 → 正向 +1
        """
        pos = 0
        neg = 0
        for phrase in positive:
            for m in _pattern_for(phrase).finditer(text):
                if self._is_negated(text, m.start()):
                    neg += 1
                else:
                    pos += 1
        for phrase in negative:
            for m in _pattern_for(phrase).finditer(text):
                if self._is_negated(text, m.start()):
                    pos += 1
                else:
                    neg += 1
        return pos, neg

    @staticmethod
    def _is_negated(text: str, phrase_start: int) -> bool:
        """判断情感词前 4 个词内是否出现否定词（朴素否定处理）。"""
        prefix = text[max(0, phrase_start - 40):phrase_start]
        tokens = re.split(r"\s+", prefix.strip())
        return any(t.strip().lower() in NEGATION_WORDS for t in tokens[-4:])

    def analyze_text(self, text: str) -> Dict[str, str]:
        """
        分析单条评论，返回每个方面的情感标签。

        Returns:
            dict: {aspect: "正面"/"负面"/"中性"/"褒贬不一"/"未提及"}
        """
        if not text or not text.strip():
            return {aspect: "未提及" for aspect in self.aspects}

        result: Dict[str, str] = {}
        for aspect, lexicon in self.aspects.items():
            mentioned = any(_pattern_for(kw).search(text) for kw in lexicon["keywords"])
            if not mentioned:
                result[aspect] = "未提及"
                continue

            pos, neg = self._count_polarity(text, lexicon["positive"], lexicon["negative"])

            if pos > 0 and neg > 0:
                result[aspect] = "褒贬不一"
            elif pos > 0:
                result[aspect] = "正面"
            elif neg > 0:
                result[aspect] = "负面"
            else:
                result[aspect] = "中性"
        return result

    def analyze_batch(self, texts: List[str]) -> AspectSentimentSummary:
        """批量分析评论，聚合为方面级情感汇总。"""
        aspects = {aspect: AspectSentiment(aspect=aspect) for aspect in self.aspects}
        valid = 0

        for text in texts:
            if not text or not text.strip():
                continue
            valid += 1
            per_aspect = self.analyze_text(text)
            for aspect, label in per_aspect.items():
                bucket = aspects[aspect]
                if label == "正面":
                    bucket.positive += 1
                elif label == "负面":
                    bucket.negative += 1
                elif label == "褒贬不一":
                    bucket.mixed += 1
                elif label == "中性":
                    bucket.neutral += 1
                # "未提及" 不计入

        mention_rate: Dict[str, float] = {}
        for aspect, bucket in aspects.items():
            mention_rate[aspect] = round(bucket.total / valid, 4) if valid else 0.0

        return AspectSentimentSummary(
            aspects=list(aspects.values()),
            total_reviews=valid,
            mention_rate=mention_rate,
        )

    def summarize(self, texts: List[str]) -> Dict[str, Any]:
        """便捷方法：返回可直接序列化的汇总字典。"""
        return self.analyze_batch(texts).to_dict()


# 模块级单例（无状态，纯词典，可安全复用）
aspect_sentiment_analyzer = AspectSentimentAnalyzer()


def analyze_aspect_sentiment(texts: List[str]) -> AspectSentimentSummary:
    """便捷函数：批量方面级情感分析。"""
    return aspect_sentiment_analyzer.analyze_batch(texts)


__all__ = [
    "ASPECTS",
    "AspectSentiment",
    "AspectSentimentSummary",
    "AspectSentimentAnalyzer",
    "aspect_sentiment_analyzer",
    "analyze_aspect_sentiment",
]
