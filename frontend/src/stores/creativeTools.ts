import { readScoped, writeScoped } from '@/utils/storageScope'
import { computed, ref, watch } from 'vue'
import { defineStore } from 'pinia'

export type CreativeToolType = 'lyrics' | 'image' | 'video' | 'audio'

export interface CreativeToolCatalogItem {
  type: CreativeToolType
  name: string
  description: string
  badge: string
  color: string
  inputHint: string
  defaults: Record<string, string>
}

export interface CreativeToolReference {
  id: string
  type: 'image' | 'text' | 'file'
  name: string
  detail?: string
}

export interface CreativeToolInstance {
  id: string
  type: CreativeToolType
  name: string
  description: string
  badge: string
  color: string
  inputHint: string
  params: Record<string, string>
  references: CreativeToolReference[]
  updatedAt: string
}

export const creativeToolCatalog: CreativeToolCatalogItem[] = [
  {
    type: 'lyrics',
    name: '歌词生成',
    description: '根据主题、情绪和曲风生成完整歌词草稿。',
    badge: '文字创作',
    color: '#b7b7b7',
    inputHint: '主题、情绪、曲风',
    defaults: {
      theme: '夜航中的城市与未寄出的信',
      style: '流行抒情',
      mood: '温柔、克制',
      language: '中文',
    },
  },
  {
    type: 'image',
    name: '图像生成',
    description: '把文字描述转成歌曲封面、角色设定和视觉概念图。',
    badge: '文字生图',
    color: '#d0d0d0',
    inputHint: '画面描述、风格、比例',
    defaults: {
      prompt: '',
      style: '电影概念艺术',
      ratio: '16:9',
      palette: '深蓝与紫色',
    },
  },
  {
    type: 'video',
    name: '视频生成',
    description: '把镜头与画面描述转成短视频片段（文生视频）。',
    badge: '文字生视频',
    color: '#a6a6a6',
    inputHint: '镜头描述、风格、比例、时长',
    defaults: {
      prompt: '镜头缓慢推进穿过雨夜的城市街道，霓虹灯反射在湿漉漉的地面上',
      style: '电影写实',
      ratio: '16:9',
      duration: '5 秒',
    },
  },
  {
    type: 'audio',
    name: '音频生成',
    description: '分析输入音频的频段和波形特征，生成相似质感的音频。',
    badge: '音频衍生',
    color: '#c4c4c4',
    inputHint: '输入音频、特征、强度',
    defaults: {
      source: '待上传音频.wav',
      feature: '频段与波形质感',
      intensity: '中等',
      variation: '保留原始节奏',
    },
  },
]

const STORAGE_KEY = 'mnc-creative-tools-draft'

function createId() {
  return `creative-tool-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

function catalogItem(type: CreativeToolType) {
  return creativeToolCatalog.find((item) => item.type === type) ?? creativeToolCatalog[0]
}

export const useCreativeToolsStore = defineStore('creativeTools', () => {
  const instances = ref<CreativeToolInstance[]>([])
  const selectedId = ref<string | null>(null)
  const activePanelId = ref<string | null>(null)
  let currentStorageKey = STORAGE_KEY

  const selectedTool = computed(
    () => instances.value.find((tool) => tool.id === selectedId.value) ?? null,
  )
  const activePanelTool = computed(
    () => instances.value.find((tool) => tool.id === activePanelId.value) ?? null,
  )
  const hasTools = computed(() => instances.value.length > 0)

  function persist() {
    writeScoped(
      currentStorageKey,
      JSON.stringify({ instances: instances.value, selectedId: selectedId.value }),
    )
  }

  function loadForProject(projectId: number | null) {
    currentStorageKey = projectId == null ? STORAGE_KEY : `${STORAGE_KEY}:${projectId}`
    try {
      const raw = readScoped(currentStorageKey)
      if (!raw) {
        instances.value = []
        selectedId.value = null
        activePanelId.value = null
        return
      }
      const parsed = JSON.parse(raw) as {
        instances?: CreativeToolInstance[]
        selectedId?: string | null
      }
      instances.value = Array.isArray(parsed.instances)
        ? parsed.instances.map((instance) => ({
            ...instance,
            references: instance.references ?? [],
          }))
        : []
      selectedId.value = parsed.selectedId ?? null
      activePanelId.value = null
    } catch {
      instances.value = []
      selectedId.value = null
      activePanelId.value = null
    }
  }

  function addTool(type: CreativeToolType) {
    return createToolInstance(createId(), type, { ...catalogItem(type).defaults })
  }

  /** 后端/AI 创建或更新节点时，把它们映射成同 ID 的 Block 实例，供参数面板和画布联动。 */
  function ensureToolInstance(id: string, type: CreativeToolType, params?: Record<string, string>) {
    const existing = instances.value.find((item) => item.id === id)
    if (existing) {
      if (params) existing.params = { ...existing.params, ...params }
      existing.updatedAt = new Date().toISOString()
      persist()
      return existing
    }
    return createToolInstance(id, type, { ...catalogItem(type).defaults, ...(params ?? {}) })
  }

  function createToolInstance(id: string, type: CreativeToolType, params: Record<string, string>) {
    const item = catalogItem(type)
    const instance: CreativeToolInstance = {
      id,
      type: item.type,
      name: item.name,
      description: item.description,
      badge: item.badge,
      color: item.color,
      inputHint: item.inputHint,
      params,
      references: [],
      updatedAt: new Date().toISOString(),
    }
    instances.value.push(instance)
    selectedId.value = instance.id
    activePanelId.value = instance.id
    persist()
    return instance
  }

  function selectTool(id: string, openPanel = true) {
    if (!instances.value.some((tool) => tool.id === id)) return
    selectedId.value = id
    if (openPanel) activePanelId.value = id
    persist()
  }

  function updateParam(id: string, key: string, value: string) {
    const tool = instances.value.find((item) => item.id === id)
    if (!tool) return
    tool.params[key] = value
    tool.updatedAt = new Date().toISOString()
    persist()
  }

  function saveTool(id: string) {
    const tool = instances.value.find((item) => item.id === id)
    if (!tool) return
    tool.updatedAt = new Date().toISOString()
    persist()
  }

  function addReference(id: string, reference: Omit<CreativeToolReference, 'id'>) {
    const tool = instances.value.find((item) => item.id === id)
    if (!tool) return
    tool.references.push({
      id: `${id}-reference-${Date.now()}-${tool.references.length}`,
      ...reference,
    })
    tool.updatedAt = new Date().toISOString()
    persist()
  }

  function removeReference(id: string, referenceId: string) {
    const tool = instances.value.find((item) => item.id === id)
    if (!tool) return
    tool.references = tool.references.filter((reference) => reference.id !== referenceId)
    tool.updatedAt = new Date().toISOString()
    persist()
  }

  function removeTool(id: string) {
    const index = instances.value.findIndex((tool) => tool.id === id)
    if (index < 0) return
    instances.value.splice(index, 1)
    if (selectedId.value === id) selectedId.value = instances.value[index]?.id ?? null
    if (activePanelId.value === id) activePanelId.value = null
    persist()
  }

  function closePanel() {
    activePanelId.value = null
    persist()
  }

  watch(instances, persist, { deep: true })

  return {
    instances,
    selectedId,
    activePanelId,
    selectedTool,
    activePanelTool,
    hasTools,
    loadForProject,
    addTool,
    ensureToolInstance,
    selectTool,
    updateParam,
    saveTool,
    addReference,
    removeReference,
    removeTool,
    closePanel,
  }
})
