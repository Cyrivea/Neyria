<template>
  <!--
    过渡覆盖层组件。

    视觉规范（与原 Loading.vue 一字不差）：
      - #040404 黑底白字
      - 0.5px / 1px 细线圆环 + 1px 指针
      - 1px 进度条

    行为规范：
      - 纯透明度渐显入场 600ms（cubic-bezier .2 .8 .2 1）
      - 渐显退场 620ms（opacity + 6px blur + -4px translateY）
      - 真实进度优先：调用方一旦 setProgress(>0) 即接管显示
      - 真实进度缺失时回退到内置 rAF（0→100 / 2.4s），让快速加载也有视觉
      - 1s 最低展示时长由 store 强制（hide() 在 minDuration 内被自动延后）
      - 无任何路由依赖，无任何外部组件联动，零侵入挂载
  -->
  <Transition name="overlay" appear>
    <div v-if="loading.visible" class="loading-page" role="status" aria-live="polite">
      <div class="grid-veil" aria-hidden="true"></div>

      <div class="stage">
        <div class="rings" aria-hidden="true">
          <span class="ring ring--outer"></span>
          <span class="ring ring--mid"></span>
          <span class="ring ring--inner"></span>
          <span class="tickmark"></span>
        </div>

        <div class="brand">
          <div class="brand-mark">NEYRIA</div>
          <div class="brand-name">AI CREATIVE WORKSTATION</div>
        </div>

        <div class="status-line">
          <span class="status-text">{{ loading.label }}</span>
          <span class="status-pct">
            {{ hasRealProgress ? Math.round(displayedProgress) + '%' : '•••' }}
          </span>
        </div>

        <div class="track" aria-hidden="true">
          <span
            class="track-fill"
            :style="{ transform: `scaleX(${displayedProgress / 100})` }"
          ></span>
        </div>
      </div>
    </div>
  </Transition>
</template>

<script setup lang="ts">
// 全局覆盖层组件。
//
// 关键点：
// 1. 完全 store 驱动，不读路由，不调 useRouter。
// 2. 进场 → 启动内置 rAF（仅在 realProgress === 0 时起视觉兜底作用）。
// 3. 退场 → 由 store 的智能 hide() 决定 visible=false 的时刻；
//    <Transition> 负责执行 620ms 渐隐 + blur + translateY，零闪烁。
// 4. 1s 最低展示时长由 store 保证（hide() < 1s 时自动延后），本组件不重复计时。
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useLoadingStore } from '@/stores/loading'

const loading = useLoadingStore()
const { visible, realProgress } = storeToRefs(loading)

const prefersReduced =
  typeof window !== 'undefined' && window.matchMedia('(prefers-reduced-motion: reduce)').matches

// 内置动画时长（仅在"无真实进度"时起兜底视觉作用，让快速加载也有动画）
const INTERNAL_DWELL = prefersReduced ? 800 : 2400

// easeOutCubic —— 进度前段快、末段稳
const easeOut = (t: number) => 1 - Math.pow(1 - t, 3)

const internalProgress = ref(0)

// 是否已收到过真实进度（realProgress > 0 即视为已接管）
const hasRealProgress = computed(() => realProgress.value > 0)

// 显示进度：真实进度一旦 > 0 即接管；无真实进度时回退到内置 rAF
const displayedProgress = computed(() => {
  if (hasRealProgress.value) {
    return realProgress.value
  }
  return internalProgress.value
})

let rafId = 0
let startTs = 0

function startTicking() {
  cancelAnimationFrame(rafId)
  startTs = 0
  internalProgress.value = 0
  const tick = (now: number) => {
    if (!startTs) startTs = now
    const elapsed = now - startTs
    const t = Math.min(1, elapsed / INTERNAL_DWELL)
    internalProgress.value = easeOut(t) * 100
    if (t < 1) {
      rafId = requestAnimationFrame(tick)
    } else {
      internalProgress.value = 100
    }
  }
  rafId = requestAnimationFrame(tick)
}

function stopTicking() {
  cancelAnimationFrame(rafId)
}

// 跟随 store.visible 启停内置 rAF
watch(
  visible,
  (v) => {
    if (v) {
      startTicking()
    } else {
      stopTicking()
    }
  },
  { immediate: true },
)

onBeforeUnmount(() => {
  stopTicking()
})
</script>

<style scoped>
/* ------------------------------------------------------------
   站点主色板：黑底白字（与 src/style.css 的 body 背景 #040404 完全一致）
   - 底色 #040404（与全站一致）
   - 次底 #070707
   - 文字 / 描边 / 圆环 / 指针 / 进度条均走纯白/灰阶透明
   - 线条档位基于 0.96 近白透明派生，保证黑白灰三色克制
   该样式与原 Loading.vue 一字不差，仅为 overlay 化（fixed inset 0 z 50）。
------------------------------------------------------------ */
.loading-page {
  /* —— 黑白灰三色 —— */
  --bg: #040404;
  --bg-2: #070707;
  --fg: #f4f4f4;
  --fg-soft: #d6d6d6;
  --fg-mute: #8c8c8c;
  --fg-faint: #4a4a4a;

  /* —— 线条档位 —— */
  --line: rgba(244, 244, 244, 0.1);
  --line-mid: rgba(244, 244, 244, 0.22);
  --line-strong: rgba(244, 244, 244, 0.45);

  /* —— 缓动 / 时长 —— */
  --ease-enter: cubic-bezier(0.2, 0.8, 0.2, 1);
  --ease-exit: cubic-bezier(0.4, 0, 1, 1);
  --d-base: 600ms; /* 渐显入场基础时长（用于子元素错峰） */
  --d-hero: 900ms;

  position: fixed;
  inset: 0;
  z-index: 50;
  display: grid;
  place-items: center;
  overflow: hidden;
  background: var(--bg);
  color: var(--fg);
  font-family: var(--font-ui);
  will-change: opacity, filter, transform;
}

/* 极淡网格纱幕 */
.grid-veil {
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(var(--line) 1px, transparent 1px),
    linear-gradient(90deg, var(--line) 1px, transparent 1px);
  background-size: 56px 56px;
  -webkit-mask-image: radial-gradient(circle at center, #000 0%, transparent 70%);
  mask-image: radial-gradient(circle at center, #000 0%, transparent 70%);
  pointer-events: none;
}

.stage {
  position: relative;
  z-index: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: clamp(20px, 4vh, 32px);
  padding: 24px;
  width: min(90vw, 440px);
}

.rings {
  position: relative;
  width: clamp(140px, 32vw, 196px);
  aspect-ratio: 1;
  display: grid;
  place-items: center;
  animation: rings-in var(--d-hero) var(--ease-enter) both;
}
@keyframes rings-in {
  from {
    opacity: 0;
    transform: scale(0.92);
  }
  to {
    opacity: 1;
    transform: scale(1);
  }
}

.ring {
  position: absolute;
  border-radius: 50%;
  border: 0.5px solid var(--line-strong);
  will-change: transform;
}
.ring--outer {
  inset: 0;
  border-style: solid;
  border-width: 0.5px;
  border-color: var(--line-strong) var(--line) var(--line) var(--line);
  animation: spin 2.6s linear infinite;
}
.ring--mid {
  inset: 14%;
  border-style: dashed;
  border-width: 0.5px;
  border-color: var(--line-mid) transparent transparent transparent;
  animation: spin-rev 1.9s linear infinite;
}
.ring--inner {
  inset: 30%;
  border-style: solid;
  border-width: 0.5px;
  border-color: var(--fg-mute) var(--line) var(--line) var(--line);
  animation: spin 1.4s linear infinite;
}

.tickmark {
  position: absolute;
  width: 1px;
  height: 36%;
  background: var(--fg);
  top: 8%;
  left: 50%;
  transform: translateX(-50%);
  transform-origin: 50% 100%;
  animation: tick 2.2s var(--ease-enter) infinite;
}
@keyframes tick {
  0%,
  100% {
    transform: translateX(-50%) rotate(0deg);
    opacity: 0.45;
  }
  50% {
    transform: translateX(-50%) rotate(180deg);
    opacity: 0.95;
  }
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
@keyframes spin-rev {
  to {
    transform: rotate(-360deg);
  }
}

.brand {
  text-align: center;
  animation: fade-up var(--d-base) var(--ease-enter) 120ms both;
}
.brand-mark {
  font-size: clamp(22px, 4.8vw, 28px);
  font-weight: 400;
  letter-spacing: 0.32em;
  color: var(--fg);
}
.brand-name {
  margin-top: 6px;
  font-size: clamp(10px, 2.2vw, 11px);
  font-weight: 500;
  letter-spacing: 0.36em;
  text-transform: uppercase;
  color: var(--fg-mute);
}

.status-line {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  width: 100%;
  border-top: 0.5px solid var(--line);
  border-bottom: 0.5px solid var(--line);
  padding: 10px 0;
  animation: fade-up var(--d-base) var(--ease-enter) 220ms both;
}
.status-text {
  font-size: clamp(10px, 2.2vw, 11px);
  font-weight: 500;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  color: var(--fg-soft);
}
.status-pct {
  font-size: clamp(10px, 2.2vw, 11px);
  font-weight: 500;
  letter-spacing: 0.08em;
  color: var(--fg);
  font-variant-numeric: tabular-nums;
  min-width: 32px;
  text-align: right;
}

.track {
  position: relative;
  width: 100%;
  height: 1px;
  background: var(--line);
  overflow: hidden;
  animation: fade-up var(--d-base) var(--ease-enter) 320ms both;
}
.track-fill {
  position: absolute;
  inset: 0;
  transform-origin: left center;
  transform: scaleX(0);
  background: var(--fg);
  /* 真实进度切换时也能柔和过渡，避免硬切 */
  transition: transform 240ms var(--ease-enter);
}

@keyframes fade-up {
  from {
    opacity: 0;
    transform: translateY(6px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

/* 渐显入场：纯透明度过渡，600ms 自然流畅 */
.overlay-enter-active {
  transition: opacity 600ms var(--ease-enter);
}
.overlay-enter-from {
  opacity: 0;
}

/* 渐隐退场：透明度 + 模糊 + 轻微位移 */
.overlay-leave-active {
  transition:
    opacity 620ms var(--ease-exit),
    filter 620ms var(--ease-exit),
    transform 620ms var(--ease-exit);
}
.overlay-leave-to {
  opacity: 0;
  filter: blur(6px);
  transform: translateY(-4px);
}

@media (prefers-reduced-motion: reduce) {
  .loading-page,
  .rings,
  .brand,
  .status-line,
  .track {
    animation: none;
  }
  .ring,
  .tickmark {
    animation: none !important;
  }
  .ring--outer {
    border-color: var(--fg) transparent transparent transparent;
  }
  .overlay-enter-active,
  .overlay-leave-active {
    transition: opacity 200ms linear;
  }
  .overlay-leave-to {
    filter: none;
    transform: none;
  }
}
</style>
