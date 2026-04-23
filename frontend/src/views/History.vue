<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listTasks, downloadTask, deleteTask } from '../api'

const tasks = ref([])
const total = ref(0)
const page = ref(1)
const pageSize = ref(20)
const loading = ref(false)

onMounted(() => loadTasks())

async function loadTasks() {
  loading.value = true
  try {
    const res = await listTasks(page.value, pageSize.value)
    tasks.value = res.data.items
    total.value = res.data.total
  } catch {
    ElMessage.error('加载失败')
  }
  loading.value = false
}

function statusText(s) {
  const map = {
    pending: '等待中', extracting: '提取文本', translating: '翻译中',
    building: '生成文档', completed: '已完成', failed: '失败',
  }
  return map[s] || s
}

function statusType(s) {
  if (s === 'completed') return 'success'
  if (s === 'failed') return 'danger'
  return 'primary'
}

async function handleDownload(taskId, filename) {
  try {
    const res = await downloadTask(taskId)
    const url = URL.createObjectURL(res.data)
    const a = document.createElement('a')
    a.href = url
    a.download = `translated_${filename}`
    a.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '下载失败')
  }
}

async function handleDelete(taskId) {
  try {
    await ElMessageBox.confirm('确定删除此任务？', '确认')
    await deleteTask(taskId)
    ElMessage.success('已删除')
    loadTasks()
  } catch {}
}
</script>

<template>
  <div class="history-page">
    <h2>历史记录</h2>

    <el-table :data="tasks" v-loading="loading" stripe>
      <el-table-column prop="original_filename" label="文件名" min-width="200" />
      <el-table-column label="状态" width="120">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)" size="small">{{ statusText(row.status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="进度" width="100">
        <template #default="{ row }">
          {{ row.status === 'completed' ? '100' : row.progress }}%
        </template>
      </el-table-column>
      <el-table-column prop="created_at" label="创建时间" width="180" />
      <el-table-column label="操作" width="160" fixed="right">
        <template #default="{ row }">
          <el-button
            v-if="row.status === 'completed'"
            type="primary"
            link
            @click="handleDownload(row.task_id, row.original_filename)"
          >下载</el-button>
          <el-button type="danger" link @click="handleDelete(row.task_id)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      v-if="total > pageSize"
      layout="prev, pager, next"
      :total="total"
      :page-size="pageSize"
      v-model:current-page="page"
      @current-change="loadTasks"
      style="margin-top: 16px;"
    />

    <el-empty v-if="!loading && tasks.length === 0" description="暂无翻译记录">
      <el-button type="primary" @click="$router.push('/')">去翻译</el-button>
    </el-empty>
  </div>
</template>

<style scoped>
.history-page { padding: 20px 0; }
</style>
