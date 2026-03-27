import request from '@/api/request'
import type { ApiResponse, LoginRequest, TokenResponse } from '@/types'

export async function loginAPI(data: LoginRequest): Promise<TokenResponse> {
  const resp = await request.post<ApiResponse<TokenResponse>>('/v1/auth/login', data)
  return resp.data.data!
}

export async function logoutAPI(): Promise<void> {
  await request.post('/v1/auth/logout')
}
