"""评测系统核心数据契约（对应 DESIGN 8.2）。

两个 dataclass 是「引擎实跑」与「臂（含下游报告）」两种记录，
所有指标与报告都消费它们，保证指标层与臂实现解耦。
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class EngineRun:
    """单引擎单次实跑的产物（review / competitor / trend / single）。"""

    query_id: str
    engine: str                 # review | competitor | trend | single
    repeat: int
    model: str                  # 钉死的 benchmark 模型名
    final_report: str           # run_research() 返回的 markdown
    paragraphs: list = field(default_factory=list)
    citations: list = field(default_factory=list)   # _extract_citations_from_result 结构
    tokens_in: int = 0
    tokens_out: int = 0
    cost: Optional[float] = None
    llm_calls: int = 0
    search_calls: int = 0
    wall_ms: int = 0
    host_trace: list = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "EngineRun":
        return cls(**d)


@dataclass
class ArmRun:
    """一个臂配置（子集 + host 模式 + 下游报告）单次重复的产物。"""

    query_id: str
    arm: str                    # R | C | T | RC | RT | CT | RCT | single_merged | ...
    host_mode: str              # off（P2 子集族固定 off）
    repeat: int
    engines: list = field(default_factory=list)
    forum_log: str = ""         # 子集族恒为空
    final_report_md: str = ""   # MarkdownRenderer(document_ir)
    document_ir: dict = field(default_factory=dict)
    tokens_in: int = 0
    tokens_out: int = 0
    cost: Optional[float] = None
    llm_calls: int = 0
    wall_ms: int = 0
    host_trace: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ArmRun":
        return cls(**d)
