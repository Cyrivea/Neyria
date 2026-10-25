<template>
  <section class="task-panel" aria-label="后台任务">
    <!-- B22: SettingsPanel 外层已有区块标题，“embedded” 模式下不重复输出自己的标题头 -->
    <div v-if="!props.embedded" class="task-panel-head">
      <div>
        <div class="task-panel-title">后台任务</div>
        <div class="task-panel-caption">文档索引在 Worker 中异步执行</div>
      </div>
      <span v-if="taskStore.hasActiveTasks" class="task-live-dot" title="任务运行中"></span>
    </div>

    <button
      v-if="taskStore.error && !props.embedded"
      type="button"
      class="task-error-link"
      @click="emit('open-settings')"
    >
      <span class="task-error-link-icon" aria-hidden="true">!</span>
      <span>任务状态异常 · 在设置中查看</span>
    </button>

    <div v-if="!projectStore.id" class="task-empty">进入一个服务端项目后，这里会显示文档状态。</div>
    <div v-else-if="taskStore.isLoading && !taskStore.documents.length" class="task-empty">
      正在读取任务状态…
    </div>
    <div v-else-if="!taskStore.documents.length && !taskStore.activeJobs.length" class="task-empty">
      暂无后台任务
    </div>

    <div v-else class="task-list">
      <article v-for="document in taskStore.documents" :key="document.id" class="task-item">
        <div class="task-item-row">
          <span class="task-file-name" :title="document.filename">{{ document.filename }}</span>
          <span class="task-status" :class="`is-${document.status}`">{{
            statusLabel(document.status)
          }}</span>
        </div>
        <div
          v-if="document.status === 'queued' || document.status === 'processing'"
          class="task-progress"
        >
          <span :style="{ width: `${documentProgress(document)}%` }"></span>
        </div>
        <div class="task-item-meta">
          <span v-if="document.status === 'ready'">{{ document.chunks_count }} 个文本块</span>
          <span v-else-if="document.status === 'processing'">正在解析并向量化…</span>
          <span v-else-if="document.status === 'queued'">等待 Worker 领取</span>
          <span v-else>{{ statusLabel(document.status) }}</span>
          <button
            v-if="document.status === 'failed' && document.job_id"
            type="button"
            class="task-cancel"
            @click="retry(document.job_id)"
          >
            重试
          </button>
        </div>
      </article>

      <article v-for="job in taskStore.activeJobs" :key="job.id" class="task-item task-job-item">
        <div class="task-item-row">
          <span class="task-file-name">后台任务</span>
          <span class="task-status is-running">{{
            job.status === 'queued' ? '排队中' : '执行中'
          }}</span>
        </div>
        <div class="task-progress"><span :style="{ width: `${job.progress}%` }"></span></div>
        <div class="task-item-meta">
          <span>{{ job.progress_message || '准备执行' }} · {{ job.progress }}%</span>
          <button type="button" class="task-cancel" @click="cancel(job.id)">取消</button>
        </div>
      </article>
    </div>
  </section>
</template>

<script setup lang="ts">
import { useProjectStore } from '@/stores/project'
import { useTaskStore } from '@/stores/tasks'
import type { AgentDocument, DocumentStatus } from '@/serve/agent'

const projectStore = useProjectStore()
const taskStore = useTaskStore()
const props = withDefaults(defineProps<{ embedded?: boolean }>(), { embedded: false })
const emit = defineEmits<{ (event: 'open-settings'): void }>()

const labels: Record<DocumentStatus, string> = {
  queued: '排队中',
  processing: '索引中',
  ready: '已就绪',
  failed: '失败',
  cancelled: '已取消',
  deleted: '已删除',
}

function statusLabel(status: DocumentStatus) {
  return labels[status]
}

function documentProgress(document: AgentDocument) {
  if (document.status === 'queued') return 5
  if (document.status === 'processing') return 55
  return document.status === 'ready' ? 100 : 0
}

async function cancel(jobId: string) {
  try {
    await taskStore.cancel(jobId)
  } catch {
    // store 会在下一次轮询时展示后端错误；此处不打断工作台。
  }
}

async function retry(jobId: string) {
  try {
    await taskStore.retry(jobId)
  } catch {
    // 下一次轮询会保留后端返回的错误状态。
  }
}
</script>

<style scoped>
.task-panel {
  padding: 14px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  color: #d8d8d8;
}

.task-panel-head,
.task-item-row,
.task-item-meta {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.task-panel-title {
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.04em;
}
.task-panel-caption,
.task-item-meta,
.task-empty {
  color: #777;
  font-size: 11px;
}
.task-panel-caption {
  margin-top: 3px;
}
.task-live-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #cacaca;
  box-shadow: 0 0 8px #cacaca;
}
.task-empty {
  padding: 16px 2px 6px;
  line-height: 1.6;
}
.task-error-link {
  display: flex;
  align-items: center;
  gap: 7px;
  width: 100%;
  margin-top: 12px;
  padding: 8px 9px;
  border: 1px solid rgba(142, 142, 142, 0.2) !important;
  border-radius: 7px;
  background: rgba(50, 50, 50, 0.12) !important;
  color: #b7b7b7 !important;
  font-size: 11px;
  text-align: left;
}
.task-error-link:hover {
  background: rgba(50, 50, 50, 0.2) !important;
}
.task-error-link-icon {
  display: grid;
  flex: 0 0 auto;
  width: 16px;
  height: 16px;
  place-items: center;
  border-radius: 50%;
  background: rgba(142, 142, 142, 0.18);
  font-size: 10px;
  font-weight: 700;
}

.task-error,
.task-item-error {
  color: #b7b7b7;
  font-size: 11px;
  line-height: 1.5;
}
.task-error {
  padding-top: 12px;
}
.task-list {
  display: grid;
  gap: 8px;
  margin-top: 12px;
  max-height: 240px;
  overflow: auto;
}
.task-item {
  padding: 9px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.025);
}
.task-file-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 12px;
}
.task-status {
  flex: 0 0 auto;
  font-size: 10px;
}
.task-status.is-ready {
  color: #d4d4d4;
}
.task-status.is-failed {
  color: #b7b7b7;
}
.task-status.is-processing,
.task-status.is-queued,
.task-status.is-running {
  color: #d2d2d2;
}
.task-status.is-cancelled {
  color: #a4a4a4;
}
.task-progress {
  height: 3px;
  margin: 8px 0 6px;
  overflow: hidden;
  border-radius: 3px;
  background: rgba(255, 255, 255, 0.08);
}
.task-progress span {
  display: block;
  height: 100%;
  border-radius: inherit;
  background: #717171;
  transition: width 0.3s ease;
}
.task-item-error {
  margin-top: 6px;
}
.task-cancel {
  border: 0;
  background: transparent;
  color: #999999;
  cursor: pointer;
  font-size: 11px;
  padding: 0;
}
.task-cancel:hover {
  color: #bdbdbd;
}
</style>
