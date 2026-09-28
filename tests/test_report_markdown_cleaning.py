"""回归测试：Markdown 报告清洗不得截断正文。

历史 bug：``format_report`` 节点把 Markdown 报告交给
``remove_reasoning_from_output()``（一个按「第一个 ``{``/``[`` 之前全部丢弃」
工作的 JSON 提取器），导致报告里一出现来源标注 ``[媒体]`` 就被砍掉开头。
实测单次丢失 50%~80% 正文（``logs/review.log``：9622 字符 → 1839 字符）。
"""

import sys
from pathlib import Path

_PROJ_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

from engines.common.report_text import (  # noqa: E402
    clean_markdown_report,
    strip_reasoning,
)
from engines.CompetitorEngine.nodes.format_report import FormatReportNode as CompetitorFormat  # noqa: E402
from engines.ReviewEngine.nodes.format_report import FormatReportNode as ReviewFormat  # noqa: E402
from engines.TrendEngine.nodes.format_report import FormatReportNode as TrendFormat  # noqa: E402

# 一份典型的「含来源标注」报告：第一处 ``[`` 出现在执行摘要里。
_REPORT_WITH_BRACKETS = """# 【竞品分析】无线耳机竞品对比分析报告

## 核心摘要

- 价格带集中在 300–1500 元 [来源：IDC 2026Q2]
- 降噪能力是第一决策因素

## 一、市场格局

IDC 数据显示 [官方/一手/机构/媒体/线索] 四级来源中，机构数据占比最高。

## 二、参数对比

| 型号 | 价格 |
|------|------|
| Sony WH-1000XM5 | 2299 |
"""

_REPORT_WITH_BRACE = """# 商品口碑分析报告

## 执行摘要

差评集中在品控（占比 38%），典型表述为 {"耳套开胶": 12} 一类问题。

## 一、评分分布

5 星 44.8%，1 星 10.3%。
"""


def test_bracket_report_is_not_truncated():
    cleaned = clean_markdown_report(_REPORT_WITH_BRACKETS)
    assert cleaned.startswith("# 【竞品分析】")
    assert "## 核心摘要" in cleaned
    assert "[来源：IDC 2026Q2]" in cleaned
    assert "## 一、市场格局" in cleaned
    # 旧实现只剩「[官方/一手/...」之后的内容，这里必须保住开头
    assert cleaned.index("核心摘要") < cleaned.index("[官方/一手/机构/媒体/线索]")


def test_brace_report_is_not_truncated():
    cleaned = clean_markdown_report(_REPORT_WITH_BRACE)
    assert cleaned.startswith("# 商品口碑分析报告")
    assert "## 执行摘要" in cleaned
    assert '{"耳套开胶": 12}' in cleaned


def test_thought_block_is_stripped():
    raw = "<thought>先想一下怎么写…</thought>\n# 报告标题\n\n正文内容。"
    cleaned = clean_markdown_report(raw)
    assert cleaned.startswith("# 报告标题")
    assert "先想一下" not in cleaned
    assert "正文内容。" in cleaned


def test_reasoning_lead_is_dropped_only_when_heading_exists():
    raw = "思考：需要先梳理数据。\n\n# 报告标题\n\n正文。"
    assert clean_markdown_report(raw).startswith("# 报告标题")

    no_heading = "思考：这里没有标题，只有一段正文。"
    assert "这里没有标题" in clean_markdown_report(no_heading)


def test_code_fence_is_unwrapped_without_losing_content():
    raw = "```markdown\n# 报告标题\n\n正文内容。\n```"
    cleaned = clean_markdown_report(raw)
    assert cleaned == "# 报告标题\n\n正文内容。"


def test_default_title_added_when_missing():
    cleaned = clean_markdown_report("## 一、市场概况\n\n正文。")
    assert cleaned.startswith("# 深度研究报告")


def test_empty_output_returns_empty():
    assert clean_markdown_report("") == ""
    assert clean_markdown_report("   \n  ") == ""
    assert strip_reasoning("") == ""


def test_all_three_engines_parse_without_truncation():
    for node_cls in (ReviewFormat, CompetitorFormat, TrendFormat):
        cleaned = node_cls._parse_report(None, _REPORT_WITH_BRACKETS)
        assert cleaned.startswith("# 【竞品分析】"), node_cls.__name__
        assert "## 核心摘要" in cleaned, node_cls.__name__
        assert "## 二、参数对比" in cleaned, node_cls.__name__


class _StubLLM:
    def __init__(self, output: str):
        self.output = output

    def stream_invoke_to_string(self, system_prompt, message, **kwargs):  # noqa: ARG002
        return self.output


class _StubCtx:
    progress_callback = None
    llm_client = _StubLLM(_REPORT_WITH_BRACKETS)


def test_competitor_node_keeps_full_report_end_to_end():
    """走完整节点路径（LLM 输出 → final_report），确认开头没有被清洗掉。"""
    node = CompetitorFormat(_StubCtx())
    state = {"paragraphs": [{"title": "市场格局", "research": {"latest_summary": "摘要"}}]}
    result = node(state)
    assert result["is_completed"] is True
    assert result["final_report"].startswith("# 【竞品分析】")
    assert "## 核心摘要" in result["final_report"]
    assert "## 二、参数对比" in result["final_report"]
