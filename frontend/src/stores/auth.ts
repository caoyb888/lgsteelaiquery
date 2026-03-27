import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { loginAPI, logoutAPI } from '@/api/auth'
import type { LoginRequest, TokenResponse, UserRole } from '@/types'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string>(localStorage.getItem('access_token') ?? '')
  const userId = ref<string>('')
  const username = ref<string>('')
  const displayName = ref<string>('')
  const role = ref<UserRole | ''>('')

  const isLoggedIn = computed(() => !!token.value)

  async function login(credentials: LoginRequest): Promise<void> {
    const data: TokenResponse = await loginAPI(credentials)
    token.value = data.access_token
    userId.value = data.user_id
    username.value = data.username
    displayName.value = data.display_name
    role.value = data.role as UserRole
    localStorage.setItem('access_token', data.access_token)
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
    }
  }

  function hasRole(...roles: UserRole[]): boolean {
    return roles.includes(role.value as UserRole)
  }

  return { token, userId, username, displayName, role, isLoggedIn, login, logout, hasRole }
})
