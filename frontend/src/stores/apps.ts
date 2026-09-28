import { defineStore } from 'pinia'
import { ref, reactive } from 'vue'

export type ConsoleLevel = 'debug' | 'info' | 'success' | 'warning' | 'error'

export interface AppInfo {
  status: 'running' | 'stopped' | 'starting' | 'error'
  port: number
  outputLines: number
}

export interface ConsoleLine {
  timestamp: string
  source: string
  level: ConsoleLevel
  text: string
  highlight: boolean
  title: string
}

/** 事件来源 → 日志栏来源标签 */
export const SOURCE_LABELS: Record<string, string> = {
  review: '口碑 Agent',
  competitor: '竞品 Agent',
  trend: '趋势 Agent',
  forum: 'Forum',
  report: 'Report',
  system: 'System',
}

export function nowTimestamp(): string {
  return new Date().toLocaleTimeString('zh-CN', { hour12: false })
}

export function normalizeLevel(level?: string): ConsoleLevel {
  const value = (level || '').toLowerCase()
  if (value === 'debug' || value === 'success' || value === 'warning' || value === 'error') {
    return value
  }
  return 'info'
}

/** 结构化日志 → 单行文本：`[时间] [来源] 正文` */
export function formatConsoleLine(line: ConsoleLine): string {
  const ts = (line.timestamp || '').slice(0, 8)
  const source = SOURCE_LABELS[line.source] || line.source || ''
  const parts = [ts, source].filter(Boolean).map((p) => `[${p}]`)
  return `${parts.join(' ')} ${line.text}`.trim()
}

/**
 * 解析一行纯文本日志（后端 loguru 输出等），尽量还原时间与级别。
 * 支持：`[13:20:01] [口碑 Agent] 文本`、`13:20:01 | INFO | ...`、`WARNING ...`
 */
export function parsePlainLine(raw: string): ConsoleLine {
  const text = raw ?? ''
  let timestamp = nowTimestamp()
  let source = ''
  let level: ConsoleLevel = 'info'
  let body = text

  const bracket = body.match(/^\[(\d{2}:\d{2}:\d{2})\]\s*\[([^\]]*)\]\s*([\s\S]*)$/)
  if (bracket) {
    timestamp = bracket[1]
    source = bracket[2]
    body = bracket[3]
  } else {
    const pipe = body.match(/^(\d{2}:\d{2}:\d{2})\s*\|\s*([A-Z]+)\s*\|\s*([\s\S]*)$/)
    if (pipe) {
      timestamp = pipe[1]
      level = normalizeLevel(pipe[2])
      body = pipe[3]
    } else {
      const loose = body.match(/^(DEBUG|INFO|SUCCESS|WARNING|ERROR)\b[\s:|-]*([\s\S]*)$/)
      if (loose) {
        level = normalizeLevel(loose[1])
        body = loose[2]
      }
    }
  }

  return { timestamp, source, level, text: body, highlight: false, title: body }
}

export const useAppsStore = defineStore('apps', () => {
  const apps = reactive<Record<string, AppInfo>>({
    review: { status: 'stopped', port: 0, outputLines: 0 },
    competitor: { status: 'stopped', port: 0, outputLines: 0 },
    trend: { status: 'stopped', port: 0, outputLines: 0 },
    forum: { status: 'stopped', port: 0, outputLines: 0 },
    report: { status: 'stopped', port: 0, outputLines: 0 },
  })

  const logBuffers = reactive<Record<string, string[]>>({
    review: [],
    competitor: [],
    trend: [],
    forum: [],
    report: [],
  })

  /** 与 logBuffers 一一对应的结构化元信息（级别/关键节点/悬停全文本） */
  const logMeta = reactive<Record<string, ConsoleLine[]>>({
    review: [],
    competitor: [],
    trend: [],
    forum: [],
    report: [],
  })

  const MAX_LOG_LINES = 5000
  const activeApp = ref<string>('review')

  function updateAppStatus(name: string, status: 'running' | 'stopped' | 'starting' | 'error') {
    if (apps[name]) {
      apps[name].status = status
    }
  }

  function trim(appName: string) {
    if (logBuffers[appName] && logBuffers[appName].length > MAX_LOG_LINES) {
      logBuffers[appName] = logBuffers[appName].slice(-MAX_LOG_LINES)
      logMeta[appName] = logMeta[appName].slice(-MAX_LOG_LINES)
    }
    if (apps[appName]) {
      apps[appName].outputLines = logBuffers[appName]?.length || 0
    }
  }

  function appendConsoleLine(appName: string, line: string) {
    if (!logBuffers[appName]) return
    logBuffers[appName].push(line)
    logMeta[appName].push(parsePlainLine(line))
    trim(appName)
  }

  /** 追加一条带级别/来源的结构化日志（后端 console_log 事件） */
  function appendStructuredLine(
    appName: string,
    line: {
      timestamp?: string
      level?: string
      source?: string
      text?: string
      highlight?: boolean
      title?: string
    },
  ) {
    if (!logBuffers[appName]) return
    const text = (line.text || '').trim()
    if (!text) return

    const entry: ConsoleLine = {
      timestamp: (line.timestamp || nowTimestamp()).slice(0, 8),
      source: line.source || appName,
      level: normalizeLevel(line.level),
      text,
      highlight: Boolean(line.highlight),
      title: line.title || text,
    }
    logBuffers[appName].push(formatConsoleLine(entry))
    logMeta[appName].push(entry)
    trim(appName)
  }

  function clearLogBuffer(appName: string) {
    if (logBuffers[appName]) {
      logBuffers[appName] = []
      logMeta[appName] = []
      apps[appName].outputLines = 0
    }
  }

  function setActiveApp(appName: string) {
    activeApp.value = appName
  }

  return {
    apps, logBuffers, logMeta, activeApp,
    updateAppStatus, appendConsoleLine, appendStructuredLine, clearLogBuffer, setActiveApp,
  }
})
