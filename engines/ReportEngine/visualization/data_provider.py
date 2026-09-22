"""
可视化数据提供器 — 从本地电商评论库聚合结构化数据，并生成报告图表。

职责：
1. 查询商品/评论数据（评分分布、评论趋势、竞品对比、价格带）；
2. 运行方面级情感分析（ABSA），得到“质量/价格/物流/体验/外观/服务”等方面情感分布；
3. 产出两类结果：
   - build_widgets()：可直接注入报告 IR 的 Chart.js widget block；
   - build_bundles()：喂给报告 LLM 的结构化数据摘要（用于章节内图表生成）。

全部查询均为只读、容错：数据库不可用或为空时返回空结果，绝不抛异常破坏报告主流程。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from loguru import logger

from .chart_builder import build_all_widgets

# 注意：ProductReviewDB / ABSA 采用延迟导入（见方法内部），避免 import
# 本模块时连带触发 engines.ReviewEngine.tools 的重型依赖（情感模型/聚类）。
# 注解中要用的名字单独走 TYPE_CHECKING 导入：运行时不会被求值（本模块启用了
# from __future__ import annotations），但类型检查器与 typing.get_type_hints() 能解析到。
if TYPE_CHECKING:
    from engines.ReviewEngine.tools.search import ProductReviewDB

# 默认时间窗口：Amazon 2023 数据大致落在此区间
DEFAULT_START = "2022-01-01"
DEFAULT_END = "2024-12-31"


def _to_num(value: Any) -> float:
    """安全转 float。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class VisualizationProvider:
    """电商评论可视化数据提供器。"""

    def __init__(self, db: Optional[ProductReviewDB] = None):
        if db is None:
            from engines.ReviewEngine.tools.search import ProductReviewDB
            db = ProductReviewDB()
        self.db = db

    # ------------------------------------------------------------------
    # 数据查询
    # ------------------------------------------------------------------

    def _fetch_products(self, query: str, limit: int = 6) -> List[Dict[str, Any]]:
        """按关键词检索商品（含价格，供竞品对比 / 价格带使用）。"""
        like = f"%{query}%"
        sql = (
            "SELECT parent_asin, title, brand, store, price, main_category, average_rating, rating_number "
            "FROM product "
            "WHERE title LIKE %s OR parent_asin = %s OR brand LIKE %s OR store LIKE %s "
            "ORDER BY rating_number DESC LIMIT %s"
        )
        try:
            return self.db._execute_query(sql, (like, query, like, like, limit)) or []
        except Exception as exc:  # pragma: no cover - 防御性容错
            logger.warning(f"可视化：检索商品失败 {exc}")
            return []

    def _resolve_asins(self, query: str, limit: int = 5) -> List[str]:
        products = self._fetch_products(query, limit)
        return [p.get("parent_asin") for p in products if p.get("parent_asin")]

    def _review_date_range(self, asins: List[str]) -> tuple[Optional[str], Optional[str]]:
        if not asins:
            return None, None
        placeholders = ",".join(["%s"] * len(asins))
        sql = (
            f"SELECT MIN(review_time) AS min_ts, MAX(review_time) AS max_ts "
            f"FROM review WHERE parent_asin IN ({placeholders})"
        )
        try:
            rows = self.db._execute_query(sql, tuple(asins)) or []
        except Exception as exc:  # pragma: no cover
            logger.warning(f"可视化：查询评论时间范围失败 {exc}")
            return None, None
        if not rows:
            return None, None

        def _fmt(ts: Any) -> Optional[str]:
            try:
                val = float(ts)
                sec = val / 1000 if val > 1_000_000_000_000 else val
                return datetime.fromtimestamp(sec).strftime("%Y-%m-%d")
            except (TypeError, ValueError, OSError):
                return None

        return _fmt(rows[0].get("min_ts")), _fmt(rows[0].get("max_ts"))

    def _rating_distribution(self, query: str) -> Dict[int, int]:
        resp = self.db.get_rating_distribution(query)
        if resp.error_message:
            return {}
        dist = resp.parameters.get("rating_distribution", {})
        return {int(k): int(v) for k, v in dist.items()} if isinstance(dist, dict) else {}

    def _sentiment_from_rating(self, dist: Dict[int, int]) -> Dict[str, int]:
        if not dist:
            return {}
        positive = dist.get(4, 0) + dist.get(5, 0)
        neutral = dist.get(3, 0)
        negative = dist.get(1, 0) + dist.get(2, 0)
        result = {}
        if positive:
            result["正面"] = positive
        if neutral:
            result["中性"] = neutral
        if negative:
            result["负面"] = negative
        return result

    def _sentiment_from_review_state(self, query: str) -> Dict[str, int]:
        """读取 ReviewEngine 真实模型跑出的情感分布（来自最近一次运行的 state 快照）。

        星级是"满意度"的代理指标，和评论正文的情感倾向并不等价（1 星差评常写
        "东西不错但物流太慢"）。只要 ReviewEngine 的模型结果落盘了，就优先用它。

        容错：读不到任何快照就返回 {}，由调用方回退到星级代理。
        """
        try:
            import glob as _glob
            import json as _json
            import os
            from app.config import PROJECT_ROOT

            pattern = str(Path(PROJECT_ROOT) / "data" / "report" / "review" / "state_*.json")
            files = _glob.glob(pattern)
            if not files:
                return {}

            q = (query or "").strip().lower()

            def _matches(path: str) -> bool:
                try:
                    with open(path, "r", encoding="utf-8") as fh:
                        state_query = str(_json.load(fh).get("query", "")).lower()
                except Exception:
                    return False
                if not q or not state_query:
                    return False
                return q in state_query or state_query in q

            candidates = [p for p in files if _matches(p)] or files
            candidates.sort(key=os.path.getmtime, reverse=True)

            best: Dict[str, int] = {}
            best_total = 0
            for path in candidates[:5]:
                try:
                    with open(path, "r", encoding="utf-8") as fh:
                        state = _json.load(fh)
                except Exception:
                    continue
                for para in state.get("paragraphs") or []:
                    research = para.get("research") or {}
                    meta = research.get("metadata") or (research.get("current_search") or {}).get("metadata") or {}
                    sa = meta.get("sentiment_analysis") or {}
                    if sa.get("available") is False:
                        continue
                    dist = sa.get("sentiment_distribution") or {}
                    total = int(sa.get("total_analyzed") or 0)
                    # 同一 state 内按"分析条数最多的那一段"取，避免跨段落重复计数
                    if total > best_total and dist:
                        best_total = total
                        best = {str(k): int(v) for k, v in dist.items()}
                if best:
                    break
            if best:
                logger.info(
                    f"可视化：使用 ReviewEngine 模型情感分布（{best_total} 条）{best}"
                )
            return best
        except Exception as exc:  # pragma: no cover - 防御性容错
            logger.debug(f"可视化：读取 ReviewEngine 情感快照失败 {exc}")
            return {}

    def _review_trend(self, query: str, asins: List[str]) -> List[Dict[str, Any]]:
        start, end = self._review_date_range(asins)
        if not start or not end:
            start, end = DEFAULT_START, DEFAULT_END
        resp = self.db.get_review_trend(query, start, end)
        if resp.error_message:
            return []
        trend = resp.parameters.get("trend", [])
        return trend if isinstance(trend, list) else []

    def _competitor_comparison(self, query: str, limit: int = 6) -> List[Dict[str, Any]]:
        products = self._fetch_products(query, limit)
        result = []
        for p in products:
            result.append({
                "title": p.get("title") or p.get("parent_asin") or "未命名",
                "parent_asin": p.get("parent_asin"),
                "brand": p.get("brand") or p.get("store"),
                "price": _to_num(p.get("price")),
                "average_rating": _to_num(p.get("average_rating")),
                "rating_number": int(_to_num(p.get("rating_number"))),
            })
        return result

    def _price_bands(self, query: str, limit: int = 50) -> List[Dict[str, Any]]:
        products = self._fetch_products(query, limit)
        prices = [_to_num(p.get("price")) for p in products if _to_num(p.get("price")) > 0]
        if not prices:
            return []
        # 以 20 为步长分桶，覆盖常见区间
        import math
        top = max(prices)
        step = 20.0
        n_bins = min(10, max(1, int(math.ceil(top / step))))
        buckets = [0] * n_bins
        for price in prices:
            idx = min(int(price // step), n_bins - 1)
            buckets[idx] += 1
        return [
            {"band": f"{int(i * step)}-{int((i + 1) * step)}", "count": buckets[i]}
            for i in range(n_bins)
            if buckets[i] > 0
        ]

    def _aspect_sentiment(self, query: str, limit: int = 300) -> List[Dict[str, Any]]:
        resp = self.db.get_product_reviews(query, limit=limit)
        if resp.error_message:
            return []
        texts = [r.title_or_content for r in resp.results if r.title_or_content]
        if not texts:
            return []
        try:
            from engines.ReviewEngine.tools.aspect_sentiment import analyze_aspect_sentiment
            summary = analyze_aspect_sentiment(texts)
        except Exception as exc:  # pragma: no cover
            logger.warning(f"可视化：方面情感分析失败 {exc}")
            return []
        return summary.distribution()

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------

    def collect(self, query: str, max_reviews: int = 300) -> Dict[str, Any]:
        """
        聚合一次查询所需的所有可视化数据。任何子查询失败都只影响对应字段。

        Returns:
            dict: 键为 rating_distribution / sentiment_distribution / review_trend /
                  competitor_comparison / aspect_sentiment / price_bands
        """
        query = (query or "").strip()
        if not query:
            return {}

        data: Dict[str, Any] = {}

        rating_dist = self._rating_distribution(query)
        data["rating_distribution"] = rating_dist

        # 情感分布优先用 ReviewEngine 模型结果，拿不到才退回星级代理
        model_sentiment = self._sentiment_from_review_state(query)
        if model_sentiment:
            data["sentiment_distribution"] = model_sentiment
            data["sentiment_source"] = "model"
        else:
            data["sentiment_distribution"] = self._sentiment_from_rating(rating_dist)
            data["sentiment_source"] = "rating" if data["sentiment_distribution"] else "none"

        asins = self._resolve_asins(query, limit=5)
        data["review_trend"] = self._review_trend(query, asins)
        data["competitor_comparison"] = self._competitor_comparison(query, limit=6)
        data["price_bands"] = self._price_bands(query, limit=50)
        data["aspect_sentiment"] = self._aspect_sentiment(query, limit=max_reviews)

        return data

    def build_widgets(self, query: str) -> List[Dict[str, Any]]:
        """构建报告可用的 Chart.js widget block 列表（空数据返回 []）。"""
        try:
            data = self.collect(query)
        except Exception as exc:  # pragma: no cover
            logger.warning(f"可视化数据收集失败 {exc}")
            return []
        return build_all_widgets(data)

    def build_bundles(self, query: str) -> List[Dict[str, Any]]:
        """
        构建给报告 LLM 的结构化数据摘要（dataBundles）。

        返回可序列化的列表，每个元素描述一个数据点，便于 LLM 在章节中生成准确图表。
        """
        data = self.collect(query)
        bundles: List[Dict[str, Any]] = []

        if data.get("rating_distribution"):
            bundles.append({
                "type": "rating_distribution",
                "title": "评分分布",
                "data": {f"{k}星": v for k, v in sorted(data["rating_distribution"].items())},
            })
        if data.get("sentiment_distribution"):
            source = data.get("sentiment_source", "none")
            bundles.append({
                "type": "sentiment_distribution",
                "title": "评论情感分布",
                "data": data["sentiment_distribution"],
                # 标明口径：model = 评论情感模型；rating = 星级代理，两者不可混用
                "source": source,
                "note": "情感模型直接分析评论文本得出"
                if source == "model"
                else "由星级折算的代理指标，非文本情感模型结果",
            })
        if data.get("review_trend"):
            bundles.append({
                "type": "review_trend",
                "title": "评论量趋势",
                "data": data["review_trend"],
            })
        if data.get("competitor_comparison"):
            bundles.append({
                "type": "competitor_comparison",
                "title": "竞品对比",
                "data": data["competitor_comparison"],
            })
        if data.get("aspect_sentiment"):
            bundles.append({
                "type": "aspect_sentiment",
                "title": "方面情感分布",
                "data": data["aspect_sentiment"],
            })
        if data.get("price_bands"):
            bundles.append({
                "type": "price_bands",
                "title": "价格带分布",
                "data": data["price_bands"],
            })
        return bundles


_provider: Optional[VisualizationProvider] = None


def get_visualization_provider() -> VisualizationProvider:
    """获取可视化数据提供器单例。"""
    global _provider
    if _provider is None:
        _provider = VisualizationProvider()
    return _provider


__all__ = [
    "VisualizationProvider",
    "get_visualization_provider",
]
