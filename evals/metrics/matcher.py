"""确定性 finding 命中器（P2 占位，P4 judge 接管）。

这是评测里唯一「模糊」的环节：把「答案键里的必需发现」映射到「报告里是否出现了
该发现」。当前用词元重叠做确定性判定，供 smoke / 早期分层结论使用；真正的语义
匹配由 P4 的 judge + 校准集接管（DESIGN 5.4）。

判定规则：把 statement 拆成内容词元，报告文本覆盖 >= 50% 即认为命中。通道归属
取三个引擎里覆盖词元最多者。
"""

from __future__ import annotations

import re
from typing import Optional

_CJK = re.compile(r"[一-鿿]+")
_LATIN = re.compile(r"[A-Za-z0-9]+")


def _normalize(text: str) -> str:
    return (text or "").lower()


def _content_tokens(text: str) -> list:
    """抽取内容词元：连续中文串 + 英文/数字串（丢弃太短的英文串）。"""
    tokens: list = []
    for m in _CJK.finditer(text):
        tokens.append(m.group())
    for m in _LATIN.finditer(text):
        w = m.group()
        if len(w) >= 2:
            tokens.append(w)
    return tokens


def _overlap(tokens: list, text: str) -> float:
    if not tokens:
        return 0.0
    hits = sum(1 for t in tokens if t in text)
    return hits / len(tokens)


def finding_hit(
    finding,
    arm_run,
    engine_runs: dict,
    threshold: float = 0.5,
) -> tuple[bool, Optional[str]]:
    """返回 (是否命中, 主要来源通道名或 None)。"""
    report_md = _normalize(arm_run.final_report_md)
    tokens = _content_tokens(_normalize(finding.statement))
    if not tokens:
        hit = _normalize(finding.statement) in report_md
        return (hit, None)
    hit = _overlap(tokens, report_md) >= threshold

    # 通道归属：三个引擎里谁覆盖词元最多
    best_channel = None
    best_score = 0.0
    for engine, run in engine_runs.items():
        score = _overlap(tokens, _normalize(run.final_report))
        if score > best_score:
            best_score = score
            best_channel = engine
    return (hit, best_channel)
