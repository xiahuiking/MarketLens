"""
Shared search execution utility for InitialSearchNode and ReflectionSearchNode.
Extracted from the old graph.py _execute_search_and_convert.
"""

from typing import Any, Dict, Tuple

from loguru import logger
from ..context import ReviewContext


def _sentiment_rank(block: Any) -> int:
    """给一份情感分析结果打分，用于合并时择优。

    - 真实产出（available != False）按实际分析条数计分；
    - 未执行/透传（available == False）恒为 -1，永远不覆盖真实结果。
    """
    if not isinstance(block, dict):
        return -1
    if block.get("available") is False:
        return -1
    try:
        return int(block.get("total_analyzed") or 0)
    except (TypeError, ValueError):
        return 0


def _clustering_rank(block: Any) -> int:
    """聚类信息：真正执行过聚类的 > 仅记录的，无信息为 -1。"""
    if not isinstance(block, dict):
        return -1
    if block.get("performed"):
        return 2
    return 1 if block.get("enabled") else 0


def merge_search_metadata(existing: Dict[str, Any] | None,
                          new: Dict[str, Any] | None) -> Dict[str, Any]:
    """把一次搜索产生的增强元信息合并进段落级累积元信息。

    背景：`current_search` 每次搜索都会被整体替换，而段落最后几轮反思常常是
    `search_products` 之类**没有后处理**的工具（结果为空、metadata 为 `{}`），
    于是前面真实算出来的情感/聚类结果会被清空，导致 LLM 在最终总结里看到
    "情感分析未执行"。这里改为择优累积，保证已产出的结果不丢。
    """
    merged: Dict[str, Any] = dict(existing or {})
    new = new or {}
    if not new:
        return merged

    sa_new, sa_old = new.get("sentiment_analysis"), merged.get("sentiment_analysis")
    if sa_new is not None:
        # 已有真实结果时，透传/空结果不许覆盖；从未成功过则保留最后一次的失败原因，
        # 让 LLM 知道"情感分析没跑"而不是"没有情感信息"。
        if sa_old is None or _sentiment_rank(sa_new) > _sentiment_rank(sa_old):
            merged["sentiment_analysis"] = sa_new

    cl_new, cl_old = new.get("clustering"), merged.get("clustering")
    if cl_new is not None and _clustering_rank(cl_new) > _clustering_rank(cl_old):
        merged["clustering"] = cl_new

    return merged


def execute_search_and_convert(
    ctx: ReviewContext, search_output: dict, search_query: str, search_tool: str
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

    # LLM 可显式关闭本次的情感分析（纯统计/趋势类查询无需花算力）
    enable_sentiment = search_output.get("enable_sentiment")
    if enable_sentiment is not None:
        kwargs["enable_sentiment"] = bool(enable_sentiment)

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
