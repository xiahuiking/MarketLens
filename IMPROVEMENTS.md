# IMPROVEMENTS — 已知问题与改进清单

本文件记录本仓库**实测可复现**的问题，替代泛泛而谈的 TODO。
维护方式：每个条目给出「现象 → 复现命令 → 根因 → 建议」，修复后移入「已修复」并保留验证命令。

## 当前测试基线

```bash
./project_venv/bin/pytest -q
# 245 passed, 0 failed, 0 error  (约 3 分 25 秒)
```

根目录直接运行 `pytest` 即可，无需再写 `pytest tests/`。
用例数从 506 降到 245，是因为移除了爬虫模块及其 261 个测试（见已修复第 4 条）。

`integration` 标记（`-m integration`）保留用于需要真实外部服务（LLM / 搜索 / 数据库）的用例；
当前 `tests/` 下暂无此类用例。

---

## 一、已修复（2026-09-21）

### 1. 根目录 `pytest` 有 7 个 collection error

**现象**

```
$ pytest -q
ERROR engines/ReportEngine/utils/test_chart_validator.py        # No module named 'engines'
ERROR engines/ReportEngine/utils/test_json_parser.py            # No module named 'engines'
ERROR tools/.../MediaCrawler/test/test_mongodb_integration.py   # No module named 'motor'
ERROR tools/.../MediaCrawler/test/test_proxy_ip_pool.py         # No module named 'redis'
ERROR tools/.../MediaCrawler/test/test_redis_cache.py           # No module named 'redis'
ERROR tools/.../MediaCrawler/test/test_utils.py                 # No module named 'playwright'
ERROR tools/.../MediaCrawler/tests                              # No module named 'tests.conftest'
```

**根因**（三件事叠在一起）

1. `tools/SentinelSpider/DeepSentimentCrawling/MediaCrawler/` 是 vendored 第三方项目，
   自带一个 `tests/` 包。它与本仓库顶层 `tests/` 同名，其内部
   `from tests.conftest import ...` 解析到了**本仓库**的 `tests` 包（该包里没有 `conftest.py`）。
2. `engines/ReportEngine/utils/` 下的两个包内测试文件在收集时只能把该目录加进 `sys.path`，
   因而无法解析 `engines.*` 绝对导入。
3. vendored 爬虫的测试依赖 `playwright` / `redis` / `motor`，不在 `requirements.txt` 中。

**修复**

- `pytest.ini` 增加 `testpaths = tests` 与 `norecursedirs`：默认只收集本仓库 `tests/`，
  显式传路径（`pytest engines/`）时仍按传入路径收集。
- `engines/ReportEngine/utils/test_{chart_validator,json_parser}.py` → `tests/`（`git mv`），
  恢复 **39 个**此前完全没被执行过的用例。

**验证**：`./project_venv/bin/pytest -q` 无 `ERROR`；`./project_venv/bin/pytest tests/test_chart_validator.py tests/test_json_parser.py -q` → `39 passed`。

**后续**：本条的第 1、3 条根因都来自 `tools/SentinelSpider/` 下 vendored 的爬虫项目。
该目录已连同其测试整体移除（见第 4 条），因此这类收集错误不会再出现。

---

### 2. 引擎 e2e 契约过期（11 个用例失败）

`tests/test_{competitor,trend}_engine_e2e.py`。看似"mock 过期"，实际是**两个独立问题**。

#### 2a. 真 bug：`progress_callback` 为 `None` 时被无条件调用

**现象**

```
TypeError: 'NoneType' object is not callable
  engines/CompetitorEngine/nodes/generate_structure.py:21
During task with name 'generate_structure'
```

**根因**：`run_research(..., progress_callback: Optional[Callable] = None)` 的**默认用法**是不传回调，
但三个引擎里共 15 处调用点无条件执行 `self.ctx.progress_callback({...})`。
只有 `format_report` / `save_report` 的部分位置写了 `if` 守卫，其余都没有。

受影响文件：

```
engines/{Review,Competitor,Trend}Engine/nodes/generate_structure.py   # 各 1 处
engines/{Review,Competitor,Trend}Engine/nodes/initial_search.py       # 各 1 处
engines/{Review,Competitor,Trend}Engine/nodes/reflection_summary.py   # 各 1 处
engines/ReviewEngine/nodes/save_report.py                             # 1 处
```

**修复**：全部 15 处按同文件既有风格补 `if self.ctx.progress_callback:` 守卫。

**验证**：`./project_venv/bin/pytest tests/test_{competitor,trend}_engine_e2e.py -q`
（新增 `test_research_without_progress_callback` 作为回归用例）。

#### 2b. mock 打在了节点不调用的方法上，导致测试真的联网

**现象**：修掉 `TypeError` 后用例"通过"了，但单文件耗时 **498 秒**。

**根因**：测试 patch 的是 `LLMClient.stream_invoke_to_string`，
而结构 / 查询 / 总结类节点实际调用的是 `LLMClient.structured_invoke`（见 `engines/common/llm_client.py`）。
于是这些节点拿着 `base_url="https://test.api.com"` 和假 key 真的发起 HTTP 请求，
靠节点自身的降级兜底才产出报告——**通过是偶然的，且依赖网络失败的方式**。

同一文件内还有第二处契约漂移：TrendEngine 的搜索后端已切到 Bocha
（消费方读 `response.webpages`，见 `engines/TrendEngine/nodes/_search_utils.py`），
测试却仍在构造 `TavilyResponse`（其字段是 `.results`）→
`AttributeError: 'TavilyResponse' object has no attribute 'webpages'`。

**修复**

- 结构化 mock 改为**按 `output_model` 分派**（`ReportStructure` / `SearchOutput` /
  `InitialSummaryOutput` / `ReflectionSummaryOutput`），而不是按调用序号返回字符串；
  出现未预期模型时直接 `AssertionError`，契约再次漂移会立刻暴露。
- 搜索响应改用 `BochaResponse` + `WebpageResult`。
- 删除已成死代码的 `retry_helper` 预注册 hack（仓库内已无裸 `retry_helper` 导入）。

**验证**：`./project_venv/bin/pytest tests/test_{competitor,trend}_engine_e2e.py -q`
→ `14 passed in 1.7s`（原 ≈10 分钟），且不再产生任何外部请求。

---

### 3. `test_crawler_spider_config.py` 依赖 import 顺序（5 个用例失败）

**现象**：单独运行 `pytest tests/test_crawler_spider_config.py` → `13 passed`；
全量运行 → 5 个默认值断言失败。

```
FAILED test_default_db_host / test_default_db_name /
       test_default_mindspider_base_url / test_default_mindspider_model_name /
       test_default_db_dialect
```

**根因**：仓库当时存在三个顶层 `config` 模块，`sys.modules["config"]` 里是谁取决于 import 顺序：

- `app/config.py` — `DB_DIALECT` 默认 `postgresql`
- `tools/SentinelSpider/config.py` — `DB_DIALECT` 默认 `mysql`
- `tools/SentinelSpider/DeepSentimentCrawling/MediaCrawler/config/`（包）

测试里写的是裸 `from config import Settings`，全量运行时命中了 `app.config`，
于是默认值断言全部对不上。

**处理**：先改为 `importlib.util.spec_from_file_location` 按文件路径加载，与收集顺序解耦；
随后该测试连同 `tools/SentinelSpider/` 一并删除（见第 4 条），**根因随之消失**。

**验证**：仓库内已无裸 `config` 导入：

```bash
$ grep -rn "^from config import\|^import config$" --include="*.py" app/ engines/ tests/ scripts/ tools/
# 无输出
```

**附带修复**：删除前该文件里 `test_env_var_sentinel_spider_keys` 被定义了两次，
后者静默覆盖前者、其中一个从未执行；已重命名区分。

---

### 4. 移除爬虫模块，并把 Docker 建表改为电商表

**背景**：`tools/SentinelSpider/`（含 vendored 的 `DeepSentimentCrawling/MediaCrawler/`）
是本项目转向电商之前的历史资产，与当前主线无关，其许可为
**NON-COMMERCIAL LEARNING LICENSE 1.1（禁止商用）**。唯一还依赖它的是 `docker-entrypoint.sh`。

**顺带暴露出的真实缺陷**：`docker-entrypoint.sh` 原先执行
`tools/SentinelSpider/schema/init_database.py`，创建的是社交媒体爬虫表
（`bilibili_video`、`weibo_note`、`xhs_note`、`tieba_note`…）；
而 ReviewEngine 唯一取数的 `product` / `review` 表定义在 `tools/ecommerce/schema.py`，
其 `init_ecommerce_tables()` **没有任何调用方**，`import_amazon.py` 也只做 INSERT 不建表。
即：全新 Docker 部署下这两张表根本不会被创建，导入/查询都会失败。

**修复**

- 删除 `tools/SentinelSpider/`（271 个跟踪文件，另含磁盘上 805MB 的 gitignored `browser_data/` 运行时数据），
  以及 12 个测试文件 / 261 个用例。
- `tools/ecommerce/schema.py` 增加 `__main__` 入口；`docker-entrypoint.sh` 改为
  `python3 -m tools.ecommerce.schema`（幂等，建 `product` / `review`）。
- 清理随之失效的配置与文档：`app/config.py` 的 `SENTINEL_SPIDER_*` 字段（含 `reload_settings` 的清理列表）、
  `app/services/system_service.py` 的 `CONFIG_KEYS`、`docker-compose.yaml`、`.env.example`。
- `docker-compose.yaml` / `.env.example` 的默认库名 `media_crawler` → `marketlens`。
- `engines/ReviewEngine/tools/sentiment_analyzer.py` 中残留的旧名 `MediaCrawlerDB` docstring → `ProductReviewDB`。

**验证**

```bash
$ ./project_venv/bin/pytest -q
245 passed, 0 failed, 0 error
$ ./project_venv/bin/python -c "import tools.ecommerce.schema as s; print(s.init_ecommerce_tables)"
<function init_ecommerce_tables at ...>
```

---

## 二、仍待修复（按优先级）

### 1. 无打包配置，20 处 `sys.path` 注入

```bash
$ ls pyproject.toml            # No such file or directory
$ grep -rn "sys.path.insert\|sys.path.append" --include="*.py" \
    app/ engines/ tests/ scripts/ tools/ecommerce/ | wc -l
20
$ grep -rln "sys.path.insert\|sys.path.append" --include="*.py" \
    app/ engines/ tests/ scripts/ tools/ecommerce/ | wc -l
20
```

**影响**：`engines` / `app` 的导入是否成功取决于运行时 cwd 与 import 顺序；
无法 `pip install -e .`，也无法被其他项目复用。

**建议**：加 `pyproject.toml`（`setuptools` 或 `hatchling`），把 `app`、`engines` 声明为包，
逐步移除全部 20 处注入。注意 `engines/` 目前没有 `__init__.py`（靠命名空间包工作）。

### 2. 配置热更新不完全（15 处按值导入）

```bash
$ grep -rn "from app.config import" --include="*.py" app/ engines/ scripts/ \
    | grep -c settings
15
```

`config.reload_settings()` 会重新赋值 `config.settings`，
而 `from app.config import settings` 把启动时的旧对象固定在模块命名空间里，热更新对其无效。

**正确写法**（可参照 `engines/ReviewEngine/utils/db.py:30`）：

```python
def _get_settings():
    from app import config
    return config.settings
```

**典型待改点**：`app/services/search_service.py:16,150,181,221`、
`engines/CompetitorEngine/tools/search.py:31`、`engines/ForumEngine/llm_host.py:9`。

### 3. LLM 调用无 token / 成本 / 耗时核算

一次报告生成会调用三个分析引擎 + 章节生成 + 图表修复，调用次数与成本完全不可见，
只能翻 `logs/*.log`。
**建议**：在 `engines/common/llm_client.py` 的 `invoke` / `stream_invoke_to_string` /
`structured_invoke` 三个出口统一埋点，落盘 JSONL（模型、prompt/completion tokens、耗时、重试次数），
再在 `/api/events` 上暴露。

### 4. 任务状态只在内存，且最多保留 5 条

```python
# app/services/report_service.py:28,32,34
MAX_TASK_HISTORY = 5
current_task: Optional["ReportTask"] = None
tasks_registry: dict[str, "ReportTask"] = {}
```

**影响**：进程重启即丢进行中的长任务；超过 5 条的历史被静默裁剪；
没有并发上限，多个报告任务会同时打满 LLM 配额。
**建议**：任务状态落表（复用现有 MySQL），加并发信号量，并把裁剪策略改为按时间/TTL。

### 5. 无 CI

没有 `.github/`。README 承诺过 `ruff check` + `pytest -m "not integration"`。
**建议**：加一条最小 workflow（安装 `requirements.txt` → `ruff check` → `pytest -q`）。
整套测试已完全 mock 掉外部依赖（不再有需要爬虫依赖的用例），因此 CI 无需数据库、网络或额外 venv。

### 6. 顶层无 LICENSE（有意为之）

```bash
$ ls LICENSE                    # 不存在（作者保留全部权利，这是明确决定，不是遗漏）
```

移除爬虫模块后，仓库内**已无非商用许可约束**：剩余第三方资源只剩
`engines/ReportEngine/renderers/assets/fonts/`（思源宋体，SIL OFL，可商用）
与 `renderers/libs/` 下的 html2canvas / jspdf（各自附带许可，随文件分发时保留）。
**建议**：若后续要公开分发，补一份顶层许可即可；README「第三方组件与许可」已列明现状。

### 7. 评测未基准化

`scripts/experiment_sentiment_impact.py` 已有 5 个子命令
（`quality` / `run` / `prompt-ab` / `compare` / `llm-vs-model`），
产物是一次性结果（`data/exp_sentiment/llm_vs_model.json`，n=150）：
本地情感模型 精确 38% / 极性 67.3% / 3.7s，LLM 精确 54% / 极性 78% / 119s。

**影响**：结论不可回归——改动提示词或换模型后，无法判断是变好还是变坏。
**建议**：固定一组 query 与评论样本作为基准集，把脚本接入 CI 的 nightly 任务；
补上 README 路线图里那条「单智能体 vs 多智能体」的量化对比。
