"""
Report Engine service layer — framework-agnostic business logic.

Manages task lifecycle and report generation.
"""

import json
import os
import queue
import sys
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from loguru import logger

# Ensure engines/ and app/ are on Python path
_root = Path(__file__).resolve().parent.parent.parent
for _p in (str(_root / "engines"), str(_root / "app")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── Constants ───────────────────────────────────────────────────────────────

MAX_TASK_HISTORY = 5

# ── Global state ────────────────────────────────────────────────────────────

current_task: Optional["ReportTask"] = None
task_lock = threading.Lock()
tasks_registry: dict[str, "ReportTask"] = {}


def _prune_tasks():
    if len(tasks_registry) > MAX_TASK_HISTORY:
        oldest = sorted(tasks_registry.values(), key=lambda t: t.created_at)
        for t in oldest[:-MAX_TASK_HISTORY]:
            tasks_registry.pop(t.task_id, None)


def _get_task(task_id: str) -> Optional["ReportTask"]:
    with task_lock:
        if current_task and current_task.task_id == task_id:
            return current_task
        return tasks_registry.get(task_id)


def _safe_filename(value: str, fallback: str = "report") -> str:
    sanitized = "".join(c for c in str(value) if c.isalnum() or c in (" ", "-", "_")).strip()
    return sanitized.replace(" ", "_") or fallback


# ── ReportTask ──────────────────────────────────────────────────────────────

class ReportTask:
    """Tracks report generation: status, progress, and output file paths."""

    def __init__(self, query: str, task_id: str, custom_template: str = ""):
        self.task_id = task_id
        self.query = query
        self.custom_template = custom_template
        self.status = "pending"
        self.progress = 0
        self.error_message = ""
        self.created_at = datetime.now()
        self.updated_at = datetime.now()
        self.html_content = ""
        self.report_file_path = ""
        self.report_file_relative_path = ""
        self.report_file_name = ""
        self.state_file_path = ""
        self.ir_file_path = ""
        self.markdown_file_path = ""
        self.markdown_file_name = ""

        # Token / 成本核算：run_id 贯穿「分析引擎 + 论坛 + 报告生成」
        self.run_id = ""
        self.cost: dict[str, Any] = {}
        self._cost_lock = threading.Lock()

        # 协作式取消与 SSE 事件推送
        self.cancel_requested = False
        self._event_queue: "queue.Queue[dict]" = queue.Queue()
        self._event_history: "deque[dict]" = deque(maxlen=200)
        self._event_seq = 0

    def update_status(self, status: str, progress: int | None = None, error_message: str = ""):
        self.status = status
        if progress is not None:
            self.progress = progress
        if error_message:
            self.error_message = error_message
        self.updated_at = datetime.now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id, "query": self.query,
            "status": self.status, "progress": self.progress,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "has_result": bool(self.html_content),
            "report_file_ready": bool(self.report_file_path),
            "report_file_name": self.report_file_name,
            "report_file_path": self.report_file_relative_path or self.report_file_path,
            "state_file_ready": bool(self.state_file_path),
            "state_file_path": self.state_file_path,
            "ir_file_ready": bool(self.ir_file_path),
            "ir_file_path": self.ir_file_path,
            "markdown_file_ready": bool(self.markdown_file_path),
            "markdown_file_name": self.markdown_file_name,
            "markdown_file_path": self.markdown_file_path,
            "run_id": self.run_id,
            "cost": self.get_cost(),
        }

    def get_cost(self) -> dict[str, Any]:
        with self._cost_lock:
            return dict(self.cost) if self.cost else {}

    def set_cost(self, summary: dict[str, Any]) -> None:
        with self._cost_lock:
            self.cost = dict(summary or {})

    def record_event(self, event_name: str, payload: dict[str, Any]) -> None:
        """将事件写入 SSE 队列与历史缓冲区。"""
        self._event_seq += 1
        frame = {
            "id": self._event_seq,
            "event": event_name,
            "task_id": self.task_id,
            "timestamp": datetime.now().isoformat(),
            "payload": payload,
        }
        self._event_history.append(frame)
        # 无 SSE 客户端时避免队列无限膨胀（历史缓冲区已保留最近 200 条用于重连回放）
        if self._event_queue.qsize() < 2000:
            try:
                self._event_queue.put_nowait(frame)
            except Exception:
                pass

    def get_event_history(self) -> list[dict[str, Any]]:
        """返回历史事件（用于 SSE 重连回放）。"""
        return list(self._event_history)


# ── Input file checks ───────────────────────────────────────────────────────

ENGINE_INPUT_DIRS = {
    "review": "data/report/review",
    "competitor": "data/report/competitor",
    "trend": "data/report/trend",
}
FORUM_LOG_PATH = "logs/forum.log"


def check_engines_ready() -> dict[str, Any]:
    """Check if engine output files and forum log are ready."""
    found, missing, latest = [], [], {}

    for engine, dirpath in ENGINE_INPUT_DIRS.items():
        if not os.path.isdir(dirpath):
            missing.append(f"{engine}: 目录不存在")
            continue
        md_files = sorted(
            [f for f in os.listdir(dirpath) if f.endswith('.md')],
            key=lambda x: os.path.getmtime(os.path.join(dirpath, x)),
        )
        if md_files:
            found.append(f"{engine}: {len(md_files)} 个文件")
            latest[engine] = os.path.join(dirpath, md_files[-1])
        else:
            missing.append(f"{engine}: 目录中没有 .md 文件")

    forum_ok = os.path.exists(FORUM_LOG_PATH)
    if forum_ok:
        found.append(f"forum: {os.path.basename(FORUM_LOG_PATH)}")
        latest['forum'] = FORUM_LOG_PATH
    else:
        missing.append("forum: 日志文件不存在")

    return {
        'ready': bool(found and forum_ok),
        'files_found': found,
        'missing_files': missing,
        'latest_files': latest,
    }


def _load_input_files(file_paths: dict[str, str]) -> dict[str, Any]:
    """Load engine reports and forum log content."""
    content = {'reports': [], 'forum_logs': ''}
    for engine in ('trend', 'competitor', 'review'):
        path = file_paths.get(engine)
        try:
            content['reports'].append(open(path, encoding='utf-8').read() if path else "")
        except Exception:
            content['reports'].append("")
    try:
        if 'forum' in file_paths:
            content['forum_logs'] = open(file_paths['forum'], encoding='utf-8').read()
    except Exception:
        pass
    return content


# ── Report generation (background thread) ───────────────────────────────────

def run_report_generation(task: ReportTask, query: str, custom_template: str = ""):
    # 该线程内所有 LLM 调用（含 rescue 客户端 / 图表修复）都归到本任务的 run
    if task.run_id:
        from engines.common import usage

        usage.set_active_run(task.run_id)
        usage.set_usage_context(run_id=task.run_id, engine="ReportEngine")

    try:
        from engines.ReportEngine.exceptions import ReportCancelledError

        task.update_status("running", 5)
        task.record_event("status", {"task": task.to_dict()})

        check_result = check_engines_ready()
        if not check_result.get("ready"):
            task.update_status("error", 0,
                               f"输入文件未准备就绪: {check_result.get('missing_files', [])}")
            task.record_event("error", {"task": task.to_dict()})
            return

        from engines.ReportEngine.agent import generate_report

        content = _load_input_files(check_result.get("latest_files", {}))

        def stream_handler(event_type: str, payload: dict[str, Any]):
            _handle_engine_event(task, event_type, payload)

        generation_result = generate_report(
            query=query,
            reports=content.get("reports", []),
            forum_logs=content.get("forum_logs", ""),
            custom_template=custom_template,
            save_report=True,
            stream_handler=stream_handler,
            report_id=task.task_id,
        )

        if not isinstance(generation_result, dict):
            task.html_content = str(generation_result)
        else:
            saved = generation_result
            task.html_content = saved.get("html_content", "")
            task.report_file_path = saved.get("report_filepath", "")
            task.report_file_relative_path = saved.get("report_relative_path", "")
            task.report_file_name = saved.get("report_filename", "")
            task.state_file_path = saved.get("state_filepath", "")
            task.ir_file_path = saved.get("ir_filepath", "")

        # 完成后从账本拉一次最终汇总，避免最后几笔调用只落在内存里
        _refresh_task_cost(task)

        task.update_status("completed", 100)
        task.record_event("html_ready", {"task": task.to_dict()})
        task.record_event("completed", {"task": task.to_dict()})

    except ReportCancelledError:
        logger.info(f"报告生成已取消: {task.task_id}")
        _refresh_task_cost(task)
        task.update_status("cancelled", task.progress)
        task.record_event("cancelled", {"task": task.to_dict()})
    except Exception as e:
        logger.exception(f"报告生成过程中发生错误: {e}")
        _refresh_task_cost(task)
        task.update_status("error", 0, str(e))
        task.record_event("error", {"task": task.to_dict()})


def _refresh_task_cost(task: ReportTask) -> None:
    """把 run 的最终成本汇总同步到任务（失败不影响任务收尾）。"""
    if not task.run_id:
        return
    try:
        from app.services import cost_service

        summary = cost_service.get_run(task.run_id) or {}
        if summary:
            task.set_cost(summary)
    except Exception:
        logger.exception("刷新任务成本失败")


def _handle_engine_event(task: ReportTask, event_type: str, payload: dict[str, Any]):
    """将 ReportEngine 的 stream_handler 事件映射为前端可消费的 SSE 事件。"""
    from engines.ReportEngine.exceptions import ReportCancelledError

    if task.cancel_requested:
        raise ReportCancelledError()

    if event_type == "progress":
        pct = int(payload.get("progress", 0))
        task.update_status("running", pct)
        task.record_event("status", {"task": task.to_dict()})
    elif event_type in ("chapter_status", "chapter_chunk"):
        task.record_event(event_type, payload)
    elif event_type == "stage":
        task.record_event("stage", payload)
    else:
        # 透传 warning / debug 等其余事件
        task.record_event(event_type, payload)


# ── Task management ─────────────────────────────────────────────────────────

def create_task(query: str, custom_template: str = "") -> ReportTask:
    global current_task

    with task_lock:
        if current_task and current_task.status == "running":
            raise RuntimeError("已有报告生成任务在运行中")
        if current_task and current_task.status in ("completed", "error"):
            current_task = None

        task_id = f"report_{int(time.time())}"
        task = ReportTask(query, task_id, custom_template)
        current_task = task
        tasks_registry[task_id] = task
        _prune_tasks()

    # 绑定成本 run 并把已有花费（分析阶段）作为基线写入任务，
    # 这样前端在报告刚启动时就能看到端到端累计金额。
    try:
        from app.services import cost_service

        task.run_id = cost_service.attach_report(query)
        baseline = cost_service.get_run(task.run_id) or {}
        if baseline:
            task.set_cost(baseline)
    except Exception:
        logger.exception("绑定成本 run 失败（不影响报告生成）")

    return task


def find_task_by_run_id(run_id: str) -> Optional[ReportTask]:
    """按 run_id 找到对应的报告任务（用于把 LLM 用量回写到任务）。"""
    if not run_id:
        return None
    with task_lock:
        candidates = list(tasks_registry.values())
        if current_task is not None:
            candidates.append(current_task)
    for task in candidates:
        if task.run_id == run_id:
            return task
    return None


def apply_cost_update(task: ReportTask, summary: dict[str, Any]) -> None:
    """用量变化时更新任务成本并推送 SSE，供前端实时累加。"""
    task.set_cost(summary)
    task.record_event("cost_update", {"task": task.to_dict(), "cost": task.get_cost()})


def start_task_thread(task: ReportTask, query: str, custom_template: str = ""):
    threading.Thread(
        target=run_report_generation, args=(task, query, custom_template),
        daemon=True,
    ).start()


def get_status_dict() -> dict[str, Any]:
    engines_status = check_engines_ready()
    with task_lock:
        task_dict = current_task.to_dict() if current_task else None
    return {
        "initialized": True,
        "engines_ready": engines_status["ready"],
        "files_found": engines_status.get("files_found", []),
        "missing_files": engines_status.get("missing_files", []),
        "current_task": task_dict,
    }


# ── Log management ──────────────────────────────────────────────────────────

def clear_report_log():
    from engines.ReportEngine.utils.config import settings
    log_file = Path(settings.LOG_FILE)
    try:
        log_file.write_text("", encoding="utf-8")
    except FileNotFoundError:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text("", encoding="utf-8")


# ── Export helpers ──────────────────────────────────────────────────────────

def export_markdown_for_task(task_id: str) -> dict[str, Any]:
    task = tasks_registry.get(task_id)
    if not task:
        raise LookupError("任务不存在")
    if task.status != "completed":
        raise RuntimeError(f"任务未完成，当前状态: {task.status}")
    if not task.ir_file_path or not os.path.exists(task.ir_file_path):
        raise FileNotFoundError("IR文件不存在，无法生成Markdown")

    with open(task.ir_file_path, encoding="utf-8") as f:
        document_ir = json.load(f)

    from engines.ReportEngine.renderers import MarkdownRenderer
    from engines.ReportEngine.utils.config import settings

    md_text = MarkdownRenderer().render(document_ir, ir_file_path=task.ir_file_path)

    metadata = (document_ir or {}).get("metadata") or {}
    topic = metadata.get("topic") or metadata.get("title") or task.query
    filename = f"report_{_safe_filename(topic)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"

    output_dir = Path(settings.OUTPUT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / filename
    md_path.write_text(md_text, encoding="utf-8")

    task.markdown_file_path = str(md_path.resolve())
    task.markdown_file_name = filename

    logger.info(f"导出Markdown完成: {md_path}")
    return {"file_path": task.markdown_file_path, "file_name": filename}


def export_pdf_for_task(task_id: str, optimize: bool = True) -> bytes:
    task = tasks_registry.get(task_id)
    if not task:
        raise LookupError("任务不存在")
    if task.status != "completed":
        raise RuntimeError(f"任务未完成，当前状态: {task.status}")
    if not task.ir_file_path or not os.path.exists(task.ir_file_path):
        raise FileNotFoundError("IR文件不存在")

    with open(task.ir_file_path, encoding="utf-8") as f:
        document_ir = json.load(f)

    from engines.ReportEngine.renderers import PDFRenderer
    logger.info(f"开始导出PDF，任务ID: {task_id}，布局优化: {optimize}")
    return PDFRenderer().render_to_bytes(document_ir, optimize_layout=optimize)


# ── 取消 / 模板 / 日志 / IR 直出 / SSE 流 ───────────────────────────────────

def cancel_task(task: ReportTask) -> None:
    """标记任务为协作式取消；生成线程会在下一个章节边界中断。"""
    task.cancel_requested = True


def get_templates() -> list[dict[str, Any]]:
    """列出 ReportEngine 模板目录中的可用模板。"""
    from engines.ReportEngine.utils.config import settings as report_settings

    template_dir = Path(report_settings.TEMPLATE_DIR)
    templates: list[dict[str, Any]] = []
    if not template_dir.is_dir():
        return templates
    for path in sorted(template_dir.glob("*.md")):
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            content = ""
        templates.append({
            "name": path.stem,
            "path": str(path),
            "description": "",
            "content": content,
        })
    return templates


def get_report_log() -> str:
    """读取 ReportEngine 日志文件内容。"""
    from engines.ReportEngine.utils.config import settings as report_settings

    log_file = Path(report_settings.LOG_FILE)
    try:
        if log_file.exists():
            return log_file.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        logger.exception("读取报告日志失败")
    return ""


def export_pdf_from_ir(document_ir: dict[str, Any], optimize: bool = True) -> bytes:
    """直接根据 Document IR 渲染 PDF 字节流。"""
    from engines.ReportEngine.renderers import PDFRenderer
    return PDFRenderer().render_to_bytes(document_ir, optimize_layout=optimize)


def _format_sse(frame: dict[str, Any]) -> str:
    """将事件帧格式化为 SSE 文本（named event + JSON data）。"""
    data = json.dumps(frame, ensure_ascii=False)
    return f"event: {frame['event']}\ndata: {data}\n\n"


async def event_stream_generator(task: ReportTask, request: Any):
    """SSE 生成器：回放历史事件，随后推送实时事件，任务结束后自动关闭。"""
    # 回放历史（供刷新/重连的客户端），并记录已回放的最大事件序号，避免与队列重复
    last_replayed_id = 0
    for frame in task.get_event_history():
        last_replayed_id = max(last_replayed_id, frame.get("id", 0))
        yield _format_sse(frame)

    last_event_time = time.time()
    while True:
        if await request.is_disconnected():
            break
        try:
            frame = task._event_queue.get(timeout=1)
            if frame.get("id", 0) <= last_replayed_id:
                continue  # 已在历史回放中发送过，跳过
            yield _format_sse(frame)
            last_event_time = time.time()
        except queue.Empty:
            if task.status in ("completed", "error", "cancelled"):
                break
            if time.time() - last_event_time > 15:
                yield ": heartbeat\n\n"
                last_event_time = time.time()
