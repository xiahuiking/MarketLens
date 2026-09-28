"""Markdown 报告输出的安全清洗工具（三个引擎共用）。

背景：三个引擎的 ``format_report`` 节点曾把 **Markdown 报告**交给
``remove_reasoning_from_output()`` 清洗，而那个函数是按「第一个 ``{`` 或 ``[``
之前的内容全部丢弃」设计的 **JSON 提取器**。报告正文里只要出现一个 Markdown
链接或来源标注（如 ``[官方/一手]``），报告开头就会被整段删掉——实测单次丢失
50%~80% 正文（``logs/review.log``：9622 字符 → 1839 字符），落盘报告只剩半句话。

这里只做**不会丢正文**的清洗：

- 剥离 ``<thought>`` / ``<thinking>`` / ``<reasoning>`` 标签块；
- 若整段输出以「思考：」这类前言开头且后面存在 Markdown 标题，则从标题开始；
- 去掉 ```` ``` ```` 围栏行；
- 没有标题时补一个默认标题。

**绝不**因为正文里出现 ``[`` 或 ``{`` 就截断。
"""

from __future__ import annotations

import re

__all__ = [
    "strip_reasoning",
    "unwrap_code_fence",
    "clean_markdown_report",
]

_REASONING_BLOCK_RE = re.compile(
    r"<(?:thought|thinking|reasoning)>.*?</(?:thought|thinking|reasoning)>",
    re.IGNORECASE | re.DOTALL,
)

_REASONING_LEAD_RE = re.compile(
    r"^\s*(?:思考|推理|推理过程|分析过程|reasoning|thinking)\s*[:：]",
    re.IGNORECASE,
)

_HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+\S", re.MULTILINE)

# 报告必须有且以一个一级标题开头（``## 小节`` 不算）
_H1_START_RE = re.compile(r"^[ \t]{0,3}#[ \t]+\S")

_FENCE_LINE_RE = re.compile(r"^[ \t]*```[a-zA-Z0-9_-]*[ \t]*$")


def strip_reasoning(text: str) -> str:
    """剥离显式推理块/前言；无法确认是前言时一律保留原文。"""
    if not text:
        return ""

    cleaned = _REASONING_BLOCK_RE.sub("", text)

    # 「思考：<一大段前言>」这种形态：只有后面确实存在 Markdown 标题时才丢弃前言，
    # 否则宁可保留一段前言，也不冒删掉正文的风险。
    if _REASONING_LEAD_RE.match(cleaned):
        match = _HEADING_RE.search(cleaned)
        if match:
            cleaned = cleaned[match.start():]

    return cleaned.strip()


def unwrap_code_fence(text: str) -> str:
    """去掉 Markdown 围栏行。

    只删围栏本身（```` ``` ```` / ```` ```markdown ````），**不删**围栏内的内容行；
    若整段输出就是单个围栏块，则直接取块内内容。
    """
    if not text:
        return ""

    lines = text.strip().splitlines()
    if len(lines) >= 2 and lines[0].strip().startswith("```") \
            and lines[-1].strip().startswith("```"):
        # 整体被一个围栏块包住：原样保留块内内容（含空行）
        return "\n".join(lines[1:-1]).strip()

    kept = [ln for ln in lines if not _FENCE_LINE_RE.match(ln)]
    return "\n".join(kept).strip()


def clean_markdown_report(text: str, default_title: str = "深度研究报告") -> str:
    """清洗 LLM 产出的 Markdown 报告；为空时返回空串（由调用方决定是否兜底）。"""
    cleaned = unwrap_code_fence(strip_reasoning(text or ""))
    if not cleaned:
        return ""
    # 报告应以一级标题开头；只有 ``## 小节`` 时补一个默认 H1，避免渲染出无标题文档
    if not _H1_START_RE.match(cleaned):
        cleaned = f"# {default_title}\n\n{cleaned}"
    return cleaned.strip()
