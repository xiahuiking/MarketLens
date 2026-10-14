"""引擎实跑与下游报告的真实实现（--live 路径）+ token/cost 采集。

这些函数是评测 harness 的「真实」实现；fixture 模式（smoke / 录制回放）会用注入的
fake 替换 ``run_engine_fn`` / ``report_fn``，从而不烧钱地跑通骨架。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Optional

from app import config as _cfg
from app.config import Settings
from engines.common import usage


# ── 引擎配置 / 客户端 / 搜索代理 ────────────────────────────────

def build_engine_config(
    engine: str,
    output_dir: Path,
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
) -> Settings:
    """镜像 search_service._run_*_research 的 Settings 构造，但 OUTPUT_DIR 指向 workspace。"""
    s = _cfg.settings
    if engine == "review":
        return Settings(
            REVIEW_ENGINE_API_KEY=api_key or s.REVIEW_ENGINE_API_KEY,
            REVIEW_ENGINE_BASE_URL=base_url or s.REVIEW_ENGINE_BASE_URL,
            REVIEW_ENGINE_MODEL_NAME=model or s.REVIEW_ENGINE_MODEL_NAME,
            DB_HOST=s.DB_HOST, DB_USER=s.DB_USER, DB_PASSWORD=s.DB_PASSWORD,
            DB_NAME=s.DB_NAME, DB_PORT=s.DB_PORT, DB_CHARSET=s.DB_CHARSET,
            DB_DIALECT=s.DB_DIALECT,
            MAX_REFLECTIONS=s.MAX_REFLECTIONS,
            MAX_CONTENT_LENGTH=s.MAX_CONTENT_LENGTH,
            OUTPUT_DIR=str(output_dir),
        )
    if engine == "competitor":
        return Settings(
            COMPETITOR_ENGINE_API_KEY=api_key or s.COMPETITOR_ENGINE_API_KEY,
            COMPETITOR_ENGINE_BASE_URL=base_url or s.COMPETITOR_ENGINE_BASE_URL,
            COMPETITOR_ENGINE_MODEL_NAME=model or s.COMPETITOR_ENGINE_MODEL_NAME,
            SEARCH_TOOL_TYPE=s.SEARCH_TOOL_TYPE,
            TAVILY_API_KEY=s.TAVILY_API_KEY,
            BOCHA_WEB_SEARCH_API_KEY=s.BOCHA_WEB_SEARCH_API_KEY,
            ANSPIRE_API_KEY=s.ANSPIRE_API_KEY,
            MAX_REFLECTIONS=s.MAX_REFLECTIONS,
            SEARCH_CONTENT_MAX_LENGTH=s.SEARCH_CONTENT_MAX_LENGTH,
            OUTPUT_DIR=str(output_dir),
        )
    if engine == "trend":
        return Settings(
            TREND_ENGINE_API_KEY=api_key or s.TREND_ENGINE_API_KEY,
            TREND_ENGINE_BASE_URL=base_url or s.TREND_ENGINE_BASE_URL,
            TREND_ENGINE_MODEL_NAME=model or s.TREND_ENGINE_MODEL_NAME,
            TAVILY_API_KEY=s.TAVILY_API_KEY,
            MAX_REFLECTIONS=s.MAX_REFLECTIONS,
            SEARCH_CONTENT_MAX_LENGTH=s.SEARCH_CONTENT_MAX_LENGTH,
            OUTPUT_DIR=str(output_dir),
        )
    raise ValueError(f"未知引擎: {engine}")


def build_llm_client(engine: str, config: Settings):
    if engine == "review":
        from engines.ReviewEngine.llms import LLMClient
        return LLMClient(
            api_key=config.REVIEW_ENGINE_API_KEY,
            model_name=config.REVIEW_ENGINE_MODEL_NAME,
            base_url=config.REVIEW_ENGINE_BASE_URL,
        )
    if engine == "competitor":
        from engines.CompetitorEngine.llms import LLMClient
        return LLMClient(
            api_key=config.COMPETITOR_ENGINE_API_KEY,
            model_name=config.COMPETITOR_ENGINE_MODEL_NAME,
            base_url=config.COMPETITOR_ENGINE_BASE_URL,
        )
    if engine == "trend":
        from engines.TrendEngine.llms import LLMClient
        return LLMClient(
            api_key=config.TREND_ENGINE_API_KEY,
            model_name=config.TREND_ENGINE_MODEL_NAME,
            base_url=config.TREND_ENGINE_BASE_URL,
        )
    raise ValueError(f"未知引擎: {engine}")


def build_search_agency(engine: str, config: Settings):
    if engine == "review":
        return None  # ReviewContext 内部构建 ProductReviewDB
    if engine == "competitor":
        from engines.CompetitorEngine.tools import (
            AnspireAISearch, BochaMultimodalSearch, TavilySearchWrapper,
        )
        st = (config.SEARCH_TOOL_TYPE or "TavilyAPI")
        if st == "TavilyAPI":
            return TavilySearchWrapper(api_key=config.TAVILY_API_KEY)
        if st == "AnspireAPI":
            return AnspireAISearch(api_key=config.ANSPIRE_API_KEY)
        return BochaMultimodalSearch(api_key=config.BOCHA_WEB_SEARCH_API_KEY)
    if engine == "trend":
        from engines.CompetitorEngine.tools.search import TavilySearchWrapper
        return TavilySearchWrapper(api_key=config.TAVILY_API_KEY)
    raise ValueError(f"未知引擎: {engine}")


# ── 实跑入口 ────────────────────────────────────────────────────

def run_engine(
    engine: str,
    query: str,
    config: Settings,
    llm_client,
    search_agency=None,
    progress_callback: Optional[Callable] = None,
) -> dict:
    """调用对应引擎的 run_research，返回 {final_report, paragraphs, ...}。"""
    if engine == "review":
        from engines.ReviewEngine.agent import run_research
        return run_research(
            query, config, llm_client, progress_callback, search_agency=search_agency
        )
    if engine == "competitor":
        from engines.CompetitorEngine.agent import run_research
        return run_research(query, config, llm_client, search_agency, progress_callback)
    if engine == "trend":
        from engines.TrendEngine.agent import run_research
        return run_research(query, config, llm_client, search_agency, progress_callback)
    raise ValueError(f"未知引擎: {engine}")


def run_engine_with_usage(
    engine: str,
    query: str,
    config: Settings,
    llm_client,
    search_agency=None,
    progress_callback: Optional[Callable] = None,
) -> tuple:
    """跑引擎并采集 token/cost 账本。返回 (result, usage_summary)。"""
    run_id = usage.new_run_id("eval-engine")
    usage.set_active_run(run_id)
    usage.set_usage_context(run_id=run_id, engine=engine)
    usage.ensure_run(run_id, query=query)
    result = run_engine(engine, query, config, llm_client, search_agency, progress_callback)
    summary = usage.get_run_summary(run_id) or {}
    return result, summary


def extract_citations(result: dict) -> list:
    from app.services.search_service import _extract_citations_from_result
    return _extract_citations_from_result(result)


def generate_report(query: str, reports: list, config: Settings, forum_logs: str = "") -> dict:
    from engines.ReportEngine.agent import generate_report as _gen
    return _gen(query, reports, forum_logs=forum_logs, config=config)


def render_markdown(document_ir: dict) -> str:
    from engines.ReportEngine.renderers.markdown_renderer import MarkdownRenderer
    return MarkdownRenderer().render(document_ir)


def run_report(
    query: str,
    reports: list,
    config: Settings,
    forum_logs: str = "",
) -> dict:
    """下游冻结封装：直调 generate_report，读回 IR 并渲染 markdown。

    返回 {document_ir, final_report_md, report_id, html_content}。
    """
    result = generate_report(query, reports, config, forum_logs=forum_logs)
    ir_path = result.get("ir_filepath")
    document_ir: dict = {}
    if ir_path:
        try:
            document_ir = json.loads(Path(ir_path).read_text(encoding="utf-8"))
        except Exception:
            document_ir = {}
    return {
        "document_ir": document_ir,
        "final_report_md": render_markdown(document_ir),
        "report_id": result.get("report_id"),
        "html_content": result.get("html_content", ""),
    }


def build_report_config(output_dirs: dict, model=None, base_url=None, api_key=None) -> Settings:
    """构建 ReportEngine Settings：钉死模型 + 三个输出目录指向 workspace。"""
    s = _cfg.settings
    return Settings(
        REPORT_ENGINE_API_KEY=api_key or s.REPORT_ENGINE_API_KEY,
        REPORT_ENGINE_BASE_URL=base_url or s.REPORT_ENGINE_BASE_URL,
        REPORT_ENGINE_MODEL_NAME=model or s.REPORT_ENGINE_MODEL_NAME,
        OUTPUT_DIR=str(output_dirs["out"]),
        DOCUMENT_IR_OUTPUT_DIR=str(output_dirs["ir"]),
        CHAPTER_OUTPUT_DIR=str(output_dirs["chapter"]),
        REPORT_BASELINE_FILE=str(output_dirs["out"] / "report_baseline.json"),
    )
