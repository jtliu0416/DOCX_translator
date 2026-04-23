<script setup>
import { ref, onMounted, onUnmounted } from 'vue'
import { ElMessage } from 'element-plus'
import { UploadFilled } from '@element-plus/icons-vue'
import { createTask, getTask, downloadTask, listGlossaries } from '../api'

const file = ref(null)
const sourceLang = ref('zh')
const targetLang = ref('en')
const glossaryId = ref('')
const glossaries = ref([])
const taskId = ref(null)
const status = ref('')
const progress = ref(0)
const loading = ref(false)
let pollTimer = null

onMounted(async () => {
  try {
    const res = await listGlossaries()
    glossaries.value = res.data
  } catch {}
})

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer)
})

function handleUpload(uploadFile) {
  file.value = uploadFile.raw
}

async function startTranslation() {
  if (!file.value) {
    ElMessage.warning('请先上传文件')
    return
  }

  loading.value = true
  status.value = 'pending'
  progress.value = 0

  const formData = new FormData()
  formData.append('file', file.value)
  formData.append('source_lang', sourceLang.value)
  formData.append('target_lang', targetLang.value)
  if (glossaryId.value) {
    formData.append('glossary_id', glossaryId.value)
  }

  try {
    const res = await createTask(formData)
    taskId.value = res.data.task_id
    status.value = 'pending'
    startPolling()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '创建任务失败')
    loading.value = false
  }
}

function startPolling() {
  pollTimer = setInterval(async () => {
    try {
      const res = await getTask(taskId.value)
      const d = res.data
      status.value = d.status
      progress.value = d.progress

      if (d.status === 'completed') {
        clearInterval(pollTimer)
        loading.value = false
        ElMessage.success('翻译完成')
      } else if (d.status === 'failed') {
        clearInterval(pollTimer)
        loading.value = false
        ElMessage.error(d.error_message || '翻译失败')
      }
    } catch {
      clearInterval(pollTimer)
      loading.value = false
    }
  }, 2000)
}

async function handleDownload() {
  try {
    const res = await downloadTask(taskId.value)
    const url = URL.createObjectURL(res.data)
    const a = document.createElement('a')
    a.href = url
    a.download = `translated_${file.value.name}`
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    ElMessage.error('下载失败')
  }
}
</script>

<template>
  <div class="translate-page">
    <h2>文档翻译</h2>

    <el-upload
      class="upload-area"
      drag
      :auto-upload="false"
      accept=".docx"
      :on-change="handleUpload"
      :limit="1"
    >
      <el-icon class="el-icon--upload"><UploadFilled /></el-icon>
      <div class="el-upload__text">拖拽文件到此处，或<em>点击上传</em></div>
      <template #tip>
        <div class="el-upload__tip">仅支持 .docx 文件，最大 10MB</div>
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
      :loading="loading"
      @click="startTranslation"
      style="width: 100%;"
    >
      {{ loading ? '翻译中...' : '开始翻译' }}
    </el-button>

    <div v-if="taskId" style="margin-top: 20px;">
      <el-progress :percentage="progress" :status="status === 'failed' ? 'exception' : status === 'completed' ? 'success' : ''" />
      <p style="text-align: center; color: #666; margin-top: 8px;">
        <template v-if="status === 'pending'">等待中...</template>
        <template v-else-if="status === 'extracting'">正在提取文本...</template>
        <template v-else-if="status === 'translating'">正在翻译 ({{ progress }}%)...</template>
        <template v-else-if="status === 'building'">正在生成双语文档...</template>
        <template v-else-if="status === 'completed'">翻译完成</template>
        <template v-else-if="status === 'failed'">翻译失败</template>
      </p>
    </div>

    <el-button
      v-if="status === 'completed'"
      type="success"
      size="large"
      @click="handleDownload"
      style="width: 100%; margin-top: 10px;"
    >
      下载翻译结果
    </el-button>
  </div>
</template>

<style scoped>
.translate-page { padding: 20px 0; }
.upload-area { width: 100%; }
</style>
