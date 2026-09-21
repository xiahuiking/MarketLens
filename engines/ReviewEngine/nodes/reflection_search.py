"""
LangGraph node: reflection search — generate follow-up query, execute search.
"""

import json
from copy import deepcopy
from datetime import datetime

from loguru import logger

from engines.common.structured_output import SearchOutput
from ..state import ReviewGraphState
from ..prompts import SYSTEM_PROMPT_REFLECTION
from ..context import ReviewContext
from ._search_utils import execute_search_and_convert, merge_search_metadata


class ReflectionSearchNode:
    """Generate a reflection (follow-up) search query and execute search."""

    def __init__(self, ctx: ReviewContext):
        self.ctx = ctx

    def __call__(self, state: ReviewGraphState) -> dict:
        idx = state["current_paragraph_index"]
        para = state["paragraphs"][idx]
        count = state.get("current_reflection_count", 0)
        max_ref = state.get("max_reflections", 3)
        logger.info(f"  - 反思 {count+1}/{max_ref}...")

        reflection_input = {
            "title": para["title"],
            "content": para["content"],
            "paragraph_latest_state": para.get("research", {}).get("latest_summary", ""),
        }
        try:
            out = self.ctx.llm_client.structured_invoke(
                SYSTEM_PROMPT_REFLECTION, json.dumps(reflection_input, ensure_ascii=False),
                SearchOutput,
            )
        except Exception:
            logger.exception("结构化反思搜索输出失败，使用默认")
            out = SearchOutput(search_query="商品口碑补充分析", search_tool="get_product_reviews", reasoning="默认反思搜索")

        search_query = out.search_query
        search_tool = out.search_tool
        logger.info(f"    反思查询: {search_query}, 工具: {search_tool}")

        search_results, search_metadata = execute_search_and_convert(
            self.ctx, out.model_dump(), search_query, search_tool
        )

        updated = deepcopy(state["paragraphs"])
        research = updated[idx].setdefault("research", {})
        history = research.setdefault("search_history", [])
        for r in search_results:
            history.append({
                "query": search_query, "url": r.get("url", ""), "title": r.get("title", ""),
                "content": r.get("content", ""), "score": r.get("score"),
                "timestamp": datetime.now().isoformat(),
            })

        research["current_search"] = {
            "query": search_query, "tool": search_tool, "results": search_results,
            "metadata": search_metadata,
        }
        # 累积而非覆盖：空结果/无后处理工具的搜索不会清掉此前真实产出的情感与聚类结果
        research["metadata"] = merge_search_metadata(research.get("metadata"), search_metadata)

        return {"paragraphs": updated}
