"""引擎/臂运行缓存（DESIGN 8.4）。

- 引擎输出按 ``(query_id, engine, repeat, model)`` 缓存 → 7 个子集只实跑 3 次引擎；
- 臂输出按 ``(query_id, arm, repeat, model)`` 缓存 → ``--metrics-only`` 零 LLM 调用。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .types import ArmRun, EngineRun


def engine_cache_key(query_id: str, engine: str, repeat: int, model: str) -> str:
    return f"engine__{query_id}__{engine}__r{repeat}__{model}"


def arm_cache_key(query_id: str, arm: str, repeat: int, model: str) -> str:
    return f"arm__{query_id}__{arm}__r{repeat}__{model}"


def _path(cache_dir: Path, key: str) -> Path:
    return cache_dir / f"{key}.json"


def load_engine_run(cache_dir: Path, key: str) -> Optional[EngineRun]:
    p = _path(cache_dir, key)
    if p.exists():
        return EngineRun.from_dict(json.loads(p.read_text(encoding="utf-8")))
    return None


def save_engine_run(cache_dir: Path, key: str, run: EngineRun):
    _path(cache_dir, key).write_text(
        json.dumps(run.to_dict(), ensure_ascii=False), encoding="utf-8"
    )


def load_arm_run(cache_dir: Path, key: str) -> Optional[ArmRun]:
    p = _path(cache_dir, key)
    if p.exists():
        return ArmRun.from_dict(json.loads(p.read_text(encoding="utf-8")))
    return None


def save_arm_run(cache_dir: Path, key: str, run: ArmRun):
    _path(cache_dir, key).write_text(
        json.dumps(run.to_dict(), ensure_ascii=False), encoding="utf-8"
    )
