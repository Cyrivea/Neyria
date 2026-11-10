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
            运行中：参数暂不可改，运行结束后结果自动刷新。
          </div>

          <!-- 运行前摘要：左侧是订阅选模型的预留位（当前只展示平台默认，不可点），右侧回显最终规格 -->
          <div class="run-summary" role="group" aria-label="运行前摘要">
            <div class="summary-cell is-reserved" aria-disabled="true" title="当前使用平台默认模型">
              <span class="summary-label">模型</span>
              <span class="summary-value">平台默认</span>
            </div>
            <div class="summary-cell">
              <span class="summary-label">输出规格</span>
              <span class="summary-value">{{ outputSpec }}</span>
            </div>
          </div>
          <p v-if="referenceWarning" class="summary-alert" role="status">{{ referenceWarning }}</p>

          <!-- 主要输入：结构信息，压成一行，不占创作主轨道 -->
          <div class="input-strip">
            <span class="port-mark" aria-hidden="true">⇥</span>
            <span class="input-strip-title">主要输入</span>
            <strong class="input-strip-value">{{ mainInputSource ?? '未连接' }}</strong>
            <span class="input-status" :class="{ 'is-linked': mainInputSource }">
              {{ mainInputSource ? '已连接' : '等待连接' }}
            </span>
          </div>
          <p class="input-strip-hint">{{ mainInputDetail }} · {{ mainInputHint(tool.type) }}</p>

          <!-- 创作：用户真正要决定的东西 -->
          <section class="block">
            <header class="block-head">
              <span>创作</span>
              <small>{{ creationHint }}</small>
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
                  placeholder="如：温柔克制、忧郁狂欢、孤独怀旧…"
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
                  class="field-control field-textarea field-textarea-lg"
                  :value="tool.params.prompt"
                  rows="5"
                  placeholder="写清主体、场景、光线、镜头与氛围，例如：雨夜霓虹下挂着工作灯的小店，胶片颗粒"
                  @input="update('prompt', $event)"
                ></textarea>
              </div>
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
            </template>

            <template v-else-if="tool.type === 'video'">
              <div class="field">
                <span class="field-label">镜头描述</span>
                <textarea
                  class="field-control field-textarea field-textarea-lg"
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
                    placeholder="如：电影写实、二手拉机械感、二次元动画…"
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
                    @click="setParam('source', 'demo-audio-input.wav')"
                  >
                    选择文件
                  </button>
                </div>
              </div>
              <div class="field-row">
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
              </div>
            </template>
          </section>

          <!-- 更多设置：低频项收进折叠层，折叠态给出摘要（对应渐进式披露） -->
          <section class="block block-advanced">
            <button
              type="button"
              class="advanced-toggle"
              :aria-expanded="advancedOpen"
              @click="advancedOpen = !advancedOpen"
            >
              <span class="advanced-title">更多设置</span>
              <span class="advanced-summary">{{ advancedSummary }}</span>
              <span class="advanced-caret" aria-hidden="true">{{ advancedOpen ? '▴' : '▾' }}</span>
            </button>

            <div v-if="advancedOpen" class="advanced-body">
              <template v-if="tool.type === 'image'">
                <div class="field">
                  <span class="field-label">画面比例</span>
                  <div class="ratio-row" role="group" aria-label="画面比例">
                    <button
                      v-for="item in imageRatios"
                      :key="item.value"
                      type="button"
                      class="ratio-btn"
                      :class="{ 'is-active': tool.params.ratio === item.value }"
                      :aria-pressed="tool.params.ratio === item.value"
                      @click="setParam('ratio', item.value)"
                    >
                      {{ item.label }}
                    </button>
                  </div>
                  <p class="field-note">{{ ratioNote }}</p>
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
                  <span class="field-label">画面比例</span>
                  <div class="ratio-row" role="group" aria-label="画面比例">
                    <button
                      v-for="item in videoRatios"
                      :key="item.value"
                      type="button"
                      class="ratio-btn"
                      :class="{ 'is-active': tool.params.ratio === item.value }"
                      :aria-pressed="tool.params.ratio === item.value"
                      @click="setParam('ratio', item.value)"
                    >
                      {{ item.label }}
                    </button>
                  </div>
                  <p class="field-note">画幅与时长共同决定成片尺寸与费用。</p>
                </div>
              </template>

              <template v-else-if="tool.type === 'audio'">
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
              </template>

              <div class="field">
                <span class="field-label">{{ referenceTitle }}</span>
                <p v-if="tool.type === 'image'" class="field-note">{{ referenceNote }}</p>
                <p v-else class="field-note">参考文件只保存在本地，暂不参与生成。</p>

                <div v-if="references.length" class="reference-grid">
                  <figure
                    v-for="reference in references"
                    :key="reference.id"
                    class="reference-thumb"
                  >
                    <img v-if="reference.url" :src="reference.url" :alt="reference.name" />
                    <span v-else class="reference-thumb-glyph" aria-hidden="true">{{
                      reference.type === 'image' ? '▧' : 'Aa'
                    }}</span>
                    <figcaption>{{ reference.name }}</figcaption>
                    <button
                      type="button"
                      class="reference-remove"
                      :aria-label="`移除${reference.name}`"
                      @click="removeReference(reference.id)"
                    >
                      ×
                    </button>
                  </figure>
                </div>
                <p v-else class="reference-empty">暂无参考。</p>

                <div class="reference-actions">
                  <label class="ghost-btn" :class="{ 'is-disabled': referenceLimitReached }">
                    <input
                      type="file"
                      :accept="referenceAccept"
                      :multiple="tool.type === 'image'"
                      :disabled="referenceLimitReached || uploadingReference"
                      @change="handleFileChange"
                    />
                    <span>{{ referenceUploadLabel }}</span>
                  </label>
                  <button
                    v-if="tool.type !== 'image'"
                    type="button"
                    class="ghost-btn"
                    @click="textReferenceOpen = !textReferenceOpen"
                  >
                    ＋ 文本备注
                  </button>
                </div>

                <div v-if="textReferenceOpen && tool.type !== 'image'" class="reference-form">
                  <textarea
                    v-model="textReference"
                    rows="2"
                    placeholder="输入风格、限制或画面补充说明"
                  ></textarea>
                  <button type="button" class="btn-secondary btn-small" @click="addTextReference">
                    添加
                  </button>
                </div>
              </div>
            </div>
          </section>

          <!-- 产出：一个权威展示位，候选改为缩略图 -->
          <section class="block output-block">
            <header class="block-head">
              <span>产出</span>
              <small>{{ outputStatusText }}</small>
            </header>

            <div v-if="failureText" class="output-error" role="alert">{{ failureText }}</div>

            <div v-if="resultAssetUrl" class="output-preview">
              <img v-if="tool.type === 'image'" :src="resultAssetUrl" alt="生成结果" />
              <video
                v-else-if="tool.type === 'video'"
                :src="resultAssetUrl"
                controls
                preload="metadata"
              ></video>
              <pre v-else class="output-text">{{ toolRun?.content }}</pre>
            </div>

            <div v-if="imageCandidates.length" class="candidate-grid">
              <button
                v-for="candidate in imageCandidates"
                :key="candidate.id"
                type="button"
                class="candidate-thumb"
                :class="{ 'is-selected': candidate.id === selectedCandidateId }"
                :aria-pressed="candidate.id === selectedCandidateId"
                @click="selectCandidate(candidate.id)"
              >
                <img :src="candidate.url" :alt="`候选图 ${candidate.id}`" />
                <span class="candidate-flag">{{
                  candidate.id === selectedCandidateId ? '正在使用' : '用作下游'
                }}</span>
              </button>
            </div>

            <div v-else-if="textCandidates.length" class="candidate-list">
              <div
                v-for="candidate in textCandidates"
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
            </div>

            <p v-if="!hasAnyOutput" class="output-empty">
              还没有产出 · 在顶部点击「运行工作流」后出现在这里
            </p>

            <button
              v-if="hasAnyOutput"
              type="button"
              class="output-archive"
              @click="emit('open-archive')"
            >
              在档案馆中查看 →
            </button>
          </section>
        </div>

        <footer class="panel-foot">
          <!-- WF1-③：块级运行按钮已下线——运行是画布级动作（顶部「运行工作流」带费用预览）。
               2026-11-09：提示按状态变化（就绪/将重跑/运行中/配置不全），但依然只是引导，
               面板内不出现任何运行动作。 -->
          <span class="foot-hint" :class="{ 'is-warning': footHintWarning }">{{ footHint }}</span>
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
import { useProjectStore } from '@/stores/project'
import {
  deleteReferenceImage,
  getCapabilityFeatures,
  uploadReferenceImage,
  type CapabilityFeatures,
} from '@/serve/reference'

const emit = defineEmits<{ (event: 'close'): void; (event: 'open-archive'): void }>()
const creativeToolsStore = useCreativeToolsStore()
const workflowStore = useWorkflowStore()
const toolRunsStore = useToolRunsStore()
const projectStore = useProjectStore()

const tool = computed(() => creativeToolsStore.activePanelTool)
const references = computed(() => tool.value?.references ?? [])
const toolRun = computed(() => (tool.value ? (toolRunsStore.records[tool.value.id] ?? null) : null))

const DEFAULT_REFERENCE_LIMIT = 3
// 兜底尺寸（后端 features 拿不到时用）；真实映射由 /capabilities/{id}/features 提供，
// 不同 Provider（本地 ComfyUI / kunbot / siliconflow）尺寸各不相同，不能写死。
const FALLBACK_RATIO_SIZES: Record<string, string> = {
  '16:9': '1344×768',
  '1:1': '1024×1024',
  '9:16': '768×1344',
  '4:3': '1152×864',
}
const imageRatios = [
  { value: '16:9', label: '16:9' },
  { value: '1:1', label: '1:1' },
  { value: '9:16', label: '9:16' },
  { value: '4:3', label: '4:3' },
]
const videoRatios = [
  { value: '16:9', label: '16:9' },
  { value: '1:1', label: '1:1' },
  { value: '9:16', label: '9:16' },
]

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
const mainInputDetail = computed(() =>
  mainInputSource.value
    ? '连接建立后，接收上一步选中的单个生产结果'
    : '从输入节点或上一步工具的输出插孔建立连接',
)

const panelRef = ref<HTMLElement | null>(null)
const textReferenceOpen = ref(false)
const textReference = ref('')
const advancedOpen = ref(false)
const uploadingReference = ref(false)
const features = ref<CapabilityFeatures | null>(null)

const isImage = computed(() => tool.value?.type === 'image')
const referenceLimit = computed(() =>
  isImage.value ? (features.value?.reference_limit ?? DEFAULT_REFERENCE_LIMIT) : 99,
)
const imageReferences = computed(() =>
  references.value.filter((reference) => reference.type === 'image' && reference.assetId),
)
const referenceSupported = computed(() => features.value?.reference_supported ?? false)
const referenceLimitReached = computed(
  () =>
    isImage.value &&
    !uploadingReference.value &&
    imageReferences.value.length >= referenceLimit.value,
)
const referenceWarning = computed(() =>
  isImage.value && imageReferences.value.length > 0 && !referenceSupported.value
    ? '当前模型不支持参考图：累积的参考图会让本次运行被拒绝，请先移除。'
    : null,
)
const referenceTitle = computed(() => (isImage.value ? '参考图' : '参考文件'))
const referenceNote = computed(() =>
  referenceSupported.value
    ? `参考图会随本次生成一并发送（最多 ${referenceLimit.value} 张）。`
    : '当前模型不支持图生图：带着参考图运行时会被拒绝（不扣积分），移除后才能跑。',
)
const referenceAccept = computed(() =>
  isImage.value ? 'image/png,image/jpeg,image/webp' : 'image/*,.txt,.md,.pdf,.wav,.mp3',
)
const referenceUploadLabel = computed(() => {
  if (uploadingReference.value) return '上传中…'
  if (referenceLimitReached.value) return `已达上限 ${referenceLimit.value} 张`
  return isImage.value ? '＋ 上传参考图' : '＋ 图片 / 文件'
})

const ratioSizes = computed<Record<string, string>>(
  () => features.value?.ratio_sizes ?? FALLBACK_RATIO_SIZES,
)
const outputSpec = computed(() => {
  if (!tool.value) return '—'
  if (tool.value.type === 'image') {
    return `${tool.value.params.ratio} · ${ratioSizes.value[tool.value.params.ratio] ?? '自动'}`
  }
  if (tool.value.type === 'video')
    return `${tool.value.params.ratio} · ${tool.value.params.duration}`
  return tool.value.type === 'lyrics' ? '文本' : '音频'
})
const ratioNote = computed(() => {
  const ratio = tool.value?.params.ratio ?? '16:9'
  const size = ratioSizes.value[ratio] ?? '自动'
  return `${ratio} 映射到当前模型的实际出图尺寸 ${size}；比例变化不会切图。`
})
const creationHint = computed(
  () =>
    ({
      lyrics: '主题 + 曲风 + 情绪',
      image: '写清主体、场景、光线与镜头',
      video: '写清镜头运动与节奏',
      audio: '选音频并指定分析维度',
    })[tool.value?.type ?? 'lyrics'] ?? '',
)
const advancedSummary = computed(() => {
  if (!tool.value) return ''
  const params = tool.value.params
  if (tool.value.type === 'image') {
    const refs = imageReferences.value.length
    return `${params.ratio} · ${params.palette || '未设色调'} · ${refs ? `参考图 ${refs} 张` : '无参考图'}`
  }
  if (tool.value.type === 'video') return `${params.ratio} · ${params.duration}`
  if (tool.value.type === 'audio') return params.variation
  return references.value.length ? `参考 ${references.value.length} 项` : '无附加项'
})

const imageResultUrl = computed(() => {
  const content = toolRun.value?.content
  if (!content) return null
  const match = content.match(/!\[[^\]]*\]\(([^)]+\.(?:png|jpg|jpeg|webp))\)/i)
  return match?.[1] ?? null
})
const videoResultUrl = computed(() => {
  const content = toolRun.value?.content
  if (!content) return null
  const match = content.match(/!\[[^\]]*\]\(([^)]+\.mp4)\)/)
  return match?.[1] ?? null
})
const resultAssetUrl = computed(() =>
  tool.value?.type === 'video' ? videoResultUrl.value : imageResultUrl.value,
)

function candidateImageUrl(content: string | null | undefined) {
  if (!content) return null
  const match = content.match(/!\[[^\]]*\]\(([^)]+\.(?:png|jpg|jpeg|webp))\)/i)
  return match?.[1] ?? null
}
const imageCandidates = computed(() =>
  isImage.value
    ? candidates.value
        .map((candidate) => ({
          id: candidate.id,
          status: candidate.status,
          url: candidateImageUrl(candidate.content),
        }))
        .filter((candidate): candidate is { id: string; status: string; url: string } =>
          Boolean(candidate.url),
        )
    : [],
)
const textCandidates = computed(() =>
  candidates.value.filter((candidate) => !candidateImageUrl(candidate.content)),
)
const hasAnyOutput = computed(() => Boolean(resultAssetUrl.value) || candidates.value.length > 0)
const outputStatusText = computed(() => {
  if (workflowStore.isRunning) return '运行中…'
  const status = toolRun.value?.status
  if (status === 'succeeded') return '最近一次运行 · 成功'
  if (status === 'failed') return '最近一次运行 · 失败'
  if (status === 'model_unavailable') return '模型尚未配置'
  return '尚未运行'
})
const failureText = computed(() => {
  if (workflowStore.isRunning) return null
  const run = toolRun.value
  if (!run) return null
  if (run.status === 'failed' || run.status === 'model_unavailable') {
    return run.error ?? generationFailureText.value
  }
  return null
})

const promptMissing = computed(() => {
  const type = tool.value?.type
  if (type !== 'image' && type !== 'video') return false
  return !String(tool.value?.params.prompt ?? '').trim()
})
const footHint = computed(() => {
  if (workflowStore.isRunning) return '运行中，参数暂不可改；结束后结果自动刷新'
  if (referenceWarning.value) return '参考图不受当前模型支持 · 移除后才能运行'
  if (promptMissing.value) return '画面描述为空 · 运行会直接失败，补完再点顶部运行'
  if (toolRun.value?.status === 'succeeded') {
    return '参数已自动保存 · 改了参数点顶部「运行工作流」会重跑本块'
  }
  return '参数已自动保存 · 点顶部「运行工作流」开始生成'
})
const footHintWarning = computed(() => Boolean(referenceWarning.value) || promptMissing.value)

async function loadFeatures() {
  if (!isImage.value) {
    features.value = null
    return
  }
  try {
    features.value = await getCapabilityFeatures('image.generate')
  } catch {
    // 拿不到能力描述时按「不支持参考图」保守处理：宁可提前警告，不要传完才发现
    features.value = null
  }
}

watch(
  () => tool.value?.id,
  () => {
    textReferenceOpen.value = false
    textReference.value = ''
  },
)
watch(() => tool.value?.type, loadFeatures, { immediate: true })

function update(key: string, event: Event) {
  const target = event.target as HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement
  if (tool.value) creativeToolsStore.updateParam(tool.value.id, key, target.value)
}

function setParam(key: string, value: string) {
  if (tool.value) creativeToolsStore.updateParam(tool.value.id, key, value)
}

function candidateStatusLabel(status: string) {
  return status === 'succeeded' ? '成功' : status === 'failed' ? '失败' : '待配置'
}

function preview(content: string) {
  return content.length > 280 ? `${content.slice(0, 280)}…` : content
}

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

async function handleFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const selected = Array.from(input.files ?? [])
  input.value = ''
  if (!tool.value || selected.length === 0) return
  const toolId = tool.value.id

  if (isImage.value) {
    const room = Math.max(0, referenceLimit.value - imageReferences.value.length)
    if (selected.length > room) ElMessage.warning(`参考图最多 ${referenceLimit.value} 张`)
    const accepted = selected.slice(0, room)
    if (accepted.length === 0) return
    uploadingReference.value = true
    try {
      for (const file of accepted) {
        const asset = await uploadReferenceImage(file, projectStore.id)
        creativeToolsStore.addReference(toolId, {
          type: 'image',
          name: file.name,
          detail: `${Math.ceil(asset.byte_size / 1024)} KB · 已上传`,
          assetId: asset.asset_id,
          url: asset.path,
        })
      }
    } catch {
      // 传输层已弹错误提示；这里只要不把半截状态写进实例
    } finally {
      uploadingReference.value = false
    }
    return
  }

  // 非图像块：沿用本地备注行为（面板文案已说明不参与生成）
  const file = selected[0]
  creativeToolsStore.addReference(toolId, {
    type: file.type.startsWith('image/') ? 'image' : 'file',
    name: file.name,
    detail: `${Math.ceil(file.size / 1024)} KB · 本地参考文件`,
  })
}

function addTextReference() {
  const value = textReference.value.trim()
  if (!value || !tool.value) return
  creativeToolsStore.addReference(tool.value.id, {
    type: 'text',
    name: value.length > 24 ? `${value.slice(0, 24)}…` : value,
    detail: '本地文本备注，不参与生成',
  })
  textReference.value = ''
  textReferenceOpen.value = false
}

async function removeReference(referenceId: string) {
  if (!tool.value) return
  const target = references.value.find((reference) => reference.id === referenceId)
  creativeToolsStore.removeReference(tool.value.id, referenceId)
  if (target?.assetId) {
    try {
      await deleteReferenceImage(target.assetId)
    } catch {
      // 资产已经在服务端消失也算移除成功：本地引用先断开更重要
    }
  }
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

.foot-hint.is-warning {
  color: #e4e4e4;
}

/* ============ 运行前摘要（模型预留位 + 输出规格回显） ============ */

.run-summary {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: 8px;
}

.summary-cell {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 3px;
  padding: 9px 11px;
  border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.025);
}

/* 订阅选模型的预留位：虚线表示「位置已留、当前不可交互」，不画成能点的下拉 */
.summary-cell.is-reserved {
  border-style: dashed;
  border-color: rgba(255, 255, 255, 0.16);
  background: transparent;
  cursor: default;
}

.summary-label {
  color: #7c7c7c;
  font-size: 10px;
  letter-spacing: 0.06em;
}

.summary-value {
  overflow: hidden;
  color: #ededed;
  font-size: 12.5px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.summary-cell.is-reserved .summary-value {
  color: #bdbdbd;
  font-weight: 500;
}

.summary-alert {
  margin: -6px 0 0;
  padding: 8px 10px;
  border: 1px solid rgba(255, 255, 255, 0.18);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.05);
  color: #dedede;
  font-size: 11px;
  line-height: 1.55;
}

/* ============ 主要输入：一行状态 ============ */

.input-strip {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 8px 11px;
  border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.02);
}

.input-strip-title {
  color: #7f7f7f;
  font-size: 11px;
}

.input-strip-value {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  color: #e6e6e6;
  font-size: 12.5px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.input-strip-hint {
  margin: -8px 0 0;
  padding-left: 2px;
  color: #707070;
  font-size: 10.5px;
  line-height: 1.5;
}

/* ============ 更多设置（折叠） ============ */

.block-advanced {
  padding: 0;
}

.advanced-toggle {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 10px;
  padding: 12px 14px;
  border: 0;
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
}

.advanced-title {
  color: #d7d7d7;
  font-size: 13px;
  font-weight: 600;
}

.advanced-summary {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  color: #7c7c7c;
  font-size: 11px;
  text-align: right;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.advanced-caret {
  color: #9a9a9a;
  font-size: 11px;
}

.advanced-toggle:hover .advanced-title {
  color: #fff;
}

.advanced-body {
  display: flex;
  flex-direction: column;
  gap: 13px;
  padding: 13px 14px 14px;
  border-top: 1px solid rgba(255, 255, 255, 0.06);
}

.field-note {
  margin: 5px 0 0;
  color: #7a7a7a;
  font-size: 10.5px;
  line-height: 1.55;
}

/* ============ 比例分段控件 ============ */

.ratio-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.ratio-btn {
  min-width: 56px;
  flex: 1 1 0;
  padding: 7px 8px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.03);
  color: #a8a8a8;
  font-size: 11.5px;
  cursor: pointer;
  transition:
    background-color 160ms ease,
    border-color 160ms ease,
    color 160ms ease;
}

.ratio-btn:hover {
  border-color: rgba(255, 255, 255, 0.24);
  color: #e8e8e8;
}

.ratio-btn.is-active {
  border-color: rgba(255, 255, 255, 0.42);
  background: rgba(255, 255, 255, 0.14);
  color: #fff;
  font-weight: 600;
}

/* ============ 参考图（真实上传 + 缩略图） ============ */

.reference-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  margin-bottom: 10px;
}

.reference-thumb {
  position: relative;
  margin: 0;
  overflow: hidden;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 9px;
  background: #0b0b0b;
}

/* 用户素材属于「内容」，不进灰度滤镜（CLAUDE.md 单色法只管 UI 板块） */
.reference-thumb img {
  display: block;
  width: 100%;
  aspect-ratio: 1 / 1;
  object-fit: cover;
}

.reference-thumb-glyph {
  display: grid;
  width: 100%;
  aspect-ratio: 1 / 1;
  place-items: center;
  color: #bdbdbd;
  font-size: 18px;
}

.reference-thumb figcaption {
  overflow: hidden;
  padding: 5px 7px;
  color: #9a9a9a;
  font-size: 10px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.reference-thumb .reference-remove {
  position: absolute;
  top: 6px;
  right: 6px;
  background: rgba(0, 0, 0, 0.66) !important;
}

.reference-empty {
  margin: 0 0 9px;
  color: #707070;
  font-size: 11px;
}

.ghost-btn.is-disabled {
  opacity: 0.45;
  pointer-events: none;
}

/* ============ 产出 ============ */

.output-preview {
  margin-bottom: 10px;
  overflow: hidden;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 10px;
  background: #080808;
}

.output-preview img,
.output-preview video {
  display: block;
  width: 100%;
  height: auto;
}

.output-text {
  max-height: 220px;
  margin: 0;
  padding: 12px;
  overflow: auto;
  color: #d0d0d0;
  font-size: 12px;
  white-space: pre-wrap;
}

.output-error {
  margin-bottom: 10px;
  padding: 10px 12px;
  border: 1px solid rgba(255, 255, 255, 0.2);
  border-radius: 9px;
  background: rgba(255, 255, 255, 0.06);
  color: #ededed;
  font-size: 11.5px;
  line-height: 1.6;
}

.output-empty {
  margin: 0;
  padding: 16px 0 2px;
  color: #6f6f6f;
  font-size: 11.5px;
  text-align: center;
}

.candidate-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  margin-bottom: 10px;
}

.candidate-thumb {
  position: relative;
  overflow: hidden;
  padding: 0;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 9px;
  background: #0b0b0b;
  cursor: pointer;
}

.candidate-thumb img {
  display: block;
  width: 100%;
  aspect-ratio: 1 / 1;
  object-fit: cover;
}

.candidate-thumb.is-selected {
  border-color: rgba(255, 255, 255, 0.5);
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.25);
}

.candidate-flag {
  position: absolute;
  right: 0;
  bottom: 0;
  left: 0;
  padding: 4px 6px;
  background: rgba(0, 0, 0, 0.72);
  color: #dcdcdc;
  font-size: 9.5px;
}

.candidate-thumb.is-selected .candidate-flag {
  color: #fff;
  font-weight: 600;
}

.output-archive {
  padding: 7px 0;
  border: 0;
  background: transparent;
  color: #b9b9b9;
  font-size: 11.5px;
  cursor: pointer;
}

.output-archive:hover {
  color: #fff;
  text-decoration: underline;
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
