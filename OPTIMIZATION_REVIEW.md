# MarketLens 优化空间复核（2026-09-28）

本文是对 `IMPROVEMENTS.md`（2026-09-22 版）的**复核与扩充**。原清单的 6 条未修复项全部仍然成立
（见第六节，数字已更新），但复核过程中发现**原清单遗漏了一批更严重的问题**，其中数据库层一条
是当前最大的性能杠杆。

所有结论均给出「复现命令 / 文件:行号 / 实测数字」，未实测的不写。

---

## 零、先看基线：测试已经变红，且慢了 3 倍

```bash
$ ./project_venv/bin/pytest -q
2 failed, 295 passed, 29 warnings in 598.64s (0:09:58)
```

对照 `IMPROVEMENTS.md` 第 10 行记录的 `272 passed, 0 failed (193.91s)`：

| 指标 | 原记录 | 实测 | 变化 |
|---|---|---|---|
| 用例数 | 272 | 297 | +25 |
| 失败 | 0 | **2** | 回归 |
| 耗时 | 194s | **598s** | **3.1×** |

### 0.1 两个失败用例同源：测试读取了本机遗留的真实快照

```
FAILED tests/test_visualization.py::TestVisualizationProvider::test_collect_empty_db_is_graceful
FAILED tests/test_visualization.py::TestVisualizationProvider::test_collect_with_data
```

`engines/ReportEngine/visualization/data_provider.py:172` 直接 glob 磁盘上上一次真实运行的快照：

```python
pattern = str(Path(PROJECT_ROOT) / "data" / "report" / "review" / "state_*.json")
files = _glob.glob(pattern)
```

这条路径**不看注入进来的 `db` 对象**。于是：

- `test_collect_empty_db_is_graceful`（`db=FakeDB()` 空库）仍然读到本机 50 条真实情感分布，
  断言 `sentiment_distribution == {}` 失败。**在干净检出上会通过，在本机必失败** —— 用例不确定。
- `test_collect_with_data` 还叠加了第二层过期：期望值仍是旧的三档
  `{"正面": 85, "中性": 10, "负面": 5}`（`tests/test_visualization.py:159`），
  而现行实现已改为五档（`非常正面/正面/中性/负面/非常负面`，见
  `engines/ReviewEngine/tools/sentiment_analyzer.py:213` 与
  `engines/ReportEngine/visualization/chart_builder.py:119` 已支持）。

**建议**：`_sentiment_from_review_state` 增加可注入的目录参数（默认仍读 `PROJECT_ROOT`），
测试里指到 `tmp_path`；同时把断言更新为五档口径。`tests/conftest.py` 已经隔离了
`data/usage/`（第 17-25 行）与 `logs/forum.log`（第 28 行），唯独漏了这个目录 —— 补上即可。

### 0.2 598s 里 587s 是 7 个用例

```
120.56s  test_report_engine_e2e.py::test_generate_report_with_chinese_query
100.64s  test_check_input_files.py::test_generate_report_without_review
 98.56s  test_report_engine_e2e.py::test_generate_report_returns_html
 74.28s  test_report_engine_e2e.py::test_generate_report_save_creates_file
 66.13s  test_report_engine_e2e.py::test_empty_reports_still_generates_html
 64.11s  test_report_engine_e2e.py::test_custom_template_does_not_crash
 63.71s  test_report_engine_e2e.py::test_empty_forum_logs_still_generates_html
```

`tests/test_report_engine_e2e.py` 只 patch 了 4 个 LLM 节点（第 137-142 行），
**没有 mock `ProductReviewDB`**。cProfile 定位到真实耗时路径：

```
test_empty_reports_still_generates_html        65.006s
└─ ReportEngine/agent.py:21 generate_report    65.005s
   └─ visualization/data_provider.py:252 collect  61.897s   ← 真实数据库
      └─ ReviewEngine/tools/search.py:154 _execute_query  61.754s
         └─ ReviewEngine/utils/db.py:88 fetch_all        61.680s
```

即：**e2e 测试在真连 MySQL 跑全表扫描**。这既是测试问题，也直接暴露了第一节的
生产性能问题 —— 不是测试写坏了，是这条路径本来就慢。

---

## 一、数据库层：当前最大性能杠杆（单次报告 32s 纯 DB 等待）

### 1.1 实测：`VisualizationProvider.collect('headphone')` = 32.03s / 10 条查询

```bash
$ ./project_venv/bin/python -c "
from engines.ReportEngine.visualization.data_provider import VisualizationProvider
import time; t=time.time(); VisualizationProvider().collect('headphone'); print(time.time()-t)"
# 32.03s
```

逐条耗时（对 `_execute_query` 打点实测）：

```
 20.04s  SELECT asin, parent_asin, user_id, rating, title, content, ... FROM review
          WHERE parent_asin IN (5 个) AND rating <= 2 ORDER BY helpful_vote DESC, ...
  1.56s  SELECT parent_asin, title, brand, store, price, ... FROM product
  1.51s  （同上，不同 limit）
  1.47s  （同上）
  1.42s  （同上）
  1.31s  SELECT FROM_UNIXTIME(review_time/1000, '%Y-%m-%d') ... FROM review
  1.25s  （同 product 查询）
  1.22s  （同 product 查询）
  1.09s  SELECT MIN(review_time), MAX(review_time) FROM review WHERE ...
  1.05s  SELECT rating, COUNT(*) FROM review WHERE parent_asin IN (...)
```

### 1.2 那 20 秒的根因：索引最左列基数=1，等于没有索引

```sql
EXPLAIN SELECT asin FROM review
 WHERE parent_asin IN (...5 个...) AND rating <= 2
 ORDER BY helpful_vote DESC, review_time DESC LIMIT 300;
```

```
-> Limit: 300 row(s)  (cost=498486 rows=300)
    -> Sort: review.helpful_vote DESC, review.review_time DESC  (cost=498486 rows=4e+6)
        -> Filter: (parent_asin in (...) and rating <= 2)  (cost=498486 rows=4e+6)
            -> Table scan on review  (cost=498486 rows=4e+6)     ← 全表扫
```

表规模与现有索引（实测）：

```
review  : 3,904,965 行
product : 1,722,596 行
review 索引: PRIMARY(id) / idx_platform_parent_asin(platform, parent_asin) / idx_review_time
             ^ platform 的 cardinality = 1  →  无法服务只按 parent_asin 过滤的谓词
```

`platform` 只有 `'amazon'` 一个值，把它放在复合索引最左列，使整个索引对
`WHERE parent_asin = ?` **完全不可用**。`engines/ReviewEngine/tools/search.py`
里所有评论查询都是这种形状（第 271、305、342、382、407 行）。

**建议**（改 DDL 即可，收益最大）：

```sql
ALTER TABLE review
  ADD KEY idx_parent_asin (parent_asin),
  ADD KEY idx_parent_rating (parent_asin, rating),
  ADD KEY idx_parent_helpful (parent_asin, helpful_vote);
```

3.9M 行上预期把 20s 降到毫秒级。落点：`tools/ecommerce/schema.py:52`，
并仿照 `_LEGACY_WIDENINGS`（第 60-86 行）加一个幂等的"补索引"迁移，让已有部署自动生效。

### 1.3 同一个查询重复跑了 4 次

对参数打点后统计重复：

```
3x  SELECT parent_asin, title, brand, store, price, ... FROM product
    params=('%headphone%','%headphone%','%headphone%','headphone','%headphone%', ...)
1x  ... params=(..., 5)     ← _resolve_asins
1x  ... params=(..., 6)     ← _competitor_comparison
1x  ... params=(..., 50)    ← _price_bands
```

`data_provider.collect`（`data_provider.py:252-279`）依次调用 `_resolve_asins` /
`_competitor_comparison` / `_price_bands`，每次都重新执行同一条商品检索 SQL，
只是 `LIMIT` 不同。6 条中 4 条是重复劳动，合计约 **6s 纯浪费**。

**建议**：`collect()` 内先取一次 `limit=50` 的结果，其余按需切片复用（或加进程内
`(query, limit)` LRU 缓存）。

### 1.4 商品检索本身无法用索引

`engines/ReviewEngine/tools/search.py:54-58`：

```python
"((title LIKE %s) + (brand LIKE %s) + (store LIKE %s) + (parent_asin = %s))"
"(title LIKE %s OR brand LIKE %s OR store LIKE %s OR parent_asin = %s)"
```

`title` / `brand` / `store` 在 `schema.py:25-27` 中是 `TEXT`，且模式是前置通配
`'%关键词%'` —— MySQL 无法使用任何 B-Tree 索引，必然全表扫 172 万行。

**建议**：`brand` / `store` 收敛为 `VARCHAR(255)` + 普通索引；中文/英文关键词检索
改用 MySQL 8 的 `FULLTEXT ... WITH PARSER ngram`，把 `LIKE '%…%'` 换成 `MATCH ... AGAINST`。
这是中长期项，改造面较大，但 1.2 + 1.3 已能吃掉大部分收益。

### 1.5 同步桥阻塞调用线程

`engines/ReviewEngine/tools/search.py:148-160`：

```python
loop = asyncio.get_event_loop()
if loop.is_closed():
    loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
return loop.run_until_complete(fetch_all(query, params))
```

`ProductReviewDB` 是**同步 API**，在 ReportEngine 可视化里被直接调用，
而 ReportEngine 同时又被 FastAPI 的 async 路由调用 —— 32s 的 DB 等待会占住线程/事件循环。

另外，反复 `asyncio.run()` / 新建 loop 再关闭，会让模块级连接池里绑在旧 loop 上的
aiomysql 连接在 GC 时报错（实测可复现）：

```
Exception ignored in: <function Connection.__del__>
RuntimeError: Event loop is closed
```

`tools/ecommerce/schema.py:109-112` 已经意识到这个问题并手动 `dispose()`，
但业务路径上同样的问题没有处理。

**建议**：`ProductReviewDB` 增加 `async` 版本，ReportEngine 可视化侧改为 await；
或在同步入口显式 `asyncio.to_thread` + 复用单一常驻 loop，并在退出时 `engine.dispose()`。

### 1.6 `count_products()` 每次都数 172 万行

`engines/ReviewEngine/tools/search.py:249-256`：

```python
def count_products(self) -> int:
    rows = self._execute_query("SELECT COUNT(*) AS cnt FROM product")
```

它的唯一用途是区分"库是空的"与"查询词没命中"（见其 docstring）。
在 InnoDB 上 `COUNT(*)` 无 `WHERE` 也要扫全表（实测 0.16–0.68s），
而这个判断完全可以用 `SELECT 1 FROM product LIMIT 1` 代替。

---

## 二、安全：这是当前优先级最高的一组

### 2.1 `GET /api/config` 明文吐出全部密钥，且无鉴权、CORS 全开

`app/services/system_service.py:33-48` 遍历 `CONFIG_KEYS` 返回字符串值，而
`CONFIG_KEYS`（同文件）包含：

```
REVIEW_ENGINE_API_KEY / COMPETITOR_ENGINE_API_KEY / TREND_ENGINE_API_KEY
REPORT_ENGINE_API_KEY / FORUM_HOST_API_KEY / KEYWORD_OPTIMIZER_API_KEY
TAVILY_API_KEY / BOCHA_WEB_SEARCH_API_KEY / ANSPIRE_API_KEY / DB_PASSWORD
```

`app/routers/config.py:10-14` 直接把它作为 `GET /api/config` 的响应体返回，
`app/main.py:67-72`：

```python
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
```

`app/` 下没有任何鉴权依赖，`docker-compose.yaml:38-39` 又把 5000 端口发布到宿主机。

**后果**：用户浏览器打开任意网页，页面里的 JS 就能
`fetch("http://127.0.0.1:5000/api/config")` 拿走全部密钥并回传。
这是"无鉴权 + CORS `*` + 明文密钥"三件事叠加，单独任何一件都不至于这么严重。

**建议**：① 响应里对 `*_API_KEY` / `DB_PASSWORD` 做掩码（只回显后 4 位）；
② 加一个最简鉴权（本地 token / 仅 127.0.0.1 绑定）；③ CORS 改显式白名单；
④ 端口默认绑 `127.0.0.1` 而非 `0.0.0.0`。

### 2.2 PDF 导出遇中文标题必 500（已复现）

`app/routers/report.py:205-211`：

```python
topic = (ir.get("metadata", {}) or {}).get("topic", "report")
filename = f"report_{topic}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
return Response(pdf_bytes, media_type="application/pdf",
                headers={"Content-Disposition": f'attachment; filename="{filename}"'})
```

HTTP header 按 latin-1 编码，`topic` 只要含中文就抛 `UnicodeEncodeError` → 500。
同文件的 Markdown 导出走 `FileResponse` 反而没事（starlette 会做 RFC 5987 兜底）。

**建议**：`filename*=UTF-8''{quote(filename)}`。

### 2.3 LLM 输出经 `v-html` 直接注入 DOM

`frontend/src/components/engine/EnginePanel.vue:33` 与
`frontend/src/components/forum/ForumChat.vue:22`：

```html
<div class="markdown-body" v-html="renderedReport" />
<div class="msg-content" v-html="renderMarkdown(msg.content)"></div>
```

`marked` 不做消毒，`renderedReport` 来自 LLM 输出，`msg.content` 来自论坛 Agent。
模型输出（或搜索结果里夹带的内容）中的 `<img onerror=...>` 会在应用源上执行 ——
而这个源恰好持有上面 2.1 的全部密钥。**建议**：接入 DOMPurify，或对 `marked`
配置禁用 raw HTML。

---

## 三、阻塞事件循环的三处

### 3.1 两个 SSE 生成器在 async 里用阻塞 `queue.Queue`

`app/routers/events.py:137` 与 `app/services/report_service.py:611`：

```python
while True:
    if await request.is_disconnected(): break
    try:
        payload = q.get(timeout=1)      # ← 阻塞调用，跑在事件循环上
        yield f"data: {payload}\n\n"
```

只要有一个 SSE 客户端连着（且没有新事件），事件循环每轮被卡最多 1 秒，
期间所有 HTTP 请求与另一条 SSE 流都被延迟。

**建议**：换 `asyncio.Queue` + `await asyncio.wait_for(q.get(), timeout=1)`。

### 3.2 WeasyPrint 渲染在 `async def` 路由里同步执行

`app/routers/report.py:227-238` → `PDFRenderer.render_to_bytes()` →
`HTML.write_pdf()`（`engines/ReportEngine/renderers/pdf_renderer.py:1599-1606`）。
WeasyPrint 是纯 CPU 的秒级工作，直接冻住整个进程。

**建议**：路由改成同步 `def`（FastAPI 自动放线程池），或 `await asyncio.to_thread(...)`，
并加并发信号量避免多份 PDF 同时吃满 CPU。

### 3.3 每次 LLM 调用都落一次盘

`engines/common/usage.py:377-396` 的 `_persist()` 在全局 `_persist_lock` 下
深拷贝整份 run summary、重写完整 JSON 快照（2 次文件写）。所有引擎线程在这把锁上串行化，
而它位于 LLM 调用热路径上。

**建议**：改成节流写（比如 1s 合并一次 + 退出时 flush）。

---

## 四、前端

### 4.1 报告 SSE 重连会重放全部历史 → 日志重复 + 结束后无限重连

`frontend/src/composables/useReportSSE.ts:95-121,134-149`：

```ts
const url = `/api/report/stream/${taskId}`
eventSource = new EventSource(url)
...
lastEventId.value = data.id || lastEventId.value     // data.id 永远 undefined
...
eventSource.onerror = () => { ...; eventSource?.close(); scheduleReconnect(taskId) }
```

服务端 `app/services/report_service.py:598-604` 每次建连都重放全部历史，
而 `_format_sse`（第 592-595 行）**只发 `event:` / `data:`，从不发 `id:`** ——
所以 `lastEventId` 永远不前进，第 98 行那句注释是失效的。

两个后果：
- 任何一次瞬断（代理空闲超时、后端重启）都会重连并重放 200 帧，
  前端无条件重新 append → 控制台日志成倍重复。
- `onerror` 里没有"任务已终态"判断，且与 `setTimeout(close(), 500)` 存在竞态：
  服务端在 `completed` 后约 1s 关流，此时 `onerror` 可能晚于 `close()` 触发，
  `scheduleReconnect` 便会指向一个已结束的任务并再次调度 → 15s 无限重连循环。

**建议**：服务端补 `id:` 并接受 `?last_event_id=` 跳过已发帧；前端加 `terminal` 标志守卫重连。

### 4.2 控制台 5000 行无虚拟化，每来一行重建整个数组

`frontend/src/components/console/ConsoleOutput.vue:58-66` 的 `visibleLines` computed
在每次 `appendConsoleLine` 时重建全部 N 个对象，模板 `v-for` 再 diff N 个 vnode；
`MAX_LOG_LINES = 5000`（`stores/apps.ts:111`），且全项目没有 `v-memo` / 虚拟列表。
报告生成期间这是每秒几十次的全量重排。

### 4.3 全量引入 Element Plus 与全部 293 个图标

`frontend/src/main.ts:2-19` 用 `app.use(ElementPlus)` + 遍历注册整个
`@element-plus/icons-vue`；`vite.config.ts` 无 `manualChunks`。实测产物：

```
dist/assets/index-*.js    1,182,627 B
dist/assets/index-*.css     351,350 B
dist/assets/logo-*.png    2,237,495 B
```

而实际按名导入使用的图标只有十几个。

### 4.4 2.2MB 的 logo 被 CSS 缩到 40×40

`frontend/src/assets/logo.png` 实测 `1920 x 1920` / 2,237,495 B，
`TheHeader.vue:61-64` 里 `.logo { width: 40px; height: 40px; }`。
首屏为 40px 徽标付 2.2MB。**建议**：换成 80×80 WebP（约 5-10 KB）。

### 4.5 其余前端问题

| 问题 | 位置 | 后果 |
|---|---|---|
| `streamEvents` 存 2000 条完整 SSE payload，**从未被任何组件读取** | `stores/report.ts:42,117-122` | 纯内存浪费，超限后每条事件还复制整个数组 |
| `renderMarkdown` 在渲染函数里调用 | `ForumChat.vue:14-22,34-37` | 每条新消息重解析全部历史（上限 2000 条）的 markdown |
| `saving.value = false` 无 `try/finally` | `ConfigModal.vue:123-144` | 保存失败时按钮永久转圈 |
| `handleGenerate` 无 `try/catch` | `ReportControls.vue:102-119` | 未处理的 promise rejection，无错误 UI |
| 轮询与 SSE 同时写 `currentTask` | `App.vue:43-46` + `stores/report.ts:48-61` | 迟到的轮询响应覆盖新进度，进度条回退 |
| `performSearch` 无在途守卫 | `stores/search.ts:95-109`，`SearchSection.vue:9,58` | 回车+点击并发触发两次搜索 |

---

## 五、架构与技术债

### 5.1 三个搜索型引擎约 2000 行高度重复

`engines/{Review,Competitor,Trend}Engine/nodes/` **文件名完全一致**，
同名文件逐行 diff 的差异行数：

```
                    差异行 / 总行数
generate_structure      35 /  ~90
initial_search          42 /  ~90
initial_summary         27 /  109
reflection_search       37 /  ~90
reflection_summary      20 /  112
format_report           20 /  ~70
save_report             42 /   54
```

差异几乎全是类名替换（`ReviewGraphState` ↔ `CompetitorGraphState`）和几行日志措辞。
三引擎合计 9131 行（Review 4326 / Competitor 2551 / Trend 2254），
其中大部分是同一份逻辑抄了三遍 —— 改一处流程要改三个地方，
这正是 `IMPROVEMENTS.md` 第 2a 条「15 处 `progress_callback` 少了守卫」的成因。

**建议**：抽 `engines/common/graph_nodes.py`，用泛型 state + 一个 `EngineSpec`
（引擎名、搜索后端、prompt 集）参数化；各引擎只保留 prompt 与差异配置。

### 5.2 `html_renderer.py` 是 6536 行的单类

`engines/ReportEngine/renderers/html_renderer.py` —— 1 个 class、**112 个方法**、
6536 行，且 61 处 f-string 拼 HTML。三种渲染器（html/markdown/pdf）各自重复实现了
同一套 IR block 分派（都处理 `heading`/`paragraph`/`swotTable`/`pestTable`/`kpiGrid`/
`engineQuote`/`widget`/`figure`）。IR 词汇表在 `ir/schema.py` 里已经集中定义，
但**分派逻辑没有集中**，新增一种 block 要改三处。

**建议**：先按职责把 HTML 渲染器拆成 `layout / table / chart / inline` 几个模块，
再抽一个共享的 block 分派表；渲染器只提供各 block 的"叶子"实现。

### 5.3 `tools/SentimentAnalysisModel/` 176MB、38 个跟踪文件、零代码引用

```bash
$ grep -rn "SentimentAnalysisModel" --include="*.py" app/ engines/ tests/ scripts/ tools/ecommerce/
# 无输出
$ du -sh tools/SentimentAnalysisModel
176M
```

（CLAUDE.md 已注明「未被代码引用」。）仓库 `.git` 才 46MB，而这个目录是它的数倍。

**建议**：移出主仓库（单独 repo 或 release 附件）。若确实要保留训练脚本，只留 `.py` + README，
权重靠下载脚本获取。

### 5.4 无打包配置，`sys.path` 注入已增至 24 处

```bash
$ ls pyproject.toml setup.py setup.cfg   # 均不存在
$ grep -rn "sys.path.insert\|sys.path.append" --include="*.py" \
    app/ engines/ tests/ scripts/ tools/ecommerce/ | wc -l
24
```

（`IMPROVEMENTS.md` 记录的是 20 处，现在 24 处。）导入能否成功取决于 cwd 与 import 顺序，
无法 `pip install -e .`。**建议**：加 `pyproject.toml`，把 `app` / `engines` 声明为包
（注意 `engines/` 目前无 `__init__.py`，靠命名空间包工作）。

### 5.5 配置热更新仍然失效（14 处）

```bash
$ grep -rn "from app.config import" --include="*.py" app/ engines/ scripts/ | grep -c settings
14
```

`reload_settings()` 会重新赋值 `app.config.settings`，按值导入的模块固定指向旧对象。
**已确认的功能性后果**：`app/services/search_service.py:16` 是这种写法，
而它在第 219-227 / 251-260 / 288-295 行被用于构造引擎 —— 也就是说
**`PUT /api/config` 改完配置后，引擎仍用旧配置跑到进程重启为止**。
正确写法见 `engines/ReviewEngine/utils/db.py:30`。

### 5.6 任务状态仅内存、最多留 5 条、无并发上限

`app/services/report_service.py:28-41`：

```python
MAX_TASK_HISTORY = 5
tasks_registry: dict[str, "ReportTask"] = {}
...
if len(tasks_registry) > MAX_TASK_HISTORY:
    oldest = sorted(...); for t in oldest[:-MAX_TASK_HISTORY]: tasks_registry.pop(t.task_id, None)
```

进程重启丢进行中任务；第 6 条之前的被静默裁剪；没有并发闸门，
多个报告任务会同时打满 LLM 配额（每个报告本身又要 32s DB + 大量 LLM 调用）。

### 5.7 若干健壮性缺口

| 位置 | 问题 |
|---|---|
| `app/routers/events.py:81` | 每连接创建**无上限** `Queue()`，转发失败被静默吞掉（第 55-61 行）→ 卡死的客户端可持续涨内存（对比 `report_service.py:140` 有 2000 上限） |
| `app/utils/retry_helper.py:41-50,228-233` + `engines/common/llm_client.py:184,323` | 重试元组以裸 `Exception` 收尾，永久性 4xx（错误 key、不支持 `tool_choice`）也会按 `LLM_RETRY_CONFIG` 重试，叠加内层循环最多 21 次调用，**每次重试都真实计费** |
| `app/services/search_service.py:147-153` | 每次引擎运行 `logger.add(...)` 却从不 `logger.remove` → 重复写同一份 `logs/<engine>.log`，fd 增长 |
| `app/services/search_service.py:62-83` | `POST /api/search` 无在途守卫，重复调用会并发起 3 个 LLM 线程，且 `cost_service.begin_run()` 覆盖 `_active_run` → 上一次的花费被算到新 run 上 |
| `app/services/report_service.py:205,210` | `open(...).read()` 未关闭（每份报告 2 个句柄），且外层 `except Exception` 静默换成 `""` → 报告劣化且无日志 |
| `app/routers/search.py:13-19`、`report.py:90-91` | 请求体只过 `await request.json()`；传 `[]` 或字符串 → `AttributeError` → 500 而非 400 |
| `engines/common/usage.py:56,490-496,605-621` | `_runs` 内存账本从不淘汰（每个 run 最多 500 条记录，直到进程结束）；`list_runs` 每次调用都重读全部 `data/usage/*.json` |
| `app/routers/report.py:39-40` | `_fail(str(e))` 把内部异常文本直接回给未鉴权的调用方，泄露路径/依赖信息 |
| `app/main.py:52-58` | 关闭钩子未 `dispose()` 数据库引擎与事件循环资源，与 1.5 的 aiomysql 告警同源 |
| `engines/ReviewEngine/utils/db.py:40-42,69-74` | 重建引擎时未 `await engine.dispose()` → 每次 reset 最多泄漏 15 个 MySQL 连接；且 `reset_engine()` 从未被 config 路由调用（与 5.5 是同一个"配置改了不生效"问题的两面） |
| `engines/ReportEngine/core/stitcher.py:73` | `datetime.utcnow()` 已废弃（测试有 DeprecationWarning） |
| `docker-compose.yaml:23-39`、`Dockerfile.backend` | 无 mem/CPU 限制、无日志大小上限、无 `stop_grace_period`，而 nginx 为 SSE 保持 3600s → `docker stop` 会 SIGKILL 到写一半；镜像无 `USER`（root 运行）；MySQL root 默认口令 `Atguigu.123` 且端口发布到宿主机 |

### 5.8 无 CI、无 LICENSE、评测未落地

- 无 `.github/`。README 承诺过 `ruff check` + `pytest -m "not integration"`，
  而测试已完全 mock 掉外部依赖 —— 但**当前 2 个失败用例恰恰需要真实 `data/` 目录**，
  上 CI 前必须先修掉 0.1，否则 CI 会红。
- 无顶层 `LICENSE`（`IMPROVEMENTS.md` 说明是有意为之）。移除爬虫模块后仓库内
  已无非商用约束，若打算公开分发补一份即可。
- `evals/` 下**只有 `DESIGN.md`**，没有实现；README 路线图里「单智能体 vs 多智能体」
  的量化对比仍缺。`scripts/experiment_sentiment_impact.py` 有 5 个子命令，
  但产物是一次性结果（`data/exp_sentiment/llm_vs_model.json`，n=150），不可回归。

---

## 六、`IMPROVEMENTS.md` 原有条目的复核结论

| 原条目 | 复核结果 |
|---|---|
| 1. 无打包配置，20 处 `sys.path` 注入 | **仍成立**，且已增至 24 处 |
| 2. 配置热更新不完全（15 处按值导入） | **仍成立**，14 处；已定位到具体功能后果（`/api/config` 改完不生效） |
| 3. 任务状态只在内存，最多 5 条 | **仍成立**（`report_service.py:28-41`） |
| 4. 无 CI | **仍成立**，但上 CI 前须先修本文 0.1 |
| 5. 顶层无 LICENSE | **仍成立**（有意为之） |
| 6. 评测未基准化 | **仍成立**，`evals/` 仍只有 DESIGN.md |

另外原清单开头的测试基线（`272 passed / 194s`）**已过期**，应更新为本文第零节的数据。

---

## 七、建议的推进顺序

**第一批（半天内可完成，风险低、收益立竿见影）**

1. `app/routers/config.py` 掩码密钥 + CORS 白名单 + 端口绑 `127.0.0.1`（第二节 2.1）
2. `ALTER TABLE review` 补 3 个索引 + 幂等迁移（第一节 1.2）→ 单次报告预期省下约 20s
3. `data_provider.collect` 复用商品检索结果（1.3）→ 再省约 6s
4. 修 `test_visualization.py` 的快照目录隔离与五档断言（0.1）→ 测试回到全绿
5. PDF 中文文件名改 RFC 5987（2.2）
6. 两个 SSE 改 `asyncio.Queue`（3.1）、PDF 导出挪出事件循环（3.2）

**第二批（1-2 天，测得出量化改善）**

7. ReportEngine e2e 用例 mock 掉 `ProductReviewDB`（0.2）→ 测试从 598s 回到 1 分钟内
8. 前端：SSE `id:` + `last_event_id` 续传、控制台虚拟化、Element Plus 按需引入、logo 压缩（第四节）
9. 修 `search_service.py` 的按值导入、`logger.remove`、在途守卫（5.5、5.7）
10. 修 `retry_helper` 只重试瞬时错误（5.7）—— 这条直接省钱
11. `usage._persist` 节流（3.3）

**第三批（结构性，需要专门排期）**

12. 三引擎节点去重（5.1）
13. `html_renderer` 拆分 + 渲染器共享 block 分派（5.2）
14. `pyproject.toml` + 清掉 24 处 `sys.path` 注入（5.4）
15. 商品表全文检索改造（1.4）
16. 任务状态落库 + 并发上限（5.6）
17. CI、`tools/SentimentAnalysisModel` 移出（5.3、5.8）

---

## 附：复核用的关键命令

```bash
# 基线
./project_venv/bin/pytest -q --durations=20

# 可视化 DB 耗时（32s）
./project_venv/bin/python -c "
from engines.ReportEngine.visualization.data_provider import VisualizationProvider
import time; t=time.time(); VisualizationProvider().collect('headphone'); print(time.time()-t)"

# 确认索引未被使用（Table scan on review, 4e6 rows）
./project_venv/bin/python -c "
import asyncio
from engines.ReviewEngine.utils.db import fetch_all
print(asyncio.run(fetch_all('EXPLAIN SELECT asin FROM review WHERE parent_asin IN (%s) AND rating<=2 ORDER BY helpful_vote DESC LIMIT 300', ('B00QSEE0SA',))))"

# 结构性问题
grep -rn "sys.path.insert\|sys.path.append" --include="*.py" app/ engines/ tests/ scripts/ tools/ecommerce/ | wc -l
grep -rn "from app.config import" --include="*.py" app/ engines/ scripts/ | grep -c settings
grep -rn "SentimentAnalysisModel" --include="*.py" app/ engines/ tests/ scripts/ tools/ecommerce/
```
