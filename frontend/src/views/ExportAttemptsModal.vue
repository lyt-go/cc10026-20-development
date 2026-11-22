<template>
  <div class="modal-backdrop" data-testid="attempts-modal" @click.self="$emit('close')">
    <div class="modal attempts-modal">
      <h3>导出任务 {{ entryId }} 的执行尝试</h3>
      <p v-if="loading" class="trace-hint">加载中…</p>
      <p v-else-if="errorMessage" class="error-text">{{ errorMessage }}</p>
      <template v-else>
        <p class="trace-hint">同一任务号的每次执行独立留痕，派发 / 成功 / 中断分别对应一次取数尝试。</p>
        <table class="data-table">
          <thead>
            <tr>
              <th>尝试</th>
              <th>执行回执</th>
              <th>事件阶段</th>
              <th>结论</th>
              <th>产物</th>
              <th>来源请求</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="attempt in attempts" :key="attempt.attempt_no" :data-testid="`modal-attempt-${attempt.attempt_no}`">
              <td>第 {{ attempt.attempt_no }} 次</td>
              <td class="cell-receipt">{{ attempt.receipt_id }}</td>
              <td>
                <div v-for="ev in attempt.events" :key="ev.logged_at + ev.event" class="event-line">
                  <span class="stage-chip" :class="`stage-${ev.stage}`">{{ ev.stage }}</span>
                  <span class="event-time">{{ formatTime(ev.logged_at) }}</span>
                  <span v-if="ev.reason" class="event-reason">{{ ev.reason }}</span>
                </div>
              </td>
              <td>
                <span class="outcome-badge" :class="`outcome-${attempt.outcome}`">{{ attempt.outcome }}</span>
              </td>
              <td>{{ attempt.has_file ? '仍有文件' : '无产物' }}</td>
              <td><code>{{ attempt.query_params || '—' }}</code></td>
            </tr>
          </tbody>
        </table>
      </template>
      <div class="page-actions modal-foot">
        <button class="btn" type="button" data-testid="attempts-close" @click="$emit('close')">关闭</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { listExportAttempts } from '@/api/backend-service'
import type { TraceAttempt } from '@/data/types'

const props = defineProps<{ entryId: number }>()
defineEmits<{ close: [] }>()

const attempts = ref<TraceAttempt[]>([])
const loading = ref(true)
const errorMessage = ref('')

function formatTime(value: string | null): string {
  if (!value) return ''
  return value.replace('T', ' ').slice(0, 19)
}

onMounted(async () => {
  try {
    const result = await listExportAttempts(props.entryId)
    attempts.value = result.attempts
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '执行尝试读取失败'
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.attempts-modal {
  width: min(880px, 100%);
}
.trace-hint {
  color: var(--muted);
  font-size: 12px;
}
.cell-receipt {
  font-family: monospace;
}
.event-line {
  display: flex;
  gap: 6px;
  align-items: center;
  margin-bottom: 3px;
}
.event-time {
  font-size: 11px;
  color: var(--muted);
}
.event-reason {
  font-size: 11px;
  color: #b42318;
}
.stage-chip {
  display: inline-block;
  padding: 1px 7px;
  border-radius: 10px;
  font-size: 11px;
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
.modal-foot {
  margin-top: 14px;
  justify-content: flex-end;
}
</style>
