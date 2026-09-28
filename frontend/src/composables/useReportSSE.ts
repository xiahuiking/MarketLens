import { ref, onUnmounted } from 'vue'
import { useReportStore } from '@/stores/report'
import type { ReportTask } from '@/stores/report'
import { useAppsStore } from '@/stores/apps'

const REPORT_SSE_EVENTS = [
  'status', 'stage', 'chapter_status', 'chapter_chunk',
  'warning', 'error', 'debug', 'html_ready', 'completed',
  'log', 'cancelled', 'heartbeat', 'cost_update',
] as const

/** 报告任务的实时进度也要出现在右下角日志栏里 */
function relayReportLog(type: string, payload: Record<string, any>) {
  const appsStore = useAppsStore()
  const text = (value: unknown) => (typeof value === 'string' ? value : '')

  switch (type) {
    case 'stage': {
      const message = text(payload.message) || text(payload.stage) || text(payload.name)
      if (message) appsStore.appendStructuredLine('report', { source: 'report', text: `[阶段] ${message}` })
      break
    }
    case 'chapter_status': {
      const title = text(payload.title) || text(payload.chapterId) || '章节'
      const attempt = Number(payload.attempt) || 1
      if (payload.status === 'running') {
        appsStore.appendStructuredLine('report', { source: 'report', text: `开始撰写章节：${title}` })
      } else if (payload.status === 'completed') {
        appsStore.appendStructuredLine('report', {
          level: 'success',
          source: 'report',
          text: `章节完成：${title}${attempt > 1 ? `（第 ${attempt} 次尝试）` : ''}`,
          highlight: true,
        })
      } else if (payload.status === 'retrying' || payload.status === 'error') {
        appsStore.appendStructuredLine('report', {
          level: payload.status === 'error' ? 'error' : 'warning',
          source: 'report',
          text: `${payload.status === 'error' ? '章节生成失败' : '章节重试'}：${title} — ${text(payload.error) || text(payload.reason)}`,
          highlight: payload.status === 'error',
        })
      }
      break
    }
    case 'warning':
    case 'error': {
      const message = text(payload.message) || text(payload.error)
      if (message) {
        appsStore.appendStructuredLine('report', {
          level: type === 'error' ? 'error' : 'warning',
          source: 'report',
          text: message,
          highlight: type === 'error',
        })
      }
      break
    }
    case 'html_ready':
      appsStore.appendStructuredLine('report', {
        level: 'success',
        source: 'report',
        text: '报告已就绪，可以预览/下载',
        highlight: true,
      })
      break
    case 'completed':
      appsStore.appendStructuredLine('report', {
        level: 'success',
        source: 'report',
        text: '报告生成完成',
        highlight: true,
      })
      break
    case 'cancelled':
      appsStore.appendStructuredLine('report', {
        level: 'warning',
        source: 'report',
        text: '报告生成已取消',
        highlight: true,
      })
      break
    default:
      break
  }
}

export function useReportSSE() {
  const reportStore = useReportStore()
  const connected = ref(false)
  const lastEventId = ref<number>(0)
  let eventSource: EventSource | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let retryDelay = 3000

  function open(taskId: string) {
    close()

    // Append lastEventId header is handled via URL query or constructor option
    const url = `/api/report/stream/${taskId}`
    eventSource = new EventSource(url)

    REPORT_SSE_EVENTS.forEach((evt) => {
      eventSource!.addEventListener(evt, (event: MessageEvent) => {
        try {
          const data = JSON.parse(event.data)
          const payload = data.payload || data
          let task: ReportTask | undefined

          if (payload.task) {
            task = payload.task
          }

          reportStore.handleSSEEvent(evt, payload, task)
          relayReportLog(evt, payload)

          if (evt === 'completed' || evt === 'error' || evt === 'cancelled') {
            setTimeout(() => close(), 500)
          }

          connected.value = true
          lastEventId.value = data.id || lastEventId.value
        } catch {
          // skip unparseable events
        }
      })
    })

    eventSource.onopen = () => {
      connected.value = true
      retryDelay = 3000
      reportStore.streamStatus = 'connected'
    }

    eventSource.onerror = () => {
      connected.value = false
      reportStore.streamStatus = 'error'
      eventSource?.close()
      scheduleReconnect(taskId)
    }
  }

  function scheduleReconnect(taskId: string) {
    if (reconnectTimer) clearTimeout(reconnectTimer)
    reportStore.streamStatus = 'reconnecting'
    reconnectTimer = setTimeout(() => {
      retryDelay = Math.min(retryDelay * 2, 15000)
      open(taskId)
    }, retryDelay)
  }

  function close() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    if (eventSource) {
      eventSource.close()
      eventSource = null
    }
    connected.value = false
    reportStore.streamStatus = 'idle'
  }

  onUnmounted(() => close())

  return { connected, open, close }
}
