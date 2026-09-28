import { ref, onUnmounted } from 'vue'
import { useSystemStore } from '@/stores/system'
import { useAppsStore } from '@/stores/apps'
import { useSearchStore } from '@/stores/search'
import { useForumStore } from '@/stores/forum'

/** 把 agent 总结压成一行日志（完整内容留在 title 里悬停查看） */
function summaryLine(summary: string, limit = 160): string {
  const flat = (summary || '').replace(/\s+/g, ' ').trim()
  if (flat.length <= limit) return flat
  return flat.slice(0, limit) + '…'
}

export function useSSE() {
  const systemStore = useSystemStore()
  const appsStore = useAppsStore()
  const searchStore = useSearchStore()
  const forumStore = useForumStore()

  const connected = ref(false)
  let eventSource: EventSource | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let retryDelay = 1000

  function connect() {
    if (eventSource) eventSource.close()

    eventSource = new EventSource('/api/events/stream')

    eventSource.addEventListener('connected', () => {
      connected.value = true
      systemStore.connectionStatus = 'connected'
      retryDelay = 1000
    })

    eventSource.addEventListener('error', () => {
      connected.value = false
      systemStore.connectionStatus = 'disconnected'
    })

    eventSource.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        const eventType = msg.event
        const eventData = msg.data

        if (eventType === 'console_output') {
          const appName = eventData.app || eventData.app_name
          const line = eventData.line || eventData.output || eventData.message || ''
          if (appName && line) {
            appsStore.appendConsoleLine(appName, line)
          }
        } else if (eventType === 'console_log') {
          // 结构化日志：时间戳 + 级别 + 来源，直接落到对应 Agent 的日志栏
          const appName = eventData.app || 'report'
          appsStore.appendStructuredLine(appName, eventData)
        } else if (eventType === 'summary_ready') {
          // 每个 Agent 的阶段总结：让用户看到「得出了什么结论」
          const engine = eventData.source || 'review'
          const summary = eventData.summary || ''
          const round = eventData.type === 'reflection' ? '反思总结' : '首轮总结'
          if (summary.trim()) {
            appsStore.appendStructuredLine(engine, {
              level: 'success',
              source: engine,
              text: `${round}：${summaryLine(summary)}`,
              title: summary,
            })
          }
        } else if (eventType === 'engine_progress') {
          searchStore.handleEngineProgress(eventData)
          appsStore.appendStructuredLine(eventData.engine, {
            source: eventData.engine,
            text: eventData.message || '处理中',
          })
        } else if (eventType === 'engine_result') {
          searchStore.handleEngineResult(eventData)
          appsStore.appendStructuredLine(eventData.engine, {
            level: 'success',
            source: eventData.engine,
            text: '研究完成',
            highlight: true,
          })
        } else if (eventType === 'engine_error') {
          searchStore.handleEngineError(eventData)
          appsStore.appendStructuredLine(eventData.engine, {
            level: 'error',
            source: eventData.engine,
            text: `错误: ${eventData.error || '未知错误'}`,
            highlight: true,
          })
        } else if (eventType === 'forum_message') {
          forumStore.handleForumMessage(eventData)
        }
      } catch {
        // skip malformed events
      }
    }

    eventSource.onerror = () => {
      connected.value = false
      systemStore.connectionStatus = 'disconnected'
      eventSource?.close()
      scheduleReconnect()
    }
  }

  function scheduleReconnect() {
    if (reconnectTimer) clearTimeout(reconnectTimer)
    reconnectTimer = setTimeout(() => {
      retryDelay = Math.min(retryDelay * 2, 15000)
      connect()
    }, retryDelay)
  }

  function disconnect() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    if (eventSource) {
      eventSource.close()
      eventSource = null
    }
    connected.value = false
  }

  onUnmounted(() => disconnect())

  return { connected, connect, disconnect }
}
