"""生成评测报告（``python -m evals.report``）。

从 runs.jsonl 读臂产出，按分层构造价值函数 ``V: 2^N -> ℝ``，算闭式 Shapley 值与
交互效应，输出分层矩阵（DESIGN 5.5：绝不跨层聚合）。
"""

from __future__ import annotations

import argparse
from collections import defaultdict

from . import shapley
from .arms import SUBSETS
from .datasets import load_dataset
from .types import ArmRun
from .workspace import Workspace

# 参与 Shapley 的标量指标（coverage_by_channel 是 dict，单独处理）
SCALAR_METRICS = [
    "coverage",
    "citation_verifiability",
    "attribution_accuracy",
    "conflict_recall",
    "abstention_correctness",
    "unsupported_claim_rate",
    "redundancy",
]


def _mean(xs: list) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def build_value_function(arm_runs: list, metric: str) -> dict:
    """按臂聚合某指标，构造 {frozenset(engines): float}，并补 V(∅)=0。"""
    by_arm = defaultdict(list)
    for r in arm_runs:
        m = r.metrics.get(metric) if isinstance(r.metrics, dict) else None
        if m is not None:
            by_arm[r.arm].append(m)
    v = {frozenset(): 0.0}
    for arm, values in by_arm.items():
        engines = frozenset(SUBSETS[arm])
        v[engines] = _mean(values)
    return v


def _stratum_rows(arm_runs: list, metric: str) -> str:
    v = build_value_function(arm_runs, metric)
    if len(v) <= 1:
        return "（该分层无数据）"
    phi = shapley.shapley_values(v)
    syn_ct = shapley.pairwise_interaction(v, "competitor", "trend")
    syn_rc = shapley.pairwise_interaction(v, "review", "competitor")
    return (
        f"φ_R={phi['review']:+.3f}  φ_C={phi['competitor']:+.3f}  "
        f"φ_T={phi['trend']:+.3f} | Syn(R,C)={syn_rc:+.3f}  Syn(C,T)={syn_ct:+.3f}"
    )


def build_report_text(run_id: str, ws: Workspace, items: list) -> str:
    arm_runs = [ArmRun.from_dict(r) for r in ws.load_arm_runs()]
    strata: dict = {}
    for item in items:
        strata.setdefault(item.stratum, []).append(item)

    lines = [f"# MarketLens 评测报告 — {run_id}", ""]

    for metric in SCALAR_METRICS:
        lines.append(f"## 指标：{metric}")
        lines.append("")
        lines.append("| 分层 | Shapley 值 / 交互效应 |")
        lines.append("|---|---|")
        for stratum, stratum_items in strata.items():
            qids = {i.id for i in stratum_items}
            rows = [r for r in arm_runs if r.query_id in qids]
            lines.append(f"| {stratum} | {_stratum_rows(rows, metric)} |")
        lines.append("")

    lines.append("## 预测 vs 实测（答案键自检）")
    lines.append("")
    lines.append("| 分层 | query | V(R) | V(C) | V(T) | V(RCT) |")
    lines.append("|---|---|---|---|---|---|")
    from .metrics import predicted_coverage
    for item in items:
        full = {"review", "competitor", "trend"}
        lines.append(
            f"| {item.stratum} | {item.id} | "
            f"{predicted_coverage(item, {'review'}):.2f} | "
            f"{predicted_coverage(item, {'competitor'}):.2f} | "
            f"{predicted_coverage(item, {'trend'}):.2f} | "
            f"{predicted_coverage(item, full):.2f} |"
        )
    lines.append("")

    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="evals.report")
    p.add_argument("--run-id", required=True)
    p.add_argument("--dataset", required=True)
    args = p.parse_args(argv)

    _, items = load_dataset(args.dataset)
    ws = Workspace(args.run_id, resume=True)
    text = build_report_text(args.run_id, ws, items)
    out = ws.root / "report.md"
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"\n报告已写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
