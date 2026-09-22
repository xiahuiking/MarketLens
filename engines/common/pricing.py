"""模型价目表与成本计算 —— token/成本核算功能的定价层。

设计要点：

- 价目表是**可编辑的 JSON 文件**（默认 ``engines/common/model_prices.json``），
  也可用 ``MODEL_PRICES_PATH`` 指向自定义文件；缺失/损坏时回退到内置空表，
  而不是让整个应用启动失败。
- 单价按「每百万 token」给出，条目自带币种；计算时统一折算为 ``COST_DISPLAY_CURRENCY``
  （默认人民币），换算用 ``USD_TO_CNY_RATE``。
- 模型名匹配是**宽松**的（精确 → 别名 → 子串），因为网关的模型名常带日期/版本后缀
  （如 ``kimi-k2-0711-preview``）。匹配不到时**不猜价格**：返回 ``price_known=False``，
  由上层标注为「价格未知」，避免把未知成本静默当成 0。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Optional

from loguru import logger

# 内置默认价目表（与 model_prices.json 同目录）
_DEFAULT_PRICES_PATH = Path(__file__).resolve().parent / "model_prices.json"

_DEFAULT_DISPLAY_CURRENCY = "CNY"
_DEFAULT_USD_TO_CNY = 7.2

_cache_lock = threading.Lock()
_cache: dict[str, Any] = {"path": None, "mtime": None, "table": None}


def _get_settings():
    """动态获取 app 配置；engines 单独被使用时退回默认值。"""
    try:
        from app import config

        return config.settings
    except Exception:  # pragma: no cover - 仅在脱离 app 的独立调用下触发
        return None


def _display_currency() -> str:
    settings = _get_settings()
    value = getattr(settings, "COST_DISPLAY_CURRENCY", None) or _DEFAULT_DISPLAY_CURRENCY
    return str(value).strip().upper() or _DEFAULT_DISPLAY_CURRENCY


def _usd_to_cny() -> float:
    settings = _get_settings()
    raw = getattr(settings, "USD_TO_CNY_RATE", None)
    try:
        rate = float(raw) if raw is not None else _DEFAULT_USD_TO_CNY
    except (TypeError, ValueError):
        rate = _DEFAULT_USD_TO_CNY
    return rate if rate > 0 else _DEFAULT_USD_TO_CNY


def display_currency() -> str:
    """成本展示币种（默认 CNY）。"""
    return _display_currency()


def usd_to_cny_rate() -> float:
    """美元兑人民币汇率。"""
    return _usd_to_cny()


def _resolve_path() -> Path:
    settings = _get_settings()
    custom = getattr(settings, "MODEL_PRICES_PATH", None)
    if custom:
        path = Path(str(custom)).expanduser()
        if not path.is_absolute():
            # 相对路径按仓库根目录解析，避免受运行时 cwd 影响
            path = Path(__file__).resolve().parent.parent.parent / path
        if path.is_file():
            return path
        logger.warning(f"MODEL_PRICES_PATH 指向的文件不存在，回退默认价目表: {path}")
    return _DEFAULT_PRICES_PATH


def _empty_table() -> dict[str, Any]:
    return {
        "display_currency": _DEFAULT_DISPLAY_CURRENCY,
        "usd_to_cny": _DEFAULT_USD_TO_CNY,
        "models": [],
    }


def load_price_table(force_reload: bool = False) -> dict[str, Any]:
    """加载价目表；文件变更（mtime 变化）时自动重新加载。"""
    path = _resolve_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None

    with _cache_lock:
        if (
            not force_reload
            and _cache["table"] is not None
            and _cache["path"] == str(path)
            and _cache["mtime"] == mtime
        ):
            return _cache["table"]

        table = _empty_table()
        if mtime is not None:
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    models = raw.get("models")
                    table["models"] = models if isinstance(models, list) else []
                    if raw.get("usd_to_cny") is not None:
                        table["usd_to_cny"] = raw["usd_to_cny"]
                    if raw.get("display_currency"):
                        table["display_currency"] = raw["display_currency"]
                    table["updated_at"] = raw.get("updated_at")
                    table["note"] = raw.get("note")
                    table["source"] = str(path)
            except Exception:
                logger.exception(f"价目表解析失败，成本将标记为未知: {path}")
                table = _empty_table()
                table["source"] = str(path)

        _cache["path"] = str(path)
        _cache["mtime"] = mtime
        _cache["table"] = table
        return table


def _normalize(name: Any) -> str:
    return str(name or "").strip().lower()


def _convert(amount: float, from_currency: str, to_currency: str, rate: float) -> Optional[float]:
    src = (from_currency or "").strip().upper()
    dst = (to_currency or "").strip().upper()
    if src == dst:
        return amount
    if src == "USD" and dst == "CNY":
        return amount * rate
    if src == "CNY" and dst == "USD":
        return amount / rate
    return None


def _entry_currency(entry: dict[str, Any], table: dict[str, Any]) -> str:
    return str(entry.get("currency") or table.get("display_currency") or _DEFAULT_DISPLAY_CURRENCY).upper()


def _entry_unit_price(entry: dict[str, Any], key: str) -> Optional[float]:
    raw = entry.get(key)
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def find_model_price(model_name: str) -> Optional[dict[str, Any]]:
    """按模型名查找价目表条目。

    匹配顺序：规范化精确命中 → 别名精确命中 → 子串命中（取别名最长者，避免
    ``qwen`` 之类短别名抢占更具体的条目）。找不到返回 ``None``。
    """
    target = _normalize(model_name)
    if not target:
        return None

    table = load_price_table()
    entries = [e for e in table.get("models", []) if isinstance(e, dict)]
    if not entries:
        return None

    # 1) 精确命中（含 name 与 aliases）
    for entry in entries:
        names = [entry.get("name"), *(entry.get("aliases") or [])]
        if any(_normalize(n) == target for n in names):
            return entry

    # 2) 子串命中：只接受长度 >= 3 的别名，且优先匹配更长的别名
    candidates: list[tuple[int, dict[str, Any]]] = []
    for entry in entries:
        names = [entry.get("name"), *(entry.get("aliases") or [])]
        for name in names:
            alias = _normalize(name)
            if len(alias) < 3:
                continue
            if alias in target or target in alias:
                candidates.append((len(alias), entry))
    if candidates:
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    return None


def compute_cost(
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> dict[str, Any]:
    """计算一次调用的成本。

    Returns:
        dict:
            cost: 折算到展示币种后的金额；价格未知时为 ``None``
            input_cost / output_cost: 分项金额（同为展示币种），未知时 ``None``
            currency: 展示币种
            price_known: 是否在价目表中命中
            matched_model: 命中的价目表条目名
            unit_prices: 命中的原始单价与币种（便于前端展示 / 排查）
    """
    display = _display_currency()
    rate = _usd_to_cny()
    table = load_price_table()
    table_rate = table.get("usd_to_cny")
    if isinstance(table_rate, (int, float)) and table_rate > 0:
        rate = float(table_rate)

    result: dict[str, Any] = {
        "cost": None,
        "input_cost": None,
        "output_cost": None,
        "currency": display,
        "price_known": False,
        "matched_model": None,
        "unit_prices": None,
    }

    entry = find_model_price(model_name)
    if entry is None:
        return result

    input_unit = _entry_unit_price(entry, "input_per_million")
    output_unit = _entry_unit_price(entry, "output_per_million")
    if input_unit is None and output_unit is None:
        return result

    entry_currency = _entry_currency(entry, table)
    prompt_tokens = max(int(prompt_tokens or 0), 0)
    completion_tokens = max(int(completion_tokens or 0), 0)

    input_cost: Optional[float] = None
    if input_unit is not None:
        input_cost = _convert(prompt_tokens / 1_000_000 * input_unit, entry_currency, display, rate)
    output_cost: Optional[float] = None
    if output_unit is not None:
        output_cost = _convert(completion_tokens / 1_000_000 * output_unit, entry_currency, display, rate)

    parts = [c for c in (input_cost, output_cost) if c is not None]
    if not parts:
        # 币种无法换算（例如价目表用了第三种币种）
        logger.warning(f"模型 {model_name} 的价目表币种 {entry_currency} 无法折算到 {display}")
        return result

    result.update(
        cost=sum(parts),
        input_cost=input_cost,
        output_cost=output_cost,
        price_known=True,
        matched_model=str(entry.get("name") or ""),
        unit_prices={
            "input_per_million": input_unit,
            "output_per_million": output_unit,
            "currency": entry_currency,
        },
    )
    return result


def describe_price_table() -> dict[str, Any]:
    """暴露价目表元信息，供 API / 前端提示「价格为估算、可自行修改」。"""
    table = load_price_table()
    return {
        "source": table.get("source") or str(_DEFAULT_PRICES_PATH),
        "display_currency": _display_currency(),
        "usd_to_cny": _usd_to_cny(),
        "updated_at": table.get("updated_at"),
        "note": table.get("note"),
        "model_count": len(table.get("models", [])),
        "models": [str(e.get("name")) for e in table.get("models", []) if isinstance(e, dict)],
    }
