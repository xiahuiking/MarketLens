"""预测覆盖率 —— 不跑 LLM 的廉价代理（DESIGN 5.2）。

``predicted_coverage(item, S)`` 按答案键的 ``channel_policy`` 算出：只启用通道集合
``S`` 时，最多能命中多少必需发现。它是答案键自身的校验装置：实测 leave-one-out
的 ``Δ_i`` 与「预测的下降」对比，差值即证据可替代性。
"""

from __future__ import annotations

from ..datasets.answer_key import satisfiable


def predicted_coverage(item, enabled: set) -> float:
    findings = item.required_findings
    if not findings:
        return 0.0
    hits = sum(1 for f in findings if satisfiable(f.channel_policy, enabled))
    return hits / len(findings)
