"""评测跑批入口（``python -m evals.runner``）。

- fixture（默认）：用 smoke/录制回放跑通骨架，不烧钱；
- ``--live``：真实 LLM + 搜索，并录制；
- ``--metrics-only``：只对已缓存的 arm run 重算指标。
"""

from __future__ import annotations

import argparse
import time
import uuid
from pathlib import Path

from . import caching, metrics
from .arms import SUBSETS, compose_reports
from .arms import engines as eng
from .datasets import load_dataset
from .types import ArmRun, EngineRun
from .workspace import Workspace

# 单 Agent 基线：arm 名 → max_reflections（single_merged=0 无反思；single_selfreflect=2 烧预算）
SINGLE_ARMS = {"single_merged": 0, "single_selfreflect": 2}

# 客观指标全集（coverage_by_channel 返回 dict，其余返回 float）
METRIC_FNS = {
    "coverage": metrics.coverage,
    "coverage_by_channel": metrics.coverage_by_channel,
    "citation_verifiability": metrics.citation_verifiability,
    "attribution_accuracy": metrics.attribution_accuracy,
    "conflict_recall": metrics.conflict_recall,
    "conflict_precision": metrics.conflict_precision,
    "abstention_correctness": metrics.abstention_correctness,
    "unsupported_claim_rate": metrics.unsupported_claim_rate,
    "redundancy": metrics.redundancy,
}


def compute_metrics(item, arm_run, engine_runs: dict) -> dict:
    out = {}
    for name, fn in METRIC_FNS.items():
        try:
            out[name] = fn(item, arm_run, engine_runs)
        except Exception as exc:  # 单指标失败不阻断整体
            out[name] = None
            out[f"{name}_error"] = str(exc)
    return out


def _engine_runner_factory(live: bool, ws: Workspace, model, base_url, api_key):
    if live:
        def run_one(engine, query):
            config = eng.build_engine_config(
                engine, ws.engines_dir / engine, model, base_url, api_key
            )
            llm = eng.build_llm_client(engine, config)
            agency = eng.build_search_agency(engine, config)
            result, summary = eng.run_engine_with_usage(engine, query, config, llm, agency)
            return result, summary
        return run_one

    from .datasets.fixtures.smoke import dispatcher

    def run_one(engine, query):
        return dispatcher.run_engine_fn(engine, query, None, None, None, None), {}
    return run_one


def _report_runner_factory(live: bool, ws: Workspace, model, base_url, api_key):
    if live:
        def run_one(query, reports):
            config = eng.build_report_config(
                {
                    "out": ws.report_dir,
                    "ir": ws.report_dir / "ir",
                    "chapter": ws.report_dir / "chapter",
                },
                model, base_url, api_key,
            )
            return eng.run_report(query, reports, config)
        return run_one

    from .datasets.fixtures.smoke import dispatcher
    return lambda query, reports: dispatcher.report_fn(query, reports, None)


def _single_engine_runner_factory(live: bool, ws: Workspace, model, base_url, api_key):
    if live:
        def run_one(arm_name, query):
            mr = SINGLE_ARMS[arm_name]
            config = eng.build_single_config(ws.engines_dir / arm_name, model, base_url, api_key)
            llm = eng.build_llm_client("competitor", config)
            agency = eng.build_search_agency("competitor", config)
            from engines.ReviewEngine.tools.search import ProductReviewDB
            review_db = ProductReviewDB()
            result, summary = eng.run_single_engine_with_usage(
                arm_name, query, config, llm, agency, review_db, mr
            )
            return result, summary
        return run_one

    from .datasets.fixtures.smoke import dispatcher

    def run_one(arm_name, query):
        return dispatcher.run_single_engine_fn(arm_name, query, None, None), {}
    return run_one


def get_or_run_engine(
    query_id: str, engine: str, repeat: int, model: str,
    ws: Workspace, run_one,
) -> EngineRun:
    key = caching.engine_cache_key(query_id, engine, repeat, model)
    cached = caching.load_engine_run(ws.cache_dir, key)
    if cached is not None:
        return cached

    started = time.perf_counter()
    query = _query_by_id[query_id]
    result, summary = run_one(engine, query)
    wall_ms = int((time.perf_counter() - started) * 1000)

    citations = eng.extract_citations(result) if result.get("paragraphs") else []
    run = EngineRun(
        query_id=query_id,
        engine=engine,
        repeat=repeat,
        model=model,
        final_report=result.get("final_report", ""),
        paragraphs=result.get("paragraphs", []),
        citations=citations,
        tokens_in=summary.get("prompt_tokens", 0),
        tokens_out=summary.get("completion_tokens", 0),
        cost=summary.get("cost"),
        llm_calls=summary.get("calls", 0),
        search_calls=len(citations),
        wall_ms=wall_ms,
    )
    caching.save_engine_run(ws.cache_dir, key, run)
    ws.append_engine_run(run)
    return run


_query_by_id: dict = {}


def run(parsed) -> int:
    fixture, items = load_dataset(parsed.dataset)
    global _query_by_id
    _query_by_id = {item.id: item.query for item in items}

    run_id = parsed.run_id or uuid.uuid4().hex[:12]
    ws = Workspace(run_id, resume=parsed.resume)

    model = parsed.model or ("smoke" if not parsed.live else _default_model())
    base_url = parsed.base_url
    api_key = parsed.api_key

    ws.write_config({
        "dataset": str(parsed.dataset),
        "fixture": fixture,
        "live": parsed.live,
        "arms": parsed.arms,
        "repeat": parsed.repeat,
        "model": model,
    })

    engine_runner = _engine_runner_factory(parsed.live, ws, model, base_url, api_key)
    report_runner = _report_runner_factory(parsed.live, ws, model, base_url, api_key)

    # 子集族一律 host=off（DESIGN 2.2）——纯拼接，保证 V 是干净集合函数。
    from app.utils import forum_reader
    forum_reader.set_host_feedback_enabled(False)

    for item in items:
        for repeat in range(parsed.repeat):
            if parsed.arms in ("subsets", "all"):
                # 先确保该 query 的三个引擎跑（缓存复用，7 个子集只实跑 3 次）
                engine_runs: dict = {}
                for eng_name in ("review", "competitor", "trend"):
                    engine_runs[eng_name] = get_or_run_engine(
                        item.id, eng_name, repeat, model, ws, engine_runner
                    )

                for arm, engines_list in SUBSETS.items():
                    arm_key = caching.arm_cache_key(item.id, arm, repeat, model)
                    cached_arm = caching.load_arm_run(ws.cache_dir, arm_key)
                    if cached_arm is not None and not parsed.live:
                        arm_run = cached_arm
                    else:
                        slots = compose_reports({
                            e: engine_runs[e].final_report for e in engines_list
                        })
                        try:
                            report = report_runner(item.query, slots)
                        except Exception as exc:
                            report = {"document_ir": {}, "final_report_md": "", "error": str(exc)}

                        arm_run = ArmRun(
                            query_id=item.id,
                            arm=arm,
                            host_mode="off",
                            repeat=repeat,
                            engines=engines_list,
                            forum_log="",
                            final_report_md=report.get("final_report_md", ""),
                            document_ir=report.get("document_ir", {}),
                            tokens_in=sum(engine_runs[e].tokens_in for e in engines_list),
                            tokens_out=sum(engine_runs[e].tokens_out for e in engines_list),
                            cost=_sum_cost(engine_runs[e] for e in engines_list),
                            llm_calls=sum(engine_runs[e].llm_calls for e in engines_list),
                            wall_ms=sum(engine_runs[e].wall_ms for e in engines_list),
                            error=report.get("error"),
                        )
                        caching.save_arm_run(ws.cache_dir, arm_key, arm_run)

                    subset_runs = {e: engine_runs[e] for e in engines_list}
                    arm_run.metrics = compute_metrics(item, arm_run, subset_runs)
                    ws.append_arm_run(arm_run)

            if parsed.arms in ("single", "all"):
                single_runner = _single_engine_runner_factory(
                    parsed.live, ws, model, base_url, api_key
                )
                for arm_name in SINGLE_ARMS:
                    single_run = get_or_run_engine(
                        item.id, arm_name, repeat, model, ws, single_runner
                    )
                    arm_run = ArmRun(
                        query_id=item.id,
                        arm=arm_name,
                        host_mode="off",
                        repeat=repeat,
                        engines=[arm_name],
                        forum_log="",
                        final_report_md=single_run.final_report,
                        document_ir={},
                        tokens_in=single_run.tokens_in,
                        tokens_out=single_run.tokens_out,
                        cost=single_run.cost,
                        llm_calls=single_run.llm_calls,
                        wall_ms=single_run.wall_ms,
                    )
                    arm_run.metrics = compute_metrics(item, arm_run, {arm_name: single_run})
                    ws.append_arm_run(arm_run)

    return 0


def _default_model() -> str:
    from app import config
    return config.settings.REVIEW_ENGINE_MODEL_NAME or "kimi-k2-0711-preview"


def _sum_cost(runs) -> float | None:
    costs = [r.cost for r in runs if r.cost is not None]
    return sum(costs) if costs else None


def metrics_only(parsed) -> int:
    """只对已缓存的 arm run 重算指标（不跑引擎/报告）。"""
    from .datasets import load_dataset
    fixture, items = load_dataset(parsed.dataset)
    by_id = {item.id: item for item in items}
    ws = Workspace(parsed.run_id, resume=True)

    engine_records = ws.load_engine_runs()
    engines_by_key = {
        (r["query_id"], r["engine"], r["repeat"]): EngineRun.from_dict(r)
        for r in engine_records
    }
    for arm in ws.load_arm_runs():
        item = by_id.get(arm["query_id"])
        if item is None:
            continue
        engine_runs = {
            e: engines_by_key.get((arm["query_id"], e, arm["repeat"]))
            for e in arm["engines"]
        }
        engine_runs = {k: v for k, v in engine_runs.items() if v is not None}
        arm_run = ArmRun.from_dict(arm)
        arm_run.metrics = compute_metrics(item, arm_run, engine_runs)
        ws.append_metrics({
            "query_id": arm_run.query_id,
            "arm": arm_run.arm,
            "repeat": arm_run.repeat,
            "metrics": arm_run.metrics,
        })
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="evals.runner")
    p.add_argument("--dataset", required=True)
    p.add_argument("--arms", default="subsets", choices=["subsets", "single", "all"])
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--live", action="store_true")
    p.add_argument("--metrics-only", action="store_true")
    p.add_argument("--run-id", default=None)
    p.add_argument("--resume", action="store_true")
    p.add_argument("--model", default=None)
    p.add_argument("--base-url", default=None)
    p.add_argument("--api-key", default=None)
    args = p.parse_args(argv)

    if args.metrics_only:
        if not args.run_id:
            raise SystemExit("--metrics-only 需要 --run-id")
        return metrics_only(args)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
