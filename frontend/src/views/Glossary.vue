<script setup>
import { ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { listGlossaries, createGlossary, getGlossary, deleteGlossary } from '../api'

const allGlossaries = ref([])
const dialogVisible = ref(false)
const detailVisible = ref(false)
const detailTerms = ref([])
const detailName = ref('')
const detailIsBuiltin = ref(false)
const detailSearch = ref('')
const uploading = ref(false)
const uploadForm = ref({ name: '', file: null })

const builtinGlossaries = computed(() => allGlossaries.value.filter(g => g.is_builtin))
const userGlossaries = computed(() => allGlossaries.value.filter(g => !g.is_builtin))

const filteredTerms = computed(() => {
  const q = detailSearch.value.trim().toLowerCase()
  if (!q) return detailTerms.value
  return detailTerms.value.filter(t =>
    t.source_term.toLowerCase().includes(q) ||
    t.target_term.toLowerCase().includes(q) ||
    (t.note && t.note.toLowerCase().includes(q))
  )
})

onMounted(() => loadGlossaries())

async function loadGlossaries() {
  try {
    const res = await listGlossaries()
    allGlossaries.value = res.data
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

async function showDetail(g) {
  try {
    const res = await getGlossary(g.id)
    detailName.value = res.data.name + (g.is_builtin ? ` (${res.data.term_count} 条)` : '')
    detailTerms.value = res.data.terms
    detailIsBuiltin.value = g.is_builtin
    detailSearch.value = ''
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

    <!-- Built-in glossary section -->
    <div v-if="builtinGlossaries.length > 0" class="builtin-section">
      <div class="section-header builtin-header">
        <span class="section-title">内置术语表</span>
        <el-tag type="" size="small" effect="dark">系统</el-tag>
      </div>
      <div v-for="g in builtinGlossaries" :key="g.id" class="glossary-card builtin-card">
        <div class="card-info">
          <span class="card-name">{{ g.name }}</span>
          <span class="card-meta">{{ g.term_count }} 条术语 · {{ g.source_lang }}→{{ g.target_lang }}</span>
        </div>
        <el-button type="primary" size="small" @click="showDetail(g)">预览</el-button>
      </div>
    </div>

    <!-- User glossary section -->
    <div class="user-section">
      <div class="section-header user-header">
        <span class="section-title">我的术语表</span>
        <el-tag type="success" size="small" effect="dark">用户</el-tag>
      </div>
      <div v-for="g in userGlossaries" :key="g.id" class="glossary-card user-card">
        <div class="card-info">
          <span class="card-name">{{ g.name }}</span>
          <span class="card-meta">{{ g.term_count }} 条术语 · {{ g.created_at?.split('T')[0] }}</span>
        </div>
        <div class="card-actions">
          <el-button type="primary" size="small" link @click="showDetail(g)">查看</el-button>
          <el-button type="danger" size="small" link @click="handleDelete(g.id)">删除</el-button>
        </div>
      </div>
      <el-empty v-if="userGlossaries.length === 0" description="暂无用户术语表" />
    </div>

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

    <!-- Detail / Preview dialog -->
    <el-dialog v-model="detailVisible" :title="detailName" width="700">
      <el-input
        v-if="detailIsBuiltin"
        v-model="detailSearch"
        placeholder="搜索术语（中文/英文/备注）..."
        clearable
        style="margin-bottom: 12px;"
      />
      <el-table :data="detailIsBuiltin ? filteredTerms : detailTerms" max-height="500">
        <el-table-column type="index" label="#" width="50" />
        <el-table-column prop="source_term" label="中文" />
        <el-table-column prop="target_term" label="英文" />
        <el-table-column prop="note" label="备注" width="120" />
      </el-table>
      <div v-if="detailIsBuiltin && detailSearch" style="margin-top:8px;color:#909399;font-size:12px;">
        显示 {{ filteredTerms.length }} / {{ detailTerms.length }} 条
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.glossary-page { padding: 20px 0; }

.section-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 0;
  margin-bottom: 12px;
  border-bottom: 2px solid;
}
.builtin-header { border-color: #409eff; }
.user-header { border-color: #67c23a; }
.section-title { font-weight: bold; font-size: 15px; }

.builtin-section { margin-bottom: 24px; }

.glossary-card {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  border-radius: 6px;
  margin-bottom: 8px;
}
.builtin-card { background: #f5f7fa; border: 1px solid #e4e7ed; }
.user-card { background: #fff; border: 1px solid #e4e7ed; }

.card-info { display: flex; flex-direction: column; gap: 2px; }
.card-name { font-weight: 600; font-size: 14px; }
.card-meta { color: #909399; font-size: 12px; }
.card-actions { display: flex; gap: 4px; }
</style>
