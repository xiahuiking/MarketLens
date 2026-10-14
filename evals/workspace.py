"""路径隔离与 run 生命周期（DESIGN 8.3）。

每个 run 有独立 workspace ``data/evals/<run_id>/``，引擎 .md、报告 IR、usage 账本、
forum.log 全部落在这里，绝不写仓库根下的 ``data/report/``、``logs/``、``data/usage/``。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .types import ArmRun, EngineRun

_EVAL_ROOT = Path("data/evals")


def _env_fingerprint() -> str:
    """对 .env 相关键求 SHA-256（只记哈希，不记值）。"""
    env_path = Path(".env")
    if not env_path.exists():
        return ""
    keys = [
        "REVIEW_ENGINE_API_KEY", "REVIEW_ENGINE_BASE_URL", "REVIEW_ENGINE_MODEL_NAME",
        "COMPETITOR_ENGINE_API_KEY", "COMPETITOR_ENGINE_BASE_URL", "COMPETITOR_ENGINE_MODEL_NAME",
        "TREND_ENGINE_API_KEY", "TREND_ENGINE_BASE_URL", "TREND_ENGINE_MODEL_NAME",
        "REPORT_ENGINE_API_KEY", "REPORT_ENGINE_BASE_URL", "REPORT_ENGINE_MODEL_NAME",
        "TAVILY_API_KEY", "SEARCH_TOOL_TYPE",
    ]
    values = []
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() in keys:
                values.append(f"{k.strip()}={v.strip()}")
    except OSError:
        return ""
    return hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()[:16]


def _git_head() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip()
    except Exception:
        return ""


class Workspace:
    """管理单个 run 的目录、config 快照与 jsonl 追加。"""

    def __init__(self, run_id: str, resume: bool = False):
        self.run_id = run_id
        self.root = _EVAL_ROOT / run_id
        self._ensure_empty_or_resume(resume)

    def _ensure_empty_or_resume(self, resume: bool):
        if self.root.exists():
            if resume:
                return
            if any(self.root.iterdir()):
                raise FileExistsError(
                    f"workspace 非空且未指定 --resume：{self.root}"
                )
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "cache").mkdir(exist_ok=True)

    # ── 路径 ─────────────────────────────────────────────
    @property
    def config_path(self) -> Path:
        return self.root / "config.json"

    @property
    def engine_runs_path(self) -> Path:
        return self.root / "engine_runs.jsonl"

    @property
    def runs_path(self) -> Path:
        return self.root / "runs.jsonl"

    @property
    def metrics_path(self) -> Path:
        return self.root / "metrics.jsonl"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def engines_dir(self) -> Path:
        return self.root / "engines"

    @property
    def report_dir(self) -> Path:
        return self.root / "report"

    @property
    def usage_dir(self) -> Path:
        return self.root / "usage"

    def write_config(self, extra: dict):
        config = {
            "run_id": self.run_id,
            "git_commit": _git_head(),
            "env_fingerprint": _env_fingerprint(),
            **extra,
        }
        self.config_path.write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ── 追加记录 ─────────────────────────────────────────
    def append_engine_run(self, run: EngineRun):
        with open(self.engine_runs_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(run.to_dict(), ensure_ascii=False) + "\n")

    def append_arm_run(self, run: ArmRun):
        with open(self.runs_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(run.to_dict(), ensure_ascii=False) + "\n")

    def append_metrics(self, record: dict):
        with open(self.metrics_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # ── 读取 ─────────────────────────────────────────────
    def _read_jsonl(self, path: Path) -> list:
        if not path.exists():
            return []
        out = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    def load_engine_runs(self) -> list:
        return self._read_jsonl(self.engine_runs_path)

    def load_arm_runs(self) -> list:
        return self._read_jsonl(self.runs_path)

    def load_metrics(self) -> list:
        return self._read_jsonl(self.metrics_path)
