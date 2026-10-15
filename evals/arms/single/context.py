"""SingleContext —— 单 Agent 基线的依赖容器。

合并 12 工具：7 个数据库工具（复用 ReviewContext 的 execute_search，含情感/聚类后处理）
+ 5 个网络检索工具（复用 Competitor 的 search_agency）。两种来源统一转成
``.webpages`` 形状（name/url/snippet/date_last_crawled），使单 Agent 能复用竞品引擎的
节点与格式化逻辑（同构控制流，DESIGN 3.2-B）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from loguru import logger


_DB_TOOLS = {
    "search_products", "get_product_reviews", "get_top_complaints",
    "get_rating_distribution", "get_review_trend", "compare_products",
    "analyze_sentiment",
}
_WEB_TOOLS = {
    "comprehensive_search", "web_search_only", "search_for_structured_data",
    "search_last_24_hours", "search_last_week",
}


class _Webpage:
    def __init__(self, name: str, url: str, snippet: str, date: Optional[str] = None):
        self.name = name
        self.url = url
        self.snippet = snippet
        self.date_last_crawled = date


class _WebResponse:
    def __init__(self, webpages: list):
        self.webpages = webpages


def _db_response_to_web_response(resp) -> _WebResponse:
    """把 ReviewEngine 的 DBResponse 转成 Competitor 节点期望的 .webpages 形状。"""
    pages: list = []
    params = getattr(resp, "parameters", None) or {}
    if params.get("rating_distribution"):
        pages.append(_Webpage(
            "评分分布", "",
            f"评分分布（1-5 星计数）: {json.dumps(params['rating_distribution'], ensure_ascii=False)}",
        ))
    if params.get("sentiment_analysis"):
        sa = params["sentiment_analysis"]
        pages.append(_Webpage(
            "评论情感分析", "",
            f"评论情感分布: {json.dumps(sa, ensure_ascii=False)[:800]}",
        ))
    if params.get("clustering"):
        pages.append(_Webpage(
            "聚类统计", "",
            f"评论聚类: {json.dumps(params['clustering'], ensure_ascii=False)}",
        ))
    for r in (getattr(resp, "results", None) or []):
        text = getattr(r, "title_or_content", "") or ""
        url = getattr(r, "url", "") or ""
        author = getattr(r, "author_nickname", "") or ""
        ctype = getattr(r, "content_type", "") or ""
        name = f"{author} 的{ctype}" if (author and ctype) else (author or "数据库结果")
        date = str(getattr(r, "publish_time", "")) if getattr(r, "publish_time", None) else None
        pages.append(_Webpage(name, url, text, date))
    return _WebResponse(pages)


@dataclass
class SingleContext:
    """单 Agent 基线上下文：DB + 网络两套工具，统一 execute_search。"""

    llm_client: Any
    config: Any
    search_agency: Any            # 网络检索代理（Tavily/Bocha/Anspire）
    review_db: Any = None         # ProductReviewDB；None 时 ReviewContext 内部构建
    engine_name: str = "single"
    progress_callback: Optional[Callable] = None

    _review_ctx: Any = field(default=None, init=False)

    def _get_review_ctx(self):
        if self._review_ctx is None:
            from engines.ReviewEngine.context import ReviewContext
            kwargs = dict(
                llm_client=self.llm_client, config=self.config,
                progress_callback=self.progress_callback,
            )
            if self.review_db is not None:
                kwargs["search_agency"] = self.review_db
            self._review_ctx = ReviewContext(**kwargs)
        return self._review_ctx

    def log(self, text: str, *, level: str = "info", highlight: bool = False) -> None:
        from engines.common.engine_console import log_event
        log_event(self, text, level=level, highlight=highlight)

    def execute_search(self, tool_name: str, query: str, **kwargs) -> _WebResponse:
        logger.info(f"  → 执行搜索工具: {tool_name}")
        if tool_name in _DB_TOOLS:
            resp = self._get_review_ctx().execute_search(tool_name, query, **kwargs)
            return _db_response_to_web_response(resp)
        return _WebResponse(self._web_search(tool_name, query, **kwargs))

    def _web_search(self, tool_name: str, query: str, **kwargs) -> list:
        if tool_name == "comprehensive_search":
            resp = self.search_agency.comprehensive_search(
                query, kwargs.get("max_results", 10))
        elif tool_name == "web_search_only":
            resp = self._call_agency("web_search_only", query, max_results=kwargs.get("max_results", 15))
        elif tool_name == "search_for_structured_data":
            resp = self._call_agency("search_for_structured_data", query)
        elif tool_name == "search_last_24_hours":
            resp = self._call_agency("search_last_24_hours", query)
        elif tool_name == "search_last_week":
            resp = self._call_agency("search_last_week", query)
        else:
            resp = self.search_agency.comprehensive_search(query)

        pages = []
        if resp and getattr(resp, "webpages", None):
            for r in resp.webpages[:10]:
                pages.append(_Webpage(
                    r.name, r.url, r.snippet,
                    getattr(r, "date_last_crawled", None),
                ))
        return pages

    def _call_agency(self, method: str, query: str, **kwargs):
        fn = getattr(self.search_agency, method, None)
        if fn:
            return fn(query, **kwargs) if kwargs else fn(query)
        return self.search_agency.comprehensive_search(query)
