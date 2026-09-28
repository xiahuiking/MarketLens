"""回归测试：结构化输出的 json_repair 抢救 + 兜底大纲。

背景：json_mode 下模型把报告结构 JSON 输出成「字符串里带裸换行」的形式，
``json.loads`` 判 "Invalid control character"，LangChain 的 JsonOutputParser 抛
OUTPUT_PARSING_FAILURE，``generate_structure`` 于是整段丢弃模型给出的 5 段结构，
静默降级成 2 个默认段落（「研究概述 / 深度分析」），报告质量直接塌掉。
"""

import sys
from pathlib import Path

import pytest

_PROJ_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

from engines.common.llm_client import LLMClient, _repair_json_payload  # noqa: E402
from engines.common.structured_output import ReportStructure  # noqa: E402

# 注意：content 里是**真实的裸换行**，这正是 json.loads 拒绝、json_repair 能修的情况
_BAD_JSON = """{
  "paragraphs": [
    {"title": "一、口碑概览", "content": "第一行说明评分分布
第二行说明情感倾向"},
    {"title": "二、好评分析", "content": "核心亮点与复购动机"}
  ]
}"""


class _FakeMessage:
    def __init__(self, content):
        self.content = content
        self.response_metadata = {}
        self.usage_metadata = None


class _FakeStructured:
    def __init__(self, payload):
        self.payload = payload

    def invoke(self, messages):  # noqa: ARG002
        return self.payload


class _FakeLLM:
    def __init__(self, payload):
        self.payload = payload

    def with_structured_output(self, model, method=None, include_raw=False):  # noqa: ARG002
        return _FakeStructured(self.payload)


def _patch_chat_deepseek(monkeypatch, payload):
    import langchain_deepseek

    monkeypatch.setattr(langchain_deepseek, "ChatDeepSeek", lambda **kwargs: _FakeLLM(payload))


def test_repair_json_payload_fixes_raw_newlines():
    payload = _repair_json_payload(_BAD_JSON)
    assert isinstance(payload, dict)
    assert len(payload["paragraphs"]) == 2
    assert "第二行说明情感倾向" in payload["paragraphs"][0]["content"]


def test_repair_json_payload_strips_code_fence():
    fenced = "```json\n" + _BAD_JSON + "\n```"
    payload = _repair_json_payload(fenced)
    assert isinstance(payload, dict) and len(payload["paragraphs"]) == 2


def test_repair_json_payload_returns_none_on_garbage():
    assert _repair_json_payload("") is None


def test_structured_invoke_recovers_unparseable_json(monkeypatch):
    payload = {
        "parsed": None,
        "raw": _FakeMessage(_BAD_JSON),
        "parsing_error": ValueError("Invalid json output"),
    }
    _patch_chat_deepseek(monkeypatch, payload)

    client = LLMClient(api_key="test-key", model_name="test-model")
    result = client.structured_invoke("system", "user", ReportStructure)
    assert isinstance(result, ReportStructure)
    assert [p.title for p in result.paragraphs] == ["一、口碑概览", "二、好评分析"]


def test_structured_invoke_still_raises_when_unrecoverable(monkeypatch):
    payload = {
        "parsed": None,
        "raw": _FakeMessage("这根本不是 JSON，也没有花括号"),
        "parsing_error": ValueError("Invalid json output"),
    }
    _patch_chat_deepseek(monkeypatch, payload)

    client = LLMClient(api_key="test-key", model_name="test-model")
    with pytest.raises(ValueError):
        client.structured_invoke("system", "user", ReportStructure)


def test_default_structures_cover_full_outline():
    from engines.CompetitorEngine.nodes.generate_structure import (
        GenerateStructureNode as CompetitorStructure,
    )
    from engines.ReviewEngine.nodes.generate_structure import (
        GenerateStructureNode as ReviewStructure,
    )
    from engines.TrendEngine.nodes.generate_structure import (
        GenerateStructureNode as TrendStructure,
    )

    for factory in (ReviewStructure._default_structure, CompetitorStructure._default_structure, TrendStructure._default):
        outline = factory()
        assert len(outline) == 5
        for item in outline:
            assert item["title"] and item["content"]
            assert "研究概述" != item["title"]
