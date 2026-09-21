"""
口碑引擎（ReviewEngine）端到端测试。

用 mock 替换外部依赖（LLM 与本地数据库），验证 run_research() 在 LangGraph 流程中完整走通。
不依赖真实 LLM API、数据库或网络。

mock 策略：
- LLMClient.structured_invoke → 按 output_model 返回对应 Pydantic 模型（5 个节点使用）
- LLMClient.stream_invoke_to_string → 返回固定 Markdown（format_report 节点使用）
- InsightContext.execute_search → 返回假的 DBResponse（数据库探测 + 各搜索节点使用）
"""

import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

# ── 先把项目根路径和 engines/ 加入 sys.path ─────────────────
_proj_root = Path(__file__).resolve().parent.parent
for _p in [str(_proj_root), str(_proj_root / "engines")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from engines.ReviewEngine.agent import run_research  # noqa: E402
from engines.ReviewEngine.context import InsightContext  # noqa: E402
from engines.ReviewEngine.tools.search import DBResponse, QueryResult  # noqa: E402
from engines.common.structured_output import (  # noqa: E402
    ReportStructure,
    ParagraphOutline,
    SearchOutput,
    InitialSummaryOutput,
    ReflectionSummaryOutput,
)


# ── 辅助函数：构造假的 DBResponse ────────────────────────────
# 假评论覆盖不同方面（质量/价格/物流/客服）与不同情感（正面/负面/中性），
# 使 mock 更接近真实评论数据，避免所有搜索结果内容雷同。
_FAKE_REVIEWS = [
    ("质量非常不错，做工精细，用了一个月没有任何问题。", 42),
    ("价格偏贵，性价比一般，但东西本身还能接受。", 18),
    ("物流很快，两天就到了，包装严实无破损。", 27),
    ("用了几次就坏了，质量堪忧，不推荐购买。", 55),
    ("中规中矩，没有惊喜也没有明显缺点。", 9),
    ("客服响应及时，退款流程顺畅，体验不错。", 12),
]


def _fake_db_response() -> DBResponse:
    results = [
        QueryResult(
            platform="amazon",
            content_type="review",
            title_or_content=text,
            author_nickname=f"用户{i+1}",
            url=f"https://amazon.com/review/{i}",
            publish_time=datetime.now(),
            engagement={"helpful_vote": helpful},
            hotness_score=round(0.9 - i * 0.12, 2),
            source_table="review",
        )
        for i, (text, helpful) in enumerate(_FAKE_REVIEWS)
    ]
    return DBResponse(
        tool_name="get_product_reviews",
        parameters={"query": "test"},
        results=results,
        results_count=len(results),
    )


# ── 结构化输出 mock：按 output_model 分发 ─────────────────────
def _fake_structured_invoke(system_prompt: str, user_prompt: str, output_model, **kwargs):
    if output_model is ReportStructure:
        return ReportStructure(paragraphs=[
            ParagraphOutline(title="市场概况", content="分析市场整体表现"),
            ParagraphOutline(title="竞争格局", content="分析主要竞争对手"),
        ])
    if output_model is SearchOutput:
        return SearchOutput(
            search_query="商品口碑", search_tool="get_product_reviews", reasoning="测试搜索"
        )
    if output_model is InitialSummaryOutput:
        return InitialSummaryOutput(paragraph_latest_state="## 市场概况\n市场整体表现良好。")
    if output_model is ReflectionSummaryOutput:
        return ReflectionSummaryOutput(
            updated_paragraph_latest_state="## 市场概况（更新）\n市场整体表现良好，增长加速。"
        )
    raise AssertionError(f"意外的 output_model: {output_model!r}")


_FAKE_FINAL_REPORT = (
    "# 深度研究报告\n\n"
    "## 市场概况\n市场整体表现良好。\n\n"
    "## 竞争格局\n主要竞争对手包括 A、B、C。"
)


@pytest.fixture
def agent(tmp_path):
    """创建 mock 好 LLM 与数据库的 runner，每个测试独享一份。"""
    from app.config import Settings
    from engines.ReviewEngine.llms import LLMClient

    config = Settings(
        REVIEW_ENGINE_API_KEY="sk-test-fake-key",
        REVIEW_ENGINE_MODEL_NAME="test-model",
        REVIEW_ENGINE_BASE_URL="https://test.api.com",
        MAX_REFLECTIONS=1,
        MAX_CONTENT_LENGTH=500000,
        MAX_SEARCH_RESULTS_FOR_LLM=0,
        OUTPUT_DIR=str(tmp_path),
    )
    llm_client = LLMClient(
        api_key="sk-test-fake-key",
        model_name="test-model",
        base_url="https://test.api.com",
    )

    patches = [
        patch.object(llm_client, "structured_invoke", side_effect=_fake_structured_invoke),
        patch.object(llm_client, "stream_invoke_to_string", return_value=_FAKE_FINAL_REPORT),
        patch.object(InsightContext, "execute_search", return_value=_fake_db_response()),
    ]
    for p in patches:
        p.start()

    runner = SimpleNamespace()
    runner.config = config
    runner.llm_client = llm_client
    runner.events = []
    runner.output_dir = tmp_path
    runner.research = lambda query, save_report=True, cb=None: run_research(
        query, config, llm_client, cb or runner.events.append, save_report
    )

    yield runner

    for p in patches:
        p.stop()


class TestReviewEngineE2E:
    """ReviewEngine 端到端测试（全 mock 外部依赖）。"""

    def test_import_chain(self):
        from engines.ReviewEngine.graph import build_insight_graph
        from engines.ReviewEngine.state import InsightGraphState
        from engines.ReviewEngine.tools import ProductReviewDB
        assert run_research is not None
        assert build_insight_graph is not None
        assert InsightGraphState is not None
        assert callable(ProductReviewDB)

    def test_research_returns_report(self, agent):
        result = agent.research("测试查询", save_report=False)
        assert result["is_completed"] is True
        assert result["final_report"]
        assert result["final_report"].startswith("#")

    def test_progress_callback_receives_events(self, agent):
        agent.research("测试查询", save_report=False)
        statuses = [e.get("status") for e in agent.events if "status" in e]
        assert "structure" in statuses, f"缺少 structure 事件: {statuses}"
        assert "processing" in statuses, f"缺少 processing 事件: {statuses}"
        assert "finalizing" in statuses, f"缺少 finalizing 事件: {statuses}"

    def test_state_sync(self, agent):
        result = agent.research("测试查询", save_report=False)
        paragraphs = result.get("paragraphs", [])
        assert len(paragraphs) == 2
        for p in paragraphs:
            assert p.get("title")
            research = p.get("research", {})
            assert research.get("is_completed") is True
            assert research.get("latest_summary")
            assert len(research.get("search_history", [])) > 0

    def test_save_report_creates_file(self, agent):
        agent.research("测试保存", save_report=True)
        md_files = list(agent.output_dir.glob("*.md"))
        assert len(md_files) == 1, f"output_dir 中没有 .md 文件: {list(agent.output_dir.iterdir())}"
        assert md_files[0].read_text(encoding="utf-8") == _FAKE_FINAL_REPORT

    def test_db_failure_propagates(self, agent):
        with patch.object(InsightContext, "execute_search", side_effect=RuntimeError("DB unreachable")):
            with pytest.raises(RuntimeError):
                agent.research("测试", save_report=False)


# ── 条件边函数测试（纯函数，零依赖） ──────────────────────────

class TestConditionalEdgeFunctions:
    """直接测试 graph.py 中的条件边函数，不依赖任何 mock。"""

    def test_should_continue_reflection_before_max(self):
        from engines.ReviewEngine.graph import _should_continue_reflection
        from engines.ReviewEngine.state import InsightGraphState

        s = InsightGraphState(current_reflection_count=0, max_reflections=3)
        assert _should_continue_reflection(s) == "reflect_again"
        s["current_reflection_count"] = 2
        assert _should_continue_reflection(s) == "reflect_again"

    def test_should_continue_reflection_at_max(self):
        from engines.ReviewEngine.graph import _should_continue_reflection
        from engines.ReviewEngine.state import InsightGraphState

        s = InsightGraphState(current_reflection_count=3, max_reflections=3)
        assert _should_continue_reflection(s) == "next_paragraph"

    def test_has_more_paragraphs_before_end(self):
        from engines.ReviewEngine.graph import _has_more_paragraphs
        from engines.ReviewEngine.state import InsightGraphState

        s = InsightGraphState(current_paragraph_index=0, paragraphs=[{"title": "a"}, {"title": "b"}])
        assert _has_more_paragraphs(s) == "process_next"

    def test_has_more_paragraphs_at_end(self):
        from engines.ReviewEngine.graph import _has_more_paragraphs
        from engines.ReviewEngine.state import InsightGraphState

        s = InsightGraphState(current_paragraph_index=2, paragraphs=[{"title": "a"}, {"title": "b"}])
        assert _has_more_paragraphs(s) == "all_done"

    def test_has_more_paragraphs_empty(self):
        from engines.ReviewEngine.graph import _has_more_paragraphs
        from engines.ReviewEngine.state import InsightGraphState

        s = InsightGraphState(current_paragraph_index=0, paragraphs=[])
        assert _has_more_paragraphs(s) == "all_done"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
