// SSE 流解析测试（F2 第一批清单：半包、多事件、坏 JSON、流结束残留）。
// mock 全局 fetch 返回自造的 ReadableStream，逐字节控制分块边界。
import { describe, expect, it, vi, beforeEach } from 'vitest'

// agent.ts 的 import 链（request → toast → element-plus CSS）与被测流式逻辑无关，mock 掉
vi.mock('@/utils/request', () => ({ default: {} }))

import { streamAgentChat } from '@/serve/agent'

function sseResponse(chunks: string[]): Response {
  const encoder = new TextEncoder()
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk))
      controller.close()
    },
  })
  return { ok: true, body } as unknown as Response
}

interface Collected {
  tools: string[]
  contents: string[]
  doneCount: number
  citations: string[]
}

async function run(chunks: string[]): Promise<Collected> {
  const collected: Collected = { tools: [], contents: [], doneCount: 0, citations: [] }
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(sseResponse(chunks)))
  await streamAgentChat(
    { message: 'hi' },
    {
      onTool: (e) => collected.tools.push(e.tool_name),
      onContent: (e) => collected.contents.push(e.content),
      onDone: (e) => {
        collected.doneCount++
        collected.citations.push(...(e.citations || []).map((item) => item.source))
      },
    },
  )
  return collected
}

beforeEach(() => {
  vi.unstubAllGlobals()
})

describe('streamAgentChat SSE 解析', () => {
  it('单块含多个事件时全部分发', async () => {
    const result = await run([
      'data: {"type":"content","content":"A"}\n\ndata: {"type":"content","content":"B"}\n\n',
    ])
    expect(result.contents).toEqual(['A', 'B'])
  })

  it('半包：事件跨 chunk 边界拼接后才分发', async () => {
    const result = await run([
      'data: {"type":"content","con',
      'tent":"hello"}\n\ndata: {"type":"done","history":[],"tool_used":null}\n\n',
    ])
    expect(result.contents).toEqual(['hello'])
    expect(result.doneCount).toBe(1)
  })

  it('多字节 UTF-8 字符被 chunk 边界劈开也能正确解码', async () => {
    const encoder = new TextEncoder()
    const bytes = encoder.encode('data: {"type":"content","content":"你好"}\n\n')
    const mid = 25 // 故意切在中文字符的字节中间
    const decoder = new TextDecoder()
    const chunks = [decoder.decode(bytes.slice(0, mid), { stream: true })]
    // 直接用字节流构造，绕过 string 中转
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, mid))
        controller.enqueue(bytes.slice(mid))
        controller.close()
      },
    })
    void chunks
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, body } as unknown as Response))
    const contents: string[] = []
    await streamAgentChat({ message: 'hi' }, { onContent: (e) => contents.push(e.content) })
    expect(contents).toEqual(['你好'])
  })

  it('坏 JSON 行只丢弃该行，不中断后续事件（B4）', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const result = await run([
      'data: {"type":"content","content":"ok1"}\n\n',
      'data: {broken json!!\n\n',
      'data: {"type":"content","content":"ok2"}\n\n',
    ])
    expect(result.contents).toEqual(['ok1', 'ok2'])
    expect(warn).toHaveBeenCalled()
    warn.mockRestore()
  })

  it('流结束时缓冲区残留（无结尾空行）也会被分发', async () => {
    const result = await run(['data: {"type":"content","content":"tail"}']) // 没有 \n\n
    expect(result.contents).toEqual(['tail'])
  })

  it('未知事件类型静默忽略（前后端事件协议演进兼容）', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const result = await run([
      'data: {"type":"error","message":"x"}\n\ndata: {"type":"content","content":"after"}\n\n',
    ])
    expect(result.contents).toEqual(['after'])
    warn.mockRestore()
  })

  it('done 事件保留 RAG 引用元数据', async () => {
    const result = await run([
      'data: {"type":"done","history":[],"tool_used":null,"citations":[{"source":"guide.md","document_id":"abc","chunk_index":1,"chunk_count":3,"distance":0.42}]}\n\n',
    ])
    expect(result.doneCount).toBe(1)
    expect(result.citations).toEqual(['guide.md'])
  })
  it('tool 事件分发 tool_name', async () => {
    const result = await run(['data: {"type":"tool","tool_name":"calculate"}\n\n'])
    expect(result.tools).toEqual(['calculate'])
  })

  it('C27：heartbeat 事件显式分发（不再当未知类型警告），且不干扰后续事件', async () => {
    const heartbeats: string[] = []
    const contents: string[] = []
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          sseResponse([
            'data: {"type":"heartbeat"}\n\ndata: {"type":"content","content":"ok"}\n\n',
          ]),
        ),
    )
    await streamAgentChat(
      { message: 'hi' },
      {
        onHeartbeat: () => heartbeats.push('hb'),
        onContent: (e) => contents.push(e.content),
      },
    )
    expect(heartbeats).toEqual(['hb'])
    expect(contents).toEqual(['ok'])
  })
})
