<template>
  <el-container class="page-layout">
    <!-- 顶部导航 -->
    <el-header class="page-header">
      <div class="header-left">
        <el-button link @click="router.push('/chat')">
          <el-icon><ArrowLeft /></el-icon> 返回问数
        </el-button>
        <span class="page-title">管理后台</span>
      </div>
    </el-header>

    <el-main class="page-main">
      <el-tabs v-model="activeTab">
        <!-- ─── 统计概览 ─── -->
        <el-tab-pane label="统计概览" name="stats">
          <div class="stats-grid">
            <!-- 准确率卡片 -->
            <el-card class="stat-card">
              <template #header>
                <span>SQL 准确率（近30天）</span>
                <el-button link size="small" @click="loadAccuracyStats">
                  <el-icon><Refresh /></el-icon>
                </el-button>
              </template>
              <el-skeleton v-if="statsLoading" :rows="3" animated />
              <template v-else-if="accuracyStats">
                <div class="big-number">
                  {{ accuracyStats.sql_success_rate_pct != null
                    ? accuracyStats.sql_success_rate_pct + '%'
                    : '--' }}
                </div>
                <el-descriptions :column="2" size="small" border>
                  <el-descriptions-item label="总查询">{{ accuracyStats.total_queries }}</el-descriptions-item>
                  <el-descriptions-item label="成功">{{ accuracyStats.success_count }}</el-descriptions-item>
                  <el-descriptions-item label="失败">{{ accuracyStats.failed_count }}</el-descriptions-item>
                  <el-descriptions-item label="拦截">{{ accuracyStats.blocked_count }}</el-descriptions-item>
                  <el-descriptions-item label="点赞">{{ accuracyStats.thumbs_up }}</el-descriptions-item>
                  <el-descriptions-item label="满意度">
                    {{ accuracyStats.satisfaction_pct != null
                      ? accuracyStats.satisfaction_pct + '%'
                      : '--' }}
                  </el-descriptions-item>
                </el-descriptions>
              </template>
            </el-card>

            <!-- 使用量卡片 -->
            <el-card class="stat-card">
              <template #header>
                <span>使用量统计（近7天）</span>
                <el-button link size="small" @click="loadUsageStats">
                  <el-icon><Refresh /></el-icon>
                </el-button>
              </template>
              <el-skeleton v-if="usageLoading" :rows="3" animated />
              <template v-else-if="usageStats">
                <div class="big-number">{{ usageStats.total_queries }}</div>
                <div class="big-label">次查询</div>
                <el-descriptions :column="2" size="small" border>
                  <el-descriptions-item label="活跃用户">{{ usageStats.active_users }}</el-descriptions-item>
                  <el-descriptions-item label="平均响应">
                    {{ usageStats.avg_execution_ms.toFixed(0) }}ms
                  </el-descriptions-item>
                  <el-descriptions-item label="Prompt Token">
                    {{ usageStats.total_prompt_tokens.toLocaleString() }}
                  </el-descriptions-item>
                  <el-descriptions-item label="Completion Token">
                    {{ usageStats.total_completion_tokens.toLocaleString() }}
                  </el-descriptions-item>
                </el-descriptions>
              </template>
            </el-card>
          </div>
        </el-tab-pane>

        <!-- ─── 审计日志 ─── -->
        <el-tab-pane label="审计日志" name="audit">
          <div class="filter-bar">
            <el-select
              v-model="auditFilter.log_status"
              placeholder="状态"
              clearable
              style="width: 110px"
            >
              <el-option label="成功" value="success" />
              <el-option label="失败" value="failed" />
              <el-option label="拦截" value="blocked" />
            </el-select>
            <el-button type="primary" size="small" @click="loadAuditLogs">查询</el-button>
          </div>

          <el-table
            v-loading="auditLoading"
            :data="auditLogs"
            border
            stripe
            size="small"
            style="width: 100%"
          >
            <el-table-column prop="question" label="问题" min-width="200" show-overflow-tooltip />
            <el-table-column prop="generated_sql" label="SQL" min-width="200" show-overflow-tooltip />
            <el-table-column prop="status" label="状态" width="80">
              <template #default="{ row }">
                <el-tag
                  :type="row.status === 'success' ? 'success' : row.status === 'blocked' ? 'danger' : 'warning'"
                  size="small"
                >
                  {{ ({'success': '成功', 'failed': '失败', 'blocked': '拦截'} as Record<string, string>)[row.status] ?? row.status }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="result_row_count" label="行数" width="70" align="right" />
            <el-table-column prop="execution_ms" label="耗时(ms)" width="90" align="right" />
            <el-table-column label="反馈" width="70" align="center">
              <template #default="{ row }">
                <el-icon v-if="row.feedback === 1" color="#67C23A"><Pointer /></el-icon>
                <el-icon v-else-if="row.feedback === -1" color="#F56C6C"><SwitchButton /></el-icon>
                <span v-else style="color: #c0c4cc">—</span>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="时间" width="150">
              <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
            </el-table-column>
          </el-table>

          <div class="pagination-row">
            <el-pagination
              v-model:current-page="auditPage"
              :page-size="20"
              :total="auditTotal"
              layout="prev, pager, next, total"
              @current-change="loadAuditLogs"
            />
          </div>
        </el-tab-pane>

        <!-- ─── 用户管理 ─── -->
        <el-tab-pane label="用户管理" name="users">
          <div class="filter-bar">
            <el-button type="primary" size="small" @click="showCreateUser = true">新增用户</el-button>
            <el-button size="small" @click="loadUsers">
              <el-icon><Refresh /></el-icon>
            </el-button>
          </div>

          <el-table
            v-loading="usersLoading"
            :data="users"
            border
            stripe
            size="small"
            style="width: 100%"
          >
            <el-table-column prop="username" label="用户名" width="130" />
            <el-table-column prop="display_name" label="显示名" width="120" />
            <el-table-column prop="email" label="邮箱" min-width="160" />
            <el-table-column prop="role" label="角色" width="150">
              <template #default="{ row }">
                <el-select
                  :model-value="row.role"
                  size="small"
                  style="width: 140px"
                  @change="(val: string) => handleRoleChange(row, val)"
                >
                  <el-option label="管理员" value="admin" />
                  <el-option label="数据管理员" value="data_manager" />
                  <el-option label="分析师" value="analyst" />
                  <el-option label="财务用户" value="finance_user" />
                  <el-option label="销售用户" value="sales_user" />
                  <el-option label="生产用户" value="production_user" />
                  <el-option label="采购用户" value="procurement_user" />
                </el-select>
              </template>
            </el-table-column>
            <el-table-column prop="is_active" label="状态" width="80">
              <template #default="{ row }">
                <el-tag :type="row.is_active ? 'success' : 'info'" size="small">
                  {{ row.is_active ? '正常' : '禁用' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="创建时间" width="150">
              <template #default="{ row }">{{ formatDate(row.created_at) }}</template>
            </el-table-column>
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </el-main>

    <!-- 新增用户对话框 -->
    <el-dialog v-model="showCreateUser" title="新增用户" width="480px" :close-on-click-modal="false">
      <el-form :model="newUserForm" label-width="90px">
        <el-form-item label="用户名" required>
          <el-input v-model="newUserForm.username" placeholder="登录用户名" />
        </el-form-item>
        <el-form-item label="显示名" required>
          <el-input v-model="newUserForm.display_name" placeholder="姓名" />
        </el-form-item>
        <el-form-item label="初始密码" required>
          <el-input v-model="newUserForm.password" type="password" show-password />
        </el-form-item>
        <el-form-item label="邮箱">
          <el-input v-model="newUserForm.email" placeholder="可选" />
        </el-form-item>
        <el-form-item label="角色" required>
          <el-select v-model="newUserForm.role" style="width: 100%">
            <el-option label="管理员" value="admin" />
            <el-option label="数据管理员" value="data_manager" />
            <el-option label="分析师" value="analyst" />
            <el-option label="财务用户" value="finance_user" />
            <el-option label="销售用户" value="sales_user" />
            <el-option label="生产用户" value="production_user" />
            <el-option label="采购用户" value="procurement_user" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showCreateUser = false">取消</el-button>
        <el-button type="primary" :loading="creatingUser" @click="handleCreateUser">创建</el-button>
      </template>
    </el-dialog>
  </el-container>
</template>

<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ArrowLeft, Pointer, Refresh, SwitchButton } from '@element-plus/icons-vue'

import {
  createUserAPI,
  getAccuracyStatsAPI,
  getAuditLogsAPI,
  getUsageStatsAPI,
  listUsersAPI,
  updateUserRoleAPI,
  type AccuracyStats,
  type AuditLogItem,
  type UsageStats,
  type UserItem,
} from '@/api/admin'

const router = useRouter()

const activeTab = ref('stats')

// ─── 统计 ───────────────────────────────────────────────────────────────────

const accuracyStats = ref<AccuracyStats | null>(null)
const usageStats = ref<UsageStats | null>(null)
const statsLoading = ref(false)
const usageLoading = ref(false)

async function loadAccuracyStats() {
  statsLoading.value = true
  try {
    accuracyStats.value = await getAccuracyStatsAPI()
  } catch {
    ElMessage.error('加载准确率统计失败')
  } finally {
    statsLoading.value = false
  }
}

async function loadUsageStats() {
  usageLoading.value = true
  try {
    usageStats.value = await getUsageStatsAPI()
  } catch {
    ElMessage.error('加载使用量统计失败')
  } finally {
    usageLoading.value = false
  }
}

// ─── 审计日志 ────────────────────────────────────────────────────────────────

const auditLogs = ref<AuditLogItem[]>([])
const auditTotal = ref(0)
const auditPage = ref(1)
const auditLoading = ref(false)
const auditFilter = ref<{ log_status: string }>({ log_status: '' })

async function loadAuditLogs() {
  auditLoading.value = true
  try {
    const result = await getAuditLogsAPI({
      page: auditPage.value,
      page_size: 20,
      log_status: auditFilter.value.log_status || undefined,
    })
    auditLogs.value = result.items
    auditTotal.value = result.total
  } catch {
    ElMessage.error('加载审计日志失败')
  } finally {
    auditLoading.value = false
  }
}

// ─── 用户管理 ────────────────────────────────────────────────────────────────

const users = ref<UserItem[]>([])
const usersLoading = ref(false)
const showCreateUser = ref(false)
const creatingUser = ref(false)
const newUserForm = ref({
  username: '',
  display_name: '',
  password: '',
  email: '',
  role: 'analyst',
})

async function loadUsers() {
  usersLoading.value = true
  try {
    users.value = await listUsersAPI()
  } catch {
    ElMessage.error('加载用户列表失败')
  } finally {
    usersLoading.value = false
  }
}

async function handleRoleChange(row: UserItem, newRole: string) {
  try {
    await updateUserRoleAPI(row.id, newRole)
    row.role = newRole
    ElMessage.success('角色修改成功')
  } catch {
    ElMessage.error('角色修改失败')
  }
}

async function handleCreateUser() {
  if (!newUserForm.value.username || !newUserForm.value.password || !newUserForm.value.role) {
    ElMessage.warning('请填写必填字段')
    return
  }
  creatingUser.value = true
  try {
    await createUserAPI({
      username: newUserForm.value.username,
      display_name: newUserForm.value.display_name,
      password: newUserForm.value.password,
      role: newUserForm.value.role,
      email: newUserForm.value.email || undefined,
    })
    ElMessage.success('用户创建成功')
    showCreateUser.value = false
    newUserForm.value = { username: '', display_name: '', password: '', email: '', role: 'analyst' }
    await loadUsers()
  } catch (err: unknown) {
    ElMessage.error((err as Error).message || '创建失败')
  } finally {
    creatingUser.value = false
  }
}

// ─── 工具 ────────────────────────────────────────────────────────────────────

function formatDate(iso: string): string {
  return iso ? iso.replace('T', ' ').slice(0, 16) : ''
}

// ─── 生命周期 ────────────────────────────────────────────────────────────────

watch(activeTab, (tab) => {
  if (tab === 'stats') {
    loadAccuracyStats()
    loadUsageStats()
  } else if (tab === 'audit') {
    loadAuditLogs()
  } else if (tab === 'users') {
    loadUsers()
  }
})

onMounted(() => {
  loadAccuracyStats()
  loadUsageStats()
})
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

.stats-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: 16px;
}

.stat-card {
  min-height: 200px;
}

.stat-card :deep(.el-card__header) {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.big-number {
  font-size: 36px;
  font-weight: 700;
  color: #409EFF;
  line-height: 1.2;
  margin-bottom: 4px;
}

.big-label {
  font-size: 13px;
  color: #909399;
  margin-bottom: 12px;
}

.filter-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
}

.pagination-row {
  display: flex;
  justify-content: flex-end;
  margin-top: 12px;
}
</style>
