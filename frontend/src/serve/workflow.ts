import request from '@/utils/request'

export type WorkflowRunStatus = 'idle' | 'running' | 'succeeded' | 'failed' | 'model_unavailable'

export interface WorkflowCandidate {
  id: string
  status: string
  content: string | null
  error?: string | null
  created_at?: string
}

export interface WorkflowDraftNode {
  id: string
  kind?: 'endpoint' | 'tool'
  endpoint?: 'input' | 'output'
  toolId?: string
  type?: string
  capability_id?: string
  name?: string
  badge?: string
  description?: string
  color?: string
  x?: number
  y?: number
  params?: Record<string, string>
  runStatus?: WorkflowRunStatus
  result?: { format?: string; content?: string } | null
  error?: string | null
  candidates?: WorkflowCandidate[]
  selectedCandidateId?: string | null
  /** 服务端写入：本次运行是否命中缓存复用（零调用零计费）。 */
  reused?: boolean
}

export interface WorkflowDraft {
  nodes: WorkflowDraftNode[]
  edges: { source: string; target: string }[]
  endpointPositions?: Record<string, { x: number; y: number }>
}

export interface WorkflowDraftResponse {
  id: number | null
  revision: number
  draft: WorkflowDraft
}

export interface WorkflowRunResponse extends WorkflowDraftResponse {
  run_id: string
  job_id?: string | null
  status: WorkflowRunStatus | 'queued'
  result?: WorkflowDraftNode['result']
  error: string | null
}

export async function getWorkflowDraft(projectId: number | null) {
  const suffix = projectId != null ? `?project_id=${projectId}` : ''
  return request.get<unknown, WorkflowDraftResponse>(`/workflows/draft${suffix}`)
}

export async function saveWorkflowDraft(
  projectId: number | null,
  draft: WorkflowDraft,
  expectedRevision: number,
) {
  return request.put<unknown, WorkflowDraftResponse>('/workflows/draft', {
    project_id: projectId,
    draft,
    expected_revision: expectedRevision,
  })
}

export async function runWorkflow(projectId: number | null) {
  const suffix = projectId != null ? `?project_id=${projectId}` : ''
  return request.post<unknown, WorkflowRunResponse>(`/workflows/run${suffix}`)
}

// ---------- WF1-③ 全局运行按钮 + 队列档案馆 ----------

export type RunPreviewBlockState = 'reused' | 'stale' | 'cold' | 'blocked'

export interface RunPreviewBlock {
  node_id: string
  name: string
  capability_id: string
  chain_no: number | null
  state: RunPreviewBlockState
  reason: string | null
  cost_credits: number
}

export interface RunPreviewChain {
  chain_no: number | null
  chain_id: string | null
  label: string
  cost_credits: number
  blocks: RunPreviewBlock[]
}

export interface WorkflowRunPreview {
  ready: boolean
  reason: string | null
  total_cost_credits: number
  reused_count: number
  billable_count: number
  chains: RunPreviewChain[]
}

export function getWorkflowRunPreview(projectId: number | null) {
  const suffix = projectId != null ? `?project_id=${projectId}` : ''
  return request.get<unknown, WorkflowRunPreview>(`/workflows/run-preview${suffix}`)
}

export interface ArchiveBlock {
  node_id: string | null
  node_name: string
  capability_id: string | null
  status: string
  reused: boolean
  content: string | null
  error: string | null
}

export interface ArchiveChain {
  chain_no: number | null
  label: string
  blocks: ArchiveBlock[]
}

export interface ArchivedRun {
  run_id: string
  status: string
  kept: boolean
  created_at: string | null
  finished_at: string | null
  error: string | null
  progress: { done: number; total: number }
  chains: ArchiveChain[]
}

export interface WorkflowRunsList {
  runs: ArchivedRun[]
  limit: number
}

export function listWorkflowRuns(projectId: number | null, limit = 20) {
  const params = new URLSearchParams()
  if (projectId != null) params.set('project_id', String(projectId))
  params.set('limit', String(limit))
  return request.get<unknown, WorkflowRunsList>(`/workflows/runs?${params}`)
}

export function keepWorkflowRun(runId: string, kept: boolean) {
  return request.patch<unknown, { run_id: string; kept: boolean }>(
    `/workflows/runs/${runId}/keep`,
    { kept },
  )
}

export interface DeleteRunResult {
  deleted: boolean
  run_id: string
  files_removed: number
  /** 诚实字段：运行即成账，删除不退积分 */
  refund: boolean
}

export function deleteWorkflowRun(runId: string) {
  return request.delete<unknown, DeleteRunResult>(`/workflows/runs/${runId}`)
}
