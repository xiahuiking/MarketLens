"""成本核算路由 —— 查询一次 run / 当前 run 的 token 与成本汇总。"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from loguru import logger

from app.services import cost_service as svc

router = APIRouter(prefix="/api/cost", tags=["cost"])


def _ok(data=None, **kw):
    if data is None:
        data = {}
    return JSONResponse(content={"success": True, **data, **kw})


def _fail(detail: str, status: int = 500):
    return JSONResponse(content={"success": False, "error": detail}, status_code=status)


# ── GET /prices ─────────────────────────────────────────────────────────────

@router.get("/prices")
def get_prices():
    """价目表元信息（展示币种、条目数、来源文件），供前端提示「价格为估算」。"""
    try:
        return _ok(prices=svc.price_info())
    except Exception as e:
        logger.exception(f"读取价目表失败: {e}")
        return _fail(str(e))


# ── GET /current ────────────────────────────────────────────────────────────

@router.get("/current")
def get_current_cost():
    """当前 run 的成本汇总（分析阶段也在累计）。"""
    try:
        return _ok(cost=svc.get_current())
    except Exception as e:
        logger.exception(f"获取当前成本失败: {e}")
        return _fail(str(e))


# ── GET /runs ───────────────────────────────────────────────────────────────

@router.get("/runs")
def list_runs(limit: int = 20):
    """最近的 run 成本列表。"""
    try:
        return _ok(runs=svc.list_runs(limit=limit))
    except Exception as e:
        logger.exception(f"获取成本历史失败: {e}")
        return _fail(str(e))


# ── GET /run/{run_id} ───────────────────────────────────────────────────────

@router.get("/run/{run_id}")
def get_run_cost(run_id: str, include_records: bool = False):
    """指定 run 的成本汇总；include_records=true 时附带逐次调用明细。"""
    try:
        summary = svc.get_run(run_id, include_records=include_records)
        if summary is None:
            return _fail("run 不存在", status=404)
        return _ok(cost=summary)
    except Exception as e:
        logger.exception(f"获取 run 成本失败: {e}")
        return _fail(str(e))
