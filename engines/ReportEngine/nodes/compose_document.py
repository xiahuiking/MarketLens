"""LangGraph node: compose document IR from chapters."""

from ..state import ReportGraphState


class ComposeDocumentNode:
    def __init__(self, ctx):
        self.ctx = ctx

    def __call__(self, state: ReportGraphState) -> dict:
        report_id = state.get("report_id", "")
        layout = state.get("layout_design", {})
        word_plan = state.get("word_plan", {})
        query = state["query"]
        template_result = state.get("template_result", {})
        template_overview = state.get("template_overview", {})
        chapters = state.get("chapters", [])
        prebuilt_widgets = state.get("prebuilt_widgets", []) or []

        manifest = {
            "query": query,
            "title": layout.get("title") or (f"{query} - 电商竞品分析报告" if query else template_result.get("template_name")),
            "templateName": template_result.get("template_name"),
            "toc": {"depth": 3, "autoNumbering": True, "title": layout.get("tocTitle") or "目录"},
            "hero": layout.get("hero"),
        }
        if layout.get("themeTokens"):
            manifest["themeTokens"] = layout["themeTokens"]
        if layout.get("tocPlan"):
            manifest["toc"]["customEntries"] = layout["tocPlan"]
        if word_plan.get("globalGuidelines"):
            manifest["wordPlan"] = {"globalGuidelines": word_plan["globalGuidelines"]}

        # 阶段5：把预构建的数据可视化图表作为“关键数据可视化”章节追加到报告末尾
        if prebuilt_widgets:
            chapters = list(chapters) + [_build_visualization_chapter(prebuilt_widgets)]

        doc_ir = self.ctx.document_composer.build_document(report_id, manifest, chapters)
        return {"document_ir": doc_ir}


def _build_visualization_chapter(widgets) -> dict:
    """把可视化 widget 列表包装成一个独立的章节 IR。"""
    blocks = [
        {
            "type": "heading",
            "level": 1,
            "text": "关键数据可视化",
            "anchor": "section-visualization",
            "numbering": "数据附录",
        },
        {
            "type": "paragraph",
            "inlines": [
                {"text": "以下图表基于本地电商评论数据库自动生成，反映该商品/品类真实的评分、情感、趋势与竞品对比数据。"}
            ],
        },
    ]
    blocks.extend(widgets)
    return {
        "chapterId": "S_VISUALIZATION",
        "title": "关键数据可视化",
        "anchor": "section-visualization",
        "order": 9999,
        "blocks": blocks,
    }
