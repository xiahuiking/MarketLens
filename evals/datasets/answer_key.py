"""答案键（answer key）schema 与加载器（DESIGN 4.3）。

答案键是客观指标的唯一事实源。每条必需发现（required finding）都带
``channel_policy``，用于：

1. ``satisfiable(policy, S)`` —— 先**预测** leave-one-out 覆盖率，再与实测比对，
   是答案键自身质量的校验装置（DESIGN 2.3 / 5.2）；
2. ``allowed_channels(policy)`` —— 把 finding 归到来源通道。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

ALL_CHANNELS = {"review", "competitor", "trend"}


@dataclass
class Finding:
    id: str
    statement: str
    channel_policy: str            # only-X | X-or-Y | any
    verify: dict = field(default_factory=dict)  # {kind: db_query|url_set, ...}

    @classmethod
    def from_dict(cls, d: dict) -> "Finding":
        return cls(
            id=d["id"],
            statement=d["statement"],
            channel_policy=d.get("channel_policy", "any"),
            verify=d.get("verify", {}),
        )


@dataclass
class Conflict:
    id: str
    between: list
    description: str
    expected_direction: str

    @classmethod
    def from_dict(cls, d: dict) -> "Conflict":
        return cls(
            id=d["id"],
            between=d.get("between", []),
            description=d.get("description", ""),
            expected_direction=d.get("expected_direction", ""),
        )


@dataclass
class AnswerKeyItem:
    id: str
    stratum: str                   # S1..S5
    query: str
    entity: dict = field(default_factory=dict)
    required_findings: list = field(default_factory=list)   # list[Finding]
    conflicts: list = field(default_factory=list)           # list[Conflict]
    expect: str = "report"         # report | abstain

    @classmethod
    def from_dict(cls, d: dict) -> "AnswerKeyItem":
        return cls(
            id=d["id"],
            stratum=d.get("stratum", "S1"),
            query=d["query"],
            entity=d.get("entity", {}),
            required_findings=[Finding.from_dict(f) for f in d.get("required_findings", [])],
            conflicts=[Conflict.from_dict(c) for c in d.get("conflicts", [])],
            expect=d.get("expect", "report"),
        )


def load_answer_key(path: str | Path) -> list[AnswerKeyItem]:
    """从 YAML 加载答案键（顶层是一个 item 列表）。"""
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if not isinstance(raw, list):
        raise ValueError(f"答案键顶层必须是列表，得到 {type(raw).__name__}")
    return [AnswerKeyItem.from_dict(item) for item in raw]


def load_dataset(path: str | Path) -> tuple:
    """加载数据集：返回 (fixture: str|None, items: list[AnswerKeyItem])。

    支持两种顶层格式：裸 item 列表，或 ``{fixture: ..., items: [...]}``。
    """
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if isinstance(raw, list):
        return (None, [AnswerKeyItem.from_dict(i) for i in raw])
    if not isinstance(raw, dict):
        raise ValueError(f"数据集顶层必须是列表或 dict，得到 {type(raw).__name__}")
    fixture = raw.get("fixture")
    items = raw.get("items", [])
    return (fixture, [AnswerKeyItem.from_dict(i) for i in items])


def allowed_channels(policy: str) -> set:
    """把 channel_policy 展开成允许的来源通道集合。"""
    p = (policy or "any").strip().lower()
    if p == "any":
        return set(ALL_CHANNELS)
    if p.startswith("only-"):
        ch = p[len("only-"):]
        return {ch} if ch in ALL_CHANNELS else set()
    if "-or-" in p:
        a, b = p.split("-or-", 1)
        return {c for c in (a, b) if c in ALL_CHANNELS}
    # 未知策略保守当作 any
    return set(ALL_CHANNELS)


def satisfiable(policy: str, enabled: set) -> bool:
    """该 finding 是否可能被 enabled 通道集合满足。"""
    allowed = allowed_channels(policy)
    if not allowed:
        return False
    if policy.strip().lower().startswith("only-"):
        return allowed <= enabled
    return bool(allowed & enabled)
