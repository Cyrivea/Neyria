import { describe, expect, it } from 'vitest'

import {
  assetUrl,
  formatCredits,
  isAssetContent,
  isVideoAsset,
  previewStateDisplay,
  previewTotalIsUpperBound,
} from '@/utils/runPreview'

describe('WF1-③ 预览状态映射（单色法：图标+文案，禁用色阶）', () => {
  it('四种状态各有唯一的图标与文案', () => {
    const states = ['reused', 'stale', 'cold', 'blocked'] as const
    const icons = states.map((s) => previewStateDisplay(s).icon)
    const labels = states.map((s) => previewStateDisplay(s).label)
    expect(new Set(icons).size).toBe(4)
    expect(new Set(labels).size).toBe(4)
    expect(previewStateDisplay('blocked').label).toContain('处理')
    expect(previewStateDisplay('stale').label).toContain('重跑')
  })
})

describe('资产内容识别（图/视频走 <img>/<video>，文本走复制）', () => {
  it('只认 /api/assets/ 前缀', () => {
    expect(isAssetContent('/api/assets/abc.png')).toBe(true)
    expect(isAssetContent('歌词正文')).toBe(false)
    expect(isAssetContent(null)).toBe(false)
  })

  it('资产 URL 拼接 API 基址', () => {
    expect(assetUrl('/api/assets/x.png')).toContain('/api/assets/x.png')
  })

  it('mp4 判定走扩展名', () => {
    expect(isVideoAsset('/api/assets/a.mp4')).toBe(true)
    expect(isVideoAsset('/api/assets/a.png')).toBe(false)
  })
})

describe('费用上界语义', () => {
  it('只要有将真跑的节点，总额就是上界', () => {
    expect(previewTotalIsUpperBound({ reused_count: 2, billable_count: 1 })).toBe(true)
    expect(previewTotalIsUpperBound({ reused_count: 3, billable_count: 0 })).toBe(false)
  })
})

describe('积分格式化', () => {
  it('整数去小数、小数去尾零、字符串也认得', () => {
    expect(formatCredits(4)).toBe('4')
    expect(formatCredits(2.5)).toBe('2.5')
    expect(formatCredits('0.50')).toBe('0.5')
  })
})
