"""全局测试夹具。

最重要的职责是**隔离副作用**，避免跑一次 pytest 就污染仓库里的生产状态：

- ``data/usage/`` 与 ``logs/usage/``：引擎 e2e 用例会真正走到
  ``engines/common/llm_client.py`` 的埋点，不隔离就会留下 ``unassigned.jsonl``；
- ``logs/forum.log``：引擎 e2e 用例的 mock 摘要（「## 测试段落」）一旦被论坛
  handler 写入真实日志，并触发真实主持人 LLM 调用，报告链路就会读到一整套
  「测试段落 + 方法论训话」，最终报告被带成方法论散文。
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


@pytest.fixture(autouse=True)
def _isolate_forum_side_effects(tmp_path_factory, monkeypatch):
    """隔离论坛侧写：测试不得写真实 ``logs/forum.log``，也不得真实调用主持人 LLM。

    历史上 ``logs/forum.log`` 被测试 mock 的「## 测试段落」占位发言写满，并被
    ReportEngine 当作章节素材读入（``build_context`` → ``forumLogs``），直接把
    报告带偏。
    """
    from app.services import forum_service
    from app.utils import forum_reader
    from engines.ForumEngine import handler as forum_handler

    forum_root = tmp_path_factory.mktemp("forum")

    real_init = forum_handler.ForumEventHandler.__init__

    def _patched_init(self, log_dir: str = "logs"):  # noqa: ARG001 - 强制落到临时目录
        real_init(self, str(forum_root))

    monkeypatch.setattr(forum_handler.ForumEventHandler, "__init__", _patched_init)
    monkeypatch.setattr(forum_handler, "generate_host_speech", lambda *a, **k: None)
    monkeypatch.setattr(forum_service, "LOG_DIR", forum_root, raising=False)
    monkeypatch.setattr(forum_reader, "_latest_host_speech", None)

    yield

    forum_reader._latest_host_speech = None
