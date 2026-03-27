/**
 * Axios 封装层
 *
 * 规范：
 * - 所有 API 调用必须通过此模块，禁止在组件中直接使用 axios
 * - 统一注入 Authorization Token
 * - 统一处理 401（跳转登录）、500（全局提示）
 */
import axios, { type AxiosInstance, type AxiosResponse } from 'axios'
import { ElMessage } from 'element-plus'
import type { ApiResponse } from '@/types'

const request: AxiosInstance = axios.create({
  baseURL: '/api',
  timeout: 60_000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// ---- 请求拦截器：注入 Token ----
request.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('access_token')
    if (token) {
      config.headers.Authorization = `Bearer ${token}`
    }
    return config
  },
  (error) => Promise.reject(error),
)

// 是否为登录接口（登录页的 401 不应触发页面跳转，应交由页面自己提示）
function isLoginEndpoint(url?: string): boolean {
  return !!url && url.includes('/auth/login')
}

// ---- 响应拦截器：统一错误处理 ----
request.interceptors.response.use(
  (response: AxiosResponse<ApiResponse<unknown>>) => {
    const { data } = response
    if (data.code !== 0) {
      // 未认证且不在登录页 → 跳转登录
      if (data.code === 4001 && !isLoginEndpoint(response.config.url)) {
        localStorage.removeItem('access_token')
        window.location.href = '/login'
        return Promise.reject(new Error(data.message))
      }
      // 其他业务错误透传给调用方（调用方负责 ElMessage 提示）
      return Promise.reject(Object.assign(new Error(data.message), { code: data.code }))
    }
    return response
  },
  (error) => {
    const status: number | undefined = error.response?.status
    const url: string | undefined = error.config?.url

    if (status === 401) {
      if (isLoginEndpoint(url)) {
        // 登录接口 401 = 用户名或密码错误，交给 LoginView 显示提示
        const msg = error.response?.data?.detail || error.response?.data?.message || '用户名或密码错误'
        return Promise.reject(new Error(msg))
      }
      // 其他接口 401 = token 过期，跳转登录
      localStorage.removeItem('access_token')
      ElMessage.error('登录已过期，请重新登录')
      setTimeout(() => { window.location.href = '/login' }, 1500)
      return Promise.reject(new Error('登录已过期'))
    }

    if (status === 403) {
      ElMessage.error(error.response?.data?.detail || '无权限执行此操作')
      return Promise.reject(new Error('无权限'))
    }

    if (status === 504) {
      ElMessage.error('查询超时，请简化查询条件后重试')
      return Promise.reject(new Error('查询超时'))
    }

    if (status && status >= 500) {
      ElMessage.error('服务器内部错误，请联系管理员')
      return Promise.reject(new Error('服务器错误'))
    }

    if (!error.response) {
      // 网络中断 / 请求超时
      if (error.code === 'ECONNABORTED') {
        ElMessage.error('请求超时，请检查网络后重试')
      } else {
        ElMessage.error('网络连接失败，请检查网络后重试')
      }
      return Promise.reject(new Error('网络错误'))
    }

    return Promise.reject(error)
  },
)

export default request
