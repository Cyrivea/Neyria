import { createRouter, createWebHistory } from 'vue-router'
import { ElMessage } from '@/utils/toast'
import { TOKEN_KEY } from '@/constants'
import { isTokenExpired } from '@/utils/jwt'
import { useUserStore } from '@/stores/user'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/',
      name: 'Intro',
      component: () => import('@/views/intro/Intro.vue'),
    },
    {
      path: '/home',
      name: 'Home',
      component: () => import('@/views/home/Home.vue'),
    },
    {
      path: '/login',
      name: 'Login',
      component: () => import('@/views/login/Login.vue'),
    },
    {
      path: '/workstation',
      name: 'Workstation',
      component: () => import('@/views/workstation/Workstation.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/billing',
      name: 'Billing',
      component: () => import('@/views/billing/BillingView.vue'),
      meta: { requiresAuth: true },
    },
  ],
})

router.beforeEach((to, _, next) => {
  const token = localStorage.getItem(TOKEN_KEY)

  // 受保护页面：未登录或 token 已过期 -> 去 /login。
  // 本地校验 exp 让过期在进入页面前就被感知，而不是等第一个请求 401 才整页弹走
  if (to.meta.requiresAuth) {
    if (!token) {
      next('/login')
      return
    }
    if (isTokenExpired(token)) {
      useUserStore().logout()
      ElMessage.warning('登录已过期，请重新登录')
      next('/login')
      return
    }
  }

  // 已登录访问 /login：直接放行到登录页，由登录页/调用方决定是否触发覆盖层
  next()
})

export default router
