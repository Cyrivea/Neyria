<template>
  <div class="login-page">
    <!-- 全局 toast -->
    <transition name="toast">
      <div v-if="toast.show" class="toast">{{ toast.msg }}</div>
    </transition>

    <div class="login-card">
      <!-- 品牌 -->
      <router-link to="/home" class="brand">Neyria</router-link>

      <!-- Tab 切换 -->
      <div class="tabs">
        <button :class="['tab', mode === 'login' && 'active']" @click="mode = 'login'">
          Sign in
        </button>
        <button :class="['tab', mode === 'register' && 'active']" @click="mode = 'register'">
          Register
        </button>
      </div>

      <!-- 登录表单 -->
      <transition name="form" mode="out-in">
        <div v-if="mode === 'login'" key="login" class="form">
          <div class="head-area">
            <div class="head-kicker">AI native creation workstation</div>
            <h1 class="head-title">Welcome back.</h1>
          </div>

          <div class="field">
            <label>Email or Phone</label>
            <input
              v-model="loginForm.account"
              type="text"
              placeholder="Enter email or phone number"
              @keyup.enter="submitLogin"
            />
          </div>
          <div class="field">
            <label>Password</label>
            <div class="input-wrap">
              <input
                v-model="loginForm.password"
                :type="showPwd ? 'text' : 'password'"
                placeholder="Enter password"
                @keyup.enter="submitLogin"
              />
              <span class="eye" @click="showPwd = !showPwd"><EyeIcon :open="showPwd" /></span>
            </div>
          </div>

          <div class="remember-row">
            <label class="checkbox-label">
              <input v-model="loginForm.remember" type="checkbox" />
              <span>Remember me for 30 days</span>
            </label>
            <a href="#" class="forgot">Forgot password?</a>
          </div>

          <p v-if="loginError" class="error-msg">{{ loginError }}</p>

          <button
            v-ripple
            class="btn-primary"
            :class="{ loading: loginLoading }"
            @click="submitLogin"
          >
            {{ loginLoading ? 'Signing in…' : 'Sign in' }}
          </button>

          <p class="switch-tip">
            Don't have an account? <span @click="mode = 'register'">Register now</span>
          </p>
        </div>

        <!-- 注册表单 -->
        <div v-else key="register" class="form">
          <div class="head-area">
            <div class="head-kicker">AI native creation workstation</div>
            <h1 class="head-title">Create account.</h1>
          </div>

          <div class="field">
            <label>Email or Phone</label>
            <div class="input-wrap">
              <input
                v-model="regForm.account"
                type="text"
                placeholder="Enter email or phone number"
              />
              <button class="send-code" :disabled="codeCooldown > 0" @click="sendCode">
                {{ codeCooldown > 0 ? `${codeCooldown}s` : 'Send code' }}
              </button>
            </div>
          </div>

          <div class="field">
            <label>Verification Code</label>
            <input
              v-model="regForm.code"
              type="text"
              placeholder="Enter verification code"
              maxlength="6"
            />
          </div>

          <div class="field">
            <label>Password</label>
            <div class="input-wrap">
              <input
                v-model="regForm.password"
                :type="showRegPwd ? 'text' : 'password'"
                placeholder="Create a password"
              />
              <span class="eye" @click="showRegPwd = !showRegPwd"
                ><EyeIcon :open="showRegPwd"
              /></span>
            </div>
          </div>

          <div class="field">
            <label>Confirm Password</label>
            <div class="input-wrap">
              <input
                v-model="regForm.confirmPassword"
                :type="showRegPwd2 ? 'text' : 'password'"
                placeholder="Confirm your password"
                :class="{ 'input-error': pwdMismatch }"
              />
              <span class="eye" @click="showRegPwd2 = !showRegPwd2"
                ><EyeIcon :open="showRegPwd2"
              /></span>
            </div>
            <p v-if="pwdMismatch" class="field-error">Passwords do not match</p>
          </div>

          <p v-if="regError" class="error-msg">{{ regError }}</p>

          <button
            v-ripple
            class="btn-primary"
            :class="{ loading: regLoading }"
            @click="submitRegister"
          >
            {{ regLoading ? 'Creating account…' : 'Create account' }}
          </button>

          <p class="switch-tip">
            Already have an account? <span @click="mode = 'login'">Sign in</span>
          </p>
        </div>
      </transition>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, reactive, h } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { useLoadingStore } from '@/stores/loading'
import { login, register, sendCode as sendCodeApi } from '@/serve/auth'
import { isAxiosError } from 'axios'

// 描边眼睛图标（对齐首页无 emoji 风格）
const EyeIcon = (props: { open: boolean }) =>
  h(
    'svg',
    {
      width: 18,
      height: 18,
      viewBox: '0 0 24 24',
      fill: 'none',
      stroke: 'currentColor',
      'stroke-width': 1.6,
      'stroke-linecap': 'round',
      'stroke-linejoin': 'round',
    },
    props.open
      ? [
          h('path', { d: 'M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z' }),
          h('circle', { cx: 12, cy: 12, r: 3 }),
        ]
      : [
          h('path', { d: 'M3 3l18 18' }),
          h('path', {
            d: 'M10.6 5.1A10.9 10.9 0 0 1 12 5c6.5 0 10 7 10 7a17.6 17.6 0 0 1-3.4 4.3',
          }),
          h('path', { d: 'M6.5 6.6A17.4 17.4 0 0 0 2 12s3.5 7 10 7a10.7 10.7 0 0 0 4.6-1' }),
          h('path', { d: 'M9.9 9.9a3 3 0 0 0 4.2 4.2' }),
        ],
  )

const router = useRouter()
const userStore = useUserStore()
const loadingStore = useLoadingStore()

const mode = ref<'login' | 'register'>('login')
const showPwd = ref(false)
const showRegPwd = ref(false)
const showRegPwd2 = ref(false)
const loginLoading = ref(false)
const regLoading = ref(false)
const loginError = ref('')
const regError = ref('')
const codeCooldown = ref(0)

const toast = reactive({ show: false, msg: '' })

const loginForm = reactive({ account: '', password: '', remember: false })
const regForm = reactive({ account: '', code: '', password: '', confirmPassword: '' })

const pwdMismatch = computed(
  () => regForm.confirmPassword.length > 0 && regForm.password !== regForm.confirmPassword,
)

function showToast(msg: string) {
  toast.msg = msg
  toast.show = true
  setTimeout(() => {
    toast.show = false
  }, 2500)
}

function startCooldown() {
  codeCooldown.value = 60
  const t = setInterval(() => {
    codeCooldown.value--
    if (codeCooldown.value <= 0) clearInterval(t)
  }, 1000)
}

// 从 axios 错误中提取后端 detail：字符串直接用；FastAPI 422 校验错误是数组，取第一条 msg
function apiErrorDetail(e: unknown): string | undefined {
  if (!isAxiosError(e)) return undefined
  const detail = e.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail[0]?.msg || 'Invalid input'
  return undefined
}

async function sendCode() {
  if (!regForm.account) {
    regError.value = 'Please enter email or phone first'
    return
  }
  if (!regForm.account.includes('@')) {
    regError.value = 'Please enter a valid email address'
    return
  }
  try {
    await sendCodeApi(regForm.account)
    startCooldown()
    regError.value = ''
  } catch (e) {
    regError.value = apiErrorDetail(e) || 'Failed to send code'
  }
}

async function submitLogin() {
  if (!loginForm.account || !loginForm.password) {
    loginError.value = 'Please fill in all fields'
    return
  }
  loginLoading.value = true
  loginError.value = ''
  try {
    const res = await login({ username: loginForm.account, password: loginForm.password })
    // 1) 先落库 token / username
    userStore.setUser(res.token, res.username)
    // 2) 写 remember 标记（用于下次免登录场景判断）
    if (loginForm.remember) {
      localStorage.setItem('remember', '1')
    } else {
      localStorage.removeItem('remember')
    }
    // 3) 通过全局 loading store 触发覆盖层，登录态落库后再切路由
    loadingStore.show('login')
    router.push('/workstation')
  } catch (e) {
    loginError.value = apiErrorDetail(e) || 'Login failed'
  } finally {
    loginLoading.value = false
  }
}

async function submitRegister() {
  if (!regForm.account || !regForm.code || !regForm.password || !regForm.confirmPassword) {
    regError.value = 'Please fill in all fields'
    return
  }
  if (regForm.password.length < 8) {
    regError.value = 'Password must be at least 8 characters'
    return
  }
  if (new TextEncoder().encode(regForm.password).length > 72) {
    regError.value = 'Password is too long'
    return
  }
  if (pwdMismatch.value) return
  regLoading.value = true
  regError.value = ''
  try {
    const res = await register({
      username: regForm.account,
      password: regForm.password,
      code: regForm.code,
    })
    // 1) 注册成功立即落 token（与其他场景一致，避免漏动画）
    userStore.setUser(res.token, res.username)
    localStorage.removeItem('remember')
    // 2) toast 走当前页，覆盖层通过 store 触发；二者不抢同一渲染层
    showToast('Registration successful')
    // 3) 触发全局覆盖层，再切路由
    loadingStore.show('register')
    router.push('/workstation')
  } catch (e) {
    regError.value = apiErrorDetail(e) || 'Registration failed'
  } finally {
    regLoading.value = false
  }
}
</script>

<style scoped>
.login-page {
  position: relative;
  z-index: 1;
  width: 100%;
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
  font-family: var(--font-ui);
}

/* Toast */
.toast {
  position: fixed;
  top: 24px;
  left: 50%;
  transform: translateX(-50%);
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.16);
  backdrop-filter: blur(12px);
  color: #fafafa;
  padding: 12px 26px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  z-index: 999;
  white-space: nowrap;
}
.toast-enter-active {
  animation: toastIn 0.4s cubic-bezier(0.34, 1.56, 0.64, 1);
}
.toast-leave-active {
  animation: toastOut 0.35s ease forwards;
}
@keyframes toastIn {
  from {
    opacity: 0;
    transform: translateX(-50%) translateY(-20px);
  }
  to {
    opacity: 1;
    transform: translateX(-50%) translateY(0);
  }
}
@keyframes toastOut {
  from {
    opacity: 1;
    transform: translateX(-50%) translateY(0);
  }
  to {
    opacity: 0;
    transform: translateX(-50%) translateY(-20px);
  }
}

/* Card */
.login-card {
  width: 100%;
  max-width: 428px;
  background:
    linear-gradient(180deg, rgba(255, 255, 255, 0.04), rgba(255, 255, 255, 0.02)),
    rgba(255, 255, 255, 0.02);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 28px;
  padding: 34px 34px 30px;
  backdrop-filter: blur(10px);
  animation: card-in 0.72s var(--ease-glide) both;
}

@keyframes card-in {
  from {
    opacity: 0;
    transform: translateY(18px) scale(0.99);
    filter: blur(8px);
  }
  to {
    opacity: 1;
    transform: none;
    filter: blur(0);
  }
}

/* Brand */
.brand {
  display: inline-block;
  color: #f8f8f8;
  text-decoration: none;
  font-size: 15px;
  font-weight: 700;
  letter-spacing: -0.04em;
  transition: opacity 180ms ease;
}
.brand:hover {
  opacity: 0.7;
}

/* Tabs */
.tabs {
  display: flex;
  gap: 4px;
  background: rgba(255, 255, 255, 0.03);
  border: 1px solid rgba(255, 255, 255, 0.06);
  border-radius: 999px;
  padding: 4px;
  margin: 24px 0 30px;
}
.tab {
  flex: 1;
  padding: 9px;
  border: none;
  background: transparent;
  color: rgba(244, 244, 244, 0.5);
  font-size: 11px;
  font-family: inherit;
  font-weight: 600;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  cursor: pointer;
  border-radius: 999px;
  transition:
    color 180ms ease,
    background-color 180ms ease;
}
.tab:hover {
  color: rgba(255, 255, 255, 0.82);
}
.tab.active {
  background: #f1f1f1;
  color: #090909;
}

/* Heading */
.head-area {
  margin-bottom: 4px;
}
.head-kicker {
  color: var(--accent-mist);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.22em;
  text-transform: uppercase;
}
.head-title {
  margin-top: 10px;
  font-family: var(--font-serif);
  font-size: 38px;
  line-height: 1;
  letter-spacing: -0.01em;
  font-weight: 400;
  color: #fafafa;
}

/* Form */
.form {
  display: flex;
  flex-direction: column;
  gap: 15px;
}
.field {
  display: flex;
  flex-direction: column;
  gap: 7px;
}
.field label {
  font-size: 10px;
  font-weight: 600;
  color: rgba(244, 244, 244, 0.44);
  letter-spacing: 0.14em;
  text-transform: uppercase;
  transition: color 180ms ease;
}
.field:focus-within label {
  color: rgba(244, 244, 244, 0.74);
}
.field input {
  width: 100%;
  padding: 12px 14px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 12px;
  color: #fafafa;
  font-size: 14px;
  font-family: inherit;
  outline: none;
  transition:
    border-color 180ms ease,
    background-color 180ms ease;
  box-sizing: border-box;
}
.field input:focus {
  border-color: rgba(181, 181, 181, 0.45);
  background: rgba(255, 255, 255, 0.05);
  box-shadow: 0 0 0 3px rgba(181, 181, 181, 0.1);
}
.field input.input-error {
  border-color: rgba(156, 156, 156, 0.6) !important;
}
.field input::placeholder {
  color: rgba(255, 255, 255, 0.26);
}

.input-wrap {
  position: relative;
  display: flex;
  align-items: center;
}
.input-wrap input {
  flex: 1;
  padding-right: 46px;
}
.eye {
  position: absolute;
  right: 13px;
  display: inline-flex;
  align-items: center;
  color: rgba(255, 255, 255, 0.4);
  cursor: pointer;
  transition: color 180ms ease;
  user-select: none;
}
.eye:hover {
  color: rgba(255, 255, 255, 0.85);
}

.send-code {
  position: absolute;
  right: 7px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.14);
  color: rgba(255, 255, 255, 0.82);
  font-size: 10px;
  font-family: inherit;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  padding: 7px 12px;
  border-radius: 999px;
  cursor: pointer;
  transition:
    background-color 180ms ease,
    border-color 180ms ease,
    color 180ms ease;
  white-space: nowrap;
}
.send-code:hover:not(:disabled) {
  background: rgba(255, 255, 255, 0.1);
  border-color: rgba(255, 255, 255, 0.24);
  color: #fff;
}
.send-code:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.remember-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: -2px;
}
.checkbox-label {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  font-size: 13px;
  color: rgba(255, 255, 255, 0.54);
}
.checkbox-label input[type='checkbox'] {
  accent-color: #f1f1f1;
  width: 14px;
  height: 14px;
}
.forgot {
  font-size: 13px;
  color: rgba(255, 255, 255, 0.48);
  text-decoration: none;
  transition: color 180ms ease;
}
.forgot:hover {
  color: #fff;
}

.field-error {
  font-size: 12px;
  color: #9c9c9c;
  margin-top: 1px;
  animation: error-pop 0.28s var(--ease-soft) both;
}
.error-msg {
  font-size: 13px;
  color: #9c9c9c;
  text-align: center;
  margin: -2px 0;
  animation: error-pop 0.28s var(--ease-soft) both;
}

@keyframes error-pop {
  from {
    opacity: 0;
    transform: translateY(-4px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

.btn-primary {
  width: 100%;
  min-height: 46px;
  padding: 0 18px;
  background: #f1f1f1;
  color: #090909;
  border: 1px solid #f1f1f1;
  border-radius: 999px;
  font-size: 12px;
  font-family: inherit;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  cursor: pointer;
  transition:
    transform 200ms ease,
    background-color 200ms ease,
    border-color 200ms ease;
  margin-top: 6px;
  position: relative;
  overflow: hidden;
}
.btn-primary::after {
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
.btn-primary:hover::after {
  transform: translateX(130%);
  transition: transform 0.7s var(--ease-soft);
}
.btn-primary:hover {
  transform: translateY(-2px);
  background: #ffffff;
  border-color: #ffffff;
}
.btn-primary:active:not(.loading) {
  transform: translateY(-1px) scale(0.985);
  transition-duration: 90ms;
}
.btn-primary.loading {
  opacity: 0.6;
  cursor: not-allowed;
  transform: none;
}

.switch-tip {
  text-align: center;
  font-size: 13px;
  color: rgba(255, 255, 255, 0.44);
  margin-top: 2px;
}
.switch-tip span {
  color: #f4f4f4;
  cursor: pointer;
  padding-bottom: 1px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.22);
  transition:
    border-color 180ms ease,
    opacity 180ms ease;
}
.switch-tip span:hover {
  border-bottom-color: rgba(255, 255, 255, 0.6);
}

/* Form transition */
.form-enter-active {
  animation: formIn 0.35s ease;
}
.form-leave-active {
  animation: formOut 0.25s ease forwards;
}
@keyframes formIn {
  from {
    opacity: 0;
    transform: translateX(16px);
  }
  to {
    opacity: 1;
    transform: translateX(0);
  }
}
@keyframes formOut {
  from {
    opacity: 1;
    transform: translateX(0);
  }
  to {
    opacity: 0;
    transform: translateX(-16px);
  }
}

@media (max-width: 480px) {
  .login-card {
    padding: 26px 22px 24px;
    border-radius: 22px;
  }
  .head-title {
    font-size: 28px;
  }
}
</style>
