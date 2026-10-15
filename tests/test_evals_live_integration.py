"""`--live` 集成测试（依赖真实 API key 与 MySQL，默认跳过）。

跑法（手动，需要 .env 配好 6 组模型 key + DB 就绪）：
    ./project_venv/bin/pytest -m integration tests/test_evals_live_integration.py
或直接跑 runner：
    ./project_venv/bin/python -m evals.runner --dataset evals/datasets/v1.yaml \
        --arms all --repeat 1 --live
"""

import pytest


@pytest.mark.integration
def test_live_single_query_smoke(tmp_path, monkeypatch):
    """真实 LLM + 搜索 + DB 跑 1 个 query 的 RCT 子集族，只断言不崩、产出记录。"""
    import evals.runner as runner
    import evals.workspace as ws_mod

    monkeypatch.setattr(ws_mod, "_EVAL_ROOT", tmp_path)
    rc = runner.main([
        "--dataset", "evals/datasets/v1.yaml",
        "--arms", "subsets", "--repeat", "1", "--live",
    ])
    assert rc == 0
    run_dirs = list(tmp_path.iterdir())
    assert run_dirs, "应产出 workspace"
