/**
 * 全局类型定义，与后端 Schema 严格对齐
 */

// ---- 通用 ----

export interface ApiResponse<T> {
  code: number
  message: string
  data: T | null
  request_id: string
  timestamp: string
}

export interface PaginatedResponse<T> {
  items: T[]
  total: number
  page: number
  page_size: number
  total_pages: number
}

// ---- 认证 ----

export interface LoginRequest {
  username: string
  password: string
}

export interface TokenResponse {
  access_token: string
  token_type: string
  expires_in: number
  user_id: string
  username: string
  display_name: string
  role: string
}

export type UserRole =
  | 'admin'
  | 'data_manager'
  | 'analyst'
  | 'finance_user'
  | 'sales_user'
  | 'production_user'
  | 'procurement_user'

// ---- 对话查询 ----

export interface DataSourceInfo {
  datasource_id: string
  datasource_name: string
  data_date: string
  upload_time: string
}

export type DisplayType = 'single_value' | 'table' | 'bar_chart' | 'line_chart' | 'pie_chart'

export interface ChatQueryRequest {
  question: string
  conversation_id?: string
  datasource_ids?: string[]
}

export interface ChatQueryResponse {
  answer_text: string
  display_type: DisplayType
  chart_option?: Record<string, unknown>
  table_data?: Record<string, unknown>[]
  sql?: string
  data_sources: DataSourceInfo[]
  confidence?: number
  execution_ms?: number
  conversation_id?: string
}

export interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: string
  queryResponse?: ChatQueryResponse
}

// ---- 数据源管理 ----

export type DataDomain = 'finance' | 'sales' | 'production' | 'procurement'

export interface FieldMappingPreview {
  raw_name: string
  std_name: string
  display_name: string
  field_type: string
  unit?: string
  confidence: number
  needs_confirm: boolean
  mapping_source: string
}

export interface DatasourceUploadResponse {
  upload_id: string
  status: 'pending_confirm' | 'processing' | 'success' | 'error'
  preview?: {
    total_rows: number
    sheets: unknown[]
    field_mappings: FieldMappingPreview[]
  }
}

export interface DatasourceListItem {
  id: string
  name: string
  domain: DataDomain
  description?: string
  original_filename: string
  data_date: string
  status: string
  total_rows?: number
  uploaded_by_name?: string
  created_at: string
  is_stale: boolean
}
