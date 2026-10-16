/**
 * 对话发送与 SSE 消费流程（D1 拆分第 4 步，todo §8.4/§8.3）。
 * 状态在 stores/chat.ts；本 composable 负责：附件上传 → 消息种子 →
 * 流式消费（打字机上屏）→ 首 token 超时 → 错误归类，并在宿主组件卸载时清理计时器。
 */
import { onBeforeUnmount } from 'vue'
import { ElMessage } from '@/utils/toast'
import {
  getAgentHistory,
  sendMessageFeedback,
  streamAgentChat,
  uploadAgentDocument,
  waitForAgentJob,
  type AgentAttachmentItem,
  type AgentHistoryItem,
} from '@/serve/agent'
import { createTypewriter } from '@/utils/typewriter'
import {
  cloneAttachmentForMessage,
  filterAcceptableFiles,
  hydrateAttachmentFromPayload,
  renamePastedFile,
} from '@/utils/attachment'
import { useChatStore, type AgentMessage } from '@/stores/chat'
import { useProjectStore } from '@/stores/project'
import { useTaskStore } from '@/stores/tasks'
import { useWorkflowStore } from '@/stores/workflow'
import type { WorkflowDraft } from '@/serve/workflow'

// 首 token 看门狗（C27）：开工心跳到达后即提前退役；这里是心跳或上游都失联的兑底。
// 须长于「后端建流预算」：流式单次尝试 60s + 重试预算已由心跳/空闲看门狗接管，
// 该常量只是双保险，不再担当常见发现限速误杀的元凶（原来是 10s，上了游偶发
// 限速时 SDK 重试还没跑完就被掐）。
const AGENT_FIRST_TOKEN_TIMEOUT_MS = 30000
// 防卡死看门狗：首个事件到达后，任何事件（content/tool/snapshot/done）
// 都会重置 60s 计时；超时就主动中断并给用户一句明确的错误结果。
const AGENT_STREAM_IDLE_TIMEOUT_MS = 60000

interface UseAgentChatOptions {
  /** 消息有更新时由宿主组件滚动到底部 */
  scrollToBottom: () => void | Promise<void>
}

export function useAgentChat(options: UseAgentChatOptions) {
  const chatStore = useChatStore()
  const projectStore = useProjectStore()
  const taskStore = useTaskStore()
  const workflowStore = useWorkflowStore()

  let thinkingTimer: number | null = null
  let firstTokenTimeout: number | null = null
  let streamIdleTimeout: number | null = null

  function startThinking() {
    stopThinking()
    chatStore.hasAgentStartedReplying = false
    chatStore.thinkingSeconds = 0
    thinkingTimer = window.setInterval(() => {
      chatStore.thinkingSeconds += 1
    }, 1000)
  }

  function stopThinking() {
    chatStore.hasAgentStartedReplying = false
    chatStore.thinkingSeconds = 0
    if (thinkingTimer !== null) {
      window.clearInterval(thinkingTimer)
      thinkingTimer = null
    }
    clearFirstTokenTimeout()
  }

  function clearFirstTokenTimeout() {
    if (firstTokenTimeout !== null) {
      window.clearTimeout(firstTokenTimeout)
      firstTokenTimeout = null
    }
  }

  function clearStreamIdleTimeout() {
    if (streamIdleTimeout !== null) {
      window.clearTimeout(streamIdleTimeout)
      streamIdleTimeout = null
    }
  }

  /** 每个 SSE 事件都要叫一次；60s 内没有新事件视为上游卡死，主动中断。 */
  function armStreamIdleTimeout(controller: AbortController) {
    clearStreamIdleTimeout()
    streamIdleTimeout = window.setTimeout(() => {
      controller.abort('STREAM_IDLE_TIMEOUT')
    }, AGENT_STREAM_IDLE_TIMEOUT_MS)
  }

  function hydrateHistoryItem(item: AgentHistoryItem): AgentMessage {
    return {
      id: chatStore.nextMessageId(),
      // mock/旧事件里可能没有数值 id（null/undefined 都归一为 undefined）
      dbId: typeof item.id === 'number' ? item.id : undefined,
      role: item.role,
      content: item.content,
      attachments: item.attachments?.map(hydrateAttachmentFromPayload),
      citations: item.citations,
      interrupted: item.interrupted,
      feedback: item.feedback === 1 || item.feedback === -1 ? item.feedback : undefined,
    }
  }

  /** 按当前项目加载服务端历史；令牌与项目双重校验，忽略迟到响应 */
  async function loadHistory() {
    const token = chatStore.nextLoadToken()
    const targetProjectId = projectStore.id
    const history = await getAgentHistory(targetProjectId)
    if (!chatStore.isCurrentLoadToken(token) || targetProjectId !== projectStore.id) {
      return false
    }
    chatStore.setMessagesFromHistory(history, hydrateHistoryItem)
    void options.scrollToBottom()
    return true
  }

  interface SendOptions {
    /** 重新生成等非输入框来源的文本 */
    overrideMessage?: string
    /** 重生成模式：后端截断数据库尾轮，前端镜像截断，不重复插用户气泡 */
    regenerate?: boolean
    /** 编辑重发（UI2-②）：与 regenerate 互斥；本地截断已在 applyEditedMessage 做完 */
    editFromId?: number
  }

  async function sendMessage(sendOptions?: SendOptions) {
    const regenerate = sendOptions?.regenerate === true
    const editFromId = sendOptions?.editFromId
    const message = (sendOptions?.overrideMessage ?? chatStore.draft).trim()
    const pendingAttachments = regenerate || editFromId ? [] : [...chatStore.uploadedAttachments]
    if (!message && !pendingAttachments.length) {
      return
    }
    if (chatStore.isSending || chatStore.isUploadingAttachment) {
      // UI2-⑥：生成中发送进队列，本轮结束后自动发。带附件的消息暂不支持排队
      //（附件需随发送时上传到当轮工程，流式期间上传窗口是关闭的）
      if (!regenerate && !editFromId && message && !pendingAttachments.length) {
        chatStore.enqueueMessage(message)
        chatStore.draft = ''
        chatStore.followUps = []
      } else if (pendingAttachments.length) {
        // 守卫原本是静默 return——粘贴/拖拽让加附件变容易了，人不知道发不出去就高位不安
        ElMessage.info('当前回复生成中，带附件的消息请稍等本轮结束后再发')
      }
      return
    }
    // 新一轮提问生效，上一轮的追问建议使命结束
    chatStore.followUps = []

    const attachmentPayloads: AgentAttachmentItem[] = []
    if (pendingAttachments.length) {
      if (projectStore.id == null) {
        ElMessage.warning('请先进入一个工程，再发送带附件的消息')
        return
      }

      chatStore.isUploadingAttachment = true
      try {
        for (const attachment of pendingAttachments) {
          if (!attachment.file) continue
          const result = await uploadAgentDocument(attachment.file, projectStore.id)
          if (result.status !== 'accepted' && result.status !== 'success') {
            throw new Error(result.message || `附件上传失败：${attachment.name}`)
          }
          if (result.job) {
            await waitForAgentJob(result.job.id)
          }
          attachmentPayloads.push({
            name: attachment.name,
            kind: attachment.kind,
            badge: attachment.badge,
            meta: attachment.meta,
          })
        }
        void taskStore.load(projectStore.id)
      } catch (error) {
        const errorMessage = error instanceof Error ? error.message : '附件发送失败'
        ElMessage.error(errorMessage)
        chatStore.isUploadingAttachment = false
        return
      }
      chatStore.isUploadingAttachment = false
    }

    const assistantMessageSeed: AgentMessage = {
      id: chatStore.nextMessageId(),
      role: 'assistant',
      content: '',
      toolName: null,
      isPending: true,
    }

    if (regenerate) {
      // 镜像后端 pop_last_turn：打掉尾轮旧答案，保留用户原问气泡
      if (!chatStore.truncateTailForRegenerate()) {
        ElMessage.warning('最新一轮对话无法重新生成（之后有其他事件），请直接发送新消息')
        return
      }
      chatStore.messages = [...chatStore.messages, assistantMessageSeed]
    } else {
      const userMessage: AgentMessage = {
        id: chatStore.nextMessageId(),
        role: 'user',
        content: message || '已发送附件',
        attachments: pendingAttachments.map(cloneAttachmentForMessage),
      }
      chatStore.messages = [...chatStore.messages, userMessage, assistantMessageSeed]
    }
    // 流式回调必须改数组里的响应式代理对象；直接改 seed 原始对象绕过 Vue 响应式（B1 教训）
    const assistantMessage = chatStore.messages[chatStore.messages.length - 1]
    // token 是一簇一簇到达的，经打字机缓冲后按帧匀速上屏，消除跳字感
    const typewriter = createTypewriter((text) => {
      if (assistantMessage.isPending) {
        assistantMessage.isPending = false
      }
      assistantMessage.content += text
      void options.scrollToBottom()
    })

    if (!regenerate) {
      // 重生成不归用户重发：不清草稿、不动待发送附件
      chatStore.draft = ''
      chatStore.clearUploadedAttachments()
    }
    chatStore.isSending = true
    chatStore.activeToolName = ''
    startThinking()

    const controller = new AbortController()
    chatStore.abortController = controller
    firstTokenTimeout = window.setTimeout(() => {
      if (!chatStore.hasAgentStartedReplying) {
        controller.abort('FIRST_TOKEN_TIMEOUT')
      }
    }, AGENT_FIRST_TOKEN_TIMEOUT_MS)
    armStreamIdleTimeout(controller)
    await options.scrollToBottom()

    try {
      await streamAgentChat(
        {
          message,
          project_id: projectStore.id,
          attachments: attachmentPayloads,
          ...(regenerate ? { regenerate: true } : {}),
          ...(editFromId ? { edit_from_id: editFromId } : {}),
        },
        {
          onTool(event) {
            clearFirstTokenTimeout()
            armStreamIdleTimeout(controller)
            chatStore.activeToolName = event.tool_name
            assistantMessage.toolName = event.tool_name
            void options.scrollToBottom()
          },
          onContent(event) {
            clearFirstTokenTimeout()
            armStreamIdleTimeout(controller)
            if (!chatStore.hasAgentStartedReplying) {
              chatStore.hasAgentStartedReplying = true
            }
            typewriter.push(event.content)
          },
          // C27：开工心跳到 -> 首 token 看门狗提前退役（后端活着且上报路径已通）；
          // 故意不重置 60s 空闲计时——心跳不是内容，职业防卡死仍由空闲看门狗负责。
          onHeartbeat() {
            clearFirstTokenTimeout()
          },
          onWorkflowSnapshot(event) {
            armStreamIdleTimeout(controller)
            // AI 通过后端的 Workflow 命令改动画布：把 Draft 快照交给 workflow store，
            // 同时桥接成左侧 Block 实例，参数/结果/状态与画布节点同步。
            workflowStore.applyWorkflowSnapshot(event.snapshot as WorkflowDraft)
          },
          async onDone(event) {
            clearStreamIdleTimeout()
            // 等缓冲吐完再用服务端历史整体替换消息列表，避免文字瞬间跳到全量
            await typewriter.finish()
            chatStore.activeToolName = ''
            chatStore.setMessagesFromHistory(event.history, hydrateHistoryItem)
            void options.scrollToBottom()
          },
          onSuggestions(event) {
            // UI2-③：追问建议在 done 后到达，只在流未被中止时采纳
            if (controller.signal.aborted) return
            chatStore.followUps = event.items
            void options.scrollToBottom()
          },
        },
        controller.signal,
      )
    } catch (error) {
      typewriter.cancel()

      // 切换项目导致的取消：直接静默返回，消息列表随新项目历史重建（§8.3）
      if (controller.signal.aborted && controller.signal.reason === 'PROJECT_SWITCH') {
        return
      }

      // UI2-①：用户主动停止。已上屏的部分保留 + interrupted 标记；
      // 后端在断连兜底里会把半截回复落库（CA 有档可查），前端不打红色错误。
      if (controller.signal.aborted && controller.signal.reason === 'USER_STOP') {
        assistantMessage.isPending = false
        assistantMessage.interrupted = true
        assistantMessage.toolName = null
        if (!assistantMessage.content) {
          assistantMessage.content = '（已停止生成，本轮没有留下内容）'
        }
        clearStreamIdleTimeout()
        await options.scrollToBottom()
        return
      }

      const isFirstTokenTimeout =
        controller.signal.aborted && controller.signal.reason === 'FIRST_TOKEN_TIMEOUT'
      const isStreamIdleTimeout =
        controller.signal.aborted && controller.signal.reason === 'STREAM_IDLE_TIMEOUT'
      assistantMessage.isPending = false
      assistantMessage.isError = true
      assistantMessage.toolName = null
      assistantMessage.content = isFirstTokenTimeout
        ? `${AGENT_FIRST_TOKEN_TIMEOUT_MS / 1000} 秒内未收到模型返回，已自动暂停本次响应。请检查后端日志、接口耗时或重试。`
        : isStreamIdleTimeout
          ? '超过 60 秒没有新的响应，已自动中断（可能是上游模型或工具卡住）。请重试。'
          : error instanceof Error
            ? error.message
            : 'Neyria 响应失败'
      ElMessage.error(
        isFirstTokenTimeout
          ? `${AGENT_FIRST_TOKEN_TIMEOUT_MS / 1000} 秒内未收到模型返回，已自动暂停`
          : isStreamIdleTimeout
            ? '响应超时，已自动中断'
            : assistantMessage.content,
      )
      clearStreamIdleTimeout()
      await options.scrollToBottom()
    } finally {
      clearStreamIdleTimeout()
      chatStore.isSending = false
      chatStore.isUploadingAttachment = false
      chatStore.activeToolName = ''
      stopThinking()
      if (chatStore.abortController === controller) {
        chatStore.abortController = null
      }
      // UI2-⑥：本轮收尾后自动发队列里的下一条（含主动停止的情况——停止停的是
      // 当前回复，不是拦着排队消息不许发）；切项目时队列已随 reset 清空。
      const queued = chatStore.dequeueMessage()
      if (queued) {
        void sendMessage({ overrideMessage: queued.message })
      }
    }
  }

  onBeforeUnmount(() => {
    stopThinking()
  })

  /** UI2-①：主动停止当前一轮生成（后端断连兜底会把半截回复落库带标） */
  function stopGenerating() {
    chatStore.abortActiveStream('USER_STOP')
  }

  /** UI2-②：编辑一条历史 user 消息并重发。本地先镜像截断，后端按 id 截库双保险。 */
  async function applyEditedMessage(original: AgentMessage, newText: string) {
    const text = newText.trim()
    if (!text || chatStore.isSending || chatStore.isUploadingAttachment) return
    if (original.dbId == null) {
      ElMessage.warning('这条消息还没入库，暂时不能编辑')
      return
    }
    if (original.content.trim() === text) {
      ElMessage.info('内容没有变化')
      return
    }
    if (!chatStore.truncateForEdit(original.dbId)) {
      ElMessage.warning('这条消息已变化，请刷新后重试')
      return
    }
    await sendMessage({ overrideMessage: text, editFromId: original.dbId })
  }

  /** UI2-⑤：评分三态——点已有评分=取消，点另一个=切换 */
  async function submitFeedback(message: AgentMessage, value: 1 | -1) {
    if (message.dbId == null) {
      ElMessage.warning('这条回复还没入库，稍后再试')
      return
    }
    const next = message.feedback === value ? 0 : value
    try {
      await sendMessageFeedback(message.dbId, next)
      message.feedback = next === 0 ? undefined : next
    } catch {
      ElMessage.error('评分失败，请稍后重试')
    }
  }

  /** 重新生成最新一轮回复（open-webui 消息操作栏设计）：取最后一条用户消息原文重发 */
  async function regenerateLastMessage() {
    if (chatStore.isSending || chatStore.isUploadingAttachment) return
    let lastUserContent = ''
    for (let i = chatStore.messages.length - 1; i >= 0; i--) {
      const item = chatStore.messages[i]
      if (item.role === 'user') {
        lastUserContent = item.content
        break
      }
    }
    if (!lastUserContent) {
      ElMessage.info('还没有可以重新生成的回复')
      return
    }
    await sendMessage({ overrideMessage: lastUserContent, regenerate: true })
  }

  /** UI2 第二批①②：三个入附口（选块器/粘贴/拖拽）共用一道门 —— 项目检查、白名单过滤、重名体检 */
  function addAttachmentFiles(files: File[]) {
    if (!files.length) return
    if (projectStore.id == null) {
      ElMessage.warning('请先新建或打开一个工程，再添加附件')
      return
    }
    const { accepted, rejected } = filterAcceptableFiles(files)
    accepted.forEach((raw) => chatStore.upsertUploadedAttachment(renamePastedFile(raw)))
    if (rejected.length) {
      ElMessage.warning(`${rejected.length} 个文件的格式不在支持列表里，已跳过`)
    }
    if (accepted.length) {
      ElMessage.success(`已添加 ${accepted.length} 个待发送附件`)
    }
  }

  return {
    sendMessage,
    loadHistory,
    regenerateLastMessage,
    stopGenerating,
    applyEditedMessage,
    submitFeedback,
    addAttachmentFiles,
  }
}
