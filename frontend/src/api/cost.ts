import client from './client'

export interface CostBucket {
  calls: number
  failed_calls: number
  estimated_calls: number
  unpriced_calls: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cost: number
  duration_ms: number
}

export interface CostSummary {
  run_id: string
  query?: string
  source?: string
  active?: boolean
  started_at?: string | null
  updated_at?: string | null
  calls: number
  failed_calls: number
  estimated_calls: number
  unpriced_calls: number
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  cost: number
  currency: string
  duration_ms: number
  by_engine: Record<string, CostBucket>
  by_model: Record<string, CostBucket>
}

export interface CostRecord extends Omit<CostBucket, 'cost'> {
  call_id: string
  engine: string
  model: string
  method: string
  cost: number | null
  currency: string
  price_known: boolean
  matched_model: string | null
  tokens_known: boolean
  estimated: boolean
  priced: boolean
  billable: boolean
  caller: string
  base_url: string
  ok: boolean
  error: string
  timestamp: string
}

export function fetchCurrentCost() {
  return client.get('/api/cost/current')
}

export function fetchRunCost(runId: string, includeRecords: boolean = false) {
  return client.get(`/api/cost/run/${runId}`, { params: { include_records: includeRecords } })
}

export function fetchCostRuns(limit: number = 20) {
  return client.get('/api/cost/runs', { params: { limit } })
}

export function fetchPriceInfo() {
  return client.get('/api/cost/prices')
}
