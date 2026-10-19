import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import type { CreativeToolInstance, CreativeToolType } from './creativeTools'
import { useCreativeToolsStore } from './creativeTools'
import { useToolRunsStore, type ToolRunStatus } from './toolRuns'
import { ElMessage } from '@/utils/toast'
import { readScoped, writeScoped } from '@/utils/storageScope'
import {
  getWorkflowDraft,
  saveWorkflowDraft,
  runWorkflow,
  getWorkflowRunPreview,
  type WorkflowCandidate,
  type WorkflowDraft,
  type WorkflowDraftNode,
  type WorkflowDraftResponse,
  type WorkflowRunPreview,
} from '@/serve/workflow'
import { waitForAgentJob } from '@/serve/agent'
import { useTaskStore } from './tasks'

export type { WorkflowCandidate } from '@/serve/workflow'

export type WorkflowCanvasMode = 'select' | 'pan'
export type WorkflowEndpoint = 'input' | 'output'

export const WORKFLOW_INPUT_ID = 'workflow-input'
export const WORKFLOW_OUTPUT_ID = 'workflow-output'

export interface WorkflowNode {
  id: string
  toolId: string
  type: CreativeToolInstance['type']
  name: string
  badge: string
  description: string
  color: string
  x: number
  y: number
  // 后端/AI 管理的节点会带 capabilityId，与 capability Runtime 的能力 ID 对应
  capabilityId?: string
  params?: Record<string, string>
  runStatus?: ToolRunStatus
  result?: { format?: string; content?: string } | null
  error?: string | null
  /** 每次成功/失败运行追加一条候选，下游需要多版本时必须手动选定 */
  candidates?: WorkflowCandidate[]
  selectedCandidateId?: string | null
  /** 服务端运行时状态：本次运行复用了上次结果（零调用零计费）。仅展示，不回存。 */
  reused?: boolean
}

export interface WorkflowEdge {
  source: string
  target: string
}

export interface WorkflowEndpointPosition {
  x: number
  y: number
}

export interface WorkflowSnapshot {
  nodes: WorkflowNode[]
  edges: WorkflowEdge[]
  endpointPositions: Partial<Record<WorkflowEndpoint, WorkflowEndpointPosition>>
}

function cloneNodes(nodes: WorkflowNode[]) {
  return nodes.map((node) => ({
    ...node,
    ...(node.params ? { params: { ...node.params } } : {}),
    ...(node.result
      ? { result: { ...node.result } }
      : node.result === undefined
        ? {}
        : { result: null }),
    ...(node.candidates
      ? { candidates: node.candidates.map((candidate) => ({ ...candidate })) }
      : {}),
  }))
}

function cloneEdges(edges: WorkflowEdge[]) {
  return edges.map((edge) => ({ ...edge }))
}

function cloneSnapshot(snapshot: WorkflowSnapshot): WorkflowSnapshot {
  return {
    nodes: cloneNodes(snapshot.nodes),
    edges: cloneEdges(snapshot.edges),
    endpointPositions: { ...snapshot.endpointPositions },
  }
}

function positionForIndex(index: number) {
  // 三列网格，与后端 position_x_for_index 同步；y 240 接 input 的输出插孔高度
  return {
    x: 300 + (index % 3) * 300,
    y: 280 + Math.floor(index / 3) * 200,
  }
}

const CAPABILITY_TO_TOOL_TYPE: Record<string, CreativeToolType> = {
  'lyrics.generate': 'lyrics',
  'image.generate': 'image',
  'video.generate': 'video',
}

const TOOL_TYPE_TO_CAPABILITY: Record<CreativeToolType, string> = {
  lyrics: 'lyrics.generate',
  image: 'image.generate',
  video: 'video.generate',
  audio: 'audio.generate',
}

export const useWorkflowStore = defineStore('workflow', () => {
  const nodes = ref<WorkflowNode[]>([])
  const edges = ref<WorkflowEdge[]>([])
  const endpointPositions = ref<Partial<Record<WorkflowEndpoint, WorkflowEndpointPosition>>>({
    input: { x: 36, y: 280 },
  })
  const selectedNodeId = ref<string | null>(null)
  const selectedNodeIds = ref<string[]>([])
  const mode = ref<WorkflowCanvasMode>('select')
  const scale = ref(1)
  const offset = ref({ x: 0, y: 0 })
  const undoStack = ref<WorkflowSnapshot[]>([])
  const redoStack = ref<WorkflowSnapshot[]>([])
  // 后端 Draft 状态：AI/用户共用的唯一真源；本地 localStorage 退化为缓存
  const revision = ref(0)
  const workflowId = ref<number | null>(null)
  const isApplyingRemote = ref(false)
  const currentProjectId = ref<number | null>(null)
  let currentStorageKey = 'mnc-workflow-draft'
  let serverSaveTimer: number | null = null
  let runningPollTimer: number | null = null

  const selectedNode = computed(
    () => nodes.value.find((node) => node.id === selectedNodeId.value) ?? null,
  )
  const canUndo = computed(() => undoStack.value.length > 0)
  const canRedo = computed(() => redoStack.value.length > 0)
  // B19：任一节点 running 即表现为运行中，编辑操作一律无效化（后端 WORKFLOW_BUSY 是第二道防线）
  const isRunning = computed(() => nodes.value.some((node) => node.runStatus === 'running'))
  /** 运行期编辑守卫：所有写操作统一从此过，避免漏改某个入口。 */
  function editBlocked(): boolean {
    return isRunning.value
  }

  function snapshot(): WorkflowSnapshot {
    return {
      nodes: cloneNodes(nodes.value),
      edges: cloneEdges(edges.value),
      endpointPositions: { ...endpointPositions.value },
    }
  }

  function persist() {
    writeScoped(
      currentStorageKey,
      JSON.stringify({
        nodes: nodes.value,
        edges: edges.value,
        endpointPositions: endpointPositions.value,
        selectedNodeId: selectedNodeId.value,
        selectedNodeIds: selectedNodeIds.value,
        revision: revision.value,
        workflowId: workflowId.value,
      }),
    )
    scheduleServerSave()
  }

  function scheduleServerSave() {
    if (isApplyingRemote.value) return
    if (serverSaveTimer !== null) window.clearTimeout(serverSaveTimer)
    serverSaveTimer = window.setTimeout(() => {
      serverSaveTimer = null
      void saveDraftToServer()
    }, 600)
  }

  /** 前端的节点/连线/端点位置 → 后端 Draft（端点也作为节点保存）。 */
  function buildDraftPayload(): WorkflowDraft {
    const toolNodes: WorkflowDraftNode[] = nodes.value.map((node) => ({
      id: node.id,
      kind: 'tool',
      toolId: node.toolId,
      type: node.type,
      capability_id: node.capabilityId ?? TOOL_TYPE_TO_CAPABILITY[node.type],
      name: node.name,
      badge: node.badge,
      description: node.description,
      color: node.color,
      x: node.x,
      y: node.y,
      params: node.params,
      runStatus: node.runStatus,
      result: node.result ?? null,
      error: node.error ?? null,
      candidates: node.candidates ?? [],
      selectedCandidateId: node.selectedCandidateId ?? null,
    }))
    const inputPosition = endpointPositions.value.input ?? { x: 36, y: 280 }
    // 输出端点贴最右节点 + 隙缝；之前 Math.max(1120, ...) 的地板值会把输出推到画布可视区外
    const maxX = Math.max(320, ...nodes.value.map((node) => node.x + 236))
    const outputPosition = endpointPositions.value.output ?? { x: maxX + 86, y: 280 }
    return {
      nodes: [
        {
          id: WORKFLOW_INPUT_ID,
          kind: 'endpoint',
          endpoint: 'input',
          name: '输入',
          description: '主题、初始文本和参考文件',
          x: inputPosition.x,
          y: inputPosition.y,
        },
        ...toolNodes,
        {
          id: WORKFLOW_OUTPUT_ID,
          kind: 'endpoint',
          endpoint: 'output',
          name: '输出',
          description: '展示、保存和导出最终结果',
          x: outputPosition.x,
          y: outputPosition.y,
        },
      ],
      edges: cloneEdges(edges.value),
    }
  }

  /** 把后端/AI 管理的节点映射成本地 Block 实例，供参数面板、连线和左侧列表继续使用现有交互。 */
  function bridgeServerManagedNodes(toolNodes: WorkflowDraftNode[]) {
    const creativeToolsStore = useCreativeToolsStore()
    const toolRunsStore = useToolRunsStore()
    for (const node of toolNodes) {
      if (!node.capability_id || !node.toolId) continue
      const toolType = CAPABILITY_TO_TOOL_TYPE[node.capability_id]
      if (!toolType) continue
      creativeToolsStore.ensureToolInstance(node.toolId, toolType, node.params)
      if (node.runStatus) {
        toolRunsStore.setRecord(node.toolId, {
          status: node.runStatus,
          content: node.result?.content ?? null,
          error: node.error ?? null,
        })
      }
      node.params = creativeToolsStore.instances.find((item) => item.id === node.toolId)?.params
    }
  }

  /** degenerate 布局自检：两个工具节点中心距小于 100px（节点宽 236）视为叠扁，拉回三列网格。
   *  只在持久化/快照时触发；不影响用户拖拽中的合法位置。 */
  function fixupDegenerateLayout() {
    const tools = [...nodes.value].filter(
      (n) => n.id !== WORKFLOW_INPUT_ID && n.id !== WORKFLOW_OUTPUT_ID,
    )
    if (tools.length <= 1) return false
    const cx = (n: { x: number }) => n.x + 118
    for (let i = 0; i < tools.length; i++) {
      for (let j = i + 1; j < tools.length; j++) {
        if (Math.abs(cx(tools[i]!) - cx(tools[j]!)) < 100) {
          for (const [idx, n] of tools.entries()) {
            n.x = 300 + (idx % 3) * 300
            n.y = 280 + Math.floor(idx / 3) * 200
          }
          return true
        }
      }
    }
    return false
  }

  /** 应用后端 Draft（AI 改图、改参、运行状态都从这里进入画布）。 */
  function applyWorkflowSnapshot(
    draft: WorkflowDraft,
    nextRevision?: number,
    nextId?: number | null,
  ) {
    // AI 快照到达意味着服务器已持有最新 Draft：取消排队中的本地防抖保存，
    // 否则本地旧图会反写服务器，把 AI 刚连好的边覆盖掉。
    if (serverSaveTimer !== null) {
      window.clearTimeout(serverSaveTimer)
      serverSaveTimer = null
    }
    isApplyingRemote.value = true
    try {
      const rawNodes = Array.isArray(draft.nodes) ? draft.nodes : []
      const endpointNodes = rawNodes.filter((node) => node.kind === 'endpoint')
      const toolNodes = rawNodes.filter((node) => node.kind !== 'endpoint')
      bridgeServerManagedNodes(toolNodes)
      // 幻影 Block 对账：助手快照才是画布的唯一事实来源。
      // 曾经桥接过、但本快照里已没有对应节点的 assistant-* 实例是孤儿（例如助手
      // 中途 clear 又重建），留在左侧列表会造成“工具数量与画布节点不对等”。
      const creativeToolsStore = useCreativeToolsStore()
      const aliveToolIds = new Set(toolNodes.map((node) => String(node.toolId ?? node.id)))
      for (const instance of [...creativeToolsStore.instances]) {
        if (instance.id.startsWith('assistant-') && !aliveToolIds.has(instance.id)) {
          creativeToolsStore.removeTool(instance.id)
        }
      }
      const nextEndpointPositions: Partial<Record<WorkflowEndpoint, WorkflowEndpointPosition>> = {}
      for (const endpointNode of endpointNodes) {
        if (endpointNode.endpoint === 'input' || endpointNode.endpoint === 'output') {
          nextEndpointPositions[endpointNode.endpoint] = {
            x: Number(endpointNode.x ?? 0),
            y: Number(endpointNode.y ?? 0),
          }
        }
      }
      nodes.value = toolNodes
        .map((node) => ({
          id: String(node.id),
          toolId: String(node.toolId ?? node.id),
          type: (node.type as CreativeToolType) ?? 'lyrics',
          name: String(node.name ?? '生成工具'),
          badge: String(node.badge ?? ''),
          description: String(node.description ?? ''),
          color: String(node.color ?? '#b7b7b7'),
          x: Number(node.x ?? 0),
          y: Number(node.y ?? 0),
          capabilityId: node.capability_id,
          params: node.params,
          runStatus: node.runStatus,
          result: node.result ?? null,
          error: node.error ?? null,
          reused: node.reused === true,
          candidates: Array.isArray(node.candidates)
            ? node.candidates.map((candidate): WorkflowCandidate => ({
                id: String(candidate.id),
                status: candidate.status as WorkflowCandidate['status'],
                content: candidate.content ?? null,
                error: candidate.error ?? null,
                created_at: candidate.created_at,
              }))
            : [],
          selectedCandidateId: node.selectedCandidateId ?? null,
        }))
        .filter((node) => node.id)
      edges.value = Array.isArray(draft.edges) ? cloneEdges(draft.edges) : []
      if (nextEndpointPositions.input) {
        endpointPositions.value = { ...endpointPositions.value, ...nextEndpointPositions }
      }
      if (nextRevision != null) revision.value = nextRevision
      if (nextId !== undefined) workflowId.value = nextId
      selectedNodeId.value = null
      selectedNodeIds.value = []
      // 只写本地缓存，不再回写服务器——这条数据本就来自服务器
      fixupDegenerateLayout() // 应用快照时先拉住重叠，别污染视图
      writeScoped(
        currentStorageKey,
        JSON.stringify({
          nodes: nodes.value,
          edges: edges.value,
          endpointPositions: endpointPositions.value,
          revision: revision.value,
          workflowId: workflowId.value,
        }),
      )
      // 有节点还在 running 时启动后台轮询，等 Worker 完成后把终态刷到画布
      if (nodes.value.some((node) => node.runStatus === 'running')) {
        startRunningPoll()
      }
    } finally {
      isApplyingRemote.value = false
    }
  }

  /** 后台轮询：Workflow 跑着的时候每 1.5s 拉一次 Draft，直到没有节点在 running。 */
  function startRunningPoll() {
    if (runningPollTimer !== null) return
    runningPollTimer = window.setInterval(() => {
      if (!nodes.value.some((node) => node.runStatus === 'running')) {
        window.clearInterval(runningPollTimer!)
        runningPollTimer = null
        return
      }
      void refreshFromServer(currentProjectId.value)
    }, 1500)
  }

  /** 项目切换/组件销毁前调用，停掉排队中的保存和轮询。 */
  function cancelPendingSync() {
    if (serverSaveTimer !== null) {
      window.clearTimeout(serverSaveTimer)
      serverSaveTimer = null
    }
    if (runningPollTimer !== null) {
      window.clearInterval(runningPollTimer)
      runningPollTimer = null
    }
  }

  async function refreshFromServer(projectId: number | null) {
    try {
      const response = await getWorkflowDraft(projectId)
      // 本地有用户手动搭的内容而服务器还是默认草稿：把本地迁移上去
      if (response.revision === 0 && nodes.value.length > 0) {
        revision.value = response.revision
        workflowId.value = response.id
        await saveDraftToServer()
        return
      }
      applyWorkflowSnapshot(response.draft, response.revision, response.id)
    } catch {
      // 网络失败时保持本地面布可用
    }
  }

  async function saveDraftToServer() {
    if (isApplyingRemote.value) return
    try {
      const response: WorkflowDraftResponse = await saveWorkflowDraft(
        currentProjectId.value,
        buildDraftPayload(),
        revision.value,
      )
      isApplyingRemote.value = true
      try {
        revision.value = response.revision
        workflowId.value = response.id
      } finally {
        isApplyingRemote.value = false
      }
    } catch (error) {
      // 409：服务器已有更新的 Draft（例如 AI 先改了），拉取最新版避免覆盖；
      // B19 修订：运行中被拒（WORKFLOW_BUSY）必须告诉用户原因而不是静默回弹，
      // 否则用户会以为画布“卡了”。
      const errObj = error as { response?: { status?: number; data?: { detail?: unknown } } }
      const status = errObj.response?.status
      const detail = errObj.response?.data?.detail
      if (status === 409 && typeof detail === 'string' && detail.includes('WORKFLOW_BUSY')) {
        ElMessage.warning(detail.replace('WORKFLOW_BUSY：', ''))
      }
      if (status === 409) await refreshFromServer(currentProjectId.value)
    }
  }

  // ---------- WF1-③ 全局运行前报价 ----------
  /** 最近一次从后端拿到的费用/缓存预览；弹窗打开与运行结束后都会刷新。 */
  const runPreview = ref<WorkflowRunPreview | null>(null)
  const runPreviewLoading = ref(false)

  async function fetchRunPreview(): Promise<WorkflowRunPreview | null> {
    runPreviewLoading.value = true
    try {
      runPreview.value = await getWorkflowRunPreview(currentProjectId.value)
      return runPreview.value
    } catch {
      runPreview.value = null
      return null
    } finally {
      runPreviewLoading.value = false
    }
  }

  /** 画布节点角标依据：预览原始数据 → node_id → 状态/原因，供画布渲染「将重跑/命中缓存」。 */
  const nodeRunHints = computed(() => {
    const hints = new Map<string, { state: string; reason: string | null }>()
    for (const chain of runPreview.value?.chains ?? []) {
      for (const block of chain.blocks) {
        hints.set(block.node_id, { state: block.state, reason: block.reason })
      }
    }
    return hints
  })

  async function runCurrentWorkflow(): Promise<ToolRunStatus> {
    if (isRunning.value) return 'running'
    await saveDraftToServer()
    const submission = await runWorkflow(currentProjectId.value)
    // 立即显示 running 节点，让用户知道任务已排队
    if (submission.draft) {
      applyWorkflowSnapshot(submission.draft, submission.revision, submission.id)
    }
    if (!submission.job_id) {
      void fetchRunPreview()
      return submission.status as ToolRunStatus
    }
    try {
      // 视频生成实测 1~5 分钟（后端轮询兜底 10 分钟），前端等待预算放宽到 12 分钟
      const job = await waitForAgentJob(submission.job_id, 12 * 60 * 1000)
      // B28 防重复提醒：手动运行路径的组件自己会弹 toast + 提示音，
      // 登记给 tasks 轮询，别再重复弹第二次通知。
      useTaskStore().markTerminalNotified(job.id)
      await refreshFromServer(currentProjectId.value)
      // 运行结束后费用画像会变（命中/过期全重算），保留下次打开时的准确性
      void fetchRunPreview()
      const jobStatus = (job.result?.status as ToolRunStatus | undefined) ?? job.status
      if (jobStatus === 'succeeded') return 'succeeded'
      if (jobStatus === 'model_unavailable') return 'model_unavailable'
      return 'failed'
    } catch (error) {
      void fetchRunPreview()
      await refreshFromServer(currentProjectId.value)
      throw error
    }
  }

  function clearSelection() {
    selectedNodeId.value = null
    selectedNodeIds.value = []
    persist()
  }

  function loadForProject(projectId: number | null) {
    currentProjectId.value = projectId
    currentStorageKey = projectId == null ? 'mnc-workflow-draft' : `mnc-workflow-draft:${projectId}`
    undoStack.value = []
    redoStack.value = []
    scale.value = 1
    offset.value = { x: 0, y: 0 }
    try {
      const raw = readScoped(currentStorageKey)
      if (!raw) {
        nodes.value = []
        edges.value = []
        endpointPositions.value = { input: { x: 36, y: 280 } }
        selectedNodeId.value = null
        selectedNodeIds.value = []
        void refreshFromServer(projectId)
        return
      }
      const parsed = JSON.parse(raw) as {
        nodes?: WorkflowNode[]
        edges?: WorkflowEdge[]
        selectedNodeId?: string | null
        selectedNodeIds?: string[]
        endpointPositions?: Partial<Record<WorkflowEndpoint, WorkflowEndpointPosition>>
        revision?: number
        workflowId?: number | null
      }
      const validNodeIds = new Set((parsed.nodes ?? []).map((node) => node.id))
      nodes.value = Array.isArray(parsed.nodes) ? parsed.nodes : []
      edges.value = Array.isArray(parsed.edges)
        ? parsed.edges
            .filter(
              (edge) =>
                [WORKFLOW_INPUT_ID, WORKFLOW_OUTPUT_ID].includes(edge.source) ||
                validNodeIds.has(edge.source),
            )
            .filter(
              (edge) =>
                [WORKFLOW_INPUT_ID, WORKFLOW_OUTPUT_ID].includes(edge.target) ||
                validNodeIds.has(edge.target),
            )
        : []
      endpointPositions.value = parsed.endpointPositions ?? { input: { x: 36, y: 280 } }
      revision.value = parsed.revision ?? 0
      workflowId.value = parsed.workflowId ?? null
      selectedNodeIds.value = Array.isArray(parsed.selectedNodeIds)
        ? parsed.selectedNodeIds.filter((id) => validNodeIds.has(id))
        : parsed.selectedNodeId && validNodeIds.has(parsed.selectedNodeId)
          ? [parsed.selectedNodeId]
          : []
      selectedNodeId.value = selectedNodeIds.value[selectedNodeIds.value.length - 1] ?? null
      void refreshFromServer(projectId)
    } catch {
      nodes.value = []
      edges.value = []
      endpointPositions.value = { input: { x: 36, y: 280 } }
      selectedNodeId.value = null
      selectedNodeIds.value = []
      void refreshFromServer(projectId)
    }
  }

  function pushUndo(previous: WorkflowSnapshot) {
    undoStack.value.push(cloneSnapshot(previous))
    if (undoStack.value.length > 50) undoStack.value.shift()
    redoStack.value = []
  }

  function addNode(tool: CreativeToolInstance) {
    if (editBlocked() || nodes.value.some((node) => node.toolId === tool.id)) return
    const position = positionForIndex(nodes.value.length)
    pushUndo(snapshot())
    const nodeId = `workflow-node-${tool.id}`
    nodes.value.push({
      id: nodeId,
      toolId: tool.id,
      type: tool.type,
      name: tool.name,
      badge: tool.badge,
      description: tool.description,
      color: tool.color,
      ...position,
    })
    selectedNodeId.value = nodeId
    selectedNodeIds.value = [nodeId]
    persist()
  }

  function syncWithTools(tools: CreativeToolInstance[]) {
    if (editBlocked()) return
    const validIds = new Set(tools.map((tool) => tool.id))
    // 后端/AI 管理的节点（带 capabilityId）即使没有对应的左侧 Block 实例也保留
    const validNodeIds = new Set(
      nodes.value
        .filter((node) => validIds.has(node.toolId) || node.capabilityId)
        .map((node) => node.id),
    )
    nodes.value = nodes.value.filter((node) => validIds.has(node.toolId) || node.capabilityId)
    edges.value = edges.value.filter(
      (edge) =>
        (edge.source === WORKFLOW_INPUT_ID ||
          edge.source === WORKFLOW_OUTPUT_ID ||
          validNodeIds.has(edge.source)) &&
        (edge.target === WORKFLOW_INPUT_ID ||
          edge.target === WORKFLOW_OUTPUT_ID ||
          validNodeIds.has(edge.target)),
    )
    for (const tool of tools) {
      if (nodes.value.some((node) => node.toolId === tool.id)) continue
      const position = positionForIndex(nodes.value.length)
      nodes.value.push({
        id: `workflow-node-${tool.id}`,
        toolId: tool.id,
        type: tool.type,
        name: tool.name,
        badge: tool.badge,
        description: tool.description,
        color: tool.color,
        ...position,
      })
    }
    const currentIds = new Set(nodes.value.map((node) => node.id))
    selectedNodeIds.value = selectedNodeIds.value.filter((id) => currentIds.has(id))
    selectedNodeId.value = selectedNodeIds.value[selectedNodeIds.value.length - 1] ?? null
    persist()
  }

  function selectNode(id: string, additive = false) {
    if (!nodes.value.some((node) => node.id === id)) return
    if (additive) {
      selectedNodeIds.value = selectedNodeIds.value.includes(id)
        ? selectedNodeIds.value.filter((selectedId) => selectedId !== id)
        : [...selectedNodeIds.value, id]
    } else {
      selectedNodeIds.value = [id]
    }
    selectedNodeId.value = selectedNodeIds.value[selectedNodeIds.value.length - 1] ?? null
  }

  function selectNodes(ids: string[]) {
    const validIds = new Set(nodes.value.map((node) => node.id))
    selectedNodeIds.value = ids.filter((id) => validIds.has(id))
    selectedNodeId.value = selectedNodeIds.value[selectedNodeIds.value.length - 1] ?? null
  }

  function moveNode(id: string, x: number, y: number) {
    const node = nodes.value.find((item) => item.id === id)
    if (!node || editBlocked()) return
    node.x = Math.round(x)
    node.y = Math.round(y)
    // 拖动过程不 persist：每帧 JSON.stringify 全图 + localStorage 同步写入会阻塞主线程，
    // 在画布上表现就是“肉眼可见的一帧一帧的卡”。终点由 commitNodeMove 负责持久化。
  }

  function moveNodes(moves: { id: string; x: number; y: number }[]) {
    if (editBlocked()) return
    for (const move of moves) {
      const node = nodes.value.find((item) => item.id === move.id)
      if (!node) continue
      node.x = Math.round(move.x)
      node.y = Math.round(move.y)
    }
    // 同上：只在拖动结束（commitNodeMove）时一次性写回。
  }

  function moveEndpoint(endpoint: WorkflowEndpoint, x: number, y: number) {
    if (editBlocked()) return
    endpointPositions.value[endpoint] = { x: Math.round(x), y: Math.round(y) }
    // 同样不每帧 persist，终点由 commitEndpointMove 负责统一写入。
  }

  function commitEndpointMove(previous: WorkflowSnapshot) {
    if (editBlocked()) return
    if (JSON.stringify(previous.endpointPositions) === JSON.stringify(endpointPositions.value))
      return
    pushUndo(previous)
    persist()
  }

  function commitNodeMove(previous: WorkflowSnapshot) {
    if (editBlocked()) return
    if (JSON.stringify(previous) === JSON.stringify(snapshot())) return
    pushUndo(previous)
    persist()
  }

  function removeNodes(ids: string[]) {
    if (editBlocked()) return
    const removeIds = new Set(ids)
    if (!removeIds.size) return
    const hasNode = nodes.value.some((node) => removeIds.has(node.id))
    if (!hasNode) return
    pushUndo(snapshot())
    nodes.value = nodes.value.filter((node) => !removeIds.has(node.id))
    edges.value = edges.value.filter(
      (edge) => !removeIds.has(edge.source) && !removeIds.has(edge.target),
    )
    selectedNodeIds.value = selectedNodeIds.value.filter((id) => !removeIds.has(id))
    selectedNodeId.value = selectedNodeIds.value[selectedNodeIds.value.length - 1] ?? null
    persist()
  }

  function removeNode(id: string) {
    removeNodes([id])
  }

  function removeNodeByToolId(toolId: string) {
    const node = nodes.value.find((item) => item.toolId === toolId)
    if (node) removeNode(node.id)
  }

  /** 在多版本里指定下游引用哪一条（一次只能有一个） */
  function selectCandidate(nodeId: string, candidateId: string) {
    const node = nodes.value.find((item) => item.id === nodeId)
    if (!node || editBlocked()) return
    node.selectedCandidateId = candidateId
    persist()
  }

  function deleteSelected() {
    removeNodes(selectedNodeIds.value)
  }

  function wouldCreateCycle(source: string, target: string) {
    const visited = new Set<string>()
    const pending = [target]
    while (pending.length) {
      const current = pending.pop()
      if (!current || visited.has(current)) continue
      if (current === source) return true
      visited.add(current)
      for (const edge of edges.value) {
        if (edge.source === current) pending.push(edge.target)
      }
    }
    return false
  }

  function addConnection(source: string, target: string): boolean {
    if (editBlocked()) return false
    const validIds = new Set(nodes.value.map((node) => node.id))
    const isValidSource = source === WORKFLOW_INPUT_ID || validIds.has(source)
    const isValidTarget = target === WORKFLOW_OUTPUT_ID || validIds.has(target)
    if (!isValidSource || !isValidTarget || source === target) return false
    if (source === WORKFLOW_OUTPUT_ID || target === WORKFLOW_INPUT_ID) return false
    if (edges.value.some((edge) => edge.source === source && edge.target === target)) return false
    // 第一版只允许一个主输入，避免无意中产生图上的合并；同一输出可以连接多个下游节点。
    if (target !== WORKFLOW_OUTPUT_ID && edges.value.some((edge) => edge.target === target)) {
      return false
    }
    if (wouldCreateCycle(source, target)) return false
    pushUndo(snapshot())
    edges.value.push({ source, target })
    persist()
    return true
  }

  function removeConnection(source: string, target: string) {
    if (editBlocked()) return
    const index = edges.value.findIndex((edge) => edge.source === source && edge.target === target)
    if (index < 0) return
    pushUndo(snapshot())
    edges.value.splice(index, 1)
    persist()
  }

  function undo() {
    if (editBlocked()) return
    const previous = undoStack.value.pop()
    if (!previous) return
    redoStack.value.push(snapshot())
    nodes.value = cloneNodes(previous.nodes)
    edges.value = cloneEdges(previous.edges)
    endpointPositions.value = { ...previous.endpointPositions }
    selectedNodeIds.value = []
    selectedNodeId.value = null
    persist()
  }

  function redo() {
    if (editBlocked()) return
    const next = redoStack.value.pop()
    if (!next) return
    undoStack.value.push(snapshot())
    nodes.value = cloneNodes(next.nodes)
    edges.value = cloneEdges(next.edges)
    endpointPositions.value = { ...next.endpointPositions }
    selectedNodeIds.value = []
    selectedNodeId.value = null
    persist()
  }

  function setMode(next: WorkflowCanvasMode) {
    mode.value = next
  }

  function zoomBy(delta: number) {
    scale.value = Math.min(2, Math.max(0.25, Number((scale.value + delta).toFixed(2))))
  }

  function resetView() {
    scale.value = 1
    offset.value = { x: 0, y: 0 }
  }

  function setView(nextScale: number, nextOffset: { x: number; y: number }) {
    scale.value = Math.min(2, Math.max(0.25, nextScale))
    offset.value = nextOffset
  }

  function panBy(dx: number, dy: number) {
    offset.value = { x: offset.value.x + dx, y: offset.value.y + dy }
  }

  return {
    nodes,
    edges,
    endpointPositions,
    selectedNodeId,
    selectedNodeIds,
    selectedNode,
    mode,
    scale,
    offset,
    canUndo,
    canRedo,
    revision,
    workflowId,
    isApplyingRemote,
    isRunning,
    nodeWidth: 236,
    nodeHeight: 116,
    loadForProject,
    addNode,
    syncWithTools,
    selectNode,
    selectNodes,
    clearSelection,
    moveNode,
    moveNodes,
    commitNodeMove,
    moveEndpoint,
    commitEndpointMove,
    removeNode,
    removeNodes,
    removeNodeByToolId,
    deleteSelected,
    selectCandidate,
    addConnection,
    removeConnection,
    undo,
    redo,
    setMode,
    zoomBy,
    resetView,
    setView,
    panBy,
    buildDraftPayload,
    applyWorkflowSnapshot,
    refreshFromServer,
    saveDraftToServer,
    runCurrentWorkflow,
    runPreview,
    runPreviewLoading,
    fetchRunPreview,
    nodeRunHints,
    cancelPendingSync,
  }
})
