import request from '@/utils/request'

export interface ReferenceAsset {
  asset_id: string
  filename: string
  path: string
  content_type: string
  byte_size: number
}

export interface CapabilityFeatures {
  capability_id: string
  /** 参考图上限（0 = 该能力不吃参考图） */
  reference_limit: number
  /** 当前部署的图像模型能不能真吃参考图（图生图） */
  reference_supported: boolean
  /** 当前 Provider 的 ratio → 实际像素映射（提交前核对用） */
  ratio_sizes?: Record<string, string>
}

/** 参考图上传：落 assets 表（capability_id=image.reference），运行时可做属主校验。 */
export async function uploadReferenceImage(file: File, projectId?: number | null) {
  const formData = new FormData()
  formData.append('file', file)
  const suffix = projectId != null ? `?project_id=${projectId}` : ''
  return request.post<unknown, ReferenceAsset>(`/assets/reference${suffix}`, formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

export async function deleteReferenceImage(assetId: string) {
  return request.delete<unknown, { status: string }>(`/assets/reference/${assetId}`)
}

/**
 * 面板的能力描述。模型选择上线前它承担一件事：让用户在上传之前就知道
 * 「当前模型吃不吃参考图」，而不是传完、跑完、被拒了才知道。
 */
export async function getCapabilityFeatures(capabilityId: string) {
  return request.get<unknown, CapabilityFeatures>(`/capabilities/${capabilityId}/features`)
}
