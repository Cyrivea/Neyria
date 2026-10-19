/**
 * WF1-③ 全局运行的预览/档案馆公共语义。
 *
 * 设计约束：
 * - 全站黑白灰（CLAUDE.md 单色法）：状态不用色阶，只靠「图标符号 + 文案」区分；
 * - 费用是预占口径的上界，不是承诺：缓存命中的节点才是确定免费的部分。
 */
import { API_BASE } from '@/constants'
import type { RunPreviewBlockState } from '@/serve/workflow'

export interface PreviewStateDisplay {
  /** 高对比度单符号，色盲/单色屏都能读 */
  icon: string
  label: string
}

export function previewStateDisplay(state: RunPreviewBlockState): PreviewStateDisplay {
  switch (state) {
    case 'reused':
      return { icon: '∞', label: '命中缓存' }
    case 'stale':
      return { icon: '↻', label: '将重跑' }
    case 'blocked':
      return { icon: '⚠', label: '需先处理' }
    default:
      return { icon: '●', label: '首次生成' }
  }
}

/** 消费方预估值是不是上界：只到「会真跑」的节点才收钱。 */
export function previewTotalIsUpperBound(preview: {
  reused_count: number
  billable_count: number
}): boolean {
  return preview.billable_count > 0
}

/** 产物内容判定：/api/assets/xx.yyy 是二进制资产（图/视频），其余是文本。 */
export function isAssetContent(content: string | null | undefined): boolean {
  return typeof content === 'string' && content.startsWith('/api/assets/')
}

export function assetUrl(content: string): string {
  return `${API_BASE}${content}`
}

export function isVideoAsset(url: string): boolean {
  return /\.mp4($|\?)/i.test(url)
}

export function formatCredits(value: number | string): string {
  const n = typeof value === 'string' ? parseFloat(value) : value
  if (!Number.isFinite(n)) return String(value)
  return Number.isInteger(n) ? String(n) : n.toFixed(2).replace(/0+$/, '').replace(/\.$/, '')
}
