"""LangGraph node: generate report structure from query."""

from loguru import logger

from engines.common.structured_output import ReportStructure
from ..state import TrendGraphState
from ..prompts import SYSTEM_PROMPT_REPORT_STRUCTURE
from ..context import TrendContext


class GenerateStructureNode:
    def __init__(self, ctx: TrendContext):
        self.ctx = ctx

    def __call__(self, state: TrendGraphState) -> dict:
        query = state["query"]
        if self.ctx.progress_callback:
            self.ctx.progress_callback({"status": "structure", "message": "正在生成报告结构...", "progress_pct": 10})
        logger.info(f"\n{'=' * 60}\n[LangGraph] 生成报告结构: {query}")

        try:
            result = self.ctx.llm_client.structured_invoke(
                SYSTEM_PROMPT_REPORT_STRUCTURE, query, ReportStructure,
            )
            # result 可能是 ReportStructure 模型，也可能是 dict，统一取段落列表
            structure = result.paragraphs if not isinstance(result, dict) else result.get("paragraphs", [])
        except Exception:
            logger.exception("结构化输出失败，使用默认结构")
            structure = []

        if not structure:
            structure = self._default()

        paragraphs = []
        for p in structure:
            # 兼容段落为 pydantic 对象（正常路径）或 dict（默认结构回退路径）
            if isinstance(p, dict):
                title, content = p.get("title", ""), p.get("content", "")
            else:
                title, content = p.title, p.content
            paragraphs.append({
                "title": title, "content": content,
                "research": {"search_history": [], "latest_summary": "", "is_completed": False, "reflection_iteration": 0},
            })

        msg = f"报告结构已生成，共 {len(paragraphs)} 个段落:"
        for i, p in enumerate(paragraphs, 1):
            msg += f"\n  {i}. {p['title']}"
        logger.info(msg)
        return {
            "report_title": f"关于'{query}'的深度研究报告",
            "paragraphs": paragraphs,
            "current_paragraph_index": 0,
            "current_reflection_count": 0,
        }

    @staticmethod
    def _default() -> list:
        """结构生成彻底失败时的兜底大纲（与趋势分析的五段覆盖一致）。"""
        return [
            {
                "title": "品类范围与市场概况",
                "content": "界定品类边界与统计口径，给出市场规模、增速、渠道结构与季节性的最新数据与来源。",
            },
            {
                "title": "价格带分布与迁移",
                "content": "划分价格带并给出各带占比、均价走势与迁移方向，说明促销节点对价格带的影响。",
            },
            {
                "title": "技术趋势与新品节奏",
                "content": "梳理关键技术演进（连接、编解码、降噪、AI 功能等）与新品发布节奏，标注时间线。",
            },
            {
                "title": "用户关注点演变",
                "content": "统计各时点用户关注关键词及其热度变化，识别关注点的结构性迁移而非季节性波动。",
            },
            {
                "title": "趋势研判与机会点",
                "content": "基于前述证据给出未来 6-12 个月的趋势研判、机会窗口与风险提示，并列出待跟踪指标。",
            },
        ]
