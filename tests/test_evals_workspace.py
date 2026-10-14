"""workspace 路径隔离的单元测试（evals/workspace.py）。"""

import pytest

from evals.types import ArmRun, EngineRun
from evals.workspace import Workspace


def test_workspace_refuses_nonempty_without_resume(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_id = "r1"
    ws = Workspace(run_id)
    ws.write_config({"k": "v"})
    with pytest.raises(FileExistsError):
        Workspace(run_id)  # 非空且未 --resume


def test_workspace_resume_allows_nonempty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_id = "r2"
    Workspace(run_id).write_config({"k": "v"})
    ws = Workspace(run_id, resume=True)
    assert ws.config_path.exists()


def test_jsonl_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ws = Workspace("r3")
    er = EngineRun(query_id="q", engine="review", repeat=0, model="m", final_report="md")
    ws.append_engine_run(er)
    ar = ArmRun(query_id="q", arm="R", host_mode="off", repeat=0, final_report_md="md")
    ws.append_arm_run(ar)

    assert [r["engine"] for r in ws.load_engine_runs()] == ["review"]
    assert [r["arm"] for r in ws.load_arm_runs()] == ["R"]


def test_config_snapshot_has_git_and_env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ws = Workspace("r4")
    ws.write_config({"model": "m"})
    import json
    cfg = json.loads(ws.config_path.read_text(encoding="utf-8"))
    assert cfg["model"] == "m"
    assert "git_commit" in cfg
    assert "env_fingerprint" in cfg
