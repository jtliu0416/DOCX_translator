<script setup>
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listGlossaries, createGlossary, getGlossary, deleteGlossary } from '../api'

const glossaries = ref([])
const dialogVisible = ref(false)
const detailVisible = ref(false)
const detailTerms = ref([])
const detailName = ref('')
const uploading = ref(false)

// Upload form
const uploadForm = ref({ name: '', file: null })

onMounted(() => loadGlossaries())

async function loadGlossaries() {
  try {
    const res = await listGlossaries()
    glossaries.value = res.data
  } catch {}
}

function handleFileChange(file) {
  uploadForm.value.file = file.raw
}

async function handleUpload() {
  if (!uploadForm.value.name || !uploadForm.value.file) {
    ElMessage.warning('请填写名称并选择文件')
    return
  }

  uploading.value = true
  const formData = new FormData()
  formData.append('file', uploadForm.value.file)
  formData.append('name', uploadForm.value.name)

  try {
    await createGlossary(formData)
    ElMessage.success('术语表上传成功')
    dialogVisible.value = false
    uploadForm.value = { name: '', file: null }
    loadGlossaries()
  } catch (e) {
    ElMessage.error(e.response?.data?.detail || '上传失败')
  }
  uploading.value = false
}

async function showDetail(id) {
  try {
    const res = await getGlossary(id)
    detailName.value = res.data.name
    detailTerms.value = res.data.terms
    detailVisible.value = true
  } catch {
    ElMessage.error('加载失败')
  }
}

async function handleDelete(id) {
  try {
    await ElMessageBox.confirm('确定删除此术语表？', '确认')
    await deleteGlossary(id)
    ElMessage.success('已删除')
    loadGlossaries()
  } catch {}
}
</script>

<template>
  <div class="glossary-page">
    <div style="display: flex; justify-content: space-between; align-items: center;">
      <h2>术语表管理</h2>
      <el-button type="primary" @click="dialogVisible = true">上传术语表</el-button>
    </div>

    <el-table :data="glossaries" stripe>
      <el-table-column prop="name" label="名称" />
      <el-table-column prop="source_lang" label="源语言" width="100" />
      <el-table-column prop="target_lang" label="目标语言" width="100" />
      <el-table-column prop="term_count" label="术语数" width="100" />
      <el-table-column prop="created_at" label="创建时间" width="180" />
      <el-table-column label="操作" width="160">
        <template #default="{ row }">
          <el-button type="primary" link @click="showDetail(row.id)">查看</el-button>
          <el-button type="danger" link @click="handleDelete(row.id)">删除</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-empty v-if="glossaries.length === 0" description="暂无术语表" />

    <!-- Upload dialog -->
    <el-dialog v-model="dialogVisible" title="上传术语表" width="500">
      <el-form label-width="80px">
        <el-form-item label="名称">
          <el-input v-model="uploadForm.name" placeholder="术语表名称" />
        </el-form-item>
        <el-form-item label="文件">
          <el-upload
            :auto-upload="false"
            :limit="1"
            accept=".csv,.xlsx,.txt"
            :on-change="handleFileChange"
          >
            <el-button>选择文件</el-button>
            <template #tip>
              <div class="el-upload__tip">支持 CSV / XLSX / TXT 格式</div>
            </template>
          </el-upload>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="uploading" @click="handleUpload">上传</el-button>
      </template>
    </el-dialog>

    <!-- Detail dialog -->
    <el-dialog v-model="detailVisible" :title="detailName" width="600">
      <el-table :data="detailTerms" max-height="400">
        <el-table-column prop="source_term" label="原文术语" />
        <el-table-column prop="target_term" label="译文术语" />
        <el-table-column prop="note" label="备注" />
      </el-table>
    </el-dialog>
  </div>
</template>

<style scoped>
.glossary-page { padding: 20px 0; }
</style>
