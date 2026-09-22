"""成本核算服务 —— run 生命周期、事件接线与面向 API 的查询封装。

一次 **run** 对应一个端到端的「分析 + 报告」周期：

- ``begin_run()``      —— 用户发起搜索分析时调用，开启新 run（三个引擎 + 论坛归入其中）
- ``attach_report()``  —— 用户生成报告时调用；复用当前 run，若该 run 已出过报告则另起一个，
  这样「一次报告要花多少钱」不会把同一 session 里第二次报告的费用重复计入

run_id 同时写入全局兜底（``usage.set_active_run``），因此引擎内部无论跑在哪个
线程、由谁创建 LLM 客户端，调用都会被自动归集，无需层层传参。
"""

from __future__ import annotations

import threading
from datetime import datetime
from typing import Any, Optional

from loguru import logger

from app.services.event_bus import publish
from app.services.event_types import EventType

_wire_lock = threading.Lock()
_wired = False

_run_lock = threading.RLock()
_active_run: Optional[dict[str, Any]] = None


# ── 事件接线 ────────────────────────────────────────────────────────────────

def ensure_wired() -> None:
    """把用量账本接到应用事件层（幂等，可在启动与首次调用时反复调用）。"""
    global _wired
    with _wire_lock:
        if _wired:
            return
        from engines.common import usage

        usage.subscribe(_on_usage_record)
        _wired = True
    logger.info("Token/成本核算已启用（价目表见 engines/common/model_prices.json）")


def _summary_without_records(summary: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in (summary or {}).items() if key not in ("records", "recent_records")}


def _on_usage_record(record: dict[str, Any], summary: dict[str, Any]) -> None:
    """每笔调用发生后：回写报告任务 + 广播全局事件，供前端实时累加。"""
    lite = _summary_without_records(summary)

    try:
        from app.services import report_service as report_svc

        task = report_svc.find_task_by_run_id(record.get("run_id"))
        if task is not None:
            report_svc.apply_cost_update(task, lite)
    except Exception:
        logger.exception("成本事件回写报告任务失败")

    try:
        publish(EventType.COST_UPDATE, {
            "run_id": record.get("run_id"),
            "record": record,
            "summary": lite,
        })
    except Exception:
        logger.exception("广播成本事件失败")


# ── run 生命周期 ────────────────────────────────────────────────────────────

def begin_run(query: str = "", source: str = "search") -> str:
    """开启一个新的分析 run，并设为当前全局兜底 run。"""
    ensure_wired()
    from engines.common import usage

    run_id = usage.new_run_id()
    usage.set_active_run(run_id)
    usage.ensure_run(run_id, query=query, meta={"source": source})

    global _active_run
    with _run_lock:
        _active_run = {
            "run_id": run_id,
            "query": query,
            "source": source,
            "report_attached": False,
            "created_at": datetime.now().isoformat(),
        }
    logger.info(f"成本核算 run 已开始: {run_id}（{query or '未命名'}）")
    return run_id


def get_active_run() -> Optional[dict[str, Any]]:
    with _run_lock:
        return dict(_active_run) if _active_run else None


def attach_report(query: str = "") -> str:
    """把一次报告生成绑定到一个 run；返回该 run 的 id。

    - 当前 run 尚未附带报告 → 直接复用（成本 = 分析 + 报告，端到端）
    - 当前 run 已有报告 / 没有 run → 新开一个 report-only run，避免第二次报告重复计入第一次的费用
    """
    ensure_wired()
    from engines.common import usage

    global _active_run
    with _run_lock:
        if _active_run and not _active_run.get("report_attached"):
            _active_run["report_attached"] = True
            run_id = _active_run["run_id"]
            source = "search+report"
        else:
            run_id = usage.new_run_id()
            _active_run = {
                "run_id": run_id,
                "query": query,
                "source": "report_only",
                "report_attached": True,
                "created_at": datetime.now().isoformat(),
            }
            source = "report_only"

    usage.set_active_run(run_id)
    usage.ensure_run(run_id, query=query, meta={"source": source})
    logger.info(f"报告生成绑定到成本 run: {run_id}")
    return run_id


def bind_active_run(run_id: str) -> None:
    """把全局兜底 run 指向指定 id（报告线程启动时用于防止被并发的搜索改写）。"""
    from engines.common import usage

    if not run_id:
        return
    usage.set_active_run(run_id)


# ── 查询 ────────────────────────────────────────────────────────────────────

def get_run(run_id: str, include_records: bool = False) -> Optional[dict[str, Any]]:
    from engines.common import usage

    return usage.get_run_summary(run_id, include_records=include_records)


def get_current() -> Optional[dict[str, Any]]:
    active = get_active_run()
    if not active:
        return None
    summary = get_run(active["run_id"])
    if summary is None:
        summary = {
            "run_id": active["run_id"],
            "query": active.get("query", ""),
            "calls": 0,
            "total_tokens": 0,
            "cost": 0.0,
            "by_engine": {},
            "by_model": {},
        }
    summary["active"] = is_run_active(active["run_id"])
    summary["source"] = active.get("source")
    return summary


def is_run_active(run_id: str) -> bool:
    """run 是否仍在产生调用：报告任务在跑，或当前 run 就是它。"""
    try:
        from app.services import report_service as report_svc

        task = report_svc.find_task_by_run_id(run_id)
        if task is not None and task.status in ("pending", "running"):
            return True
    except Exception:
        pass
    active = get_active_run()
    return bool(active and active.get("run_id") == run_id and not active.get("report_attached"))


def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    from engines.common import usage

    runs = usage.list_runs(limit=limit)
    for run in runs:
        run["cost"] = round(float(run.get("cost") or 0.0), 6)
    return runs


def price_info() -> dict[str, Any]:
    from engines.common import pricing

    return pricing.describe_price_table()


def reset_state() -> None:
    """重置 run 状态（测试用）。"""
    global _active_run, _wired
    from engines.common import usage

    usage.unsubscribe(_on_usage_record)
    with _run_lock:
        _active_run = None
    with _wire_lock:
        _wired = False
