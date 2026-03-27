<template>
  <el-container class="chat-layout">
    <!-- 侧边栏：历史对话入口 + 数据源快捷入口 -->
    <el-aside width="220px" class="sidebar">
      <div class="sidebar-header">
        <span class="logo-text">莱钢 AI 问数</span>
      </div>
      <el-button
        type="primary"
        plain
        class="new-chat-btn"
        @click="startNewConversation"
      >
        + 新对话
      </el-button>
      <el-divider />
      <div class="nav-item" @click="router.push('/datasource')">
        <el-icon><FolderOpened /></el-icon>
        <span>数据源管理</span>
      </div>
      <div
        v-if="authStore.hasRole('admin', 'data_manager')"
        class="nav-item"
        @click="router.push('/admin')"
      >
        <el-icon><Setting /></el-icon>
        <span>管理后台</span>
      </div>
      <div class="user-area">
        <el-avatar size="small" :style="{ background: '#409EFF' }">
          {{ authStore.displayName.charAt(0) }}
        </el-avatar>
        <span class="user-name">{{ authStore.displayName }}</span>
        <el-button link @click="handleLogout">退出</el-button>
      </div>
    </el-aside>

    <!-- 主聊天区域 -->
    <el-main class="chat-main">
      <!-- 消息列表 -->
      <div ref="messageListRef" class="message-list">
        <!-- 空状态引导 -->
        <div v-if="messages.length === 0" class="empty-guide">
          <el-icon size="56" color="#c0c4cc"><ChatLineRound /></el-icon>
          <p class="empty-title">您好，我是莱钢 AI 问数助手</p>
          <p class="empty-sub">请用自然语言提问，例如：</p>
          <div class="example-questions">
            <el-tag
              v-for="q in exampleQuestions"
              :key="q"
              class="example-tag"
              @click="quickSend(q)"
            >
              {{ q }}
            </el-tag>
          </div>
        </div>

        <!-- 消息气泡 -->
        <template v-for="msg in messages" :key="msg.id">
          <!-- 用户消息 -->
          <div v-if="msg.role === 'user'" class="message-row user-row">
            <div class="bubble user-bubble">{{ msg.content }}</div>
            <el-avatar size="small" :style="{ background: '#409EFF' }">
              {{ authStore.displayName.charAt(0) }}
            </el-avatar>
          </div>

          <!-- AI 回复 -->
          <div v-else class="message-row ai-row">
            <el-avatar size="small" :style="{ background: '#67C23A' }">AI</el-avatar>
            <div class="bubble ai-bubble">
              <!-- 加载中 -->
              <div v-if="msg.content === '__loading__'" class="loading-dots">
                <span /><span /><span />
              </div>
              <!-- 错误 -->
              <div v-else-if="msg.content === '__error__'" class="error-msg">
                <el-icon color="#F56C6C"><WarningFilled /></el-icon>
                查询出错，请稍后再试
              </div>
              <!-- 正常结果 -->
              <template v-else>
                <!-- 摘要文字 -->
                <p class="answer-summary">{{ msg.content }}</p>

                <!-- 单值展示 -->
                <div
                  v-if="msg.queryResponse?.display_type === 'single_value'"
                  class="single-value-card"
                >
                  <span class="single-value-num">
                    {{ getSingleValue(msg.queryResponse) }}
                  </span>
                </div>

                <!-- 表格展示 -->
                <el-table
                  v-else-if="msg.queryResponse?.display_type === 'table'"
                  :data="getTableRows(msg.queryResponse)"
                  border
                  stripe
                  size="small"
                  class="result-table"
                  max-height="360"
                >
                  <el-table-column
                    v-for="col in getTableCols(msg.queryResponse)"
                    :key="col"
                    :prop="col"
                    :label="col"
                    show-overflow-tooltip
                  />
                </el-table>

                <!-- 图表展示 -->
                <div
                  v-else-if="msg.queryResponse?.chart_option"
                  :id="`chart-${msg.id}`"
                  class="chart-container"
                />

                <!-- 数据来源标注 -->
                <div
                  v-if="msg.queryResponse?.data_sources?.length"
                  class="source-info"
                >
                  <el-icon size="12"><InfoFilled /></el-icon>
                  数据来源：{{ msg.queryResponse.data_sources[0]?.datasource_name }}
                  · {{ msg.queryResponse.data_sources[0]?.data_date }}
                </div>

                <!-- 反馈按钮 -->
                <div class="feedback-row">
                  <el-button
                    size="small"
                    :type="msg.feedback === 1 ? 'success' : 'default'"
                    circle
                    @click="submitFeedback(msg, 1)"
                  >
                    <el-icon><Pointer /></el-icon>
                  </el-button>
                  <el-button
                    size="small"
                    :type="msg.feedback === -1 ? 'danger' : 'default'"
                    circle
                    @click="submitFeedback(msg, -1)"
                  >
                    <el-icon><SwitchButton /></el-icon>
                  </el-button>
                  <span v-if="msg.queryResponse?.execution_ms" class="exec-time">
                    {{ msg.queryResponse.execution_ms }}ms
                  </span>
                </div>
              </template>
            </div>
          </div>
        </template>
      </div>

      <!-- 输入区域 -->
      <div class="input-area">
        <el-input
          v-model="inputText"
          type="textarea"
          :autosize="{ minRows: 1, maxRows: 4 }"
          :placeholder="inputPlaceholder"
          :disabled="isLoading"
          resize="none"
          @keydown.enter.exact.prevent="handleSend"
        />
        <el-button
          type="primary"
          :loading="isLoading"
          :disabled="!inputText.trim()"
          class="send-btn"
          @click="handleSend"
        >
          发送
        </el-button>
      </div>
    </el-main>
  </el-container>
</template>

<script setup lang="ts">
import { nextTick, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  ChatLineRound,
  FolderOpened,
  InfoFilled,
  Pointer,
  Setting,
  SwitchButton,
  WarningFilled,
} from '@element-plus/icons-vue'
import * as echarts from 'echarts'

import { queryChatAPI, submitFeedbackAPI } from '@/api/chat'
import { useAuthStore } from '@/stores/auth'
import type { ChatMessage, ChatQueryResponse } from '@/types'

const router = useRouter()
const authStore = useAuthStore()

const messages = ref<(ChatMessage & { feedback?: 1 | -1; logId?: string })[]>([])
const inputText = ref('')
const isLoading = ref(false)
const conversationId = ref<string | undefined>(undefined)
const messageListRef = ref<HTMLElement | null>(null)

const exampleQuestions = [
  '本月各产品线销售额汇总',
  '本季度财务收入与上季度对比',
  '当前库存量最低的前5种物料',
  '采购金额最大的前3个供应商',
]

const inputPlaceholder = '输入问题后按 Enter 发送（Shift+Enter 换行）'

function startNewConversation() {
  conversationId.value = undefined
  messages.value = []
}

async function handleLogout() {
  await authStore.logout()
  router.push('/login')
}

function quickSend(q: string) {
  inputText.value = q
  handleSend()
}

async function handleSend() {
  const question = inputText.value.trim()
  if (!question || isLoading.value) return

  inputText.value = ''
  const msgId = Date.now().toString()

  // 添加用户消息
  messages.value.push({
    id: msgId,
    role: 'user',
    content: question,
    timestamp: new Date().toISOString(),
  })

  // 添加 AI 占位消息
  const aiMsgId = (Date.now() + 1).toString()
  messages.value.push({
    id: aiMsgId,
    role: 'assistant',
    content: '__loading__',
    timestamp: new Date().toISOString(),
  })

  await scrollToBottom()
  isLoading.value = true

  try {
    const resp: ChatQueryResponse = await queryChatAPI({
      question,
      conversation_id: conversationId.value,
    })

    conversationId.value = resp.conversation_id

    // 更新 AI 消息
    const aiMsg = messages.value.find((m) => m.id === aiMsgId)
    if (aiMsg) {
      aiMsg.content = resp.answer_text
      aiMsg.queryResponse = resp
    }

    await scrollToBottom()

    // 渲染图表
    if (resp.chart_option) {
      await nextTick()
      renderChart(aiMsgId, resp.chart_option as Record<string, unknown>)
    }
  } catch (err: unknown) {
    const aiMsg = messages.value.find((m) => m.id === aiMsgId)
    if (aiMsg) aiMsg.content = '__error__'
    ElMessage.error((err as Error).message || '查询失败，请稍后重试')
  } finally {
    isLoading.value = false
  }
}

function renderChart(msgId: string, option: Record<string, unknown>) {
  const el = document.getElementById(`chart-${msgId}`)
  if (!el) return
  const chart = echarts.init(el)
  chart.setOption(option)
}

function getTableRows(resp: ChatQueryResponse | undefined): Record<string, unknown>[] {
  if (!resp?.table_data) return []
  const td = resp.table_data as { columns?: string[]; rows?: unknown[][] }
  if (!td.columns || !td.rows) return resp.table_data as Record<string, unknown>[]
  return td.rows.map((row) => {
    const obj: Record<string, unknown> = {}
    td.columns!.forEach((col, i) => { obj[col] = row[i] })
    return obj
  })
}

function getTableCols(resp: ChatQueryResponse | undefined): string[] {
  if (!resp?.table_data) return []
  const td = resp.table_data as { columns?: string[] }
  return td.columns ?? Object.keys((resp.table_data as Record<string, unknown>[])[0] ?? {})
}

function getSingleValue(resp: ChatQueryResponse | undefined): string {
  if (!resp?.table_data) return '--'
  const rows = getTableRows(resp)
  if (!rows.length) return '--'
  const val = Object.values(rows[0])[0]
  return val !== null && val !== undefined ? String(val) : '--'
}

async function submitFeedback(msg: ChatMessage & { feedback?: 1 | -1; logId?: string }, score: 1 | -1) {
  if (!msg.logId) return
  try {
    await submitFeedbackAPI(msg.logId, score)
    msg.feedback = score
  } catch {
    ElMessage.warning('反馈提交失败')
  }
}

async function scrollToBottom() {
  await nextTick()
  if (messageListRef.value) {
    messageListRef.value.scrollTop = messageListRef.value.scrollHeight
  }
}
</script>

<style scoped>
.chat-layout {
  height: 100vh;
}

.sidebar {
  display: flex;
  flex-direction: column;
  background: #1d2230;
  color: #fff;
  padding: 0;
}

.sidebar-header {
  padding: 20px 16px 12px;
  font-size: 16px;
  font-weight: 600;
}

.logo-text {
  color: #fff;
}

.new-chat-btn {
  margin: 0 16px 8px;
  width: calc(100% - 32px);
}

.nav-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  cursor: pointer;
  color: #b0b8c9;
  font-size: 14px;
  transition: background 0.2s;
}

.nav-item:hover {
  background: #2d3548;
  color: #fff;
}

.user-area {
  margin-top: auto;
  padding: 12px 16px;
  display: flex;
  align-items: center;
  gap: 8px;
  border-top: 1px solid #2d3548;
  color: #b0b8c9;
  font-size: 13px;
}

.user-name {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.chat-main {
  display: flex;
  flex-direction: column;
  padding: 0;
  overflow: hidden;
  background: #f5f6fa;
}

.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 24px 10% 16px;
}

.empty-guide {
  text-align: center;
  margin-top: 80px;
  color: #909399;
}

.empty-title {
  font-size: 20px;
  color: #303133;
  margin: 16px 0 8px;
}

.empty-sub {
  font-size: 14px;
  margin-bottom: 16px;
}

.example-questions {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 10px;
}

.example-tag {
  cursor: pointer;
}

.message-row {
  display: flex;
  align-items: flex-start;
  margin-bottom: 20px;
  gap: 10px;
}

.user-row {
  flex-direction: row-reverse;
}

.bubble {
  max-width: 70%;
  padding: 10px 14px;
  border-radius: 12px;
  line-height: 1.6;
  font-size: 14px;
}

.user-bubble {
  background: #409EFF;
  color: #fff;
  border-radius: 12px 2px 12px 12px;
}

.ai-bubble {
  background: #fff;
  color: #303133;
  border-radius: 2px 12px 12px 12px;
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.08);
  min-width: 120px;
}

.loading-dots {
  display: flex;
  gap: 4px;
  padding: 4px 0;
}

.loading-dots span {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #c0c4cc;
  animation: bounce 1.2s infinite;
}

.loading-dots span:nth-child(2) {
  animation-delay: 0.2s;
}

.loading-dots span:nth-child(3) {
  animation-delay: 0.4s;
}

@keyframes bounce {
  0%, 80%, 100% { transform: scale(0.8); opacity: 0.5; }
  40% { transform: scale(1.2); opacity: 1; }
}

.error-msg {
  display: flex;
  align-items: center;
  gap: 6px;
  color: #F56C6C;
}

.answer-summary {
  margin: 0 0 8px;
  white-space: pre-wrap;
}

.single-value-card {
  background: #ecf5ff;
  border-radius: 8px;
  padding: 12px 16px;
  text-align: center;
  margin: 8px 0;
}

.single-value-num {
  font-size: 28px;
  font-weight: 600;
  color: #409EFF;
}

.result-table {
  margin: 8px 0;
  font-size: 12px;
}

.chart-container {
  width: 100%;
  height: 300px;
  margin: 8px 0;
}

.source-info {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: #909399;
  margin-top: 8px;
}

.feedback-row {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 10px;
}

.exec-time {
  font-size: 11px;
  color: #c0c4cc;
  margin-left: auto;
}

.input-area {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  padding: 12px 10%;
  background: #fff;
  border-top: 1px solid #e4e7ed;
}

.input-area :deep(.el-textarea__inner) {
  border-radius: 8px;
  font-size: 14px;
}

.send-btn {
  flex-shrink: 0;
  height: 40px;
  padding: 0 20px;
}
</style>
