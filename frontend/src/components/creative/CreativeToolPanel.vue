<template>
  <div class="panel-layer" :class="{ 'is-active': tool }">
    <Transition name="tool-config" mode="out-in">
      <section
        v-if="tool"
        ref="panelRef"
        :key="tool.id"
        class="panel"
        role="dialog"
        :aria-label="`${tool.name}参数面板`"
        @click.stop
      >
        <header class="panel-head">
          <div class="panel-title">
            <span class="panel-mark" :style="{ color: tool.color }">{{ artMark(tool.type) }}</span>
            <div class="panel-title-copy">
              <div class="panel-eyebrow">{{ tool.badge }}</div>
              <h2>{{ tool.name }}</h2>
              <p>{{ tool.description }}</p>
            </div>
          </div>
          <button type="button" class="panel-close" aria-label="关闭参数面板" @click="saveAndClose">
            ✕
          </button>
        </header>

        <div class="panel-body" :class="{ 'is-running-locked': workflowStore.isRunning }">
          <div v-if="workflowStore.isRunning" class="panel-running-lock" role="status">
            Workflow 运行中，参数暂不可修改，运行结束后自动恢复。
          </div>
          <section class="block">
            <header class="block-head">
              <span>主要输入</span>
              <small>来自输入节点或上一步结果</small>
            </header>
            <div class="main-input-card">
              <span class="port-mark" aria-hidden="true">⇥</span>
              <div class="main-input-copy">
                <strong>{{ mainInputLabel }}</strong>
                <small>{{ mainInputDetail }} · {{ mainInputHint(tool.type) }}</small>
              </div>
              <span class="input-status" :class="{ 'is-linked': mainInputSource }">
                {{ mainInputSource ? '已连接' : '等待连接' }}
              </span>
            </div>
          </section>

          <section class="block">
            <header class="block-head">
              <span>补充参考</span>
              <small>可选 · 不替换主要输入</small>
            </header>

            <div v-if="references.length" class="reference-list">
              <div v-for="reference in references" :key="reference.id" class="reference-item">
                <span class="reference-type" aria-hidden="true">{{
                  reference.type === 'image' ? '▧' : 'Aa'
                }}</span>
                <span class="reference-copy">
                  <strong>{{ reference.name }}</strong>
                  <small>{{ reference.detail ?? '用户补充参考' }}</small>
                </span>
                <button
                  type="button"
                  class="reference-remove"
                  :aria-label="`移除${reference.name}`"
                  @click="removeReference(reference.id)"
                >
                  ×
                </button>
              </div>
            </div>
            <div v-else class="reference-empty">暂无参考，生成时可只使用主要输入。</div>

            <div class="reference-actions">
              <label class="ghost-btn">
                <input
                  type="file"
                  accept="image/*,.txt,.md,.pdf,.wav,.mp3"
                  @change="handleFileChange"
                />
                <span>＋ 图片 / 文件</span>
              </label>
              <button
                type="button"
                class="ghost-btn"
                @click="textReferenceOpen = !textReferenceOpen"
              >
                ＋ 文本参考
              </button>
            </div>

            <div v-if="textReferenceOpen" class="reference-form">
              <textarea
                v-model="textReference"
                rows="2"
                placeholder="输入风格、限制或画面补充说明"
              ></textarea>
              <button type="button" class="btn-secondary btn-small" @click="addTextReference">
                添加
              </button>
            </div>
          </section>

          <section class="block">
            <header class="block-head">
              <span>生成参数</span>
              <small>{{ tool.inputHint }}</small>
            </header>

            <template v-if="tool.type === 'lyrics'">
              <div class="field">
                <span class="field-label">创作主题</span>
                <textarea
                  class="field-control field-textarea"
                  :value="tool.params.theme"
                  rows="3"
                  placeholder="输入创作主题，例如：漫步在夏夜的城市街头"
                  @input="update('theme', $event)"
                ></textarea>
              </div>
              <div class="field-row">
                <div class="field">
                  <span class="field-label">曲风</span>
                  <!-- 2026-10-25 创作自由化：枚举横不刑了——随便写，下拉只是推荐 -->
                  <input
                    class="field-control"
                    type="text"
                    list="lyrics-style-suggestions"
                    :value="tool.params.style"
                    placeholder="如：流行抒情、独立摇滚、古典交响…"
                    maxlength="60"
                    @input="update('style', $event)"
                  />
                  <datalist id="lyrics-style-suggestions">
                    <option value="流行抒情"></option>
                    <option value="城市民谣"></option>
                    <option value="电子流行"></option>
                    <option value="摇滚叙事"></option>
                  </datalist>
                </div>
                <div class="field">
                  <span class="field-label">语言</span>
                  <select
                    class="field-control field-select"
                    :value="tool.params.language"
                    @change="update('language', $event)"
                  >
                    <option>中文</option>
                    <option>英文</option>
                    <option>中英混合</option>
                  </select>
                </div>
              </div>
              <div class="field">
                <span class="field-label">情绪方向</span>
                <input
                  class="field-control"
                  type="text"
                  list="lyrics-mood-suggestions"
                  :value="tool.params.mood"
                  placeholder="如：温柔免丶、忧郁狂欢、孤独怀旧…"
                  maxlength="60"
                  @input="update('mood', $event)"
                />
                <datalist id="lyrics-mood-suggestions">
                  <option value="温柔、克制"></option>
                  <option value="明亮、轻快"></option>
                  <option value="忧郁、克制"></option>
                  <option value="热烈、昂扬"></option>
                  <option value="孤独、怀旧"></option>
                </datalist>
              </div>
            </template>

            <template v-else-if="tool.type === 'image'">
              <div class="field">
                <span class="field-label">画面描述</span>
                <textarea
                  class="field-control field-textarea"
                  :value="tool.params.prompt"
                  rows="4"
                  placeholder="输入画面描述，例如：雨夜霓虹下挂着工作灯的小店"
                  @input="update('prompt', $event)"
                ></textarea>
              </div>
              <div class="field-row">
                <div class="field">
                  <span class="field-label">视觉风格</span>
                  <input
                    class="field-control"
                    type="text"
                    list="image-style-suggestions"
                    :value="tool.params.style"
                    placeholder="如：电影概念艺术、抽象水墨、赛博朋克…"
                    maxlength="80"
                    @input="update('style', $event)"
                  />
                  <datalist id="image-style-suggestions">
                    <option value="电影概念艺术"></option>
                    <option value="二次元插画"></option>
                    <option value="写实摄影"></option>
                    <option value="复古胶片"></option>
                  </datalist>
                </div>
                <div class="field">
                  <span class="field-label">画面比例</span>
                  <select
                    class="field-control field-select"
                    :value="tool.params.ratio"
                    @change="update('ratio', $event)"
                  >
                    <option>16:9</option>
                    <option>1:1</option>
                    <option>9:16</option>
                    <option>4:3</option>
                  </select>
                </div>
              </div>
              <div class="field">
                <span class="field-label">色彩方向</span>
                <input
                  class="field-control"
                  type="text"
                  list="image-palette-suggestions"
                  :value="tool.params.palette"
                  placeholder="如：黑白、深蓝与紫色、暖金、酸橙…"
                  maxlength="80"
                  @input="update('palette', $event)"
                />
                <datalist id="image-palette-suggestions">
                  <option value="黑白"></option>
                  <option value="深蓝与紫色"></option>
                  <option value="暖金"></option>
                  <option value="低饱和绿色"></option>
                  <option value="高对比彩色"></option>
                </datalist>
              </div>
            </template>

            <template v-else-if="tool.type === 'video'">
              <div class="field">
                <span class="field-label">镜头描述</span>
                <textarea
                  class="field-control field-textarea"
                  :value="tool.params.prompt"
                  rows="4"
                  placeholder="描述镜头运动和画面节奏，例如：镜头缓慢推进穿过雨夜的城市街道"
                  @input="update('prompt', $event)"
                ></textarea>
              </div>
              <div class="field-row">
                <div class="field">
                  <span class="field-label">视觉风格</span>
                  <input
                    class="field-control"
                    type="text"
                    list="video-style-suggestions"
                    :value="tool.params.style"
                    placeholder="如：电影写实、二手拉机械功、二次元动画…"
                    maxlength="80"
                    @input="update('style', $event)"
                  />
                  <datalist id="video-style-suggestions">
                    <option value="电影写实"></option>
                    <option value="二次元动画"></option>
                    <option value="3D 渲染"></option>
                    <option value="复古胶片"></option>
                  </datalist>
                </div>
                <div class="field">
                  <span class="field-label">画面比例</span>
                  <select
                    class="field-control field-select"
                    :value="tool.params.ratio"
                    @change="update('ratio', $event)"
                  >
                    <option>16:9</option>
                    <option>1:1</option>
                    <option>9:16</option>
                  </select>
                </div>
              </div>
              <div class="field">
                <span class="field-label">时长</span>
                <select
                  class="field-control field-select"
                  :value="tool.params.duration"
                  @change="update('duration', $event)"
                >
                  <option>5 秒</option>
                  <option>10 秒</option>
                </select>
              </div>
            </template>

            <template v-else>
              <div class="field">
                <span class="field-label">输入音频</span>
                <div class="file-input-row">
                  <span class="file-input-name">{{ tool.params.source }}</span>
                  <button
                    type="button"
                    class="ghost-btn btn-inline"
                    @click="updateValue('source', 'demo-audio-input.wav')"
                  >
                    选择文件
                  </button>
                </div>
              </div>
              <div class="field">
                <span class="field-label">分析特征</span>
                <select
                  class="field-control field-select"
                  :value="tool.params.feature"
                  @change="update('feature', $event)"
                >
                  <option>频段与波形质感</option>
                  <option>节奏与动态</option>
                  <option>音色与空间感</option>
                </select>
              </div>
              <div class="field-row">
                <div class="field">
                  <span class="field-label">生成强度</span>
                  <select
                    class="field-control field-select"
                    :value="tool.params.intensity"
                    @change="update('intensity', $event)"
                  >
                    <option>轻微</option>
                    <option>中等</option>
                    <option>强烈</option>
                  </select>
                </div>
                <div class="field">
                  <span class="field-label">节奏处理</span>
                  <select
                    class="field-control field-select"
                    :value="tool.params.variation"
                    @change="update('variation', $event)"
                  >
                    <option>保留原始节奏</option>
                    <option>轻微变化</option>
                    <option>重新编排</option>
                  </select>
                </div>
              </div>
            </template>
          </section>

          <div class="output-note">
            <span class="port-mark" aria-hidden="true">⇥</span>
            <div>
              <strong>输出接口</strong>
              <small>生成结果写入当前节点，可被下游节点引用</small>
            </div>
          </div>

          <section v-if="candidates.length" class="block candidate-block">
            <header class="candidate-head">
              <span>候选版本</span>
              <small>手动选定一张，下游节点才会用它</small>
            </header>
            <div
              v-for="candidate in [...candidates].reverse()"
              :key="candidate.id"
              class="candidate-item"
              :class="{ 'is-selected': candidate.id === selectedCandidateId }"
            >
              <div class="candidate-top">
                <span class="candidate-status" :class="`is-${candidate.status}`">
                  {{ candidateStatusLabel(candidate.status) }}
                </span>
                <button
                  type="button"
                  class="candidate-select ghost-btn"
                  :disabled="candidate.id === selectedCandidateId"
                  @click="selectCandidate(candidate.id)"
                >
                  {{ candidate.id === selectedCandidateId ? '正在使用' : '用作下游输入' }}
                </button>
              </div>
              <pre class="candidate-snippet">{{
                candidate.content ? preview(candidate.content) : candidate.error || '未生成'
              }}</pre>
            </div>
          </section>

          <section
            v-if="runnableTool && toolRun?.status === 'succeeded' && tool.type === 'lyrics'"
            class="block result-block"
          >
            <header class="result-head">
              <span>歌词生成结果</span>
              <span class="result-meta">最新一次运行</span>
            </header>
            <pre class="result-content">{{ toolRun.content }}</pre>
          </section>
          <section
            v-else-if="runnableTool && toolRun?.status === 'succeeded' && tool.type === 'image'"
            class="block result-block"
          >
            <header class="result-head">
              <span>图像生成结果</span>
              <span class="result-meta">最新一次运行</span>
            </header>
            <img v-if="imageResultUrl" class="result-image" :src="imageResultUrl" alt="生成图" />
            <pre v-else class="result-content">{{ toolRun.content }}</pre>
          </section>
          <section
            v-else-if="runnableTool && toolRun?.status === 'succeeded' && tool.type === 'video'"
            class="block result-block"
          >
            <header class="result-head">
              <span>视频生成结果</span>
              <span class="result-meta">最新一次运行</span>
            </header>
            <video
              v-if="videoResultUrl"
              class="result-video"
              :src="videoResultUrl"
              controls
              preload="metadata"
            ></video>
            <pre v-else class="result-content">{{ toolRun.content }}</pre>
          </section>
          <section
            v-else-if="runnableTool && toolRun && toolRun.status !== 'running'"
            class="block error-block"
          >
            {{ toolRun.error ?? generationFailureText }}
          </section>
        </div>

        <footer class="panel-foot">
          <!-- WF1-③：块级运行按钮已下线——运行是画布级动作（顶部「运行工作流」带费用预览），
               避免同一链被重复点火、也挡不住局部运行与全局缓存语义的冲突。 -->
          <span class="foot-hint">参数会自动保存；点顶部「运行工作流」开始生成</span>
          <div class="foot-actions">
            <button type="button" class="btn-secondary" @click="saveAndClose">保存参数</button>
          </div>
        </footer>
      </section>
    </Transition>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from '@/utils/toast'
import { useCreativeToolsStore } from '@/stores/creativeTools'
import { useToolRunsStore } from '@/stores/toolRuns'
import { useWorkflowStore, WORKFLOW_INPUT_ID } from '@/stores/workflow'

const emit = defineEmits<{ (event: 'close'): void }>()
const creativeToolsStore = useCreativeToolsStore()
const workflowStore = useWorkflowStore()
const toolRunsStore = useToolRunsStore()
const tool = computed(() => creativeToolsStore.activePanelTool)
const references = computed(() => tool.value?.references ?? [])
const toolRun = computed(() => (tool.value ? (toolRunsStore.records[tool.value.id] ?? null) : null))
const runnableTool = computed(
  () =>
    tool.value?.type === 'lyrics' || tool.value?.type === 'image' || tool.value?.type === 'video',
)
const generationFailureText = computed(() =>
  tool.value?.type === 'image'
    ? '图像模型尚未配置，请稍后再试'
    : tool.value?.type === 'video'
      ? '视频模型尚未配置或生成超时，请稍后再试'
      : '歌词生成失败，请稍后重试',
)
const mainInputSource = computed(() => {
  const nodeId = tool.value
    ? (workflowStore.nodes.find((candidate) => candidate.toolId === tool.value!.id)?.id ?? null)
    : null
  const edge = workflowStore.edges.find((item) => item.target === nodeId)
  if (!edge) return null
  if (edge.source === WORKFLOW_INPUT_ID) return '输入节点'
  return workflowStore.nodes.find((node) => node.id === edge.source)?.name ?? '上一步工具'
})
const currentNode = computed(() =>
  tool.value ? (workflowStore.nodes.find((node) => node.toolId === tool.value!.id) ?? null) : null,
)
const candidates = computed(() => currentNode.value?.candidates ?? [])
const selectedCandidateId = computed(() => currentNode.value?.selectedCandidateId ?? null)
const mainInputLabel = computed(() => mainInputSource.value ?? '等待连接')
const mainInputDetail = computed(() =>
  mainInputSource.value
    ? '连接建立后，将接收上一步选中的单个生产结果'
    : '请从输入节点或上一步工具的输出插孔建立连接',
)
const panelRef = ref<HTMLElement | null>(null)
const textReferenceOpen = ref(false)
const textReference = ref('')

watch(
  () => tool.value?.id,
  () => {
    textReferenceOpen.value = false
    textReference.value = ''
  },
)

function update(key: string, event: Event) {
  const target = event.target as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement
  if (tool.value) creativeToolsStore.updateParam(tool.value.id, key, target.value)
}

function updateValue(key: string, value: string) {
  if (tool.value) creativeToolsStore.updateParam(tool.value.id, key, value)
}

function candidateStatusLabel(status: string) {
  return status === 'succeeded' ? '成功' : status === 'failed' ? '失败' : '待配置'
}

function preview(content: string) {
  return content.length > 280 ? `${content.slice(0, 280)}…` : content
}

// 图像结果以 markdown 图片行回传（![alt](/api/assets/xxx.png)），应直接显示为图片
const imageResultUrl = computed(() => {
  const content = toolRun.value?.content
  if (!content) return null
  const match = content.match(/!\[[^\]]*\]\(([^)]+)\)/)
  return match?.[1] ?? null
})

// 视频结果同样以 markdown 行回传（![视频](/api/assets/xxx.mp4)），渲染为 <video>
const videoResultUrl = computed(() => {
  const content = toolRun.value?.content
  if (!content) return null
  const match = content.match(/!\[[^\]]*\]\(([^)]+\.mp4)\)/)
  return match?.[1] ?? null
})

function selectCandidate(candidateId: string) {
  if (!currentNode.value) return
  workflowStore.selectCandidate(currentNode.value.id, candidateId)
  ElMessage.success('已指定作为下游输入')
}

function mainInputHint(type: string) {
  return (
    {
      lyrics: '输入节点的主题文字或项目资料',
      image: '上一工具的 Prompt / 歌词或图片结果',
      video: '上一工具的图片、分镜或音频结果',
      audio: '输入节点的音频或上一工具的声音结果',
    }[type] ?? '上一工具的生产结果'
  )
}

function handleFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file || !tool.value) return
  const type = file.type.startsWith('image/') ? 'image' : 'file'
  creativeToolsStore.addReference(tool.value.id, {
    type,
    name: file.name,
    detail: `${Math.ceil(file.size / 1024)} KB · 本地参考文件`,
  })
  input.value = ''
}

function addTextReference() {
  const value = textReference.value.trim()
  if (!value || !tool.value) return
  creativeToolsStore.addReference(tool.value.id, {
    type: 'text',
    name: value.length > 24 ? `${value.slice(0, 24)}…` : value,
    detail: '用户输入的文本参考',
  })
  textReference.value = ''
  textReferenceOpen.value = false
}

function removeReference(referenceId: string) {
  if (tool.value) creativeToolsStore.removeReference(tool.value.id, referenceId)
}

function saveAndClose() {
  if (!tool.value) return
  creativeToolsStore.saveTool(tool.value.id)
  textReferenceOpen.value = false
  textReference.value = ''
  creativeToolsStore.closePanel()
  emit('close')
  ElMessage.success('参数已保存')
}

function handleDocumentPointerDown(event: PointerEvent) {
  if (!tool.value) return
  const target = event.target as Node | null
  if (target && panelRef.value?.contains(target)) return
  saveAndClose()
}

onMounted(() => document.addEventListener('pointerdown', handleDocumentPointerDown))

onBeforeUnmount(() => {
  document.removeEventListener('pointerdown', handleDocumentPointerDown)
})

function artMark(type: string) {
  return { lyrics: 'Aa', image: '✦', video: '▶', audio: '∿' }[type] ?? '✦'
}
</script>

<style scoped>
/* ============ 面板骨架（暗室分层：深色底 + 细分区块） ============ */

.panel-layer {
  position: absolute;
  inset: 0;
  z-index: 3;
  pointer-events: none;
}

.panel-layer.is-active {
  pointer-events: none;
}

.panel {
  position: absolute;
  top: 0;
  left: 18px;
  width: min(460px, calc(100% - 36px));
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
  pointer-events: auto;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 16px;
  background: linear-gradient(180deg, #131313 0%, #0f0f0f 100%);
  box-shadow:
    0 24px 64px rgba(0, 0, 0, 0.5),
    inset 0 1px 0 rgba(255, 255, 255, 0.05);
  overflow: hidden;
}

.panel-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  padding: 20px 20px 18px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.07);
  background: rgba(255, 255, 255, 0.015);
}

.panel-title {
  display: flex;
  min-width: 0;
  align-items: flex-start;
  gap: 13px;
}

.panel-mark {
  display: grid;
  flex: 0 0 auto;
  width: 40px;
  height: 40px;
  place-items: center;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.04);
  filter: grayscale(1);
  font-size: 22px;
}

.panel-eyebrow {
  color: #828282;
  font-size: 10px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
}

.panel-title-copy h2 {
  margin: 3px 0 4px;
  color: #f2f2f2;
  font-size: 20px;
  font-weight: 600;
  letter-spacing: -0.01em;
}

.panel-title-copy p {
  margin: 0;
  color: #8d8d8d;
  font-size: 12.5px;
  line-height: 1.55;
}

.panel-close {
  display: grid;
  flex: 0 0 auto;
  width: 36px;
  height: 36px;
  place-items: center;
  border: 1px solid rgba(255, 255, 255, 0.14) !important;
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.03) !important;
  color: #9c9c9c !important;
  font-size: 15px;
  cursor: pointer;
  transition:
    background-color 160ms ease,
    color 160ms ease,
    border-color 160ms ease;
}

.panel-close:hover {
  background: rgba(255, 255, 255, 0.09) !important;
  border-color: rgba(255, 255, 255, 0.26) !important;
  color: #fff !important;
}

/* B19：运行期锁定参数编辑 */
.panel-running-lock {
  margin: 4px 0 10px;
  padding: 8px 10px;
  border: 1px solid rgba(156, 156, 156, 0.4);
  border-radius: 8px;
  background: rgba(28, 28, 28, 0.9);
  color: #cecece;
  font-size: 11px;
}

.panel-body.is-running-locked .block {
  pointer-events: none;
  opacity: 0.65;
}

.panel-body {
  min-height: 0;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 14px;
  overflow-y: auto;
  padding: 16px 18px 18px;
}

/* ============ 区块卡片（暗室分层：卡片浮起于面板底） ============ */

.block {
  padding: 14px;
  border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 12px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.028), rgba(255, 255, 255, 0.012));
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.03);
}

.block-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 12px;
  color: #d7d7d7;
  font-size: 13px;
  font-weight: 600;
}

.block-head small {
  color: #7a7a7a;
  font-size: 11px;
  font-weight: 400;
}

/* ============ 主要输入卡 ============ */

.main-input-card {
  display: flex;
  align-items: center;
  gap: 12px;
  min-height: 72px;
  padding: 12px 14px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 10px;
  background: #0c0c0c;
}

.main-input-copy {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
  gap: 4px;
}

.main-input-copy strong {
  color: #ededed;
  font-size: 13px;
  font-weight: 600;
}

.main-input-copy small {
  color: #7f7f7f;
  font-size: 11px;
  line-height: 1.5;
}

.port-mark {
  color: #bbbbbb;
  font-size: 20px;
  line-height: 1;
}

.input-status {
  flex: 0 0 auto;
  padding: 5px 9px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.05);
  color: #959595;
  font-size: 10px;
}

.input-status.is-linked {
  border-color: rgba(255, 255, 255, 0.22);
  background: rgba(255, 255, 255, 0.1);
  color: #e7e7e7;
}

/* ============ 补充参考 ============ */

.reference-list {
  display: grid;
  gap: 8px;
  margin-bottom: 10px;
}

.reference-item {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
  padding: 10px 12px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 10px;
  background: #0c0c0c;
}

.reference-type {
  display: grid;
  flex: 0 0 auto;
  width: 30px;
  height: 30px;
  place-items: center;
  border-radius: 7px;
  background: rgba(255, 255, 255, 0.06);
  color: #cbcbcb;
  font-size: 13px;
}

.reference-copy {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
  gap: 2px;
}

.reference-copy strong {
  overflow: hidden;
  color: #e3e3e3;
  font-size: 12.5px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.reference-copy small {
  color: #7a7a7a;
  font-size: 10.5px;
}

.reference-remove {
  display: grid;
  flex: 0 0 auto;
  width: 28px;
  height: 28px;
  place-items: center;
  border: 1px solid rgba(255, 255, 255, 0.12) !important;
  border-radius: 7px;
  background: transparent;
  color: #909090 !important;
  font-size: 16px;
  cursor: pointer;
  transition:
    background-color 160ms ease,
    color 160ms ease;
}

.reference-remove:hover {
  background: rgba(255, 255, 255, 0.1) !important;
  color: #fff !important;
}

.reference-empty {
  margin-bottom: 10px;
  padding: 12px 14px;
  border: 1px dashed rgba(255, 255, 255, 0.12);
  border-radius: 10px;
  color: #797979;
  font-size: 12px;
}

.reference-actions {
  display: flex;
  gap: 10px;
}

.reference-form {
  display: flex;
  align-items: flex-end;
  gap: 10px;
  margin-top: 10px;
}

.reference-form textarea {
  flex: 1;
  min-height: 56px;
  padding: 10px 12px;
  border: 1px solid #2e2e2e;
  border-radius: 10px;
  outline: none;
  background: #0b0b0b;
  color: #f0f0f0;
  font-size: 13px;
  line-height: 1.6;
  resize: vertical;
  transition:
    border-color 160ms ease,
    box-shadow 160ms ease;
}

.reference-form textarea:focus {
  border-color: #909090;
  box-shadow: 0 0 0 3px rgba(255, 255, 255, 0.08);
}

/* ============ 参数字段：统一大控件 ============ */

.field {
  display: flex;
  flex-direction: column;
  gap: 7px;
  min-width: 0;
}

.field + .field,
.field-row + .field,
.field + .field-row {
  margin-top: 12px;
}

.field-row {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
}

.field-label {
  color: #cbcbcb;
  font-size: 12.5px;
  font-weight: 500;
}

.field-control {
  width: 100%;
  min-height: 44px;
  padding: 11px 13px;
  border: 1px solid #2d2d2d;
  border-radius: 10px;
  outline: none;
  background: #0b0b0b;
  color: #f0f0f0;
  font-size: 13.5px;
  line-height: 1.6;
  cursor: text;
  transition:
    border-color 160ms ease,
    background-color 160ms ease,
    box-shadow 160ms ease;
}

.field-control::placeholder {
  color: #656565;
}

.field-control:hover {
  border-color: #414141;
  background: #0e0e0e;
}

.field-control:focus,
.field-control:focus-visible {
  border-color: #9d9d9d;
  background: #0f0f0f;
  box-shadow: 0 0 0 3px rgba(255, 255, 255, 0.08);
}

.field-select {
  appearance: none;
  cursor: pointer;
  padding-right: 38px;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 12 8' fill='none'%3E%3Cpath d='M1 1.5L6 6.5L11 1.5' stroke='%239a9aa0' stroke-width='1.6' stroke-linecap='round'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 14px center;
  background-size: 12px;
}

.field-select option {
  background: #171717;
  color: #f0f0f0;
}

.field-textarea {
  resize: vertical;
}

.file-input-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.file-input-name {
  flex: 1;
  overflow: hidden;
  min-height: 44px;
  padding: 11px 13px;
  border: 1px solid #2d2d2d;
  border-radius: 10px;
  background: #0b0b0b;
  color: #9e9e9e;
  font-size: 13px;
  line-height: 1.6;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* ============ 输出与结果 ============ */

.output-note {
  display: flex;
  align-items: center;
  gap: 11px;
  padding: 11px 14px;
  border: 1px solid rgba(255, 255, 255, 0.07);
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.015);
}

.output-note > div {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 3px;
}

.output-note strong {
  color: #d1d1d1;
  font-size: 12.5px;
  font-weight: 600;
}

.output-note small {
  color: #767676;
  font-size: 11px;
  line-height: 1.5;
}

.result-image {
  display: block;
  max-width: 100%;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.08);
}

.result-video {
  display: block;
  width: 100%;
  max-height: 360px;
  border-radius: 8px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: #000;
}

.result-block {
  border-color: rgba(255, 255, 255, 0.16);
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.045), rgba(255, 255, 255, 0.02));
}

.result-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  color: #e5e5e5;
  font-size: 13px;
  font-weight: 600;
}

.result-meta {
  color: #878787;
  font-size: 10.5px;
  font-weight: 400;
}

.result-content {
  max-height: 380px;
  margin: 10px 0 0;
  overflow: auto;
  color: #dbdbdb;
  font: inherit;
  font-size: 13px;
  line-height: 1.8;
  white-space: pre-wrap;
}

/* ============ 候选版本 ============ */

.candidate-block {
  border-color: rgba(255, 255, 255, 0.12);
}

.candidate-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
  color: #d7d7d7;
  font-size: 13px;
  font-weight: 600;
}

.candidate-head small {
  color: #7a7a7a;
  font-size: 11px;
  font-weight: 400;
}

.candidate-item {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-bottom: 8px;
  padding: 10px 12px;
  border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 10px;
  background: #0c0c0c;
}

.candidate-item.is-selected {
  border-color: rgba(255, 255, 255, 0.35);
  background: rgba(255, 255, 255, 0.04);
}

.candidate-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.candidate-status {
  display: inline-flex;
  align-items: center;
  min-height: 22px;
  padding: 0 8px;
  border-radius: 6px;
  border: 1px solid rgba(255, 255, 255, 0.14);
  background: rgba(255, 255, 255, 0.04);
  color: #b9b9b9;
  font-size: 10.5px;
}

.candidate-status.is-succeeded {
  border-color: rgba(255, 255, 255, 0.26);
  color: #e7e7e7;
}

.candidate-status.is-failed {
  border-color: rgba(185, 185, 185, 0.3);
  color: #afafaf;
}

.candidate-select {
  min-height: 30px !important;
  font-size: 11px !important;
  padding: 0 10px !important;
}

.candidate-snippet {
  margin: 0;
  max-height: 130px;
  overflow: auto;
  color: #d1d1d1;
  font: inherit;
  font-size: 12px;
  line-height: 1.7;
  white-space: pre-wrap;
}

.error-block {
  border-color: rgba(185, 185, 185, 0.24);
  background: rgba(171, 171, 171, 0.04);
  color: #b2b2b2;
  font-size: 12.5px;
  line-height: 1.65;
}

/* ============ 按钮系统（可点区域必须一眼能认出） ============ */

.panel-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 14px 18px 16px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(255, 255, 255, 0.015);
}

.foot-hint {
  color: #797979;
  font-size: 11.5px;
}

.foot-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.btn-primary,
.btn-secondary,
.ghost-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 46px;
  padding: 0 20px;
  border-radius: 10px;
  font-size: 13.5px;
  font-weight: 600;
  cursor: pointer;
  transition:
    background-color 160ms ease,
    border-color 160ms ease,
    color 160ms ease,
    transform 120ms ease;
}

.btn-primary {
  border: 1px solid #ededed !important;
  background: #ededed !important;
  color: #0e0e0e !important;
}

.btn-primary:hover:not(:disabled) {
  background: #ffffff !important;
  transform: translateY(-1px);
}

.btn-primary:disabled {
  cursor: wait;
  opacity: 0.5;
}

.btn-secondary {
  border: 1px solid rgba(255, 255, 255, 0.16) !important;
  background: rgba(255, 255, 255, 0.03) !important;
  color: #dbdbdb !important;
}

.btn-secondary:hover {
  border-color: rgba(255, 255, 255, 0.3) !important;
  background: rgba(255, 255, 255, 0.08) !important;
  color: #fff !important;
}

.btn-small {
  min-height: 40px;
  padding: 0 16px;
  font-size: 12.5px;
}

.ghost-btn {
  flex: 1;
  min-height: 44px;
  border: 1px dashed rgba(255, 255, 255, 0.18) !important;
  background: rgba(255, 255, 255, 0.02) !important;
  color: #c6c6c6 !important;
  font-size: 13px;
  font-weight: 500;
}

.ghost-btn:hover {
  border-color: rgba(255, 255, 255, 0.34) !important;
  background: rgba(255, 255, 255, 0.06) !important;
  color: #fff !important;
}

.ghost-btn input[type='file'] {
  display: none;
}

.btn-inline {
  flex: 0 0 auto;
  min-height: 44px;
}

/* ============ 面板过渡 ============ */

.tool-config-enter-active,
.tool-config-leave-active {
  transition:
    transform 300ms cubic-bezier(0.16, 1, 0.3, 1),
    opacity 260ms ease;
}

.tool-config-enter-from,
.tool-config-leave-to {
  opacity: 0;
  transform: translateX(-100%);
}

.tool-config-enter-to,
.tool-config-leave-from {
  opacity: 1;
  transform: translateX(0);
}

@media (max-width: 760px) {
  .panel {
    left: 12px;
    width: min(100%, 340px);
  }

  .field-row {
    grid-template-columns: 1fr;
  }
}
</style>
