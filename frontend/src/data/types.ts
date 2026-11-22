/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

export type TraceEvent = {
  event: string
  stage: string
  receipt_id: string
  logged_at: string | null
  reason: string | null
  rows_count: number | null
}

export type TraceAttempt = {
  task_no: string
  attempt_no: number
  export_id: number
  api_name: string
  receipt_id: string
  query_params: string
  stages: string[]
  events: TraceEvent[]
  dispatched: boolean
  succeeded: boolean
  interrupted: boolean
  outcome: '派发' | '成功' | '中断'
  has_file: boolean
  artifact_file: string | null
  recoverable: boolean
  resume_request: {
    export_id: number
    task_no: string
    attempt_no: number
    receipt_id: string
    query_params: string
  }
  resumed_from_attempt: number | null
  resume_receipt: string | null
}

export type TraceTask = {
  task_no: string
  attempts: TraceAttempt[]
  dispatched_count: number
  succeeded_count: number
  interrupted_count: number
}

export type ExportTraceResult = {
  ok: boolean
  filter: { task_no: string | null; receipt_ids: string[] }
  task_count: number
  attempt_count: number
  tasks: TraceTask[]
  attempts: TraceAttempt[]
  unknown_receipts: string[]
}

export type ReissueResult = {
  ok: boolean
  message?: string
  reissued_from?: 'artifact' | 'recovered'
  artifact_file?: string
  rows_count?: number
  attempt_no?: number
  receipt_id?: string
  source_receipt_id?: string
  source_query_params?: string
  supplemental_inputs?: string[]
  missing_inputs?: boolean
}

export type ExportAttemptsResult = {
  ok: boolean
  export_id: number
  attempt_count: number
  attempts: TraceAttempt[]
}
