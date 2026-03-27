import request from '@/api/request'
import type {
  ApiResponse,
  DataDomain,
  DatasourceListItem,
  DatasourceUploadResponse,
} from '@/types'

export async function uploadDatasourceAPI(
  file: File,
  domain: DataDomain,
  dataDate: string,
  description?: string,
  updateMode: 'replace' | 'append' = 'replace',
): Promise<DatasourceUploadResponse> {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('domain', domain)
  formData.append('data_date', dataDate)
  formData.append('update_mode', updateMode)
  if (description) formData.append('description', description)

  const resp = await request.post<ApiResponse<DatasourceUploadResponse>>(
    '/v1/datasource/upload',
    formData,
    { headers: { 'Content-Type': 'multipart/form-data' } },
  )
  return resp.data.data!
}

export async function confirmMappingsAPI(
  uploadId: string,
  confirmedMappings: unknown[],
): Promise<void> {
  await request.post(`/v1/datasource/confirm/${uploadId}`, {
    confirmed_mappings: confirmedMappings,
  })
}

export async function listDatasourcesAPI(domain?: DataDomain): Promise<DatasourceListItem[]> {
  const params = domain ? { domain } : {}
  const resp = await request.get<ApiResponse<DatasourceListItem[]>>('/v1/datasource/list', {
    params,
  })
  return resp.data.data ?? []
}

export async function deleteDatasourceAPI(datasourceId: string): Promise<void> {
  await request.delete(`/v1/datasource/${datasourceId}`)
}
