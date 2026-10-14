"""客观指标（纯函数）+ 确定性 finding 命中器 + 预测覆盖率。"""

from .matcher import finding_hit
from .objective import (
    abstention_correctness,
    attribution_accuracy,
    citation_verifiability,
    conflict_precision,
    conflict_recall,
    coverage,
    coverage_by_channel,
    redundancy,
    unsupported_claim_rate,
)
from .predicted import predicted_coverage

__all__ = [
    "finding_hit",
    "abstention_correctness",
    "attribution_accuracy",
    "citation_verifiability",
    "conflict_precision",
    "conflict_recall",
    "coverage",
    "coverage_by_channel",
    "redundancy",
    "unsupported_claim_rate",
    "predicted_coverage",
]
