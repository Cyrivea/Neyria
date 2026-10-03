import request from '@/utils/request'

export type LyricsGenerateStatus = 'succeeded' | 'failed' | 'model_unavailable'

export interface LyricsGenerateResponse {
  capability_id: 'lyrics.generate'
  status: LyricsGenerateStatus
  result: {
    format: 'markdown'
    content: string
  } | null
  error: string | null
}

export interface LyricsGenerateParams {
  theme: string
  style: string
  mood: string
  language: string
}

/**
 * I12 幂等约定：未来 Block 面板接入独立入口时，「一次用户点击」生成一个
 * crypto.randomUUID() 存入组件状态，网络重试复用同一 key，成功/失败后清空。
 * 后端凭 reservation 唯一索引拒重（409），生成/扣费绝不会发生第二次。
 */
export async function runLyricsCapability(
  projectId: number | null,
  inputs: LyricsGenerateParams,
  idempotencyKey?: string,
) {
  return request.post<unknown, LyricsGenerateResponse>('/capabilities/run', {
    capability_id: 'lyrics.generate',
    project_id: projectId,
    inputs,
    ...(idempotencyKey ? { idempotency_key: idempotencyKey } : {}),
  })
}
