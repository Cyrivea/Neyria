/**
 * 会话状态唯一真源（D2，todo §8.3）。
 * Store 管理 messages / sending / tool state / 附件 / abort controller；
 * 发送与 SSE 消费流程在 composables/useAgentChat.ts；组件只负责展示和用户操作。
 */
import { computed, ref, shallowRef } from 'vue'
import { defineStore } from 'pinia'
import type { AgentCitation, AgentHistoryItem } from '@/serve/agent'
import {
  buildUploadedAttachment,
  revokeAttachmentPreview,
  type UploadedAttachment,
} from '@/utils/attachment'

export interface AgentMessage {
  id: string
  /** 数据库行 id：评分/编辑重发等消息级操作的定位键（UI2），流式中的本地种子还没有 */
  dbId?: number
  role: 'user' | 'assistant' | 'system-notice'
  content: string
  toolName?: string | null
  isError?: boolean
  isPending?: boolean
  attachments?: UploadedAttachment[]
  citations?: AgentCitation[]
  interrupted?: boolean
  /** 用户评分（UI2-⑤）：undefined=未评 */
  feedback?: 1 | -1
}

export function formatToolName(toolName: string) {
  const toolMap: Record<string, string> = {
    calculate: '计算工具',
    get_current_time: '时间工具',
    search_web: '联网搜索',
    generate_lyrics_block: '歌词生成',
    list_workflow_templates: '查看模板库',
    add_workflow_chains: '搭建创作链',
    update_chain_params: '调整链参数',
    remove_workflow_chain: '删除创作链',
    run_current_workflow: '运行工作流',
  }

  return toolMap[toolName] || toolName
}

export const useChatStore = defineStore('chat', () => {
  const messages = ref<AgentMessage[]>([])
  const uploadedAttachments = ref<UploadedAttachment[]>([])
  const draft = ref('')
  const isSending = ref(false)
  const isUploadingAttachment = ref(false)
  const activeToolName = ref('')
  const hasAgentStartedReplying = ref(false)
  const thinkingSeconds = ref(0)
  const selectedModel = ref('glm-4-flash')
  // 流控制句柄与自增 id 收进 store，消灭模块级 let（重挂载后不再丢状态）
  const abortController = shallowRef<AbortController | null>(null)
  let messageIdSeed = 0
  // 加载令牌：切项目时丢弃迟到的历史响应
  let loadToken = 0
  // UI2-⑥：生成中发送的消息进队列，本轮结束后自动发下一条。文本-only——
  // 附件与“流中可传附件”是两回事，附件输入在流式期间被禁，队列只需带草稿文字。
  const messageQueue = ref<{ id: string; message: string }[]>([])
  // UI2-③：本轮回复完成后服务端补发的追问建议，新一轮发送/切项目时清空
  const followUps = ref<string[]>([])

  const hasUploadedAttachments = computed(() => uploadedAttachments.value.length > 0)

  const thinkingStatusText = computed(() => {
    if (!isSending.value) return ''
    if (activeToolName.value) return `正在调用 ${formatToolName(activeToolName.value)}`
    if (hasAgentStartedReplying.value) return `正在输出回复 · 已思考 ${thinkingSeconds.value}s`
    return `正在思考 · 已思考 ${thinkingSeconds.value}s`
  })

  function nextMessageId() {
    return `agent-${messageIdSeed++}`
  }

  function nextLoadToken() {
    return ++loadToken
  }

  function isCurrentLoadToken(token: number) {
    return token === loadToken
  }

  /** 替换消息列表前统一释放消息里的 blob: 预览（solved.md 第 19 条） */
  function clearMessages() {
    for (const message of messages.value) {
      message.attachments?.forEach(revokeAttachmentPreview)
    }
    messages.value = []
  }

  function setMessagesFromHistory(
    history: AgentHistoryItem[],
    hydrate: (item: AgentHistoryItem) => AgentMessage,
  ) {
    clearMessages()
    messages.value = history.map(hydrate)
  }

  /**
   * 重新生成前的本地尾轮截断（UI1）：保留最后一条 user 消息（它就是本次要重答的问题），
   * 截掉其后的所有 assistant 消息。镜像后端 chat_repo.pop_last_turn 的同态规则：
   * 尾轮内一旦出现 system-notice 等第三方角色，拒绝截断（由后端 409 兑底、前端提前拦截）。
   */
  function truncateTailForRegenerate(): boolean {
    let lastUserIndex = -1
    for (let i = messages.value.length - 1; i >= 0; i--) {
      if (messages.value[i].role === 'user') {
        lastUserIndex = i
        break
      }
    }
    if (lastUserIndex < 0) return false

    const tail = messages.value.slice(lastUserIndex + 1)
    if (!tail.length || tail.some((message) => message.role !== 'assistant')) return false

    tail.forEach((message) => message.attachments?.forEach(revokeAttachmentPreview))
    messages.value = messages.value.slice(0, lastUserIndex + 1)
    return true
  }

  /**
   * 编辑重发前的本地截断（UI2-②）：以指定 dbId 的 user 消息为界，
   * 它本身及其后所有消息全部移除（镜像后端 truncate_from_message）。
   * 与尾轮不同这里没有角色检查——编辑是显式删后续对话的用户自愿行为，
   * 确认框已在 UI 层明示丢失数量。
   */
  function truncateForEdit(dbId: number): boolean {
    const index = messages.value.findIndex((m) => m.dbId === dbId && m.role === 'user')
    if (index < 0) return false
    messages.value.slice(index).forEach((m) => m.attachments?.forEach(revokeAttachmentPreview))
    messages.value = messages.value.slice(0, index)
    return true
  }

  function enqueueMessage(message: string) {
    messageQueue.value = [...messageQueue.value, { id: nextMessageId(), message }]
  }

  function dequeueMessage() {
    const [head, ...rest] = messageQueue.value
    messageQueue.value = rest
    return head
  }

  function removeQueuedMessage(id: string) {
    messageQueue.value = messageQueue.value.filter((item) => item.id !== id)
  }

  function clearUploadedAttachments() {
    uploadedAttachments.value.forEach(revokeAttachmentPreview)
    uploadedAttachments.value = []
  }

  function upsertUploadedAttachment(file: File) {
    const nextAttachment = buildUploadedAttachment(file)
    const existingIndex = uploadedAttachments.value.findIndex(
      (item) => item.name === nextAttachment.name,
    )

    if (existingIndex >= 0) {
      revokeAttachmentPreview(uploadedAttachments.value[existingIndex])
      uploadedAttachments.value.splice(existingIndex, 1, nextAttachment)
      return
    }

    uploadedAttachments.value = [nextAttachment, ...uploadedAttachments.value]
  }

  function removeUploadedAttachment(attachmentId: string) {
    const nextAttachments: UploadedAttachment[] = []
    for (const attachment of uploadedAttachments.value) {
      if (attachment.id === attachmentId) {
        revokeAttachmentPreview(attachment)
        continue
      }
      nextAttachments.push(attachment)
    }
    uploadedAttachments.value = nextAttachments
  }

  function abortActiveStream(reason?: string) {
    abortController.value?.abort(reason)
    abortController.value = null
  }

  /** 切换项目 / 重置面板：取消旧流、清空输入与消息（含 blob 回收） */
  function resetForProjectSwitch() {
    nextLoadToken()
    abortActiveStream('PROJECT_SWITCH')
    isSending.value = false
    isUploadingAttachment.value = false
    hasAgentStartedReplying.value = false
    activeToolName.value = ''
    thinkingSeconds.value = 0
    draft.value = ''
    messageQueue.value = []
    followUps.value = []
    clearUploadedAttachments()
    clearMessages()
  }

  return {
    messages,
    uploadedAttachments,
    draft,
    isSending,
    isUploadingAttachment,
    activeToolName,
    hasAgentStartedReplying,
    thinkingSeconds,
    selectedModel,
    abortController,
    hasUploadedAttachments,
    thinkingStatusText,
    nextMessageId,
    nextLoadToken,
    isCurrentLoadToken,
    clearMessages,
    setMessagesFromHistory,
    messageQueue,
    followUps,
    truncateTailForRegenerate,
    truncateForEdit,
    enqueueMessage,
    dequeueMessage,
    removeQueuedMessage,
    clearUploadedAttachments,
    upsertUploadedAttachment,
    removeUploadedAttachment,
    abortActiveStream,
    resetForProjectSwitch,
  }
})
