<template>
  <div
    class="assistant-panel"
    @dragenter="handleDragEnter"
    @dragover="handleDragOver"
    @dragleave="handleDragLeave"
    @drop="handleDrop"
  >
    <!-- UI2 第二批②：拖文件进对话区时的提示覆盖层（open-webui dropzone 设计） -->
    <Transition name="panel-float">
      <div v-if="isDragOver" class="assistant-drop-overlay" aria-hidden="true">
        <div class="assistant-drop-box">松手把文件放进对话附件位</div>
      </div>
    </Transition>
    <div class="assistant-head">
      <div class="assistant-head-top">
        <div class="assistant-head-copy">
          <div class="assistant-title-row">
            <div class="assistant-title">Neyria</div>
            <div class="assistant-presence" aria-hidden="true"></div>
          </div>
          <div class="assistant-meta">为音乐续写篇章</div>
        </div>
        <div class="window-controls" aria-hidden="true">
          <div class="window-dot"></div>
          <div class="window-dot"></div>
          <div class="window-dot"></div>
        </div>
      </div>
      <div class="assistant-head-divider" aria-hidden="true"></div>
    </div>

    <div
      ref="assistantBodyRef"
      class="assistant-body"
      :class="{
        'has-floating-composer': true,
        'has-attachment-dock': chatStore.hasUploadedAttachments,
      }"
      @scroll="handleBodyScroll"
    >
      <MessageList @regenerate="handleRegenerate" @edit="handleEdit" @feedback="handleFeedback" />

      <div v-if="chatStore.isSending" class="agent-status-line">
        <span class="agent-status-pulse" aria-hidden="true"></span>
        {{ chatStore.thinkingStatusText }}
      </div>
    </div>

    <div
      class="assistant-composer-underlay"
      :class="{ 'has-attachment-dock': chatStore.hasUploadedAttachments }"
      aria-hidden="true"
    ></div>

    <!-- UI2 第二批⑤：离开底部足够远时浮沉一个回底按钮（open-webui 设计） -->
    <Transition name="panel-float">
      <button
        v-if="scrolledAwayFromBottom"
        type="button"
        class="agent-back-to-bottom"
        title="回到最新消息"
        @click="scrollToBottom"
      >
        ↓ 回到底部
      </button>
    </Transition>

    <div class="composer-wrap" :class="{ 'has-attachment-dock': chatStore.hasUploadedAttachments }">
      <ChatComposer @send="handleSend" @stop="handleStop" @attach="handleAttach" />
    </div>
  </div>
</template>

<script setup lang="ts">
// 助手面板（D1 拆分第 10 步，todo §8.4）：组合消息列表与输入区，
// 持有滚动容器，并实例化 useAgentChat 完成发送/加载/附件入队流程。
import { computed, nextTick, ref, watch } from 'vue'
import { useChatStore, type AgentMessage } from '@/stores/chat'
import { useAgentChat } from '@/composables/useAgentChat'
import MessageList from './MessageList.vue'
import ChatComposer from './ChatComposer.vue'

const chatStore = useChatStore()

const assistantBodyRef = ref<HTMLElement | null>(null)

async function scrollToBottom() {
  await nextTick()
  if (!assistantBodyRef.value) return
  assistantBodyRef.value.scrollTop = assistantBodyRef.value.scrollHeight
}

// UI2 第二批⑤：距底阈超过 80px 才认为“离开了底部”
const scrolledAwayFromBottom = ref(false)

function handleBodyScroll() {
  const el = assistantBodyRef.value
  if (!el) return
  scrolledAwayFromBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight > 80
}

const {
  sendMessage,
  loadHistory,
  regenerateLastMessage,
  stopGenerating,
  applyEditedMessage,
  submitFeedback,
  addAttachmentFiles,
} = useAgentChat({ scrollToBottom })

// UI2 第二批②：拖拽判定（depth 计数——拖过子元素也会触发 leave，只计最后真正出离面板）
const dragDepth = ref(0)
const isDragOver = computed(() => dragDepth.value > 0)

function dragHasFiles(event: DragEvent) {
  return Array.from(event.dataTransfer?.types ?? []).includes('Files')
}

function handleDragEnter(event: DragEvent) {
  if (!dragHasFiles(event)) return
  event.preventDefault()
  dragDepth.value += 1
}

function handleDragOver(event: DragEvent) {
  if (dragHasFiles(event)) event.preventDefault() // 不 prevent 就不会触发 drop
}

function handleDragLeave() {
  dragDepth.value = Math.max(0, dragDepth.value - 1)
}

function handleDrop(event: DragEvent) {
  dragDepth.value = 0
  const files = Array.from(event.dataTransfer?.files ?? [])
  if (!files.length) return
  event.preventDefault()
  addAttachmentFiles(files)
}

function handleAttach(files: File[]) {
  addAttachmentFiles(files)
}

function handleSend() {
  void sendMessage()
}

function handleStop() {
  stopGenerating()
}

function handleRegenerate() {
  void regenerateLastMessage()
}

function handleEdit(payload: { message: AgentMessage; text: string }) {
  void applyEditedMessage(payload.message, payload.text)
}

function handleFeedback(payload: { message: AgentMessage; value: 1 | -1 }) {
  void submitFeedback(payload.message, payload.value)
}

// 历史被整体替换（加载 / done 重建）时保持滚动到底部
watch(
  () => chatStore.messages.length,
  () => {
    void scrollToBottom()
  },
)

defineExpose({ loadHistory, scrollToBottom })
</script>

<style scoped>
/* UI2 第二批⑤：回底浮沉按钮 */
.agent-back-to-bottom {
  position: absolute;
  right: 18px;
  bottom: 150px; /* 浮在 composer 上方，不挡文字 */
  z-index: 30;
  padding: 6px 14px;
  border-radius: 999px;
  font-size: 12px;
  color: rgb(208, 208, 208);
  background: rgba(28, 28, 28, 0.94);
  border: 1px solid rgba(160, 160, 160, 0.3);
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.35);
  cursor: pointer;
}

.agent-back-to-bottom:hover {
  background: rgba(42, 42, 42, 0.96);
}

/* UI2 第二批②：拖拽覆盖层 */
.assistant-drop-overlay {
  position: absolute;
  inset: 0;
  z-index: 40;
  display: grid;
  place-items: center;
  background: rgba(14, 14, 14, 0.62);
  pointer-events: none; /* 覆盖层不吃事件，否则 drop 会被它劫走 */
}

.assistant-drop-box {
  padding: 18px 34px;
  border: 1.5px dashed rgba(186, 186, 186, 0.6);
  border-radius: 16px;
  color: rgb(208, 208, 208);
  font-size: 14px;
  background: rgba(160, 160, 160, 0.08);
}

.assistant-panel {
  height: 100%;
  min-height: 0;
  position: relative;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.014), rgba(255, 255, 255, 0)), var(--column-bg);
  overflow: hidden;
}

.assistant-head {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 18px 0 10px;
  background: var(--column-bg);
}

.assistant-head-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 0 14px 0 18px;
}

.assistant-head-copy {
  flex: 1;
  min-width: 0;
}

.assistant-title-row {
  display: inline-flex;
  align-items: center;
  gap: 10px;
}

.assistant-title {
  font-size: 20px;
  letter-spacing: -0.02em;
}

.assistant-presence {
  width: 7px;
  height: 7px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.72);
  box-shadow: 0 0 0 4px rgba(255, 255, 255, 0.04);
  animation: assistant-presence-pulse 2.6s ease-in-out infinite;
}

.assistant-meta {
  font-size: 14px;
  color: var(--text-secondary);
  margin-top: 4px;
}

.assistant-head-divider {
  width: auto;
  margin: 0 6px;
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.1), transparent);
}

.assistant-body {
  min-height: 0;
  height: 100%;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 14px 14px 10px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  background: var(--column-bg);
  scroll-behavior: smooth;
  scrollbar-gutter: stable;
  scrollbar-width: thin;
  scrollbar-color: rgba(255, 255, 255, 0.22) transparent;
  overscroll-behavior: contain;
  -webkit-overflow-scrolling: touch;
  touch-action: pan-y;
  position: relative;
  z-index: 1;
}

.assistant-body.has-floating-composer {
  padding-bottom: 234px;
}

.assistant-body.has-floating-composer.has-attachment-dock {
  padding-bottom: 332px;
}

.assistant-body::-webkit-scrollbar {
  width: 8px;
}

.assistant-body::-webkit-scrollbar-track {
  background: transparent;
}

.assistant-body::-webkit-scrollbar-thumb {
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.16);
}

.assistant-body::-webkit-scrollbar-thumb:hover {
  background: rgba(255, 255, 255, 0.24);
}

.agent-status-line {
  color: var(--text-secondary);
  font-size: 12px;
  position: sticky;
  bottom: 0;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  width: fit-content;
  margin: 4px 0 0;
  padding: 8px 10px 2px 4px;
  color: rgba(255, 255, 255, 0.68);
  background: linear-gradient(
    180deg,
    transparent,
    rgba(11, 11, 11, 0.92) 32%,
    rgba(11, 11, 11, 0.98)
  );
  pointer-events: none;
  z-index: 1;
}

.agent-status-pulse {
  width: 6px;
  height: 6px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.76);
  animation: agent-status-pulse 1.6s ease-in-out infinite;
}

.assistant-composer-underlay {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 248px;
  background: var(--column-bg);
  z-index: 2;
  pointer-events: none;
}

.assistant-composer-underlay.has-attachment-dock {
  height: 346px;
}

.assistant-composer-underlay::before {
  content: '';
  position: absolute;
  left: 0;
  right: 0;
  top: -44px;
  height: 44px;
  background: linear-gradient(
    180deg,
    rgba(4, 4, 4, 0),
    var(--column-bg) 82%,
    var(--column-bg) 100%
  );
}

.composer-wrap {
  position: absolute;
  left: 8px;
  right: 8px;
  bottom: 8px;
  padding: 18px 18px 14px;
  border: 1px solid rgba(255, 255, 255, 0.08) !important;
  border-radius: 28px;
  background: linear-gradient(180deg, #171717, #111111 72%, #0b0b0b 100%);
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow: visible;
  transition:
    padding-top 280ms cubic-bezier(0.22, 1, 0.36, 1),
    background 180ms ease,
    border-color 180ms ease,
    box-shadow 220ms cubic-bezier(0.22, 1, 0.36, 1);
  box-shadow:
    0 -18px 48px rgba(0, 0, 0, 0.28),
    inset 0 1px 0 rgba(255, 255, 255, 0.03);
  z-index: 3;
  pointer-events: none;
}

.composer-wrap::before {
  display: none;
}

.composer-wrap.has-attachment-dock {
  padding-top: 106px;
}

@keyframes assistant-presence-pulse {
  0%,
  100% {
    opacity: 0.55;
    transform: scale(1);
  }

  50% {
    opacity: 1;
    transform: scale(1.08);
  }
}

@keyframes agent-status-pulse {
  0%,
  100% {
    opacity: 0.25;
    transform: scale(1);
  }

  50% {
    opacity: 1;
    transform: scale(1.2);
  }
}

@media (max-width: 1480px) {
  .assistant-head-top {
    padding-inline: 12px;
  }

  .assistant-body {
    padding-inline: 12px;
  }

  .composer-wrap {
    left: 6px;
    right: 6px;
    bottom: 6px;
    padding: 16px 16px 12px;
  }

  .assistant-body.has-floating-composer {
    padding-bottom: 224px;
  }

  .assistant-body.has-floating-composer.has-attachment-dock {
    padding-bottom: 320px;
  }

  .assistant-composer-underlay {
    height: 238px;
  }

  .assistant-composer-underlay.has-attachment-dock {
    height: 336px;
  }

  .composer-wrap.has-attachment-dock {
    padding-top: 102px;
  }
}

@media (max-width: 1320px) {
  .assistant-title {
    font-size: 18px;
  }

  .assistant-meta {
    font-size: 13px;
  }

  .agent-status-line {
    font-size: 11px;
  }
}
</style>
