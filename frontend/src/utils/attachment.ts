/**
 * 附件识别与格式化纯函数（D1 拆分第 1 步，todo §8.4）。
 * 全部为无副作用函数 + 类型定义，不依赖任何组件状态，可单测。
 */
import type { AgentAttachmentItem } from '@/serve/agent'

export type AttachmentKind = 'image' | 'pdf' | 'word' | 'excel' | 'ppt' | 'text' | 'file'

/** 附件白名单（选块器/粘贴/拖拽三个入口共用，防人工散滑） */
export const ATTACHMENT_ACCEPT_EXTENSIONS = [
  'png',
  'jpg',
  'jpeg',
  'gif',
  'webp',
  'pdf',
  'doc',
  'docx',
  'xls',
  'xlsx',
  'ppt',
  'pptx',
  'txt',
  'md',
  'json',
  'csv',
] as const

/** 把任意来源文件拦分成可用/被拒两拨；被拒返回给调用方数量的 toast */
export function filterAcceptableFiles(files: File[]): { accepted: File[]; rejected: File[] } {
  const accepted: File[] = []
  const rejected: File[] = []
  for (const file of files) {
    const ext = getAttachmentExtension(file.name)
    if ((ATTACHMENT_ACCEPT_EXTENSIONS as readonly string[]).includes(ext)) accepted.push(file)
    else rejected.push(file)
  }
  return { accepted, rejected }
}

export interface UploadedAttachment {
  id: string
  name: string
  extension: string
  kind: AttachmentKind
  file: File | null
  previewUrl: string | null
  mimeType: string
  size: number
  badge: string
  meta: string
}

export function getAttachmentExtension(filename: string) {
  const segments = filename.split('.')
  return segments.length > 1 ? segments.pop()?.toLowerCase() || '' : ''
}

export function resolveAttachmentKind(file: Pick<File, 'name' | 'type'>): AttachmentKind {
  const extension = getAttachmentExtension(file.name)
  const mimeType = file.type.toLowerCase()

  if (
    mimeType.startsWith('image/') ||
    ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'svg'].includes(extension)
  ) {
    return 'image'
  }

  if (mimeType === 'application/pdf' || extension === 'pdf') {
    return 'pdf'
  }

  if (
    mimeType.includes('word') ||
    mimeType.includes('officedocument.wordprocessingml') ||
    ['doc', 'docx'].includes(extension)
  ) {
    return 'word'
  }

  if (
    mimeType.includes('excel') ||
    mimeType.includes('spreadsheetml') ||
    ['xls', 'xlsx', 'csv'].includes(extension)
  ) {
    return 'excel'
  }

  if (
    mimeType.includes('powerpoint') ||
    mimeType.includes('presentationml') ||
    ['ppt', 'pptx'].includes(extension)
  ) {
    return 'ppt'
  }

  if (['txt', 'md', 'json'].includes(extension)) {
    return 'text'
  }

  return 'file'
}

export function getAttachmentBadge(kind: AttachmentKind, extension: string) {
  if (kind === 'pdf') return 'PDF'
  if (kind === 'word') return extension === 'doc' ? 'DOC' : 'DOCX'
  if (kind === 'excel') return extension === 'xls' ? 'XLS' : extension === 'csv' ? 'CSV' : 'XLSX'
  if (kind === 'ppt') return extension === 'ppt' ? 'PPT' : 'PPTX'
  if (kind === 'text') return extension ? extension.toUpperCase() : 'TXT'
  return extension ? extension.toUpperCase() : 'FILE'
}

export function getAttachmentMeta(kind: AttachmentKind) {
  const labelMap: Record<AttachmentKind, string> = {
    image: '图片附件',
    pdf: 'PDF 文档',
    word: 'Word 文档',
    excel: 'Excel 表格',
    ppt: 'PPT 演示稿',
    text: '文本资料',
    file: '文件资料',
  }

  return labelMap[kind]
}

function nextAttachmentId() {
  return `attachment-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

export function buildUploadedAttachment(file: File): UploadedAttachment {
  const extension = getAttachmentExtension(file.name)
  const kind = resolveAttachmentKind(file)

  return {
    id: nextAttachmentId(),
    name: file.name,
    extension,
    kind,
    file,
    previewUrl: kind === 'image' ? URL.createObjectURL(file) : null,
    mimeType: file.type,
    size: file.size,
    badge: getAttachmentBadge(kind, extension),
    meta: getAttachmentMeta(kind),
  }
}

/**
 * 粘贴/拖拽来源的文件改名（UI2 第二批①②）：剪贴板图片的名字是无意义的
 * “image.png”/"blob"，两个连续粘贴会同名打架；统一改成 `pasted-时间戳-随机串.扩展名`。
 * 已有正常名字的文件（拖拽本地文件）原样返回。
 */
export function renamePastedFile(file: File, now: Date = new Date()): File {
  const generic = !file.name || file.name === 'blob' || /^image\.[a-z0-9]+$/i.test(file.name)
  if (!generic) return file
  const fromMime = file.type.split('/')[1]?.replace('jpeg', 'jpg') || ''
  const extension = getAttachmentExtension(file.name) || fromMime || 'bin'
  const stamp = now.toISOString().replace(/[-:]/g, '').replace('T', '-').slice(0, 15)
  const rand = Math.random().toString(36).slice(2, 6)
  return new File([file], `pasted-${stamp}-${rand}.${extension}`, { type: file.type })
}

/** 发送消息时给消息列表克隆一份附件展示对象（图片附件生成独立 blob 预览） */
export function cloneAttachmentForMessage(attachment: UploadedAttachment): UploadedAttachment {
  return {
    ...attachment,
    file: null,
    previewUrl:
      attachment.kind === 'image' && attachment.file
        ? URL.createObjectURL(attachment.file)
        : attachment.previewUrl,
  }
}

/** 从服务端历史里的附件元数据还原展示对象（无本地文件与预览） */
export function hydrateAttachmentFromPayload(payload: AgentAttachmentItem): UploadedAttachment {
  const extension = getAttachmentExtension(payload.name)
  const kind =
    (payload.kind as AttachmentKind | undefined) ||
    resolveAttachmentKind({ name: payload.name, type: '' })

  return {
    id: nextAttachmentId(),
    name: payload.name,
    extension,
    kind,
    file: null,
    previewUrl: null,
    mimeType: '',
    size: 0,
    badge: payload.badge || getAttachmentBadge(kind, extension),
    meta: payload.meta || getAttachmentMeta(kind),
  }
}

/** blob: 预览 URL 属于当前页面实例，替换/移除消息与附件时必须主动释放 */
export function revokeAttachmentPreview(attachment: UploadedAttachment) {
  if (attachment.previewUrl?.startsWith('blob:')) {
    URL.revokeObjectURL(attachment.previewUrl)
  }
}
