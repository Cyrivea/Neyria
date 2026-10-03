<template>
  <!-- eslint-disable-next-line vue/no-v-html -->
  <div class="md-body" @click="handleBlockClick" v-html="html"></div>
</template>

<script setup lang="ts">
// 助手消息富文本出口（UI1）：唯一做了消毒的 v-html 渲染点；
// 代码块复制按钮通过事件委托处理，图标态反馈沿用操作栏语言。
import { computed } from 'vue'
import { renderMarkdown } from '@/utils/markdown'
import 'highlight.js/styles/github-dark.css'

const props = defineProps<{ content: string }>()

const html = computed(() => renderMarkdown(props.content))

async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    // 非安全上下文/权限被拒时的退路：隐藏 textarea + execCommand
    try {
      const area = document.createElement('textarea')
      area.value = text
      area.style.position = 'fixed'
      area.style.opacity = '0'
      document.body.appendChild(area)
      area.select()
      const ok = document.execCommand('copy')
      area.remove()
      return ok
    } catch {
      return false
    }
  }
}

async function handleBlockClick(event: MouseEvent) {
  const target = event.target as HTMLElement | null
  const button = target?.closest('.md-code-copy')
  if (!button) return
  const code = button.closest('.md-codeblock')?.querySelector('code')
  if (!code || button.classList.contains('is-copied')) return

  const ok = await copyText(code.textContent ?? '')
  if (!ok) return
  button.classList.add('is-copied')
  button.textContent = '✓ 已复制'
  window.setTimeout(() => {
    button.classList.remove('is-copied')
    button.textContent = '复制'
  }, 1500)
}
</script>

<style scoped>
/* v-html 内容不参与 scoped 属性选择器，内部样式统一走 :deep() 围栏 */

.md-body :deep(> :first-child) {
  margin-top: 0;
}

.md-body :deep(> :last-child) {
  margin-bottom: 0;
}

.md-body :deep(p) {
  margin: 0 0 10px;
  line-height: 1.7;
}

.md-body :deep(h1),
.md-body :deep(h2),
.md-body :deep(h3),
.md-body :deep(h4) {
  margin: 14px 0 8px;
  line-height: 1.4;
  font-weight: 600;
}

.md-body :deep(h1) {
  font-size: 17px;
}

.md-body :deep(h2) {
  font-size: 15.5px;
}

.md-body :deep(h3),
.md-body :deep(h4) {
  font-size: 14.5px;
}

/* 全局 * { padding: 0 } 会吞掉列表缩进，这里补回来 */
.md-body :deep(ul),
.md-body :deep(ol) {
  margin: 6px 0 10px;
  padding-left: 22px;
}

.md-body :deep(li) {
  margin: 3px 0;
  line-height: 1.65;
}

.md-body :deep(li::marker) {
  color: rgba(255, 255, 255, 0.45);
}

.md-body :deep(a) {
  color: var(--accent-mist-strong, #b4b4b4);
  text-decoration: none;
  border-bottom: 1px solid rgba(180, 180, 180, 0.35);
}

.md-body :deep(a:hover) {
  border-bottom-color: currentColor;
}

.md-body :deep(strong) {
  font-weight: 600;
  color: #fff;
}

.md-body :deep(code:not(pre code)) {
  padding: 2px 6px;
  border-radius: 6px;
  font-size: 12.5px;
  background: rgba(255, 255, 255, 0.08);
  border: 1px solid rgba(255, 255, 255, 0.06);
}

.md-body :deep(blockquote) {
  margin: 8px 0 12px;
  padding: 4px 12px;
  border-left: 2px solid rgba(180, 180, 180, 0.45);
  color: var(--text-secondary);
}

.md-body :deep(hr) {
  margin: 12px 0;
  border: none;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}

.md-body :deep(table) {
  margin: 8px 0 12px;
  border-collapse: collapse;
  font-size: 13px;
  width: 100%;
}

.md-body :deep(th),
.md-body :deep(td) {
  padding: 6px 10px;
  border: 1px solid rgba(255, 255, 255, 0.09);
  text-align: left;
}

.md-body :deep(th) {
  background: rgba(255, 255, 255, 0.04);
}

/* ------- 代码块：语言标签 + 复制按钮 ------- */
.md-body :deep(.md-codeblock) {
  margin: 10px 0 14px;
  border-radius: 12px;
  overflow: hidden;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: #0d1117;
}

.md-body :deep(.md-codeblock-head) {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 6px 12px;
  background: rgba(255, 255, 255, 0.04);
  border-bottom: 1px solid rgba(255, 255, 255, 0.07);
}

.md-body :deep(.md-codeblock-lang) {
  font-size: 11px;
  letter-spacing: 0.06em;
  text-transform: lowercase;
  color: rgba(255, 255, 255, 0.5);
}

.md-body :deep(.md-code-copy) {
  font-size: 11px;
  color: rgba(255, 255, 255, 0.62);
  padding: 2px 8px;
  border-radius: 6px;
  background: transparent;
  border: 1px solid transparent;
  cursor: pointer;
  transition: all 150ms ease;
}

.md-body :deep(.md-code-copy:hover) {
  color: #fff;
  background: rgba(255, 255, 255, 0.08);
}

.md-body :deep(.md-code-copy.is-copied) {
  color: #c3c3c3;
}

.md-body :deep(.md-codeblock pre) {
  margin: 0;
  padding: 12px 14px;
  overflow-x: auto;
  font-size: 12.5px;
  line-height: 1.6;
  scrollbar-width: thin;
}

.md-body :deep(.md-codeblock pre code) {
  font-family: ui-monospace, 'SF Mono', 'Cascadia Mono', Consolas, monospace;
  background: transparent;
  border: none;
  padding: 0;
}
</style>
