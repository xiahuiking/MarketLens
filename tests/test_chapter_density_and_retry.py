"""回归测试：章节密度校验与「内容稀疏」重试兜底。

两个历史 bug：

1. ``_ensure_content_density`` 只数**顶层**非 heading 块。LLM 偶尔把整章内容塞进
   单个 ``callout``，顶层就只剩 1 个有效块 —— 实测第4章「顶层 2 个块 / 嵌套 35 个
   块 / 5271 字」被判"正文不足：有效区块 1 个"，连续失败 3 次。
2. ``except ChapterContentError`` 排在通用 ``except (..., ValueError)`` 之后，
   而 ChapterContentError 继承自 ValueError → 稀疏兜底分支永不执行，本该"带警告
   降级出报告"的章节变成整份报告失败（``在第3次尝试后仍失败``）。
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_PROJ_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

from engines.ReportEngine.core import TemplateSection  # noqa: E402
from engines.ReportEngine.nodes.generate_chapters import (  # noqa: E402
    ChapterContentError,
    GenerateChaptersNode,
)


def _paragraph(text: str) -> dict:
    return {"type": "paragraph", "inlines": [{"text": text, "marks": []}]}


def _bare_node() -> GenerateChaptersNode:
    """不跑 __init__（避免依赖 llm/validator/storage），只测纯逻辑。"""
    return GenerateChaptersNode.__new__(GenerateChaptersNode)


def test_single_container_chapter_is_not_sparse():
    node = _bare_node()
    chapter = {
        "title": "四、评论趋势与热度对比",
        "blocks": [
            {"type": "heading", "level": 1, "text": "四、评论趋势与热度对比"},
            {
                "type": "callout",
                "tone": "info",
                "blocks": [
                    {"type": "heading", "level": 2, "text": "4.1 趋势"},
                    _paragraph("评论量在 2026 年二季度环比上升 18%，其中无线降噪占比最高。" * 12),
                    _paragraph("好评集中在降噪与续航，差评集中在佩戴压耳与固件稳定性。" * 12),
                    {"type": "table", "rows": [{"cells": [{"blocks": [_paragraph("型号")]}]}]},
                ],
            },
        ],
    }
    # 旧实现会因为顶层只有 1 个非 heading 块而抛异常
    node._ensure_content_density(chapter)


def test_heading_only_chapter_is_sparse():
    node = _bare_node()
    chapter = {
        "title": "空章节",
        "blocks": [{"type": "heading", "level": 1, "text": "空章节"}],
    }
    with pytest.raises(ChapterContentError) as exc:
        node._ensure_content_density(chapter)
    assert "有效区块 0 个" in str(exc.value)


def test_content_block_count_flattens_containers():
    node = _bare_node()
    blocks = [
        {"type": "heading", "level": 1, "text": "标题"},
        {"type": "callout", "blocks": [_paragraph("a" * 40), _paragraph("b" * 40)]},
        _paragraph("c" * 40),
        {"type": "divider"},
    ]
    assert node._count_content_blocks(blocks) == 3


class _StubStorage:
    def __init__(self, root: Path):
        self.root = root

    def start_session(self, report_id, meta):
        path = self.root / str(report_id)
        path.mkdir(parents=True, exist_ok=True)
        return path


def _section() -> TemplateSection:
    return TemplateSection(
        title="四、评论趋势与热度对比",
        slug="section-4-0",
        order=40,
        depth=1,
        raw_title="4.0 评论趋势与热度对比",
        number="4",
        chapter_id="S4",
    )


def _sparse_node(tmp_path: Path) -> GenerateChaptersNode:
    node = _bare_node()
    node.ctx = SimpleNamespace(
        config=SimpleNamespace(CHAPTER_JSON_MAX_ATTEMPTS=2),
        chapter_storage=_StubStorage(tmp_path),
        stream_handler=None,
        json_rescue_clients=None,
    )

    def _always_sparse(section, context, run_dir, **kwargs):
        raise ChapterContentError(
            "四、评论趋势与热度对比 正文不足：有效区块 1 个，估算字符数 5271，叙述性字符数 4604",
            chapter={
                "chapterId": section.chapter_id,
                "title": section.title,
                "blocks": [{"type": "heading", "level": 1, "text": section.title}, _paragraph("正文" * 60)],
            },
            body_characters=5271,
            narrative_characters=4604,
            non_heading_blocks=1,
        )

    node.run = _always_sparse  # type: ignore[assignment]
    return node


def test_sparse_chapter_degrades_instead_of_failing_the_report(tmp_path):
    """稀疏章节在第 3 次尝试后应带警告降级，而不是抛 ChapterJsonParseError。"""
    node = _sparse_node(tmp_path)
    state = {
        "template_sections": [_section()],
        "generation_context": {},
        "report_id": "report_test",
    }
    result = node(state)
    chapters = result["chapters"]
    assert len(chapters) == 1
    assert chapters[0]["meta"]["contentSparseWarning"] is True
    # 警告段落插在标题之后
    assert chapters[0]["blocks"][1]["meta"]["role"] == "content-sparse-warning"
