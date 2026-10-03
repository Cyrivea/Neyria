<template>
  <div class="input-composer" :class="{ 'has-attachment-dock': chatStore.hasUploadedAttachments }">
    <Transition name="attachment-dock-float">
      <AttachmentDock v-if="chatStore.hasUploadedAttachments" @add="openAttachmentPicker" />
    </Transition>

    <input
      ref="attachmentInputRef"
      class="composer-attachment-input"
      type="file"
      :accept="attachmentAcceptAttr"
      @change="handleAttachmentPicked"
    />
    <!-- UI2-⑥：生成中允许继续敲打草稿（发送进排队），输入框不锁 -->
    <Transition name="attachment-dock-float">
      <div v-if="chatStore.messageQueue.length" class="composer-queue" role="list">
        <div
          v-for="item in chatStore.messageQueue"
          :key="item.id"
          class="composer-queue-item"
          role="listitem"
          :title="item.message"
        >
          <span class="composer-queue-text">{{ item.message }}</span>
          <button
            type="button"
            class="composer-queue-remove"
            aria-label="从队列移除"
            @click="chatStore.removeQueuedMessage(item.id)"
          >
            ✕
          </button>
        </div>
      </div>
    </Transition>
    <!-- UI2 第二批①：输入框直接粘贴图片进附件位 -->
    <textarea
      ref="composerTextareaRef"
      v-model="chatStore.draft"
      placeholder="我来帮你实现想法！"
      @keydown="handleComposerKeydown"
      @paste="handlePaste"
    ></textarea>
    <div class="input-composer-toolbar">
      <div class="composer-toolbar-left">
        <button
          class="composer-tool"
          title="选择待发送附件"
          aria-label="附件"
          type="button"
          :disabled="chatStore.isUploadingAttachment || chatStore.isSending"
          @click="openAttachmentPicker"
        >
          <svg
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="1.6"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <path
              d="M21.44 11.05 12.2 20.29a5 5 0 0 1-7.07-7.07l9.19-9.19a3.5 3.5 0 0 1 4.95 4.95l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.48-8.49"
            />
          </svg>
        </button>
      </div>
      <div class="composer-toolbar-right">
        <div ref="modelMenuRef" class="composer-model-wrap">
          <button
            class="composer-model-button"
            :aria-expanded="isModelMenuOpen"
            title="选择对话模型"
            type="button"
            @click.stop="isModelMenuOpen = !isModelMenuOpen"
          >
            {{ selectedModelLabel }} ▾
          </button>
          <Transition name="panel-float">
            <div v-if="isModelMenuOpen" class="composer-model-menu">
              <button
                v-for="option in modelOptions"
                :key="option.value"
                class="composer-model-option"
                :class="{
                  active: chatStore.selectedModel === option.value,
                  disabled: option.disabled,
                }"
                type="button"
                :disabled="option.disabled"
                @click="selectModel(option)"
              >
                <span class="composer-model-option-name">{{ option.label }}</span>
                <span class="composer-model-option-meta">{{ option.meta }}</span>
              </button>
            </div>
          </Transition>
        </div>
        <!-- UI2-①⑥：生成中 Stop 与 Send 同框——Send 此时语义是“排队”（open-webui 设计），
             否则流式期间没有任何把下一条送进去的入口 -->
        <button
          v-if="chatStore.isSending"
          class="composer-action stop"
          title="停止生成"
          type="button"
          @click="emit('stop')"
        >
          <span class="composer-action-icon">■</span>
        </button>
        <button
          class="composer-action primary"
          :title="chatStore.isSending ? '加入发送队列' : 'Send'"
          type="button"
          :disabled="
            chatStore.isUploadingAttachment ||
            (!chatStore.draft.trim() && !chatStore.hasUploadedAttachments)
          "
          @click="emit('send')"
        >
          <span class="composer-action-icon">↑</span>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
// 输入区（D1 拆分第 9 步，todo §8.4）：草稿 v-model 到 chat store、
// 附件选择、模型菜单；发送动作通过事件上抛给 AssistantPanel。
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from '@/utils/toast'
import { ATTACHMENT_ACCEPT_EXTENSIONS } from '@/utils/attachment'
import { useChatStore } from '@/stores/chat'
import AttachmentDock from './AttachmentDock.vue'

interface ModelOption {
  value: string
  label: string
  meta: string
  disabled?: boolean
}

const emit = defineEmits<{
  (e: 'send'): void
  (e: 'stop'): void
  /** UI2 第二批：三个入附口（选块器/粘贴/拖拽）统一成一道事件，状态收在 composable */
  (e: 'attach', files: File[]): void
}>()

const chatStore = useChatStore()

const attachmentInputRef = ref<HTMLInputElement | null>(null)
const modelMenuRef = ref<HTMLElement | null>(null)
const composerTextareaRef = ref<HTMLTextAreaElement | null>(null)

// UI2 第二批③：输入框随内容长高（open-webui 行为）。120px 起步、顶到 40vh 后内部滚动，
// 防止长草稿把整个面板挤爆。CSS 里 min-height 对应 MIN、max-height 对应 MAX，这两处要同步。
const COMPOSER_MIN_HEIGHT = 120
const COMPOSER_MAX_HEIGHT_RATIO = 0.4

function autoGrowComposer() {
  const el = composerTextareaRef.value
  if (!el) return
  const max = Math.round(window.innerHeight * COMPOSER_MAX_HEIGHT_RATIO)
  el.style.height = 'auto' // 先塌回 auto 才能量出真实内容高度（缩小亦然）
  const next = Math.min(Math.max(el.scrollHeight, COMPOSER_MIN_HEIGHT), max)
  el.style.height = `${next}px`
  el.style.overflowY = el.scrollHeight > max ? 'auto' : 'hidden'
}

watch(
  () => chatStore.draft,
  () => {
    void nextTick(autoGrowComposer)
  },
)

// 窗口变高/矮后重算 40vh 上限（工作站拖窗口是常态）
onMounted(() => window.addEventListener('resize', autoGrowComposer))
onBeforeUnmount(() => window.removeEventListener('resize', autoGrowComposer))
// accept 与白名单同一个源（UI2 第二批：选块器/粘贴/拖拽共用同一份格式门禁）
const attachmentAcceptAttr = computed(() =>
  (ATTACHMENT_ACCEPT_EXTENSIONS as readonly string[]).map((ext) => `.${ext}`).join(','),
)
const isModelMenuOpen = ref(false)

const modelOptions: ModelOption[] = [
  { value: 'glm-4-flash', label: 'GLM-4-Flash', meta: '当前已接入 · 智谱' },
  { value: 'gpt-4.1', label: 'GPT-4.1', meta: '未接入后端', disabled: true },
  { value: 'claude-3-7-sonnet', label: 'Claude Sonnet', meta: '未接入后端', disabled: true },
]

const selectedModelLabel = computed(() => {
  return (
    modelOptions.find((option) => option.value === chatStore.selectedModel)?.label || 'GLM-4-Flash'
  )
})

function selectModel(option: ModelOption) {
  if (option.disabled) {
    ElMessage.info(`${option.label} 目前未接入后端，当前只能使用智谱模型`)
    return
  }

  chatStore.selectedModel = option.value
  isModelMenuOpen.value = false
  ElMessage.success(`已切换为 ${option.label}`)
}

/** UI2 第二批①：剪贴板有文件（截图/复制的图片）就抛给统一个人入口；纯文本粘贴不拦截 */
function handlePaste(event: ClipboardEvent) {
  const files = Array.from(event.clipboardData?.files ?? [])
  if (!files.length) return
  event.preventDefault()
  emit('attach', files)
}

function openAttachmentPicker() {
  if (chatStore.isUploadingAttachment) return
  attachmentInputRef.value?.click()
}

function handleAttachmentPicked(event: Event) {
  const input = event.target as HTMLInputElement | null
  const files = Array.from(input?.files ?? [])
  if (files.length) {
    emit('attach', files)
  }
  if (input) {
    input.value = ''
  }
}

function handleComposerKeydown(event: KeyboardEvent) {
  if (event.isComposing) return
  if (event.key !== 'Enter' || event.shiftKey) return
  event.preventDefault()
  emit('send')
}

function handleDocumentClick(event: MouseEvent) {
  const target = event.target as Node | null
  if (modelMenuRef.value && target && !modelMenuRef.value.contains(target)) {
    isModelMenuOpen.value = false
  }
}

onMounted(() => {
  document.addEventListener('click', handleDocumentClick)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', handleDocumentClick)
})
</script>

<style scoped>
.input-composer {
  border: 0 !important;
  border-radius: 0;
  background: transparent;
  padding: 8px 4px 2px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  position: relative;
  overflow: visible;
  pointer-events: auto;
  z-index: 1;
  transition:
    border-color 180ms ease,
    background 180ms ease,
    box-shadow 220ms cubic-bezier(0.22, 1, 0.36, 1),
    transform 220ms cubic-bezier(0.22, 1, 0.36, 1);
}

.input-composer:hover,
.input-composer:focus-within {
  background: transparent;
  border-color: transparent !important;
  box-shadow: none;
  transform: none;
}

.composer-attachment-input {
  display: none;
}

.input-composer textarea {
  min-height: 120px;
  /* UI2 第三批③：长高由 autoGrowComposer 定量控制，这里只保留初始态 */
  overflow-y: hidden;
  border: 0;
  outline: 0;
  resize: none;
  background: transparent;
  color: var(--text-primary);
  font-size: 17px;
  font-weight: 400;
  line-height: 1.7;
  padding: 0;
  width: 100%;
  display: block;
}

.input-composer textarea::placeholder {
  color: var(--text-secondary);
}

.input-composer-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.composer-toolbar-left,
.composer-toolbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.composer-action {
  width: 42px;
  height: 42px;
  display: grid;
  place-items: center;
  border-radius: 999px;
}

.composer-action-icon {
  display: inline-block;
  transition:
    transform 180ms ease,
    opacity 180ms ease;
}

.composer-action.primary {
  color: var(--text-secondary);
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.08), rgba(255, 255, 255, 0.04)),
    rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.08) !important;
}

.composer-action.primary:hover {
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.14), rgba(255, 255, 255, 0.07)),
    rgba(255, 255, 255, 0.08);
}

.composer-action.primary:hover .composer-action-icon {
  transform: translateY(-1px) scale(1.04);
}

.composer-action:disabled {
  opacity: 0.56;
  cursor: default;
}

/* UI2-①：停止按钮——与 Send 同位置同尺寸，方块图标表达“可中断” */
.composer-action.stop {
  background: rgba(94, 94, 94, 0.16);
  border: 1px solid rgba(94, 94, 94, 0.32) !important;
  color: #c3c3c3;
}

.composer-action.stop:hover {
  background: rgba(94, 94, 94, 0.28);
}

/* UI2-⑥：排队消息条（斜在输入框上方，可单条移除） */
.composer-queue {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.composer-queue-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.08);
  font-size: 13px;
  color: var(--text-secondary);
}

.composer-queue-text {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.composer-queue-item::before {
  content: '排队中';
  flex-shrink: 0;
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 999px;
  background: rgba(160, 160, 160, 0.16);
  color: rgb(186, 186, 186);
}

.composer-queue-remove {
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  border-radius: 999px;
  color: var(--text-secondary);
}

.composer-queue-remove:hover {
  background: rgba(255, 255, 255, 0.1);
  color: var(--text-primary);
}

.composer-tool {
  min-width: 32px;
  height: 32px;
  display: grid;
  place-items: center;
  padding: 0 8px;
  border-radius: 999px;
  color: var(--text-secondary);
  background: rgba(255, 255, 255, 0.02);
  border: 1px solid transparent !important;
}

.composer-tool:disabled {
  opacity: 0.5;
  cursor: default;
}

.composer-tool svg {
  width: 16px;
  height: 16px;
}

.composer-model-wrap {
  position: relative;
}

.composer-model-button {
  min-width: 72px;
  height: 40px;
  padding: 0 12px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.06), rgba(255, 255, 255, 0.028)),
    rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.06) !important;
  color: var(--text-secondary);
  transition:
    background-color 150ms ease-out,
    color 180ms ease;
}

.composer-model-button:hover {
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.11), rgba(255, 255, 255, 0.05)),
    rgba(255, 255, 255, 0.1) !important;
  color: var(--text-primary) !important;
  filter: none !important;
}

.composer-model-menu {
  position: absolute;
  right: 0;
  bottom: calc(100% + 10px);
  min-width: 220px;
  padding: 8px;
  border-radius: 18px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.035), rgba(255, 255, 255, 0.015)),
    rgba(11, 11, 11, 0.96);
  box-shadow: 0 18px 40px rgba(0, 0, 0, 0.28);
  z-index: 6;
}

.composer-model-option {
  width: 100%;
  padding: 10px 12px;
  border-radius: 14px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  color: var(--text-primary);
  background: transparent;
  transition:
    background 160ms ease,
    transform 160ms ease,
    opacity 160ms ease;
}

.composer-model-option:hover:not(.disabled) {
  background: rgba(255, 255, 255, 0.05);
  transform: translateY(-1px);
}

.composer-model-option.active {
  background: rgba(255, 255, 255, 0.06);
}

.composer-model-option.disabled {
  opacity: 0.42;
  cursor: default;
}

.composer-model-option-name {
  font-size: 13px;
}

.composer-model-option-meta {
  font-size: 11px;
  color: var(--text-secondary);
}

@media (max-width: 1320px) {
  .input-composer {
    padding: 14px 10px 10px 14px;
  }

  .input-composer textarea {
    min-height: 104px;
    font-size: 15px;
  }

  .composer-model-button {
    min-width: 64px;
  }
}
</style>
