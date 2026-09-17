"""
Shared search execution utility for InitialSearchNode and ReflectionSearchNode.
Extracted from the old graph.py _execute_search_and_convert.
"""

from typing import Any, Dict, Tuple

from loguru import logger
from ..context import InsightContext

def execute_search_and_convert(
    ctx: InsightContext, search_output: dict, search_query: str, search_tool: str
) -> Tuple[list[dict], dict]:
    """LLM 产出搜索参数 → 处理工具选择、参数补全、执行搜索、结果裁切和格式化 →
    返回 (results: list[dict], metadata: dict)，metadata 包含 sentiment_analysis / clustering
    等增强管线产生的元信息，供 summary 节点写入 LLM prompt。
    """
    kwargs: Dict[str, Any] = {}

    # 电商工具参数处理
    if search_tool == "get_review_trend":
        start = search_output.get("start_date")
        end = search_output.get("end_date")
        if start and end and ctx.validate_date_format(start) and ctx.validate_date_format(end):
            kwargs["start_date"] = start
            kwargs["end_date"] = end
        else:
            logger.warning("get_review_trend 缺少合法日期，回退到 get_product_reviews")
            search_tool = "get_product_reviews"
    elif search_tool == "compare_products":
        queries = search_output.get("product_queries") or [search_query]
        kwargs["product_queries"] = queries
    elif search_tool == "analyze_sentiment":
        texts = search_output.get("texts")
        if texts:
            kwargs["texts"] = texts
        else:
            search_tool = "get_product_reviews"

    if search_tool == "search_products":
        kwargs["limit"] = 50
    elif search_tool == "get_product_reviews":
        kwargs["limit"] = 100
    elif search_tool == "get_top_complaints":
        kwargs["limit"] = 50

    logger.info("  - 执行数据库查询...")
    response = ctx.execute_search(search_tool, search_query, **kwargs)

    results: list[dict] = []
    if response and response.results:
        max_results = ctx.config.MAX_SEARCH_RESULTS_FOR_LLM
        limit = min(len(response.results), max_results) if max_results > 0 else len(response.results)
        for r in response.results[:limit]:
            results.append({
                "title": r.title_or_content, "url": r.url or "",
                "content": r.title_or_content, "score": r.hotness_score,
                "raw_content": r.title_or_content,
                "published_date": r.publish_time.isoformat() if r.publish_time else None,
                "platform": r.platform, "content_type": r.content_type,
                "author": r.author_nickname, "engagement": r.engagement,
            })

    # 提取增强管线元信息（情感分析、聚类），不让它被丢弃
    metadata: dict = {}
    if response and response.parameters:
        if "sentiment_analysis" in response.parameters:
            metadata["sentiment_analysis"] = response.parameters["sentiment_analysis"]
        if "clustering" in response.parameters:
            metadata["clustering"] = response.parameters["clustering"]

    if results:
        msg = f"  - 找到 {len(results)} 个搜索结果"
        for r in results[:5]:
            msg += f"\n    {r['title'][:50]}..."
        logger.info(msg)
    else:
        logger.info("  - 未找到搜索结果")
    return results, metadata
