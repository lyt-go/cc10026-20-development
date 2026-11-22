<template>
  <section class="trace-panel" data-testid="export-trace-panel">
    <header class="trace-head">
      <div>
        <h3>导出调用明细检索</h3>
        <p class="trace-hint">
          按任务号或执行回执找回各次执行尝试，同任务号的派发 / 成功 / 中断分别成组，不会合并成一次成功。
        </p>
      </div>
    </header>

    <form class="trace-query" @submit.prevent="runTrace">
      <label class="trace-field">
        <span>任务编号</span>
        <input
          v-model="taskNo"
          data-testid="trace-task-no"
          placeholder="如 EXP-E2E-A"
          :disabled="Boolean(initialTaskNo)"
        />
      </label>
      <label class="trace-field trace-field-wide">
        <span>执行回执（多个用逗号分隔，可含另一任务回执）</span>
        <input v-model="receipts" data-testid="trace-receipts" placeholder="如 RCPT-20261123-0001,RCPT-20261123-0003" />
      </label>
      <button class="btn primary" data-testid="trace-search" type="submit">检索调用明细</button>
    </form>

    <p v-if="traceError" class="error-text">{{ traceError }}</p>

    <p v-if="traceResult && traceResult.unknown_receipts.length" class="error-text" data-testid="trace-unknown">
      未匹配到回执：{{ traceResult.unknown_receipts.join('、') }}
    </p>

    <div v-if="traceResult" class="trace-summary" data-testid="trace-summary">
      <span>任务数：{{ traceResult.task_count }}</span>
      <span>执行尝试：{{ traceResult.attempt_count }}</span>
      <span>成功：{{ totalByOutcome('成功') }}</span>
      <span>中断：{{ totalByOutcome('中断') }}</span>
      <span>仅派发：{{ totalByOutcome('派发') }}</span>
    </div>

    <div v-for="task in traceResult?.tasks ?? []" :key="task.task_no" class="trace-task" :data-testid="`trace-task-${task.task_no}`">
      <h4 class="trace-task-title">
        任务 {{ task.task_no }}
        <small>派发 {{ task.dispatched_count }} · 成功 {{ task.succeeded_count }} · 中断 {{ task.interrupted_count }}</small>
      </h4>

      <table class="data-table trace-table">
        <thead>
          <tr>
            <th>尝试</th>
            <th>执行回执</th>
            <th>阶段</th>
            <th>结论</th>
            <th>产物文件</th>
            <th>来源请求</th>
            <th>恢复 / 补发</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="attempt in task.attempts" :key="`${attempt.task_no}-${attempt.attempt_no}`" :data-testid="`trace-attempt-${attempt.attempt_no}`">
            <td>第 {{ attempt.attempt_no }} 次</td>
            <td class="cell-receipt">{{ attempt.receipt_id }}</td>
            <td>
              <span v-for="stage in attempt.stages" :key="stage" class="stage-chip" :class="`stage-${stage}`">{{ stage }}</span>
            </td>
            <td>
              <span class="outcome-badge" :class="`outcome-${attempt.outcome}`">{{ attempt.outcome }}</span>
            </td>
            <td>
              <template v-if="attempt.has_file">
                <span class="file-ok">仍有文件</span>
                <code class="file-name">{{ baseName(attempt.artifact_file) }}</code>
              </template>
              <span v-else class="file-missing">无产物，可恢复</span>
            </td>
            <td class="cell-params">
              <code>{{ attempt.query_params || '—' }}</code>
              <p v-if="attempt.resumed_from_attempt" class="resume-line">
                恢复自第 {{ attempt.resumed_from_attempt }} 次尝试（回执 {{ attempt.resume_receipt }}）
              </p>
            </td>
            <td class="trace-actions">
              <button
                class="link"
                type="button"
                :data-testid="`recover-${attempt.attempt_no}`"
                @click="onRecover(attempt)"
              >
                进入恢复入口
              </button>
              <button
                class="link"
                type="button"
                :data-testid="`reissue-${attempt.attempt_no}`"
                @click="onReissue(attempt)"
              >
                补发取数
              </button>
              <div v-if="supplementFor === attemptKey(attempt)" class="supplement-box" :data-testid="`supplement-${attempt.attempt_no}`">
                <input v-model="supplementalInput" placeholder="沿事件记录补采的输入，如 region=east&page=2" />
                <button class="btn primary" type="button" @click="confirmSupplement(attempt)">确认补采并补发</button>
                <button class="btn ghost" type="button" @click="cancelSupplement">取消</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <p v-if="traceResult && !traceResult.attempt_count" class="empty-state">未检索到导出执行事件</p>

    <div v-if="actionMessage" class="action-message" :class="{ 'action-error': actionError }" data-testid="trace-action-message">
      {{ actionMessage }}
    </div>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { recoverExport, reissueExport, traceExportCalls } from '@/api/backend-service'
import type { ExportTraceResult, TraceAttempt } from '@/data/types'

const props = defineProps<{ initialTaskNo?: string }>()

const taskNo = ref(props.initialTaskNo ?? '')
const receipts = ref('')
const traceResult = ref<ExportTraceResult | null>(null)
const traceError = ref('')
const actionMessage = ref('')
const actionError = ref(false)
const supplementFor = ref('')
const supplementalInput = ref('')

function attemptKey(attempt: TraceAttempt): string {
  return `${attempt.export_id}-${attempt.attempt_no}`
}

function baseName(path: string | null | undefined): string {
  return path ? path.split('/').pop() ?? path : ''
}

function totalByOutcome(outcome: string): number {
  return traceResult.value?.attempts.filter((item) => item.outcome === outcome).length ?? 0
}

async function runTrace() {
  traceError.value = ''
  if (!taskNo.value && !receipts.value) {
    traceError.value = '请至少填写任务编号或一个执行回执'
    return
  }
  try {
    traceResult.value = await traceExportCalls({
      taskNo: taskNo.value.trim() || undefined,
      receipts: receipts.value.trim() || undefined,
    })
  } catch (error) {
    traceError.value = error instanceof Error ? error.message : '调用明细检索失败'
  }
}

function flash(message: string, isError = false) {
  actionMessage.value = message
  actionError.value = isError
}

async function onRecover(attempt: TraceAttempt) {
  try {
    const result = await recoverExport(attempt.export_id, {
      attempt_no: attempt.attempt_no,
      receipt_id: attempt.receipt_id,
    })
    if (!result.ok) {
      flash(`恢复失败：${result.message ?? '未知原因'}`, true)
      return
    }
    flash(`已带第${attempt.attempt_no}次尝试的请求进入恢复入口，新尝试第${result.attempt_no}次执行成功（回执 ${result.receipt_id}）`)
    await runTrace()
  } catch (error) {
    flash(error instanceof Error ? error.message : '恢复入口调用失败', true)
  }
}

async function onReissue(attempt: TraceAttempt) {
  if (attempt.interrupted || !attempt.has_file) {
    // 中断缺输入：先要求沿事件记录补采
    supplementFor.value = attemptKey(attempt)
    supplementalInput.value = ''
    return
  }
  try {
    const result = await reissueExport(attempt.export_id, {
      attempt_no: attempt.attempt_no,
      receipt_id: attempt.receipt_id,
    })
    if (!result.ok) {
      flash(`补发失败：${result.message ?? '未知原因'}`, true)
      return
    }
    flash(`已从确认产物取数补发：${baseName(result.artifact_file)}，共 ${result.rows_count} 行，来源回执 ${result.receipt_id}`)
  } catch (error) {
    flash(error instanceof Error ? error.message : '补发入口调用失败', true)
  }
}

function cancelSupplement() {
  supplementFor.value = ''
  supplementalInput.value = ''
}

async function confirmSupplement(attempt: TraceAttempt) {
  if (!supplementalInput.value.trim()) {
    flash('请填写沿事件记录补采的输入', true)
    return
  }
  try {
    const result = await reissueExport(attempt.export_id, {
      attempt_no: attempt.attempt_no,
      receipt_id: attempt.receipt_id,
      supplemental_inputs: [supplementalInput.value.trim()],
    })
    if (!result.ok) {
      flash(`补采补发失败：${result.message ?? '未知原因'}`, true)
      return
    }
    flash(`已沿记录补采并恢复：新尝试第${result.attempt_no}次，来源回执 ${result.source_receipt_id}，补采输入 ${result.supplemental_inputs?.join('；')}`)
    cancelSupplement()
    await runTrace()
  } catch (error) {
    flash(error instanceof Error ? error.message : '补采补发失败', true)
  }
}

onMounted(() => {
  if (props.initialTaskNo) void runTrace()
})
</script>

<style scoped>
.trace-panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px 16px;
  margin: 14px 0;
}
.trace-head h3 {
  margin: 0 0 4px;
  font-size: 15px;
}
.trace-hint {
  margin: 0;
  color: var(--muted);
  font-size: 12px;
}
.trace-query {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: flex-end;
  margin: 12px 0;
}
.trace-field span {
  display: block;
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 4px;
}
.trace-field input {
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 7px 9px;
  min-width: 180px;
}
.trace-field-wide input {
  min-width: 340px;
}
.trace-field input:disabled {
  background: #f0f2f5;
  color: var(--muted);
}
.trace-summary {
  display: flex;
  gap: 16px;
  font-size: 13px;
  padding: 8px 10px;
  background: #f6f8fb;
  border-radius: 6px;
  margin-bottom: 10px;
}
.trace-task {
  margin-bottom: 14px;
}
.trace-task-title {
  font-size: 14px;
  margin: 8px 0 6px;
}
.trace-task-title small {
  color: var(--muted);
  font-weight: normal;
  margin-left: 8px;
}
.trace-table {
  font-size: 12px;
}
.cell-receipt {
  font-family: monospace;
}
.cell-params code {
  display: block;
  word-break: break-all;
}
.resume-line {
  margin: 4px 0 0;
  color: #b45309;
  font-size: 12px;
}
.stage-chip {
  display: inline-block;
  padding: 1px 7px;
  border-radius: 10px;
  font-size: 11px;
  margin-right: 4px;
  color: #fff;
}
.stage-派发 {
  background: #64748b;
}
.stage-成功 {
  background: #16a34a;
}
.stage-中断 {
  background: #dc2626;
}
.outcome-badge {
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 12px;
  color: #fff;
}
.outcome-成功 {
  background: #16a34a;
}
.outcome-中断 {
  background: #dc2626;
}
.outcome-派发 {
  background: #64748b;
}
.file-ok {
  color: #16a34a;
  font-size: 12px;
}
.file-missing {
  color: #b45309;
  font-size: 12px;
}
.file-name {
  display: block;
  font-size: 11px;
  color: var(--muted);
  margin-top: 2px;
}
.trace-actions {
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: flex-start;
}
.supplement-box {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 4px;
  max-width: 280px;
}
.supplement-box input {
  width: 100%;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 8px;
}
.action-message {
  margin-top: 10px;
  padding: 8px 10px;
  border-radius: 6px;
  background: #ecfdf3;
  color: #166534;
  font-size: 13px;
}
.action-message.action-error {
  background: #fef3f2;
  color: #b42318;
}
</style>
