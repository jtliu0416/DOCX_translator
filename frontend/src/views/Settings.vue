<script setup>
import { ref, onMounted } from 'vue'

const config = ref({ provider: '', api_url: '', api_key_set: false, model: '' })
const loading = ref(true)

onMounted(async () => {
  try {
    const res = await fetch('/api/settings/llm')
    config.value = (await res.json())
  } catch {}
  loading.value = false
})
</script>

<template>
  <div class="settings-page">
    <h2>LLM 配置</h2>
    <p style="color: #909399; margin-bottom: 20px;">
      当前配置从后端 <code>.env</code> 文件加载，修改后需重启后端生效。
    </p>

    <el-descriptions v-loading="loading" :column="1" border style="max-width: 560px;">
      <el-descriptions-item label="Provider">{{ config.provider }}</el-descriptions-item>
      <el-descriptions-item label="API 地址">{{ config.api_url }}</el-descriptions-item>
      <el-descriptions-item label="API Key">
        <el-tag :type="config.api_key_set ? 'success' : 'danger'">
          {{ config.api_key_set ? '已配置' : '未配置' }}
        </el-tag>
      </el-descriptions-item>
      <el-descriptions-item label="模型">{{ config.model }}</el-descriptions-item>
    </el-descriptions>
  </div>
</template>

<style scoped>
.settings-page { padding: 20px 0; }
code { background: #f5f7fa; padding: 2px 6px; border-radius: 3px; }
</style>
