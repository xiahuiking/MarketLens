"""Judge（LLM-as-judge）—— P4 阶段实现，当前仅占位。

客观指标（evals.metrics）是 P2 的唯一证据来源；judge 只补充融合质量 / 洞见深度，
且必须带人工校准集 + Cohen's κ（DESIGN 5.4），未达标不予采信。
"""


def rubric() -> None:
    raise NotImplementedError("judge rubric 在 P4 阶段实现")
