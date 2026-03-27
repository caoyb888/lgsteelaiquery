import request from '@/api/request'
import type { ApiResponse } from '@/types'

export interface AccuracyStats {
  period_days: number
  total_queries: number
  success_count: number
  failed_count: number
  blocked_count: number
  sql_success_rate_pct: number | null
  with_feedback: number
  thumbs_up: number
  thumbs_down: number
  satisfaction_pct: number | null
}

export interface UsageStats {
  period_days: number
  total_queries: number
  active_users: number
  total_prompt_tokens: number
  total_completion_tokens: number
  avg_execution_ms: number
}

export interface AuditLogItem {
  id: string
  user_id: string
  question: string
  generated_sql: string | null
  status: string
  block_reason: string | null
  result_row_count: number | null
  execution_ms: number | null
  feedback: 1 | -1 | null
  created_at: string
}

export interface UserItem {
  id: string
  username: string
  display_name: string
  email: string | null
  role: string
  is_active: boolean
  created_at: string
}

export interface CreateUserPayload {
  username: string
  display_name: string
  password: string
  role: string
  email?: string
}

export async function getAccuracyStatsAPI(days = 30): Promise<AccuracyStats> {
  const resp = await request.get<ApiResponse<AccuracyStats>>('/v1/admin/stats/accuracy', {
    params: { days },
  })
  return resp.data.data!
}

export async function getUsageStatsAPI(days = 7): Promise<UsageStats> {
  const resp = await request.get<ApiResponse<UsageStats>>('/v1/admin/stats/usage', {
    params: { days },
  })
  return resp.data.data!
}

export async function getAuditLogsAPI(params: {
  page?: number
  page_size?: number
  log_status?: string
  user_id?: string
}): Promise<{ items: AuditLogItem[]; total: number; total_pages: number }> {
  const resp = await request.get<ApiResponse<{ items: AuditLogItem[]; total: number; total_pages: number }>>(
    '/v1/admin/audit/logs',
    { params },
  )
  return resp.data.data!
}

export async function listUsersAPI(): Promise<UserItem[]> {
  const resp = await request.get<ApiResponse<UserItem[]>>('/v1/admin/users')
  return resp.data.data ?? []
}

export async function createUserAPI(payload: CreateUserPayload): Promise<UserItem> {
  const resp = await request.post<ApiResponse<UserItem>>('/v1/admin/users', payload)
  return resp.data.data!
}

export async function updateUserRoleAPI(userId: string, role: string): Promise<void> {
  await request.patch(`/v1/admin/users/${userId}/role`, { role })
}
