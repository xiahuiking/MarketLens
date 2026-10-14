"""MarketLens 多智能体 benchmark 评测系统。

设计见 evals/DESIGN.md。本包实现 P0 前提 + P2 子集族核心：

- 子集族 7 臂（{R},{C},{T},{R,C},{R,T},{C,T},{R,C,T}）在 host=off 下运行，
  用闭式 Shapley 值 + 交互效应回答「每个 Agent 是否有正贡献」；
- 客观指标（coverage / attribution / conflict / abstention / redundancy）；
- fixture 默认回放，真实 LLM/搜索由 ``--live`` 显式开启。
"""
