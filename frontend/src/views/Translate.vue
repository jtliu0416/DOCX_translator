<script setup>
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import {
  completeTaskUpload,
  createTaskUpload,
  getTask,
  downloadTask,
  retryTask,
  listGlossaries,
  getGlossary,
  getTaskStatistics,
  getUploadSettings,
  uploadTaskChunk,
} from '../api'
import { getAccessToken } from '../auth'

const sourceLang = ref('zh')
const targetLang = ref('en')
const glossaryId = ref('')
const glossaries = ref([])
const useBuiltinGlossary = ref(true)
const builtinGlossary = ref(null)
const builtinPreviewVisible = ref(false)
const builtinTerms = ref([])
const builtinSearch = ref('')
const uploadFiles = ref([])
const pendingFiles = ref([])
const tasks = ref([])          // [{taskId, filename, status, progress, error}]
const taskStatistics = ref(null)
let refreshingTaskStatistics = false
let queuedTaskStatisticsRefresh = false
const uploading = ref(false)
const maxFileSize = ref(100 * 1024 * 1024)
const maxFileSizeLabel = ref('100MB')
const chunkSize = ref(5 * 1024 * 1024)
const wsMap = new Map()
const pollingMap = new Map()
const heartbeatMap = new Map()
const supportedFilePattern = /\.(docx|xlsx|pptx)$/i

function oppositeLang(lang) {
  return lang === 'zh' ? 'en' : 'zh'
}

watch(sourceLang, (value) => {
  if (value === targetLang.value) {
    targetLang.value = oppositeLang(value)
  }
})

watch(targetLang, (value) => {
  if (value === sourceLang.value) {
    sourceLang.value = oppositeLang(value)
  }
})

const userGlossaries = computed(() => glossaries.value.filter(g => !g.is_builtin))

const builtinFilteredTerms = computed(() => {
  const q = builtinSearch.value.trim().toLowerCase()
  if (!q) return builtinTerms.value
  return builtinTerms.value.filter(t =>
    t.source_term.toLowerCase().includes(q) ||
    t.target_term.toLowerCase().includes(q) ||
    (t.note && t.note.toLowerCase().includes(q))
  )
})

const uploadFileSizeTip = computed(() =>
  maxFileSize.value
    ? `单个文件最大 ${maxFileSizeLabel.value}，分片上传`
    : '单个文件大小不限，分片上传'
)

onMounted(async () => {
  loadTaskStatistics()

  try {
    const res = await listGlossaries()
    glossaries.value = res.data
    builtinGlossary.value = res.data.find(g => g.is_builtin) || null
  } catch {}

  try {
    const res = await getUploadSettings()
    maxFileSize.value = res.data.max_file_size
    maxFileSizeLabel.value = res.data.max_file_size_label || maxFileSizeLabel.value
    chunkSize.value = res.data.chunk_size || chunkSize.value
  } catch {}
})

async function loadTaskStatistics() {
  if (refreshingTaskStatistics) {
    queuedTaskStatisticsRefresh = true
    return
  }

  refreshingTaskStatistics = true
  try {
    const res = await getTaskStatistics()
    taskStatistics.value = res.data
  } catch {
    taskStatistics.value = null
  } finally {
    refreshingTaskStatistics = false
    if (queuedTaskStatisticsRefresh) {
      queuedTaskStatisticsRefresh = false
      void loadTaskStatistics()
    }
  }
}

function statisticValue(key) {
  const value = taskStatistics.value?.[key]
  return Number.isFinite(value) ? value.toLocaleString('zh-CN') : '--'
}

onUnmounted(() => {
  wsMap.forEach(ws => ws.close())
  wsMap.clear()
  pollingMap.forEach(timer => clearInterval(timer))
  pollingMap.clear()
  heartbeatMap.forEach(timer => clearInterval(timer))
  heartbeatMap.clear()
})

async function showBuiltinPreview() {
  if (!builtinGlossary.value) return
  if (builtinTerms.value.length > 0) {
    builtinPreviewVisible.value = true
    return
  }
  try {
    const res = await getGlossary(builtinGlossary.value.id)
    builtinTerms.value = res.data.terms
    builtinPreviewVisible.value = true
  } catch {
    ElMessage.error('加载术语表失败')
  }
}

function normalizeUploadFile(item) {
  if (!item) return null
  if (typeof File !== 'undefined' && item instanceof File) return item
  return item.raw || item.originFileObj || item.file || null
}

function isUploadableFile(file) {
  return file &&
    typeof file.name === 'string' &&
    typeof file.size === 'number' &&
    (typeof Blob === 'undefined' || file instanceof Blob || typeof file.arrayBuffer === 'function')
}

function collectUploadFiles(fileList) {
  return fileList
    .map(normalizeUploadFile)
    .filter(isUploadableFile)
}

function syncUploadFiles(fileList) {
  uploadFiles.value = fileList
  pendingFiles.value = collectUploadFiles(fileList)
}

function handleUploadChange(_, fileList) {
  syncUploadFiles(fileList)
}

function handleUploadRemove(_, fileList) {
  syncUploadFiles(fileList)
}

async function startTranslation() {
  if (sourceLang.value === targetLang.value) {
    ElMessage.warning('源语言和目标语言不能相同')
    return
  }

  const selectedFiles = pendingFiles.value.length > 0
    ? pendingFiles.value
    : collectUploadFiles(uploadFiles.value)

  if (selectedFiles.length === 0) {
    ElMessage.warning('请先上传文件')
    return
  }

  const documentFiles = selectedFiles.filter(file => supportedFilePattern.test(file.name))
  if (documentFiles.length === 0) {
    ElMessage.warning('仅支持 .docx / .xlsx / .pptx 文件')
    return
  }
  if (documentFiles.length !== selectedFiles.length) {
    ElMessage.warning('已忽略非 .docx / .xlsx / .pptx 文件')
  }

  const acceptedFiles = maxFileSize.value
    ? documentFiles.filter(file => file.size <= maxFileSize.value)
    : documentFiles
  if (acceptedFiles.length === 0) {
    ElMessage.warning(`文件超过 ${maxFileSizeLabel.value} 限制`)
    return
  }
  if (acceptedFiles.length !== documentFiles.length) {
    ElMessage.warning(`已忽略超过 ${maxFileSizeLabel.value} 限制的文件`)
  }

  uploading.value = true
  tasks.value = []

  for (const f of acceptedFiles) {
    const entry = {
      taskId: null,
      filename: f.name,
      status: 'uploading',
      progress: 0,
      error: '',
    }
    tasks.value.push(entry)

    try {
      const res = await uploadFileInChunks(f, entry)
      entry.taskId = res.data.task_id
      entry.status = 'pending'
      entry.progress = 0
      connectWebSocket(entry)
    } catch (e) {
      entry.status = 'failed'
      entry.progress = 0
      entry.error = e.response?.data?.detail || '创建任务失败'
    }
  }

  uploading.value = false
  void loadTaskStatistics()
}

async function uploadFileInChunks(file, entry) {
  const createRes = await createTaskUpload({
    filename: file.name,
    size: file.size,
    source_lang: sourceLang.value,
    target_lang: targetLang.value,
    glossary_id: glossaryId.value || null,
    use_builtin_glossary: useBuiltinGlossary.value,
  })

  const uploadId = createRes.data.upload_id
  const serverChunkSize = createRes.data.chunk_size || chunkSize.value
  const chunkCount = createRes.data.chunk_count || Math.ceil(file.size / serverChunkSize)

  for (let index = 0; index < chunkCount; index += 1) {
    const start = index * serverChunkSize
    const end = Math.min(start + serverChunkSize, file.size)
    const chunk = file.slice(start, end)
    const formData = new FormData()
    formData.append('index', String(index))
    formData.append('file', chunk, `${file.name}.part${index}`)

    await uploadTaskChunk(uploadId, formData, (event) => {
      const currentChunkRatio = event.total ? event.loaded / event.total : 0
      const uploadedRatio = (index + currentChunkRatio) / chunkCount
      entry.progress = Math.min(10, Math.floor(uploadedRatio * 10))
    })
  }

  entry.progress = 10
  return completeTaskUpload(uploadId)
}

function connectWebSocket(entry) {
  if (!entry.taskId) return

  closeTaskWebSocket(entry.taskId)
  stopTaskHeartbeat(entry.taskId)
  startPollingForTask(entry)

  const token = getAccessToken()
  if (!token) return
  const ws = new WebSocket(getTaskWebSocketUrl(entry.taskId), ['doctrans-v1', `jwt.${token}`])

  ws.onmessage = (event) => {
    if (event.data === 'pong') return

    const d = JSON.parse(event.data)
    applyTaskUpdate(entry.taskId, d)

    if (d.status === 'completed' || d.status === 'failed') {
      stopPollingForTask(entry.taskId)
      stopTaskHeartbeat(entry.taskId)
      closeTaskWebSocket(entry.taskId)
    }
  }

  ws.onerror = () => {
    closeTaskWebSocket(entry.taskId)
    stopTaskHeartbeat(entry.taskId)
  }

  ws.onclose = () => {
    wsMap.delete(entry.taskId)
    stopTaskHeartbeat(entry.taskId)
  }

  wsMap.set(entry.taskId, ws)
  startTaskHeartbeat(entry.taskId, ws)
}

function startPollingForTask(entry) {
  if (!entry.taskId || pollingMap.has(entry.taskId)) return

  const timer = setInterval(async () => {
    try {
      const res = await getTask(entry.taskId)
      const task = applyTaskUpdate(entry.taskId, res.data)
      if (!task) {
        stopPollingForTask(entry.taskId)
        return
      }

      if (task.status === 'completed' || task.status === 'failed') {
        stopPollingForTask(entry.taskId)
        stopTaskHeartbeat(entry.taskId)
        closeTaskWebSocket(entry.taskId)
      }
    } catch {
      stopPollingForTask(entry.taskId)
    }
  }, 2000)

  pollingMap.set(entry.taskId, timer)
}

async function handleDownload(task) {
  try {
    const res = await downloadTask(task.taskId)
    const url = URL.createObjectURL(res.data)
    const a = document.createElement('a')
    a.href = url
    const extMatch = task.filename.match(/\.(docx|xlsx|pptx)$/i)
    const ext = extMatch ? extMatch[0] : '.docx'
    const base = task.filename.replace(/\.(docx|xlsx|pptx)$/i, '')
    const disposition = res.headers['content-disposition']
    const suffix = ext.toLowerCase() === '.xlsx' ? '翻译版' : '双语'
    let downloadName = `${base}_${suffix}${ext}`
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
    void loadTaskStatistics()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '重试失败')
  }
}

function statusText(s) {
  const map = {
    uploading: '正在上传...',
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

function stopPollingForTask(taskId) {
  const timer = pollingMap.get(taskId)
  if (!timer) return
  clearInterval(timer)
  pollingMap.delete(taskId)
}

function closeTaskWebSocket(taskId) {
  const ws = wsMap.get(taskId)
  if (!ws) return
  wsMap.delete(taskId)
  ws.close()
}

function startTaskHeartbeat(taskId, ws) {
  const timer = setInterval(() => {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send('ping')
    }
  }, 15000)
  heartbeatMap.set(taskId, timer)
}

function stopTaskHeartbeat(taskId) {
  const timer = heartbeatMap.get(taskId)
  if (!timer) return
  clearInterval(timer)
  heartbeatMap.delete(taskId)
}

function applyTaskUpdate(taskId, data) {
  const task = tasks.value.find(t => t.taskId === taskId)
  if (!task) return null

  const statusChanged = Boolean(data.status && data.status !== task.status)
  if (data.status) task.status = data.status
  if (data.progress != null) task.progress = data.progress
  if (data.error_message) task.error = data.error_message
  if (statusChanged) void loadTaskStatistics()
  return task
}

function getTaskWebSocketUrl(taskId) {
  const envBase = import.meta.env.VITE_WS_BASE_URL?.trim()
  if (envBase) {
    return `${envBase.replace(/\/$/, '')}/ws/tasks/${taskId}`
  }

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const hostname = window.location.hostname
  const port = window.location.port === '5173' ? '8000' : window.location.port
  const host = port ? `${hostname}:${port}` : hostname
  return `${protocol}//${host}/ws/tasks/${taskId}`
}
</script>

<template>
  <div class="translate-page">
    <header class="page-heading">
      <div>
        <span class="page-eyebrow">DOCUMENT TRANSLATION</span>
        <h1>文档翻译</h1>
        <p>上传文档后选择翻译语言和术语表，即可开始处理。</p>
      </div>
      <span class="supported-formats">DOCX&nbsp;&nbsp;XLSX&nbsp;&nbsp;PPTX</span>
    </header>

    <section class="task-statistics" aria-label="任务统计">
      <div class="statistic-item">
        <strong>{{ statisticValue('pending') }}</strong>
        <span class="statistic-label">当前排队</span>
      </div>
      <div class="statistic-item">
        <strong>{{ statisticValue('executing') }}</strong>
        <span class="statistic-label">执行中</span>
      </div>
      <div class="statistic-item">
        <strong>{{ statisticValue('completed_today') }}</strong>
        <span class="statistic-label">今日完成</span>
      </div>
      <div class="statistic-item statistic-item--featured">
        <strong>{{ statisticValue('completed_total') }}</strong>
        <span class="statistic-label">累计翻译文档</span>
      </div>
      <div class="statistic-item statistic-item--featured">
        <strong>{{ statisticValue('submitted_today') }}</strong>
        <span class="statistic-label">今日翻译文档</span>
      </div>
    </section>

    <section class="translation-workspace" aria-label="翻译设置">
      <div class="upload-section">
        <div class="section-heading">
          <div>
            <h2>上传文档</h2>
            <p>{{ uploadFileSizeTip }}</p>
          </div>
        </div>
        <el-upload
          v-model:file-list="uploadFiles"
          class="upload-area"
          drag
          multiple
          :auto-upload="false"
          accept=".docx,.xlsx,.pptx"
          :on-change="handleUploadChange"
          :on-remove="handleUploadRemove"
        >
          <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
          <div class="el-upload__text">拖拽文件到此处，或<em>点击上传</em></div>
          <div class="upload-support-text">支持多文件同时上传</div>
        </el-upload>
      </div>

      <div class="translation-settings">
        <div class="section-heading">
          <div>
            <h2>翻译设置</h2>
            <p>确认语言方向和术语表。</p>
          </div>
        </div>
        <el-form class="translation-form" label-position="top">
          <div class="language-fields">
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
          </div>
          <el-form-item label="内置术语表" v-if="builtinGlossary">
            <div class="builtin-glossary-row">
              <el-checkbox v-model="useBuiltinGlossary">
                {{ builtinGlossary.name }} ({{ builtinGlossary.term_count }} 条)
              </el-checkbox>
              <el-button size="small" @click="showBuiltinPreview">预览</el-button>
            </div>
          </el-form-item>
          <el-form-item label="我的术语表">
            <el-select v-model="glossaryId" clearable placeholder="可选">
              <el-option
                v-for="g in userGlossaries"
                :key="g.id"
                :label="`${g.name} (${g.term_count} 条)`"
                :value="g.id"
              />
            </el-select>
          </el-form-item>
        </el-form>
        <el-button
          class="translate-submit"
          type="primary"
          size="large"
          :loading="uploading || hasActive()"
          @click="startTranslation"
        >
          {{ uploading ? '提交中...' : hasActive() ? '翻译中...' : '开始翻译' }}
        </el-button>
      </div>
    </section>

    <section v-if="tasks.length > 0" class="task-results">
      <div class="section-heading task-results-heading">
        <div>
          <h2>本次任务</h2>
          <p>处理完成后可直接下载翻译结果。</p>
        </div>
      </div>
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
        class="clear-results-button"
      >
        清空结果，继续翻译
      </el-button>
    </section>
    <!-- Built-in glossary preview dialog -->
    <el-dialog v-model="builtinPreviewVisible" :title="builtinGlossary?.name" width="700">
      <el-input
        v-model="builtinSearch"
        placeholder="搜索术语（中文/英文/备注）..."
        clearable
        style="margin-bottom: 12px;"
      />
      <el-table :data="builtinFilteredTerms" max-height="500">
        <el-table-column type="index" label="#" width="50" />
        <el-table-column prop="source_term" label="中文" />
        <el-table-column prop="target_term" label="英文" />
        <el-table-column prop="note" label="备注" width="120" />
      </el-table>
      <div v-if="builtinSearch" style="margin-top:8px;color:#909399;font-size:12px;">
        显示 {{ builtinFilteredTerms.length }} / {{ builtinTerms.length }} 条
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.translate-page { padding: 0; }
.page-heading { display: flex; align-items: flex-end; justify-content: space-between; gap: 24px; margin-bottom: 24px; }
.page-eyebrow { color: #3976b8; font-size: 11px; font-weight: 700; letter-spacing: 1.2px; }
.page-heading h1 { margin: 5px 0 6px; color: #182230; font-size: 28px; line-height: 1.2; }
.page-heading p, .section-heading p { margin: 0; color: #718096; font-size: 13px; line-height: 1.5; }
.supported-formats { padding: 7px 10px; border: 1px solid #dce5ef; border-radius: 4px; color: #60758d; background: #fff; font-size: 11px; font-weight: 600; letter-spacing: .7px; white-space: nowrap; }
.task-statistics { display: grid; grid-template-columns: repeat(3, minmax(0, .9fr)) repeat(2, minmax(0, 1.25fr)); gap: 12px; margin-bottom: 24px; }
.statistic-item { min-height: 88px; padding: 15px 17px; border: 1px solid #dce4ee; border-radius: 6px; background: #fff; display: flex; flex-direction: column; justify-content: space-between; box-sizing: border-box; }
.statistic-label { color: #718096; font-size: 13px; line-height: 1.2; }
.statistic-item strong { color: #1f2937; font-size: 26px; line-height: 1; font-variant-numeric: tabular-nums; }
.statistic-item--featured { background: #eef6ff; border-color: #b9d8ff; }
.statistic-item--featured strong { color: #1769c2; }
.translation-workspace { display: grid; grid-template-columns: minmax(0, 1.45fr) minmax(320px, 1fr); border: 1px solid #dfe6ef; border-radius: 8px; background: #fff; overflow: hidden; }
.upload-section, .translation-settings { padding: 28px; }
.upload-section { border-right: 1px solid #e7edf3; }
.section-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
.section-heading h2 { margin: 0 0 4px; color: #263445; font-size: 17px; line-height: 1.3; }
.upload-area { width: 100%; }
.upload-area :deep(.el-upload) { display: block; width: 100%; }
.upload-area :deep(.el-upload-dragger) { width: 100%; min-height: 248px; box-sizing: border-box; border-color: #bfd0e4; border-radius: 6px; background: #fbfdff; display: flex; flex-direction: column; justify-content: center; }
.upload-area :deep(.el-icon--upload) { color: #5489bd; margin-bottom: 14px; }
.upload-area :deep(.el-upload__text) { color: #46566a; font-size: 15px; }
.upload-area :deep(.el-upload__text em) { color: #2478c9; font-style: normal; }
.upload-support-text { margin-top: 9px; color: #91a0b2; font-size: 12px; }
.translation-form :deep(.el-form-item) { margin-bottom: 18px; }
.translation-form :deep(.el-form-item__label) { padding-bottom: 7px; color: #4a5b70; font-size: 13px; line-height: 1; }
.translation-form :deep(.el-select) { width: 100%; }
.language-fields { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.builtin-glossary-row { display: flex; align-items: center; flex-wrap: wrap; gap: 8px 12px; min-height: 32px; }
.translate-submit { width: 100%; margin-top: 2px; }
.task-results { margin-top: 24px; padding: 24px 28px; border: 1px solid #dfe6ef; border-radius: 8px; background: #fff; }
.task-results-heading { margin-bottom: 18px; }
.task-item { margin-bottom: 12px; padding: 14px 16px; border: 1px solid #e3e9f0; border-radius: 6px; background: #fbfcfe; }
.task-header { display: flex; justify-content: space-between; align-items: center; gap: 16px; margin-bottom: 10px; }
.task-filename { min-width: 0; overflow: hidden; color: #303d4e; font-size: 14px; font-weight: 600; text-overflow: ellipsis; white-space: nowrap; }
.task-error { font-size: 12px; color: #d14343; margin-top: 6px; }
.task-actions { margin-top: 10px; display: flex; gap: 8px; }
.clear-results-button { width: 100%; margin-top: 4px; }
@media (max-width: 720px) {
  .page-heading { align-items: flex-start; flex-direction: column; }
  .task-statistics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .statistic-item--featured:last-child { grid-column: span 2; }
  .translation-workspace { grid-template-columns: 1fr; }
  .upload-section { border-right: 0; border-bottom: 1px solid #e7edf3; }
}
@media (max-width: 420px) {
  .page-heading h1 { font-size: 24px; }
  .upload-section, .translation-settings, .task-results { padding: 20px; }
  .language-fields { grid-template-columns: 1fr; gap: 0; }
}
</style>
