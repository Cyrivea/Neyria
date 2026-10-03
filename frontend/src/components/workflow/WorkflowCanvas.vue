<template>
  <section
    ref="canvasRef"
    class="workflow-canvas"
    :class="{
      'is-pan-mode': workflowStore.mode === 'pan',
      'is-connecting': connectionState,
      'is-running-locked': workflowStore.isRunning,
    }"
    aria-label="Workflow 工作流画布"
    @wheel.prevent="handleWheel"
    @pointerdown="handleCanvasPointerDown"
  >
    <div v-if="workflowStore.isRunning" class="workflow-running-banner" aria-live="polite">
      <span class="workflow-running-spinner" aria-hidden="true"></span>
      <strong>工作区运行中</strong>
      <span>助手正在执行此 Workflow，画布暂不可编辑，运行结束后自动恢复。</span>
    </div>

    <div class="workflow-mode-hint" aria-live="polite">
      <span class="workflow-mode-dot" :class="`is-${workflowStore.mode}`"></span>
      <strong>{{ modeLabel }}</strong>
      <span>{{ modeDescription }}</span>
    </div>

    <div v-if="connectionState" class="workflow-connection-hint" aria-live="polite">
      正在连接：拖到目标工具左侧的输入插孔
      <button type="button" @click.stop="cancelConnection">取消</button>
    </div>

    <div
      v-if="selectionState"
      class="workflow-selection-box"
      :style="selectionBoxStyle"
      aria-hidden="true"
    ></div>

    <div
      class="workflow-world"
      :style="{
        transform: `translate(${workflowStore.offset.x}px, ${workflowStore.offset.y}px) scale(${workflowStore.scale})`,
      }"
    >
      <svg class="workflow-connections" aria-label="工作流连接线">
        <g
          v-for="connection in connections"
          :key="`connection-${connection.from.id}-${connection.to.id}`"
          class="workflow-connection-group"
          @pointerdown.stop
          @dblclick.stop="removeConnection(connection)"
        >
          <title>双击拆除这条连接</title>
          <path
            class="workflow-connection-hit"
            :d="connectionPath(connection.from, connection.to)"
          />
          <path
            class="workflow-connection-line"
            :d="connectionPath(connection.from, connection.to)"
            aria-hidden="true"
          />
        </g>
        <path
          v-if="connectionState"
          class="workflow-connection-preview"
          :d="connectionPreviewPath"
          aria-hidden="true"
        />
      </svg>

      <article
        v-for="node in canvasNodes"
        :key="node.id"
        class="workflow-node"
        :class="{
          'is-selected': isNodeSelected(node.id),
          'is-endpoint': node.kind === 'endpoint',
          'is-input-endpoint': node.endpoint === 'input',
          'is-output-endpoint': node.endpoint === 'output',
        }"
        :data-node-id="node.id"
        :data-node-kind="node.kind"
        :style="{ left: `${node.x}px`, top: `${node.y}px`, '--node-color': node.color }"
        :tabindex="node.kind === 'tool' ? 0 : -1"
        @pointerdown.stop="handleNodePointerDown($event, node)"
        @click.stop="selectNode(node)"
        @keydown.delete.stop="deleteNode(node)"
        @keydown.backspace.stop="deleteNode(node)"
      >
        <button
          v-if="node.endpoint !== 'output'"
          type="button"
          class="workflow-node-port workflow-node-port-out"
          :class="{ 'is-hot': connectionState?.sourceId === node.id }"
          :aria-label="`${node.name}输出插孔`"
          title="从这里拖出连接"
          @pointerdown.stop="startConnection($event, node.id)"
        ></button>
        <button
          v-if="node.endpoint !== 'input'"
          type="button"
          class="workflow-node-port workflow-node-port-in"
          :class="{ 'is-target': Boolean(connectionState) }"
          :aria-label="`${node.name}输入插孔`"
          title="接收上一个节点的输出；双击连接线拆除"
          @pointerdown.stop.prevent
          @pointerup.stop="finishConnection(node.id)"
          @click.stop.prevent
          @dblclick.stop.prevent
        ></button>

        <template v-if="node.kind === 'endpoint'">
          <div class="workflow-endpoint-kicker">
            {{ node.endpoint === 'input' ? 'START' : 'OUTPUT' }}
          </div>
          <div class="workflow-endpoint-title">
            <span class="workflow-endpoint-icon">{{ node.endpoint === 'input' ? '⇥' : '⇥' }}</span>
            {{ node.name }}
          </div>
          <p>{{ node.description }}</p>
        </template>
        <template v-else>
          <div class="workflow-node-head">
            <span class="workflow-node-icon">{{ nodeMark(node.type) }}</span>
            <div class="workflow-node-heading">
              <span class="workflow-node-badge">{{ node.badge }}</span>
              <h3>{{ node.name }}</h3>
            </div>
            <span class="workflow-node-number">{{ toolIndex(node.id) }}</span>
          </div>
          <p>{{ node.description }}</p>
          <div class="workflow-node-footer">
            <template v-if="node.runStatus === 'running'">生成中…</template>
            <template v-else-if="node.runStatus === 'succeeded'"
              >已完成 · 结果已写入参数面板</template
            >
            <template v-else-if="node.runStatus === 'failed'"
              >执行失败 · 请在参数面板查看原因</template
            >
            <template v-else-if="node.runStatus === 'model_unavailable'">模型尚未配置</template>
            <template v-else>主要输入 · 补充参考 · 生成设置</template>
          </div>
        </template>
      </article>
    </div>

    <div v-if="!isPanelOpen" class="workflow-canvas-toolbar" aria-label="画布工具">
      <button
        type="button"
        class="workflow-tool-button workflow-add-button"
        title="添加节点"
        aria-label="添加节点"
        @click.stop="emit('open-picker')"
      >
        +
      </button>
      <span class="workflow-tool-divider"></span>
      <button
        type="button"
        class="workflow-tool-button"
        :class="{ active: workflowStore.mode === 'select' }"
        :aria-pressed="workflowStore.mode === 'select'"
        title="选择工具并框选移动"
        aria-label="选择工具并框选移动"
        @click.stop="workflowStore.setMode('select')"
      >
        ↖
      </button>
      <button
        type="button"
        class="workflow-tool-button"
        :class="{ active: workflowStore.mode === 'pan' }"
        :aria-pressed="workflowStore.mode === 'pan'"
        title="平移背景画布，不改变工作流形状"
        aria-label="平移背景画布，不改变工作流形状"
        @click.stop="workflowStore.setMode('pan')"
      >
        ✋
      </button>
      <span class="workflow-tool-divider"></span>
      <button
        type="button"
        class="workflow-tool-button"
        title="适应画布"
        aria-label="适应画布"
        @click.stop="workflowStore.resetView()"
      >
        ⛶
      </button>
      <button
        type="button"
        class="workflow-tool-button"
        title="更多画布操作"
        aria-label="更多画布操作"
        @click.stop="toggleMoreMenu"
      >
        …
      </button>
      <div v-if="moreMenuOpen" class="workflow-more-menu" @click.stop>
        <button type="button" @click="resetWorkflow">清空并重新开始</button>
        <button type="button" @click="emit('notify', '复制工作流功能已预留')">复制工作流</button>
        <button type="button" @click="emit('notify', '导出工作流功能已预留')">导出工作流</button>
      </div>
    </div>

    <template v-if="!isPanelOpen">
      <div class="workflow-history-controls" aria-label="编辑历史">
        <button
          type="button"
          title="撤销"
          aria-label="撤销"
          :disabled="!workflowStore.canUndo"
          @click.stop="workflowStore.undo()"
        >
          ↶
        </button>
        <button
          type="button"
          title="重做"
          aria-label="重做"
          :disabled="!workflowStore.canRedo"
          @click.stop="workflowStore.redo()"
        >
          ↷
        </button>
        <button
          type="button"
          title="操作历史"
          aria-label="操作历史"
          @click.stop="emit('notify', '操作历史面板已预留')"
        >
          ◷
        </button>
      </div>

      <div
        class="workflow-minimap"
        aria-label="工作流鸟瞰图，可拖动视口框浏览画布"
        @pointerdown.stop
      >
        <svg class="workflow-minimap-connections" aria-hidden="true">
          <path
            v-for="connection in minimapConnections"
            :key="`mini-connection-${connection.key}`"
            :d="connection.path"
          />
        </svg>
        <div
          v-for="item in minimapItems"
          :key="`mini-${item.id}`"
          class="workflow-minimap-node"
          :class="{
            'is-selected': item.kind === 'tool' && isNodeSelected(item.id),
            'is-endpoint': item.kind === 'endpoint',
          }"
          :style="item.style"
          aria-hidden="true"
        ></div>
        <div
          class="workflow-minimap-viewport"
          :style="minimapViewportStyle"
          title="拖动查看对应画布区域"
          aria-label="当前画布视口，拖动以浏览"
          @pointerdown.stop="startMinimapDrag"
        ></div>
      </div>

      <div class="workflow-zoom-controls" aria-label="画布缩放">
        <button
          type="button"
          title="缩小"
          aria-label="缩小"
          @click.stop="workflowStore.zoomBy(-0.1)"
        >
          −
        </button>
        <button type="button" class="workflow-zoom-value" @click.stop="workflowStore.resetView()">
          {{ Math.round(workflowStore.scale * 100) }}%
        </button>
        <button
          type="button"
          title="放大"
          aria-label="放大"
          @click.stop="workflowStore.zoomBy(0.1)"
        >
          +
        </button>
      </div>
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useCreativeToolsStore, type CreativeToolType } from '@/stores/creativeTools'
import {
  useWorkflowStore,
  WORKFLOW_INPUT_ID,
  WORKFLOW_OUTPUT_ID,
  type WorkflowNode,
  type WorkflowSnapshot,
} from '@/stores/workflow'

const emit = defineEmits<{
  (event: 'open-picker'): void
  (event: 'node-selected', toolId: string): void
  (event: 'notify', message: string): void
}>()

interface CanvasEndpoint {
  kind: 'endpoint'
  id: string
  endpoint: 'input' | 'output'
  name: string
  description: string
  color: string
  x: number
  y: number
  width: number
  height: number
  type: null
}

type CanvasNode =
  | (WorkflowNode & { kind: 'tool'; endpoint?: never; width: number; height: number })
  | CanvasEndpoint

const MINIMAP_WIDTH = 150
const MINIMAP_HEIGHT = 90
const MINIMAP_PADDING = 7

interface MinimapProjection {
  left: number
  top: number
  scale: number
  viewportLeft: number
  viewportTop: number
  viewportWidth: number
  viewportHeight: number
}

const workflowStore = useWorkflowStore()

/* 首次加载后自动 fit：把画布世界居中缩放到当前容器。解决“节点全在画布外”的失衡感（图中 canvasRef 上方已有） */
const fitView = () => {
  const el = canvasRef.value
  const nodes = workflowStore.nodes
  if (!el || !nodes.length) return
  // 用 store 里逻辑坐标（而不是 getBoundingClientRect 的变换后坐标）计算内容包络
  const xs = [...nodes.map((n) => n.x), workflowStore.endpointPositions.input?.x ?? 36]
  const ys = [...nodes.map((n) => n.y), workflowStore.endpointPositions.input?.y ?? 280]
  // 含 output 端点
  const out = workflowStore.endpointPositions.output
  if (out) {
    xs.push(out.x)
    ys.push(out.y)
  }
  const minX = Math.min(...xs)
  const maxX = Math.max(...xs) + 236 // 节点宽约 236
  const minY = Math.min(...ys)
  const maxY = Math.max(...ys) + 116 // 节点高约 116
  const view = el.getBoundingClientRect()
  const targetScale = Math.min(
    1.4,
    (view.width - 80) / Math.max(1, maxX - minX),
    (view.height - 80) / Math.max(1, maxY - minY),
  )
  const cx = (minX + maxX) / 2
  const cy = (minY + maxY) / 2
  workflowStore.setView(targetScale, {
    x: view.width / 2 - cx * targetScale,
    y: view.height / 2 - cy * targetScale,
  })
}
let hasFittedInitially = false
onMounted(() => {
  // 换项目后第一次出内容时 fit；用户后续手动拖拽编码不再被抢
  if (!hasFittedInitially && workflowStore.nodes.length > 0) {
    hasFittedInitially = true
    setTimeout(fitView, 50)
  }
})
watch(
  () => workflowStore.nodes.length,
  (next, prev) => {
    if (!hasFittedInitially && prev === 0 && next > 0) {
      hasFittedInitially = true
      setTimeout(fitView, 50)
    }
  },
)
const creativeToolsStore = useCreativeToolsStore()
const canvasRef = ref<HTMLElement | null>(null)
const moreMenuOpen = ref(false)
const selectionState = ref<{
  startX: number
  startY: number
  currentX: number
  currentY: number
} | null>(null)
const connectionState = ref<{ sourceId: string; currentX: number; currentY: number } | null>(null)
const dragState = ref<{
  ids: string[]
  startX: number
  startY: number
  origins: Record<string, { x: number; y: number }>
  before: WorkflowSnapshot
} | null>(null)
const endpointDragState = ref<{
  endpoint: 'input' | 'output'
  startX: number
  startY: number
  originX: number
  originY: number
  before: WorkflowSnapshot
} | null>(null)
const panState = ref<{ startX: number; startY: number; originX: number; originY: number } | null>(
  null,
)
const minimapDragState = ref<{
  startX: number
  startY: number
  originOffsetX: number
  originOffsetY: number
  mapScale: number
} | null>(null)

const isPanelOpen = computed(() => creativeToolsStore.activePanelId !== null)
const modeLabel = computed(() => (workflowStore.mode === 'select' ? '选择工具' : '平移画布'))
const modeDescription = computed(() =>
  workflowStore.mode === 'select'
    ? '拖拽节点或在空白处框选；不会移动背景'
    : '拖拽空白背景查看画布；不会改变节点和连线',
)

const canvasNodes = computed<CanvasNode[]>(() => {
  const maxX = Math.max(1120, ...workflowStore.nodes.map((node) => node.x + 236))
  const inputPosition = workflowStore.endpointPositions.input ?? { x: 36, y: 280 }
  const outputPosition = workflowStore.endpointPositions.output ?? { x: maxX + 170, y: 280 }
  return [
    {
      kind: 'endpoint',
      id: WORKFLOW_INPUT_ID,
      endpoint: 'input',
      name: '输入',
      description: '主题、初始文本和参考文件',
      color: '#9c9c9c',
      x: inputPosition.x,
      y: inputPosition.y,
      width: 184,
      height: 96,
      type: null,
    },
    ...workflowStore.nodes.map((node) => ({
      ...node,
      kind: 'tool' as const,
      width: 236,
      height: 116,
    })),
    {
      kind: 'endpoint',
      id: WORKFLOW_OUTPUT_ID,
      endpoint: 'output',
      name: '输出',
      description: '展示、保存和导出最终结果',
      color: '#999999',
      x: outputPosition.x,
      y: outputPosition.y,
      width: 184,
      height: 96,
      type: null,
    },
  ]
})

const connections = computed(() =>
  workflowStore.edges
    .map((edge) => ({
      from: canvasNodes.value.find((node) => node.id === edge.source),
      to: canvasNodes.value.find((node) => node.id === edge.target),
    }))
    .filter((connection): connection is { from: CanvasNode; to: CanvasNode } =>
      Boolean(connection.from && connection.to),
    ),
)

const minimapProjection = computed<MinimapProjection>(() => {
  const canvasRect = canvasRef.value?.getBoundingClientRect()
  const viewportWidth = (canvasRect?.width ?? 716) / workflowStore.scale
  const viewportHeight = (canvasRect?.height ?? 764) / workflowStore.scale
  const viewportLeft = -workflowStore.offset.x / workflowStore.scale
  const viewportTop = -workflowStore.offset.y / workflowStore.scale
  const items = canvasNodes.value
  const contentLeft = Math.min(...items.map((item) => item.x))
  const contentTop = Math.min(...items.map((item) => item.y))
  const contentRight = Math.max(...items.map((item) => item.x + item.width))
  const contentBottom = Math.max(...items.map((item) => item.y + item.height))
  const contentWidth = Math.max(1, contentRight - contentLeft)
  const contentHeight = Math.max(1, contentBottom - contentTop)
  // 以图内容中心建立稳定的鸟瞰范围，不把当前视口位置纳入 bounds，
  // 否则拖动小视口时 bounds 会跟着移动，视口框看起来像“粘”在原地。
  const worldWidth = Math.max(contentWidth + 160, viewportWidth * 1.2)
  const worldHeight = Math.max(contentHeight + 160, viewportHeight * 1.2)
  const contentCenterX = (contentLeft + contentRight) / 2
  const contentCenterY = (contentTop + contentBottom) / 2
  const left = contentCenterX - worldWidth / 2
  const top = contentCenterY - worldHeight / 2
  return {
    left,
    top,
    scale: Math.min(
      (MINIMAP_WIDTH - MINIMAP_PADDING * 2) / worldWidth,
      (MINIMAP_HEIGHT - MINIMAP_PADDING * 2) / worldHeight,
    ),
    viewportLeft,
    viewportTop,
    viewportWidth,
    viewportHeight,
  }
})

const minimapItems = computed(() => {
  const projection = minimapProjection.value
  return canvasNodes.value.map((item) => ({
    id: item.id,
    kind: item.kind,
    style: {
      left: `${MINIMAP_PADDING + (item.x - projection.left) * projection.scale}px`,
      top: `${MINIMAP_PADDING + (item.y - projection.top) * projection.scale}px`,
      width: `${Math.max(5, item.width * projection.scale)}px`,
      height: `${Math.max(4, item.height * projection.scale)}px`,
      '--mini-node-color': item.color,
    },
  }))
})

const minimapConnections = computed(() => {
  const projection = minimapProjection.value
  const point = (node: CanvasNode, port: 'in' | 'out') => {
    const raw = portPoint(node, port)
    return {
      x: MINIMAP_PADDING + (raw.x - projection.left) * projection.scale,
      y: MINIMAP_PADDING + (raw.y - projection.top) * projection.scale,
    }
  }
  return connections.value.map((connection) => ({
    key: `${connection.from.id}-${connection.to.id}`,
    path: connectionPathFromPoints(point(connection.from, 'out'), point(connection.to, 'in')),
  }))
})

const minimapViewportStyle = computed(() => {
  const projection = minimapProjection.value
  return {
    left: `${MINIMAP_PADDING + (projection.viewportLeft - projection.left) * projection.scale}px`,
    top: `${MINIMAP_PADDING + (projection.viewportTop - projection.top) * projection.scale}px`,
    width: `${Math.max(12, projection.viewportWidth * projection.scale)}px`,
    height: `${Math.max(10, projection.viewportHeight * projection.scale)}px`,
  }
})

const selectionBoxStyle = computed(() => {
  if (!selectionState.value || !canvasRef.value) return {}
  const rect = canvasRef.value.getBoundingClientRect()
  const { startX, startY, currentX, currentY } = selectionState.value
  return {
    left: `${Math.min(startX, currentX) - rect.left}px`,
    top: `${Math.min(startY, currentY) - rect.top}px`,
    width: `${Math.abs(currentX - startX)}px`,
    height: `${Math.abs(currentY - startY)}px`,
  }
})

const connectionPreviewPath = computed(() => {
  if (!connectionState.value) return ''
  const source = canvasNodes.value.find((node) => node.id === connectionState.value?.sourceId)
  if (!source) return ''
  return connectionPathFromPoints(portPoint(source, 'out'), {
    x: connectionState.value.currentX,
    y: connectionState.value.currentY,
  })
})

function nodeMark(type: CreativeToolType | null) {
  return { lyrics: 'Aa', image: '✦', video: '▶', audio: '∿' }[type ?? 'lyrics']
}

function toolIndex(id: string) {
  return workflowStore.nodes.findIndex((node) => node.id === id) + 1
}

function isNodeSelected(id: string) {
  return workflowStore.selectedNodeIds.includes(id)
}

function selectNode(node: CanvasNode) {
  if (node.kind !== 'tool' || workflowStore.mode !== 'select') return
  workflowStore.selectNode(node.id)
  emit('node-selected', node.toolId)
}

function handleNodePointerDown(event: PointerEvent, node: CanvasNode) {
  if (node.kind === 'endpoint') {
    if (workflowStore.mode === 'select') startEndpointDrag(event, node)
    return
  }
  if (workflowStore.mode !== 'select') return
  if (event.shiftKey) {
    workflowStore.selectNode(node.id, true)
    emit('node-selected', node.toolId)
    return
  }

  if (!isNodeSelected(node.id)) {
    workflowStore.selectNode(node.id)
    emit('node-selected', node.toolId)
  }

  const ids = workflowStore.selectedNodeIds.includes(node.id)
    ? [...workflowStore.selectedNodeIds]
    : [node.id]
  const origins = Object.fromEntries(
    ids.flatMap((id) => {
      const item = workflowStore.nodes.find((candidate) => candidate.id === id)
      return item ? [[id, { x: item.x, y: item.y }]] : []
    }),
  )
  dragState.value = {
    ids,
    startX: event.clientX,
    startY: event.clientY,
    origins,
    before: {
      nodes: workflowStore.nodes.map((item) => ({ ...item })),
      edges: workflowStore.edges.map((edge) => ({ ...edge })),
      endpointPositions: { ...workflowStore.endpointPositions },
    },
  }
  window.addEventListener('pointermove', handleNodeDrag)
  window.addEventListener('pointerup', finishNodeDrag, { once: true })
  window.addEventListener('pointercancel', finishNodeDrag, { once: true })
}

function startEndpointDrag(event: PointerEvent, node: CanvasEndpoint) {
  event.preventDefault()
  endpointDragState.value = {
    endpoint: node.endpoint,
    startX: event.clientX,
    startY: event.clientY,
    originX: node.x,
    originY: node.y,
    before: {
      nodes: workflowStore.nodes.map((item) => ({ ...item })),
      edges: workflowStore.edges.map((edge) => ({ ...edge })),
      endpointPositions: { ...workflowStore.endpointPositions },
    },
  }
  window.addEventListener('pointermove', handleEndpointDrag)
  window.addEventListener('pointerup', finishEndpointDrag, { once: true })
  window.addEventListener('pointercancel', finishEndpointDrag, { once: true })
}

function handleEndpointDrag(event: PointerEvent) {
  if (!endpointDragState.value) return
  const { endpoint, startX, startY, originX, originY } = endpointDragState.value
  workflowStore.moveEndpoint(
    endpoint,
    originX + (event.clientX - startX) / workflowStore.scale,
    originY + (event.clientY - startY) / workflowStore.scale,
  )
}

function finishEndpointDrag() {
  if (endpointDragState.value) workflowStore.commitEndpointMove(endpointDragState.value.before)
  endpointDragState.value = null
  window.removeEventListener('pointermove', handleEndpointDrag)
  window.removeEventListener('pointerup', finishEndpointDrag)
  window.removeEventListener('pointercancel', finishEndpointDrag)
}

function handleNodeDrag(event: PointerEvent) {
  if (!dragState.value) return
  const { ids, startX, startY, origins } = dragState.value
  workflowStore.moveNodes(
    ids.map((id) => ({
      id,
      x: origins[id].x + (event.clientX - startX) / workflowStore.scale,
      y: origins[id].y + (event.clientY - startY) / workflowStore.scale,
    })),
  )
}

function finishNodeDrag() {
  if (dragState.value) workflowStore.commitNodeMove(dragState.value.before)
  dragState.value = null
  window.removeEventListener('pointermove', handleNodeDrag)
}

function handleCanvasPointerDown(event: PointerEvent) {
  const target = event.target as HTMLElement | null
  if (target?.closest('button, .workflow-node')) return

  if (workflowStore.mode === 'pan') {
    panState.value = {
      startX: event.clientX,
      startY: event.clientY,
      originX: workflowStore.offset.x,
      originY: workflowStore.offset.y,
    }
    window.addEventListener('pointermove', handlePan)
    window.addEventListener('pointerup', finishPan, { once: true })
    window.addEventListener('pointercancel', finishPan, { once: true })
    return
  }

  selectionState.value = {
    startX: event.clientX,
    startY: event.clientY,
    currentX: event.clientX,
    currentY: event.clientY,
  }
  window.addEventListener('pointermove', handleSelection)
  window.addEventListener('pointerup', finishSelection, { once: true })
}

function handleSelection(event: PointerEvent) {
  if (!selectionState.value) return
  selectionState.value.currentX = event.clientX
  selectionState.value.currentY = event.clientY
}

function finishSelection() {
  if (!selectionState.value) return
  const state = selectionState.value
  const moved =
    Math.abs(state.currentX - state.startX) > 5 || Math.abs(state.currentY - state.startY) > 5
  if (!moved) {
    workflowStore.clearSelection()
  } else if (canvasRef.value) {
    const left = Math.min(state.startX, state.currentX)
    const right = Math.max(state.startX, state.currentX)
    const top = Math.min(state.startY, state.currentY)
    const bottom = Math.max(state.startY, state.currentY)
    const selectedIds = [
      ...canvasRef.value.querySelectorAll<HTMLElement>('.workflow-node[data-node-kind="tool"]'),
    ]
      .filter((element) => {
        const rect = element.getBoundingClientRect()
        return rect.right >= left && rect.left <= right && rect.bottom >= top && rect.top <= bottom
      })
      .map((element) => element.dataset.nodeId)
      .filter((id): id is string => Boolean(id))
    workflowStore.selectNodes(selectedIds)
    const selectedTool = workflowStore.nodes.find(
      (node) => node.id === selectedIds[selectedIds.length - 1],
    )
    if (selectedTool) emit('node-selected', selectedTool.toolId)
  }
  selectionState.value = null
  window.removeEventListener('pointermove', handleSelection)
}

function handlePan(event: PointerEvent) {
  if (!panState.value) return
  workflowStore.setView(workflowStore.scale, {
    x: panState.value.originX + event.clientX - panState.value.startX,
    y: panState.value.originY + event.clientY - panState.value.startY,
  })
}

function finishPan() {
  panState.value = null
  window.removeEventListener('pointermove', handlePan)
}

function portPoint(node: CanvasNode, port: 'in' | 'out') {
  return {
    x: node.x + (port === 'out' ? node.width : 0),
    y: node.y + node.height / 2,
  }
}

function connectionPath(from: CanvasNode, to: CanvasNode) {
  return connectionPathFromPoints(portPoint(from, 'out'), portPoint(to, 'in'))
}

function connectionPathFromPoints(from: { x: number; y: number }, to: { x: number; y: number }) {
  const bend = Math.max(48, Math.abs(to.x - from.x) / 2)
  return `M ${from.x} ${from.y} C ${from.x + bend} ${from.y}, ${to.x - bend} ${to.y}, ${to.x} ${to.y}`
}

function removeConnection(connection: { from: CanvasNode; to: CanvasNode }) {
  workflowStore.removeConnection(connection.from.id, connection.to.id)
  emit('notify', '连接已拆除')
}

function startConnection(event: PointerEvent, sourceId: string) {
  if (workflowStore.mode !== 'select') return
  event.preventDefault()
  const point = clientToWorld(event.clientX, event.clientY)
  connectionState.value = { sourceId, currentX: point.x, currentY: point.y }
  window.addEventListener('pointermove', handleConnectionPreview)
  window.addEventListener('pointerup', cancelConnection, { once: true })
  window.addEventListener('pointercancel', cancelConnection, { once: true })
}

function clientToWorld(clientX: number, clientY: number) {
  const rect = canvasRef.value?.getBoundingClientRect()
  if (!rect) return { x: clientX, y: clientY }
  return {
    x: (clientX - rect.left - workflowStore.offset.x) / workflowStore.scale,
    y: (clientY - rect.top - workflowStore.offset.y) / workflowStore.scale,
  }
}

function handleConnectionPreview(event: PointerEvent) {
  if (!connectionState.value) return
  const point = clientToWorld(event.clientX, event.clientY)
  connectionState.value.currentX = point.x
  connectionState.value.currentY = point.y
}

function finishConnection(targetId: string) {
  if (!connectionState.value) return
  const sourceId = connectionState.value.sourceId
  if (workflowStore.addConnection(sourceId, targetId)) {
    emit('notify', '连接已建立')
  } else {
    emit('notify', '连接无效：每个工具只接收一个主输入，或该连接会形成循环')
  }
  stopConnectionListeners()
}

function cancelConnection() {
  if (!connectionState.value) return
  stopConnectionListeners()
}

function stopConnectionListeners() {
  connectionState.value = null
  window.removeEventListener('pointermove', handleConnectionPreview)
  window.removeEventListener('pointerup', cancelConnection)
  window.removeEventListener('pointercancel', cancelConnection)
}

function deleteNode(node: CanvasNode) {
  if (node.kind !== 'tool') return
  workflowStore.removeNode(node.id)
  emit('notify', `已移除工具：${node.name}`)
}

function handleWheel(event: WheelEvent) {
  workflowStore.zoomBy(event.deltaY > 0 ? -0.05 : 0.05)
}

function toggleMoreMenu() {
  moreMenuOpen.value = !moreMenuOpen.value
}

function resetWorkflow() {
  moreMenuOpen.value = false
  while (workflowStore.canUndo) workflowStore.undo()
  emit('notify', '工作流画布已清空')
}

function startMinimapDrag(event: PointerEvent) {
  event.preventDefault()
  const projection = minimapProjection.value
  minimapDragState.value = {
    startX: event.clientX,
    startY: event.clientY,
    originOffsetX: workflowStore.offset.x,
    originOffsetY: workflowStore.offset.y,
    mapScale: projection.scale,
  }
  const target = event.currentTarget as HTMLElement | null
  try {
    target?.setPointerCapture?.(event.pointerId)
  } catch {
    // 某些浏览器/自动化环境的合成 pointer 事件不能设置 capture，仍由 window 监听完成拖拽。
  }
  window.addEventListener('pointermove', handleMinimapDrag)
  window.addEventListener('pointerup', finishMinimapDrag, { once: true })
  window.addEventListener('pointercancel', finishMinimapDrag, { once: true })
}

function handleMinimapDrag(event: PointerEvent) {
  if (!minimapDragState.value) return
  const { startX, startY, originOffsetX, originOffsetY, mapScale } = minimapDragState.value
  workflowStore.setView(workflowStore.scale, {
    x: originOffsetX - ((event.clientX - startX) / mapScale) * workflowStore.scale,
    y: originOffsetY - ((event.clientY - startY) / mapScale) * workflowStore.scale,
  })
}

function finishMinimapDrag() {
  minimapDragState.value = null
  window.removeEventListener('pointermove', handleMinimapDrag)
  window.removeEventListener('pointerup', finishMinimapDrag)
  window.removeEventListener('pointercancel', finishMinimapDrag)
}

onBeforeUnmount(() => {
  window.removeEventListener('pointermove', handleNodeDrag)
  window.removeEventListener('pointermove', handleEndpointDrag)
  window.removeEventListener('pointermove', handlePan)
  window.removeEventListener('pointermove', handleSelection)
  window.removeEventListener('pointermove', handleConnectionPreview)
  window.removeEventListener('pointermove', handleMinimapDrag)
  window.removeEventListener('pointerup', finishMinimapDrag)
  window.removeEventListener('pointercancel', finishMinimapDrag)
})
</script>

<style scoped>
.workflow-canvas {
  position: absolute;
  inset: 0;
  overflow: hidden;
  background-color: #0d0e11;
  background-image: radial-gradient(circle, rgba(161, 161, 161, 0.17) 1px, transparent 1px);
  background-position: 10px 10px;
  background-size: 24px 24px;
  color: #d7d8dc;
  cursor: default;
  touch-action: none;
}

.workflow-canvas.is-pan-mode {
  cursor: grab;
}

.workflow-canvas.is-pan-mode:active {
  cursor: grabbing;
}

.workflow-canvas.is-connecting {
  cursor: crosshair;
}

/* B19：运行期画布锁定。编辑操作由 store.isRunning 守卫拦截，这里提供视觉与交互反馈 */
.workflow-running-banner {
  position: absolute;
  top: 18px;
  left: 50%;
  transform: translateX(-50%);
  z-index: 6;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 8px 14px;
  border: 1px solid rgba(156, 156, 156, 0.45);
  border-radius: 8px;
  background: rgba(23, 27, 34, 0.92);
  color: #c8cdd6;
  font-size: 11px;
  line-height: 1.4;
  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.4);
}

.workflow-running-banner strong {
  color: #f2f4f8;
}

.workflow-running-spinner {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  border: 2px solid rgba(156, 156, 156, 0.25);
  border-top-color: #9c9c9c;
  animation: workflow-spin 0.9s linear infinite;
}

@keyframes workflow-spin {
  to {
    transform: rotate(360deg);
  }
}

/* 运行期间鼠标交互全部转向横幅提示，节点本身不可点 */
.workflow-canvas.is-running-locked .workflow-node,
.workflow-canvas.is-running-locked .workflow-connection-group {
  pointer-events: none;
}

.workflow-canvas.is-running-locked .workflow-node {
  opacity: 0.82;
}

.workflow-mode-hint {
  position: absolute;
  top: 18px;
  left: 76px;
  z-index: 5;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 6px 9px;
  border: 1px solid rgba(255, 255, 255, 0.09);
  border-radius: 7px;
  background: rgba(18, 19, 23, 0.82);
  color: #8f949f;
  font-size: 10px;
  pointer-events: none;
}

.workflow-mode-hint strong {
  color: #d9dce2;
  font-weight: 600;
}

.workflow-mode-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #9c9c9c;
  box-shadow: 0 0 0 3px rgba(156, 156, 156, 0.15);
}

.workflow-mode-dot.is-pan {
  background: #999999;
  box-shadow: 0 0 0 3px rgba(153, 153, 153, 0.15);
}

.workflow-connection-hint {
  position: absolute;
  top: 58px;
  left: 76px;
  z-index: 6;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 7px 10px;
  border: 1px solid rgba(156, 156, 156, 0.5);
  border-radius: 7px;
  background: rgba(34, 34, 34, 0.96);
  color: #e1e1e1;
  font-size: 10px;
}

.workflow-connection-hint button {
  padding: 2px 5px;
  border-radius: 4px;
  color: #c5c5c5;
  font-size: 10px;
}

.workflow-selection-box {
  position: absolute;
  z-index: 4;
  border: 1px solid rgba(156, 156, 156, 0.9);
  background: rgba(156, 156, 156, 0.12);
  pointer-events: none;
}

.workflow-world {
  position: absolute;
  inset: 0;
  transform-origin: 0 0;
  transition: transform 120ms ease-out;
}

.workflow-connections {
  position: absolute;
  inset: 0;
  width: 3200px;
  height: 1800px;
  overflow: visible;
  pointer-events: none;
}

.workflow-connections .workflow-connection-group {
  pointer-events: visiblePainted;
}

.workflow-connections .workflow-connection-hit {
  fill: none;
  stroke: transparent;
  stroke-width: 16;
  pointer-events: stroke;
  cursor: pointer;
}

.workflow-connections .workflow-connection-line {
  fill: none;
  stroke: rgba(224, 224, 224, 0.88);
  stroke-width: 2.25;
  stroke-linecap: round;
  stroke-linejoin: round;
  opacity: 0.92;
  pointer-events: none;
  filter: drop-shadow(0 0 2px rgba(196, 196, 196, 0.32));
}

.workflow-connections .workflow-connection-hit:hover + .workflow-connection-line {
  stroke: #eef6ff;
  filter: drop-shadow(0 0 4px rgba(196, 196, 196, 0.6));
}

.workflow-connections .workflow-connection-preview {
  stroke: #c9c9c9;
  stroke-width: 2.5;
  stroke-dasharray: 6 6;
  opacity: 0.95;
  filter: drop-shadow(0 0 3px rgba(156, 156, 156, 0.45));
}

.workflow-node {
  position: absolute;
  width: 236px;
  height: 116px;
  padding: 13px 14px 10px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 13px;
  background: #242529;
  cursor: grab;
  user-select: none;
  transition:
    border-color 160ms ease,
    box-shadow 160ms ease,
    transform 160ms ease;
}

.workflow-node:hover,
.workflow-node.is-selected {
  border-color: color-mix(in srgb, var(--node-color) 75%, white 5%);
}

.workflow-node.is-selected {
  transform: translateY(-2px);
  box-shadow:
    0 0 0 2px color-mix(in srgb, var(--node-color) 52%, transparent),
    0 8px 26px rgba(0, 0, 0, 0.24);
}

.workflow-node:active {
  cursor: grabbing;
}

.workflow-node.is-endpoint {
  width: 184px;
  height: 96px;
  padding: 15px 16px;
  cursor: grab;
  border-color: color-mix(in srgb, var(--node-color) 55%, transparent);
  background:
    linear-gradient(145deg, rgba(255, 255, 255, 0.08), rgba(255, 255, 255, 0.025)), #202126;
}

.workflow-node.is-endpoint p {
  margin-top: 8px;
  color: #989da8;
}

.workflow-endpoint-kicker {
  color: var(--node-color);
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.16em;
}

.workflow-endpoint-title {
  display: flex;
  align-items: center;
  gap: 7px;
  margin-top: 7px;
  color: #f1f2f4;
  font-size: 15px;
  font-weight: 600;
}

.workflow-endpoint-icon {
  color: var(--node-color);
  font-size: 19px;
  line-height: 1;
}

.workflow-node-head {
  display: flex;
  align-items: center;
  gap: 9px;
}

.workflow-node-icon {
  display: grid;
  width: 28px;
  height: 28px;
  flex: 0 0 auto;
  place-items: center;
  border-radius: 8px;
  background: color-mix(in srgb, var(--node-color) 20%, #27282d);
  color: var(--node-color);
  font-size: 14px;
  font-weight: 600;
}

.workflow-node-heading {
  min-width: 0;
  flex: 1;
}

.workflow-node-badge,
.workflow-node-footer {
  color: #858993;
  font-size: 9px;
}

.workflow-node-heading h3 {
  overflow: hidden;
  margin: 1px 0 0;
  color: #f0f0f1;
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.workflow-node-number {
  align-self: flex-start;
  color: #777a83;
  font-size: 10px;
}

.workflow-node p {
  display: -webkit-box;
  overflow: hidden;
  margin: 9px 0 0;
  color: #a1a4ad;
  font-size: 10px;
  line-height: 1.45;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.workflow-node-footer {
  margin-top: 5px;
}

.workflow-node-port {
  position: absolute;
  top: 50%;
  z-index: 2;
  width: 13px;
  height: 13px;
  margin-top: -6.5px;
  padding: 0;
  border: 1.5px solid #f4f8fc !important;
  border-radius: 50%;
  background: transparent !important;
  box-shadow:
    0 0 0 1px rgba(10, 13, 18, 0.86),
    0 0 4px rgba(234, 234, 234, 0.38);
  cursor: crosshair;
  transition:
    background-color 140ms ease,
    border-color 140ms ease,
    box-shadow 140ms ease,
    transform 140ms ease;
}

.workflow-node-port::after {
  position: absolute;
  top: 50%;
  left: 50%;
  width: 3px;
  height: 3px;
  border-radius: 50%;
  background: #f4f8fc;
  content: '';
  opacity: 0.82;
  transform: translate(-50%, -50%);
}

.workflow-node-port:hover,
.workflow-node-port.is-hot,
.workflow-node-port.is-target:hover {
  border-color: #ffffff !important;
  background: rgba(169, 169, 169, 0.2) !important;
  box-shadow:
    0 0 0 2px rgba(156, 156, 156, 0.16),
    0 0 9px rgba(190, 190, 190, 0.62);
  transform: scale(1.16);
}

.workflow-node-port:hover::after,
.workflow-node-port.is-hot::after {
  background: #ffffff;
  opacity: 1;
}

.workflow-node-port-in {
  left: -8px;
}

.workflow-node-port-out {
  right: -8px;
}

.workflow-canvas-toolbar,
.workflow-history-controls,
.workflow-zoom-controls,
.workflow-minimap {
  position: absolute;
  z-index: 5;
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(34, 35, 39, 0.94);
}

.workflow-canvas-toolbar {
  top: 24px;
  left: 18px;
  display: flex;
  width: 42px;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  padding: 6px 4px;
  border-radius: 10px;
}

.workflow-tool-button,
.workflow-history-controls button,
.workflow-zoom-controls button {
  display: grid;
  width: 32px;
  height: 32px;
  place-items: center;
  border-radius: 6px;
  color: #8d919b;
  font-size: 16px;
}

.workflow-tool-button:hover,
.workflow-tool-button.active,
.workflow-history-controls button:hover:not(:disabled),
.workflow-zoom-controls button:hover {
  background: rgba(255, 255, 255, 0.09);
  color: #f1f2f4;
}

.workflow-tool-button.active {
  position: relative;
  border: 1px solid #bebebe !important;
  background: #727272 !important;
  color: #ffffff !important;
  box-shadow:
    inset 0 0 0 1px rgba(255, 255, 255, 0.18),
    0 0 14px rgba(123, 123, 123, 0.52);
}

.workflow-tool-button.active::after {
  position: absolute;
  right: -4px;
  width: 4px;
  height: 16px;
  border-radius: 2px;
  background: #c9c9c9;
  box-shadow: 0 0 8px rgba(156, 156, 156, 0.8);
  content: '';
}

.workflow-add-button {
  color: #d5d7db;
  font-size: 22px;
  font-weight: 300;
}

.workflow-tool-divider {
  width: 25px;
  height: 1px;
  margin: 3px 0;
  background: rgba(255, 255, 255, 0.1);
}

.workflow-more-menu {
  position: absolute;
  top: 4px;
  left: 47px;
  display: grid;
  width: 150px;
  gap: 2px;
  padding: 5px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  background: #242529;
}

.workflow-more-menu button {
  padding: 7px 8px;
  border-radius: 5px;
  color: #aaaeb7;
  font-size: 10px;
  text-align: left;
}

.workflow-more-menu button:hover {
  background: rgba(255, 255, 255, 0.08);
  color: #fff;
}

.workflow-history-controls {
  bottom: 20px;
  left: 18px;
  display: flex;
  gap: 2px;
  padding: 4px;
  border-radius: 9px;
}

.workflow-history-controls button:disabled {
  cursor: not-allowed;
  opacity: 0.3;
}

.workflow-minimap {
  right: 18px;
  bottom: 62px;
  width: 150px;
  height: 90px;
  overflow: hidden;
  border-radius: 8px;
  background: rgba(35, 36, 40, 0.75);
  cursor: default;
  touch-action: none;
}

.workflow-minimap-connections {
  position: absolute;
  inset: 0;
  width: 150px;
  height: 90px;
  overflow: hidden;
  pointer-events: none;
}

.workflow-minimap-connections path {
  fill: none;
  stroke: rgba(202, 202, 202, 0.58);
  stroke-width: 0.8;
  stroke-linecap: round;
  pointer-events: none;
}

.workflow-minimap-node {
  position: absolute;
  box-sizing: border-box;
  border: 1px solid color-mix(in srgb, var(--mini-node-color) 75%, white 10%);
  border-radius: 2px;
  background: color-mix(in srgb, var(--mini-node-color) 28%, transparent);
  opacity: 0.78;
  pointer-events: none;
}

.workflow-minimap-node.is-endpoint {
  border-width: 1px;
  border-color: color-mix(in srgb, var(--mini-node-color) 88%, white 12%);
  border-radius: 3px;
  background: transparent;
  opacity: 0.95;
}

.workflow-minimap-node.is-selected {
  border-color: #ffffff;
  box-shadow: 0 0 0 1px rgba(255, 255, 255, 0.45);
}

.workflow-minimap-viewport {
  position: absolute;
  z-index: 2;
  box-sizing: border-box;
  min-width: 12px;
  min-height: 10px;
  border: 1px solid rgba(255, 255, 255, 0.9);
  border-radius: 2px;
  background: rgba(156, 156, 156, 0.1);
  box-shadow:
    0 0 0 1px rgba(156, 156, 156, 0.28),
    inset 0 0 0 1px rgba(255, 255, 255, 0.12);
  cursor: move;
  touch-action: none;
  transition:
    background-color 140ms ease,
    box-shadow 140ms ease;
}

.workflow-minimap-viewport:hover {
  background: rgba(156, 156, 156, 0.2);
  box-shadow:
    0 0 0 1px rgba(190, 190, 190, 0.7),
    0 0 8px rgba(156, 156, 156, 0.35),
    inset 0 0 0 1px rgba(255, 255, 255, 0.2);
}

.workflow-zoom-controls {
  right: 18px;
  bottom: 20px;
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 4px;
  border-radius: 9px;
}

.workflow-zoom-value {
  width: 52px !important;
  color: #b6bac2 !important;
  font-size: 10px !important;
}
</style>
