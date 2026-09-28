"""
LangGraph node: generate report structure from query.
"""

from loguru import logger

from engines.common.structured_output import ReportStructure
from ..state import CompetitorGraphState
from ..prompts import SYSTEM_PROMPT_REPORT_STRUCTURE
from ..context import CompetitorContext


class GenerateStructureNode:
    """Generate the report structure (paragraph list) from the user's query."""

    def __init__(self, ctx: CompetitorContext):
        self.ctx = ctx

    def __call__(self, state: CompetitorGraphState) -> dict:
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
            structure = self._default_structure()

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
    def _default_structure() -> list:
        """结构生成彻底失败时的兜底大纲（与竞品分析提示词的五段覆盖一致）。"""
        return [
            {
                "title": "竞品市场概况",
                "content": "界定品类与细分市场，给出市场规模、增速、渠道占比、价格带分布与主要品牌份额，标注来源与统计口径。",
            },
            {
                "title": "主要竞品参数与价格对比",
                "content": "按子品类列出代表竞品的核心参数（芯片/编解码/降噪/续航/防水/重量/延迟/生态）与到手价，做成参数价格对照表。",
            },
            {
                "title": "品牌与媒体报道",
                "content": "梳理各品牌定位、渠道策略、新品节奏与媒体评测结论、获奖及争议事件，标注来源与时点。",
            },
            {
                "title": "用户口碑差异",
                "content": "对比各竞品的评分、评论量、高频好评与差评关键词，并分析参数表现与口碑的背离点。",
            },
            {
                "title": "竞争格局与机会点",
                "content": "给出优劣势矩阵、壁垒与价格战风险判断，识别价格带空位与差异化机会，并提出可执行的应对建议与监控指标。",
            },
        ]
