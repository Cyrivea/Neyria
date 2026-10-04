import request from '@/utils/request'

export interface LedgerEntry {
  type: string
  amount_credits: string
  plan_balance_after: string
  prepaid_balance_after: string
  description: string
  created_at: string
}

export interface PlanSnapshot {
  plan: string
  included_credits: string
  period_end: string
}

export interface BalanceData {
  plan_credits: string
  prepaid_credits: string
  plan_reserved_credits: string
  prepaid_reserved_credits: string
  plan_snapshot: PlanSnapshot | null
  recent_ledger: LedgerEntry[]
}

export interface UsageSummaryItem {
  capability_id: string
  calls: number
  succeeded: number
  billed_credits: string
}

export interface UsageSummaryData {
  days: number
  items: UsageSummaryItem[]
}

export function getBalance() {
  return request.get<unknown, BalanceData>('/billing/balance')
}

export function getUsageSummary(days = 30) {
  return request.get<unknown, UsageSummaryData>('/billing/usage-summary', { params: { days } })
}
