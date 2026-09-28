"""
LangGraph node: generate report structure from query.
"""

from loguru import logger

from engines.common.structured_output import ReportStructure
from ..state import ReviewGraphState
from ..prompts import SYSTEM_PROMPT_REPORT_STRUCTURE
from ..context import ReviewContext


class GenerateStructureNode:
    """Generate the report structure (paragraph list) from the user's query."""

    def __init__(self, ctx: ReviewContext):
        self.ctx = ctx

    def __call__(self, state: ReviewGraphState) -> dict:
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
        """结构生成彻底失败时的兜底大纲。

        旧版只给「研究概述 / 深度分析」2 段且内容描述空洞，报告会直接退化成
        两段泛泛而谈；这里按口碑分析的标准维度给全 5 段，与
        ``SYSTEM_PROMPT_REPORT_STRUCTURE`` 的要求保持一致。
        """
        return [
            {
                "title": "商品整体口碑概览：评分分布、情感倾向与热度",
                "content": "呈现样本量、评论覆盖时间范围、真实星级或情感折算星级分布、正/中/负情感占比与 NPS，并标注数据口径。",
            },
            {
                "title": "用户好评分析：核心亮点、场景认可与复购动机",
                "content": "按音质、降噪、续航、佩戴、连接、通话、性价比等维度拆解好评点，引用典型好评原文并统计各维度正向提及率。",
            },
            {
                "title": "用户差评归因：质量、物流、价格与售后问题",
                "content": "把负面评价按产品品控、功能未达预期、佩戴不适、物流包装、客服售后、价格与描述不符归类，给出占比与典型案例。",
            },
            {
                "title": "竞品对比：同价位段评分、价格与口碑差异",
                "content": "选取同品类同价位竞品，对比评分、评论量、价格、核心卖点与好评/差评高频词，指出差异化优势与劣势。",
            },
            {
                "title": "购买建议与风险提示：适合人群、性价比与避坑",
                "content": "给出分人群推荐结论、性价比判断、建议入手价与促销节点，并列出售后、批次、渠道等购买风险。",
            },
        ]
