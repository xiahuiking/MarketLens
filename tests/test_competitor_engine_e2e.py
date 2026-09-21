"""
端到端测试：CompetitorEngine

用 mock 替换全部外部依赖（LLM API、搜索 API），只验证 research() 的外部契约。

mock 必须打在节点**真正调用**的方法上，否则测试会真的联网：
  - LLMClient.structured_invoke          —— 结构/查询/总结类节点
  - LLMClient.stream_invoke_to_string    —— format_report 最终成文
  - CompetitorContext.execute_search     —— 搜索引擎
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# ── 先把项目根路径和 engines/ 加入 sys.path ─────────────────
_proj_root = Path(__file__).resolve().parent.parent
for _p in [str(_proj_root), str(_proj_root / "engines")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from engines.common.structured_output import (  # noqa: E402
    InitialSummaryOutput,
    ReflectionSummaryOutput,
    ReportStructure,
    SearchOutput,
)


# ── 假的外部依赖 ─────────────────────────────────────────────

_FINAL_REPORT = (
    "# 深度研究报告\n\n"
    "## 市场概况\n2025年市场整体表现良好。\n\n"
    "## 竞争格局\n主要竞争对手包括A、B、C三家。"
)


def _fake_structured(system_prompt, user_prompt, output_model, **kwargs):
    """按节点请求的 output_model 返回对应的假结构化结果。

    比"按调用序号返回字符串"更稳：节点调整顺序不会让 mock 错位，
    而出现未预期的模型时会立刻失败（说明契约变了，测试需要同步）。
    """
    if output_model is ReportStructure:
        return ReportStructure(paragraphs=[
            {"title": "市场概况", "content": "分析市场整体表现"},
            {"title": "竞争格局", "content": "分析主要竞争对手"},
        ])
    if output_model is SearchOutput:
        return SearchOutput(
            search_query="测试查询", search_tool="comprehensive_search", reasoning="覆盖测试",
        )
    if output_model is InitialSummaryOutput:
        return InitialSummaryOutput(paragraph_latest_state="## 测试段落\n这是初始总结。")
    if output_model is ReflectionSummaryOutput:
        return ReflectionSummaryOutput(
            updated_paragraph_latest_state="## 测试段落（更新）\n这是反思后的总结。",
        )
    raise AssertionError(f"未预期的结构化输出模型: {output_model}")


def _fake_bocha_response():
    """构造假的 BochaResponse（搜索引擎返回值，消费方读取 .webpages）。"""
    from CompetitorEngine.tools.search import BochaResponse, WebpageResult
    return BochaResponse(
        query="test",
        answer="模拟的 AI 总结",
        webpages=[
            WebpageResult(
                name=f"搜索结果{i}",
                url=f"https://example.com/{i}",
                snippet=f"这是第{i}条搜索结果的摘要内容。",
            )
            for i in range(1, 4)
        ],
    )


_AGENT_CONFIG = {
    "COMPETITOR_ENGINE_API_KEY": "sk-test-fake-key",
    "COMPETITOR_ENGINE_MODEL_NAME": "test-model",
    "COMPETITOR_ENGINE_BASE_URL": "https://test.api.com",
    "BOCHA_API_KEY": "test-bocha-key",
    "MAX_REFLECTIONS": 2,
    "SEARCH_CONTENT_MAX_LENGTH": 20000,
}


@pytest.fixture
def agent():
    """创建 mock 好外部依赖的 CompetitorEngine runner。"""
    from types import SimpleNamespace

    from CompetitorEngine.agent import run_research
    from CompetitorEngine.llms import LLMClient
    from CompetitorEngine.tools import BochaMultimodalSearch
    from CompetitorEngine.utils.config import Settings

    config = Settings(OUTPUT_DIR="/tmp/test_competitor_reports", **_AGENT_CONFIG)
    llm_client = LLMClient(
        api_key=config.COMPETITOR_ENGINE_API_KEY,
        model_name=config.COMPETITOR_ENGINE_MODEL_NAME,
        base_url=config.COMPETITOR_ENGINE_BASE_URL,
    )
    search_agency = BochaMultimodalSearch(api_key="test-bocha-key")

    patches = [
        patch("CompetitorEngine.llms.base.LLMClient.structured_invoke", side_effect=_fake_structured),
        patch("CompetitorEngine.llms.base.LLMClient.stream_invoke_to_string", return_value=_FINAL_REPORT),
        patch("CompetitorEngine.context.CompetitorContext.execute_search", return_value=_fake_bocha_response()),
    ]
    for p in patches:
        p.start()

    runner = SimpleNamespace()
    runner.config = config
    runner.llm_client = llm_client
    runner.progress_callback = None
    runner.research = lambda query, save_report=True: run_research(
        query, config, llm_client, search_agency, runner.progress_callback, save_report,
    )

    yield runner

    for p in patches:
        p.stop()


class TestCompetitorEngineBehavior:
    """CompetitorEngine 行为级端到端测试，只验证外部契约。"""

    def test_research_returns_non_empty_markdown(self, agent):
        """research() 返回非空 Markdown 文本。"""
        result = agent.research("测试查询", save_report=False)
        report = result["final_report"]
        assert report
        assert isinstance(report, str)
        assert len(report) > 50
        assert report.startswith("#"), "报告应以 Markdown 标题开头"

    def test_research_with_chinese_query(self, agent):
        """中文查询正常产出报告。"""
        result = agent.research("人工智能对教育的影响", save_report=False)
        assert result["final_report"] and len(result["final_report"]) > 50

    def test_research_save_report_creates_file(self, agent, tmp_path):
        """save_report=True 时 .md 报告写入磁盘。"""
        agent.config.OUTPUT_DIR = str(tmp_path)
        result = agent.research("测试保存")
        report = result["final_report"]
        assert report
        md_files = [f for f in tmp_path.iterdir() if f.suffix == ".md"]
        assert len(md_files) > 0, f"tmp_path 中没有 .md 文件: {list(tmp_path.iterdir())}"
        assert md_files[0].read_text(encoding="utf-8") == report

    def test_search_failure_propagates_error(self, agent):
        """搜索工具抛出异常时 research() 向上传播。"""
        with patch("CompetitorEngine.context.CompetitorContext.execute_search", side_effect=RuntimeError("API unreachable")):
            with pytest.raises(RuntimeError):
                agent.research("测试", save_report=False)

    def test_llm_garbage_still_returns_report(self, agent):
        """LLM 返回非法内容时仍能产出报告，不崩溃。"""
        llm_patch = patch.object(
            agent.llm_client, "stream_invoke_to_string",
            return_value="这是一段无法解析的文本。",
        )
        llm_patch.start()
        try:
            result = agent.research("测试", save_report=False)
            assert result["final_report"] and isinstance(result["final_report"], str) and len(result["final_report"]) > 0
        finally:
            llm_patch.stop()

    def test_progress_callback_receives_events(self, agent):
        """progress_callback 在 research 期间被触发。"""
        events = []
        agent.progress_callback = events.append
        result = agent.research("测试查询")
        assert result["final_report"]
        statuses = [e.get("status") for e in events if "status" in e]
        assert "structure" in statuses
        assert "processing" in statuses
        assert "finalizing" in statuses

    def test_research_without_progress_callback(self, agent):
        """progress_callback 为 None（默认用法）时不应抛 TypeError。"""
        agent.progress_callback = None
        result = agent.research("测试查询", save_report=False)
        assert result["final_report"]
