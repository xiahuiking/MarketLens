"""客观指标（纯函数）。

每个指标签名统一为 ``fn(item: AnswerKeyItem, arm_run: ArmRun, engine_runs: dict) -> float``，
只依赖答案键 + 臂产出 + 引擎产出，不依赖 judge。Shapley 与分层报告都消费这里的
标量；「无适用样本」时返回 0.0（报告层按 item 的分层决定是否采信）。

P2 阶段：coverage / coverage_by_channel / attribution_accuracy / conflict_recall /
abstention_correctness / redundancy 有确定性实现；citation_verifiability 用「引用是否带
URL」近似；unsupported_claim_rate / conflict_precision 为占位（P4 judge 接管）。
"""

from __future__ import annotations

import re

from .matcher import finding_hit
from ..datasets.answer_key import allowed_channels

_SENT_SPLIT = re.compile(r"[。！？!?\n]+")
_ABSTAIN_MARKERS = ("无法", "暂无数据", "缺乏", "没有足够", "证据不足", "无法给出", "数据不足")


def _primary_channel(policy: str) -> str:
    allowed = allowed_channels(policy)
    if len(allowed) == 3:
        return "any"
    return sorted(allowed)[0] if allowed else "any"


def _sentences(text: str) -> list:
    return [s.strip() for s in _SENT_SPLIT.split(text or "") if s.strip()]


def coverage(item, arm_run, engine_runs) -> float:
    findings = item.required_findings
    if not findings:
        return 0.0
    hits = sum(1 for f in findings if finding_hit(f, arm_run, engine_runs)[0])
    return hits / len(findings)


def coverage_by_channel(item, arm_run, engine_runs) -> dict:
    buckets: dict = {}
    for f in item.required_findings:
        ch = _primary_channel(f.channel_policy)
        hit, _ = finding_hit(f, arm_run, engine_runs)
        buckets.setdefault(ch, {"hit": 0, "total": 0})
        buckets[ch]["total"] += 1
        if hit:
            buckets[ch]["hit"] += 1
    return {k: v["hit"] / v["total"] if v["total"] else 0.0 for k, v in buckets.items()}


def citation_verifiability(item, arm_run, engine_runs) -> float:
    citations = [c for r in engine_runs.values() for c in (r.citations or [])]
    if not citations:
        return 0.0
    verifiable = sum(1 for c in citations if c.get("url"))
    return verifiable / len(citations)


def attribution_accuracy(item, arm_run, engine_runs) -> float:
    correct = attributed = 0
    for f in item.required_findings:
        hit, channel = finding_hit(f, arm_run, engine_runs)
        if hit and channel is not None:
            attributed += 1
            if channel in allowed_channels(f.channel_policy):
                correct += 1
    return correct / attributed if attributed else 0.0


def conflict_recall(item, arm_run, engine_runs) -> float:
    conflicts = item.conflicts
    if not conflicts:
        return 0.0
    from .matcher import _content_tokens, _normalize, _overlap

    text = _normalize(arm_run.final_report_md)
    recalled = 0
    for c in conflicts:
        tokens = _content_tokens(_normalize(c.description))
        if tokens and _overlap(tokens, text) >= 0.5:
            recalled += 1
    return recalled / len(conflicts)


def conflict_precision(item, arm_run, engine_runs) -> float:
    # 占位：P2 阶段无法确定性抽取「声称识别的冲突」，由 P4 judge 接管。
    return 0.0


def abstention_correctness(item, arm_run, engine_runs) -> float:
    if item.expect != "abstain":
        return 1.0  # 占位：非弃权项默认判「正确地未弃权」
    text = (arm_run.final_report_md or "")
    abstained = any(m in text for m in _ABSTAIN_MARKERS)
    return 1.0 if abstained else 0.0


def unsupported_claim_rate(item, arm_run, engine_runs) -> float:
    # 占位：需要 claim 级证据链（P4 judge / 引用回查）才能可靠计算。
    return 0.0


def redundancy(item, arm_run, engine_runs) -> float:
    sents = _sentences(arm_run.final_report_md)
    if not sents:
        return 0.0
    return 1.0 - len(set(sents)) / len(sents)
