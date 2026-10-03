<template>
  <!-- UI2 第二批④：⌘K/Ctrl+K 命令面板（open-webui 快捷键面板设计） -->
  <Teleport to="body">
    <Transition name="panel-float">
      <div v-if="open" class="palette-overlay" @click.self="emit('close')">
        <div class="palette-box" role="dialog" aria-label="命令面板">
          <input
            ref="inputRef"
            v-model="query"
            class="palette-input"
            type="text"
            placeholder="输入命令或功能名称……"
            @keydown="handleKeydown"
          />
          <div v-if="filtered.length" class="palette-list" role="listbox">
            <button
              v-for="(command, index) in filtered"
              :key="command.id"
              type="button"
              class="palette-item"
              :class="{ active: index === activeIndex }"
              role="option"
              :aria-selected="index === activeIndex"
              @mouseenter="activeIndex = index"
              @click="pick(command)"
            >
              <span class="palette-item-title">{{ command.title }}</span>
              <span v-if="command.meta" class="palette-item-meta">{{ command.meta }}</span>
            </button>
          </div>
          <div v-else class="palette-empty">没有匹配的命令</div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { filterPaletteCommands, type PaletteCommand } from '@/utils/palette'

const props = defineProps<{
  open: boolean
  commands: PaletteCommand[]
}>()

const emit = defineEmits<{
  (e: 'close'): void
  (e: 'select', command: PaletteCommand): void
}>()

const query = ref('')
const activeIndex = ref(0)
const inputRef = ref<HTMLInputElement | null>(null)

const filtered = computed(() => filterPaletteCommands(props.commands, query.value))

watch(
  () => props.open,
  async (open) => {
    if (!open) return
    query.value = ''
    activeIndex.value = 0
    await nextTick()
    inputRef.value?.focus()
  },
)

// 查询改变后回到第一条，避免列表缩短后 activeIndex 悬空
watch(query, () => {
  activeIndex.value = 0
})

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'ArrowDown') {
    event.preventDefault()
    activeIndex.value = Math.min(activeIndex.value + 1, filtered.value.length - 1)
  } else if (event.key === 'ArrowUp') {
    event.preventDefault()
    activeIndex.value = Math.max(activeIndex.value - 1, 0)
  } else if (event.key === 'Enter') {
    event.preventDefault()
    const command = filtered.value[activeIndex.value]
    if (command) pick(command)
  } else if (event.key === 'Escape') {
    emit('close')
  }
}

function pick(command: PaletteCommand) {
  emit('select', command)
  emit('close')
}
</script>

<style scoped>
.palette-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  justify-content: center;
  padding-top: 14vh;
  background: rgba(8, 10, 18, 0.5);
  backdrop-filter: blur(2px);
}

.palette-box {
  width: min(520px, 92vw);
  height: min-content;
  border-radius: 16px;
  background: rgba(22, 24, 36, 0.96);
  border: 1px solid rgba(255, 255, 255, 0.1);
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.5);
  overflow: hidden;
}

.palette-input {
  width: 100%;
  padding: 14px 16px;
  border: 0;
  outline: 0;
  background: transparent;
  color: var(--text-primary);
  font-size: 15px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.palette-list {
  max-height: 46vh;
  overflow-y: auto;
  padding: 6px;
}

.palette-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  width: 100%;
  text-align: left;
  padding: 10px 12px;
  border-radius: 10px;
  color: var(--text-primary);
  background: transparent;
  cursor: pointer;
}

.palette-item.active {
  background: rgba(160, 160, 160, 0.16);
}

.palette-item-title {
  font-size: 14px;
}

.palette-item-meta {
  font-size: 12px;
  color: var(--text-secondary);
}

.palette-empty {
  padding: 24px;
  text-align: center;
  color: var(--text-secondary);
  font-size: 13px;
}
</style>
