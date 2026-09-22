"""LLM token / 成本核算核心 —— 所有引擎 LLM 调用的统一记帐处。

``engines/common/llm_client.py`` 的四个出口（``invoke`` / ``stream_invoke`` /
``stream_invoke_to_string`` / ``structured_invoke``）都会调用 :func:`record_llm_call`，
因此这里拿到的是**全平台唯一**的 LLM 调用事实来源。

一次「run」= 一次端到端的分析+报告周期（三个分析引擎 + 论坛 + 报告生成）。
run 的归属通过 :func:`set_active_run`（全局兜底）与 :func:`set_usage_context`
（线程内覆盖）确定：Web 层在启动分析/报告线程前设置，之后引擎内部无论跑在哪个
线程，都不需要显式传递 run_id。

落盘两份产物：

- ``logs/usage/<run_id>.jsonl`` —— 逐次调用的明细（append-only，便于事后审计）
- ``data/usage/<run_id>.json``  —— 该 run 的聚合快照（供重启后查询 / API 读取）

为保证「不猜测」，网关未返回 usage 时按字符数启发式估算并标记
``estimated=True``；价目表未命中时 ``cost=None`` 且计入 ``unpriced_calls``，
而不是把未知成本静默算成 0。
"""

from __future__ import annotations

import copy
import json
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional
from uuid import uuid4

from loguru import logger

from . import pricing

_ROOT = Path(__file__).resolve().parent.parent.parent

_DEFAULT_USAGE_DIR = "logs/usage"
_SUMMARY_DIR_NAME = "data/usage"
_MAX_RECORDS_IN_MEMORY = 500
_CJK_RANGES = (
    (0x3000, 0x303F),  # CJK 标点
    (0x3400, 0x4DBF),  # 扩展 A
    (0x4E00, 0x9FFF),  # 基本区
    (0xF900, 0xFAFF),  # 兼容表意
    (0xFF00, 0xFFEF),  # 全角字符
)

# ── 全局状态 ────────────────────────────────────────────────────────────────

_state_lock = threading.RLock()
_persist_lock = threading.Lock()
_runs: dict[str, dict[str, Any]] = {}
_listeners: list[Callable[[dict[str, Any], dict[str, Any]], None]] = []
_active_run_id: Optional[str] = None
_ctx_local = threading.local()


def _get_settings():
    """动态获取 app 配置（reload_settings 会替换 settings 对象，必须按需读取）。"""
    try:
        from app import config

        return config.settings
    except Exception:  # pragma: no cover - 脱离 app 独立运行时的兜底
        return None


# ── run 生命周期 ────────────────────────────────────────────────────────────


def new_run_id(prefix: str = "run") -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{stamp}_{uuid4().hex[:6]}"


def _safe_run_id(run_id: str) -> str:
    sanitized = re.sub(r"[^0-9A-Za-z_.-]", "_", str(run_id or "").strip())
    return sanitized or "unassigned"


def set_active_run(run_id: Optional[str]) -> None:
    """设置全局兜底 run_id；没有线程级上下文的调用会归到它。"""
    global _active_run_id
    with _state_lock:
        _active_run_id = run_id or None


def get_active_run_id() -> Optional[str]:
    with _state_lock:
        return _active_run_id


def set_usage_context(run_id: Optional[str] = None, engine: Optional[str] = None) -> None:
    """设置当前线程的核算上下文（线程隔离，不影响其它线程）。"""
    state = getattr(_ctx_local, "state", None)
    if not isinstance(state, dict):
        state = {}
    if run_id is not None:
        state["run_id"] = run_id
    if engine is not None:
        state["engine"] = engine
    _ctx_local.state = state


def get_usage_context() -> dict[str, Any]:
    state = getattr(_ctx_local, "state", None)
    return dict(state) if isinstance(state, dict) else {}


def clear_usage_context() -> None:
    _ctx_local.state = {}


def _current_run_id(explicit: Optional[str] = None) -> str:
    if explicit:
        return _safe_run_id(explicit)
    ctx_run = get_usage_context().get("run_id")
    if ctx_run:
        return _safe_run_id(ctx_run)
    active = get_active_run_id()
    return _safe_run_id(active) if active else "unassigned"


# ── token 估算 ──────────────────────────────────────────────────────────────


def _is_cjk(ch: str) -> bool:
    code = ord(ch)
    return any(low <= code <= high for low, high in _CJK_RANGES)


def estimate_tokens(text: str) -> int:
    """按字符构成启发式估算 token 数。

    中日韩字符约 1 token/字，其余字符约 4 字符/token。这是**估算**：不同厂商
    分词器差异明显（尤其中文），因此调用方会把它标记为 ``estimated``。
    真实 usage 可用时永远优先使用真实值。
    """
    if not text:
        return 0
    text = str(text)
    cjk = sum(1 for ch in text if _is_cjk(ch))
    other = len(text) - cjk
    return max(1, int(round(cjk + other / 4.0)))


def normalize_usage(usage: Any) -> Optional[dict[str, int]]:
    """把各家 SDK 的 usage 对象/字典统一成 prompt/completion/total。

    兼容：
    - OpenAI SDK 的 ``CompletionUsage``（``prompt_tokens`` / ``completion_tokens``）
    - LangChain 的 ``usage_metadata``（``input_tokens`` / ``output_tokens``）
    - 普通 dict
    """
    if usage is None:
        return None

    def pick(*names: str) -> Optional[int]:
        for name in names:
            value = usage.get(name) if isinstance(usage, dict) else getattr(usage, name, None)
            if value is None:
                continue
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
        return None

    prompt = pick("prompt_tokens", "input_tokens")
    completion = pick("completion_tokens", "output_tokens")
    total = pick("total_tokens")
    if prompt is None and completion is None and total is None:
        return None

    prompt = prompt or 0
    completion = completion or 0
    if total is None:
        total = prompt + completion
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}


# ── 记录 ────────────────────────────────────────────────────────────────────


@dataclass
class UsageRecord:
    """单次 LLM 调用的一条核算记录。"""

    run_id: str
    engine: str
    model: str
    method: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: Optional[float] = None
    currency: str = "CNY"
    price_known: bool = False
    matched_model: Optional[str] = None
    tokens_known: bool = False
    estimated: bool = False
    priced: bool = False
    billable: bool = True
    duration_ms: float = 0.0
    caller: str = ""
    base_url: str = ""
    ok: bool = True
    error: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    call_id: str = field(default_factory=lambda: uuid4().hex[:12])
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "run_id": self.run_id,
            "engine": self.engine,
            "model": self.model,
            "method": self.method,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cost": round(self.cost, 8) if self.cost is not None else None,
            "currency": self.currency,
            "price_known": self.price_known,
            "matched_model": self.matched_model,
            "tokens_known": self.tokens_known,
            "estimated": self.estimated,
            "priced": self.priced,
            "billable": self.billable,
            "duration_ms": round(self.duration_ms, 2),
            "caller": self.caller,
            "base_url": self.base_url,
            "ok": self.ok,
            "error": self.error,
            "timestamp": self.timestamp,
            "extra": self.extra,
        }


def _bucket() -> dict[str, Any]:
    return {
        "calls": 0,
        "failed_calls": 0,
        "estimated_calls": 0,
        "unpriced_calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost": 0.0,
        "duration_ms": 0.0,
    }


def _new_run_summary(run_id: str) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "query": "",
        "meta": {},
        "started_at": None,
        "updated_at": None,
        "calls": 0,
        "failed_calls": 0,
        "estimated_calls": 0,
        "unpriced_calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost": 0.0,
        "currency": pricing.display_currency(),
        "duration_ms": 0.0,
        "by_engine": {},
        "by_model": {},
        "records": [],
    }


def _apply_record(summary: dict[str, Any], record: dict[str, Any]) -> None:
    summary["calls"] += 1
    if not record.get("ok", True):
        summary["failed_calls"] += 1
    if record.get("estimated"):
        summary["estimated_calls"] += 1
    if record.get("billable", True) and not record.get("priced"):
        summary["unpriced_calls"] += 1

    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        summary[key] = summary.get(key, 0) + int(record.get(key) or 0)
    summary["duration_ms"] = summary.get("duration_ms", 0.0) + float(record.get("duration_ms") or 0.0)
    if record.get("cost"):
        summary["cost"] = summary.get("cost", 0.0) + float(record["cost"])

    if not summary.get("started_at"):
        summary["started_at"] = record.get("timestamp")
    summary["updated_at"] = record.get("timestamp")

    for group_key, group_name in (("by_engine", record.get("engine")), ("by_model", record.get("model"))):
        name = str(group_name or "unknown")
        groups = summary.setdefault(group_key, {})
        bucket = groups.setdefault(name, _bucket())
        bucket["calls"] += 1
        if not record.get("ok", True):
            bucket["failed_calls"] += 1
        if record.get("estimated"):
            bucket["estimated_calls"] += 1
        if record.get("billable", True) and not record.get("priced"):
            bucket["unpriced_calls"] += 1
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            bucket[key] += int(record.get(key) or 0)
        bucket["duration_ms"] += float(record.get("duration_ms") or 0.0)
        if record.get("cost"):
            bucket["cost"] += float(record["cost"])


def detect_caller() -> str:
    """定位调用来源（文件:行号），用于把成本归因到具体节点。

    只做有限深度的栈回溯并跳过 ``engines/common``、site-packages，
    单次开销远小于一次 LLM 调用。
    """
    try:
        import inspect

        frame = inspect.currentframe()
        for _ in range(14):
            if frame is None:
                break
            frame = frame.f_back
            if frame is None:
                break
            filename = frame.f_code.co_filename
            if not filename:
                continue
            normalized = filename.replace("\\", "/")
            # 跳过公共埋点与重试装饰器：它们只是管道，不是真正的调用方
            if (
                "/engines/common/" in normalized
                or "site-packages" in normalized
                or normalized.endswith("app/utils/retry_helper.py")
            ):
                continue
            try:
                rel = os.path.relpath(filename, _ROOT)
            except ValueError:
                rel = filename
            return f"{rel.replace(os.sep, '/')}:{frame.f_lineno}"
    except Exception:  # pragma: no cover - 归因失败不应影响主流程
        pass
    return ""


def _usage_dir() -> Path:
    settings = _get_settings()
    raw = getattr(settings, "COST_USAGE_DIR", None) or _DEFAULT_USAGE_DIR
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = _ROOT / path
    return path


def _summary_dir() -> Path:
    return _ROOT / _SUMMARY_DIR_NAME


def _persist(record: dict[str, Any]) -> None:
    """把单次调用追加到 JSONL，并刷新 run 聚合快照。

    并发保护：三个分析引擎会同时产生调用。``_persist_lock`` 串行化整个「重新取快照 →
    落盘」过程，保证最后写入的必定是最新状态（否则较旧的线程可能后拿到锁、把快照写回退）。
    """
    run_id = record["run_id"]
    try:
        with _persist_lock:
            with _state_lock:
                current = _runs.get(run_id)
                snapshot = copy.deepcopy(current) if current else None
            if snapshot is None:
                return

            usage_dir = _usage_dir()
            usage_dir.mkdir(parents=True, exist_ok=True)
            with open(usage_dir / f"{run_id}.jsonl", "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

            summary_dir = _summary_dir()
            summary_dir.mkdir(parents=True, exist_ok=True)
            payload = {k: v for k, v in snapshot.items() if k != "records"}
            payload["recent_records"] = snapshot.get("records", [])[-20:]
            # 文件名必须唯一：同进程多线程并发落盘时，同名 tmp 会互相覆盖
            tmp_path = summary_dir / f".{run_id}.{os.getpid()}.{uuid4().hex[:8]}.tmp"
            tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp_path, summary_dir / f"{run_id}.json")
    except Exception:
        logger.exception(f"LLM 用量落盘失败（run_id={run_id}）")


def record_llm_call(
    *,
    engine: str,
    model: str,
    method: str,
    base_url: str = "",
    prompt_text: str = "",
    completion_text: str = "",
    usage: Any = None,
    duration_ms: float = 0.0,
    ok: bool = True,
    error: str = "",
    run_id: Optional[str] = None,
    caller: Optional[str] = None,
    billable: Optional[bool] = None,
    extra: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """记录一次 LLM 调用并返回记录字典（关闭核算时返回 ``None``）。

    Args:
        billable: 该调用是否产生费用。默认与 ``ok`` 一致——失败的请求（如 400
            tool_choice 不支持）通常不计费，因此不估算 token、也不计入
            ``unpriced_calls``，避免凭空造出成本。
    """
    settings = _get_settings()
    if settings is not None and getattr(settings, "COST_TRACKING_ENABLED", True) is False:
        return None

    is_billable = bool(ok) if billable is None else bool(billable)
    estimate_enabled = True if settings is None else bool(getattr(settings, "COST_ESTIMATE_TOKENS", True))
    normalized = normalize_usage(usage)

    if not is_billable:
        prompt_tokens = completion_tokens = total_tokens = 0
        tokens_known = False
        estimated = False
        price = {"cost": None, "currency": pricing.display_currency(), "price_known": False, "matched_model": None}
    elif normalized is not None:
        prompt_tokens = normalized["prompt_tokens"]
        completion_tokens = normalized["completion_tokens"]
        total_tokens = normalized["total_tokens"]
        tokens_known = True
        estimated = False
    elif estimate_enabled:
        prompt_tokens = estimate_tokens(prompt_text)
        completion_tokens = estimate_tokens(completion_text)
        total_tokens = prompt_tokens + completion_tokens
        tokens_known = False
        estimated = total_tokens > 0
    else:
        prompt_tokens = completion_tokens = total_tokens = 0
        tokens_known = False
        estimated = False

    if tokens_known or estimated:
        price = pricing.compute_cost(model, prompt_tokens, completion_tokens)
    else:
        price = {"cost": None, "currency": pricing.display_currency(), "price_known": False, "matched_model": None}

    resolved_run_id = _current_run_id(run_id)
    # 客户端没标注引擎时，回退到线程上下文里的引擎（如 run_engine_task 设置的）
    resolved_engine = str(engine or "").strip()
    if not resolved_engine or resolved_engine == "Engine":
        resolved_engine = str(get_usage_context().get("engine") or resolved_engine or "unknown")
    record = UsageRecord(
        run_id=resolved_run_id,
        engine=resolved_engine,
        model=str(model or "unknown"),
        method=str(method or "invoke"),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cost=price.get("cost"),
        currency=price.get("currency") or pricing.display_currency(),
        price_known=bool(price.get("price_known")),
        matched_model=price.get("matched_model"),
        tokens_known=tokens_known,
        estimated=estimated,
        priced=price.get("cost") is not None,
        billable=is_billable,
        duration_ms=float(duration_ms or 0.0),
        caller=caller if caller is not None else detect_caller(),
        base_url=str(base_url or ""),
        ok=bool(ok),
        error=str(error or "")[:500],
        extra=dict(extra or {}),
    )
    record_dict = record.to_dict()

    with _state_lock:
        summary = _runs.setdefault(resolved_run_id, _new_run_summary(resolved_run_id))
        _apply_record(summary, record_dict)
        records = summary.setdefault("records", [])
        records.append(record_dict)
        if len(records) > _MAX_RECORDS_IN_MEMORY:
            summary["records"] = records[-_MAX_RECORDS_IN_MEMORY:]
        snapshot = copy.deepcopy(summary)
        listeners = list(_listeners)

    _persist(record_dict)

    for callback in listeners:
        try:
            callback(record_dict, snapshot)
        except Exception:
            # 订阅者异常绝不能影响 LLM 调用本身
            logger.exception("用量订阅回调执行失败")

    return record_dict


# ── 查询 ────────────────────────────────────────────────────────────────────


def ensure_run(run_id: str, query: str = "", meta: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """创建/更新 run 元信息（即使还没有任何 LLM 调用也能查到）。"""
    safe_id = _safe_run_id(run_id)
    with _state_lock:
        summary = _runs.setdefault(safe_id, _new_run_summary(safe_id))
        if query:
            summary["query"] = query
        if meta:
            summary["meta"] = {**summary.get("meta", {}), **meta}
        summary.setdefault("started_at", datetime.now().isoformat())
        snapshot = copy.deepcopy(summary)

    try:
        # 与 _persist 用同一把锁并重新取快照：避免「读-改-写」把并发写入的新记录覆盖掉
        with _persist_lock:
            with _state_lock:
                current = _runs.get(safe_id)
                if current is not None:
                    snapshot = copy.deepcopy(current)
            summary_dir = _summary_dir()
            summary_dir.mkdir(parents=True, exist_ok=True)
            payload = {k: v for k, v in snapshot.items() if k != "records"}
            payload["recent_records"] = snapshot.get("records", [])[-20:]
            path = summary_dir / f"{safe_id}.json"
            tmp_path = summary_dir / f".{safe_id}.{os.getpid()}.{uuid4().hex[:8]}.tmp"
            tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp_path, path)
    except Exception:
        logger.exception(f"run 元信息落盘失败（run_id={safe_id}）")

    return snapshot


def _load_run_from_disk(run_id: str) -> Optional[dict[str, Any]]:
    safe_id = _safe_run_id(run_id)
    summary_path = _summary_dir() / f"{safe_id}.json"
    if summary_path.is_file():
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("records", data.get("recent_records", []))
                return data
        except Exception:
            logger.exception(f"读取用量快照失败: {summary_path}")

    # 快照丢失时，用 JSONL 明细重建聚合，保证重启后仍可对账
    detail_path = _usage_dir() / f"{safe_id}.jsonl"
    if not detail_path.is_file():
        return None
    rebuilt = _new_run_summary(safe_id)
    try:
        with open(detail_path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                _apply_record(rebuilt, json.loads(line))
    except Exception:
        logger.exception(f"从明细重建用量失败: {detail_path}")
        return None
    return rebuilt


def get_run_summary(run_id: str, include_records: bool = False) -> Optional[dict[str, Any]]:
    """获取 run 汇总；内存没有则回落到磁盘快照 / JSONL 重建。"""
    if not run_id:
        return None
    safe_id = _safe_run_id(run_id)
    with _state_lock:
        summary = _runs.get(safe_id)
        snapshot = copy.deepcopy(summary) if summary else None

    if snapshot is None:
        snapshot = _load_run_from_disk(safe_id)
    if snapshot is None:
        return None

    snapshot["cost"] = round(float(snapshot.get("cost") or 0.0), 6)
    for group_key in ("by_engine", "by_model"):
        for bucket in (snapshot.get(group_key) or {}).values():
            bucket["cost"] = round(float(bucket.get("cost") or 0.0), 6)
            bucket["duration_ms"] = round(float(bucket.get("duration_ms") or 0.0), 2)
    snapshot["duration_ms"] = round(float(snapshot.get("duration_ms") or 0.0), 2)
    snapshot["currency"] = snapshot.get("currency") or pricing.display_currency()
    if not include_records:
        snapshot.pop("records", None)
        snapshot.pop("recent_records", None)
    return snapshot


def list_runs(limit: int = 20) -> list[dict[str, Any]]:
    """列出最近的 run（按更新时间倒序），供成本历史查询。"""
    runs: list[dict[str, Any]] = []
    summary_dir = _summary_dir()
    if summary_dir.is_dir():
        for path in summary_dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            data.pop("records", None)
            data.pop("recent_records", None)
            if data.get("run_id"):
                runs.append(data)

    # 内存中尚未落盘 / 更新的 run 覆盖磁盘版本
    with _state_lock:
        known = {r.get("run_id") for r in runs}
        for run_id, summary in _runs.items():
            if run_id in known:
                continue
            runs.append({k: v for k, v in summary.items() if k != "records"})

    runs.sort(key=lambda item: str(item.get("updated_at") or item.get("started_at") or ""), reverse=True)
    return runs[: max(int(limit or 0), 0)]


# ── 订阅（供 app 层把用量转成 SSE 事件） ─────────────────────────────────────


def subscribe(callback: Callable[[dict[str, Any], dict[str, Any]], None]) -> None:
    with _state_lock:
        if callback not in _listeners:
            _listeners.append(callback)


def unsubscribe(callback: Callable[[dict[str, Any], dict[str, Any]], None]) -> None:
    with _state_lock:
        try:
            _listeners.remove(callback)
        except ValueError:
            pass


def clear_listeners() -> None:
    with _state_lock:
        _listeners.clear()


def reset_state() -> None:
    """清空内存状态（测试用；不动磁盘文件）。"""
    global _active_run_id
    with _state_lock:
        _runs.clear()
        _listeners.clear()
        _active_run_id = None
    clear_usage_context()


def snapshot_all() -> dict[str, dict[str, Any]]:
    """返回所有内存中 run 的浅拷贝（调试 / 测试用）。"""
    with _state_lock:
        return {run_id: copy.deepcopy(summary) for run_id, summary in _runs.items()}
