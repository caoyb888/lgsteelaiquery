<template>
  <el-container class="page-layout">
    <!-- 顶部导航 -->
    <el-header class="page-header">
      <div class="header-left">
        <el-button link @click="router.push('/chat')">
          <el-icon><ArrowLeft /></el-icon> 返回问数
        </el-button>
        <span class="page-title">数据源管理</span>
      </div>
      <el-button
        v-if="authStore.hasRole('admin', 'data_manager')"
        type="primary"
        @click="showUploadDialog = true"
      >
        <el-icon><Upload /></el-icon> 上传数据
      </el-button>
    </el-header>

    <el-main class="page-main">
      <!-- 过滤栏 -->
      <div class="filter-bar">
        <el-select
          v-model="filterDomain"
          placeholder="数据域"
          clearable
          style="width: 140px"
          @change="loadDatasources"
        >
          <el-option label="全部" value="" />
          <el-option label="财务" value="finance" />
          <el-option label="销售" value="sales" />
          <el-option label="生产" value="production" />
          <el-option label="采购" value="procurement" />
        </el-select>
        <el-button :loading="tableLoading" @click="loadDatasources">
          <el-icon><Refresh /></el-icon>
        </el-button>
      </div>

      <!-- 数据源列表 -->
      <el-table
        v-loading="tableLoading"
        :data="datasources"
        border
        stripe
        style="width: 100%"
      >
        <el-table-column prop="name" label="名称" min-width="200" show-overflow-tooltip />
        <el-table-column prop="domain" label="数据域" width="100">
          <template #default="{ row }">
            <el-tag :type="domainTagType(row.domain)" size="small">
              {{ domainLabel(row.domain) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="original_filename" label="文件名" min-width="180" show-overflow-tooltip />
        <el-table-column prop="data_date" label="数据截止日" width="120" />
        <el-table-column prop="total_rows" label="行数" width="80" align="right" />
        <el-table-column prop="status" label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="statusTagType(row.status, row.is_stale)" size="small">
              {{ statusLabel(row.status, row.is_stale) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="uploaded_by_name" label="上传者" width="100" />
        <el-table-column prop="created_at" label="上传时间" width="160">
          <template #default="{ row }">
            {{ formatDate(row.created_at) }}
          </template>
        </el-table-column>
        <el-table-column label="操作" width="100">
          <template #default="{ row }">
            <el-popconfirm
              v-if="authStore.hasRole('admin', 'data_manager')"
              title="确认删除该数据源？"
              confirm-button-text="删除"
              cancel-button-text="取消"
              confirm-button-type="danger"
              @confirm="handleDelete(row)"
            >
              <template #reference>
                <el-button link type="danger" size="small">删除</el-button>
              </template>
            </el-popconfirm>
          </template>
        </el-table-column>
      </el-table>
    </el-main>

    <!-- 上传对话框 -->
    <el-dialog
      v-model="showUploadDialog"
      title="上传数据文件"
      width="600px"
      :close-on-click-modal="false"
      @close="resetUpload"
    >
      <!-- 步骤 1：选择文件 -->
      <template v-if="uploadStep === 'select'">
        <el-form :model="uploadForm" label-width="100px" label-position="left">
          <el-form-item label="数据域" required>
            <el-select v-model="uploadForm.domain" placeholder="请选择数据域" style="width: 100%">
              <el-option label="财务" value="finance" />
              <el-option label="销售" value="sales" />
              <el-option label="生产" value="production" />
              <el-option label="采购" value="procurement" />
            </el-select>
          </el-form-item>
          <el-form-item label="数据截止日" required>
            <el-date-picker
              v-model="uploadForm.dataDate"
              type="date"
              placeholder="数据截止日期"
              format="YYYY-MM-DD"
              value-format="YYYY-MM-DD"
              style="width: 100%"
            />
          </el-form-item>
          <el-form-item label="更新模式">
            <el-radio-group v-model="uploadForm.updateMode">
              <el-radio value="replace">覆盖（清空旧数据）</el-radio>
              <el-radio value="append">追加</el-radio>
            </el-radio-group>
          </el-form-item>
          <el-form-item label="Excel 文件" required>
            <el-upload
              drag
              :auto-upload="false"
              :limit="1"
              accept=".xlsx,.xls,.csv"
              :on-change="onFileChange"
              :file-list="fileList"
            >
              <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
              <div class="el-upload__text">拖拽或 <em>点击选择文件</em></div>
              <template #tip>
                <div class="el-upload__tip">支持 .xlsx / .xls / .csv，最大 50MB</div>
              </template>
            </el-upload>
          </el-form-item>
        </el-form>
      </template>

      <!-- 步骤 2：字段映射确认 -->
      <template v-else-if="uploadStep === 'confirm'">
        <p class="step-hint">
          共识别 <strong>{{ previewData?.field_mappings?.length }}</strong> 个字段，
          置信度低于 70% 的字段需人工确认。
        </p>
        <el-table
          :data="previewData?.field_mappings ?? []"
          border
          size="small"
          max-height="380"
        >
          <el-table-column prop="raw_name" label="原始字段" width="120" />
          <el-table-column label="标准名" min-width="140">
            <template #default="{ row }">
              <el-input
                v-if="row.needs_confirm"
                v-model="row.std_name"
                size="small"
                placeholder="请填写标准名"
              />
              <span v-else>{{ row.std_name }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="display_name" label="展示名" width="120" />
          <el-table-column prop="field_type" label="类型" width="80" />
          <el-table-column prop="unit" label="单位" width="70" />
          <el-table-column label="置信度" width="90" align="right">
            <template #default="{ row }">
              <el-tag :type="row.confidence >= 0.7 ? 'success' : 'warning'" size="small">
                {{ (row.confidence * 100).toFixed(0) }}%
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
        <p class="step-hint" style="margin-top: 10px">
          数据共 <strong>{{ previewData?.total_rows }}</strong> 行
        </p>
      </template>

      <template #footer>
        <div style="display: flex; gap: 8px; justify-content: flex-end">
          <el-button @click="showUploadDialog = false">取消</el-button>
          <el-button
            v-if="uploadStep === 'select'"
            type="primary"
            :loading="uploading"
            :disabled="!uploadForm.domain || !uploadForm.dataDate || !selectedFile"
            @click="handleUpload"
          >
            上传并解析
          </el-button>
          <el-button
            v-else-if="uploadStep === 'confirm'"
            type="primary"
            :loading="confirming"
            @click="handleConfirm"
          >
            确认入库
          </el-button>
        </div>
      </template>
    </el-dialog>
  </el-container>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  ArrowLeft,
  Refresh,
  Upload,
  UploadFilled,
} from '@element-plus/icons-vue'
import type { UploadFile } from 'element-plus'

import {
  confirmMappingsAPI,
  deleteDatasourceAPI,
  listDatasourcesAPI,
  uploadDatasourceAPI,
} from '@/api/datasource'
import { useAuthStore } from '@/stores/auth'
import type { DataDomain, DatasourceListItem, FieldMappingPreview } from '@/types'

const router = useRouter()
const authStore = useAuthStore()

const datasources = ref<DatasourceListItem[]>([])
const tableLoading = ref(false)
const filterDomain = ref<DataDomain | ''>('')

// 上传相关
const showUploadDialog = ref(false)
const uploadStep = ref<'select' | 'confirm'>('select')
const uploading = ref(false)
const confirming = ref(false)
const selectedFile = ref<File | null>(null)
const fileList = ref<UploadFile[]>([])
const uploadId = ref('')
const previewData = ref<{ total_rows: number; field_mappings: FieldMappingPreview[] } | null>(null)

const uploadForm = ref<{
  domain: DataDomain | ''
  dataDate: string
  updateMode: 'replace' | 'append'
}>({
  domain: '',
  dataDate: '',
  updateMode: 'replace',
})

async function loadDatasources() {
  tableLoading.value = true
  try {
    datasources.value = await listDatasourcesAPI(
      filterDomain.value ? (filterDomain.value as DataDomain) : undefined,
    )
  } catch {
    ElMessage.error('加载数据源失败')
  } finally {
    tableLoading.value = false
  }
}

function onFileChange(file: UploadFile) {
  selectedFile.value = file.raw ?? null
}

async function handleUpload() {
  if (!selectedFile.value || !uploadForm.value.domain || !uploadForm.value.dataDate) return
  uploading.value = true
  try {
    const resp = await uploadDatasourceAPI(
      selectedFile.value,
      uploadForm.value.domain as DataDomain,
      uploadForm.value.dataDate,
      undefined,
      uploadForm.value.updateMode,
    )
    uploadId.value = resp.upload_id
    previewData.value = resp.preview
      ? { total_rows: resp.preview.total_rows, field_mappings: resp.preview.field_mappings }
      : null
    uploadStep.value = 'confirm'
  } catch (err: unknown) {
    ElMessage.error((err as Error).message || '上传失败')
  } finally {
    uploading.value = false
  }
}

async function handleConfirm() {
  confirming.value = true
  try {
    const needsConfirm =
      previewData.value?.field_mappings.filter((f) => f.needs_confirm) ?? []
    await confirmMappingsAPI(uploadId.value, needsConfirm)
    ElMessage.success('数据入库任务已提交，处理完成后状态将自动更新')
    showUploadDialog.value = false
    await loadDatasources()
  } catch (err: unknown) {
    ElMessage.error((err as Error).message || '确认失败')
  } finally {
    confirming.value = false
  }
}

async function handleDelete(row: DatasourceListItem) {
  try {
    await deleteDatasourceAPI(row.id)
    ElMessage.success('删除成功')
    await loadDatasources()
  } catch (err: unknown) {
    ElMessage.error((err as Error).message || '删除失败')
  }
}

function resetUpload() {
  uploadStep.value = 'select'
  uploadId.value = ''
  previewData.value = null
  selectedFile.value = null
  fileList.value = []
  uploadForm.value = { domain: '', dataDate: '', updateMode: 'replace' }
}

function domainLabel(domain: string): string {
  const map: Record<string, string> = {
    finance: '财务',
    sales: '销售',
    production: '生产',
    procurement: '采购',
    unknown: '未知',
  }
  return map[domain] ?? domain
}

function domainTagType(domain: string): '' | 'success' | 'warning' | 'info' | 'danger' {
  const map: Record<string, '' | 'success' | 'warning' | 'info' | 'danger'> = {
    finance: '',
    sales: 'success',
    production: 'warning',
    procurement: 'info',
  }
  return map[domain] ?? 'info'
}

function statusLabel(s: string, isStale: boolean): string {
  if (s === 'active' && isStale) return '数据过期'
  const map: Record<string, string> = {
    active: '正常',
    pending_confirm: '待确认',
    processing: '处理中',
    error: '错误',
    archived: '已归档',
  }
  return map[s] ?? s
}

function statusTagType(s: string, isStale: boolean): '' | 'success' | 'warning' | 'info' | 'danger' {
  if (s === 'active' && isStale) return 'warning'
  const map: Record<string, '' | 'success' | 'warning' | 'info' | 'danger'> = {
    active: 'success',
    pending_confirm: 'info',
    processing: '',
    error: 'danger',
    archived: 'info',
  }
  return map[s] ?? 'info'
}

function formatDate(iso: string): string {
  return iso ? iso.replace('T', ' ').slice(0, 16) : ''
}

onMounted(loadDatasources)
</script>

<style scoped>
.page-layout {
  height: 100vh;
  flex-direction: column;
}

.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #e4e7ed;
  background: #fff;
}

.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.page-title {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
}

.page-main {
  padding: 20px;
  overflow: auto;
}

.filter-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
}

.step-hint {
  color: #606266;
  font-size: 13px;
  margin-bottom: 12px;
}
</style>
