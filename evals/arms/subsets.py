"""子集族臂定义与 3 槽报告拼接（DESIGN 3.2-A）。

``generate_report`` 的 NormalizeReportsNode 按**位置**映射 ``[trend, competitor, review]``，
因此任何子集都要拼成 3 元素列表、空串占位。
"""

from __future__ import annotations

SUBSETS: dict = {
    "R": ["review"],
    "C": ["competitor"],
    "T": ["trend"],
    "RC": ["review", "competitor"],
    "RT": ["review", "trend"],
    "CT": ["competitor", "trend"],
    "RCT": ["review", "competitor", "trend"],
}

# 位置槽顺序：index 0 = trend, 1 = competitor, 2 = review
_SLOT_ORDER = ("trend", "competitor", "review")


def compose_reports(engine_md: dict) -> list:
    """把 {engine: markdown} 拼成 [trend, competitor, review]，缺失槽填空串。"""
    return [engine_md.get(slot, "") for slot in _SLOT_ORDER]
