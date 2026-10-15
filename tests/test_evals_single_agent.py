"""单 Agent 基线（P2b）测试：DB→webpage 适配器 + 合并引擎端到端跑通。"""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

_proj_root = Path(__file__).resolve().parent.parent
for _p in [str(_proj_root), str(_proj_root / "engines")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.config import Settings  # noqa: E402
from engines.ReviewEngine.tools.search import DBResponse, QueryResult  # noqa: E402
from engines.common.structured_output import (  # noqa: E402
    InitialSummaryOutput,
    ParagraphOutline,
    ReflectionSummaryOutput,
    ReportStructure,
    SearchOutput,
)


def test_db_response_to_webpages_adapts_structured_params():
    from evals.arms.single.context import _db_response_to_web_response
    resp = DBResponse(
        tool_name="get_rating_distribution",
        parameters={"rating_distribution": {4: 50, 5: 35}},
        results=[], results_count=0,
    )
    web = _db_response_to_web_response(resp)
    assert len(web.webpages) == 1
    assert web.webpages[0].name == "评分分布"
    assert "50" in web.webpages[0].snippet


def test_db_response_to_webpages_adapts_results():
    from evals.arms.single.context import _db_response_to_web_response
    from datetime import datetime
    resp = DBResponse(
        tool_name="get_product_reviews",
        parameters={},
        results=[QueryResult(
            platform="amazon", content_type="review", title_or_content="续航一般",
            author_nickname="用户1", url="https://x", publish_time=datetime.now(),
            engagement={"helpful_vote": 3}, hotness_score=0.8, source_table="review",
        )],
        results_count=1,
    )
    web = _db_response_to_web_response(resp)
    assert len(web.webpages) == 1
    assert web.webpages[0].snippet == "续航一般"
    assert web.webpages[0].url == "https://x"


class _Webpage:
    def __init__(self):
        self.name = "page"
        self.url = "https://x"
        self.snippet = "竞品价格 50 至 90 美元"
        self.date_last_crawled = None


class _FakeAgency:
    def comprehensive_search(self, query, max_results=10):
        return SimpleNamespace(webpages=[_Webpage()])


def _fake_structured(system, user, output_model, **kw):
    if output_model is ReportStructure:
        return ReportStructure(paragraphs=[ParagraphOutline(title="口碑概况", content="评分与评论")])
    if output_model is SearchOutput:
        return SearchOutput(search_query="q", search_tool="comprehensive_search", reasoning="r")
    if output_model is InitialSummaryOutput:
        return InitialSummaryOutput(paragraph_latest_state="## 口碑概况\n平均评分 4.2")
    if output_model is ReflectionSummaryOutput:
        return ReflectionSummaryOutput(updated_paragraph_latest_state="## 口碑概况\n平均评分 4.2（更新）")
    raise AssertionError(f"意外的 output_model: {output_model!r}")


def test_single_engine_runs_end_to_end(tmp_path):
    from engines.CompetitorEngine.llms import LLMClient
    from evals.arms.single.agent import run_research

    config = Settings(
        COMPETITOR_ENGINE_API_KEY="sk-test",
        COMPETITOR_ENGINE_MODEL_NAME="test-model",
        COMPETITOR_ENGINE_BASE_URL="https://test",
        MAX_REFLECTIONS=0,
        MAX_CONTENT_LENGTH=500000,
        SEARCH_CONTENT_MAX_LENGTH=20000,
        OUTPUT_DIR=str(tmp_path),
        ENABLE_CLUSTERING=False,
        SENTIMENT_ANALYSIS_ENABLED=False,
    )
    llm = LLMClient(api_key="sk-test", model_name="test-model", base_url="https://test")
    patches = [
        patch.object(llm, "structured_invoke", side_effect=_fake_structured),
        patch.object(llm, "stream_invoke_to_string", return_value="# 报告\n\n正文"),
    ]
    for p in patches:
        p.start()
    try:
        result = run_research("测试耳机", config, llm, _FakeAgency(), max_reflections=0)
    finally:
        for p in patches:
            p.stop()

    assert result["is_completed"] is True
    assert result["final_report"]
    assert result["paragraphs"], "应产出段落"
