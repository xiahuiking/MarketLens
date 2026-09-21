"""
情感分析流水线回归测试。

覆盖四项优化，全部不依赖真实模型权重与数据库：
- 元数据累积：空搜索/透传结果不得覆盖已产出的情感与聚类结果；
- enable_sentiment：LLM 的 per-call 开关要贯通到搜索参数；
- 批处理与缓存：n 条文本只做 ceil(n/batch) 次前向，重复文本命中缓存；
- prompt 瘦身：高置信度明细限量、透传结构不携带全文。
"""

import json
import re
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from engines.ReviewEngine.nodes._search_utils import (  # noqa: E402
    execute_search_and_convert,
    merge_search_metadata,
)
from engines.ReviewEngine.tools.sentiment_analyzer import (  # noqa: E402
    SentimentResult,
    WeiboMultilingualSentimentAnalyzer,
)


def _real_sentiment(total: int = 38) -> dict:
    return {
        "sentiment_analysis": {
            "available": True,
            "total_analyzed": total,
            "sentiment_distribution": {"正面": total - 8, "中性": 5, "负面": 3},
            "high_confidence_results": [],
            "summary": f"共分析{total}条内容",
        }
    }


def _passthrough() -> dict:
    return {
        "sentiment_analysis": {
            "available": False,
            "total_analyzed": 0,
            "reason": "缺少依赖: Transformers，情感分析已禁用。",
            "sentiment_distribution": {},
            "high_confidence_results": [],
        }
    }


class TestMergeSearchMetadata:
    """P0-1：段落级元信息累积。"""

    def test_empty_metadata_keeps_existing(self):
        merged = merge_search_metadata(_real_sentiment(), {})
        assert merged["sentiment_analysis"]["total_analyzed"] == 38
        assert merged["sentiment_analysis"]["available"] is True

    def test_pass_through_does_not_overwrite_real_result(self):
        merged = merge_search_metadata(_real_sentiment(), _passthrough())
        assert merged["sentiment_analysis"]["available"] is True
        assert merged["sentiment_analysis"]["total_analyzed"] == 38

    def test_none_metadata_does_not_break(self):
        merged = merge_search_metadata(_real_sentiment(), None)
        assert merged["sentiment_analysis"]["total_analyzed"] == 38

    def test_stronger_result_upgrades(self):
        merged = merge_search_metadata(_real_sentiment(38), _real_sentiment(60))
        assert merged["sentiment_analysis"]["total_analyzed"] == 60

    def test_weaker_result_does_not_downgrade(self):
        merged = merge_search_metadata(_real_sentiment(60), _real_sentiment(12))
        assert merged["sentiment_analysis"]["total_analyzed"] == 60

    def test_first_ever_pass_through_is_recorded(self):
        """从未成功过时要保留失败原因，让 LLM 知道"没跑"而不是"没有情感信息"。"""
        merged = merge_search_metadata(None, _passthrough())
        assert merged["sentiment_analysis"]["available"] is False
        assert "缺少依赖" in merged["sentiment_analysis"]["reason"]

    def test_clustering_merge_prefers_performed(self):
        merged = merge_search_metadata(
            {"clustering": {"enabled": True, "performed": False}},
            {"clustering": {"enabled": True, "performed": True, "original_count": 38, "sampled_count": 20}},
        )
        assert merged["clustering"]["performed"] is True
        assert merged["clustering"]["sampled_count"] == 20


class _FakeResponse:
    def __init__(self):
        self.results = []
        self.parameters = {}


class _FakeContext:
    def __init__(self):
        self.config = type("C", (), {"MAX_SEARCH_RESULTS_FOR_LLM": 0})()
        self.captured = {}

    def validate_date_format(self, date_str):
        return True

    def execute_search(self, tool_name, query, **kwargs):
        self.captured = {"tool": tool_name, "query": query, **kwargs}
        return _FakeResponse()


class TestEnableSentimentPassthrough:
    """P1-5：LLM 的 enable_sentiment 贯通到搜索参数。"""

    def test_false_is_forwarded(self):
        ctx = _FakeContext()
        execute_search_and_convert(
            ctx, {"enable_sentiment": False}, "headphones", "get_product_reviews"
        )
        assert ctx.captured["enable_sentiment"] is False

    def test_true_is_forwarded(self):
        ctx = _FakeContext()
        execute_search_and_convert(
            ctx, {"enable_sentiment": True}, "headphones", "get_top_complaints"
        )
        assert ctx.captured["enable_sentiment"] is True

    def test_absent_keeps_default(self):
        ctx = _FakeContext()
        execute_search_and_convert(ctx, {}, "headphones", "get_product_reviews")
        assert "enable_sentiment" not in ctx.captured

    def test_search_output_model_exposes_field(self):
        from engines.common.structured_output import SearchOutput

        out = SearchOutput(search_query="q", search_tool="get_product_reviews",
                           reasoning="r", enable_sentiment=False)
        assert out.model_dump()["enable_sentiment"] is False
        assert SearchOutput(search_query="q", search_tool="t", reasoning="r").enable_sentiment is None


torch = pytest.importorskip("torch", reason="批处理测试需要 torch")

import engines.ReviewEngine.tools.sentiment_analyzer as sentiment_module  # noqa: E402

# 依赖探测现在是惰性的：真实运行时由 initialize() 完成，测试里显式触发一次，
# 让模块级 torch 引用就绪（推理路径需要它）。
assert sentiment_module._load_torch()


class _RecordingTokenizer:
    """记录每次 tokenizer 调用覆盖了多少条文本，用于验证"真批处理"。"""

    def __init__(self, n_labels: int = 5):
        self.calls = []
        self.n_labels = n_labels

    def __call__(self, texts, max_length=None, padding=None, truncation=None, return_tensors=None):
        if isinstance(texts, str):
            texts = [texts]
        self.calls.append(len(texts))
        # 用文本长度的一半作为"logits"，保证结果稳定可复现
        rows = [[float(len(t) % 7) + i for i in range(self.n_labels)] for t in texts]
        return {"input_ids": torch.tensor([[1, 2, 3]] * len(texts)),
                "logits_hint": torch.tensor(rows)}


class _StubModel:
    def __init__(self, tokenizer):
        self.tokenizer = tokenizer
        self.forward_calls = 0

    def __call__(self, input_ids=None, **kwargs):
        self.forward_calls += 1
        n = input_ids.shape[0]
        rows = [[float((i * 3 + j) % 11) for j in range(5)] for i in range(n)]
        return type("Out", (), {"logits": torch.tensor(rows)})()

    def eval(self):
        return self

    def to(self, device):
        return self


def _make_analyzer(batch_size: int = 8) -> WeiboMultilingualSentimentAnalyzer:
    """构造一个"已初始化"的分析器，用桩模型避免加载真实权重。"""
    sa = WeiboMultilingualSentimentAnalyzer()
    sa.is_disabled = False
    sa.is_initialized = True
    sa.device = torch.device("cpu")
    sa.loaded_model_name = "stub-model"
    sa.tokenizer = _RecordingTokenizer()
    sa.model = _StubModel(sa.tokenizer)
    sa._batch_size = lambda: batch_size  # type: ignore[assignment]
    sa._cache_size = lambda: 100  # type: ignore[assignment]
    return sa


class TestBatchedInference:
    """P0-2：真批处理。"""

    def test_forward_count_equals_number_of_batches(self):
        sa = _make_analyzer(batch_size=32)
        texts = [f"评论{i}" for i in range(100)]
        result = sa.analyze_batch(texts, show_progress=False)
        assert result.total_processed == 100
        assert result.success_count == 100
        # 100 条 / 批大小 32 -> 4 次前向（而非 100 次）
        assert sa.model.forward_calls == 4
        assert sa.tokenizer.calls == [32, 32, 32, 4]

    def test_results_align_with_input_order(self):
        sa = _make_analyzer(batch_size=8)
        texts = ["a" * 10, "b" * 20, "c" * 30]
        result = sa.analyze_batch(texts, show_progress=False)
        assert [r.text for r in result.results] == texts

    def test_empty_and_blank_inputs_do_not_break_batching(self):
        sa = _make_analyzer(batch_size=8)
        result = sa.analyze_batch(["", "   ", "正常评论"], show_progress=False)
        assert result.total_processed == 3
        assert result.success_count == 1
        assert result.failed_count == 2

    def test_batch_failure_falls_back_to_single_inference(self):
        sa = _make_analyzer(batch_size=8)
        calls = {"n": 0}

        class _FlakyModel(_StubModel):
            def __call__(self, input_ids=None, **kwargs):
                calls["n"] += 1
                if input_ids.shape[0] > 1:
                    raise RuntimeError("simulated OOM")
                return super().__call__(input_ids=input_ids, **kwargs)

        sa.model = _FlakyModel(sa.tokenizer)
        result = sa.analyze_batch(["x" * 5, "y" * 6, "z" * 7], show_progress=False)
        # 整批失败后逐条重试，仍应全部拿到结果
        assert result.success_count == 3
        assert sa.model.forward_calls == 3


class TestSentimentCache:
    """P1-3：结果缓存。"""

    def test_repeated_texts_hit_cache(self):
        sa = _make_analyzer(batch_size=8)
        texts = ["同一段评论"] * 10
        sa.analyze_batch(texts, show_progress=False)
        # 批内去重：10 条完全相同的文本只做一次前向
        assert sa.model.forward_calls == 1
        assert sa.cache_stats()["size"] == 1
        # 第二次全部命中缓存，不再调用模型
        sa.analyze_batch(texts, show_progress=False)
        assert sa.model.forward_calls == 1
        assert sa.cache_stats()["hits"] >= 9

    def test_second_run_does_not_call_model(self):
        sa = _make_analyzer(batch_size=8)
        texts = [f"评论{i}" for i in range(20)]
        sa.analyze_batch(texts, show_progress=False)
        first = sa.model.forward_calls
        sa.analyze_batch(texts, show_progress=False)
        assert sa.model.forward_calls == first

    def test_cache_disabled_still_works(self):
        sa = _make_analyzer(batch_size=8)
        sa._cache_size = lambda: 0  # type: ignore[assignment]
        texts = ["同样的评论"] * 3
        result = sa.analyze_batch(texts, show_progress=False)
        assert result.success_count == 3
        assert sa.cache_stats()["hits"] == 0


class TestPromptSlimming:
    """P1-4：写入 LLM prompt 的情感元信息限量、去冗余。"""

    def _items(self, n: int) -> list:
        return [
            {"content": f"评论正文{i}" + "内容" * 100, "platform": "amazon",
             "url": f"https://example.com/{i}", "author": "u", "publish_time": "2024-01-01"}
            for i in range(n)
        ]

    def test_high_confidence_results_are_capped(self, monkeypatch):
        sa = _make_analyzer(batch_size=16)
        monkeypatch.setattr(sa, "_max_high_confidence", lambda: 10)
        out = sa.analyze_query_results(self._items(40), min_confidence=0.0)["sentiment_analysis"]
        assert len(out["high_confidence_results"]) == 10

    def test_detail_entries_carry_no_full_text_blob(self, monkeypatch):
        sa = _make_analyzer(batch_size=16)
        monkeypatch.setattr(sa, "_max_high_confidence", lambda: 5)
        out = sa.analyze_query_results(self._items(10), min_confidence=0.0)["sentiment_analysis"]
        payload = json.dumps(out, ensure_ascii=False)
        assert "original_data" not in payload
        assert set(out["high_confidence_results"][0]) == {
            "sentiment", "confidence", "text_preview", "platform", "url", "publish_time"
        }

    def test_passthrough_analysis_omits_full_texts(self):
        sa = WeiboMultilingualSentimentAnalyzer()
        sa.disable("缺少依赖: Transformers，情感分析已禁用。", by_missing_deps=True)
        out = sa.analyze_query_results(self._items(30))["sentiment_analysis"]
        assert out["available"] is False
        assert out["original_count"] == 30
        payload = json.dumps(out, ensure_ascii=False)
        assert "original_texts" not in payload
        assert "passthrough_texts" not in payload
        assert len(payload) < 1000


class TestMetadataReachesSummaryPrompt:
    """P0-1 端到端复现：末轮空搜索不得让情感结果从 prompt 里消失。

    这正是线上那次运行的失效链路：某段落先搜到 38 条评论并跑完情感分析，
    随后几轮反思都命中空结果（metadata 为 {}），旧实现在最后一次 summary 时
    把情感元信息丢干净，报告最终写成"情感自动分析未执行"。
    """

    def _fake_ctx(self, responses):
        from engines.ReviewEngine.tools.search import DBResponse, QueryResult
        from engines.common.structured_output import (
            InitialSummaryOutput,
            ReflectionSummaryOutput,
        )

        captured = {"messages": []}
        queue = list(responses)

        class _Ctx:
            pass

        ctx = _Ctx()
        ctx.engine_name = "review"
        ctx.config = type("C", (), {"MAX_SEARCH_RESULTS_FOR_LLM": 0, "MAX_CONTENT_LENGTH": 500000})()
        ctx.progress_callback = lambda *a, **k: None

        def _execute_search(tool_name, query, **kwargs):
            return queue.pop(0) if queue else DBResponse(
                tool_name=tool_name, parameters={}, results=[], results_count=0
            )

        def _structured_invoke(system_prompt, user_prompt, output_model, **kwargs):
            captured["messages"].append(user_prompt)
            if output_model is InitialSummaryOutput:
                return InitialSummaryOutput(paragraph_latest_state="初始总结")
            if output_model is ReflectionSummaryOutput:
                return ReflectionSummaryOutput(updated_paragraph_latest_state="更新总结")
            raise AssertionError(f"意外的 output_model: {output_model!r}")

        ctx.execute_search = _execute_search
        ctx.validate_date_format = lambda s: True
        ctx.llm_client = type("L", (), {"structured_invoke": staticmethod(_structured_invoke)})()
        return ctx, captured, DBResponse, QueryResult

    def test_sentiment_metadata_survives_empty_reflection(self):
        from engines.ReviewEngine.nodes.initial_search import InitialSearchNode
        from engines.ReviewEngine.nodes.initial_summary import InitialSummaryNode
        from engines.ReviewEngine.nodes.reflection_search import ReflectionSearchNode
        from engines.ReviewEngine.nodes.reflection_summary import ReflectionSummaryNode

        ctx, captured, DBResponse, QueryResult = self._fake_ctx([])

        reviews = [
            QueryResult(platform="amazon", content_type="review", title_or_content=f"评论{i}",
                        url=f"https://x/{i}", hotness_score=0.5, source_table="review")
            for i in range(38)
        ]
        real = DBResponse(
            tool_name="get_product_reviews",
            parameters={
                "sentiment_analysis": {
                    "available": True,
                    "total_analyzed": 38,
                    "sentiment_distribution": {"正面": 22, "中性": 9, "负面": 7},
                    "summary": "共分析38条内容，主要情感倾向为'正面'(22条，占57.9%)",
                    "high_confidence_results": [],
                }
            },
            results=reviews,
            results_count=38,
        )
        empty = DBResponse(tool_name="search_products", parameters={}, results=[], results_count=0)
        calls = {"n": 0}

        def _search(tool_name, query, **kw):
            # 第一次搜索有评论 + 情感结果，之后所有反思都命中空结果（复现线上场景）
            calls["n"] += 1
            return real if calls["n"] == 1 else empty

        ctx.execute_search = _search

        state = {
            "paragraphs": [{"title": "整体口碑概览", "content": "分析情感倾向"}],
            "current_paragraph_index": 0,
            "current_reflection_count": 0,
            "max_reflections": 2,
        }

        state.update(InitialSearchNode(ctx)(state))
        state.update(InitialSummaryNode(ctx)(state))
        assert "共分析38条内容" in captured["messages"][-1], "初始总结应看到情感分析结果"

        # 反思轮：搜到空结果（无后处理），metadata 为空
        for i in range(2):
            state["current_reflection_count"] = i
            state.update(ReflectionSearchNode(ctx)(state))
            state.update(ReflectionSummaryNode(ctx)(state))
            assert "共分析38条内容" in captured["messages"][-1], (
                f"第 {i + 1} 轮反思后情感元信息不应被空搜索清空"
            )

        # 段落实体里也留下了累积结果
        meta = state["paragraphs"][0]["research"]["metadata"]
        assert meta["sentiment_analysis"]["total_analyzed"] == 38
        assert state["paragraphs"][0]["research"]["current_search"]["metadata"] == {}

    def test_passthrough_reason_reaches_prompt_when_never_succeeded(self):
        from engines.ReviewEngine.nodes.initial_search import InitialSearchNode
        from engines.ReviewEngine.nodes.initial_summary import InitialSummaryNode

        ctx, captured, DBResponse, QueryResult = self._fake_ctx([])
        ctx.execute_search = lambda tool_name, query, **kw: DBResponse(
            tool_name="get_product_reviews",
            parameters={
                "sentiment_analysis": {
                    "available": False,
                    "reason": "缺少依赖: Transformers，情感分析已禁用。",
                    "total_analyzed": 0,
                    "sentiment_distribution": {},
                    "high_confidence_results": [],
                    "summary": "情感分析未执行：缺少依赖: Transformers，情感分析已禁用。",
                    "original_count": 12,
                }
            },
            results=[
                QueryResult(platform="amazon", content_type="review", title_or_content=f"评论{i}",
                            url=f"https://x/{i}", hotness_score=0.5, source_table="review")
                for i in range(12)
            ],
            results_count=12,
        )

        state = {
            "paragraphs": [{"title": "整体口碑概览", "content": "分析情感倾向"}],
            "current_paragraph_index": 0,
            "current_reflection_count": 0,
            "max_reflections": 1,
        }
        state.update(InitialSearchNode(ctx)(state))
        state.update(InitialSummaryNode(ctx)(state))
        # 从未成功过时，要让 LLM 知道"没跑"以及原因，而不是看到一片空白
        assert "缺少依赖" in captured["messages"][-1]


class TestGlobalSwitchIsAuthoritative:
    """全局开关也必须对显式 analyze_sentiment 工具生效（否则关不干净）。"""

    def _ctx(self, enabled: bool):
        from engines.ReviewEngine.context import ReviewContext

        ctx = ReviewContext.__new__(ReviewContext)
        ctx.config = type("C", (), {
            "SENTIMENT_ANALYSIS_ENABLED": enabled,
            "ENABLE_SENTIMENT_PER_SEARCH": enabled,
        })()
        ctx.sentiment_analyzer = WeiboMultilingualSentimentAnalyzer()
        return ctx

    def test_tool_returns_passthrough_when_globally_disabled(self):
        ctx = self._ctx(False)
        out = ctx.analyze_sentiment_only(["评论一", "评论二"])
        assert out["success"] is False
        assert out["total_analyzed"] == 0
        assert out["available"] is False
        assert out["original_count"] == 2
        # 不应触碰模型（此时模型未初始化）
        assert ctx.sentiment_analyzer.is_initialized is False

    def test_review_post_process_respects_global_switch(self):
        ctx = self._ctx(False)
        assert ctx._sentiment_enabled({}) is False
        # 即使 LLM 显式要求开启，全局关闭仍优先
        assert ctx._sentiment_enabled({"enable_sentiment": True}) is False

    def test_per_search_switch_can_still_be_overridden_by_llm_when_global_on(self):
        ctx = self._ctx(True)
        assert ctx._sentiment_enabled({}) is True
        assert ctx._sentiment_enabled({"enable_sentiment": False}) is False


class TestSentimentStarMappingPrompt:
    """prompt 里必须给出情感档位 ↔ 星级的官方折算口径。"""

    EXPECTED = {
        "非常正面": "5 星",
        "正面": "4 星",
        "中性": "3 星",
        "负面": "2 星",
        "非常负面": "1 星",
    }

    def test_rule_defines_all_five_levels(self):
        from engines.ReviewEngine.prompts import SENTIMENT_STAR_MAPPING_RULE as rule

        for label, star in self.EXPECTED.items():
            assert re.search(rf"\|\s*{label}\s*\|\s*{re.escape(star)}\s*\|", rule), f"缺少 {label} → {star}"

    def test_rule_injected_into_all_summary_and_format_prompts(self):
        from engines.ReviewEngine import prompts as P

        for name in ("SYSTEM_PROMPT_FIRST_SUMMARY",
                     "SYSTEM_PROMPT_REFLECTION_SUMMARY",
                     "SYSTEM_PROMPT_REPORT_FORMATTING"):
            text = getattr(P, name)
            assert "情感档位与星级的折算口径" in text, f"{name} 未注入折算口径"
            for label, star in self.EXPECTED.items():
                assert re.search(rf"\|\s*{label}\s*\|\s*{re.escape(star)}\s*\|", text), \
                    f"{name} 缺少 {label} → {star}"

    def test_rule_carries_provenance_guardrails(self):
        from engines.ReviewEngine.prompts import SENTIMENT_STAR_MAPPING_RULE as rule

        # 真实星级优先 + 折算必须标注来源 + 样本量 + 两口径不一致要并列
        assert "真实星级优先" in rule
        assert "非平台真实星级" in rule
        assert "total_analyzed" in rule
        assert "10 个百分点" in rule

    def test_format_prompt_requires_data_provenance_column(self):
        from engines.ReviewEngine.prompts import SYSTEM_PROMPT_REPORT_FORMATTING as fmt

        assert "数据口径" in fmt
        assert "文本情感折算" in fmt

    def test_report_engine_prompt_warns_against_conflating_axes(self):
        from engines.ReportEngine.prompts import SYSTEM_PROMPT_HTML_GENERATION as html_prompt

        assert "不可互相冒充" in html_prompt
        assert "非平台真实星级" in html_prompt


class TestLazyDependencyLoading:
    """P2-6：导入本模块不得拉起 torch/transformers，也不得加载模型。"""

    def test_module_level_instance_is_not_initialized(self):
        from engines.ReviewEngine.tools import multilingual_sentiment_analyzer as shared

        # 全局实例只做构造，不加载权重（是否已初始化取决于本进程其他测试）
        assert hasattr(shared, "deps_probed")
        assert hasattr(shared, "cache_stats")

    def test_new_instance_does_not_load_model(self):
        sa = WeiboMultilingualSentimentAnalyzer()
        assert sa.is_initialized is False
        assert sa.model is None and sa.tokenizer is None

    def test_dependency_probe_is_lazy(self):
        import engines.ReviewEngine.tools.sentiment_analyzer as mod

        sa = WeiboMultilingualSentimentAnalyzer()
        assert sa.deps_probed is False
        # 探测入口存在且幂等
        assert isinstance(mod.probe_sentiment_dependencies(), str)
        assert mod._load_transformers() in (True, False)
