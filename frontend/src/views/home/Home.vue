<template>
  <div class="home-shell">
    <header class="home-nav" :class="{ scrolled: isScrolled }">
      <div class="shell-frame nav-frame">
        <router-link to="/home" class="nav-brand">Neyria</router-link>

        <nav class="nav-desktop">
          <a href="#features">Capabilities</a>
          <a href="#workflow">Workflow</a>
          <a href="#about">About</a>
        </nav>

        <div class="nav-status">
          <span>AI native creative platform</span>
        </div>

        <div class="nav-actions">
          <a v-if="!userStore.isLoggedIn" class="nav-link-button" @click="openAuthEntry">Sign in</a>
          <template v-else>
            <a class="nav-link-button" @click="openAuthEntry">Open Workstation</a>
            <a class="nav-link-button" @click="handleSignOut">Sign out</a>
          </template>
          <button class="nav-menu-button" type="button">Menu</button>
        </div>

        <button
          class="nav-mobile-toggle"
          type="button"
          :aria-expanded="menuOpen"
          aria-label="Toggle menu"
          @click="menuOpen = !menuOpen"
        >
          <span></span>
          <span></span>
        </button>
      </div>

      <Transition name="nav-panel">
        <div v-if="menuOpen" class="nav-mobile-panel">
          <a href="#features" @click="menuOpen = false">Capabilities</a>
          <a href="#workflow" @click="menuOpen = false">Workflow</a>
          <a href="#about" @click="menuOpen = false">About</a>
          <a class="nav-mobile-link" @click="openAuthEntry">Open Workstation</a>
          <a v-if="userStore.isLoggedIn" class="nav-mobile-link" @click="handleSignOut">Sign out</a>
        </div>
      </Transition>
    </header>

    <main class="home-main">
      <section class="hero-section">
        <div class="shell-frame hero-frame">
          <div class="hero-copy">
            <div class="hero-eyebrow">AI native creation workstation</div>
            <h1 class="hero-title">
              Build the creative
              <span>infrastructure</span>
              for AI-native music.
            </h1>
            <p class="hero-description">
              Neyria combines composition, lyrics, visual generation, PV workflow, and assistant
              orchestration inside one continuous surface instead of scattered tools.
            </p>

            <div class="hero-actions">
              <a v-ripple class="hero-primary" @click="openAuthEntry">Open Workstation</a>
              <a v-ripple href="#features" class="hero-secondary">Explore Capabilities</a>
              <a v-ripple href="#workflow" class="hero-secondary">See Workflow</a>
            </div>

            <div class="hero-bottom-note">Scroll to explore</div>
          </div>

          <div class="hero-stage">
            <div class="hero-side-panel">
              <div class="side-kicker">Organize creative flows by</div>
              <div class="side-filter-list">
                <button
                  v-for="filter in sideFilters"
                  :key="filter"
                  class="side-filter"
                  type="button"
                >
                  {{ filter }}
                </button>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section id="features" class="content-section">
        <div class="shell-frame section-frame">
          <div v-reveal class="section-intro">
            <div class="section-kicker">Capabilities</div>
            <h2 class="section-title">One surface for modular creation.</h2>
            <p class="section-copy">
              The front page now uses larger full-width sections, tighter hierarchy, and more
              controlled motion so the product feels like a real platform instead of a small
              centered landing block.
            </p>
          </div>

          <div class="feature-grid">
            <article
              v-for="(feature, index) in features"
              :key="feature.title"
              v-reveal
              class="feature-card"
              :style="{ '--i': index }"
            >
              <div class="feature-index">{{ feature.index }}</div>
              <h3>{{ feature.title }}</h3>
              <p>{{ feature.desc }}</p>
              <div class="feature-tags">
                <span v-for="tag in feature.tags" :key="tag">{{ tag }}</span>
              </div>
            </article>
          </div>
        </div>
      </section>

      <section id="workflow" class="content-section workflow-section">
        <div class="shell-frame workflow-frame">
          <div v-reveal class="section-intro section-intro--split">
            <div>
              <div class="section-kicker">Workflow</div>
              <h2 class="section-title">
                Structured like a production flow, not a stack of widgets.
              </h2>
            </div>
            <p class="section-copy">
              The visual rhythm now follows large narrative blocks: hero, capability matrix,
              workflow, and conversion. That is the main reason the reference site feels more
              premium.
            </p>
          </div>

          <div class="workflow-rail">
            <div
              v-for="(step, index) in workflowSteps"
              :key="step.title"
              v-reveal
              class="workflow-step"
              :style="{ '--i': index }"
            >
              <div class="workflow-step-number">{{ step.number }}</div>
              <div class="workflow-step-title">{{ step.title }}</div>
              <div class="workflow-step-copy">{{ step.copy }}</div>
            </div>
          </div>
        </div>
      </section>

      <section id="about" class="content-section about-section">
        <div class="shell-frame about-frame">
          <div v-reveal class="about-panel">
            <div class="section-kicker">Design Direction</div>
            <h2 class="section-title">Full-screen, calmer motion, stronger depth.</h2>
            <p class="section-copy">
              This version removes the cramped 1200px landing feel and pushes the page into a wider,
              denser, more immersive layout with quieter interaction rules.
            </p>
          </div>

          <div class="about-list">
            <div v-reveal class="about-item">
              <div class="about-item-title">Fill</div>
              <div class="about-item-copy">
                Hero and major sections now occupy the browser as a stage instead of a centered
                card.
              </div>
            </div>
            <div v-reveal class="about-item" :style="{ '--i': 1 }">
              <div class="about-item-title">Motion</div>
              <div class="about-item-copy">
                Buttons and panels use restrained lift and contrast changes instead of noisy glow
                effects.
              </div>
            </div>
            <div v-reveal class="about-item" :style="{ '--i': 2 }">
              <div class="about-item-title">Depth</div>
              <div class="about-item-copy">
                Background animation stays behind a stable content layer so the page feels alive
                without becoming messy.
              </div>
            </div>
          </div>
        </div>
      </section>
    </main>

    <footer class="home-footer">
      <div v-reveal class="shell-frame footer-frame">
        <div class="footer-brand">Neyria</div>
        <div class="footer-copy">
          AI native workstation for music, visuals, and agent-driven creation.
        </div>
        <a class="footer-button" @click="openAuthEntry">Enter</a>
      </div>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from '@/utils/toast'
import { useUserStore } from '@/stores/user'
import { useLoadingStore } from '@/stores/loading'
import { TOKEN_KEY } from '@/constants'

const router = useRouter()
const userStore = useUserStore()
const loadingStore = useLoadingStore()

const menuOpen = ref(false)
const isScrolled = ref(false)

// 已登录用户点击 "Sign in / Open Workstation" 入口：
// 直接拉起覆盖层，再切到工作站，不让用户再过一次登录表单。
function openAuthEntry() {
  if (userStore.token || localStorage.getItem(TOKEN_KEY)) {
    loadingStore.show('auto')
    router.push('/workstation')
    return
  }
  router.push('/login')
}

function handleSignOut() {
  userStore.logout()
  menuOpen.value = false
  ElMessage.success('已退出登录')
}

const sideFilters = ['Composer', 'Lyrics', 'Visual', 'Video', 'Workflow']

const features = [
  {
    index: '01',
    title: 'Music generation',
    desc: 'Compose structures, refine arrangement logic, and keep music tasks tied to the current project state.',
    tags: ['Composition', 'Arrangement', 'Project Context'],
  },
  {
    index: '02',
    title: 'Lyrics alignment',
    desc: 'Generate lyric drafts and map text toward rhythm and structure instead of treating words as isolated output.',
    tags: ['MIDI Mapping', 'Syllable Logic', 'Draft Refinement'],
  },
  {
    index: '03',
    title: 'Visual pipeline',
    desc: 'Extend concept art, image generation, and PV preparation into the same creation session.',
    tags: ['Image', 'PV', 'Cutout'],
  },
  {
    index: '04',
    title: 'Agent orchestration',
    desc: 'Use the right assistant for the right stage rather than forcing every task through one generic chat box.',
    tags: ['Routing', 'Context', 'Modular Tools'],
  },
]

const workflowSteps = [
  {
    number: '01',
    title: 'Capture the idea',
    copy: 'Turn a raw concept into a structured project entry point with enough context for downstream agents.',
  },
  {
    number: '02',
    title: 'Route to specialist modules',
    copy: 'Send the same intent into composition, lyrics, image, or video logic depending on the actual task.',
  },
  {
    number: '03',
    title: 'Keep outputs synchronized',
    copy: 'Store results inside the workstation instead of scattering them across disconnected pages and tools.',
  },
]

function handleScroll() {
  isScrolled.value = window.scrollY > 24
}

onMounted(() => {
  handleScroll()
  window.addEventListener('scroll', handleScroll, { passive: true })
})

onUnmounted(() => {
  window.removeEventListener('scroll', handleScroll)
})
</script>

<style scoped>
.home-shell {
  position: relative;
  width: 100%;
  min-height: 100vh;
  color: #f4f4f4;
  background: transparent;
  overflow-x: hidden;
}

.home-shell::before {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  background:
    radial-gradient(circle at 18% 24%, rgba(255, 255, 255, 0.03), transparent 26%),
    radial-gradient(circle at 76% 48%, rgba(255, 255, 255, 0.025), transparent 28%),
    linear-gradient(180deg, rgba(0, 0, 0, 0.18), rgba(0, 0, 0, 0.26));
  z-index: 0;
}

.shell-frame {
  width: 100%;
  max-width: 100%;
  margin: 0 auto;
  padding-left: clamp(28px, 4vw, 44px);
  padding-right: clamp(28px, 4vw, 44px);
}

.home-nav {
  position: fixed;
  inset: 0 0 auto;
  z-index: 40;
  padding-top: 10px;
  transition:
    padding 220ms ease,
    background-color 220ms ease,
    backdrop-filter 220ms ease;
}

.home-nav.scrolled {
  padding-top: 0;
  background: rgba(6, 6, 6, 0.82);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
}

.nav-frame {
  min-height: 62px;
  display: flex;
  align-items: center;
  gap: 22px;
}

.nav-brand {
  color: #f8f8f8;
  text-decoration: none;
  font-size: 16px;
  font-weight: 700;
  letter-spacing: -0.04em;
}

.nav-desktop {
  display: flex;
  align-items: center;
  gap: 22px;
  margin-left: 22px;
}

.nav-desktop a,
.nav-link-button {
  color: rgba(244, 244, 244, 0.58);
  text-decoration: none;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  transition:
    color 180ms ease,
    transform 180ms ease;
}

.nav-desktop a {
  position: relative;
}

.nav-desktop a::after {
  content: '';
  position: absolute;
  left: 0;
  bottom: -5px;
  width: 100%;
  height: 1px;
  background: rgba(244, 244, 244, 0.72);
  transform: scaleX(0);
  transform-origin: right center;
  transition: transform 0.38s var(--ease-glide);
}

.nav-desktop a:hover::after {
  transform: scaleX(1);
  transform-origin: left center;
}

.nav-desktop a:hover,
.nav-link-button:hover {
  color: #ffffff;
  transform: translateY(-1px);
}

.nav-status {
  margin-left: auto;
  color: rgba(244, 244, 244, 0.42);
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.12em;
}

.nav-actions {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-left: 18px;
}

.hero-primary,
.footer-button {
  position: relative;
  overflow: hidden;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 42px;
  padding: 0 18px;
  border-radius: 999px;
  background: #f1f1f1;
  color: #090909;
  text-decoration: none;
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  border: 1px solid #f1f1f1;
  transition:
    transform 200ms ease,
    background-color 200ms ease,
    border-color 200ms ease;
}

/* 悬停高光扫过（逆时针一次性的细腻扫光，无色块感） */
.hero-primary::after,
.footer-button::after {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: linear-gradient(
    105deg,
    transparent 32%,
    rgba(255, 255, 255, 0.65) 50%,
    transparent 68%
  );
  transform: translateX(-130%);
}

.hero-primary:hover::after,
.footer-button:hover::after {
  transform: translateX(130%);
  transition: transform 0.7s var(--ease-soft);
}

.nav-primary-button:hover,
.hero-primary:hover,
.footer-button:hover {
  transform: translateY(-2px);
  background: #ffffff;
  border-color: #ffffff;
}

.nav-menu-button {
  min-height: 24px;
  padding: 0;
  background: transparent;
  border: none;
  color: #f5f5f5;
  font-size: 13px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  cursor: pointer;
}

.nav-mobile-toggle {
  display: none;
  width: 42px;
  height: 42px;
  margin-left: auto;
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.04);
  cursor: pointer;
}

.nav-mobile-toggle span {
  display: block;
  width: 16px;
  height: 1px;
  margin: 6px auto;
  background: rgba(255, 255, 255, 0.88);
}

.nav-mobile-panel {
  margin: 0 clamp(24px, 4vw, 56px) 0;
  padding: 12px 0 20px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.nav-mobile-panel a {
  color: rgba(244, 244, 244, 0.76);
  text-decoration: none;
  font-size: 15px;
  padding: 10px 0;
}

.nav-panel-enter-active,
.nav-panel-leave-active {
  transition:
    opacity 200ms ease,
    transform 200ms ease;
}

.nav-panel-enter-from,
.nav-panel-leave-to {
  opacity: 0;
  transform: translateY(-10px);
}

.home-main {
  position: relative;
  z-index: 1;
}

.hero-section {
  min-height: 100svh;
  padding-top: 78px;
  padding-bottom: 0;
  display: flex;
  align-items: stretch;
}

.hero-frame {
  display: grid;
  grid-template-columns: minmax(0, 0.88fr) minmax(0, 1.12fr);
  gap: 0;
  align-items: stretch;
  min-height: calc(100svh - 78px);
}

.hero-copy {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  justify-content: center;
  min-width: 0;
  padding-top: 48px;
  padding-bottom: 54px;
}

.hero-eyebrow,
.section-kicker,
.stage-kicker {
  display: inline-flex;
  align-items: center;
  min-height: 20px;
  padding: 0;
  border: none;
  border-radius: 0;
  color: var(--accent-mist);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.24em;
  text-transform: uppercase;
  background: transparent;
}

.hero-title {
  margin-top: 18px;
  max-width: 9.5ch;
  font-size: clamp(72px, 9vw, 112px);
  line-height: 0.9;
  letter-spacing: -0.085em;
  font-weight: 600;
  color: #fafafa;
}

/* 关键单词用衬线斜体做编辑感点缀（仅一字，不成色块） */
.hero-title span {
  display: block;
  font-family: var(--font-serif);
  font-style: italic;
  font-weight: 400;
  font-size: 1.04em;
  letter-spacing: -0.015em;
  color: #e9e9e9;
  padding-bottom: 0.06em;
}

.hero-description,
.section-copy,
.stage-panel-copy,
.stage-card-copy,
.workflow-step-copy,
.about-item-copy {
  color: rgba(255, 255, 255, 0.62);
  line-height: 1.75;
  font-size: 16px;
}

.hero-description {
  max-width: 480px;
  margin-top: 26px;
  font-size: 16px;
  color: rgba(255, 255, 255, 0.54);
}

.hero-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 30px;
}

.hero-secondary {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-height: 42px;
  padding: 0 18px;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.1);
  background: rgba(255, 255, 255, 0.02);
  color: rgba(255, 255, 255, 0.82);
  text-decoration: none;
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  transition:
    transform 200ms ease,
    border-color 200ms ease,
    background-color 200ms ease;
}

.hero-secondary:hover {
  transform: translateY(-2px);
  border-color: rgba(255, 255, 255, 0.24);
  background: rgba(255, 255, 255, 0.06);
}

.feature-card,
.workflow-step,
.about-item {
  border: 1px solid rgba(255, 255, 255, 0.08);
  background: rgba(255, 255, 255, 0.028);
  backdrop-filter: blur(10px);
}

.hero-bottom-note {
  margin-top: 48px;
  color: rgba(255, 255, 255, 0.34);
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.24em;
}

/* 英雄区入场：文案错峰上浮 + 轻微去模糊；侧栏稍晚入场 */
.hero-eyebrow,
.hero-title,
.hero-description,
.hero-actions,
.hero-bottom-note,
.hero-side-panel {
  animation: hero-rise 0.95s var(--ease-glide) both;
}
.hero-eyebrow {
  animation-delay: 80ms;
}
.hero-title {
  animation-delay: 160ms;
}
.hero-description {
  animation-delay: 280ms;
}
.hero-actions {
  animation-delay: 380ms;
}
.hero-side-panel {
  animation-delay: 320ms;
}
.hero-bottom-note {
  animation:
    hero-rise 0.95s var(--ease-glide) 500ms both,
    hero-note-float 2.8s ease-in-out 1.6s infinite;
}

@keyframes hero-rise {
  from {
    opacity: 0;
    transform: translateY(24px);
    filter: blur(10px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
    filter: blur(0);
  }
}

@keyframes hero-note-float {
  0%,
  100% {
    transform: translateY(0);
    opacity: 0.7;
  }
  50% {
    transform: translateY(4px);
    opacity: 1;
  }
}

.hero-stage {
  min-width: 0;
  display: flex;
  justify-content: flex-end;
  align-items: flex-end;
  padding-bottom: 48px;
}

.hero-side-panel {
  width: 220px;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 12px;
}

.side-kicker {
  text-align: right;
  color: rgba(255, 255, 255, 0.34);
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.18em;
}

.side-filter-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  align-items: flex-end;
}

.side-filter {
  min-height: 28px;
  padding: 0 12px;
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  color: rgba(255, 255, 255, 0.72);
  font-size: 12px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  background: rgba(255, 255, 255, 0.02);
  cursor: pointer;
  transition:
    transform 180ms ease,
    background-color 180ms ease,
    border-color 180ms ease;
}

.side-filter:hover {
  transform: translateX(-3px);
  background: rgba(255, 255, 255, 0.06);
  border-color: rgba(255, 255, 255, 0.16);
}

.content-section {
  position: relative;
  z-index: 1;
  padding: 34px 0;
}

.section-frame,
.workflow-frame,
.about-frame {
  padding-top: 30px;
  padding-bottom: 30px;
}

.section-intro {
  max-width: 780px;
}

.section-intro--split {
  max-width: none;
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(0, 0.95fr);
  gap: 28px;
  align-items: end;
}

.section-title {
  margin-top: 18px;
  font-size: clamp(34px, 4vw, 62px);
  line-height: 0.98;
  letter-spacing: -0.06em;
  color: #fafafa;
  font-weight: 650;
}

.section-copy {
  margin-top: 18px;
  max-width: 680px;
}

.feature-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
  margin-top: 34px;
}

.feature-card {
  padding: 28px 24px 24px;
  border-radius: 28px;
  min-height: 320px;
  transition:
    transform 220ms ease,
    border-color 220ms ease,
    background-color 220ms ease;
}

.feature-index {
  font-size: 13px;
  color: rgba(255, 255, 255, 0.42);
  letter-spacing: 0.14em;
  text-transform: uppercase;
  transition: color 220ms ease;
}

/* 功能序号用低饱和点缀色（仅文字，不做色块） */
.feature-card:nth-child(1) .feature-index {
  color: var(--accent-mist-strong);
}
.feature-card:nth-child(2) .feature-index {
  color: var(--accent-mauve);
}
.feature-card:nth-child(3) .feature-index {
  color: var(--accent-sage);
}
.feature-card:nth-child(4) .feature-index {
  color: var(--accent-sand);
}

.feature-card:hover {
  transform: translateY(-4px);
  border-color: rgba(255, 255, 255, 0.16);
  background-color: rgba(255, 255, 255, 0.045);
}

.feature-card h3 {
  margin-top: 20px;
  font-size: 24px;
  line-height: 1.08;
  letter-spacing: -0.045em;
  font-weight: 600;
  color: #f7f7f7;
}

.feature-card p {
  margin-top: 18px;
  color: rgba(255, 255, 255, 0.62);
  line-height: 1.72;
  font-size: 15px;
}

.feature-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 26px;
}

.feature-tags span {
  min-height: 30px;
  padding: 0 12px;
  border-radius: 999px;
  border: 1px solid rgba(255, 255, 255, 0.08);
  display: inline-flex;
  align-items: center;
  color: rgba(255, 255, 255, 0.64);
  font-size: 12px;
}

.workflow-frame {
  border-top: 1px solid rgba(255, 255, 255, 0.07);
  border-bottom: 1px solid rgba(255, 255, 255, 0.07);
}

.workflow-rail {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  margin-top: 34px;
}

.workflow-step {
  min-height: 260px;
  padding: 24px;
  border-radius: 28px;
  transition:
    transform 220ms ease,
    border-color 220ms ease,
    background-color 220ms ease;
}

.workflow-step-number {
  font-size: 13px;
  color: rgba(255, 255, 255, 0.44);
  letter-spacing: 0.14em;
  text-transform: uppercase;
  transition: color 220ms ease;
}

.workflow-step:nth-child(1) .workflow-step-number {
  color: var(--accent-mist-strong);
}
.workflow-step:nth-child(2) .workflow-step-number {
  color: var(--accent-mauve);
}
.workflow-step:nth-child(3) .workflow-step-number {
  color: var(--accent-sage);
}

.workflow-step:hover {
  transform: translateY(-4px);
  border-color: rgba(255, 255, 255, 0.16);
  background-color: rgba(255, 255, 255, 0.045);
}

.workflow-step-title {
  margin-top: 18px;
  font-size: 24px;
  line-height: 1.08;
  letter-spacing: -0.045em;
  color: #f8f8f8;
  font-weight: 600;
}

.workflow-step-copy {
  margin-top: 16px;
  font-size: 15px;
}

.about-frame {
  display: grid;
  grid-template-columns: minmax(0, 1.05fr) minmax(0, 0.95fr);
  gap: 18px;
}

.about-panel {
  padding: 32px;
  min-height: 420px;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  border-radius: 32px;
}

.about-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.about-item {
  min-height: 129px;
  padding: 24px;
  border-radius: 24px;
  transition:
    transform 220ms ease,
    border-color 220ms ease,
    background-color 220ms ease;
}

.about-item-title {
  font-size: 24px;
  line-height: 1.08;
  letter-spacing: -0.04em;
  color: #f7f7f7;
  font-weight: 600;
}

.about-item:hover {
  transform: translateY(-3px);
  border-color: rgba(255, 255, 255, 0.15);
  background-color: rgba(255, 255, 255, 0.04);
}

.about-item-copy {
  margin-top: 14px;
  font-size: 15px;
}

.home-footer {
  position: relative;
  z-index: 1;
  padding: 22px 0 40px;
}

.footer-frame {
  min-height: 84px;
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: 20px;
  align-items: center;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
  padding-top: 24px;
}

.footer-brand {
  color: #f6f6f6;
  font-family: var(--font-serif);
  font-size: 19px;
  font-weight: 400;
  letter-spacing: 0.02em;
}

.footer-copy {
  color: rgba(255, 255, 255, 0.52);
  line-height: 1.7;
  font-size: 14px;
}

@media (max-width: 1180px) {
  .hero-frame,
  .feature-grid,
  .workflow-rail,
  .about-frame,
  .section-intro--split,
  .footer-frame {
    grid-template-columns: 1fr;
  }

  .stage-shell,
  .hero-title,
  .section-copy {
    max-width: none;
  }

  .hero-section {
    padding-bottom: 28px;
  }

  .hero-stage {
    justify-content: flex-start;
    padding-top: 12px;
  }
}

@media (max-width: 900px) {
  .nav-desktop,
  .nav-actions,
  .nav-status {
    display: none;
  }

  .nav-mobile-toggle {
    display: inline-block;
  }

  .hero-section {
    padding-top: 82px;
  }

  .hero-frame {
    min-height: auto;
    padding-top: 18px;
    padding-bottom: 18px;
  }

  .hero-title {
    font-size: clamp(54px, 15vw, 78px);
  }
}

@media (max-width: 640px) {
  .shell-frame {
    padding-left: 18px;
    padding-right: 18px;
  }

  .nav-frame {
    min-height: 68px;
  }

  .hero-description,
  .section-copy,
  .workflow-step-copy,
  .about-item-copy {
    font-size: 15px;
  }

  .about-panel {
    padding: 20px;
  }

  .feature-card,
  .workflow-step,
  .about-item {
    border-radius: 22px;
  }
}
</style>
