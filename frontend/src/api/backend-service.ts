import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type {
  ActionResult,
  EntryRow,
  ExportAttemptsResult,
  ExportTraceResult,
  OverviewResult,
  PageResult,
  ReissueResult,
} from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

// ========== 调用审计：导出调用明细检索与恢复补发 ==========

export async function traceExportCalls(params: {
  taskNo?: string
  receipts?: string
}): Promise<ExportTraceResult> {
  const query = new URLSearchParams()
  if (params.taskNo) query.set('task_no', params.taskNo)
  if (params.receipts) query.set('receipts', params.receipts)
  const response = await request(`/api/call_audit/export-trace?${query.toString()}`)
  if (!response.ok) throw new Error('调用明细检索失败')
  return await response.json()
}

export async function listExportAttempts(exportId: number): Promise<ExportAttemptsResult> {
  const response = await request(`/api/export_task/${exportId}/attempts`)
  if (!response.ok) throw new Error('执行尝试读取失败')
  return await response.json()
}

export async function recoverExport(
  exportId: number,
  payload: { attempt_no: number; receipt_id?: string },
): Promise<ReissueResult> {
  const response = await request(`/api/export_task/${exportId}/recover`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  return await response.json()
}

export async function reissueExport(
  exportId: number,
  payload: { attempt_no: number; receipt_id?: string; supplemental_inputs?: string[] },
): Promise<ReissueResult> {
  const response = await request(`/api/export_task/${exportId}/reissue`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  return await response.json()
}
