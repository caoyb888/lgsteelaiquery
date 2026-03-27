import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { loginAPI, logoutAPI } from '@/api/auth'
import type { LoginRequest, TokenResponse, UserRole } from '@/types'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string>(localStorage.getItem('access_token') ?? '')
  const userId = ref<string>(localStorage.getItem('user_id') ?? '')
  const username = ref<string>(localStorage.getItem('username') ?? '')
  const displayName = ref<string>(localStorage.getItem('display_name') ?? '')
  const role = ref<UserRole | ''>(localStorage.getItem('user_role') as UserRole ?? '')

  const isLoggedIn = computed(() => !!token.value)

  async function login(credentials: LoginRequest): Promise<void> {
    const data: TokenResponse = await loginAPI(credentials)
    token.value = data.access_token
    userId.value = data.user_id
    username.value = data.username
    displayName.value = data.display_name
    role.value = data.role as UserRole
    localStorage.setItem('access_token', data.access_token)
    localStorage.setItem('user_id', data.user_id)
    localStorage.setItem('username', data.username)
    localStorage.setItem('display_name', data.display_name)
    localStorage.setItem('user_role', data.role)
  }

  async function logout(): Promise<void> {
    try {
      await logoutAPI()
    } finally {
      token.value = ''
      userId.value = ''
      username.value = ''
      displayName.value = ''
      role.value = ''
      localStorage.removeItem('access_token')
      localStorage.removeItem('user_id')
      localStorage.removeItem('username')
      localStorage.removeItem('display_name')
      localStorage.removeItem('user_role')
    }
  }

  function hasRole(...roles: UserRole[]): boolean {
    return roles.includes(role.value as UserRole)
  }

  return { token, userId, username, displayName, role, isLoggedIn, login, logout, hasRole }
})
