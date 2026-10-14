"""闭式 Shapley 值与交互效应的单元测试（evals/shapley.py）。"""

from evals.shapley import (
    bootstrap_ci,
    marginal_contribution,
    pairwise_interaction,
    shapley_values,
    triple_interaction,
)


def _additive_v():
    # 纯可加：V(S) = |S|
    return {
        frozenset(): 0.0,
        frozenset({"review"}): 1.0,
        frozenset({"competitor"}): 1.0,
        frozenset({"trend"}): 1.0,
        frozenset({"review", "competitor"}): 2.0,
        frozenset({"review", "trend"}): 2.0,
        frozenset({"competitor", "trend"}): 2.0,
        frozenset({"review", "competitor", "trend"}): 3.0,
    }


def test_shapley_additive_each_gets_own_contribution():
    phi = shapley_values(_additive_v())
    assert phi == {"review": 1.0, "competitor": 1.0, "trend": 1.0}


def test_shapley_only_review_contributes():
    v = {
        frozenset(): 0.0,
        frozenset({"review"}): 1.0,
        frozenset({"competitor"}): 0.0,
        frozenset({"trend"}): 0.0,
        frozenset({"review", "competitor"}): 1.0,
        frozenset({"review", "trend"}): 1.0,
        frozenset({"competitor", "trend"}): 0.0,
        frozenset({"review", "competitor", "trend"}): 1.0,
    }
    phi = shapley_values(v)
    assert phi["review"] == 1.0
    assert phi["competitor"] == 0.0
    assert phi["trend"] == 0.0


def test_marginal_contribution_definition():
    v = _additive_v()
    # Δ_C({R}) = V({R,C}) - V({R}) = 2 - 1 = 1
    assert marginal_contribution(v, "competitor", frozenset({"review"})) == 1.0


def test_pairwise_interaction_zero_when_additive():
    v = _additive_v()
    assert pairwise_interaction(v, "competitor", "trend") == 0.0


def test_pairwise_interaction_positive_when_complementary():
    # V({R,C}) = 3 > V(R)+V(C) = 2 → Syn(R,C) = 1
    v = _additive_v()
    v[frozenset({"review", "competitor"})] = 3.0
    assert pairwise_interaction(v, "review", "competitor") == 1.0


def test_triple_interaction():
    v = _additive_v()
    # 三体交互 = V(RCT) - ΣV(i) - ΣSyn(i,j) = 3 - 3 - 0 = 0
    assert triple_interaction(v, "review", "competitor", "trend") == 0.0


def test_bootstrap_ci_orders_and_empty():
    lo, hi = bootstrap_ci([1.0, 2.0, 3.0], n_boot=200, seed=0)
    assert lo <= hi
    lo2, hi2 = bootstrap_ci([], n_boot=10)
    assert lo2 != lo2 and hi2 != hi2  # NaN for empty
