<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import { createTask, getTask, downloadTask, retryTask, listGlossaries } from '../api'

const sourceLang = ref('zh')
const targetLang = ref('en')
const glossaryId = ref('')
const glossaries = ref([])
const tasks = ref([])          // [{taskId, filename, status, progress, error}]
const uploading = ref(false)
const wsMap = new Map()

onMounted(async () => {
  try {
    const res = await listGlossaries()
    glossaries.value = res.data
  } catch {}
})

onUnmounted(() => {
  wsMap.forEach(ws => ws.close())
  wsMap.clear()
})

async function startTranslation() {
  const uploadComp = document.querySelector('.el-upload input[type=file]')
  const fileList = uploadComp?.files
  if (!fileList || fileList.length === 0) {
    ElMessage.warning('请先上传文件')
    return
  }

  uploading.value = true
  tasks.value = []

  for (const f of fileList) {
    if (!f.name.endsWith('.docx')) continue

    const formData = new FormData()
    formData.append('file', f)
    formData.append('source_lang', sourceLang.value)
    formData.append('target_lang', targetLang.value)
    if (glossaryId.value) {
      formData.append('glossary_id', glossaryId.value)
    }

    try {
      const res = await createTask(formData)
      const entry = {
        taskId: res.data.task_id,
        filename: f.name,
        status: 'pending',
        progress: 0,
        error: '',
      }
      tasks.value.push(entry)
      connectWebSocket(entry)
    } catch (e) {
      tasks.value.push({
        taskId: null,
        filename: f.name,
        status: 'failed',
        progress: 0,
        error: e.response?.data?.detail || '创建任务失败',
      })
    }
  }

  uploading.value = false
}

function connectWebSocket(entry) {
  if (!entry.taskId) return

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const host = window.location.host
  const ws = new WebSocket(`${protocol}//${host}/ws/tasks/${entry.taskId}`)

  ws.onmessage = (event) => {
    const d = JSON.parse(event.data)
    if (d.status) entry.status = d.status
    if (d.progress != null) entry.progress = d.progress
    if (d.error_message) entry.error = d.error_message

    if (d.status === 'completed' || d.status === 'failed') {
      ws.close()
      wsMap.delete(entry.taskId)
    }
  }

  ws.onerror = () => {
    ws.close()
    wsMap.delete(entry.taskId)
    startPollingForTask(entry)
  }

  ws.onclose = () => {
    wsMap.delete(entry.taskId)
  }

  wsMap.set(entry.taskId, ws)
}

function startPollingForTask(entry) {
  const timer = setInterval(async () => {
    try {
      const res = await getTask(entry.taskId)
      const d = res.data
      entry.status = d.status
      entry.progress = d.progress
      if (d.error_message) entry.error = d.error_message

      if (d.status === 'completed' || d.status === 'failed') {
        clearInterval(timer)
      }
    } catch {
      clearInterval(timer)
    }
  }, 2000)
}

async function handleDownload(task) {
  try {
    const res = await downloadTask(task.taskId)
    const url = URL.createObjectURL(res.data)
    const a = document.createElement('a')
    a.href = url
    const base = task.filename.replace(/\.docx$/i, '')
    const disposition = res.headers['content-disposition']
    let downloadName = `${base}_双语.docx`
    if (disposition) {
      const m = disposition.match(/filename\*?=(?:UTF-8''|"?)([^";]+)/i)
      if (m) downloadName = decodeURIComponent(m[1].replace(/"/g, ''))
    }
    a.download = downloadName
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    ElMessage.error('下载失败')
  }
}

async function handleRetry(task) {
  try {
    await retryTask(task.taskId)
    task.status = 'pending'
    task.progress = 0
    task.error = ''
    connectWebSocket(task)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '重试失败')
  }
}

function statusText(s) {
  const map = {
    pending: '排队等待中...', extracting: '正在提取文本...',
    translating: '正在翻译...', building: '正在生成文档...',
    completed: '翻译完成', failed: '翻译失败',
  }
  return map[s] || s
}

function statusType(s) {
  if (s === 'completed') return 'success'
  if (s === 'failed') return 'danger'
  return ''
}

const allDone = () => tasks.value.length > 0 && tasks.value.every(t => t.status === 'completed' || t.status === 'failed')
const hasActive = () => tasks.value.some(t => t.status !== 'completed' && t.status !== 'failed')
</script>

<template>
  <div class="translate-page">
    <h2>文档翻译</h2>

    <el-upload
      class="upload-area"
      drag
      multiple
      :auto-upload="false"
      accept=".docx"
    >
      <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
      <div class="el-upload__text">拖拽文件到此处，或<em>点击上传</em>（支持多文件）</div>
      <template #tip>
        <div class="el-upload__tip">仅支持 .docx 文件，单个文件最大 10MB</div>
      </template>
    </el-upload>

    <el-form label-width="100px" style="margin-top: 20px;">
      <el-form-item label="源语言">
        <el-select v-model="sourceLang">
          <el-option label="中文" value="zh" />
          <el-option label="English" value="en" />
        </el-select>
      </el-form-item>
      <el-form-item label="目标语言">
        <el-select v-model="targetLang">
          <el-option label="English" value="en" />
          <el-option label="中文" value="zh" />
        </el-select>
      </el-form-item>
      <el-form-item label="术语表">
        <el-select v-model="glossaryId" clearable placeholder="可选">
          <el-option
            v-for="g in glossaries"
            :key="g.id"
            :label="`${g.name} (${g.term_count} 条)`"
            :value="g.id"
          />
        </el-select>
      </el-form-item>
    </el-form>

    <el-button
      type="primary"
      size="large"
      :loading="uploading || hasActive()"
      @click="startTranslation"
      style="width: 100%;"
    >
      {{ uploading ? '提交中...' : hasActive() ? '翻译中...' : '开始翻译' }}
    </el-button>

    <div v-if="tasks.length > 0" style="margin-top: 24px;">
      <div v-for="task in tasks" :key="task.taskId || task.filename" class="task-item">
        <div class="task-header">
          <span class="task-filename">{{ task.filename }}</span>
          <el-tag :type="statusType(task.status)" size="small">{{ statusText(task.status) }}</el-tag>
        </div>
        <el-progress
          :percentage="task.progress"
          :status="task.status === 'failed' ? 'exception' : task.status === 'completed' ? 'success' : ''"
        />
        <div v-if="task.error" class="task-error">{{ task.error }}</div>
        <div class="task-actions">
          <el-button v-if="task.status === 'completed'" type="success" size="small" @click="handleDownload(task)">下载</el-button>
          <el-button v-if="task.status === 'failed' && task.taskId" type="warning" size="small" @click="handleRetry(task)">重试</el-button>
        </div>
      </div>

      <el-button
        v-if="allDone()"
        type="success"
        size="large"
        @click="tasks = []"
        style="width: 100%; margin-top: 16px;"
      >
        清空结果，继续翻译
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.translate-page { padding: 20px 0; }
.upload-area { width: 100%; }
.task-item { margin-bottom: 18px; padding: 12px; border: 1px solid #ebeef5; border-radius: 8px; }
.task-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
.task-filename { font-size: 14px; color: #303133; font-weight: 500; }
.task-error { font-size: 12px; color: #f56c6c; margin-top: 4px; }
.task-actions { margin-top: 8px; display: flex; gap: 8px; }
</style>
