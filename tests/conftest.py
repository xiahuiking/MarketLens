"""全局测试夹具。

最重要的职责是**隔离 LLM 用量核算的落盘目录**：引擎 e2e 用例会真正走到
``engines/common/llm_client.py`` 的埋点，如果不隔离，跑一次 pytest 就会在仓库的
``data/usage/`` 与 ``logs/usage/`` 下留下一堆 ``unassigned.jsonl``。
"""

import pytest

from engines.common import usage


@pytest.fixture(autouse=True)
def _isolate_usage_storage(tmp_path_factory, monkeypatch):
    """把用量账本的落盘目录与内存状态隔离到临时目录，避免污染仓库。"""
    root = tmp_path_factory.mktemp("llm-usage")
    monkeypatch.setattr(usage, "_usage_dir", lambda: root / "logs")
    monkeypatch.setattr(usage, "_summary_dir", lambda: root / "data")
    usage.reset_state()
    yield
    usage.reset_state()
