// chat store 尾轮截断测试（UI1 重生成模式）：镜像后端 pop_last_turn 的同态规则
import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useChatStore, type AgentMessage } from '@/stores/chat'

function makeMessage(role: AgentMessage['role'], content: string): AgentMessage {
  return { id: `m-${Math.random()}`, role, content }
}

describe('useChatStore.truncateTailForRegenerate', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('常规尾轮：截掉最后一条 user 之后的 assistant，保留原问', () => {
    const store = useChatStore()
    store.messages = [
      makeMessage('user', '第一问'),
      makeMessage('assistant', '第一答'),
      makeMessage('user', '第二问'),
      makeMessage('assistant', '旧答案'),
    ]
    expect(store.truncateTailForRegenerate()).toBe(true)
    expect(store.messages.map((m) => m.content)).toEqual(['第一问', '第一答', '第二问'])
  })

  it('尾巴是连续多条 assistant（错误重试残留）也能截净', () => {
    const store = useChatStore()
    store.messages = [
      makeMessage('user', '问'),
      makeMessage('assistant', '答'),
      makeMessage('assistant', '错误重试'),
    ]
    expect(store.truncateTailForRegenerate()).toBe(true)
    expect(store.messages).toHaveLength(1)
  })

  it('尾轮混有 system-notice 时拒绝截断（镜像后端 409 规则）', () => {
    const store = useChatStore()
    store.messages = [
      makeMessage('user', '问'),
      makeMessage('assistant', '答'),
      makeMessage('system-notice', '工作流已完成'),
    ]
    expect(store.truncateTailForRegenerate()).toBe(false)
    expect(store.messages).toHaveLength(3)
  })

  it('最后一条 user 之后没有回复时拒绝截断', () => {
    const store = useChatStore()
    store.messages = [makeMessage('user', '刚问还没答')]
    expect(store.truncateTailForRegenerate()).toBe(false)
  })

  it('完全没有 user 消息时拒绝截断', () => {
    const store = useChatStore()
    store.messages = [makeMessage('assistant', '无主之言')]
    expect(store.truncateTailForRegenerate()).toBe(false)
    expect(store.messages).toHaveLength(1)
  })
})

// UI2-②：编辑重发的本地镜像截断——以指定 dbId 的 user 消息为界整段移除
describe('useChatStore.truncateForEdit', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  function msgWithDb(role: AgentMessage['role'], content: string, dbId?: number): AgentMessage {
    return { id: `m-${Math.random()}`, role, content, dbId }
  }

  it('从中间 user 消息截断：它本身及之后全部移除', () => {
    const store = useChatStore()
    store.messages = [
      msgWithDb('user', '第一问', 10),
      msgWithDb('assistant', '第一答', 11),
      msgWithDb('user', '第二问', 12),
      msgWithDb('system-notice', '工作流完成', 13),
      msgWithDb('assistant', '第二答', 14),
    ]
    expect(store.truncateForEdit(12)).toBe(true)
    expect(store.messages.map((m) => m.content)).toEqual(['第一问', '第一答'])
  })

  it('各种错怔靶全部拒绝且不动作表', () => {
    const store = useChatStore()
    store.messages = [msgWithDb('user', '问', 10), msgWithDb('assistant', '答', 11)]
    expect(store.truncateForEdit(11)).toBe(false) // assistant 消息不可锚
    expect(store.truncateForEdit(999)).toBe(false) // 不存在的 id
    expect(store.messages).toHaveLength(2)
  })
})

// UI2-⑥：生成中的发送排队（文本-only）
describe('useChatStore.messageQueue', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('入队/出队先进先出，可单条移除', () => {
    const store = useChatStore()
    store.enqueueMessage('第一条')
    store.enqueueMessage('第二条')
    expect(store.messageQueue.map((q) => q.message)).toEqual(['第一条', '第二条'])

    expect(store.dequeueMessage()?.message).toBe('第一条')
    expect(store.messageQueue).toHaveLength(1)

    store.enqueueMessage('第三条')
    const victim = store.messageQueue[1]
    store.removeQueuedMessage(victim.id)
    expect(store.messageQueue.map((q) => q.message)).toEqual(['第二条'])
  })

  it('切项目重置时队列与追问建议一起清空', () => {
    const store = useChatStore()
    store.enqueueMessage('排队')
    store.followUps = ['你可以接着问 X']
    store.resetForProjectSwitch()
    expect(store.messageQueue).toHaveLength(0)
    expect(store.followUps).toHaveLength(0)
  })
})
