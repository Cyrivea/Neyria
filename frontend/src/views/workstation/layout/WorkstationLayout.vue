<template>
  <div class="workstation-layout">
    <div class="app-shell">
      <div class="app-frame">
        <header class="topbar">
          <div class="topbar-left">
            <div class="title-row">
              <div class="app-title">Neyria</div>
            </div>
          </div>

          <div class="topbar-center">
            <ProjectSwitcher
              @create="openCreateProjectDialog"
              @open-picker="handleOpenProjectPicker"
              @open-recent="handleOpenRecentProject"
            />
          </div>

          <div class="topbar-right">
            <div class="status-row">
              <div ref="saveModeWrapRef" class="status-button-wrap">
                <button
                  class="status-button"
                  type="button"
                  :aria-expanded="isSaveModeOpen"
                  @click.stop="isSaveModeOpen = !isSaveModeOpen"
                >
                  <Transition name="save-mode-label" mode="out-in">
                    <span :key="currentSaveModeLabel" class="status-button-label">{{
                      currentSaveModeLabel
                    }}</span>
                  </Transition>
                  <span>▾</span>
                </button>
                <Transition name="panel-float">
                  <div v-if="isSaveModeOpen" class="status-panel">
                    <button
                      v-for="item in saveModeOptions"
                      :key="item.value"
                      class="status-panel-option"
                      type="button"
                      :disabled="isSaveModeApplying"
                      @click="handleSelectSaveMode(item)"
                    >
                      {{ item.label }}
                    </button>
                  </div>
                </Transition>
              </div>
              <button class="status-button" type="button" title="退出登录" @click="handleLogout">
                <span class="status-button-label">退出登录</span>
              </button>
            </div>
            <div class="window-controls" aria-hidden="true">
              <div class="window-dot"></div>
              <div class="window-dot"></div>
              <div class="window-dot"></div>
            </div>
          </div>
        </header>

        <main class="main-grid" :class="{ 'assistant-collapsed': isAssistantCollapsed }">
          <aside class="left-column">
            <div class="icon-rail" aria-label="Side navigation">
              <button
                class="icon-button"
                title="Billing"
                type="button"
                aria-label="打开计费与用量页"
                @click="router.push('/billing')"
              >
                ☰
              </button>
              <div class="rail-spacer"></div>
              <button
                class="icon-button"
                :class="{ active: isSettingsOpen }"
                title="Settings"
                type="button"
                :aria-expanded="isSettingsOpen"
                aria-label="打开工作站设置"
                @click="isSettingsOpen = !isSettingsOpen"
              >
                ⚙
              </button>
            </div>

            <div class="sidebar-stack">
              <div class="sidebar-workspace">
                <section class="section-card creative-tool-section">
                  <div class="section-body">
                    <TransitionGroup name="creative-tool-list" tag="div" class="creative-tool-list">
                      <div
                        v-for="tool in creativeToolsStore.instances"
                        :key="tool.id"
                        :data-tool-id="tool.id"
                        class="creative-tool-block"
                        :class="{ 'is-selected': tool.id === creativeToolsStore.selectedId }"
                        role="button"
                        tabindex="0"
                        @click="handleSelectTool(tool.id)"
                        @keydown.enter="handleSelectTool(tool.id)"
                        @keydown.space.prevent="handleSelectTool(tool.id)"
                      >
                        <span
                          class="creative-tool-block-mark"
                          :style="{ color: tool.color }"
                          aria-hidden="true"
                          >{{ toolMark(tool.type) }}</span
                        >
                        <span class="creative-tool-block-copy">
                          <span class="creative-tool-block-title">{{ tool.name }}</span>
                          <span class="creative-tool-block-meta">{{ tool.badge }}</span>
                        </span>
                        <button
                          type="button"
                          class="creative-tool-block-remove"
                          :aria-label="`移除${tool.name}`"
                          title="从当前工作流移除"
                          @click.stop="handleRemoveTool(tool.id)"
                        >
                          ×
                        </button>
                        <span class="creative-tool-block-arrow" aria-hidden="true">→</span>
                      </div>
                    </TransitionGroup>
                    <button class="add-tool-large" type="button" @click="isToolModalOpen = true">
                      添加创作工具 +
                    </button>
                  </div>
                </section>
              </div>
            </div>
          </aside>

          <section class="center-column">
            <div class="canvas-surface">
              <div class="canvas-area">
                <WorkflowCanvas
                  @open-picker="isToolModalOpen = true"
                  @node-selected="handleWorkflowNodeSelected"
                  @notify="handleWorkflowNotify"
                />
                <CreativeToolPanel @close="handleCreativePanelClose" />
              </div>
            </div>
          </section>

          <aside class="right-column" :class="{ 'is-collapsed': isAssistantCollapsed }">
            <AssistantPanel ref="assistantPanelRef" />
            <button
              v-if="!isAssistantCollapsed"
              type="button"
              class="assistant-collapse-button"
              aria-label="收起智能助手"
              title="收起智能助手"
              @click="isAssistantCollapsed = true"
            >
              →
            </button>
            <button
              v-if="isAssistantCollapsed"
              type="button"
              class="assistant-reveal-button"
              aria-label="展开智能助手"
              title="展开智能助手"
              @click="isAssistantCollapsed = false"
            >
              ←
            </button>
          </aside>
        </main>
      </div>
    </div>

    <CreativeToolPicker
      :open="isToolModalOpen"
      @close="isToolModalOpen = false"
      @added="handleToolAdded"
    />

    <SettingsPanel :open="isSettingsOpen" @close="isSettingsOpen = false" />

    <!-- UI2 第二批④：⌘K/Ctrl+K 命令面板 -->
    <CommandPalette
      :open="isPaletteOpen"
      :commands="paletteCommands"
      @close="isPaletteOpen = false"
      @select="handlePaletteSelect"
    />

    <CreateProjectDialog
      :open="isCreateProjectModalOpen"
      @close="isCreateProjectModalOpen = false"
      @created="handleProjectCreated"
    />

    <Transition name="modal-fade">
      <div
        v-if="isProjectWarningOpen"
        class="modal-overlay"
        :aria-hidden="!isProjectWarningOpen"
        @click.self="isProjectWarningOpen = false"
      >
        <div
          class="modal-card project-warning-modal"
          role="alertdialog"
          aria-modal="true"
          aria-labelledby="projectWarningTitle"
        >
          <div class="modal-head">
            <div>
              <div id="projectWarningTitle" class="modal-title">{{ projectWarningTitle }}</div>
            </div>
          </div>
          <div class="modal-body project-create-body">
            <div class="warning-callout">
              <div class="warning-icon" aria-hidden="true">!</div>
              <div class="warning-content">
                <div class="warning-title">{{ projectWarningTitle }}</div>
                <div class="warning-message">{{ projectWarningMessage }}</div>
              </div>
            </div>
            <div class="project-warning-actions">
              <button
                class="project-danger-button"
                type="button"
                @click="isProjectWarningOpen = false"
              >
                确定
              </button>
            </div>
          </div>
        </div>
      </div>
    </Transition>
  </div>
</template>

<script setup lang="ts">
// 工作站布局（D1 拆分完成后的形态）：只保留布局骨架与流程协调——
// 入口初始化（路由项目 id -> 服务端详情 -> store）、项目打开/新建/切换导航、
// 保存模式切换、登出。项目/会话状态在 Pinia（stores/project.ts、stores/chat.ts），
// 具体交互在 components/project/*、components/assistant/* 与 composables/*。
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from '@/utils/toast'
import { getProject, touchProjectOpened } from '@/serve/project'
import { useLoadingStore } from '@/stores/loading'
import { useUserStore } from '@/stores/user'
import { normalizeSaveMode, useProjectStore, type RecentProjectItem } from '@/stores/project'
import { useChatStore } from '@/stores/chat'
import { useCreativeToolsStore } from '@/stores/creativeTools'
import { useTaskStore } from '@/stores/tasks'
import { useWorkflowStore } from '@/stores/workflow'
import {
  saveModeLabels,
  saveModeOptions,
  useAutoSave,
  type SaveModeOption,
} from '@/composables/useAutoSave'
import {
  isDirectoryPickerSupported,
  readProjectDirectory,
  requestProjectDirectoryHandle,
  type BrowserDirectoryHandle,
} from '@/utils/localProject'
import ProjectSwitcher from '@/components/project/ProjectSwitcher.vue'
import CreateProjectDialog from '@/components/project/CreateProjectDialog.vue'
import CommandPalette from '@/components/palette/CommandPalette.vue'
import type { PaletteCommand } from '@/utils/palette'
import AssistantPanel from '@/components/assistant/AssistantPanel.vue'
import CreativeToolPicker from '@/components/creative/CreativeToolPicker.vue'
import CreativeToolPanel from '@/components/creative/CreativeToolPanel.vue'
import WorkflowCanvas from '@/components/workflow/WorkflowCanvas.vue'
import SettingsPanel from '@/components/settings/SettingsPanel.vue'
import { JOB_TERMINAL_EVENT } from '@/utils/jobTransitions'
import '@/views/workstation/styles/workstation-base.css'

// 真实进度接管：让工作站初始化阶段能推动 loadingStore.setProgress(),
// 覆盖层在 realProgress > 0 时无缝从内置 rAF 切到真实进度。
const loadingStore = useLoadingStore()
const route = useRoute()
const router = useRouter()
const projectStore = useProjectStore()
const chatStore = useChatStore()
const creativeToolsStore = useCreativeToolsStore()
const taskStore = useTaskStore()
const workflowStore = useWorkflowStore()
const autoSave = useAutoSave()

// emit:ready —— 核心资源初始化完成时通知外层淡出覆盖层。
// 该事件与"是否加载成功"解耦（成功/失败都会发出），由父级 Workstation.vue 负责真正收起。
const emit = defineEmits<{ (e: 'ready'): void }>()

const assistantPanelRef = ref<InstanceType<typeof AssistantPanel> | null>(null)
const saveModeWrapRef = ref<HTMLElement | null>(null)
const { id: projectId } = storeToRefs(projectStore)

const isToolModalOpen = ref(false)
const isSettingsOpen = ref(false)
const isAssistantCollapsed = ref(false)

// UI2 第二批④：命令面板（⌘K/Ctrl+K）。命令走注册表，新增功能 = 加一条命令项。
const isPaletteOpen = ref(false)

const paletteCommands = computed<PaletteCommand[]>(() => [
  {
    id: 'focus-composer',
    title: '聚焦智能助手输入框',
    keywords: ['focus', 'input', 'shuru', 'ruku'],
  },
  {
    id: 'toggle-assistant',
    title: isAssistantCollapsed.value ? '展开智能助手面板' : '收起智能助手面板',
    keywords: ['assistant', 'zhushou', 'toggle'],
  },
  {
    id: 'new-project',
    title: '新建项目',
    meta: '创建一个新的工程文件并进入新工作站',
    keywords: ['new', 'project', 'xinjian'],
  },
  {
    id: 'open-project',
    title: '选择项目打开',
    meta: '从本地工程列表继续创作',
    keywords: ['open', 'project', 'dakai'],
  },
  {
    id: 'billing',
    title: '打开计费与用量页',
    meta: '额度、用量与账户流水（独立页面）',
    keywords: ['billing', 'zhangdan', '账单', 'jifei', '计费'],
  },
  {
    id: 'settings',
    title: 'Settings 面板',
    keywords: ['settings', 'shezhi', '设置'],
  },
])

function handleGlobalKeydown(event: KeyboardEvent) {
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') {
    event.preventDefault()
    isPaletteOpen.value = !isPaletteOpen.value
  }
}

async function handlePaletteSelect(command: PaletteCommand) {
  switch (command.id) {
    case 'focus-composer':
      if (isAssistantCollapsed.value) {
        isAssistantCollapsed.value = false
        await nextTick()
      }
      document.querySelector<HTMLTextAreaElement>('.input-composer textarea')?.focus()
      break
    case 'toggle-assistant':
      isAssistantCollapsed.value = !isAssistantCollapsed.value
      break
    case 'new-project':
      openCreateProjectDialog()
      break
    case 'open-project':
      void handleOpenProjectPicker()
      break
    case 'billing':
      void router.push('/billing')
      break
    case 'settings':
      isSettingsOpen.value = true
      break
  }
}
const isSaveModeOpen = ref(false)
const isSaveModeApplying = ref(false)
const isCreateProjectModalOpen = ref(false)
const isProjectWarningOpen = ref(false)
const projectWarningTitle = ref('')
const projectWarningMessage = ref('')

const currentSaveModeLabel = computed(() => saveModeLabels[projectStore.saveMode])

/* ---------- 项目打开 / 新建 / 切换 ---------- */

function openProjectWarning(title: string, message: string) {
  projectWarningTitle.value = title
  projectWarningMessage.value = message
  isProjectWarningOpen.value = true
}

function getRouteProjectId() {
  if (typeof route.query.project !== 'string') return null
  if (route.query.project === 'default') return null

  const routeProjectId = Number(route.query.project)
  return Number.isInteger(routeProjectId) && routeProjectId > 0 ? routeProjectId : null
}

/**
 * 以路由为真源导航到目标项目：query 变化时由 Workstation.vue 的 :key 触发重挂载，
 * 新实例走 initializeWorkspaceFromEntryPoint；query 相同则原地重置会话面板。
 */
async function navigateToProject(projectId: number | null) {
  const currentProjectQuery = typeof route.query.project === 'string' ? route.query.project : null
  const nextProjectQuery = projectId != null ? String(projectId) : 'default'
  if (currentProjectQuery === nextProjectQuery) {
    chatStore.resetForProjectSwitch()
    void loadAgentPanel()
    return
  }

  await router.replace({
    path: '/workstation',
    query: { project: nextProjectQuery },
  })
}

/** 项目打开后的统一收口：写 store、同步定时器、重置会话、导航 */
function applyOpenedProject(
  project: { id?: number; name: string; projectPath: string; saveMode: string },
  directoryHandle: BrowserDirectoryHandle | null = null,
) {
  const saveMode = normalizeSaveMode(project.saveMode)
  projectStore.setActiveProject(
    {
      id: project.id ?? null,
      name: project.name,
      projectPath: project.projectPath,
      saveMode,
    },
    directoryHandle,
  )
  autoSave.syncTimer(saveMode)
  chatStore.resetForProjectSwitch()

  projectStore.upsertRecentProject({
    id: project.id ?? projectStore.nextLocalProjectId(),
    title: project.name,
    meta: `最近打开 · 刚刚 · ${project.projectPath}`,
    projectPath: project.projectPath,
    saveMode,
  })
  // B29：真实打开时刻上报——刷新 last_opened_at（修「面板按创建时间排」旧 bug），
  // 被放弃但保存过的工程在此复活。fire-and-forget：失败不影响打开体验。
  if (typeof project.id === 'number' && project.id > 0) {
    void touchProjectOpened(project.id).catch(() => undefined)
  }
  void navigateToProject(projectStore.id)
}

function openCreateProjectDialog() {
  isCreateProjectModalOpen.value = true
}

function handleProjectCreated(payload: {
  id: number | null
  name: string
  projectPath: string
  directoryHandle: BrowserDirectoryHandle
}) {
  isCreateProjectModalOpen.value = false
  applyOpenedProject(
    {
      id: payload.id ?? undefined,
      name: payload.name,
      projectPath: payload.projectPath,
      saveMode: 'manual',
    },
    payload.directoryHandle,
  )
}

async function handleOpenProjectPicker() {
  if (!isDirectoryPickerSupported()) {
    openProjectWarning('当前浏览器不支持', '请选择 Chromium 内核浏览器后再打开本地项目文件夹。')
    return
  }

  try {
    const directoryHandle = await requestProjectDirectoryHandle()
    const project = await readProjectDirectory(directoryHandle)

    // B29：磁盘捡回的工程先过服务端校验——被「不保存」放弃且从未保存过的 → 提示失效（用户拍板：没保存的就不要了）；
    // 保存过的（含被放弃）→ getProject 正常返回，面板复活由 applyOpenedProject 的 touch 完成
    if (typeof project.id === 'number' && project.id > 0) {
      try {
        await getProject(project.id)
      } catch {
        openProjectWarning(
          '项目已失效',
          '该项目从未保存过，已被放弃。磁盘文件保留作备份，但无法重新打开。',
        )
        return
      }
    }

    applyOpenedProject(project, directoryHandle)
    ElMessage.success(`已打开项目：${project.name}`)
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') return

    if (error instanceof Error) {
      if (error.message === 'USER_CANCELLED_DIRECTORY_PICKER') return
      if (error.message === 'INVALID_PROJECT_DIRECTORY') {
        openProjectWarning('项目文件不合法', '所选文件夹中未找到可用的 `project.json` 项目文件。')
        return
      }
      if (error.message === 'INVALID_PROJECT_FILE') {
        openProjectWarning(
          '项目文件不合法',
          '检测到 `project.json`，但内容格式不符合当前项目要求。',
        )
        return
      }
    }

    openProjectWarning('打开项目失败', '本地项目读取失败，请重新选择一个有效的项目文件夹。')
  }
}

function handleOpenRecentProject(item: RecentProjectItem) {
  applyOpenedProject({
    id: item.id > 0 ? item.id : undefined,
    name: item.title,
    projectPath: item.projectPath,
    saveMode: item.saveMode,
  })
  ElMessage.success(`已打开项目：${item.title}`)
}

/**
 * 入口初始化（B3 的修复结论）：URL 项目 id 是更高优先级的真源，
 * 先按 id 调服务端详情恢复元数据；只有路由不可用时才回退本地缓存。
 */
async function initializeWorkspaceFromEntryPoint() {
  const routeProjectId = getRouteProjectId()
  if (routeProjectId !== null) {
    try {
      const { project } = await getProject(routeProjectId)
      applyOpenedProject({
        id: project.id,
        name: project.name,
        projectPath: project.project_path,
        saveMode: project.save_mode,
      })
      return
    } catch {
      // 路由里的项目可能已被删除或无权访问，回退到本地缓存。
    }
  }

  if (projectStore.restoreFromStorage()) {
    autoSave.syncTimer(projectStore.saveMode)
  }
  void loadAgentPanel()
}

/* ---------- 会话面板加载（真实进度接管 + ready 通知） ---------- */

async function loadAgentPanel() {
  try {
    loadingStore.setProgress(35)
    loadingStore.setLabel('Loading conversation history')
    await assistantPanelRef.value?.loadHistory()
    loadingStore.setProgress(95)
    loadingStore.setLabel('Finalizing')
  } catch {
    chatStore.clearMessages()
    loadingStore.setProgress(95)
    loadingStore.setLabel('Finalizing')
  } finally {
    loadingStore.setProgress(100)
    loadingStore.setLabel('Entering')
    // 关键：核心资源加载完成（成功或失败都算"完成"）后通知外层淡出覆盖层。
    emit('ready')
  }
}

/* ---------- 保存模式 / 登出 ---------- */

async function handleSelectSaveMode(option: SaveModeOption) {
  if (isSaveModeApplying.value) return

  isSaveModeApplying.value = true
  try {
    const applied = await autoSave.selectSaveMode(option)
    if (applied) {
      isSaveModeOpen.value = false
    }
  } finally {
    isSaveModeApplying.value = false
  }
}

/* ---------- 创作工具 Block ---------- */

function toolMark(type: string) {
  return { lyrics: 'Aa', image: '✦', video: '▶', audio: '∿' }[type] ?? '✦'
}

function handleToolAdded(id: string) {
  const tool = creativeToolsStore.instances.find((item) => item.id === id)
  if (tool) workflowStore.addNode(tool)
}

function handleSelectTool(id: string) {
  creativeToolsStore.selectTool(id, true)
  workflowStore.selectNode(`workflow-node-${id}`)
}

function handleRemoveTool(id: string) {
  const tool = creativeToolsStore.instances.find((item) => item.id === id)
  if (!tool) return
  workflowStore.removeNodeByToolId(id)
  creativeToolsStore.removeTool(id)
  ElMessage.success(`已移除工具：${tool.name}，相关连接已断开`)
}

function handleWorkflowNodeSelected(toolId: string) {
  creativeToolsStore.selectTool(toolId, false)
  const toolElement = document.querySelector<HTMLElement>(`[data-tool-id="${toolId}"]`)
  toolElement?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
}

function handleCreativePanelClose() {
  creativeToolsStore.closePanel()
}

function handleWorkflowNotify(message: string) {
  ElMessage.success(message)
}

function handleLogout() {
  useUserStore().logout()
  ElMessage.success('已退出登录')
  router.push('/login')
}

function handleDocumentClick(event: MouseEvent) {
  const target = event.target as Node | null
  if (saveModeWrapRef.value && target && !saveModeWrapRef.value.contains(target)) {
    isSaveModeOpen.value = false
  }
}

function loadCreativeWorkspace(projectId: number | null) {
  creativeToolsStore.loadForProject(projectId)
  workflowStore.loadForProject(projectId)
  workflowStore.syncWithTools(creativeToolsStore.instances)
}

watch(projectId, loadCreativeWorkspace, { immediate: true })

// B28：后台任务（如助手提交的 workflow）落地时，把聊天里的 system-notice 及时刷进来。
// 助手正在流式输出时先不刷——stream done 事件会带完整历史，强行重载会清掉正在打的气泡。
function handleJobTerminal() {
  if (chatStore.isSending) return
  void assistantPanelRef.value?.loadHistory()
}

/** B29：防手滑——手动保存模式下关闭/刷新页面时，浏览器原生警示「当前操作不会被保存」。
 * 说明：会话与 Workflow 草稿在服务端持久化，此处保护的是本地工程元数据（名称等）
 * 与用户心理预期；自动保存模式下由定时 tick 兑现保存，不弹。 */
function handleBeforeUnload(event: BeforeUnloadEvent) {
  const projectOpen = projectStore.id !== null || !!projectStore.projectPath
  if (projectOpen && projectStore.saveMode === 'manual') {
    event.preventDefault()
    event.returnValue = ''
  }
}

onMounted(() => {
  taskStore.startPolling(projectId)
  document.addEventListener('click', handleDocumentClick)
  window.addEventListener('keydown', handleGlobalKeydown)
  window.addEventListener(JOB_TERMINAL_EVENT, handleJobTerminal)
  window.addEventListener('beforeunload', handleBeforeUnload)
  void initializeWorkspaceFromEntryPoint()
})

onBeforeUnmount(() => {
  taskStore.stopPolling()
  document.removeEventListener('click', handleDocumentClick)
  window.removeEventListener('keydown', handleGlobalKeydown)
  window.removeEventListener(JOB_TERMINAL_EVENT, handleJobTerminal)
  window.removeEventListener('beforeunload', handleBeforeUnload)
})
</script>

<style scoped>
.app-shell {
  width: max(100%, var(--app-min-width));
  min-width: var(--app-min-width);
  min-height: var(--app-min-height);
  height: max(100vh, var(--app-min-height));
  background: var(--body-bg);
}

.app-frame {
  width: 100%;
  min-width: var(--app-min-width);
  min-height: var(--app-min-height);
  height: 100%;
  background: var(--body-bg);
  overflow: hidden;
  display: grid;
  grid-template-rows: var(--topbar-h) 1fr;
}

.topbar {
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 16px;
  position: relative;
  min-width: var(--app-min-width);
  padding: 0 18px;
  border-bottom: 1px solid var(--line) !important;
  background: var(--column-bg);
}

.topbar-left,
.topbar-center,
.topbar-right,
.status-row,
.title-row {
  display: flex;
  align-items: center;
  min-width: 0;
}

.app-title {
  letter-spacing: -0.04em;
  font-weight: 650;
}

.topbar-left {
  gap: 0;
}

.title-row {
  gap: 10px;
  min-width: 0;
}

.topbar-center {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  justify-content: center;
  gap: 12px;
  min-width: 0;
  padding-right: 0;
  z-index: 2;
}

.topbar-right {
  justify-content: flex-end;
  gap: 12px;
  min-width: 0;
  padding-right: 0;
  margin-right: -6px;
}

.topbar-right .window-controls {
  display: none;
}

.status-row {
  gap: 8px;
  flex-wrap: wrap;
  position: relative;
}

.status-button-wrap {
  position: relative;
}

.status-button {
  height: 32px;
  display: inline-flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-width: 124px;
  padding: 0 12px;
  border: 1px solid rgba(255, 255, 255, 0.12) !important;
  border-radius: 0;
  background: rgba(255, 255, 255, 0.015);
  color: var(--text-secondary);
  font-size: 12px;
  cursor: pointer;
}

.status-button-label {
  display: inline-flex;
  align-items: center;
  min-width: 0;
}

.status-button:hover {
  background: rgba(255, 255, 255, 0.04);
  color: var(--text-primary);
}

.status-button:hover span {
  color: var(--text-primary);
}

.status-panel {
  position: absolute;
  top: calc(100% + 8px);
  right: 0;
  width: 220px;
  flex-direction: column;
  gap: 6px;
  padding: 8px;
  border: 1px solid rgba(40, 40, 40, 0.55);
  border-radius: 16px;
  background: rgba(20, 20, 20, 0.98);
  z-index: 30;
  display: flex;
}

.status-panel-option {
  width: 100%;
  text-align: left;
  padding: 8px 10px;
  border-radius: 16px;
  color: var(--text-secondary);
  background: transparent;
  cursor: pointer;
}

.save-mode-label-enter-active,
.save-mode-label-leave-active {
  transition:
    opacity 180ms cubic-bezier(0.16, 1, 0.3, 1),
    transform 180ms cubic-bezier(0.16, 1, 0.3, 1);
}

.save-mode-label-enter-from,
.save-mode-label-leave-to {
  opacity: 0;
  transform: translateY(3px);
}

.save-mode-label-enter-to,
.save-mode-label-leave-from {
  opacity: 1;
  transform: translateY(0);
}

.main-grid {
  min-height: 0;
  height: 100%;
  display: grid;
  min-width: var(--app-min-width);
  grid-template-columns: var(--left-column-w) minmax(716px, 1fr) var(--right-column-w);
  grid-template-areas: 'left center right';
  background: var(--body-bg);
  overflow: hidden;
}

.main-grid.assistant-collapsed {
  grid-template-columns: var(--left-column-w) minmax(716px, 1fr) 44px;
}

.left-column {
  grid-area: left;
  min-width: 0;
  min-height: 0;
  display: grid;
  grid-template-columns: 42px 1fr;
  border-right: 1px solid var(--line) !important;
  background: var(--column-bg);
}

.icon-rail {
  padding: 8px 4px;
  border-right: 1px solid var(--line) !important;
  display: flex;
  flex-direction: column;
  gap: 6px;
  background: var(--column-bg);
}

.rail-spacer {
  flex: 1;
}

.icon-button {
  width: 32px;
  height: 32px;
  border-radius: 4px;
  color: var(--text-sidebar);
  display: grid;
  place-items: center;
  cursor: pointer;
}

.icon-button:hover,
.icon-button.active {
  background: var(--hover-bg);
  color: #ffffff;
}

.sidebar-stack {
  min-width: 0;
  display: grid;
  grid-template-rows: 1fr;
  gap: 0;
  padding: 6px;
  background: var(--column-bg);
}

.sidebar-workspace {
  min-height: 100%;
  border: 1px solid rgba(40, 40, 40, 0.55);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.015);
  padding: 4px;
  display: flex;
  flex-direction: column;
}

.section-card {
  border-radius: 0;
  background: transparent;
  overflow: hidden;
}

.section-body {
  padding: 0;
}

.add-tool-large {
  width: 100%;
  min-height: 76px;
  border: 1px solid rgba(40, 40, 40, 0.6) !important;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.02);
  color: var(--text-secondary);
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  cursor: pointer;
}

.add-tool-large:hover {
  background: rgba(255, 255, 255, 0.06) !important;
  color: var(--text-primary) !important;
  filter: none !important;
}

.creative-tool-section {
  min-height: 0;
  flex: 1;
  overflow: hidden;
}

.creative-tool-section .section-body {
  display: flex;
  min-height: 0;
  flex-direction: column;
  gap: 8px;
}

.creative-tool-list {
  display: flex;
  min-height: 0;
  flex: 1;
  flex-direction: column;
  gap: 8px;
  overflow: auto;
}

.creative-tool-block {
  display: flex;
  width: 100%;
  min-height: 76px;
  flex: 0 0 76px;
  align-items: center;
  gap: 10px;
  padding: 0 13px;
  border: 1px solid rgba(40, 40, 40, 0.6) !important;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.02) !important;
  color: var(--text-secondary) !important;
  text-align: left;
  cursor: pointer;
  user-select: none;
}

.creative-tool-block:hover,
.creative-tool-block.is-selected {
  background: rgba(255, 255, 255, 0.06) !important;
  color: var(--text-primary) !important;
}

.creative-tool-block.is-selected {
  border-color: rgba(210, 210, 210, 0.48) !important;
}

.creative-tool-block-mark {
  display: grid;
  width: 29px;
  height: 29px;
  flex: 0 0 auto;
  place-items: center;
  border-radius: 7px;
  filter: grayscale(1);
  font-size: 15px;
}

.creative-tool-block-copy {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
  gap: 2px;
}

.creative-tool-block-title {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.creative-tool-block-meta {
  color: var(--text-secondary);
  font-size: 10px;
}

.creative-tool-block-arrow {
  flex: 0 0 auto;
  color: #777;
  font-size: 14px;
  transition:
    transform 240ms cubic-bezier(0.22, 1, 0.36, 1),
    color 180ms ease;
}

.creative-tool-block:hover .creative-tool-block-arrow,
.creative-tool-block.is-selected .creative-tool-block-arrow {
  transform: translateX(3px);
  color: #e6e6e6;
}

.creative-tool-block-remove {
  display: grid;
  width: 22px;
  height: 22px;
  flex: 0 0 auto;
  place-items: center;
  border-radius: 5px;
  color: #727680 !important;
  font-size: 16px;
  opacity: 0;
  transition:
    opacity 160ms ease,
    background-color 160ms ease,
    color 160ms ease;
}

.creative-tool-block:hover .creative-tool-block-remove,
.creative-tool-block:focus-within .creative-tool-block-remove,
.creative-tool-block.is-selected .creative-tool-block-remove {
  opacity: 1;
}

.creative-tool-block-remove:hover {
  background: rgba(255, 255, 255, 0.1) !important;
  color: #fff !important;
}

.creative-tool-list-enter-active,
.creative-tool-list-leave-active,
.creative-tool-list-move {
  transition:
    transform 300ms cubic-bezier(0.16, 1, 0.3, 1),
    opacity 240ms ease;
}

.creative-tool-list-enter-from,
.creative-tool-list-leave-to {
  opacity: 0;
  transform: translateY(-10px);
}

.creative-tool-list-leave-active {
  position: absolute;
  width: 100%;
}

.center-column {
  grid-area: center;
  min-width: 0;
  min-height: 0;
  position: relative;
  background: var(--body-bg);
}

.canvas-surface {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  padding: 0;
  gap: 0;
}

.canvas-area {
  position: relative;
  flex: 1;
  min-height: 0;
  display: flex;
  overflow: hidden;
}

.canvas-center {
  width: 100%;
  min-height: 100%;
  border: 1px solid rgba(40, 40, 40, 0.55);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.015);
  justify-content: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 18px;
  color: var(--text-secondary);
}

.canvas-callout {
  padding: 8px 12px;
  border-radius: 4px;
  background: transparent;
  color: var(--text-secondary);
  font-size: 16px;
  font-weight: 400;
}

.right-column {
  grid-area: right;
  min-width: 0;
  min-height: 0;
  padding: 0;
  border-left: 1px solid var(--line) !important;
  background:
    radial-gradient(circle at top, rgba(255, 255, 255, 0.04), transparent 34%),
    linear-gradient(180deg, rgba(255, 255, 255, 0.02), rgba(255, 255, 255, 0)), var(--column-bg);
  position: relative;
  overflow: hidden;
}

.right-column > .assistant-panel {
  transition:
    transform 340ms cubic-bezier(0.16, 1, 0.3, 1),
    opacity 260ms ease;
}

.assistant-collapse-button {
  position: absolute;
  top: 13px;
  right: 12px;
  z-index: 6;
  display: grid;
  width: 28px;
  height: 28px;
  place-items: center;
  border: 1px solid rgba(255, 255, 255, 0.12) !important;
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.035) !important;
  color: #9297a1 !important;
  font-size: 16px;
}

.assistant-collapse-button:hover {
  background: rgba(255, 255, 255, 0.1) !important;
  color: #fff !important;
}

.right-column.is-collapsed {
  overflow: visible;
  background: var(--column-bg);
}

.right-column.is-collapsed > .assistant-panel {
  opacity: 0;
  pointer-events: none;
  transform: translateX(calc(100% + 20px));
}

.assistant-reveal-button {
  position: absolute;
  top: 50%;
  right: 5px;
  display: grid;
  width: 32px;
  height: 72px;
  place-items: center;
  transform: translateY(-50%);
  border: 1px solid rgba(96, 165, 250, 0.45) !important;
  border-radius: 8px 0 0 8px;
  background: rgba(255, 255, 255, 0.07) !important;
  color: #d3d3d3 !important;
  font-size: 16px;
}

.assistant-reveal-button:hover {
  background: rgba(255, 255, 255, 0.16) !important;
  border-color: rgba(255, 255, 255, 0.32) !important;
}

@media (max-width: 1180px) {
  .app-shell,
  .app-frame,
  .topbar,
  .main-grid {
    min-width: var(--app-min-width);
  }
}
</style>
