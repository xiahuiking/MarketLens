"""host 回灌开关与版本号 trace 的回归测试。

评测系统（evals/）子集族实验依赖两件事：

1. ``set_host_feedback_enabled(False)`` 必须让 ``get_latest_host_speech()`` /
   ``read_host_speech_for_node()`` 恒返回 None，即使缓存里已有 host 发言——
   否则去掉某个 Agent 会通过 host 发言间接改变其它 Agent 的下一轮总结，`V`
   不再是干净的集合函数（见 evals/DESIGN.md 2.2）。
2. host 发言自增版本号，``read_host_speech_for_node`` 记录 (node, version) trace。
"""

import pytest

from app.utils import forum_reader


@pytest.fixture(autouse=True)
def _reset_feedback_state():
    """每个用例后把开关/版本/trace 复位，避免跨用例污染。"""
    yield
    forum_reader.set_host_feedback_enabled(True)
    forum_reader.reset_host_trace()
    forum_reader._latest_host_speech = None
    forum_reader._host_speech_version = 0


def _cache_host_speech(content: str):
    forum_reader._on_forum_message("forum_message", {"type": "host", "content": content})


def test_switch_off_blocks_speech_even_when_cached():
    _cache_host_speech("这是一条真实长度的主持人发言内容")
    forum_reader.set_host_feedback_enabled(False)

    assert forum_reader.get_latest_host_speech() is None
    assert forum_reader.read_host_speech_for_node("review:initial_summary") is None


def test_switch_off_does_not_record_trace():
    _cache_host_speech("这是一条真实长度的主持人发言内容")
    forum_reader.set_host_feedback_enabled(False)

    forum_reader.read_host_speech_for_node("review:initial_summary")
    assert forum_reader.get_host_trace() == []


def test_version_increments_on_each_host_message():
    _cache_host_speech("第一条")
    _cache_host_speech("第二条")

    forum_reader.set_host_feedback_enabled(True)
    speech, version = forum_reader.get_latest_host_speech_with_version()
    assert speech == "第二条"
    assert version == 2


def test_read_host_speech_for_node_records_trace():
    _cache_host_speech("主持人发言")
    forum_reader.set_host_feedback_enabled(True)

    speech, version = forum_reader.read_host_speech_for_node("competitor:reflection_summary")
    assert speech == "主持人发言"
    assert version == 1
    assert forum_reader.get_host_trace() == [
        {"node": "competitor:reflection_summary", "host_version": 1}
    ]
