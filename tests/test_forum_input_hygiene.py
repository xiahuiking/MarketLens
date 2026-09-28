"""论坛输入卫生：净化 ``logs/forum.log`` 与主持人发言注入。

背景：测试 mock 的「## 测试段落」占位发言曾被写进真实 ``logs/forum.log``，并被
ReportEngine 当作章节素材读入；主持人动辄几千字的方法论发言也被原样拼进段落
提示词，模型于是转去「回应主持人」。这两条链路都要有护栏。
"""

import sys
from pathlib import Path

_PROJ_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

from app.services.forum_service import sanitize_forum_log_text  # noqa: E402
from app.utils.forum_reader import (  # noqa: E402
    HOST_SPEECH_MAX_CHARS,
    format_host_speech_for_prompt,
)

_REAL_REVIEW = "口碑侧样本量 29 条，正向 44.8%，负向 48.3%，主要集中在睡眠头戴耳机细分。"
_REAL_HOST = "本轮三视角中口碑与竞品数据已就位，争议点是价格带口径是否统一，请补充采集时间与样本量后再做交叉验证。"

_LOG = "\n".join([
    "=== ForumEngine 系统初始化 - 2026-09-22 14:13:34 ===",
    "[14:15:44] [SYSTEM] === ForumEngine 论坛开始 - 2026-09-22 14:15:44 ===",
    f"[14:15:44] [REVIEW] {_REAL_REVIEW}",
    "[14:22:13] [COMPETITOR] ## 测试段落\\n这是初始总结。",
    "[14:22:13] [COMPETITOR] ## 测试段落（更新）\\n这是反思后的总结。",
    "[10:00:01] [HOST] 发言",
    f"[14:22:22] [HOST] {_REAL_HOST}",
    f"[14:22:23] [HOST] {_REAL_HOST}",
])


def test_sanitize_drops_system_placeholder_short_and_duplicate_lines():
    cleaned = sanitize_forum_log_text(_LOG)
    assert "系统初始化" not in cleaned
    assert "SYSTEM" not in cleaned
    assert "测试段落" not in cleaned
    assert "这是初始总结" not in cleaned
    assert "[10:00:01] [HOST] 发言" not in cleaned
    assert cleaned.count(_REAL_HOST) == 1
    assert _REAL_REVIEW in cleaned


def test_sanitize_returns_empty_when_nothing_usable():
    assert sanitize_forum_log_text("") == ""
    assert sanitize_forum_log_text("纯噪声，没有任何合法行") == ""
    assert sanitize_forum_log_text("[14:22:13] [COMPETITOR] ## 测试段落") == ""


def test_sanitize_truncates_to_max_chars_on_line_boundary():
    lines = [f"[10:00:{i:02d}] [REVIEW] {'正' * 60}第{i}条" for i in range(20)]
    cleaned = sanitize_forum_log_text("\n".join(lines), max_chars=400)
    assert len(cleaned) <= 400
    assert cleaned
    # 不能把一行切一半
    for line in cleaned.splitlines():
        assert line.startswith("[10:00:")


def test_host_speech_placeholder_is_dropped():
    assert format_host_speech_for_prompt("") == ""
    assert format_host_speech_for_prompt("## 测试段落\\n这是初始总结。") == ""
    assert format_host_speech_for_prompt("这段是占位内容，等真实数据") == ""


def test_host_speech_prompt_carries_discipline_and_is_truncated():
    prompt = format_host_speech_for_prompt("主持人" * 2000)
    assert "仅作背景参考" in prompt
    assert "不要在正文中回应" in prompt
    assert "已截断" in prompt
    assert len(prompt) < HOST_SPEECH_MAX_CHARS + 600


def test_forum_handler_writes_into_tmp_dir_during_tests():
    """conftest 的隔离夹具必须把论坛日志挡在仓库 logs/ 之外。"""
    from engines.ForumEngine.handler import ForumEventHandler

    handler = ForumEventHandler()
    assert "logs/forum.log" not in str(handler.forum_log_file)
    handler._write_forum_log("测试用例写入", "SYSTEM")
    assert handler.forum_log_file.exists()
    assert "测试用例写入" in handler.forum_log_file.read_text(encoding="utf-8")
