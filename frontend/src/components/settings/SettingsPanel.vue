<template>
  <Transition name="modal-fade">
    <div v-if="open" class="modal-overlay settings-overlay" role="presentation" @click.self="close">
      <section
        class="modal-card settings-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="settingsTitle"
      >
        <div class="modal-head settings-head">
          <div>
            <div id="settingsTitle" class="modal-title">工作站设置</div>
            <div class="modal-caption">管理工作站状态，并集中查看需要处理的问题。</div>
          </div>
          <button class="modal-close" type="button" aria-label="关闭设置" @click="close">✕</button>
        </div>

        <div class="settings-body">
          <section class="settings-section" aria-labelledby="errorSectionTitle">
            <div class="settings-section-head">
              <div>
                <h2 id="errorSectionTitle" class="settings-section-title">错误信息</h2>
                <p class="settings-section-caption">
                  后台任务、文档索引和接口请求的错误会显示在这里。
                </p>
              </div>
              <span v-if="errorEntries.length" class="settings-error-count">
                {{ errorEntries.length }} 条
              </span>
            </div>

            <div v-if="!errorEntries.length" class="settings-empty settings-empty-success">
              <span class="settings-empty-icon" aria-hidden="true">✓</span>
              <div>
                <div class="settings-empty-title">暂无错误</div>
                <div class="settings-empty-copy">当前工作站运行正常。</div>
              </div>
            </div>

            <div v-else class="settings-error-list">
              <article v-for="entry in errorEntries" :key="entry.id" class="settings-error-item">
                <div class="settings-error-item-head">
                  <span class="settings-error-icon" aria-hidden="true">!</span>
                  <div class="settings-error-title">{{ entry.title }}</div>
                  <span class="settings-error-source">{{ entry.source }}</span>
                </div>
                <div class="settings-error-message">{{ entry.message }}</div>
                <div class="settings-error-meta">{{ entry.meta }}</div>
                <button
                  v-if="entry.retryJobId"
                  type="button"
                  class="settings-error-action"
                  @click="retry(entry.retryJobId)"
                >
                  重试任务
                </button>
              </article>
            </div>
          </section>

          <section class="settings-section" aria-labelledby="taskSectionTitle">
            <div class="settings-section-head">
              <div>
                <h2 id="taskSectionTitle" class="settings-section-title">后台任务</h2>
                <p class="settings-section-caption">文档索引在 Worker 中异步执行。</p>
              </div>
            </div>
            <TaskPanel embedded />
          </section>

          <section
            class="settings-section settings-section-secondary"
            aria-labelledby="runtimeTitle"
          >
            <div class="settings-section-head">
              <div>
                <h2 id="runtimeTitle" class="settings-section-title">运行状态</h2>
                <p class="settings-section-caption">当前项目与后台任务轮询状态。</p>
              </div>
            </div>
            <div class="settings-runtime-grid">
              <div class="settings-runtime-item">
                <span class="settings-runtime-label">当前项目</span>
                <span class="settings-runtime-value">{{ projectStore.name || '未选择项目' }}</span>
              </div>
              <div class="settings-runtime-item">
                <span class="settings-runtime-label">后台任务</span>
                <span class="settings-runtime-value"
                  >{{ taskStore.activeJobs.length }} 个运行中</span
                >
              </div>
            </div>
          </section>
        </div>

        <div class="settings-footer">
          <button
            class="settings-refresh-button"
            type="button"
            :disabled="isRefreshing || !projectStore.id"
            @click="refresh"
          >
            {{ isRefreshing ? '刷新中…' : '重新检查' }}
          </button>
          <button class="settings-primary-button" type="button" @click="close">完成</button>
        </div>
      </section>
    </div>
  </Transition>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useProjectStore } from '@/stores/project'
import { useTaskStore } from '@/stores/tasks'
import TaskPanel from '@/components/task/TaskPanel.vue'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (event: 'close'): void }>()

const projectStore = useProjectStore()
const taskStore = useTaskStore()
const isRefreshing = ref(false)

interface ErrorEntry {
  id: string
  title: string
  message: string
  source: string
  meta: string
  retryJobId?: string
}

const errorEntries = computed<ErrorEntry[]>(() => {
  const entries: ErrorEntry[] = []

  if (taskStore.error) {
    entries.push({
      id: 'task-request',
      title: '后台任务请求失败',
      message: taskStore.error,
      source: '接口请求',
      meta: '无法读取当前项目的任务状态，请点击“重新检查”再试。',
    })
  }

  taskStore.documents.forEach((document) => {
    if (!document.error) return
    entries.push({
      id: `document-${document.id}`,
      title: `文档索引失败：${document.filename}`,
      message: document.error,
      source: '文档索引',
      meta: '可以在后台任务区域重试该文档。',
      retryJobId: document.job_id ?? undefined,
    })
  })

  taskStore.jobs.forEach((job) => {
    if (job.status !== 'failed' || !job.error) return
    entries.push({
      id: `job-${job.id}`,
      title: `后台任务失败：${job.type}`,
      message: job.error,
      source: 'Worker',
      meta: `已尝试 ${job.attempts}/${job.max_attempts} 次。`,
      retryJobId: job.id,
    })
  })

  return entries
})

function close() {
  emit('close')
}

async function refresh() {
  if (!projectStore.id || isRefreshing.value) return
  isRefreshing.value = true
  try {
    await taskStore.load(projectStore.id)
  } finally {
    isRefreshing.value = false
  }
}

async function retry(jobId: string) {
  try {
    await taskStore.retry(jobId)
  } catch {
    // 请求错误由统一 request 拦截器提示，面板保留原错误信息。
  }
}

function handleKeydown(event: KeyboardEvent) {
  if (props.open && event.key === 'Escape') close()
}

watch(
  () => props.open,
  (open) => {
    if (open) document.addEventListener('keydown', handleKeydown)
    else document.removeEventListener('keydown', handleKeydown)
  },
)

onBeforeUnmount(() => document.removeEventListener('keydown', handleKeydown))
</script>

<style scoped>
.settings-overlay {
  align-items: flex-end;
  justify-content: flex-start;
  padding: 24px 24px 24px 66px;
}

.settings-card {
  width: min(620px, calc(100vw - 90px));
  max-height: min(760px, calc(100vh - 48px));
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.settings-head {
  flex: 0 0 auto;
}

.settings-body {
  min-height: 0;
  padding: 0 22px 6px;
  overflow: auto;
}

.settings-section {
  padding: 18px 0;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}

.settings-section:first-child {
  border-top: 0;
}

.settings-section-secondary {
  padding-bottom: 10px;
}

.settings-section-head,
.settings-error-item-head,
.settings-runtime-item {
  display: flex;
  align-items: center;
  gap: 10px;
}

.settings-section-head {
  justify-content: space-between;
  align-items: flex-start;
}

.settings-section-title {
  margin: 0;
  color: var(--text-primary);
  font-size: 14px;
  font-weight: 600;
}

.settings-section-caption {
  margin: 4px 0 0;
  color: var(--text-secondary);
  font-size: 11px;
  line-height: 1.5;
}

.settings-error-count {
  flex: 0 0 auto;
  padding: 3px 8px;
  border: 1px solid rgba(142, 142, 142, 0.28);
  border-radius: 999px;
  color: #b7b7b7;
  font-size: 10px;
}

.settings-empty {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 14px;
  padding: 14px;
  border: 1px solid rgba(212, 212, 212, 0.15);
  border-radius: 10px;
  background: rgba(212, 212, 212, 0.04);
}

.settings-empty-icon {
  display: grid;
  width: 24px;
  height: 24px;
  place-items: center;
  border-radius: 50%;
  background: rgba(212, 212, 212, 0.14);
  color: #d4d4d4;
  font-size: 13px;
}

.settings-empty-title {
  color: #f0f0f0;
  font-size: 12px;
}

.settings-empty-copy {
  margin-top: 2px;
  color: var(--text-secondary);
  font-size: 11px;
}

.settings-error-list {
  display: grid;
  gap: 8px;
  margin-top: 14px;
}

.settings-error-item {
  padding: 12px;
  border: 1px solid rgba(142, 142, 142, 0.2);
  border-radius: 10px;
  background: rgba(50, 50, 50, 0.12);
}

.settings-error-item-head {
  min-width: 0;
}

.settings-error-icon {
  display: grid;
  flex: 0 0 auto;
  width: 20px;
  height: 20px;
  place-items: center;
  border-radius: 50%;
  background: rgba(142, 142, 142, 0.18);
  color: #b7b7b7;
  font-size: 12px;
  font-weight: 700;
}

.settings-error-title {
  min-width: 0;
  overflow: hidden;
  color: #d5d5d5;
  font-size: 12px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.settings-error-source {
  flex: 0 0 auto;
  margin-left: auto;
  color: #8e8e8e;
  font-size: 10px;
}

.settings-error-message {
  margin: 9px 0 0 30px;
  overflow-wrap: anywhere;
  color: #b7b7b7;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  line-height: 1.5;
}

.settings-error-meta {
  margin: 6px 0 0 30px;
  color: var(--text-secondary);
  font-size: 10px;
}

.settings-error-action {
  margin: 10px 0 0 30px;
  padding: 5px 9px;
  border: 1px solid rgba(153, 153, 153, 0.3) !important;
  border-radius: 5px;
  color: #bdbdbd !important;
  font-size: 11px;
}

.settings-error-action:hover {
  background: rgba(113, 113, 113, 0.14) !important;
}

.settings-runtime-grid {
  display: grid;
  gap: 8px;
  margin-top: 12px;
}

.settings-runtime-item {
  justify-content: space-between;
  padding: 9px 11px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.025);
}

.settings-runtime-label {
  color: var(--text-secondary);
  font-size: 11px;
}

.settings-runtime-value {
  max-width: 68%;
  overflow: hidden;
  color: var(--text-primary);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.settings-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  flex: 0 0 auto;
  padding: 16px 22px 22px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}

.settings-refresh-button,
.settings-primary-button {
  padding: 8px 13px;
  border-radius: 6px;
  font-size: 11px;
}

.settings-refresh-button {
  border: 1px solid rgba(255, 255, 255, 0.12) !important;
  color: var(--text-secondary) !important;
}

.settings-refresh-button:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.05) !important;
  color: var(--text-primary) !important;
}

.settings-refresh-button:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}

.settings-primary-button {
  background: #e6e6e6 !important;
  color: #111111 !important;
}

.settings-primary-button:hover {
  background: #ffffff !important;
}
</style>
