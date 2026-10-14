"""runner 冒烟端到端测试：smoke 数据集 + fixture 回放，零成本跑通全骨架。"""

import json
from pathlib import Path

from app.utils import forum_reader

SMOKE = Path(__file__).resolve().parent.parent / "evals" / "datasets" / "smoke.yaml"


def _run_smoke(tmp_path, monkeypatch):
    import evals.runner as runner
    import evals.workspace as ws_mod

    monkeypatch.setattr(ws_mod, "_EVAL_ROOT", tmp_path)
    try:
        rc = runner.main(["--dataset", str(SMOKE), "--arms", "subsets", "--repeat", "1"])
    finally:
        forum_reader.set_host_feedback_enabled(True)
    assert rc == 0
    run_dirs = list(tmp_path.iterdir())
    assert len(run_dirs) == 1, run_dirs
    return run_dirs[0]


def _load(path):
    return [json.loads(l) for l in open(path, encoding="utf-8")]


def test_runner_smoke_produces_engine_and_arm_runs(tmp_path, monkeypatch):
    ws = _run_smoke(tmp_path, monkeypatch)
    engine_runs = _load(ws / "engine_runs.jsonl")
    arm_runs = _load(ws / "runs.jsonl")
    # 2 query × 3 引擎（缓存去重后只需 3 次引擎实跑 × 2 query）
    assert len(engine_runs) == 6, len(engine_runs)
    # 2 query × 7 子集
    assert len(arm_runs) == 14, len(arm_runs)


def test_runner_smoke_coverage_reflects_subset(tmp_path, monkeypatch):
    ws = _run_smoke(tmp_path, monkeypatch)
    arm_runs = _load(ws / "runs.jsonl")
    by_key = {(r["arm"], r["query_id"]): r for r in arm_runs}
    s3 = by_key[("RCT", "smoke-s3-001")]["metrics"]["coverage"]
    s3_r = by_key[("R", "smoke-s3-001")]["metrics"]["coverage"]
    assert s3 == 1.0
    assert s3_r == 1 / 3


def test_report_generates_stratified_shapley(tmp_path, monkeypatch):
    ws = _run_smoke(tmp_path, monkeypatch)
    import evals.report as report
    from evals.types import ArmRun

    arm_runs = [ArmRun.from_dict(r) for r in _load(ws / "runs.jsonl")]
    items = [i for i in _iter_items()]
    text = report.build_report_text("t", _FakeWS(ws, arm_runs), items)
    assert "φ_R" in text
    assert "smoke-s3-001" in text


def _iter_items():
    from evals.datasets import load_dataset
    _, items = load_dataset(SMOKE)
    return items


class _FakeWS:
    def __init__(self, root, arm_runs):
        self.root = root
        self._arm_runs = arm_runs

    def load_arm_runs(self):
        return [r.to_dict() for r in self._arm_runs]
