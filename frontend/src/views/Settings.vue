<script setup>
import { ref, onMounted } from 'vue'
import { getConcurrencySettings, getLLMSettings } from '../api'

const config = ref({ provider: '', api_url: '', api_key_set: false, model: '' })
const concurrency = ref({})
const loading = ref(true)

onMounted(async () => {
  try {
    const [llmRes, concurrencyRes] = await Promise.all([
      getLLMSettings(),
      getConcurrencySettings(),
    ])
    config.value = llmRes.data
    concurrency.value = concurrencyRes.data
  } catch {}
  loading.value = false
})

function limitText(value) {
  return value == null ? '不限制' : value
}

function uploadModeText(value) {
  if (value === 'serial_files_and_chunks') return '前端文件与分片串行上传'
  return value || '-'
}
</script>

<template>
  <div class="settings-page">
    <h2>LLM 配置</h2>
    <p style="color: #909399; margin-bottom: 20px;">
      当前配置从后端 <code>.env</code> 文件加载，修改后需重启后端生效。
    </p>

    <el-descriptions v-loading="loading" :column="1" border class="settings-table">
      <el-descriptions-item label="Provider">{{ config.provider }}</el-descriptions-item>
      <el-descriptions-item label="API 地址">{{ config.api_url }}</el-descriptions-item>
      <el-descriptions-item label="API Key">
        <el-tag :type="config.api_key_set ? 'success' : 'danger'">
          {{ config.api_key_set ? '已配置' : '未配置' }}
        </el-tag>
      </el-descriptions-item>
      <el-descriptions-item label="模型">{{ config.model }}</el-descriptions-item>
    </el-descriptions>

    <h2 class="section-title">并发配置</h2>
    <el-descriptions v-loading="loading" :column="1" border class="settings-table">
      <el-descriptions-item label="每用户未完成任务上限">
        {{ limitText(concurrency.max_parallel_tasks_per_token) }}
      </el-descriptions-item>
      <el-descriptions-item label="全局翻译任务并发">
        {{ limitText(concurrency.max_concurrent_translations) }}
      </el-descriptions-item>
      <el-descriptions-item label="单任务批次并发">
        {{ concurrency.translation_batch_concurrency ?? '-' }}
      </el-descriptions-item>
      <el-descriptions-item label="最大同时大模型请求">
        {{ limitText(concurrency.max_simultaneous_llm_requests) }}
      </el-descriptions-item>
      <el-descriptions-item label="上传分片大小">
        {{ concurrency.upload_chunk_size_label || '-' }}
      </el-descriptions-item>
      <el-descriptions-item label="上传方式">
        {{ uploadModeText(concurrency.frontend_upload_mode) }}
      </el-descriptions-item>
    </el-descriptions>
  </div>
</template>

<style scoped>
.settings-page { padding: 20px 0; }
.settings-table { max-width: 640px; }
.section-title { margin-top: 28px; }
code { background: #f5f7fa; padding: 2px 6px; border-radius: 3px; }
</style>
