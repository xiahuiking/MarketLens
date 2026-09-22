"""Token / 成本核算的单元与集成测试。

覆盖：token 估算、usage 归一化、价目表匹配与币种折算、账本聚合与落盘、
LLMClient 四个出口的埋点、cost_service 的 run 生命周期、报告任务成本回写。
全部离线运行，不访问任何外部服务。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from engines.common import pricing, usage
from engines.common.llm_client import LLMClient


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def isolated_usage(tmp_path, monkeypatch):
    """把账本状态与落盘目录隔离到 tmp_path，避免污染仓库。"""
    usage_dir = tmp_path / "usage"
    summary_dir = tmp_path / "summary"
    monkeypatch.setattr(usage, "_usage_dir", lambda: usage_dir)
    monkeypatch.setattr(usage, "_summary_dir", lambda: summary_dir)
    usage.reset_state()
    usage.set_active_run("run_test")
    yield SimpleNamespace(usage_dir=usage_dir, summary_dir=summary_dir)
    usage.reset_state()


@pytest.fixture(autouse=True)
def _fresh_price_cache():
    """每个用例都从干净的价格缓存开始，防止跨用例串味。"""
    pricing._cache["path"] = None
    pricing._cache["mtime"] = None
    pricing._cache["table"] = None
    yield


def _fake_response(prompt_tokens=100, completion_tokens=50, content="hello"):
    usage_obj = SimpleNamespace(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=usage_obj,
    )


class _StubCompletions:
    """替代 openai 客户端的 ``chat.completions``。"""

    def __init__(self, response=None, stream=None, error=None):
        self.response = response
        self.stream = stream
        self.error = error
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        if kwargs.get("stream"):
            return iter(self.stream or [])
        return self.response


def _make_client(*, response=None, stream=None, error=None, model="deepseek-chat"):
    client = LLMClient(
        api_key="test-key",
        model_name=model,
        base_url="https://example.invalid/v1",
        engine_name="TestEngine",
    )
    stub = _StubCompletions(response=response, stream=stream, error=error)
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=stub))
    return client, stub


# ── token 估算 ──────────────────────────────────────────────────────────────

def test_estimate_tokens_handles_empty_cjk_and_latin():
    assert usage.estimate_tokens("") == 0
    # 全中文：约 1 token/字
    assert usage.estimate_tokens("口碑分析报告") == len("口碑分析报告")
    # 全英文：约 4 字符/token
    assert usage.estimate_tokens("abcdefgh") == 2
    assert usage.estimate_tokens("abcdefgh") < usage.estimate_tokens("口碑分析报告")


def test_normalize_usage_supports_openai_and_langchain_shapes():
    assert usage.normalize_usage(None) is None
    assert usage.normalize_usage({"prompt_tokens": 10, "completion_tokens": 5}) == {
        "prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
    }
    # LangChain usage_metadata 字段名
    assert usage.normalize_usage({"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}) == {
        "prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10,
    }
    # 对象属性形式（OpenAI SDK CompletionUsage）
    obj = SimpleNamespace(prompt_tokens=1, completion_tokens=2, total_tokens=3)
    assert usage.normalize_usage(obj)["total_tokens"] == 3


# ── 价目表与成本计算 ────────────────────────────────────────────────────────

def test_price_table_matches_exact_alias_and_substring():
    assert pricing.find_model_price("deepseek-chat")["name"] == "deepseek-chat"
    # 带日期后缀的网关模型名应命中别名
    assert pricing.find_model_price("kimi-k2-0711-preview")["name"] == "kimi-k2"
    # 子串命中
    assert pricing.find_model_price("deepseek-chat-0324")["name"] == "deepseek-chat"
    # 未知模型不猜价格
    assert pricing.find_model_price("totally-unknown-model") is None


def test_compute_cost_cny_and_usd_conversion():
    # 纯 CNY 模型
    cny = pricing.compute_cost("deepseek-chat", 1_000_000, 1_000_000)
    assert cny["price_known"] is True
    assert cny["cost"] == pytest.approx(10.0)  # 2 + 8
    assert cny["currency"] == "CNY"

    # USD 模型按汇率折算成 CNY
    usd = pricing.compute_cost("gemini-2.5-pro", 1_000_000, 1_000_000)
    rate = pricing.usd_to_cny_rate()
    assert usd["cost"] == pytest.approx((1.25 + 10.0) * rate)

    # 未知模型：返回 None 而不是 0
    unknown = pricing.compute_cost("no-such-model", 1000, 1000)
    assert unknown["cost"] is None and unknown["price_known"] is False


def test_custom_price_table_via_settings(tmp_path, monkeypatch):
    table = {
        "display_currency": "CNY",
        "usd_to_cny": 7.0,
        "models": [
            {"name": "custom-model", "currency": "CNY", "input_per_million": 1.0, "output_per_million": 3.0}
        ],
    }
    path = tmp_path / "prices.json"
    path.write_text(json.dumps(table), encoding="utf-8")

    from app import config

    monkeypatch.setattr(config.settings, "MODEL_PRICES_PATH", str(path), raising=False)
    pricing._cache.update({"path": None, "mtime": None, "table": None})

    result = pricing.compute_cost("custom-model", 1_000_000, 1_000_000)
    assert result["cost"] == pytest.approx(4.0)


# ── 账本聚合与落盘 ──────────────────────────────────────────────────────────

def test_record_aggregates_tokens_cost_and_breakdowns(isolated_usage):
    usage.record_llm_call(
        engine="ReviewEngine", model="deepseek-chat", method="invoke",
        prompt_text="x", completion_text="y",
        usage={"prompt_tokens": 1000, "completion_tokens": 1000},
        duration_ms=50.0,
    )
    usage.record_llm_call(
        engine="TrendEngine", model="gemini-2.5-pro", method="stream",
        prompt_text="x", completion_text="y",
        usage={"prompt_tokens": 1_000_000, "completion_tokens": 0},
        duration_ms=150.0,
    )

    summary = usage.get_run_summary("run_test")
    assert summary["calls"] == 2
    assert summary["total_tokens"] == 1_002_000
    assert summary["unpriced_calls"] == 0
    assert summary["estimated_calls"] == 0
    assert summary["by_engine"]["ReviewEngine"]["calls"] == 1
    assert set(summary["by_model"]) == {"deepseek-chat", "gemini-2.5-pro"}
    expected = (2.0 + 8.0) / 1000 + 1.25 * pricing.usd_to_cny_rate()
    assert summary["cost"] == pytest.approx(expected)


def test_missing_usage_is_estimated_and_flagged(isolated_usage):
    record = usage.record_llm_call(
        engine="ReviewEngine", model="deepseek-chat", method="invoke",
        prompt_text="这是一段用于估算的中文提示词" * 10,
        completion_text="输出内容" * 5,
        usage=None,
    )
    assert record["tokens_known"] is False
    assert record["estimated"] is True
    assert record["prompt_tokens"] > 0
    assert usage.get_run_summary("run_test")["estimated_calls"] == 1


def test_unpriced_model_is_counted_not_silently_zero(isolated_usage):
    usage.record_llm_call(
        engine="ReviewEngine", model="mystery-model-9000", method="invoke",
        usage={"prompt_tokens": 100, "completion_tokens": 100},
    )
    summary = usage.get_run_summary("run_test")
    assert summary["cost"] == 0.0
    assert summary["unpriced_calls"] == 1


def test_failed_call_is_not_billed_or_counted_unpriced(isolated_usage):
    record = usage.record_llm_call(
        engine="ReportEngine", model="deepseek-chat", method="structured:function_calling",
        prompt_text="", completion_text="", ok=False, error="400 tool_choice unsupported",
    )
    assert record["billable"] is False
    assert record["total_tokens"] == 0
    summary = usage.get_run_summary("run_test")
    assert summary["failed_calls"] == 1
    assert summary["unpriced_calls"] == 0
    assert summary["cost"] == 0.0


def test_records_persist_to_jsonl_and_snapshot(isolated_usage):
    usage.record_llm_call(
        engine="ReviewEngine", model="deepseek-chat", method="invoke",
        usage={"prompt_tokens": 10, "completion_tokens": 5},
    )
    detail = isolated_usage.usage_dir / "run_test.jsonl"
    snapshot = isolated_usage.summary_dir / "run_test.json"
    assert detail.is_file() and snapshot.is_file()

    lines = [json.loads(line) for line in detail.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1 and lines[0]["engine"] == "ReviewEngine"
    payload = json.loads(snapshot.read_text(encoding="utf-8"))
    assert payload["calls"] == 1 and "records" not in payload


def test_summary_falls_back_to_jsonl_after_restart(isolated_usage):
    usage.record_llm_call(
        engine="ReviewEngine", model="deepseek-chat", method="invoke",
        usage={"prompt_tokens": 100, "completion_tokens": 100},
    )
    # 模拟进程重启：内存清空，仅剩磁盘
    usage._runs.clear()
    summary = usage.get_run_summary("run_test")
    assert summary is not None
    assert summary["calls"] == 1
    assert summary["total_tokens"] == 200


def test_concurrent_records_stay_consistent(isolated_usage):
    """三个引擎是并行线程：并发记账不能丢记录，落盘快照也不能写回退。"""
    import threading as _threading

    def worker(index: int):
        usage.record_llm_call(
            engine=f"Engine{index % 3}", model="deepseek-chat", method="invoke",
            usage={"prompt_tokens": 100, "completion_tokens": 100},
        )

    threads = [_threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    summary = usage.get_run_summary("run_test")
    assert summary["calls"] == 20
    assert summary["total_tokens"] == 4000

    # 磁盘快照必须反映全部记录（相同 run 的并发落盘曾会互相覆盖/写回退）
    on_disk = json.loads((isolated_usage.summary_dir / "run_test.json").read_text(encoding="utf-8"))
    assert on_disk["calls"] == 20
    lines = [
        line for line in (isolated_usage.usage_dir / "run_test.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 20


def test_listeners_receive_record_and_summary(isolated_usage):
    seen = []
    usage.subscribe(lambda record, summary: seen.append((record["engine"], summary["calls"])))
    usage.record_llm_call(
        engine="ForumEngine", model="qwen-plus", method="invoke",
        usage={"prompt_tokens": 5, "completion_tokens": 5},
    )
    assert seen == [("ForumEngine", 1)]


def test_engine_falls_back_to_thread_context(isolated_usage):
    """未标注引擎的客户端落到默认值 "Engine" 时，应回退到线程上下文里的引擎。"""
    usage.set_usage_context(engine="ReviewEngine")
    record = usage.record_llm_call(
        engine="Engine", model="deepseek-chat", method="invoke",
        usage={"prompt_tokens": 1, "completion_tokens": 1},
    )
    assert record["engine"] == "ReviewEngine"


# ── LLMClient 埋点 ──────────────────────────────────────────────────────────

def test_invoke_records_real_usage(isolated_usage):
    client, stub = _make_client(response=_fake_response(100, 50))
    assert client.invoke("system", "user") == "hello"

    summary = usage.get_run_summary("run_test")
    assert summary["calls"] == 1
    assert summary["prompt_tokens"] == 100
    assert summary["completion_tokens"] == 50
    assert summary["by_engine"]["TestEngine"]["calls"] == 1
    assert summary["cost"] > 0
    # 调用来源应被自动归因到本测试文件
    record = usage.snapshot_all()["run_test"]["records"][0]
    assert "test_usage_cost.py" in record["caller"]


def test_stream_failure_is_recorded_but_not_billed(isolated_usage):
    """失败调用要留痕（便于发现重试风暴），但不凭空估算 token/成本。

    刻意用未加 ``with_retry`` 的 ``stream_invoke``：``invoke`` 套了
    LLM_RETRY_CONFIG（initial_delay=60s），在测试里重试会真的等下去。
    """
    client, _ = _make_client(error=RuntimeError("400 Bad Request"))
    with pytest.raises(RuntimeError):
        list(client.stream_invoke("system", "user"))

    summary = usage.get_run_summary("run_test")
    assert summary["calls"] == 1
    assert summary["failed_calls"] == 1
    assert summary["total_tokens"] == 0
    assert summary["cost"] == 0.0
    assert summary["unpriced_calls"] == 0


def test_stream_invoke_captures_final_usage_chunk(isolated_usage):
    chunks = [
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="你"))], usage=None),
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="好"))], usage=None),
        SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=200, completion_tokens=20, total_tokens=220)),
    ]
    client, stub = _make_client(stream=chunks)
    assert "".join(client.stream_invoke("system", "user")) == "你好"

    assert stub.calls[0].get("stream_options") == {"include_usage": True}
    summary = usage.get_run_summary("run_test")
    assert summary["calls"] == 1
    assert summary["total_tokens"] == 220


def test_stream_invoke_without_usage_falls_back_to_estimate(isolated_usage):
    chunks = [
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="流式输出内容"))], usage=None),
    ]
    client, _ = _make_client(stream=chunks)
    assert "".join(client.stream_invoke("system", "user")) == "流式输出内容"

    summary = usage.get_run_summary("run_test")
    assert summary["estimated_calls"] == 1
    assert summary["total_tokens"] > 0


def test_stream_invoke_retries_without_stream_options(isolated_usage):
    """网关不认 stream_options 时应降级重试，而不是直接失败。"""
    chunks = [
        SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="ok"))], usage=None),
    ]

    class _RejectingFirstCompletions:
        def __init__(self):
            self.calls = []

        def create(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                raise RuntimeError("Unrecognized request argument: stream_options")
            return iter(chunks)

    client, _ = _make_client()
    stub = _RejectingFirstCompletions()
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=stub))

    assert "".join(client.stream_invoke("system", "user")) == "ok"
    assert len(stub.calls) == 2
    assert "stream_options" in stub.calls[0]
    assert "stream_options" not in stub.calls[1]


def test_structured_invoke_reads_usage_from_raw_message(isolated_usage, monkeypatch):
    """structured_invoke 走 include_raw，才能拿到真实 token usage。"""
    from pydantic import BaseModel

    class _Out(BaseModel):
        answer: str

    captured: dict = {}

    class _FakeStructured:
        def __init__(self, schema, method, include_raw):
            captured["method"] = method
            captured["include_raw"] = include_raw

        def invoke(self, messages):
            raw = SimpleNamespace(
                usage_metadata={"input_tokens": 300, "output_tokens": 60, "total_tokens": 360}
            )
            return {"parsed": _Out(answer="ok"), "raw": raw, "parsing_error": None}

    class _FakeChatDeepSeek:
        def __init__(self, **kwargs):
            captured["kwargs"] = kwargs

        def with_structured_output(self, schema, method=None, include_raw=False):
            return _FakeStructured(schema, method, include_raw)

    import langchain_deepseek

    monkeypatch.setattr(langchain_deepseek, "ChatDeepSeek", _FakeChatDeepSeek)

    client, _ = _make_client()
    result = client.structured_invoke("system", "user", _Out)

    assert result.answer == "ok"
    assert captured["include_raw"] is True
    # langchain-deepseek 只认 api_base
    assert captured["kwargs"]["api_base"] == "https://example.invalid/v1"

    summary = usage.get_run_summary("run_test")
    assert summary["total_tokens"] == 360
    assert summary["cost"] > 0


# ── cost_service：run 生命周期与任务回写 ────────────────────────────────────

@pytest.fixture
def isolated_cost_service(tmp_path, monkeypatch):
    from app.services import cost_service, report_service

    monkeypatch.setattr(usage, "_usage_dir", lambda: tmp_path / "usage")
    monkeypatch.setattr(usage, "_summary_dir", lambda: tmp_path / "summary")
    cost_service.reset_state()
    usage.reset_state()
    report_service.tasks_registry.clear()
    report_service.current_task = None
    cost_service.ensure_wired()
    yield cost_service, report_service
    cost_service.reset_state()
    usage.reset_state()
    report_service.tasks_registry.clear()
    report_service.current_task = None


def test_begin_run_and_attach_report_reuse_run(isolated_cost_service):
    cost_service, _ = isolated_cost_service

    run_id = cost_service.begin_run("测试查询")
    assert cost_service.get_active_run()["run_id"] == run_id

    # 第一次报告复用分析 run（端到端成本）
    assert cost_service.attach_report("测试查询") == run_id

    # 同一 run 的第二份报告另起新 run，避免重复计入第一次分析的成本
    second = cost_service.attach_report("测试查询")
    assert second != run_id


def test_attach_report_without_prior_search_creates_report_only_run(isolated_cost_service):
    cost_service, _ = isolated_cost_service
    run_id = cost_service.attach_report("直接出报告")
    summary = cost_service.get_run(run_id)
    assert summary["meta"]["source"] == "report_only"


def test_report_task_receives_cost_updates_and_sse_event(isolated_cost_service):
    cost_service, report_service = isolated_cost_service

    run_id = cost_service.begin_run("口碑分析")
    task = report_service.ReportTask("口碑分析", "report_test")
    task.run_id = run_id
    report_service.current_task = task
    report_service.tasks_registry[task.task_id] = task

    usage.set_active_run(run_id)
    usage.record_llm_call(
        engine="ReviewEngine", model="deepseek-chat", method="invoke",
        usage={"prompt_tokens": 1000, "completion_tokens": 1000},
    )

    # 监听器应把成本回写到任务，并发出一条 cost_update 事件
    assert task.get_cost()["calls"] == 1
    assert task.to_dict()["cost"]["cost"] > 0
    events = [frame["event"] for frame in task.get_event_history()]
    assert "cost_update" in events


def test_find_task_by_run_id(isolated_cost_service):
    cost_service, report_service = isolated_cost_service
    task = report_service.ReportTask("q", "report_x")
    task.run_id = "run_abc"
    report_service.tasks_registry[task.task_id] = task

    assert report_service.find_task_by_run_id("run_abc") is task
    assert report_service.find_task_by_run_id("run_missing") is None


def test_cost_api_helpers_return_expected_shapes(isolated_cost_service):
    cost_service, _ = isolated_cost_service

    run_id = cost_service.begin_run("接口测试")
    usage.set_active_run(run_id)
    usage.record_llm_call(
        engine="TrendEngine", model="deepseek-chat", method="invoke",
        usage={"prompt_tokens": 500, "completion_tokens": 500},
    )

    current = cost_service.get_current()
    assert current["run_id"] == run_id and current["calls"] == 1

    detail = cost_service.get_run(run_id, include_records=True)
    assert len(detail["records"]) == 1

    runs = cost_service.list_runs(limit=5)
    assert any(item["run_id"] == run_id for item in runs)

    prices = cost_service.price_info()
    assert prices["display_currency"] in ("CNY", "USD")
    assert prices["model_count"] >= 1


def test_disabled_tracking_records_nothing(isolated_usage, monkeypatch):
    from app import config

    monkeypatch.setattr(config.settings, "COST_TRACKING_ENABLED", False, raising=False)
    assert usage.record_llm_call(
        engine="ReviewEngine", model="deepseek-chat", method="invoke",
        usage={"prompt_tokens": 10, "completion_tokens": 10},
    ) is None
    # 完全关闭核算后，账本里不应留下任何 run
    assert usage.snapshot_all() == {}
    assert usage.get_run_summary("run_test") is None


def test_repo_price_table_is_valid_json():
    path = Path(pricing.__file__).resolve().parent / "model_prices.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["models"], "内置价目表不应为空"
    for entry in data["models"]:
        assert entry["name"]
        assert entry["currency"] in ("CNY", "USD")
        assert entry["input_per_million"] >= 0
        assert entry["output_per_million"] >= 0
