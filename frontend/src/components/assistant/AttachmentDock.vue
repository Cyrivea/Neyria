<template>
  <div class="attachment-dock" aria-label="待发送附件">
    <div class="attachment-dock-scroll">
      <article
        v-for="attachment in chatStore.uploadedAttachments"
        :key="attachment.id"
        class="attachment-chip"
        :class="[`is-${attachment.kind}`]"
      >
        <button
          class="attachment-chip-remove"
          type="button"
          title="移除待发送附件"
          @click.stop="chatStore.removeUploadedAttachment(attachment.id)"
        >
          ×
        </button>

        <template v-if="attachment.kind === 'image' && attachment.previewUrl">
          <img class="attachment-chip-image" :src="attachment.previewUrl" :alt="attachment.name" />
          <div class="attachment-chip-image-caption">{{ attachment.name }}</div>
        </template>

        <template v-else>
          <div class="attachment-chip-icon" aria-hidden="true">
            <span>{{ attachment.badge }}</span>
          </div>
          <div class="attachment-chip-copy">
            <div class="attachment-chip-name">{{ attachment.name }}</div>
            <div class="attachment-chip-meta">{{ attachment.meta }}</div>
          </div>
        </template>
      </article>

      <button
        class="attachment-add-card"
        type="button"
        :disabled="chatStore.isUploadingAttachment || chatStore.isSending"
        @click="emit('add')"
      >
        <span class="attachment-add-icon" aria-hidden="true">+</span>
        <span class="attachment-add-copy">
          {{ chatStore.isUploadingAttachment ? '发送中...' : '继续添加' }}
        </span>
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
// 待发送附件坞（D1 拆分第 8 步，todo §8.4）：展示 + 移除/继续添加操作。
import { useChatStore } from '@/stores/chat'

const emit = defineEmits<{ (e: 'add'): void }>()

const chatStore = useChatStore()
</script>

<style scoped>
.attachment-dock {
  position: absolute;
  top: 0;
  left: 14px;
  right: 14px;
  transform: translateY(calc(-100% - 10px));
  z-index: 3;
  pointer-events: none;
}

.attachment-dock-scroll {
  display: flex;
  align-items: stretch;
  gap: 8px;
  max-width: 100%;
  overflow-x: auto;
  padding-bottom: 4px;
  scrollbar-width: none;
  pointer-events: auto;
}

.attachment-dock-scroll::-webkit-scrollbar {
  display: none;
}

.attachment-chip,
.attachment-add-card {
  flex: 0 0 auto;
  min-height: 74px;
  border-radius: 18px;
  border: 1px solid rgba(255, 255, 255, 0.08) !important;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.028), rgba(255, 255, 255, 0.014)), #111112;
}

.attachment-chip {
  min-width: 146px;
  max-width: 178px;
  padding: 10px 12px;
  display: flex;
  align-items: center;
  gap: 10px;
  position: relative;
  overflow: hidden;
  transition:
    transform 220ms cubic-bezier(0.22, 1, 0.36, 1),
    opacity 180ms ease,
    border-color 180ms ease,
    background 180ms ease;
}

.attachment-chip.is-image {
  min-width: 92px;
  max-width: 92px;
  padding: 0;
  align-items: stretch;
  justify-content: stretch;
}

.attachment-chip.is-removing {
  opacity: 0.58;
}

.attachment-chip-remove {
  position: absolute;
  top: 6px;
  right: 6px;
  width: 20px;
  height: 20px;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  z-index: 2;
  color: rgba(255, 255, 255, 0.72) !important;
  background: rgba(0, 0, 0, 0.45) !important;
}

.attachment-chip-remove:disabled {
  cursor: default;
  opacity: 0.56;
}

.attachment-chip-image {
  width: 100%;
  height: 100%;
  object-fit: cover;
  /* 附件里的图是内容，不是 UI——不吃单色滤镜（见 CLAUDE.md 单色法修订） */
}

.attachment-chip-image-caption {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  padding: 22px 8px 8px;
  color: rgba(255, 255, 255, 0.9);
  font-size: 10px;
  line-height: 1.35;
  background: linear-gradient(180deg, transparent, rgba(0, 0, 0, 0.92));
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.attachment-chip-icon {
  width: 42px;
  height: 52px;
  border-radius: 12px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.08), rgba(255, 255, 255, 0.03)),
    rgba(255, 255, 255, 0.03);
  position: relative;
  display: inline-flex;
  align-items: flex-end;
  justify-content: center;
  padding-bottom: 9px;
  color: rgba(255, 255, 255, 0.88);
  font-size: 10px;
  letter-spacing: 0.08em;
}

.attachment-chip-icon::after {
  content: '';
  position: absolute;
  top: -1px;
  right: -1px;
  width: 14px;
  height: 14px;
  border-left: 1px solid rgba(255, 255, 255, 0.1);
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
  background: rgba(255, 255, 255, 0.06);
  clip-path: polygon(0 0, 100% 100%, 0 100%);
}

.attachment-chip-copy {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.attachment-chip-name,
.attachment-chip-meta {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
}

.attachment-chip-name {
  color: var(--text-primary);
  font-size: 12px;
  line-height: 1.45;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  white-space: normal;
}

.attachment-chip-meta {
  color: var(--text-secondary);
  font-size: 11px;
  white-space: nowrap;
}

.attachment-add-card {
  min-width: 92px;
  padding: 10px 12px;
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  color: var(--text-secondary);
}

.attachment-add-icon {
  font-size: 22px;
  line-height: 1;
  color: rgba(255, 255, 255, 0.9);
}

.attachment-add-copy {
  font-size: 11px;
  line-height: 1.4;
}

@media (max-width: 1320px) {
  .attachment-dock {
    left: 12px;
    right: 12px;
  }

  .attachment-chip {
    min-width: 132px;
    max-width: 160px;
  }

  .attachment-chip.is-image,
  .attachment-add-card {
    min-width: 84px;
    max-width: 84px;
  }
}

/* 进出场过渡：必须声明在 .attachment-dock 静态 transform 之后以便同优先级覆盖 */
.attachment-dock-float-enter-active,
.attachment-dock-float-leave-active {
  transition:
    opacity 260ms cubic-bezier(0.16, 1, 0.3, 1),
    transform 260ms cubic-bezier(0.16, 1, 0.3, 1),
    filter 260ms cubic-bezier(0.16, 1, 0.3, 1);
}

.attachment-dock-float-enter-from,
.attachment-dock-float-leave-to {
  opacity: 0;
  transform: translateY(calc(-100% - 2px)) scale(0.985);
  filter: blur(6px);
}

.attachment-dock-float-enter-to,
.attachment-dock-float-leave-from {
  opacity: 1;
  transform: translateY(calc(-100% - 10px)) scale(1);
  filter: blur(0);
}
</style>
