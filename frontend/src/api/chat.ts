import request from '@/api/request'
import type { ApiResponse, ChatQueryRequest, ChatQueryResponse } from '@/types'

export async function queryChatAPI(data: ChatQueryRequest): Promise<ChatQueryResponse> {
  const resp = await request.post<ApiResponse<ChatQueryResponse>>('/v1/chat/query', data)
  return resp.data.data!
}

export async function submitFeedbackAPI(logId: string, feedback: 1 | -1): Promise<void> {
  await request.post(`/v1/chat/${logId}/feedback`, { feedback })
}

export async function getChatHistoryAPI(): Promise<unknown[]> {
  const resp = await request.get<ApiResponse<unknown[]>>('/v1/chat/history')
  return resp.data.data ?? []
}
