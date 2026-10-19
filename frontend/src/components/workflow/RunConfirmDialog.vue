<template>
  <Transition name="modal-fade">
    <div v-if="open" class="modal-overlay" @click.self="emit('close')">
      <div
        class="modal-card run-confirm-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="runConfirmTitle"
      >
        <div class="modal-head">
          <div>
            <div id="runConfirmTitle" class="modal-title">运行整个工作流</div>
            <div class="modal-caption">先报账再生成：命中的节点直接复用，不再重跑也不计费。</div>
          </div>
        </div>

        <div class="modal-body run-confirm-body">
          <div v-if="workflowStore.runPreviewLoading" class="run-confirm-loading">正在核算…</div>

          <template v-else-if="!preview">
            <p class="run-confirm-error">预览暂时拉取失败，请检查后重试。</p>
          </template>

          <template v-else-if="!preview.ready">
            <p class="run-confirm-error">⚠ {{ preview.reason ?? '画布还没准备好运行' }}</p>
          </template>

          <template v-else>
            <div
              v-for="chain in preview.chains"
              :key="String(chain.chain_no)"
              class="preview-chain"
            >
              <div class="preview-chain-head">
                <span class="preview-chain-title">
                  {{ chain.chain_no != null ? `链 ${chain.chain_no}` : '未登记链' }} ·
                  {{ chain.label }}
                </span>
                <span class="preview-chain-cost">{{ formatCredits(chain.cost_credits) }} 积分</span>
              </div>
              <ul class="preview-block-list">
                <li
                  v-for="block in chain.blocks"
                  :key="block.node_id"
                  class="preview-block"
                  :class="{ 'is-free': block.cost_credits === 0 }"
                >
                  <span class="preview-block-icon">{{
                    previewStateDisplay(block.state).icon
                  }}</span>
                  <span class="preview-block-name">{{ block.name }}</span>
                  <span class="preview-block-state">
                    {{ previewStateDisplay(block.state).label }}
                    <template v-if="block.reason"> · {{ block.reason }}</template>
                  </span>
                  <span class="preview-block-cost">
                    {{ block.cost_credits === 0 ? '0' : formatCredits(block.cost_credits) }}
                  </span>
                </li>
              </ul>
            </div>

            <div class="preview-total">
              <span>
                预计消耗（上限）
                <span v-if="preview.reused_count > 0" class="preview-reused-note">
                  其中 {{ preview.reused_count }} 个节点命中缓存，0 积分
                </span>
              </span>
              <strong>{{ formatCredits(preview.total_cost_credits) }} 积分</strong>
            </div>

            <div v-if="balanceSummary" class="preview-balance">
              当前可用 {{ balanceSummary }} 积分
              <span v-if="isUnderfunded" class="preview-underfunded"
                >⚠ 余额可能不足以覆盖本次运行</span
              >
            </div>

            <p class="preview-footnote">
              预估按各工具预占价求和，是上限而非承诺：上游重跑后产出若与之前一致，下游会自动转缓存复用。
            </p>
          </template>
        </div>

        <div class="project-create-actions run-confirm-actions">
          <button class="project-secondary-button" type="button" @click="emit('close')">
            取消
          </button>
          <button
            class="project-primary-button"
            type="button"
            :disabled="!preview?.ready || workflowStore.runPreviewLoading || running"
            @click="confirmRun"
          >
            {{ running ? '已提交…' : '确认运行' }}
          </button>
        </div>
      </div>
    </div>
  </Transition>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from '@/utils/toast'
import { getBalance } from '@/serve/billing'
import { useWorkflowStore } from '@/stores/workflow'
import { formatCredits, previewStateDisplay } from '@/utils/runPreview'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{ (event: 'close'): void; (event: 'started', status: string): void }>()

const workflowStore = useWorkflowStore()
const preview = computed(() => workflowStore.runPreview)
const running = ref(false)
const availableCredits = ref<number | null>(null)

watch(
  () => props.open,
  (open) => {
    if (!open) return
    void workflowStore.fetchRunPreview()
    void getBalance()
      .then((balance) => {
        availableCredits.value =
          parseFloat(balance.plan_credits) + parseFloat(balance.prepaid_credits)
      })
      .catch(() => {
        availableCredits.value = null
      })
  },
)

const balanceSummary = computed(() =>
  availableCredits.value === null ? null : formatCredits(availableCredits.value),
)
const isUnderfunded = computed(
  () =>
    availableCredits.value !== null &&
    preview.value !== null &&
    availableCredits.value < preview.value.total_cost_credits,
)

async function confirmRun() {
  if (!preview.value?.ready || running.value) return
  running.value = true
  try {
    const status = await workflowStore.runCurrentWorkflow()
    if (status === 'succeeded') {
      ElMessage.success('工作流已完成，产物已写入画布与产物队列')
    } else if (status === 'model_unavailable') {
      ElMessage.warning('部分工具模型尚未配置，请在画布上查看失败原因')
    } else {
      ElMessage.error('工作流运行未全部成功，请在画布上查看失败原因')
    }
    emit('started', status)
    emit('close')
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '运行提交失败')
  } finally {
    running.value = false
  }
}
</script>

<style scoped>
.run-confirm-card {
  max-width: 560px;
  width: min(560px, 92vw);
}

.run-confirm-body {
  display: flex;
  flex-direction: column;
  gap: 14px;
  max-height: 60vh;
  overflow-y: auto;
}

.run-confirm-loading,
.run-confirm-error {
  color: var(--text-muted, #9a9a9a);
  padding: 12px 4px;
}

.preview-chain {
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 10px;
  padding: 10px 12px;
}

.preview-chain-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 12px;
  margin-bottom: 8px;
}

.preview-chain-title {
  font-weight: 600;
}

.preview-chain-cost {
  color: var(--text-muted, #9a9a9a);
  font-variant-numeric: tabular-nums;
}

.preview-block-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.preview-block {
  display: grid;
  grid-template-columns: 18px minmax(0, 1fr) auto 48px;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}

.preview-block.is-free {
  opacity: 0.72;
}

.preview-block-icon {
  text-align: center;
  color: var(--text-muted, #9a9a9a);
}

.preview-block-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.preview-block-state {
  color: var(--text-muted, #9a9a9a);
  font-size: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.preview-block-cost {
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.preview-total {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding-top: 10px;
  border-top: 1px solid rgba(255, 255, 255, 0.1);
}

.preview-total strong {
  font-size: 18px;
  font-variant-numeric: tabular-nums;
}

.preview-reused-note,
.preview-balance,
.preview-footnote {
  color: var(--text-muted, #9a9a9a);
  font-size: 12px;
}

.preview-balance {
  display: flex;
  gap: 10px;
  align-items: baseline;
}

.preview-underfunded {
  color: #ddd;
}

.preview-footnote {
  line-height: 1.6;
}

.run-confirm-actions {
  padding: 0 24px 20px;
}
</style>
