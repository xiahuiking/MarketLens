"""Report Engine routes — FastAPI router."""

import json
import os
from datetime import datetime

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse, Response, FileResponse, StreamingResponse
from loguru import logger

from app.services import report_service as svc

router = APIRouter(prefix="/api/report", tags=["report"])


# ── Helpers ─────────────────────────────────────────────────────────────────

def _task_or_404(task_id: str):
    task = svc._get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


def _check_pango():
    from engines.ReportEngine.utils.dependency_check import check_pango_available
    ok, msg = check_pango_available()
    if not ok:
        raise HTTPException(status_code=503, detail=msg)


def _ok(data=None, **kw):
    """JSONResponse with success=True."""
    if data is None:
        data = {}
    return JSONResponse(content={"success": True, **data, **kw})


def _fail(detail: str, status=500):
    return JSONResponse(content={"success": False, "error": detail}, status_code=status)

# ── GET /status ─────────────────────────────────────────────────────────────

@router.get("/status")
def get_status():
    try:
        return _ok(svc.get_status_dict())
    except Exception as e:
        logger.exception(f"获取Report Engine状态失败: {e}")
        return _fail(str(e))


# ── GET /templates ──────────────────────────────────────────────────────────

@router.get("/templates")
def get_templates():
    try:
        return _ok(templates=svc.get_templates())
    except Exception as e:
        logger.exception(f"获取报告模板失败: {e}")
        return _fail(str(e))


# ── GET /log & POST /log/clear ──────────────────────────────────────────────

@router.get("/log")
def get_report_log():
    try:
        return _ok(log=svc.get_report_log())
    except Exception as e:
        logger.exception(f"获取报告日志失败: {e}")
        return _fail(str(e))


@router.post("/log/clear")
def clear_report_log():
    try:
        svc.clear_report_log()
        return _ok(message="日志已清空")
    except Exception as e:
        logger.exception(f"清空报告日志失败: {e}")
        return _fail(str(e))


# ── POST /generate ──────────────────────────────────────────────────────────

@router.post("/generate")
async def generate_report(request: Request):
    try:
        data = await request.json() or {}
        query = data.get("query", "智能舆情分析报告")
        custom_template = data.get("custom_template", "")

        svc.clear_report_log()

        engines_status = svc.check_engines_ready()
        if not engines_status["ready"]:
            return _fail("输入文件未准备就绪", status=400)

        task = svc.create_task(query, custom_template)
        svc.start_task_thread(task, query, custom_template)
        return _ok(task_id=task.task_id, message="报告生成已启动",
                   task=task.to_dict())

    except Exception as e:
        logger.exception(f"开始生成报告失败: {e}")
        return _fail(str(e))


# ── GET /progress/{task_id} ─────────────────────────────────────────────────

@router.get("/progress/{task_id}")
def get_progress(task_id: str):
    task = _task_or_404(task_id)
    return _ok(task=task.to_dict())


# ── POST /cancel/{task_id} ──────────────────────────────────────────────────

@router.post("/cancel/{task_id}")
def cancel_report(task_id: str):
    task = _task_or_404(task_id)
    if task.status != "running":
        return _fail("任务未在运行中", status=400)
    svc.cancel_task(task)
    return _ok(message="取消请求已提交", task=task.to_dict())


# ── GET /stream/{task_id} (SSE) ─────────────────────────────────────────────

@router.get("/stream/{task_id}")
async def report_stream(task_id: str, request: Request):
    task = _task_or_404(task_id)
    return StreamingResponse(
        svc.event_stream_generator(task, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── GET /result/{task_id} ───────────────────────────────────────────────────

@router.get("/result/{task_id}")
def get_result(task_id: str):
    task = _task_or_404(task_id)
    if task.status != "completed":
        return _fail("报告尚未完成", status=400)
    return Response(content=task.html_content, media_type="text/html")


@router.get("/result/{task_id}/json")
def get_result_json(task_id: str):
    task = _task_or_404(task_id)
    data = task.to_dict()
    data["html_content"] = task.html_content if task.status == "completed" else ""
    return _ok(**data)


# ── GET /download/{task_id} ─────────────────────────────────────────────────

@router.get("/download/{task_id}")
def download_report(task_id: str):
    task = _task_or_404(task_id)
    if task.status != "completed" or not task.report_file_path:
        return _fail("报告尚未完成或尚未保存", status=400)
    if not os.path.exists(task.report_file_path):
        return _fail("报告文件不存在或已被删除", status=404)
    download_name = task.report_file_name or os.path.basename(task.report_file_path)
    return FileResponse(task.report_file_path, media_type="text/html", filename=download_name)


# ── GET /export/md/{task_id} ────────────────────────────────────────────────

@router.get("/export/md/{task_id}")
def export_markdown(task_id: str):
    try:
        info = svc.export_markdown_for_task(task_id)
        return FileResponse(info["file_path"], media_type="text/markdown",
                            filename=info["file_name"])
    except (LookupError, FileNotFoundError) as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"导出Markdown失败: {e}")
        return _fail(str(e))


# ── GET /export/pdf/{task_id} ───────────────────────────────────────────────

@router.get("/export/pdf/{task_id}")
def export_pdf(task_id: str, optimize: bool = True):
    try:
        _check_pango()
        task = svc.tasks_registry.get(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="任务不存在")

        pdf_bytes = svc.export_pdf_for_task(task_id, optimize=optimize)

        with open(task.ir_file_path, encoding="utf-8") as f:
            ir = json.load(f)
        topic = (ir.get("metadata", {}) or {}).get("topic", "report")
        filename = f"report_{topic}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"

        return Response(pdf_bytes, media_type="application/pdf",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    except HTTPException:
        raise
    except (LookupError, FileNotFoundError) as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"导出PDF失败: {e}")
        return _fail(str(e))


# ── POST /export/pdf-from-ir ────────────────────────────────────────────────

@router.post("/export/pdf-from-ir")
async def export_pdf_from_ir(request: Request):
    try:
        _check_pango()
        data = await request.json() or {}
        document_ir = data.get("document_ir")
        optimize = bool(data.get("optimize", True))
        if not isinstance(document_ir, dict):
            return _fail("document_ir 无效", status=400)

        pdf_bytes = svc.export_pdf_from_ir(document_ir, optimize=optimize)
        return Response(pdf_bytes, media_type="application/pdf",
                        headers={"Content-Disposition": 'attachment; filename="report.pdf"'})
    except Exception as e:
        logger.exception(f"从IR导出PDF失败: {e}")
        return _fail(str(e))

