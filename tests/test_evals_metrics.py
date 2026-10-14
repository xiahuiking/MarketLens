"""客观指标与预测覆盖率的单元测试（evals/metrics）。"""

from evals.datasets.answer_key import AnswerKeyItem, Finding
from evals.metrics import (
    coverage,
    coverage_by_channel,
    predicted_coverage,
    redundancy,
)
from evals.types import ArmRun


def _item(findings):
    return AnswerKeyItem(
        id="q1", stratum="S3", query="q",
        required_findings=findings,
    )


def _f(statement, policy):
    return Finding(id="f", statement=statement, channel_policy=policy)


def _arm(md):
    return ArmRun(query_id="q1", arm="RCT", host_mode="off", repeat=0, final_report_md=md)


def _engine_runs(md_by_engine):
    from evals.types import EngineRun
    return {
        e: EngineRun(query_id="q1", engine=e, repeat=0, model="m", final_report=md)
        for e, md in md_by_engine.items()
    }


def test_coverage_hits_only_present_findings():
    item = _item([
        _f("平均评分落在 4.0 至 4.4 之间", "only-review"),
        _f("主流竞品价格区间为 50 至 90 美元", "only-competitor"),
        _f("该品类近一年搜索热度上升", "only-trend"),
    ])
    md = "平均评分落在 4.0 至 4.4 之间。该品类近一年搜索热度上升。"
    runs = _engine_runs({"review": "平均评分落在 4.0 至 4.4 之间。", "trend": "该品类近一年搜索热度上升。"})
    assert coverage(item, _arm(md), runs) == 2 / 3


def test_coverage_by_channel_buckets():
    item = _item([
        _f("平均评分落在 4.0 至 4.4 之间", "only-review"),
        _f("主流竞品价格区间为 50 至 90 美元", "only-competitor"),
    ])
    md = "平均评分落在 4.0 至 4.4 之间。"
    runs = _engine_runs({"review": "平均评分落在 4.0 至 4.4 之间。"})
    by = coverage_by_channel(item, _arm(md), runs)
    assert by["review"] == 1.0
    assert by["competitor"] == 0.0


def test_predicted_coverage_only_review():
    item = _item([_f("平均评分落在 4.0 至 4.4 之间", "only-review")])
    assert predicted_coverage(item, {"review"}) == 1.0
    assert predicted_coverage(item, {"competitor"}) == 0.0
    assert predicted_coverage(item, {"review", "competitor", "trend"}) == 1.0


def test_predicted_coverage_or_policy():
    item = _item([_f("x", "review-or-competitor")])
    assert predicted_coverage(item, {"review"}) == 1.0
    assert predicted_coverage(item, {"trend"}) == 0.0


def test_redundancy_dedups_identical_sentences():
    item = _item([])
    md = "句子A。句子A。句子B。"
    assert redundancy(item, _arm(md), {}) == 1.0 - 2 / 3
