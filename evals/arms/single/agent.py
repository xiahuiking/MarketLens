"""单 Agent 基线入口 —— run_research()。

``max_reflections`` 可注入：``single_merged`` 传 0（无反思 = 未匹配预算对照），
``single_selfreflect`` 传 2（反思烧预算 = 算力匹配基线）。
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, Optional

from loguru import logger

from .context import SingleContext
from .graph import build_single_graph


def run_research(
    query: str,
    config: Any,
    llm_client,
    search_agency,
    review_db: Any = None,
    progress_callback: Optional[Callable] = None,
    save_report: bool = True,
    max_reflections: Optional[int] = None,
) -> Dict[str, Any]:
    """执行单 Agent 综合研究，返回 {final_report, paragraphs, ...}。"""
    logger.info(f"\n{'=' * 60}\n开始单 Agent 研究: {query}\n{'=' * 60}")

    ctx = SingleContext(
        llm_client=llm_client,
        config=config,
        search_agency=search_agency,
        review_db=review_db,
        progress_callback=progress_callback,
    )

    os.makedirs(config.OUTPUT_DIR, exist_ok=True)

    try:
        graph = build_single_graph(ctx)
        mr = config.MAX_REFLECTIONS if max_reflections is None else max_reflections
        initial_state = {"query": query, "save_report": save_report, "max_reflections": mr}
        result = graph.invoke(initial_state, {"recursion_limit": 100})
        logger.info("单 Agent 研究完成！")
        return {
            "final_report": result.get("final_report", ""),
            "report_title": result.get("report_title", ""),
            "is_completed": result.get("is_completed", False),
            "paragraphs": result.get("paragraphs", []),
        }
    except Exception as e:
        logger.exception(f"研究过程中发生错误: {str(e)}")
        raise
