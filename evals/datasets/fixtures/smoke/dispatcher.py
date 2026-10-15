"""smoke fixture —— 零成本的手写 fake，证明骨架端到端可跑。

fake 引擎报告按通道给出**固定**句子，与 ``datasets/smoke.yaml`` 答案键的 finding
语句保持一致，使覆盖率指标可解释：

- review 报告命中 only-review finding；
- competitor 报告命中 only-competitor finding；
- trend 报告命中 only-trend finding。

report_fn 把传入的引擎报告直接拼接成最终报告 markdown，因此覆盖率精确反映子集构成。
"""

from __future__ import annotations

from typing import Callable

_ENGINE_REPORTS = {
    "review": (
        "# 口碑分析\n\n"
        "平均评分落在 4.0 至 4.4 之间。\n\n"
        "负面评价集中在续航方面，用户普遍反映电池不耐用。\n"
    ),
    "competitor": (
        "# 竞品分析\n\n"
        "主流竞品价格区间为 50 至 90 美元。\n\n"
        "同价位竞品在降噪与续航上各有取舍。\n"
    ),
    "trend": (
        "# 趋势分析\n\n"
        "该品类近一年搜索热度上升。\n\n"
        "季节性需求在年末达到峰值。\n"
    ),
}


def run_engine_fn(
    engine: str,
    query: str,
    config,
    llm_client,
    search_agency=None,
    progress_callback=None,
) -> dict:
    report = _ENGINE_REPORTS.get(engine, "")
    return {
        "final_report": report,
        "report_title": f"{engine} report",
        "is_completed": True,
        "paragraphs": [],
    }


def report_fn(query: str, reports: list, config, forum_logs: str = "") -> dict:
    parts = [f"# 报告：{query}"]
    for r in reports:
        if r and r.strip():
            parts.append(r.strip())
    md = "\n\n".join(parts)
    document_ir = {"metadata": {"title": query, "query": query}, "chapters": []}
    return {
        "document_ir": document_ir,
        "final_report_md": md,
        "report_id": "smoke",
        "html_content": "",
    }


def run_single_engine_fn(
    arm_name: str,
    query: str,
    config,
    llm_client,
    search_agency=None,
    review_db=None,
    progress_callback=None,
    max_reflections: int = 0,
) -> dict:
    """单 Agent 基线（smoke）：返回覆盖三通道的报告，证明合并 Agent 能拿到全部证据。"""
    report = "\n\n".join(_ENGINE_REPORTS[e] for e in ("review", "competitor", "trend"))
    return {
        "final_report": report,
        "report_title": f"{arm_name} report",
        "is_completed": True,
        "paragraphs": [],
    }


def make_engine_runner() -> Callable:
    return run_engine_fn


def make_report_runner() -> Callable:
    return report_fn
