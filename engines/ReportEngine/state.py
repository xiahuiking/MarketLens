"""ReportEngine LangGraph 状态定义。"""
from typing import Any, Callable, Dict, List, Optional
from typing_extensions import TypedDict


class ReportGraphState(TypedDict, total=False):
    """ReportEngine 的 LangGraph 状态"""
    # Input
    query: str
    reports: List[Any]
    normalized_reports: dict
    forum_logs: str
    custom_template: str
    save_report: bool
    report_id: str
    stream_handler: Optional[Callable]
    # 可视化（阶段5）：结构化数据摘要 + 预构建图表 widget
    visualization_bundles: list
    prebuilt_widgets: list
    # Pipeline
    template_result: dict
    template_sections: list
    layout_design: dict
    word_plan: dict
    generation_context: dict
    template_overview: dict
    # Output
    chapters: list
    document_ir: dict
    html_content: str
    saved_files: dict
    error: Optional[str]
