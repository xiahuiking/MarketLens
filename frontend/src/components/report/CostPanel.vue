<template>
  <el-card class="cost-panel" shadow="never">
    <template #header>
      <div class="cost-header">
        <span class="cost-title">Token / 成本核算</span>
        <el-tag v-if="cost && isLive" size="small" type="warning" effect="dark">累计中</el-tag>
        <el-tag v-else-if="cost" size="small" type="info">已完成</el-tag>
        <span class="cost-spacer" />
        <span v-if="prices" class="cost-price-hint" :title="prices.note || ''">
          价目表：{{ prices.model_count }} 个模型 · {{ currencyLabel }}
        </span>
      </div>
    </template>

    <div v-if="!cost || !cost.calls" class="cost-empty">
      暂无 LLM 调用记录。发起搜索分析或生成报告后，这里会实时显示本次花费。
    </div>

    <template v-else>
      <div class="cost-totals">
        <div class="cost-total-item cost-total-primary">
          <div class="cost-total-label">本次成本</div>
          <div class="cost-total-value">{{ formatMoney(cost.cost, cost.currency) }}</div>
        </div>
        <div class="cost-total-item">
          <div class="cost-total-label">调用次数</div>
          <div class="cost-total-value">{{ cost.calls }}</div>
        </div>
        <div class="cost-total-item">
          <div class="cost-total-label">输入 tokens</div>
          <div class="cost-total-value">{{ formatInt(cost.prompt_tokens) }}</div>
        </div>
        <div class="cost-total-item">
          <div class="cost-total-label">输出 tokens</div>
          <div class="cost-total-value">{{ formatInt(cost.completion_tokens) }}</div>
        </div>
        <div class="cost-total-item">
          <div class="cost-total-label">LLM 耗时</div>
          <div class="cost-total-value">{{ formatDuration(cost.duration_ms) }}</div>
        </div>
      </div>

      <div class="cost-alerts">
        <el-alert
          v-if="cost.unpriced_calls > 0"
          type="warning"
          :closable="false"
          show-icon
          :title="`${cost.unpriced_calls} 次调用未命中价目表，成本未计入`"
          description="请在 engines/common/model_prices.json 补充该模型单价，或用 MODEL_PRICES_PATH 指向自定义价目表。"
        />
        <el-alert
          v-if="cost.estimated_calls > 0"
          type="info"
          :closable="false"
          show-icon
          :title="`${cost.estimated_calls} 次调用的 token 为估算值`"
          description="网关未返回 usage，已按字符数估算，金额仅供参考。"
        />
        <el-alert
          v-if="cost.failed_calls > 0"
          type="error"
          :closable="false"
          show-icon
          :title="`${cost.failed_calls} 次调用失败`"
          description="失败调用通常不计费，已从成本中排除。"
        />
      </div>

      <el-tabs v-model="activeTab" class="cost-tabs">
        <el-tab-pane label="按引擎" name="engine">
          <el-table :data="engineRows" size="small" :show-header="true" empty-text="暂无数据">
            <el-table-column prop="label" label="引擎" min-width="110" />
            <el-table-column prop="calls" label="调用" width="70" />
            <el-table-column label="输入 tok" width="100" align="right">
              <template #default="{ row }">{{ formatInt(row.prompt_tokens) }}</template>
            </el-table-column>
            <el-table-column label="输出 tok" width="100" align="right">
              <template #default="{ row }">{{ formatInt(row.completion_tokens) }}</template>
            </el-table-column>
            <el-table-column label="成本" width="110" align="right">
              <template #default="{ row }">{{ formatMoney(row.cost, currency) }}</template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
        <el-tab-pane label="按模型" name="model">
          <el-table :data="modelRows" size="small" :show-header="true" empty-text="暂无数据">
            <el-table-column prop="label" label="模型" min-width="140" />
            <el-table-column prop="calls" label="调用" width="70" />
            <el-table-column label="输入 tok" width="100" align="right">
              <template #default="{ row }">{{ formatInt(row.prompt_tokens) }}</template>
            </el-table-column>
            <el-table-column label="输出 tok" width="100" align="right">
              <template #default="{ row }">{{ formatInt(row.completion_tokens) }}</template>
            </el-table-column>
            <el-table-column label="成本" width="110" align="right">
              <template #default="{ row }">{{ formatMoney(row.cost, currency) }}</template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>

      <div class="cost-footnote">
        金额为估算：单价按每百万 token 计，可在
        <code>engines/common/model_prices.json</code> 中按实际账单调整；未返回 usage
        的调用按字符数估算（已标注）。
      </div>
    </template>
  </el-card>
</template>

<script setup lang="ts">
import { computed, onActivated, onDeactivated, onMounted, onUnmounted, ref } from 'vue'
import { useReportStore } from '@/stores/report'
import {
  fetchCurrentCost,
  fetchPriceInfo,
  fetchRunCost,
  type CostBucket,
  type CostSummary,
} from '@/api/cost'

const reportStore = useReportStore()

const currentRun = ref<CostSummary | null>(null)
const taskRun = ref<CostSummary | null>(null)
const prices = ref<Record<string, any> | null>(null)
const activeTab = ref<'engine' | 'model'>('engine')

let pollTimer: ReturnType<typeof setInterval> | null = null

const ENGINE_LABELS: Record<string, string> = {
  ReviewEngine: '口碑 Agent',
  CompetitorEngine: '竞品 Agent',
  TrendEngine: '趋势 Agent',
  ReportEngine: '报告 Agent',
  ForumEngine: '论坛主持人',
  Engine: '未标注引擎',
}

function engineLabel(name: string): string {
  if (ENGINE_LABELS[name]) return ENGINE_LABELS[name]
  if (name.startsWith('ReportEngine:')) {
    return `报告 Agent（${name.split(':').slice(1).join(':')}）`
  }
  return name
}

// 三个数据源，优先级从高到低：
//   1. 后端「当前 run」且它比页面上的任务快照更新 → 新一轮分析已开始
//   2. 任务自带的 cost（报告生成期间由 SSE 实时推送，最及时）
//   3. 按任务 run_id 拉取的落盘汇总（刷新/重启后仍可对账）
const cost = computed<CostSummary | null>(() => {
  const taskCost = reportStore.currentTask?.cost
  const live = currentRun.value
  if (live?.active && live.run_id && live.run_id !== taskCost?.run_id) return live
  if (taskCost && (taskCost.calls || taskCost.run_id)) return taskCost
  return taskRun.value || live
})

const currency = computed(() => cost.value?.currency || 'CNY')
const currencyLabel = computed(() => (currency.value === 'USD' ? '美元 USD' : '人民币 CNY'))

const isLive = computed(() => {
  const status = reportStore.currentTask?.status
  return Boolean(cost.value?.active) || status === 'running' || status === 'pending'
})

function toRows(group: Record<string, CostBucket> | undefined, labeler: (name: string) => string) {
  return Object.entries(group || {})
    .map(([name, bucket]) => ({ name, label: labeler(name), ...bucket }))
    .sort((a, b) => (b.cost || 0) - (a.cost || 0) || b.calls - a.calls)
}

const engineRows = computed(() => toRows(cost.value?.by_engine, engineLabel))
const modelRows = computed(() => toRows(cost.value?.by_model, (name) => name))

function formatInt(value: number | undefined | null): string {
  return (value || 0).toLocaleString('zh-CN')
}

function formatMoney(value: number | undefined | null, cur: string = 'CNY'): string {
  const symbol = cur === 'USD' ? '$' : '¥'
  const amount = value || 0
  // 单次报告成本通常很小，保留 4 位小数才有区分度
  const digits = Math.abs(amount) > 0 && Math.abs(amount) < 1 ? 4 : 2
  return `${symbol}${amount.toFixed(digits)}`
}

function formatDuration(ms: number | undefined | null): string {
  const seconds = (ms || 0) / 1000
  if (seconds < 60) return `${seconds.toFixed(1)}s`
  const minutes = Math.floor(seconds / 60)
  return `${minutes}m${Math.round(seconds - minutes * 60)}s`
}

async function refresh() {
  const task = reportStore.currentTask

  // 始终查一次「当前 run」：这样上一份报告完成后新开的分析也能立刻反映出来
  try {
    const res = await fetchCurrentCost()
    if (res.data?.success) currentRun.value = res.data.cost || null
  } catch {
    // 核算不可用时静默降级，不影响报告页其它功能
  }

  if (!task?.run_id) {
    taskRun.value = null
    return
  }
  try {
    const res = await fetchRunCost(task.run_id)
    if (res.data?.success && res.data.cost) taskRun.value = res.data.cost
  } catch {
    // ignore
  }
}

async function loadPrices() {
  try {
    const res = await fetchPriceInfo()
    if (res.data?.success) prices.value = res.data.prices
  } catch {
    // ignore
  }
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer)
  pollTimer = null
}

function startPolling() {
  stopPolling()
  refresh()
  pollTimer = setInterval(refresh, 5000)
}

onMounted(() => {
  loadPrices()
  startPolling()
})

// ReportTab 被 KeepAlive 缓存：切走时暂停轮询，切回时立即刷新
onActivated(startPolling)
onDeactivated(stopPolling)
onUnmounted(stopPolling)
</script>

<style scoped>
.cost-panel {
  margin-top: 12px;
}
.cost-header {
  display: flex;
  align-items: center;
  gap: 8px;
}
.cost-title {
  font-weight: 600;
  font-size: 14px;
}
.cost-spacer {
  flex: 1;
}
.cost-price-hint {
  color: #909399;
  font-size: 12px;
}
.cost-empty {
  color: #909399;
  font-size: 13px;
  padding: 8px 0;
}
.cost-totals {
  display: flex;
  flex-wrap: wrap;
  gap: 24px;
  padding: 4px 0 12px;
}
.cost-total-item {
  min-width: 96px;
}
.cost-total-label {
  color: #909399;
  font-size: 12px;
  margin-bottom: 2px;
}
.cost-total-value {
  font-size: 18px;
  font-weight: 600;
  font-family: monospace;
  color: #303133;
}
.cost-total-primary .cost-total-value {
  color: #e6a23c;
}
.cost-alerts {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 8px;
}
.cost-tabs {
  margin-top: 4px;
}
.cost-footnote {
  color: #909399;
  font-size: 12px;
  line-height: 1.6;
  border-top: 1px solid #ebeef5;
  padding-top: 8px;
}
.cost-footnote code {
  background: #f5f7fa;
  padding: 0 4px;
  border-radius: 3px;
  font-size: 11px;
}
</style>
