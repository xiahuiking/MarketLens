"""报告格式化节点的容错测试（对应 "报告生成失败" 事故）。

事故链路：思考模型（deepseek-v4-pro）在网关提前断流时，一个 content chunk 都
不返回；``stream_invoke_to_string`` 返回空串（**不抛异常**），于是
``_parse_report`` 把它变成 "# 报告生成失败" 占位符，而段落摘要里的真实内容
被整份丢弃。

当前策略（按产品选择）：**空返回显式失败，不产出兜底报告**。
- 空 / 纯空白输出 -> 抛 RuntimeError，交由 run_engine_task 发布 ENGINE_ERROR；
- 调用抛异常     -> 仍使用备用方法（原有行为，不回归）；
- 正常输出       -> 仍然使用 LLM 结果；
- LLMClient 空流短重试 -> 有界，且成功后不再重试。

说明：``llm_client`` 内部已对空流做短重试（首次 + 2 次），所以走到节点这一层
仍为空，说明重试也已耗尽，此时再显式失败。
"""

from types import SimpleNamespace

import pytest

PARAGRAPHS = [
    {
        "title": "竞品市场概况",
        "research": {"latest_summary": "中国蓝牙耳机 2025 年出货 12,137 万台，同比增长 6.9%。"},
    },
    {
        "title": "主要竞品参数与价格对比",
        "research": {"latest_summary": "价格带从 99 元到 1999 元不等，开放式增速最快。"},
    },
]

LEGACY_SENTINEL = "报告生成失败"


class _FakeLLM:
    """可控的假 LLM：返回指定内容，或按需抛异常。"""

    def __init__(self, result: str = "", error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls = 0

    def stream_invoke_to_string(self, *args, **kwargs) -> str:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


def _ctx(llm: _FakeLLM) -> SimpleNamespace:
    return SimpleNamespace(llm_client=llm, progress_callback=lambda data: None)


def _state() -> dict:
    return {"paragraphs": PARAGRAPHS, "report_title": "测试报告标题"}


def _competitor_node(llm):
    from engines.CompetitorEngine.nodes.format_report import FormatReportNode
    return FormatReportNode(_ctx(llm))


def _review_node(llm):
    from engines.ReviewEngine.nodes.format_report import FormatReportNode
    return FormatReportNode(_ctx(llm))


def _trend_node(llm):
    from engines.TrendEngine.nodes.format_report import FormatReportNode
    return FormatReportNode(_ctx(llm))


ENGINE_NODES = {
    "competitor": _competitor_node,
    "review": _review_node,
    "trend": _trend_node,
}


class TestEmptyOutputFailsExplicitly:
    """核心用例：空输出必须显式失败，绝不产出兜底报告。"""

    @pytest.mark.parametrize("engine", sorted(ENGINE_NODES))
    def test_empty_output_raises(self, engine):
        node = ENGINE_NODES[engine](_FakeLLM(""))
        with pytest.raises(RuntimeError) as excinfo:
            node(_state())
        message = str(excinfo.value)
        assert "未产出任何内容" in message
        assert "不生成兜底报告" in message

    @pytest.mark.parametrize("engine", sorted(ENGINE_NODES))
    def test_whitespace_only_output_raises(self, engine):
        """纯空白与空串等价（清洗后 strip 为空）。"""
        node = ENGINE_NODES[engine](_FakeLLM("   \n\n  \t "))
        with pytest.raises(RuntimeError):
            node(_state())

    @pytest.mark.parametrize("engine", sorted(ENGINE_NODES))
    def test_no_report_is_returned_on_empty(self, engine):
        """确认空输出不会退化成"拼装报告"或旧占位符。"""
        node = ENGINE_NODES[engine](_FakeLLM(""))
        try:
            result = node(_state())
        except RuntimeError:
            return  # 期望路径
        pytest.fail(
            "空输出不应返回报告，实际返回: "
            f"{str(result.get('final_report'))[:80]!r} / legacy_sentinel="
            f"{LEGACY_SENTINEL in str(result.get('final_report'))}"
        )

    @pytest.mark.parametrize("engine", sorted(ENGINE_NODES))
    def test_empty_state_paragraphs_also_raises(self, engine):
        """即使段落摘要为空也不应静默返回空报告。"""
        node = ENGINE_NODES[engine](_FakeLLM(""))
        empty_state = {"paragraphs": [], "report_title": "空"}
        with pytest.raises(RuntimeError):
            node(empty_state)


class TestNonEmptyOutputUnchanged:
    """正常路径不能被改动破坏。"""

    @pytest.mark.parametrize("engine", sorted(ENGINE_NODES))
    def test_llm_output_is_used(self, engine):
        node = ENGINE_NODES[engine](_FakeLLM("# LLM 产出报告\n\n正文内容。"))
        result = node(_state())
        assert result["is_completed"] is True
        assert result["final_report"].startswith("# LLM 产出报告")
        assert "正文内容。" in result["final_report"]

    @pytest.mark.parametrize("engine", sorted(ENGINE_NODES))
    def test_output_without_heading_gets_prefixed(self, engine):
        node = ENGINE_NODES[engine](_FakeLLM("没有标题的正文。"))
        report = node(_state())["final_report"]
        assert report.startswith("# 深度研究报告")
        assert "没有标题的正文。" in report


class TestExceptionStillFallsBack:
    """调用抛异常时仍走备用方法（原有行为不回归）。"""

    @pytest.mark.parametrize("engine", sorted(ENGINE_NODES))
    def test_exception_uses_fallback(self, engine):
        node = ENGINE_NODES[engine](_FakeLLM(error=RuntimeError("网关 502")))
        result = node(_state())
        assert result["is_completed"] is True
        report = result["final_report"]
        assert LEGACY_SENTINEL not in report
        assert PARAGRAPHS[0]["title"] in report
        assert PARAGRAPHS[0]["research"]["latest_summary"] in report


class TestEmptyStreamRetry:
    """LLMClient 的空流短重试：有界、成功后停止。"""

    def _client(self, monkeypatch, chunks_per_call):
        from engines.common import llm_client as mod

        client = mod.LLMClient.__new__(mod.LLMClient)  # 跳过 __init__ 的外部依赖
        calls = {"n": 0}

        def fake_stream(*args, **kwargs):
            calls["n"] += 1
            return iter(chunks_per_call[calls["n"] - 1])

        monkeypatch.setattr(client, "stream_invoke", fake_stream)
        monkeypatch.setattr(mod.time, "sleep", lambda seconds: None)
        return client, calls

    def test_retries_then_returns_content(self, monkeypatch):
        client, calls = self._client(monkeypatch, [[], [], ["恢", "复"]])
        assert client.stream_invoke_to_string("s", "u") == "恢复"
        assert calls["n"] == 3, "首次 + 2 次重试"

    def test_no_retry_when_content_present(self, monkeypatch):
        client, calls = self._client(monkeypatch, [["正常"]])
        assert client.stream_invoke_to_string("s", "u") == "正常"
        assert calls["n"] == 1, "有内容时不应重试"

    def test_gives_up_after_bounded_retries(self, monkeypatch):
        client, calls = self._client(monkeypatch, [[], [], [], []])
        assert client.stream_invoke_to_string("s", "u") == ""
        assert calls["n"] == 3, "重试次数必须有界（首次 + 2 次），不应无限重试"

    def test_retry_count_is_small(self):
        """守住『短重试』这一设计约束：不能退化成 LLM_RETRY_CONFIG 那样的长等待。"""
        from engines.common.llm_client import LLMClient
        assert LLMClient.EMPTY_STREAM_MAX_RETRIES <= 3
        assert LLMClient.EMPTY_STREAM_RETRY_DELAY <= 10.0
