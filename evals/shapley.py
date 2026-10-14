"""闭式 Shapley 值 + 交互效应 + bootstrap 置信区间（DESIGN 1 / 7）。

``n = 3`` 时无需采样近似，直接用闭式解。价值函数 ``v`` 是一个集合函数
``v: frozenset[str] -> float``（按分层、按指标分别构造）。
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from typing import Iterable

AGENTS = ("review", "competitor", "trend")


def marginal_contribution(v: Mapping, agent: str, coalition: frozenset) -> float:
    """Δ_i(S) = v(S ∪ {i}) − v(S)。"""
    with_i = v[frozenset(set(coalition) | {agent})]
    without_i = v[frozenset(coalition)]
    return with_i - without_i


def shapley_values(v: Mapping) -> dict:
    """n=3 闭式 Shapley 值。"""
    r, c, t = AGENTS
    phi = {}
    for i in AGENTS:
        others = [a for a in AGENTS if a != i]
        j, k = others
        phi[i] = (
            (1 / 3) * marginal_contribution(v, i, frozenset())
            + (1 / 6) * (marginal_contribution(v, i, frozenset({j}))
                         + marginal_contribution(v, i, frozenset({k})))
            + (1 / 3) * marginal_contribution(v, i, frozenset({j, k}))
        )
    return phi


def pairwise_interaction(v: Mapping, i: str, j: str) -> float:
    """Syn(i,j) = v({i,j}) − v({i}) − v({j})。>0 互补，<0 冗余。"""
    return v[frozenset({i, j})] - v[frozenset({i})] - v[frozenset({j})]


def triple_interaction(v: Mapping, i: str, j: str, k: str) -> float:
    """Syn(i,j,k) = v({i,j,k}) − Σ v({i}) − Σ Syn(i,j)。"""
    total = v[frozenset({i, j, k})]
    singles = v[frozenset({i})] + v[frozenset({j})] + v[frozenset({k})]
    pairs = (pairwise_interaction(v, i, j)
             + pairwise_interaction(v, i, k)
             + pairwise_interaction(v, j, k))
    return total - singles - pairs


def bootstrap_ci(values: Iterable[float], n_boot: int = 2000, alpha: float = 0.05,
                 seed: int = 0) -> tuple:
    """对「每个 query 一个数」的样本做 bootstrap 百分位置信区间。"""
    samples = list(values)
    if not samples:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    means = []
    n = len(samples)
    for _ in range(n_boot):
        resampled = [samples[rng.randrange(n)] for _ in range(n)]
        means.append(sum(resampled) / n)
    means.sort()
    lo = means[int(alpha / 2 * (len(means) - 1))]
    hi = means[int((1 - alpha / 2) * (len(means) - 1))]
    return (lo, hi)
