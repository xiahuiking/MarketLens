"""FastAPI main application — primary backend."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse, Response
from fastapi.staticfiles import StaticFiles
from loguru import logger

from app import config as app_config
from app.services.forum_service import init_forum_log, shutdown_forum_service
from app.utils.forum_reader import init_forum_reader, shutdown_forum_reader
from app.routers import system, config, forum, search, events, report, cost


async def _warmup_sentiment_if_enabled() -> None:
    """按配置在启动阶段单线程预热情感模型。

    预热本身是可选优化（默认关闭）：它把数秒的模型加载从"首次搜索"挪到启动阶段，
    并由单一线程完成导入，从而彻底规避多引擎并发首次导入 transformers 的竞态。
    失败不阻断启动 —— 运行时仍会按需懒加载并自动重试。
    """
    try:
        if not app_config.settings.SENTIMENT_ANALYSIS_ENABLED:
            return
        if not app_config.settings.SENTIMENT_WARMUP_ON_STARTUP:
            return
        from engines.ReviewEngine.tools import warmup_sentiment_analyzer

        await asyncio.to_thread(warmup_sentiment_analyzer)
    except Exception as exc:  # pragma: no cover - 预热失败不应影响服务可用性
        logger.warning(f"情感分析模型预热跳过: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown lifecycle."""
    events.init_event_stream()
    init_forum_log()
    init_forum_reader()
    # 在最早时机接通 token/成本核算的事件订阅，避免漏掉启动后的首批 LLM 调用
    try:
        from app.services import cost_service

        cost_service.ensure_wired()
    except Exception as exc:  # pragma: no cover - 核算不可用不应阻断启动
        logger.warning(f"成本核算初始化失败: {exc}")
    await _warmup_sentiment_if_enabled()
    logger.info("FastAPI 服务器已启动，共享服务已初始化")
    try:
        yield
    finally:
        shutdown_forum_reader()
        shutdown_forum_service()
        events.shutdown_event_stream()
        logger.info("FastAPI 服务器已关闭，共享服务已清理")


app = FastAPI(
    title="MarketLens API",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register API routers
app.include_router(system.router)
app.include_router(system.app_status_router)
app.include_router(config.router)
app.include_router(forum.router)
app.include_router(search.router)
app.include_router(events.router)
app.include_router(report.router)
app.include_router(cost.router)

# ── SPA & static files ──────────────────────────────────────────────────

_BASE = Path(__file__).resolve().parent.parent
_VUE_DIST = _BASE / "frontend" / "dist"
_INDEX_HTML = _VUE_DIST / "index.html"


# Static files from Vue build
if (_VUE_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=_VUE_DIST / "assets"), name="vue_assets")


@app.get("/favicon.svg")
async def serve_favicon():
    path = _VUE_DIST / "favicon.svg"
    if path.exists():
        return FileResponse(path)
    return Response(status_code=404)


@app.get("/icons.svg")
async def serve_icons():
    path = _VUE_DIST / "icons.svg"
    if path.exists():
        return FileResponse(path)
    return Response(status_code=404)


@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    """Serve Vue SPA entry point."""
    if _INDEX_HTML.exists():
        return FileResponse(_INDEX_HTML)
    return HTMLResponse(content="<h1>MarketLens · 电商商品评论竞品分析平台</h1>", status_code=200)
