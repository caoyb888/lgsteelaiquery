/**
 * Axios 封装层
 *
 * 规范：
 * - 所有 API 调用必须通过此模块，禁止在组件中直接使用 axios
 * - 统一注入 Authorization Token
 * - 统一处理 401（跳转登录）、500（全局提示）
 */
import axios, { type AxiosInstance, type AxiosResponse } from 'axios'
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

// ---- 响应拦截器：统一错误处理 ----
request.interceptors.response.use(
  (response: AxiosResponse<ApiResponse<unknown>>) => {
    const { data } = response
    // 业务错误（code !== 0）
    if (data.code !== 0) {
      // 未认证，跳转登录
      if (data.code === 4001) {
        localStorage.removeItem('access_token')
        window.location.href = '/login'
        return Promise.reject(new Error(data.message))
      }
      // 其他业务错误，透传给调用方处理
      return Promise.reject(Object.assign(new Error(data.message), { code: data.code }))
    }
    return response
  },
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('access_token')
      window.location.href = '/login'
    }
    return Promise.reject(error)
  },
)

export default request
