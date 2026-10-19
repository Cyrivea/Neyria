<template>
  <Transition name="archive-slide">
    <aside v-if="open" class="archive-panel" aria-label="产物队列">
      <header class="archive-head">
        <div>
          <h3 class="archive-title">产物队列</h3>
          <p class="archive-subtitle">
            每次运行的产物按链归档；删除只清记录与文件，已消耗的积分不退还。
          </p>
        </div>
        <div class="archive-head-actions">
          <button
            type="button"
            class="archive-refresh"
            title="刷新"
            :disabled="loading"
            @click="refresh"
          >
            ⟳
          </button>
          <button
            type="button"
            class="archive-close"
            title="关闭"
            aria-label="关闭产物队列"
            @click="emit('close')"
          >
            ×
          </button>
        </div>
      </header>

      <div v-if="loading && runs.length === 0" class="archive-empty">正在读取…</div>
      <div v-else-if="runs.length === 0" class="archive-empty">
        还没有运行记录。点顶部「运行工作流」产出第一批产物。
      </div>

      <ul v-else class="archive-list">
        <li v-for="run in runs" :key="run.run_id" class="archive-run">
          <div class="archive-run-head">
            <span class="archive-run-status" :class="`is-${run.status}`">
              {{ statusLabel(run.status) }}
            </span>
            <span class="archive-run-time">{{ formatTime(run.created_at) }}</span>
            <span class="archive-run-progress"
              >{{ run.progress.done }}/{{ run.progress.total }} 步</span
            >
          </div>
          <p v-if="run.error" class="archive-run-error">{{ run.error }}</p>

          <div
            v-for="(chain, idx) in run.chains"
            :key="`${run.run_id}-${idx}`"
            class="archive-chain"
          >
            <div class="archive-chain-label">
              链 {{ chain.chain_no ?? '—' }} · {{ chain.label }}
            </div>
            <div
              v-for="block in chain.blocks"
              :key="`${run.run_id}-${block.node_id}-${block.status}`"
              class="archive-block"
            >
              <div class="archive-block-head">
                <span class="archive-block-name">
                  {{ block.node_name }}
                  <template v-if="block.reused"
                    ><span class="archive-reused">（复用缓存）</span></template
                  >
                </span>
                <span class="archive-block-status">{{ blockStatusLabel(block) }}</span>
              </div>

              <template v-if="block.status === 'succeeded' && block.content">
                <div v-if="isAssetContent(block.content)" class="archive-asset">
                  <video
                    v-if="isVideoAsset(block.content)"
                    class="archive-video"
                    :src="assetUrl(block.content)"
                    controls
                    preload="metadata"
                  ></video>
                  <img
                    v-else
                    class="archive-image"
                    :src="assetUrl(block.content)"
                    :alt="block.node_name"
                  />
                  <a
                    class="archive-download"
                    :href="assetUrl(block.content)"
                    download
                    target="_blank"
                    rel="noopener"
                  >
                    下载
                  </a>
                </div>
                <div v-else class="archive-text">
                  <pre class="archive-text-content">{{ block.content }}</pre>
                  <button type="button" class="archive-copy" @click="copyText(block.content ?? '')">
                    复制
                  </button>
                </div>
              </template>
              <p v-else-if="block.status === 'failed' && block.error" class="archive-block-error">
                {{ block.error }}
              </p>
            </div>
          </div>

          <div class="archive-run-actions">
            <button
              type="button"
              class="archive-action"
              :class="{ 'is-on': run.kept }"
              :title="
                run.kept ? '已标记保留，未来的自动清理不会动它' : '标记保留：这版产物留着别清'
              "
              @click="toggleKeep(run)"
            >
              {{ run.kept ? '★ 已保留' : '☆ 保留' }}
            </button>
            <button
              type="button"
              class="archive-action is-danger"
              @click="askingDeleteId = run.run_id"
            >
              删除
            </button>
          </div>

          <div v-if="askingDeleteId === run.run_id" class="archive-delete-confirm">
            删除会移除本次运行的记录与产物文件；已消耗的积分不会退还。确定删除？
            <div class="archive-delete-actions">
              <button type="button" class="archive-action" @click="askingDeleteId = null">
                取消
              </button>
              <button
                type="button"
                class="archive-action is-danger"
                :disabled="deletingId === run.run_id"
                @click="confirmDelete(run.run_id)"
              >
                {{ deletingId === run.run_id ? '删除中…' : '确定删除' }}
              </button>
            </div>
          </div>
        </li>
      </ul>
    </aside>
  </Transition>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from '@/utils/toast'
import {
  deleteWorkflowRun,
  keepWorkflowRun,
  listWorkflowRuns,
  type ArchivedRun,
} from '@/serve/workflow'
import { useProjectStore } from '@/stores/project'
import { JOB_TERMINAL_EVENT } from '@/utils/jobTransitions'
import { formatProjectTime } from '@/utils/datetime'
import { isAssetContent, isVideoAsset, assetUrl } from '@/utils/runPreview'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (event: 'close'): void }>()

const projectStore = useProjectStore()
const runs = ref<ArchivedRun[]>([])
const loading = ref(false)
const askingDeleteId = ref<string | null>(null)
const deletingId = ref<string | null>(null)

async function refresh() {
  loading.value = true
  try {
    const data = await listWorkflowRuns(projectStore.id)
    runs.value = data.runs
  } catch {
    ElMessage.error('产物队列读取失败，请稍后重试')
  } finally {
    loading.value = false
  }
}

watch(
  () => props.open,
  (open) => {
    if (open) void refresh()
  },
)

function onJobTerminal() {
  if (props.open) void refresh()
}

onMounted(() => window.addEventListener(JOB_TERMINAL_EVENT, onJobTerminal))
onBeforeUnmount(() => window.removeEventListener(JOB_TERMINAL_EVENT, onJobTerminal))

function statusLabel(status: string): string {
  switch (status) {
    case 'succeeded':
      return '✓ 已完成'
    case 'failed':
      return '✗ 失败'
    case 'cancelled':
      return '■ 已取消'
    case 'running':
      return '… 运行中'
    default:
      return '… 排队中'
  }
}

function blockStatusLabel(block: ArchivedRun['chains'][number]['blocks'][number]): string {
  if (block.status === 'reused') return '复用缓存'
  if (block.status === 'succeeded') return '成功'
  if (block.status === 'failed') return '失败'
  return block.status
}

function formatTime(value: string | null): string {
  if (!value) return ''
  try {
    return formatProjectTime(value)
  } catch {
    return value
  }
}

async function toggleKeep(run: ArchivedRun) {
  try {
    await keepWorkflowRun(run.run_id, !run.kept)
    run.kept = !run.kept
  } catch {
    ElMessage.error('保留标记失败，请重试')
  }
}

async function confirmDelete(runId: string) {
  deletingId.value = runId
  try {
    await deleteWorkflowRun(runId)
    runs.value = runs.value.filter((item) => item.run_id !== runId)
    ElMessage.success('已删除该次运行的记录与产物文件')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '删除失败')
  } finally {
    deletingId.value = null
    askingDeleteId.value = null
  }
}

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('已复制')
  } catch {
    ElMessage.error('复制失败，请手动选择文本')
  }
}
</script>

<style scoped>
.archive-panel {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  width: min(420px, 92vw);
  background: var(--panel-bg, #141414);
  border-left: 1px solid rgba(255, 255, 255, 0.1);
  display: flex;
  flex-direction: column;
  z-index: 60;
  box-shadow: -12px 0 32px rgba(0, 0, 0, 0.45);
}

.archive-slide-enter-active,
.archive-slide-leave-active {
  transition: transform 0.22s ease;
}

.archive-slide-enter-from,
.archive-slide-leave-to {
  transform: translateX(24px);
  opacity: 0;
}

.archive-head {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 16px 16px 12px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.archive-title {
  margin: 0;
  font-size: 16px;
}

.archive-subtitle {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--text-muted, #9a9a9a);
  line-height: 1.5;
}

.archive-head-actions {
  display: flex;
  gap: 8px;
  align-items: flex-start;
}

.archive-refresh,
.archive-close {
  background: none;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 8px;
  color: inherit;
  width: 30px;
  height: 30px;
  cursor: pointer;
}

.archive-empty {
  padding: 24px 16px;
  color: var(--text-muted, #9a9a9a);
  font-size: 13px;
  line-height: 1.6;
}

.archive-list {
  list-style: none;
  margin: 0;
  padding: 12px 16px 24px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.archive-run {
  border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 12px;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.archive-run-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  font-size: 12px;
}

.archive-run-status {
  font-weight: 600;
}

.archive-run-status.is-succeeded {
  color: #f2f2f2;
}

.archive-run-status.is-failed,
.archive-run-status.is-cancelled {
  color: var(--text-muted, #9a9a9a);
}

.archive-run-time,
.archive-run-progress {
  color: var(--text-muted, #9a9a9a);
}

.archive-run-error {
  margin: 0;
  font-size: 12px;
  color: var(--text-muted, #9a9a9a);
}

.archive-chain {
  border-top: 1px dashed rgba(255, 255, 255, 0.08);
  padding-top: 8px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.archive-chain-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--text-muted, #9a9a9a);
}

.archive-block {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding-left: 8px;
  border-left: 2px solid rgba(255, 255, 255, 0.12);
}

.archive-block-head {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
}

.archive-block-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.archive-reused,
.archive-block-status {
  color: var(--text-muted, #9a9a9a);
}

.archive-asset {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.archive-image,
.archive-video {
  max-width: 100%;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.1);
}

.archive-download,
.archive-copy {
  align-self: flex-start;
  font-size: 12px;
  color: inherit;
  background: none;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 6px;
  padding: 3px 10px;
  cursor: pointer;
  text-decoration: none;
}

.archive-text-content {
  margin: 0;
  max-height: 160px;
  overflow: auto;
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
  background: rgba(255, 255, 255, 0.04);
  border-radius: 8px;
  padding: 8px;
}

.archive-block-error {
  margin: 0;
  font-size: 12px;
  color: var(--text-muted, #9a9a9a);
}

.archive-run-actions {
  display: flex;
  gap: 8px;
}

.archive-action {
  font-size: 12px;
  background: none;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 6px;
  padding: 3px 10px;
  color: inherit;
  cursor: pointer;
}

.archive-action.is-on {
  background: rgba(255, 255, 255, 0.1);
}

.archive-action.is-danger {
  border-color: rgba(255, 255, 255, 0.24);
}

.archive-delete-confirm {
  font-size: 12px;
  color: var(--text-muted, #9a9a9a);
  line-height: 1.6;
  border-top: 1px dashed rgba(255, 255, 255, 0.08);
  padding-top: 8px;
}

.archive-delete-actions {
  margin-top: 8px;
  display: flex;
  gap: 8px;
}
</style>
