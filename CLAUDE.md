# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

**MarketLens**（电商商品评论竞品分析平台）—— 一个多智能体（multi-agent）LLM 平台，用于分析电商商品评论与竞品。它将 Amazon 评论数据导入本地 MySQL 数据库，然后运行一组基于 LangGraph 的"引擎"Agent 生成研究报告，最终渲染为 HTML / Markdown / PDF。

技术栈为：**FastAPI**（后端，`app/`）+ **LangGraph**（AI Agent，`engines/`）+ **Vue 3 / Vite / Element Plus / Pinia**（前端，`frontend/`）+ **MySQL**（默认数据库）。所有组件之间通过 REST + Server-Sent Events（SSE）通信。

## 常用命令

### Python 环境

本地存在一个已 gitignore 的虚拟环境，无需重建：
- `project_venv/` —— 主应用（后端 + 引擎 + ReportEngine 依赖，如 `weasyprint`/`torch`）。对应 `pip install -r requirements.txt`。

### 运行后端

```bash
./project_venv/bin/python main.py            # 从 app/config.py 的 settings 读取 HOST/PORT
# 或者
./project_venv/bin/uvicorn app.main:app --reload
```

FastAPI 应用同时对外提供 REST API 和已构建的 Vue SPA 静态文件（来自 `frontend/dist/`），因此生产环境下后端与前端无需分开启动。

### 运行前端（开发模式）

```bash
cd frontend && npm install && npm run dev     # Vite 开发服务器
npm run build                                  # vue-tsc + vite build → frontend/dist/
```

### 测试

```bash
./project_venv/bin/pytest                       # 运行全部测试
./project_venv/bin/pytest tests/test_visualization.py::TestX::test_y   # 运行单个测试
./project_venv/bin/pytest -m "not integration"  # 跳过网络/数据库相关测试
./project_venv/bin/pytest -m integration        # 仅运行集成测试（需要数据库/网络）
```

- `@pytest.mark.asyncio` 标记异步测试（pytest-asyncio 已在 `requirements.txt` 中）。
- `@pytest.mark.integration` 在 `pytest.ini` 中注册，用于标记会请求真实外部服务的测试；当前 `tests/` 下暂无此类用例。
- 各引擎的端到端测试（`tests/test_*_engine_e2e.py`）全部用 mock 替换 LLM / 搜索 / 数据库依赖，**不需要真实 API Key**，默认即纳入运行范围。

### Docker（全栈）

```bash
docker-compose up          # db（MySQL）+ backend + frontend（nginx）
```

`docker-entrypoint.sh` 会等待 MySQL 就绪，运行 `python3 -m tools.ecommerce.schema` 创建电商表（`product` / `review`，幂等），然后启动 uvicorn。nginx 将 `/api/` 代理到后端，并对 SSE 关闭缓冲。

### 导入数据（ReviewEngine 的前置条件）

```bash
# 0. 建表（幂等，可重复执行；Docker 部署时由 docker-entrypoint.sh 自动完成）
./project_venv/bin/python -m tools.ecommerce.schema
# 1. 下载 Amazon Reviews 2023（支持断点续传）
./project_venv/bin/python tools/ecommerce/download_amazon.py \
    --url "https://mcauleylab.ucsd.edu/public_datasets/data/amazon_2023/raw/meta_categories/meta_Electronics.jsonl.gz" \
    --out data/amazon/meta_Electronics.jsonl.gz
# 2. 导入 MySQL（product 表幂等，review 表 append-only）
./project_venv/bin/python tools/ecommerce/import_amazon.py \
    --meta data/amazon/meta_Electronics.jsonl.gz \
    --reviews data/amazon/Electronics.jsonl.gz --limit 200000
```

## 架构

### 顶层目录结构

```
app/        FastAPI 后端 —— config、routers、services、schemas、utils
engines/    LangGraph AI Agent（核心产品）
tools/      独立脚本 —— ecommerce 导入/下载 + schema、SentimentAnalysisModel（情感模型，未被代码引用）
frontend/   Vue 3 SPA
tests/      pytest 测试套件
```

### `app/` —— HTTP 层（薄）

- `app/main.py` —— 组装 FastAPI、注册路由、挂载 Vue SPA、执行 lifespan。
- `app/routers/` —— REST 端点：`system`、`config`、`forum`、`search`、`events`（SSE）、`report`。
- `app/services/` —— 业务逻辑，尽量保持框架无关（如 `report_service.py`、`forum_service.py`、`search_service.py`、`cost_service.py`）。
- `app/services/event_bus.py` —— 一个进程内极简的发布/订阅。服务通过 `publish()` 发布事件；`app/routers/events.py` 订阅并转发给 `/api/events/stream` 的 SSE 客户端。事件类型包括 `console_output`、`forum_message`、`engine_progress`、`engine_result`、`cost_update`。

### `engines/` —— AI Agent

四个主 Agent，每个都是一个 LangGraph `StateGraph`，外加共享基础设施：

- **`ReviewEngine`**（口碑 Agent）—— 通过 `ProductReviewDB` 查询本地 MySQL 电商库（`product`/`review` 表），并对结果做聚类 + 情感 + 方面级情感（aspect-sentiment）后处理。是唯一访问数据库的引擎。
- **`CompetitorEngine`**（竞品 Agent）—— 基于网络搜索（Tavily/Anspire/Bocha）。
- **`TrendEngine`**（趋势 Agent）—— 基于网络搜索。
- **`ReportEngine`** —— 汇总三个引擎的输出 + 论坛日志，生成最终报告。
- **`ForumEngine`** —— 主持上述 Agent 之间讨论的"主持人"。
- **`engines/common/llm_client.py`** —— 共享的 `LLMClient`，封装 OpenAI SDK 以对接任意 OpenAI 兼容端点，同时是全平台 LLM 调用的**唯一成本埋点处**。
- **`engines/common/usage.py` + `pricing.py`** —— token / 成本核算：用量账本（记录、聚合、JSONL 落盘、订阅回调）与价目表（`model_prices.json`，可编辑）。

每个引擎遵循相同的目录结构：`agent.py`（模块级入口：`run_research()` / `generate_report()`）、`context.py`（一个 `@dataclass` 依赖容器，持有 `llm_client`、`config`、工具）、`graph.py`（`build_*_graph(ctx)` 返回编译好的 `StateGraph`）、`nodes/`（每个图节点一个类，各自 `__call__(state) -> dict`）、`llms/base.py`（重新导出共享的 `LLMClient`）、`tools/`、`prompts/`、`utils/`。

三个搜索型引擎共享同一条节点流程：

```
generate_structure → initial_search → initial_summary
  → reflection_search → reflection_summary  (循环 MAX_REFLECTIONS 次)
  → format_report → save_report
```

### ReportEngine 流水线（最复杂的引擎）

`ReportEngine` 是两阶段流水线：

1. **章节生成** —— 各节点产出逐章节的 JSON，并依据 `engines/ReportEngine/ir/schema.py` 中手写的 JSON Schema（`CHAPTER_JSON_SCHEMA`）进行校验。该 Schema 定义了一套丰富的块词汇表（`heading`、`paragraph`、`table`、`swotTable`、`pestTable`、`engineQuote`、`kpiGrid`、`widget`、`figure` 等）。
2. **渲染** —— `engines/ReportEngine/renderers/` 将章节 IR 渲染为 HTML（`html_renderer.py`）、Markdown（`markdown_renderer.py`）和 PDF（`pdf_renderer.py`，基于 WeasyPrint；需要 Pango/cairo 系统库）。`visualization/` 构建图表/数据组件（阶段5）。

完整的 IR 契约（块类型、行内标记、引擎标识 `review`/`competitor`/`trend`）都定义在 `engines/ReportEngine/ir/schema.py` 中 —— 任何对报告输出结构的改动都必须同步到这里。

### 数据库层

- `engines/ReviewEngine/utils/db.py` —— 异步 SQLAlchemy 2.x 引擎（`create_async_engine`），支持 MySQL（`mysql+aiomysql`）和 PostgreSQL（`postgresql+asyncpg`）。主要读接口是 `fetch_all(query, params)` → `list[dict]`。配置变更后 `reset_engine()` 会重建引擎。
- `tools/ecommerce/schema.py` —— `product` 和 `review` 两张表的 DDL（Amazon schema，以 `parent_asin` 为键）。

### 端到端数据流

```
download_amazon.py → import_amazon.py → MySQL (product/review)
  → ReviewEngine 查询数据库，Competitor/TrendEngine 进行网络搜索
  → 各引擎将 .md 写入 data/report/{review,competitor,trend}/
  → ForumEngine 写入日志 logs/forum.log
  → ReportEngine 读取这些文件 + 论坛日志 → IR → HTML/MD/PDF
```

## 约定与注意事项

- **配置基于 `pydantic-settings`**，位于 `app/config.py`，从仓库根目录的 `.env` 加载（路径固定，与运行时的 cwd 无关）。每个 Agent 拥有自己的 `*_ENGINE_API_KEY` / `*_ENGINE_BASE_URL` / `*_ENGINE_MODEL_NAME` 三元组。`reload_settings()` 可在运行时重载配置（由 `/api/config` 路由调用）—— 它会先清除环境变量，以确保 `.env` 的修改生效。

- **应动态导入 `config.settings`，而非按值导入。** 因为 `reload_settings()` 会重新赋值 `config.settings`，需要感知运行时配置变更的代码应使用 `from app import config; config.settings`（参见 `engines/ReviewEngine/utils/db.py`），而不是 `from app.config import settings`（后者会固定指向启动时的旧对象）。

- **项目曾多次改名。** 历史映射：`InsightEngine`→`ReviewEngine`、`MediaEngine`→`CompetitorEngine`、`QueryEngine`→`TrendEngine`、`MediaCrawlerDB`→`ProductReviewDB`、`SentinelAI`/`尚舆`→`MarketLens`。**三个分析引擎的内部标识均已同步**：`InsightContext`/`InsightGraphState`/`build_insight_graph`→`ReviewContext`/`ReviewGraphState`/`build_review_graph`；`MediaContext`/`MediaGraphState`/`build_media_graph`→`CompetitorContext`/`CompetitorGraphState`/`build_competitor_graph`；`QueryContext`/`QueryGraphState`/`build_query_graph`→`TrendContext`/`TrendGraphState`/`build_trend_graph`。对应测试为 `tests/test_{review,competitor,trend}_engine_e2e.py`。仍残留的旧名只有 `MediaCrawlerDB`（DB 层，与引擎命名无关，已仅存于历史提交）；注意 `洞察` 作为普通中文词仍大量出现在提示词与报告模板中（如「深度洞察」），与引擎命名无关，不要误改。另外 `common.llm_client`→`engines.common.llm_client`（`aa99702` 修复了该脆弱 import）。

- **实际默认数据库是 MySQL**，尽管 `app/config.py` 中 `DB_DIALECT` 的 `Field(default="postgresql")`。docker-compose 文件、`tools/ecommerce/schema.py` 以及导入脚本均假定 MySQL。请显式设置 `DB_DIALECT=mysql`，或依赖 `.env`/docker-compose 的配置。

- **`LLMClient`**（`engines/common/llm_client.py`）是全项目唯一的 OpenAI 兼容 chat 封装，它会在用户 prompt 中注入"今天的实际时间"前缀。`structured_invoke()` 使用 LangChain 的 `with_structured_output`，对不支持 `tool_choice` 的推理模型提供 function-calling → json-mode 的降级回退。

- **Token / 成本核算的埋点契约。** 新增 LLM 调用必须走 `LLMClient`，或像 `engines/ForumEngine/llm_host.py`（裸 OpenAI 客户端）那样显式调用 `usage.record_llm_call(...)`；绕过它 = 该调用在成本面板里不可见。归因靠 `app/services/cost_service.py`：`usage.set_active_run()` 做全局兜底，`usage.set_usage_context()` 做线程级覆盖（新线程不继承上下文，需显式设置），报告任务通过 `ReportTask.run_id` 关联。`structured_invoke()` 必须保留 `include_raw=True`，否则 `with_structured_output` 只返回解析后的对象，拿不到真实 token usage。价目表未命中的模型一律标为「价格未知」并计入 `unpriced_calls`，**不要**回退成 0。

- **LLM 配置是按 Agent 划分的。** ReportEngine 还会额外从其他引擎的 Key 构建一组"rescue" LLM 客户端，用于重试失败的章节生成。

- **SSE 是实时通信机制**（没有 WebSocket）。存在两条相互独立的流：全局的 `/api/events/stream`（基于 event bus，带 300 条事件回放缓冲区），以及每任务独立的 `/api/report/stream/{task_id}`（报告进度/章节）。任何承载这些流的 nginx 路由都需保持 `proxy_buffering off`。
