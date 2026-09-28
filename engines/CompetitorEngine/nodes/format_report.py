"""
LangGraph node: format final report from completed paragraphs.
"""

import json

from loguru import logger

from ..state import CompetitorGraphState
from ..prompts import SYSTEM_PROMPT_REPORT_FORMATTING
from engines.common.report_text import clean_markdown_report


class FormatReportNode:
    """Format the completed paragraph summaries into a final Markdown report."""

    def __init__(self, ctx):
        self.ctx = ctx

    def __call__(self, state: CompetitorGraphState) -> dict:
        self._pc({"status": "finalizing", "message": "正在生成最终报告...", "progress_pct": 90})
        logger.info("\n[步骤 3] 生成最终报告...")
        paragraphs = state["paragraphs"]

        report_data = [{
            "title": p["title"],
            "paragraph_latest_state": p.get("research", {}).get("latest_summary", ""),
        } for p in paragraphs]

        try:
            message = json.dumps(report_data, ensure_ascii=False)
            raw = self.ctx.llm_client.stream_invoke_to_string(SYSTEM_PROMPT_REPORT_FORMATTING, message)
        except Exception as e:
            logger.warning(f"LLM格式化调用失败，使用备用方法: {e}")
            final_report = self._fallback_format(
                report_data, state.get("report_title", "深度研究报告"))
        else:
            final_report = self._parse_report(raw)
            if not final_report:
                # 空返回不是异常（思考模型可能在 reasoning 阶段就被网关断流），
                # 但绝不产出低质拼装报告冒充成功：显式失败，交由
                # run_engine_task 发布 ENGINE_ERROR，让问题暴露出来。
                raise RuntimeError(
                    "LLM 格式化未产出任何内容（模型很可能在 reasoning 阶段被网关断流）。"
                    "按配置选择显式失败，不生成兜底报告。"
                )

        return {"final_report": final_report, "is_completed": True}

    def _pc(self, data: dict):
        if self.ctx.progress_callback:
            self.ctx.progress_callback(data)

    def _parse_report(self, output: str) -> str:
        """清洗 LLM 输出；为空时返回空串，由调用方决定兜底。

        注意：这里**不能**用 ``remove_reasoning_from_output``——那个函数按
        「第一个 ``{``/``[`` 之前全部丢弃」工作，会把含来源标注（``[媒体]``）的
        Markdown 报告开头整段删掉（实测丢失 50%~80%）。见
        ``engines/common/report_text.py``。
        """
        return clean_markdown_report(output)

    @staticmethod
    def _fallback_format(paragraphs_data: list, report_title: str = "深度研究报告") -> str:
        lines = [f"# {report_title}", "", "---", ""]
        for i, p in enumerate(paragraphs_data, 1):
            content = p.get("paragraph_latest_state", "")
            if content:
                lines.extend([f"## {p.get('title', f'段落 {i}')}", "", content, "", "---", ""])
        if len(paragraphs_data) > 1:
            lines.extend([
                "## 结论", "",
                "本报告通过深度搜索和研究，对相关主题进行了全面分析。"
                "以上各个方面的内容为理解该主题提供了重要参考。", "",
            ])
        return "\n".join(lines)
