# MarketLens 多智能体 Benchmark 设计

> 目标：把「三个 Agent 比一个 Agent 好」「主持人比自由发挥好」这两个**叙事**，变成可证伪、可复现、可量化的**结论**。
>
> 本文件是设计文档，不含实现。实现阶段见第 9 节。

---

## 0. 要回答的问题

三个问题，按重要性排序。每一个都写明了「什么结果会推翻它」。

| # | 问题 | 推翻条件 |
|---|---|---|
| **Q1** | 分工成三个 Agent，比合并成一个 Agent 更好吗？ | 在**算力匹配**下，单 Agent 的覆盖率不劣于三 Agent |
| **Q2** | 三个 Agent 里，**每一个**都有正贡献吗？ | 某个 Agent 的 Shapley 值 95% CI 上界 ≤ 0 |
| **Q3** | 主持人（ForumEngine）带来了什么？ | 有主持人 ≯ 无主持人；或收益完全来自「多一次 LLM 调用」而非「引导内容」 |

**Q2 是本 benchmark 的核心**。因为 CompetitorEngine 与 TrendEngine 在代码上高度同构（`state.py` 字段逐字段相同、`graph.py` 拓扑逐节点相同、`context.py` 的 dispatch 表逐键相同、Trend 直接 import 竞品的 `TavilySearchWrapper`），Trend 的边际贡献是最可疑的一环。**如果测出来它不贡献，正确的结论是删掉它。**

---

## 1. 形式化

设 Agent 集合 `N = {R, C, T}`（review / competitor / trend）。

**价值函数**是一个集合函数：

```
V : 2^N → ℝ^m
```

`V(S)` = 只启用 `S` 中的 Agent 时，最终交付物（ReportEngine 报告）的质量向量。`m > 1`，因为「质量」不是标量（见第 5 节）。

**边际贡献**：

```
Δ_i(S) = V(S ∪ {i}) − V(S),    i ∉ S
```

> ⚠️ `Δ_i(S)` 依赖 `S`。**边际贡献是集合函数，不是数**。任何单一数字都必须声明其定义域。

**Shapley 值**（`n = 3` 时有精确解，无需采样近似）：

```
φ_i = Σ_{S ⊆ N\{i}}  [ |S|! (n−|S|−1)! / n! ] · Δ_i(S)
```

展开为可手算形式：

```
φ_i = (1/3)·Δ_i(∅)  +  (1/6)·[Δ_i({j}) + Δ_i({k})]  +  (1/3)·Δ_i({j,k})
```

含义：在**所有加入顺序**下边际贡献的平均，消除了顺序偏差。

**交互效应**（「分工协同」的量化）：

```
Syn(i,j) = V({i,j}) − V({i}) − V({j})          # >0 互补，<0 冗余
Syn(i,j,k) = V({i,j,k}) − ΣV({i}) − ΣSyn(i,j)  # 三体协同
```

**预期的关键读数**：

- `Syn(R,C) > 0` → 私有评论库与公开网页检索确实互补。**这是「多 Agent 有必要」最硬的证据。**
- `Syn(C,T) ≈ 0` → C 与 T 冗余（代码同构预判）。

---

## 2. 三个前提（不满足则所有数字无意义）

### 2.1 下游必须冻结

所有臂用**同一个** ReportEngine、同模型、同模板、同温度。否则测到的是报告生成器的随机性，而非证据的贡献。

实现上无需改代码：`generate_report(query, reports=[...])` 接受任意长度的 `List[Any]`（`engines/ReportEngine/agent.py:23,108`），直接传 1/2/3 份报告即可。

**不要走 `app/services/report_service.py`** —— 它的 `check_engines_ready()` 从固定目录读「最新」的 .md，会串味。

### 2.2 引擎必须互相独立（最关键）

当前存在一条 host 回灌回路：

```
引擎 summary 节点 → publish(SUMMARY_READY)   (initial_summary.py:80 / reflection_summary.py:72)
  → ForumEventHandler 缓冲，≥5 条触发          (handler.py:82)
    → HOST 生成发言 → publish(FORUM_MESSAGE, type=host)
      → forum_reader 缓存"最新一条"            (forum_reader.py:14,21)
        → 下一个 summary 节点读出并前置进 prompt (initial_summary.py:60 / reflection_summary.py:53)
```

**这条回路破坏了独立性**：去掉 Trend 会改变 host 发言，host 发言又改变 Review 的下一轮总结。于是

```
V({R,C}) 里的 Review  ≠  V({R,C,T}) 里的 Review
```

`V` 不再是干净的集合函数，**边际贡献的数学定义失效**。

**因此：子集族实验一律在 `host = off`（纯拼接）下进行**，让三个引擎的输出可复用、贡献可加。host 效应作为**独立因子**单独测（第 3.3 节）。这是因子分离，不是妥协——它还让实验便宜一个数量级（第 8.4 节）。

### 2.3 答案键必须带通道标签

每条必需发现（required finding）标注 `channel_policy ∈ {only-X, X-or-Y, any}`。这样才能**先预测** leave-one-out 的结果、再与实测比对，用于验证答案键自身的质量。

---

## 3. 实验设计

### 3.1 因子表

| 因子 | 水平 | 说明 |
|---|---|---|
| **子集** `S ⊆ {R,C,T}` | 7 个非空子集 | 边际贡献 / Shapley 的数据来源 |
| **架构** | `single_merged` / `single_selfreflect` | 单 Agent 基线（见 3.4 公平性） |
| **host 模式** | `off` / `synth_only` / `full` / `placebo` | 仅在全集 `{R,C,T}` 上测（3.3） |
| **重复** `R` | ≥ 5 | 应对 LLM 随机性 |

### 3.2 臂清单（共 13 个配置）

**A. 子集族（host = off，7 个）** —— 引擎输出可复用

```
{R}  {C}  {T}  {R,C}  {R,T}  {C,T}  {R,C,T}
```

**B. 架构族（2 个）** —— 算力匹配的单 Agent 基线

```
single_merged        一个 Agent，合并 12 个工具（7 SQL + 5 检索），同构控制流
single_selfreflect   single_merged + 多轮自我反思，用于把算力预算花完
```

**C. host 族（仅 {R,C,T}，4 个）**

```
host_off        三份报告直接拼接，无 host
host_synth_only host 只写 forum.log 供 ReportEngine 使用，**不回灌**给引擎
host_full       现系统（回灌 + 摘要）
host_placebo    host 照常调用 LLM、消耗同等算力，但被要求只输出中性套话、禁止指出矛盾
```

`host_placebo` 是**安慰剂对照**：它与 `host_full` 算力完全相同，唯一差别是提示词内容。`host_full − host_placebo` 才能回答「收益来自多一次 LLM 调用，还是来自『请指出矛盾』这条指令」——这正是 Q3 的精确形式。

### 3.3 为什么 host 只在全集上测

`7 子集 × 4 host 模式 = 28` 跑不起，而且没必要：子集族要的是**可加性**（必须 host off），host 族要的是**引导效应**（只能在全集上看）。两者目的不同，强行交叉只会把预算摊薄。

### 3.4 公平性约束（必须写进实验记录）

每一条都是为了避免「稻草人基线」：

1. **同模型**。当前项目按 Agent 配了 6 组模型三元组（`app/config.py`）。benchmark 里必须**全部钉死为同一模型**，否则模型质量与「分工」混杂。
2. **算力匹配**。`single_merged` 与 `single_selfreflect` 的 token 预算 = `{R,C,T}` 三引擎合计的均值，并允许它用更多反思轮次把预算花完。
   **未匹配的对照也要跑**（单 Agent 只拿 1/3 预算），用于展示「不匹配时会得出什么错误结论」。
3. **同等提示词工程投入**。`single_merged` 的提示词必须与三个专用提示词同等用力打磨，并记录迭代次数。基线写得潦草 = 结论无效。
4. **同标签预算**。`MAX_REFLECTIONS`、段落数上限、搜索调用上限全部对齐。
5. **同等终止策略**。host 族的轮次上限一致。

### 3.5 双预算报告（最有力的陈述）

不满足于「固定预算下谁更好」，而报**质量–算力曲线**：

```
对每个臂，扫 B ∈ {0.5×, 1×, 2×} 的 token 预算，画 V vs. B
```

若三 Agent 的曲线在每个 B 上都在单 Agent 之上 → **frontier dominance**。这比「花 3 倍算力换更好结果」强得多，也是唯一能顶住「你只是花得更多」这类质疑的陈述。

---

## 4. 数据集

### 4.1 分层

固定 query 集按**通道结构**分层，因为两个假设的区分度完全来自分层：

| 层 | 构造 | 规模 | 测什么 |
|---|---|---|---|
| **S1 单通道充分** | 只靠评论库就能答好的商品（DB 评论充足，如 top 商品 400–840 条） | 5 | **阴性对照** |
| **S2 双通道互补** | 评论 + 竞品各占一半信息 | 5 | 分工的下界增益 |
| **S3 三通道必须融合** | 任缺其一都答不全 | 5 | 分工的上界增益 |
| **S4 通道冲突** | 评论评价与竞品/趋势数据矛盾 | 5 | 交互效应与冲突发现 |
| **S5 证据缺失** | DB 无该商品 / 搜索无结果 | 3 | 弃权正确性、抗幻觉 |

**S1 是整个 benchmark 的证伪装置**：如果多 Agent 在「单通道就够了」的任务上也显著更强，说明指标测的是混杂变量（输出更长、更自信、结构更漂亮），不是分工——整份评测的效度不成立。

### 4.2 数据来源

基于真实数据构造（当前库：112,590 商品 / 246,383 评论）：

- **S1/S2/S3/S4**：按评论量分桶抽样商品。`SELECT parent_asin, COUNT(*) n FROM review GROUP BY parent_asin` 的分位点选样本。
- **S3/S4**：需人工筛选「评论与网页信息不一致」的商品，这一步无法自动化，是数据集工作量的重心。
- **S5**：用库中不存在的 ASIN + 无搜索结果的长尾品类词。

### 4.3 答案键 schema

```yaml
- id: s3-headphones-001
  stratum: S3
  query: "无线降噪耳机"
  entity: { type: product, asin: B085BB7B1M }

  required_findings:
    - id: f1
      statement: "平均评分落在 4.0–4.4"
      channel_policy: only-review          # only-X | X-or-Y | any
      verify:
        kind: db_query
        sql: "SELECT AVG(rating) FROM review WHERE parent_asin='B085BB7B1M'"
        tolerance: 0.2

    - id: f2
      statement: "负面评价集中在『续航』方面"
      channel_policy: only-review
      verify: { kind: db_query, sql: "...", tolerance: 0 }

    - id: f3
      statement: "该价格带主流竞品区间为 50–90 美元"
      channel_policy: only-competitor
      verify:
        kind: url_set
        urls: ["https://...", "https://..."]
        must_contain: ["$"]

  conflicts:                                # 仅 S4
    - id: c1
      between: [review, competitor]
      description: "评论对降噪评价高，竞品对比指出该型号降噪弱于同价位"
      expected_direction: "review > competitor"

  expect: report                            # report | abstain（S5 用 abstain）
```

`verify` 的两种形式对应两类可回查证据：`db_query` 重跑 SQL 比对数值；`url_set` 回查 URL 存在且含指定实体。

---

## 5. 指标

### 5.1 主指标（客观，不依赖 judge）

| 指标 | 定义 |
|---|---|
| `coverage` | 命中的必需发现 / 必需发现总数 |
| `coverage_by_channel` | 按通道分解的覆盖率 |
| `citation_verifiability` | 通过回查的引用 / 有引用支撑的断言 |
| `attribution_accuracy` | 被归到正确通道的断言 / 被归因的断言 |
| `conflict_recall` | 识别出的真实冲突 / 真实冲突总数（S4） |
| `conflict_precision` | 正确识别 / 声称识别的冲突（S4） |
| `abstention_correctness` | 正确弃权率（S5） |
| `unsupported_claim_rate` | 无证据支撑的断言 / 断言总数（幻觉率） |
| `redundancy` | `1 − 去重句数 / 总句数`（衡量讨论臂的「绕圈」） |

### 5.2 归因指标（证据账本）

边际贡献要可归因，必须建立可追溯链：

```
最终报告的每条断言 → 支撑它的证据项 → 来源通道
```

复用 `app/services/search_service.py:248 _extract_citations_from_result` 已有的引用结构（含 `query` / `url` / `content` / `paragraph_index`）。

由此得到两个层次的边际贡献：

| 层次 | 定义 | 成本 |
|---|---|---|
| **廉价代理** | `MC_unique(X)` = 只有 X 的证据能支撑的必需发现数 | 不用跑子集 |
| **真值** | leave-one-out 实测 `Δ` | 要跑子集 |

**两者之差本身是有效指标**：它衡量「LLM 能否用别的通道替代 X 的证据」（可替代性）。差值大 = X 的证据只是恰好被用了，并非不可替代。

### 5.3 成本指标

```
tokens_in / tokens_out / USD / wall_ms / llm_calls / search_calls
```

并报**边际贡献率**：

```
MCE_i = φ_i / E[ΔTokens_i]        （每千 token 的边际质量增益）
```

只报 `ΔV` 会得出「什么都加上」的结论；`MCE` 才是决策依据。

### 5.4 Judge 指标（补充，非唯一证据）

仅用于客观指标覆盖不到的部分：**融合质量**（是否真正cross-reference 了多通道证据）、**洞见深度**。

四个必须的对策：

1. **自评偏差** → judge 模型 ≠ 任何被测臂的模型，尽量跨厂商。
2. **长度/结构偏差** → host 输出天生结构漂亮（四段式），judge 极易奖励排版。rubric 用**原子二元项**，不用 1–10 分；prompt 明确要求忽略篇幅与排版。
3. **位置偏差** → 成对比较交换 A/B 位置各跑一次，只采信两次一致的判定。
4. **无校准** → 留 30–50 条人工标注作为校准集，**报告 judge–human 一致率（Cohen's κ）**。κ 不达标则 judge 结果不予采信。

### 5.5 分层报告，绝不聚合

Trend 可能在 S1/S2 贡献 0，但在 S3/S4 是唯一信源。**全局平均会误杀它。**

必须报 `MC(agent | stratum)`：

| | S1 | S2 | S3 | S4 | S5 |
|---|---|---|---|---|---|
| `φ_R` | | | | | |
| `φ_C` | | | | | |
| `φ_T` | | | | | |

---

## 6. 判定规则（预注册）

**在跑数据之前写下**，避免看到结果再挑指标：

| 假设 | 判定 |
|---|---|
| H1 分工有效 | `φ_R` 与 `φ_C` 的 95% CI 下界 > 0，**且** `V(RCT) > V(single_merged)` 在匹配预算与各分层上成立 |
| H2 Trend 有贡献 | `φ_T` 的 95% CI 下界 > 0。**否则建议删除 TrendEngine** |
| H3 C 与 T 冗余 | `Syn(C,T)` 的 95% CI 包含 0 |
| H4 主持人有内容价值 | `host_full > host_placebo`（即收益不是来自多一次调用） |
| H5 主持人有引导价值 | `host_synth_only > host_off`（回灌是否有效） |

**允许的结论包括「这个 Agent 应该删掉」。** 一个能得出否定结论的 benchmark 才有价值。

---

## 7. 统计

- 每格 **`R ≥ 5` 次重复**，**按 query 配对**比较（同 query 跨臂）。
- 报 **bootstrap 置信区间**或 Wilcoxon 符号秩检验，**报分布与效应量，不报单点**。
- 随机性来源要**先量化**：`forum_reader` 的全局单槽缓存使「哪个节点读到哪版 host 发言」依赖线程交错，同一 query 两次跑控制流都不同。因此 host 族必须记录 `host_trace`（见 8.5），并在报告中给出控制流方差。
- 对 judge 指标额外报 κ。

---

## 8. 实现架构

### 8.1 目录

```
evals/
  DESIGN.md            本文件
  datasets/            固定 query 集 + 答案键（YAML）
  arms/                各臂实现，契约统一
  metrics/             客观指标，纯函数
  judges/              judge prompt + 校准集
  runner.py            跑批、缓存、重放
  report.py            对比表 + 质量–算力曲线
  workspace.py         路径隔离
```

### 8.2 核心契约

所有臂必须吐出同一种记录，否则指标层无法复用：

```python
@dataclass
class EngineRun:
    query_id: str
    engine: str                 # review | competitor | trend
    repeat: int
    model: str                  # 钉死的模型名
    final_report: str
    citations: list[dict]       # 复用 _extract_citations_from_result 结构
    paragraphs: list[dict]
    tokens_in: int
    tokens_out: int
    wall_ms: int
    search_calls: int
    error: str | None

@dataclass
class ArmRun:
    query_id: str
    arm: str                    # single_merged | R | RC | RCT | ...
    host_mode: str              # off | synth_only | full | placebo
    repeat: int
    engines: list[str]
    forum_log: str
    final_report_md: str        # ReportEngine 输出
    document_ir: dict
    tokens_in: int
    tokens_out: int
    wall_ms: int
    host_trace: list[dict]      # [(node, host_speech_id)]
```

### 8.3 路径隔离（必须做，否则数据被污染）

`report_service.py:138-143` 用的是**固定相对路径**（`data/report/{review,competitor,trend}`、`logs/forum.log`）。而 `tests/test_sentiment_pipeline.py:351` 会往同一个 `logs/forum.log` 写占位内容（已实测发生）。

**每个 run 必须有独立的 workspace**：

```
data/evals/<run_id>/
  config.json          实验配置快照（模型、预算、参数、git commit）
  cache/               引擎输出缓存
  runs.jsonl           每个 ArmRun 一行
  engine_runs.jsonl    每个 EngineRun 一行
  metrics.jsonl        每个 ArmRun 的指标
  report.md            对比表 + 曲线
```

启动时校验 workspace 为空或显式 `--resume`，并在 `config.json` 里记录 `git rev-parse HEAD` 与 `.env` 相关键的**哈希**（不记值）。

### 8.4 缓存与回放

- **引擎输出按 `(query_id, engine, repeat, model)` 缓存**。有了 2.2 的独立性，8 个子集只需 3 次引擎实跑 + 7 次廉价的融合与下游。**成本从 8× 降到 ~1×。**
- **LLM / 搜索调用按 prompt hash 录制回放**。迭代指标时不烧钱，同时把线程竞态冻结为确定性。
- 默认走 fixture；真实调用由 `--live` 显式开启，并打 `@pytest.mark.integration`。

### 8.5 host trace

当前「哪个节点读到了哪版 host 发言」是 `forum_reader` 全局单槽里的隐式状态——既不可复现也不可分析。

改造：给 host 发言加自增版本号，`get_latest_host_speech()` 增加返回 `(speech, version)` 的接口，让每个 summary 节点记录 `(node_name, host_version)`。

这是**所有后续测量的前提**，且无论 benchmark 最终如何设计，这个改动都让系统更可观测。

### 8.6 跑批入口

```bash
# 冒烟：2 个 query，7 子集，fixture 回放
./project_venv/bin/python -m evals.runner --dataset datasets/smoke.yaml --arms subsets

# 正式：全分层，匹配预算
./project_venv/bin/python -m evals.runner --dataset datasets/v1.yaml --arms all --repeat 5

# 只算指标（复用缓存，不烧钱）
./project_venv/bin/python -m evals.runner --run-id <id> --metrics-only

# 生成报告
./project_venv/bin/python -m evals.report --run-id <id>
```

---

## 9. 执行阶段

| 阶段 | 内容 | 产出 |
|---|---|---|
| **P0 前提** | host 回灌开关 + 版本号 trace；workspace 路径隔离；下游冻结封装 | 可复现的骨架 |
| **P1 数据** | 5 层 × 23 query 的 v0 数据集 + 答案键 | `datasets/v1.yaml` |
| **P2 子集族** | 7 子集 + 2 单 Agent 臂，客观指标，Shapley 与交互效应 | 第一版结论（Q1/Q2） |
| **P3 host 族** | 4 种 host 模式 | Q3 结论 |
| **P4 judge** | rubric + 人工校准集 + κ | 补充分数 |
| **P5 报告** | 质量–算力曲线、分层矩阵、结论与建议 | 可交付报告 |

**P0 必须先做**：它的三个改动是后面所有测量的前提，且越晚做越痛（尤其 host trace，涉及三个引擎的 summary 节点）。

**建议的第一份结论**：只做 P0+P1+P2 的 S1/S2 层。因为 S1 是证伪装置——如果连 S1 都测出「多 Agent 更好」，说明指标有偏，必须停下来先修指标，不必浪费后续算力。

---

## 10. 效度威胁

| 威胁 | 后果 | 对策 |
|---|---|---|
| **算力不匹配** | Q1 变成同义反复 | 匹配预算 + 双预算报告（3.4 / 3.5） |
| **基线写得潦草** | Q1 假阳性 | 同等提示词工程投入 + 记录迭代次数（3.4） |
| **在回灌开启时测子集** | `V` 不是集合函数，数字全废 | 子集族一律 host=off（2.2） |
| **下游未冻结** | 测到报告生成器噪声 | 同模型同参数，直接调 `generate_report`（2.1） |
| **聚合报告** | 误杀只在特定分层有价值的 Agent | 分层矩阵（5.5） |
| **judge 当唯一证据** | 测出篇幅与排版偏好 | 客观指标打底（5.1）+ 四对策（5.4） |
| **答案键主观** | 全部指标平移 | 通道标签 + 先预测后实测比对（2.3） |
| **单次运行下结论** | 噪声当信号 | R ≥ 5 + 配对 + bootstrap CI（第 7 节） |
| **测试/在线运行污染 workspace** | 数据串味 | 路径隔离（8.3） |
| **模型不一致** | 模型质量与分工混杂 | 全臂钉死同一模型（3.4） |

---

## 附：本设计依赖的代码事实

| 事实 | 位置 |
|---|---|
| `reports` 是普通 list，子集实验无需改 ReportEngine | `engines/ReportEngine/agent.py:23,108` |
| host 回灌回路（破坏独立性） | `forum_reader.py:14,21,43` → `initial_summary.py:60` / `reflection_summary.py:53` |
| host 触发条件（≥5 条缓冲） | `engines/ForumEngine/handler.py:82,112,117,130` |
| 主持人提示词中的「指出矛盾」指令 | `engines/ForumEngine/llm_host.py:177` |
| 三引擎并行启动（硬编码三个） | `app/services/search_service.py:37` |
| 引用结构可直接复用 | `app/services/search_service.py:248` |
| C 与 T 同构：state / graph / context / 工具类复用 | `CompetitorEngine/state.py` vs `TrendEngine/state.py`；`search_service.py:224` |
| TrendContext 自述「通过提示词实现角色差异化」 | `engines/TrendEngine/context.py:3-4` |
| 固定相对路径（污染风险） | `app/services/report_service.py:138-143` |
| 测试写入共享 forum.log | `tests/test_sentiment_pipeline.py:351` |
| 数据规模（112,590 商品 / 246,383 评论） | 实测 `SELECT COUNT(*)` |
