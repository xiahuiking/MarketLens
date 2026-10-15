"""单 Agent 基线（P2b）—— 合并 12 工具（7 SQL + 5 检索）的同构引擎。

回答 Q1：分工成三个 Agent 是否优于一个合并 Agent。公平性约束见 DESIGN 3.4：
同模型、同提示词工程投入、算力匹配（``single_selfreflect`` 用反思轮次烧完 RCT 预算）。
"""

from .agent import run_research

__all__ = ["run_research"]
