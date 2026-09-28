<template>
  <div class="console-wrapper">
    <div class="console-toolbar">
      <span class="console-title">运行日志</span>
      <span class="console-count">{{ visibleLines.length }} / {{ total }}</span>
      <span class="console-spacer" />
      <el-button
        size="small"
        text
        :class="{ 'filter-on': keyOnly }"
        @click="keyOnly = !keyOnly"
        :title="keyOnly ? '显示全部日志' : '只看关键节点（检索命中、总结完成、章节完成、报告就绪、报错）'"
      >
        {{ keyOnly ? '全部' : '只看关键' }}
      </el-button>
      <el-button size="small" text @click="clearLog">清空</el-button>
    </div>
    <div class="console-output" ref="consoleRef">
      <div v-if="visibleLines.length === 0" class="console-empty">
        {{ keyOnly ? '暂无关键节点日志' : '暂无日志输出' }}
      </div>
      <div
        v-for="line in visibleLines"
        :key="line.key"
        class="console-line"
        :class="[`lv-${line.meta.level}`, { hl: line.meta.highlight }]"
      >
        <span class="console-ts">{{ line.meta.timestamp }}</span>
        <span class="console-source">{{ sourceLabel(line.meta.source) }}</span>
        <span class="console-text" :title="line.meta.title">{{ line.meta.text }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, watch, nextTick, ref } from 'vue'
import { useAppsStore, SOURCE_LABELS, parsePlainLine, type ConsoleLine } from '@/stores/apps'

const appsStore = useAppsStore()
const consoleRef = ref<HTMLElement | null>(null)
const keyOnly = ref(false)

interface DisplayLine {
  key: string
  text: string
  meta: ConsoleLine
}

const rawLines = computed(() => appsStore.logBuffers[appsStore.activeApp] || [])
const metaLines = computed(() => appsStore.logMeta[appsStore.activeApp] || [])
const total = computed(() => rawLines.value.length)

function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] || source || ''
}

const visibleLines = computed<DisplayLine[]>(() => {
  const result: DisplayLine[] = []
  rawLines.value.forEach((text, index) => {
    const meta = metaLines.value[index] || parsePlainLine(text)
    if (keyOnly.value && !meta.highlight) return
    result.push({ key: `${appsStore.activeApp}-${index}`, text, meta })
  })
  return result
})

function clearLog() {
  appsStore.clearLogBuffer(appsStore.activeApp)
}

// 切换 Agent 或过滤时重置过滤，避免用户以为日志丢了
watch(() => appsStore.activeApp, () => {
  keyOnly.value = false
})

// Auto-scroll to bottom on new lines
watch(
  () => [appsStore.activeApp, visibleLines.value.length] as const,
  async () => {
    await nextTick()
    if (consoleRef.value) {
      consoleRef.value.scrollTop = consoleRef.value.scrollHeight
    }
  },
)
</script>

<style scoped>
.console-wrapper {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.console-toolbar {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 2px 6px 2px 10px;
  background: #14142b;
  color: #888;
  font-size: 12px;
  flex-shrink: 0;
}
.console-title {
  font-weight: 600;
  color: #bbb;
}
.console-count {
  font-family: 'Courier New', monospace;
}
.console-spacer {
  flex: 1;
}
.console-toolbar :deep(.el-button) {
  color: #9aa;
}
.console-toolbar :deep(.filter-on) {
  color: #e6a23c;
  font-weight: 700;
}
.console-output {
  flex: 1;
  overflow-y: auto;
  background: #0c0c1d;
  color: #0f0;
  font-family: 'Courier New', monospace;
  font-size: 13px;
  padding: 8px;
  white-space: pre-wrap;
  word-break: break-word;
  position: relative;
}
.console-empty {
  color: #555;
  padding: 20px;
  text-align: center;
}
.console-line {
  display: flex;
  gap: 8px;
  min-height: 18px;
  line-height: 1.45;
  padding: 0 2px;
}
.console-ts {
  color: #556;
  flex-shrink: 0;
}
.console-source {
  color: #e6a23c;
  flex-shrink: 0;
  min-width: 76px;
}
.console-text {
  color: #9f9;
  white-space: pre-wrap;
}
/* 级别配色 */
.console-line.lv-debug .console-text { color: #667; }
.console-line.lv-info .console-text { color: #9f9; }
.console-line.lv-success .console-text { color: #67e28a; }
.console-line.lv-warning .console-text { color: #e6a23c; }
.console-line.lv-error .console-text { color: #ff6b6b; }
/* 关键节点高亮 */
.console-line.hl {
  background: rgba(230, 162, 60, 0.08);
  border-left: 2px solid #e6a23c;
}
.console-line.hl .console-text {
  font-weight: 700;
}
.console-line.lv-success.hl {
  border-left-color: #67c23a;
  background: rgba(103, 194, 58, 0.1);
}
.console-line.lv-error.hl {
  border-left-color: #f56c6c;
  background: rgba(245, 108, 108, 0.12);
}
</style>
