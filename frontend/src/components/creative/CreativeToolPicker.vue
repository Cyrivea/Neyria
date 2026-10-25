<template>
  <Transition name="modal-fade">
    <div v-if="open" class="modal-overlay creative-tools-overlay" @click.self="close">
      <section
        class="creative-tools-picker"
        role="dialog"
        aria-modal="true"
        aria-labelledby="creativeToolsTitle"
      >
        <header class="creative-tools-picker-head">
          <div>
            <div class="creative-tools-kicker">CREATIVE BLOCKS</div>
            <h2 id="creativeToolsTitle">添加创作工具</h2>
            <p>选择一个创作 Block 加入当前工作站。相同工具可以重复添加。</p>
          </div>
          <button class="creative-tools-close" type="button" aria-label="关闭工具库" @click="close">
            ✕
          </button>
        </header>

        <div class="creative-tools-grid">
          <button
            v-for="tool in creativeToolCatalog"
            :key="tool.type"
            class="creative-tool-choice"
            type="button"
            @click="add(tool.type)"
          >
            <div class="creative-tool-art" :style="{ '--tool-color': tool.color }">
              <span class="creative-tool-art-mark" aria-hidden="true">{{
                artMark(tool.type)
              }}</span>
              <span class="creative-tool-art-line"></span>
              <span class="creative-tool-art-line short"></span>
            </div>
            <div class="creative-tool-choice-copy">
              <div class="creative-tool-choice-title">{{ tool.name }}</div>
              <div class="creative-tool-choice-badge">{{ tool.badge }}</div>
              <div class="creative-tool-choice-description">{{ tool.description }}</div>
              <div class="creative-tool-choice-action">添加到工作站 <span>→</span></div>
            </div>
          </button>
        </div>
      </section>
    </div>
  </Transition>
</template>

<script setup lang="ts">
import { onBeforeUnmount, watch } from 'vue'
import {
  creativeToolCatalog,
  useCreativeToolsStore,
  type CreativeToolType,
} from '@/stores/creativeTools'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{
  (event: 'close'): void
  (event: 'added', id: string): void
}>()

const creativeToolsStore = useCreativeToolsStore()

function close() {
  emit('close')
}

function add(type: CreativeToolType) {
  const instance = creativeToolsStore.addTool(type)
  emit('added', instance.id)
  close()
}

function artMark(type: CreativeToolType) {
  return { lyrics: 'Aa', image: '✦', video: '▶', audio: '∿' }[type]
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
.creative-tools-overlay {
  padding: 30px;
}

.creative-tools-picker {
  width: min(900px, 100%);
  max-height: min(760px, calc(100vh - 60px));
  overflow: auto;
  padding: 28px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 18px;
  background: #141414;
}

.creative-tools-picker-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 24px;
  margin-bottom: 24px;
}

.creative-tools-kicker {
  color: #888;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.16em;
}

.creative-tools-picker h2 {
  margin: 7px 0 4px;
  color: #eeeeee;
  font-size: 24px;
  font-weight: 600;
  letter-spacing: -0.03em;
}

.creative-tools-picker p {
  margin: 0;
  color: #7e7e7e;
  font-size: 12px;
}

.creative-tools-close {
  width: 30px;
  height: 30px;
  border: 1px solid rgba(255, 255, 255, 0.12) !important;
  border-radius: 7px;
  color: #999 !important;
  font-size: 13px;
}

.creative-tools-close:hover {
  background: rgba(255, 255, 255, 0.07) !important;
  color: #fff !important;
}

.creative-tools-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.creative-tool-choice {
  display: grid;
  grid-template-columns: 132px minmax(0, 1fr);
  gap: 16px;
  min-width: 0;
  padding: 12px;
  border: 1px solid rgba(255, 255, 255, 0.09) !important;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.025) !important;
  text-align: left;
}

.creative-tool-choice:hover {
  border-color: color-mix(in srgb, var(--tool-color) 50%, transparent) !important;
  background: rgba(255, 255, 255, 0.055) !important;
}

.creative-tool-art {
  position: relative;
  display: flex;
  min-height: 142px;
  flex-direction: column;
  justify-content: center;
  gap: 7px;
  overflow: hidden;
  padding: 16px;
  border-radius: 9px;
  background:
    radial-gradient(
      circle at 80% 20%,
      color-mix(in srgb, var(--tool-color) 26%, transparent),
      transparent 44%
    ),
    #202020;
  color: var(--tool-color);
  filter: grayscale(1);
}

.creative-tool-art::after {
  position: absolute;
  right: -15px;
  bottom: -28px;
  width: 110px;
  height: 110px;
  border: 1px solid color-mix(in srgb, var(--tool-color) 40%, transparent);
  border-radius: 50%;
  content: '';
}

.creative-tool-art-mark {
  position: relative;
  z-index: 1;
  font-size: 38px;
  font-weight: 500;
  line-height: 1;
}

.creative-tool-art-line {
  position: relative;
  z-index: 1;
  width: 74px;
  height: 3px;
  border-radius: 99px;
  background: currentColor;
  opacity: 0.72;
}

.creative-tool-art-line.short {
  width: 42px;
  opacity: 0.35;
}

.creative-tool-choice-copy {
  display: flex;
  min-width: 0;
  flex-direction: column;
  padding: 3px 2px 2px 0;
}

.creative-tool-choice-title {
  color: #ededed;
  font-size: 16px;
  font-weight: 600;
}

.creative-tool-choice-badge {
  width: fit-content;
  margin-top: 7px;
  padding: 3px 6px;
  border-radius: 4px;
  background: rgba(255, 255, 255, 0.08);
  color: #a3a3a3;
  font-size: 10px;
}

.creative-tool-choice-description {
  margin-top: 12px;
  color: #858585;
  font-size: 11px;
  line-height: 1.6;
}

.creative-tool-choice-action {
  display: flex;
  justify-content: space-between;
  margin-top: auto;
  padding-top: 14px;
  color: #c2c2c2;
  font-size: 11px;
}

.creative-tool-choice-action span {
  color: #888;
  font-size: 15px;
  line-height: 1;
}

@media (max-width: 760px) {
  .creative-tools-overlay {
    padding: 16px;
  }

  .creative-tools-picker {
    padding: 18px;
  }

  .creative-tools-grid {
    grid-template-columns: 1fr;
  }
}
</style>
