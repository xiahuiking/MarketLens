"""P0 核心测试：host=off 下引擎输出彼此独立（V 是干净集合函数）。

在 host 回灌开关打开/关闭两种状态下，把一条 host 发言注入缓存，跑一遍 ReviewEngine
（LLM 用 mock 记录「段落提示词里是否含主持人发言」），断言：

- 开关关闭 → 没有任何 summary 节点的提示词注入 host 发言；
- 开关打开 → summary 节点提示词确实注入了 host 发言。

这直接验证了 6 个 summary 节点改用 ``read_host_speech_for_node`` 后，开关真的切断了
回灌回路（DESIGN 2.2）。
"""

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

_proj_root = Path(__file__).resolve().parent.parent
for _p in [str(_proj_root), str(_proj_root / "engines")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.config import Settings  # noqa: E402
from app.utils import forum_reader  # noqa: E402
from engines.ReviewEngine.agent import run_research  # noqa: E402
from engines.ReviewEngine.context import ReviewContext  # noqa: E402
from engines.ReviewEngine.llms import LLMClient  # noqa: E402
from engines.ReviewEngine.tools.search import DBResponse, QueryResult  # noqa: E402
from engines.common.structured_output import (  # noqa: E402
    InitialSummaryOutput,
    ParagraphOutline,
    ReflectionSummaryOutput,
    ReportStructure,
    SearchOutput,
)


def _fake_db_response() -> DBResponse:
    return DBResponse(
        tool_name="get_product_reviews",
        parameters={"query": "q"},
        results=[QueryResult(
            platform="amazon", content_type="review", title_or_content="质量不错",
            author_nickname="u", url="https://x", publish_time=datetime.now(),
            engagement={"helpful_vote": 1}, hotness_score=0.9, source_table="review",
        )],
        results_count=1,
    )


def _fake_structured(system, user, output_model, **kw):
    if output_model is ReportStructure:
        return ReportStructure(paragraphs=[ParagraphOutline(title="市场概况", content="x")])
    if output_model is SearchOutput:
        return SearchOutput(search_query="q", search_tool="get_product_reviews", reasoning="r")
    has_host = "主持人最新总结" in user
    marker = "HOST" if has_host else "NOHOST"
    if output_model is InitialSummaryOutput:
        return InitialSummaryOutput(paragraph_latest_state=marker)
    if output_model is ReflectionSummaryOutput:
        return ReflectionSummaryOutput(updated_paragraph_latest_state=marker)
    raise AssertionError(f"意外的 output_model: {output_model!r}")


@pytest.fixture(autouse=True)
def _reset():
    yield
    forum_reader.set_host_feedback_enabled(True)
    forum_reader._latest_host_speech = None
    forum_reader.reset_host_trace()


def _run(enabled: bool):
    forum_reader.set_host_feedback_enabled(enabled)
    forum_reader._on_forum_message("forum_message", {"type": "host", "content": "主持人总结内容"})

    config = Settings(
        REVIEW_ENGINE_API_KEY="sk-test",
        REVIEW_ENGINE_MODEL_NAME="test-model",
        REVIEW_ENGINE_BASE_URL="https://test",
        MAX_REFLECTIONS=1,
        MAX_CONTENT_LENGTH=500000,
        OUTPUT_DIR="data/evals/_tmp_host_indep",
    )
    llm = LLMClient(api_key="sk-test", model_name="test-model", base_url="https://test")
    patches = [
        patch.object(llm, "structured_invoke", side_effect=_fake_structured),
        patch.object(llm, "stream_invoke_to_string", return_value="# report\n\nbody"),
        patch.object(ReviewContext, "execute_search", return_value=_fake_db_response()),
    ]
    for p in patches:
        p.start()
    try:
        result = run_research("测试查询", config, llm, save_report=False)
    finally:
        for p in patches:
            p.stop()

    summaries = []
    for p in result.get("paragraphs", []):
        s = (p.get("research", {}) or {}).get("latest_summary", "")
        if s:
            summaries.append(s)
    return summaries


def test_host_off_blocks_injection_at_node_level():
    summaries = _run(enabled=False)
    assert summaries, "应产出至少一个 summary"
    assert all(s == "NOHOST" for s in summaries), summaries


def test_host_on_allows_injection_at_node_level():
    summaries = _run(enabled=True)
    assert summaries, "应产出至少一个 summary"
    assert all(s == "HOST" for s in summaries), summaries
