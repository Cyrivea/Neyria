<template>
  <div class="billing-page">
    <header class="billing-topbar">
      <button class="billing-back" type="button" aria-label="返回工作台" @click="goBack">
        ← 返回工作台
      </button>
      <div class="billing-title-wrap">
        <h1 class="billing-title">计费与用量</h1>
        <p class="billing-caption">额度、近 30 天用量与账户流水，数据来自后端账本。</p>
      </div>
      <button
        class="billing-refresh"
        type="button"
        :disabled="loading"
        aria-label="刷新数据"
        @click="load"
      >
        {{ loading ? '刷新中…' : '刷新' }}
      </button>
    </header>

    <main v-if="error" class="billing-state-card" role="alert">
      <p class="billing-state-title">计费数据加载失败</p>
      <p class="billing-state-desc">{{ error }}</p>
      <button class="billing-action-button primary" type="button" @click="load">重试</button>
    </main>

    <main v-else class="billing-columns">
      <!-- 额度总览 -->
      <section class="billing-overview" aria-label="额度总览">
        <div class="billing-stat-card">
          <div class="billing-stat-label">
            套餐额度（可用）<span v-if="planSnapshot" class="billing-plan-name">
              {{ planSnapshot.plan }}</span
            >
          </div>
          <div class="billing-stat-value">
            {{ balance?.plan_credits ?? '—' }}<span class="billing-stat-unit">积分</span>
          </div>
          <template v-if="planSnapshot">
            <div class="billing-progress" role="presentation">
              <div class="billing-progress-fill" :style="{ width: planUsedPercent + '%' }"></div>
            </div>
            <div class="billing-stat-meta">
              已用 {{ planUsed }} / {{ planSnapshot.included_credits }} 积分 ｜
              {{ planSnapshot.period_end.slice(0, 10) }} 到期；冻结中
              {{ balance?.plan_reserved_credits ?? '0' }}（多退少补）
            </div>
          </template>
          <div v-else class="billing-stat-meta">
            当前无生效套餐；冻结中 {{ balance?.plan_reserved_credits ?? '0' }} 积分
          </div>
        </div>
        <div class="billing-stat-card">
          <div class="billing-stat-label">预付余额（可用）</div>
          <div class="billing-stat-value">
            {{ balance?.prepaid_credits ?? '—' }}<span class="billing-stat-unit">积分</span>
          </div>
          <div class="billing-stat-meta">
            套餐额度用尽后自动扣减；冻结中 {{ balance?.prepaid_reserved_credits ?? '0' }} 积分
          </div>
        </div>
      </section>

      <!-- 近 30 天用量 -->
      <section class="billing-section" aria-labelledby="billingSummaryTitle">
        <div id="billingSummaryTitle" class="billing-section-head">近 30 天用量</div>
        <div v-if="summaryItems.length" class="billing-summary-grid">
          <div v-for="item in summaryItems" :key="item.capability_id" class="billing-summary-item">
            <div class="billing-summary-label">{{ capabilityLabel(item.capability_id) }}</div>
            <div class="billing-summary-value">{{ item.billed_credits }} 积分</div>
            <div class="billing-summary-meta">
              {{ item.calls }} 次调用{{
                item.succeeded < item.calls
                  ? `（失败 ${item.calls - item.succeeded} 次，不计费）`
                  : ''
              }}
            </div>
          </div>
        </div>
        <div v-else class="billing-empty">
          {{ loading ? '正在加载…' : '近 30 天暂无生成记录，去工作台跑一个吧' }}
        </div>
      </section>

      <!-- 账户流水 -->
      <section class="billing-section" aria-labelledby="billingLedgerTitle">
        <div id="billingLedgerTitle" class="billing-section-head">账户流水</div>
        <ul v-if="ledger.length" class="billing-usage-list">
          <li v-for="(entry, idx) in ledger" :key="idx" class="billing-usage-item">
            <div class="billing-usage-main">
              <span class="billing-usage-capability">{{ ledgerLabel(entry.type) }}</span>
              <span class="billing-usage-model">{{ entry.description }}</span>
            </div>
            <div class="billing-usage-side">
              <span
                class="billing-usage-credits"
                :class="{ inflow: !entry.amount_credits.startsWith('-') }"
              >
                {{ formatAmount(entry.amount_credits) }} 积分
              </span>
              <span class="billing-usage-time">{{ formatTime(entry.created_at) }}</span>
            </div>
          </li>
        </ul>
        <div v-else class="billing-empty">
          {{ loading ? '正在加载…' : '暂无流水，额度发放/生成/充值都会在这里留痕' }}
        </div>
      </section>

      <!-- 消费设置与操作：后端能力后置（I7/I9），先摆出不装死 -->
      <section class="billing-section" aria-labelledby="billingSettingsTitle">
        <div id="billingSettingsTitle" class="billing-section-head">
          消费设置 <span class="billing-soon-badge">即将上线</span>
        </div>
        <div class="billing-setting-row muted">
          <div class="billing-setting-copy">
            <div class="billing-setting-name">月度消费上限</div>
            <div class="billing-setting-desc">
              达到上限后暂停所有付费调用，次月自动恢复（成本风控 I9 未上线）
            </div>
          </div>
          <div class="billing-limit-value">未设置</div>
        </div>
        <div class="billing-actions">
          <button
            class="billing-action-button primary"
            type="button"
            disabled
            title="支付渠道对接中（I7）"
          >
            升级套餐 · 即将上线
          </button>
          <button class="billing-action-button" type="button" disabled title="支付渠道对接中（I7）">
            充值预付余额 · 即将上线
          </button>
        </div>
      </section>
    </main>
  </div>
</template>

<script setup lang="ts">
/**
 * 计费与用量页（I8 接线版）。
 * 数据源：/billing/balance（余额四元组 + 最近流水）+ /billing/usage-summary（近 30 天聚合）。
 * 消费设置与升级/充值按钮仅作占位——后端支付与成本风控未上线（I7/I9），
 * 不做假交互，按钮直接置灰并注明状态。
 */
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  getBalance,
  getUsageSummary,
  type BalanceData,
  type UsageSummaryItem,
} from '@/serve/billing'

const router = useRouter()

const balance = ref<BalanceData | null>(null)
const summaryItems = ref<UsageSummaryItem[]>([])
const loading = ref(false)
const error = ref('')

const ledger = ref<BalanceData['recent_ledger']>([])

const planSnapshot = computed(() => balance.value?.plan_snapshot ?? null)
/** 已用 = 总额 - 可用（冻结视为未消耗，结算后自然收拢）；数字全走后端账本，前端不猜。 */
const planUsed = computed(() => {
  const snap = planSnapshot.value
  if (!snap || !balance.value) return '0'
  const used = Number(snap.included_credits) - Number(balance.value.plan_credits)
  return String(Math.max(0, Math.round(used * 1e6) / 1e6))
})
const planUsedPercent = computed(() => {
  const snap = planSnapshot.value
  if (!snap || !balance.value) return 0
  const included = Number(snap.included_credits)
  if (included <= 0) return 0
  return Math.min(100, Math.round((Number(planUsed.value) / included) * 100))
})

const CAPABILITY_LABELS: Record<string, string> = {
  'lyrics.generate': '歌词生成',
  'image.generate': '图像生成',
  'video.generate': '视频生成',
}

const LEDGER_LABELS: Record<string, string> = {
  subscription_grant: '套餐发放',
  usage_reserve: '调用预占',
  usage_settlement: '结算扣减',
  usage_release: '退回',
  top_up: '预付充值',
  refund: '退款',
  expiration: '套餐到期清零',
  manual_adjustment: '人工调账',
}

function capabilityLabel(id: string): string {
  return CAPABILITY_LABELS[id] ?? id
}

function ledgerLabel(type: string): string {
  return LEDGER_LABELS[type] ?? type
}

/** 单色 UI 去掉了正负颜色语义，入账金额加 + 前缀保持可读性。 */
function formatAmount(amount: string): string {
  if (amount.startsWith('-') || Number(amount) === 0) return amount
  return `+${amount}`
}

function formatTime(iso: string): string {
  const dt = new Date(iso)
  if (Number.isNaN(dt.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${dt.getMonth() + 1}-${pad(dt.getDate())} ${pad(dt.getHours())}:${pad(dt.getMinutes())}`
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [balanceData, summary] = await Promise.all([getBalance(), getUsageSummary(30)])
    balance.value = balanceData
    ledger.value = balanceData.recent_ledger // 后端新→久排序，页面保持新在前
    summaryItems.value = summary.items
  } catch (err) {
    error.value = err instanceof Error ? err.message : '网络异常，请稍后重试'
  } finally {
    loading.value = false
  }
}

function goBack() {
  router.push('/workstation')
}

onMounted(load)
</script>

<style scoped>
.billing-page {
  min-height: 100vh;
  background: var(--page-bg, #0f1014);
  color: var(--text-primary, #f4f4f4);
  padding: 0 24px 64px;
}

.billing-topbar {
  max-width: 880px;
  margin: 0 auto;
  padding: 28px 0 24px;
  display: flex;
  align-items: center;
  gap: 18px;
}

.billing-back {
  padding: 7px 14px;
  border-radius: 10px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: rgba(255, 255, 255, 0.02);
  color: var(--text-secondary, #c9cdd6);
  font-size: 13px;
  cursor: pointer;
  white-space: nowrap;
  transition:
    background 140ms ease,
    color 140ms ease;
}

.billing-back:hover {
  background: rgba(255, 255, 255, 0.06);
  color: var(--text-primary, #f4f4f4);
}

.billing-title-wrap {
  flex: 1;
  min-width: 0;
}

.billing-title {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
}

.billing-caption {
  margin: 4px 0 0;
  font-size: 12px;
  color: #6f6f76;
}

.billing-refresh {
  padding: 7px 16px;
  border-radius: 10px;
  border: 1px solid rgba(156, 156, 156, 0.4);
  background: transparent;
  color: #9c9c9c;
  font-size: 13px;
  cursor: pointer;
  white-space: nowrap;
}

.billing-refresh:disabled {
  opacity: 0.5;
  cursor: default;
}

.billing-columns {
  max-width: 880px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 28px;
}

.billing-state-card {
  max-width: 880px;
  margin: 40px auto 0;
  border: 1px solid rgba(142, 142, 142, 0.3);
  border-radius: 16px;
  padding: 32px;
  text-align: center;
}

.billing-state-title {
  margin: 0 0 8px;
  font-size: 15px;
  font-weight: 600;
}

.billing-state-desc {
  margin: 0 0 18px;
  font-size: 13px;
  color: #6f6f76;
}

/* ---- 额度总览 ---- */
.billing-overview {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 14px;
}

.billing-stat-card {
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 16px;
  padding: 20px 22px;
  background: rgba(255, 255, 255, 0.02);
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.billing-stat-label {
  font-size: 12px;
  color: #6f6f76;
}

.billing-stat-value {
  font-size: 34px;
  font-weight: 700;
  line-height: 1.1;
}

.billing-plan-name {
  margin-left: 8px;
  padding: 1px 8px;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 999px;
  font-size: 11px;
  color: var(--text-secondary, #c9cdd6);
  font-weight: 400;
  vertical-align: 1px;
}

.billing-progress {
  height: 6px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.07);
  overflow: hidden;
}

.billing-progress-fill {
  height: 100%;
  border-radius: 999px;
  background: #d0d0d0;
  transition: width 300ms ease;
}

.billing-stat-unit {
  margin-left: 6px;
  font-size: 13px;
  font-weight: 400;
  color: #6f6f76;
}

.billing-stat-meta {
  font-size: 12px;
  color: #6f6f76;
  line-height: 1.6;
}

/* ---- 区段 ---- */
.billing-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.billing-section-head {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-secondary, #c9cdd6);
}

.billing-soon-badge {
  display: inline-block;
  margin-left: 8px;
  padding: 1px 8px;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 999px;
  color: #6f6f76;
  font-size: 11px;
  font-weight: 400;
  vertical-align: 1px;
}

/* ---- 用量摘要 ---- */
.billing-summary-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 12px;
}

.billing-summary-item {
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 12px;
  padding: 12px 14px;
  background: rgba(255, 255, 255, 0.015);
}

.billing-summary-label {
  font-size: 11px;
  color: #6f6f76;
}

.billing-summary-value {
  margin-top: 6px;
  font-size: 16px;
  font-weight: 600;
}

.billing-summary-meta {
  margin-top: 2px;
  font-size: 11px;
  color: #6f6f76;
  line-height: 1.5;
}

.billing-empty {
  border: 1px dashed rgba(255, 255, 255, 0.1);
  border-radius: 12px;
  padding: 28px;
  text-align: center;
  font-size: 12px;
  color: #6f6f76;
}

/* ---- 流水列表 ---- */
.billing-usage-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 12px;
  overflow: hidden;
}

.billing-usage-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 11px 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.05);
}

.billing-usage-item:last-child {
  border-bottom: none;
}

.billing-usage-main {
  display: flex;
  align-items: baseline;
  gap: 10px;
  min-width: 0;
}

.billing-usage-capability {
  font-size: 13px;
  flex-shrink: 0;
}

.billing-usage-model {
  font-size: 11px;
  color: #6f6f76;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.billing-usage-side {
  display: flex;
  align-items: baseline;
  gap: 12px;
  flex-shrink: 0;
}

.billing-usage-credits {
  font-size: 12px;
  color: var(--text-secondary, #c9cdd6);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.billing-usage-credits.inflow {
  color: var(--text-primary, #f4f4f4);
  font-weight: 600;
}

.billing-usage-time {
  font-size: 11px;
  color: #6f6f76;
  min-width: 72px;
  text-align: right;
}

/* ---- 设置与操作 ---- */
.billing-setting-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 12px 16px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 12px;
}

.billing-setting-row.muted {
  opacity: 0.65;
}

.billing-setting-name {
  font-size: 13px;
}

.billing-setting-desc {
  margin-top: 2px;
  font-size: 11px;
  color: #6f6f76;
  line-height: 1.5;
}

.billing-limit-value {
  font-size: 12px;
  color: #6f6f76;
  flex-shrink: 0;
}

.billing-actions {
  display: flex;
  gap: 10px;
}

.billing-action-button {
  flex: 1;
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid rgba(255, 255, 255, 0.12);
  background: rgba(255, 255, 255, 0.02);
  color: var(--text-secondary, #c9cdd6);
  font-size: 13px;
  cursor: pointer;
  transition: background 140ms ease;
}

.billing-action-button:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.05);
}

.billing-action-button.primary {
  background: #9c9c9c;
  border-color: #9c9c9c;
  color: #0a0a0c;
  font-weight: 600;
}

.billing-action-button:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

@media (max-width: 640px) {
  .billing-overview {
    grid-template-columns: 1fr;
  }

  .billing-topbar {
    flex-wrap: wrap;
  }
}
</style>
