"""
InsightContext — clean dependency container for ReviewEngine graph.

Holds config, LLM client, search tools, and utility methods.
LangGraph node classes receive ctx and pull what they need.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Union

from loguru import logger

from .llms import LLMClient
from .tools import (
    ClusteringService,
    DBResponse,
    ProductReviewDB,
    multilingual_sentiment_analyzer,
    WeiboMultilingualSentimentAnalyzer,
)
from app.config import Settings


@dataclass
class InsightContext:
    """Holds all dependencies needed by ReviewEngine's LangGraph nodes."""

    llm_client: LLMClient
    config: Settings
    engine_name: str = "review"
    search_agency: ProductReviewDB = field(default_factory=ProductReviewDB)
    progress_callback: Optional[Callable] = None

    # Lazy-loaded helpers
    clustering: ClusteringService = None
    sentiment_analyzer: WeiboMultilingualSentimentAnalyzer = None

    def __post_init__(self):
        if self.clustering is None:
            self.clustering = ClusteringService(self.config)
        if self.sentiment_analyzer is None:
            self.sentiment_analyzer = multilingual_sentiment_analyzer

    # ── Search execution ──────────────────────────────────────────────

    def execute_search(self, tool_name: str, query: str, **kwargs) -> DBResponse:
        """根据 tool_name 执行电商数据查询，并对评论类结果做情感/聚类后处理。

        工具：
        - search_products: 检索商品
        - get_product_reviews: 获取商品评论
        - get_top_complaints: 差评归因
        - get_rating_distribution: 评分分布
        - get_review_trend: 评论时间趋势
        - compare_products: 竞品对比
        - analyze_sentiment: 直接情感分析
        """
        logger.info(f"  → 执行数据库查询工具: {tool_name}")

        limit = kwargs.get("limit")

        if tool_name == "search_products":
            return self.search_agency.search_products(query, limit=limit or 50)

        if tool_name == "get_product_reviews":
            response = self.search_agency.get_product_reviews(query, limit=limit or 100)
            return self._post_process(response, kwargs)

        if tool_name == "get_top_complaints":
            response = self.search_agency.get_top_complaints(query, limit=limit or 50)
            return self._post_process(response, kwargs)

        if tool_name == "get_rating_distribution":
            return self.search_agency.get_rating_distribution(query)

        if tool_name == "get_review_trend":
            start = kwargs.get("start_date")
            end = kwargs.get("end_date")
            if not start or not end:
                raise ValueError("get_review_trend 需要 start_date 和 end_date")
            return self.search_agency.get_review_trend(query, start_date=start, end_date=end)

        if tool_name == "compare_products":
            queries = kwargs.get("product_queries") or [query]
            return self.search_agency.compare_products(queries)

        if tool_name == "analyze_sentiment":
            texts = kwargs.get("texts", query)
            result = self.analyze_sentiment_only(texts)
            return DBResponse(
                tool_name="analyze_sentiment",
                parameters={
                    "texts": texts if isinstance(texts, list) else [texts],
                    "sentiment_analysis": result,
                    **kwargs,
                },
                results=[], results_count=0,
            )

        logger.warning(f"未知工具 '{tool_name}'，回退到商品搜索")
        return self.search_agency.search_products(query, limit=limit or 50)

    def _post_process(self, response: DBResponse, kwargs: dict) -> DBResponse:
        """对评论类结果做去重、聚类与情感分析。"""
        if not response.results:
            return response

        unique_results = self._deduplicate_results(response.results)
        logger.info(f"  去重后 {len(unique_results)} 条")

        clustering_meta = None
        if self.config.ENABLE_CLUSTERING and len(unique_results) > 1:
            before = len(unique_results)
            unique_results = self.clustering.cluster_and_sample(unique_results)
            clustering_meta = {
                "enabled": True,
                "performed": len(unique_results) < before,
                "original_count": before,
                "sampled_count": len(unique_results),
            }
        if clustering_meta:
            response.parameters["clustering"] = clustering_meta

        if self._sentiment_enabled(kwargs) and unique_results:
            logger.info("  🎭 开始对评论进行情感分析...")
            analysis = self._perform_sentiment_analysis(unique_results)
            if analysis:
                response.parameters["sentiment_analysis"] = analysis

        response.results = unique_results
        response.results_count = len(unique_results)
        return response

    def _deduplicate_results(self, results: list) -> list:
        seen = set()
        unique = []
        for r in results:
            key = r.url if r.url else r.title_or_content[:100]
            if key not in seen:
                seen.add(key)
                unique.append(r)
        return unique

    # ── Sentiment analysis ────────────────────────────────────────────

    def _sentiment_enabled(self, kwargs: dict) -> bool:
        """全局开关 + 每次搜索 per-call 开关的统一判断。"""
        if not self.config.SENTIMENT_ANALYSIS_ENABLED:
            return False
        return kwargs.get("enable_sentiment", self.config.ENABLE_SENTIMENT_PER_SEARCH)

    def _perform_sentiment_analysis(self, results: list) -> Optional[Dict[str, Any]]:
        try:
            if not self.sentiment_analyzer.is_initialized and not self.sentiment_analyzer.is_disabled:
                logger.info("    初始化情感分析模型...")
                if not self.sentiment_analyzer.initialize():
                    logger.info("     情感分析模型初始化失败")
            results_dict = [{
                "content": r.title_or_content, "platform": r.platform,
                "author": r.author_nickname, "url": r.url,
                "publish_time": str(r.publish_time) if r.publish_time else None,
            } for r in results]
            sa = self.sentiment_analyzer.analyze_query_results(
                query_results=results_dict, text_field="content", min_confidence=0.5
            )
            return sa.get("sentiment_analysis")
        except Exception as e:
            logger.exception(f"情感分析出错: {e}")
            return None

    def analyze_sentiment_only(self, texts: Union[str, List[str]]) -> Dict[str, Any]:
        try:
            if not self.sentiment_analyzer.is_initialized and not self.sentiment_analyzer.is_disabled:
                logger.info("    初始化情感分析模型...")
                if not self.sentiment_analyzer.initialize():
                    logger.info("     情感分析模型初始化失败")
            if isinstance(texts, str):
                result = self.sentiment_analyzer.analyze_single_text(texts)
                return {"success": result.success and result.analysis_performed,
                        "total_analyzed": 1 if result.analysis_performed and result.success else 0,
                        "results": [result.__dict__]}
            batch = self.sentiment_analyzer.analyze_batch(list(texts), show_progress=True)
            return {"success": batch.analysis_performed and batch.success_count > 0,
                    "total_analyzed": batch.total_processed if batch.analysis_performed else 0,
                    "success_count": batch.success_count, "failed_count": batch.failed_count,
                    "average_confidence": batch.average_confidence if batch.analysis_performed else 0.0,
                    "results": [r.__dict__ for r in batch.results]}
        except Exception as e:
            logger.exception(f"情感分析出错: {e}")
            return {"success": False, "error": str(e), "results": []}

    # ── Utilities ─────────────────────────────────────────────────────

    @staticmethod
    def validate_date_format(date_str: str) -> bool:
        if not date_str:
            return False
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
            return True
        except ValueError:
            return False
