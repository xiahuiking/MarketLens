"""单 Agent 基线节点。

生成结构 / 首次检索 / 反思检索三个节点用合并后的 12 工具提示词（本包 prompts.py），
其余节点（总结/格式化/落盘）直接复用竞品引擎（同构控制流，DESIGN 3.2-B）。
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime

from loguru import logger

from engines.common.structured_output import ReportStructure, SearchOutput
from engines.CompetitorEngine.nodes._search_utils import execute_search_and_convert

# 复用竞品引擎的内容纪律/落盘节点（不涉及工具选择）
from engines.CompetitorEngine.nodes.initial_summary import InitialSummaryNode
from engines.CompetitorEngine.nodes.reflection_summary import ReflectionSummaryNode
from engines.CompetitorEngine.nodes.format_report import FormatReportNode
from engines.CompetitorEngine.nodes.save_report import SaveReportNode

from .context import SingleContext
from .prompts import (
    SYSTEM_PROMPT_FIRST_SEARCH,
    SYSTEM_PROMPT_REFLECTION,
    SYSTEM_PROMPT_REPORT_STRUCTURE,
)


class GenerateStructureNode:
    """从 query 生成覆盖口碑/竞品/趋势的报告结构。"""

    def __init__(self, ctx: SingleContext):
        self.ctx = ctx

    def __call__(self, state: dict) -> dict:
        query = state["query"]
        if self.ctx.progress_callback:
            self.ctx.progress_callback({"status": "structure", "message": "正在生成报告结构...", "progress_pct": 10})
        try:
            result = self.ctx.llm_client.structured_invoke(
                SYSTEM_PROMPT_REPORT_STRUCTURE, query, ReportStructure,
            )
            structure = result.paragraphs if not isinstance(result, dict) else result.get("paragraphs", [])
        except Exception:
            logger.exception("结构化输出失败，使用默认结构")
            structure = []

        if not structure:
            structure = self._default_structure()

        paragraphs = []
        for p in structure:
            if isinstance(p, dict):
                title, content = p.get("title", ""), p.get("content", "")
            else:
                title, content = p.title, p.content
            paragraphs.append({
                "title": title, "content": content,
                "research": {"search_history": [], "latest_summary": "", "is_completed": False, "reflection_iteration": 0},
            })

        return {
            "report_title": f"关于'{query}'的深度研究报告",
            "paragraphs": paragraphs,
            "current_paragraph_index": 0,
            "current_reflection_count": 0,
        }

    @staticmethod
    def _default_structure() -> list:
        return [
            {"title": "商品口碑概况", "content": "评分分布、评论量、高频好评与差评点。"},
            {"title": "竞品参数与价格对比", "content": "同类竞品的核心参数、价格与口碑差异。"},
            {"title": "品类趋势与机会点", "content": "热度变化、季节特征与差异化机会。"},
        ]


def _search_node_common(ctx: SingleContext, search_output: dict, search_query: str, search_tool: str):
    kwargs = {}
    if search_tool in ("comprehensive_search", "web_search_only"):
        kwargs["max_results"] = 10
    return execute_search_and_convert(ctx, search_output, search_query, search_tool)


class InitialSearchNode:
    def __init__(self, ctx: SingleContext):
        self.ctx = ctx

    def __call__(self, state: dict) -> dict:
        idx = state["current_paragraph_index"]
        paragraphs = state["paragraphs"]
        para = paragraphs[idx]
        search_input = {"title": para["title"], "content": para["content"]}
        try:
            out = self.ctx.llm_client.structured_invoke(
                SYSTEM_PROMPT_FIRST_SEARCH, json.dumps(search_input, ensure_ascii=False),
                SearchOutput,
            )
        except Exception:
            logger.exception("结构化搜索输出失败，使用默认")
            out = SearchOutput(search_query="相关主题研究", search_tool="comprehensive_search", reasoning="默认搜索")

        search_query, search_tool = out.search_query, out.search_tool
        search_results = execute_search_and_convert(self.ctx, out.model_dump(), search_query, search_tool)

        updated = deepcopy(paragraphs)
        research = updated[idx].setdefault("research", {})
        history = research.setdefault("search_history", [])
        for r in search_results:
            history.append({
                "query": search_query, "url": r.get("url", ""),
                "title": r.get("title", ""), "content": r.get("content", ""),
                "score": r.get("score"), "paragraph_title": para["title"],
                "search_tool": search_tool, "has_result": True,
                "timestamp": datetime.now().isoformat(),
            })
        research["current_search"] = {
            "query": search_query, "tool": search_tool, "results": search_results,
        }
        return {"paragraphs": updated}


class ReflectionSearchNode:
    def __init__(self, ctx: SingleContext):
        self.ctx = ctx

    def __call__(self, state: dict) -> dict:
        idx = state["current_paragraph_index"]
        para = state["paragraphs"][idx]
        count = state.get("current_reflection_count", 0)
        max_ref = state.get("max_reflections", 2)
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
            out = SearchOutput(search_query="深度研究补充信息", search_tool="comprehensive_search", reasoning="默认反思搜索")

        search_query, search_tool = out.search_query, out.search_tool
        search_results = execute_search_and_convert(self.ctx, out.model_dump(), search_query, search_tool)

        updated = deepcopy(state["paragraphs"])
        research = updated[idx].setdefault("research", {})
        history = research.setdefault("search_history", [])
        for r in search_results:
            history.append({
                "query": search_query, "url": r.get("url", ""),
                "title": r.get("title", ""), "content": r.get("content", ""),
                "score": r.get("score"), "paragraph_title": para["title"],
                "search_tool": search_tool, "has_result": True,
                "timestamp": datetime.now().isoformat(),
            })
        research["current_search"] = {
            "query": search_query, "tool": search_tool, "results": search_results,
        }
        return {"paragraphs": updated}


__all__ = [
    "GenerateStructureNode",
    "InitialSearchNode",
    "InitialSummaryNode",
    "ReflectionSearchNode",
    "ReflectionSummaryNode",
    "FormatReportNode",
    "SaveReportNode",
]
