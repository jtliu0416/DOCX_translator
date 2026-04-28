# V0.3 内置术语表 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Embed the 375-term biopharma glossary into Doctrans so users can enable it via a checkbox on the translate page and preview it on the glossary page.

**Architecture:** Database seed approach — add `is_builtin` column to `glossaries` table and `use_builtin_glossary` column to `translation_tasks`. Seed 375 terms at startup. Merge built-in + user glossary terms in the translation pipeline, with user terms overriding built-in on conflict.

**Tech Stack:** FastAPI, aiosqlite, Vue 3, Element Plus

---

## Task 1: Database Schema Migration + Seed Data

**Files:**
- Modify: `backend/app/database.py`
- Create: `backend/app/services/builtin_glossary.py`

- [ ] **Step 1: Generate the terms data file from the XLSX source**

The 375 terms come from `翻译指定对照表.xlsx` (7 sheets). Run this script to generate `backend/app/services/builtin_terms_data.py`:

```bash
cd "D:/AI/Doctrans" && python -c "
import openpyxl

wb = openpyxl.load_workbook('翻译指定对照表.xlsx')

def add(terms, source, target, note):
    source = str(source).strip() if source else ''
    target = str(target).strip() if target else ''
    note = str(note).strip() if note else ''
    if source and target and source != 'None' and target != 'None':
        terms.append((source, target, note or None))

all_terms = []

ws = wb['Sheet1']
for row in ws.iter_rows(min_row=5, max_row=20, values_only=True):
    add(all_terms, row[2], row[3], None)

ws = wb['CLD']
for row in ws.iter_rows(min_row=3, values_only=True):
    add(all_terms, row[0], row[1], row[2] if len(row) > 2 else None)

ws = wb['CCPD']
for row in ws.iter_rows(min_row=2, values_only=True):
    add(all_terms, row[0], row[1], None)

ws = wb['FD']
for row in ws.iter_rows(min_row=2, values_only=True):
    note_val = row[4] if len(row) > 4 and row[4] else (row[1] if len(row) > 1 and row[1] else None)
    add(all_terms, row[2], row[3], note_val)

ws = wb['AD']
for row in ws.iter_rows(min_row=2, max_row=57, values_only=True):
    add(all_terms, row[0], row[1], None)

ws = wb['BCD']
for row in ws.iter_rows(min_row=2, values_only=True):
    add(all_terms, row[0], row[1], row[2] if len(row) > 2 else None)

ws = wb['MFG']
for row in ws.iter_rows(min_row=3, max_row=150, values_only=True):
    add(all_terms, row[0], row[1], None)

seen = {}
for source, target, note in all_terms:
    key = source.lower()
    if key not in seen:
        seen[key] = (source, target, note)
    elif note and not seen[key][2]:
        seen[key] = (source, target, note)

deduped = sorted(seen.values(), key=lambda x: x[0])

with open('backend/app/services/builtin_terms_data.py', 'w', encoding='utf-8') as f:
    f.write('\"\"\"Auto-generated built-in glossary terms data.\"\"\"\\n\\n')
    f.write('BUILTIN_TERMS = [\\n')
    for s, t, n in deduped:
        n_str = repr(n) if n else 'None'
        f.write(f'    ({repr(s)}, {repr(t)}, {n_str}),\\n')
    f.write(']\\n')

print(f'Written {len(deduped)} terms')
"
```

Expected output: `Written 375 terms`

- [ ] **Step 2: Create the seed module**

Create `backend/app/services/builtin_glossary.py` with the seed function:

```python
"""Built-in biopharma glossary seed data and initialization."""

from .builtin_terms_data import BUILTIN_TERMS

BUILTIN_GLOSSARY_ID = "builtin-biopharma-zh-en"
BUILTIN_GLOSSARY_NAME = "生物制药专业术语对照表"


async def seed_builtin_glossary(db):
    """Insert built-in glossary if not already present."""
    cursor = await db.execute(
        "SELECT COUNT(*) as cnt FROM glossaries WHERE is_builtin = 1"
    )
    row = await cursor.fetchone()
    if row["cnt"] > 0:
        return

    await db.execute(
        """INSERT INTO glossaries (id, token, name, source_lang, target_lang, file_path, term_count, is_builtin)
        VALUES (?, NULL, ?, 'zh', 'en', NULL, ?, 1)""",
        (BUILTIN_GLOSSARY_ID, BUILTIN_GLOSSARY_NAME, len(BUILTIN_TERMS)),
    )

    for source, target, note in BUILTIN_TERMS:
        await db.execute(
            "INSERT INTO glossary_terms (glossary_id, source_term, target_term, note) VALUES (?, ?, ?, ?)",
            (BUILTIN_GLOSSARY_ID, source, target, note),
        )

    await db.commit()
```

- [ ] **Step 3: Add schema migration and seed call to database.py**

In `backend/app/database.py`, add `is_builtin` and `use_builtin_glossary` columns via migration, and call the seed function in `init_db()`:

Change the `MIGRATIONS` list from empty to contain two ALTER TABLE statements:

```python
MIGRATIONS = [
    "ALTER TABLE glossaries ADD COLUMN is_builtin INTEGER DEFAULT 0",
    "ALTER TABLE translation_tasks ADD COLUMN use_builtin_glossary INTEGER DEFAULT 0",
]
```

Change `init_db()` to run migrations and seed:

```python
async def init_db():
    db = await get_db()
    await db.executescript(SCHEMA)
    # Run migrations (ALTER TABLE is idempotent-safe with try/except)
    for sql in MIGRATIONS:
        try:
            await db.execute(sql)
        except Exception:
            pass  # Column already exists
    await db.commit()
    await db.close()

    # Seed built-in glossary
    from .services.builtin_glossary import seed_builtin_glossary
    db = await get_db()
    await seed_builtin_glossary(db)
    await db.close()
```

- [ ] **Step 4: Verify backend starts and seeds correctly**

Run:
```bash
cd backend && rm -f doctrans.db && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 &
sleep 3
curl -s http://localhost:8000/api/glossaries | python -m json.tool
```

Expected: Response includes the built-in glossary with `is_builtin: true` and `term_count: 375`.

- [ ] **Step 5: Commit**

```bash
git add backend/app/database.py backend/app/services/builtin_glossary.py backend/app/services/builtin_terms_data.py
git commit -m "feat: add database migration and built-in glossary seed data"
```

---

## Task 2: Backend API Changes

**Files:**
- Modify: `backend/app/api/glossaries.py`
- Modify: `backend/app/api/tasks.py`

- [ ] **Step 1: Modify `list_glossaries` to include built-in glossaries**

In `backend/app/api/glossaries.py`, change the `list_glossaries` SQL to also return built-in glossaries and add `is_builtin` to the response:

Replace the query in `list_glossaries`:

```python
cursor = await db.execute(
    """SELECT id, name, source_lang, target_lang, term_count, created_at, is_builtin
       FROM glossaries
       WHERE token = ? OR is_builtin = 1
       ORDER BY is_builtin DESC, created_at DESC""",
    (token,),
)
```

Add `is_builtin` to the response dict:

```python
return [{
    "id": r["id"],
    "name": r["name"],
    "source_lang": r["source_lang"],
    "target_lang": r["target_lang"],
    "term_count": r["term_count"],
    "created_at": r["created_at"],
    "is_builtin": bool(r["is_builtin"]),
} for r in rows]
```

- [ ] **Step 2: Modify `get_glossary` to return all terms for built-in glossaries**

In `get_glossary`, change the terms query to remove the LIMIT 50 for built-in glossaries. First, fetch the glossary row to check `is_builtin`:

```python
cursor = await db.execute(
    "SELECT id, name, source_lang, target_lang, term_count, created_at, is_builtin FROM glossaries WHERE id = ?",
    (glossary_id,),
)
row = await cursor.fetchone()

if not row:
    await db.close()
    raise HTTPException(404, "术语表不存在")

limit_clause = "" if row["is_builtin"] else " LIMIT 50"
cursor = await db.execute(
    f"SELECT source_term, target_term, note FROM glossary_terms WHERE glossary_id = ?{limit_clause}",
    (glossary_id,),
)
terms = await cursor.fetchall()
await db.close()
```

Add `is_builtin` to the response:

```python
return {
    "id": row["id"],
    "name": row["name"],
    "source_lang": row["source_lang"],
    "target_lang": row["target_lang"],
    "term_count": row["term_count"],
    "created_at": row["created_at"],
    "is_builtin": bool(row["is_builtin"]),
    "terms": [{
        "source_term": t["source_term"],
        "target_term": t["target_term"],
        "note": t["note"],
    } for t in terms],
}
```

- [ ] **Step 3: Protect built-in glossaries from deletion**

In `delete_glossary`, add a check after fetching the row:

```python
cursor = await db.execute("SELECT file_path, is_builtin FROM glossaries WHERE id = ?", (glossary_id,))
row = await cursor.fetchone()

if not row:
    await db.close()
    raise HTTPException(404, "术语表不存在")

if row["is_builtin"]:
    await db.close()
    raise HTTPException(403, "内置术语表不可删除")
```

- [ ] **Step 4: Modify `create_task` to accept `use_builtin_glossary`**

In `backend/app/api/tasks.py`, add the new form parameter to `create_task`:

```python
async def create_task(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    source_lang: str = Form("zh"),
    target_lang: str = Form("en"),
    glossary_id: Optional[str] = Form(None),
    use_builtin_glossary: str = Form("false"),
):
```

Add `use_builtin_glossary` to the INSERT:

```python
await db.execute(
    """INSERT INTO translation_tasks
    (id, token, original_filename, original_path, glossary_id, source_lang, target_lang, status, expires_at, use_builtin_glossary)
    VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
    (task_id, token, file.filename, original_path, glossary_id, source_lang, target_lang, expires_at, 1 if use_builtin_glossary == "true" else 0),
)
```

- [ ] **Step 5: Modify `run_translation` to pass `use_builtin` to translator**

In `run_translation`, read the new column and pass it:

```python
cursor = await db.execute(
    "SELECT original_path, glossary_id, use_builtin_glossary, status FROM translation_tasks WHERE id = ?",
    (task_id,),
)
row = await cursor.fetchone()
await db.close()

if not row or row["status"] not in ("pending", "failed"):
    return

original_path = row["original_path"]
glossary_id = row["glossary_id"]
use_builtin = bool(row["use_builtin_glossary"])
```

Then change the `translate_all` call:

```python
translations = await translate_all(units, glossary_id, task_id, use_builtin=use_builtin)
```

- [ ] **Step 6: Verify API changes work**

Run:
```bash
# Start backend (restart if running)
cd backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 &
sleep 3

# List glossaries — should show built-in
curl -s -b "token=test123" http://localhost:8000/api/glossaries | python -m json.tool

# Try deleting built-in — should get 403
curl -s -X DELETE -b "token=test123" http://localhost:8000/api/glossaries/builtin-biopharma-zh-en | python -m json.tool

# Get built-in detail — should return 375 terms
curl -s -b "token=test123" http://localhost:8000/api/glossaries/builtin-biopharma-zh-en | python -c "import sys,json; d=json.load(sys.stdin); print(f'Terms: {len(d[\"terms\"])}, builtin: {d[\"is_builtin\"]}')"
```

Expected: 375 terms, `is_builtin: true`, delete returns 403.

- [ ] **Step 7: Commit**

```bash
git add backend/app/api/glossaries.py backend/app/api/tasks.py
git commit -m "feat: API support for built-in glossary (list, detail, delete protection, task param)"
```

---

## Task 3: Translation Pipeline — Glossary Merge Logic

**Files:**
- Modify: `backend/app/services/translator.py`

- [ ] **Step 1: Add `use_builtin` parameter to `translate_all`**

In `backend/app/services/translator.py`, change the `translate_all` function signature and add merge logic:

```python
async def translate_all(
    units: list[dict],
    glossary_id: Optional[str] = None,
    task_id: str = "",
    use_builtin: bool = False,
) -> list[dict]:
    to_translate = [u for u in units if not u.get("skip", False)]
    total = len(to_translate)

    if total == 0:
        return []

    # Collect glossary terms
    glossary_terms = None
    if glossary_id or use_builtin:
        glossary_terms = await _merge_glossary_terms(glossary_id, use_builtin)

    await _update_task_progress(task_id, status="translating", total=total)

    # ... rest unchanged, just replace `glossary_terms` usage
```

Remove the old line:
```python
    glossary_terms = await get_glossary_terms(glossary_id) if glossary_id else None
```

- [ ] **Step 2: Add the `_merge_glossary_terms` helper function**

Add this new function before `translate_all`:

```python
async def _merge_glossary_terms(glossary_id: Optional[str], use_builtin: bool) -> list[dict]:
    """Merge built-in and user glossary terms, with user terms taking priority."""
    builtin_terms = []
    user_terms = []

    if use_builtin:
        builtin_terms = await get_glossary_terms("builtin-biopharma-zh-en")

    if glossary_id:
        user_terms = await get_glossary_terms(glossary_id)

    if not builtin_terms:
        return user_terms
    if not user_terms:
        return builtin_terms

    # Merge: user terms override built-in on conflict (by source term)
    user_sources = {t["source"].lower(): t for t in user_terms}
    merged = list(user_terms)
    for bt in builtin_terms:
        if bt["source"].lower() not in user_sources:
            merged.append(bt)
    merged.sort(key=lambda t: len(t["source"]), reverse=True)
    return merged
```

- [ ] **Step 3: Verify with a manual test**

Run backend, upload a DOCX with both built-in enabled and a user glossary, check that the translation completes successfully. You can test via the frontend after Task 5 is done, or test the function directly:

```bash
cd backend && python -c "
import asyncio
from app.database import init_db
from app.services.translator import _merge_glossary_terms

async def test():
    await init_db()
    terms = await _merge_glossary_terms(None, True)
    print(f'Built-in terms only: {len(terms)}')
    assert len(terms) == 375

asyncio.run(test())
"
```

Expected: `Built-in terms only: 375`

- [ ] **Step 4: Commit**

```bash
git add backend/app/services/translator.py
git commit -m "feat: merge built-in + user glossary terms with user priority"
```

---

## Task 4: Frontend — Glossary Page Redesign

**Files:**
- Modify: `frontend/src/views/Glossary.vue`

- [ ] **Step 1: Rewrite Glossary.vue with built-in section and search-enabled preview**

Replace the entire content of `frontend/src/views/Glossary.vue` with:

```vue
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
```

- [ ] **Step 2: Verify glossary page renders correctly**

Run:
```bash
cd frontend && npm run dev
```

Open the glossary page in browser. Expected:
- Blue "内置术语表" section shows the biopharma glossary card with "预览" button
- Green "我的术语表" section shows user glossaries with "查看" and "删除" buttons
- Clicking "预览" opens a dialog with search box and all 375 terms

- [ ] **Step 3: Commit**

```bash
git add frontend/src/views/Glossary.vue
git commit -m "feat: glossary page with built-in section and search-enabled preview"
```

---

## Task 5: Frontend — Translate Page Glossary Selection

**Files:**
- Modify: `frontend/src/views/Translate.vue`

- [ ] **Step 1: Add built-in glossary checkbox and split glossary data**

In `frontend/src/views/Translate.vue`, modify the `<script setup>` section.

Add new refs after the existing `glossaries` ref:

```javascript
const useBuiltinGlossary = ref(true)
const builtinGlossary = ref(null)
const builtinPreviewVisible = ref(false)
const builtinTerms = ref([])
const builtinSearch = ref('')

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
```

Add `computed` to the import:
```javascript
import { ref, computed, onMounted, onUnmounted } from 'vue'
```

Modify the `onMounted` to extract built-in glossary info:

```javascript
onMounted(async () => {
  try {
    const res = await listGlossaries()
    glossaries.value = res.data
    builtinGlossary.value = res.data.find(g => g.is_builtin) || null
  } catch {}
})
```

Add a preview function:

```javascript
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
```

Add `getGlossary` to the API import:

```javascript
import { createTask, getTask, downloadTask, retryTask, listGlossaries, getGlossary } from '../api'
```

- [ ] **Step 2: Add `use_builtin_glossary` to form submission**

In `startTranslation()`, add the new field after the existing `glossary_id` append:

```javascript
    if (glossaryId.value) {
      formData.append('glossary_id', glossaryId.value)
    }
    formData.append('use_builtin_glossary', useBuiltinGlossary.value ? 'true' : 'false')
```

- [ ] **Step 3: Replace the glossary form-item in the template**

Replace the existing `<el-form-item label="术语表">` block with:

```html
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
```

Add the preview dialog before the closing `</template>`:

```html
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
```

Add the CSS for the new row layout:

```css
.builtin-glossary-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
```

- [ ] **Step 4: Verify translate page works end-to-end**

Run frontend dev server and backend. Test:

1. Open the translate page — built-in glossary checkbox should appear, checked by default
2. Click "预览" — should open dialog with 375 terms and search
3. Upload a user glossary on the glossary page
4. Back on translate page — user glossary appears in dropdown (built-in glossary does NOT appear in dropdown)
5. Submit a translation with built-in checkbox checked + user glossary selected — should complete successfully
6. Submit with built-in unchecked — should complete without built-in terms

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/Translate.vue
git commit -m "feat: translate page with built-in glossary checkbox and user glossary dropdown"
```

---

## Task 6: Cleanup and Final Integration Test

**Files:**
- Delete temporary: `backend/app/services/builtin_terms_data.py` (already committed in Task 1, keep it)

- [ ] **Step 1: Delete old test database and restart fresh**

```bash
cd backend && rm -f doctrans.db && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 &
```

- [ ] **Step 2: Full end-to-end test**

1. Open `http://localhost:8000` in browser
2. Navigate to "术语表" page — verify built-in glossary appears with 375 terms
3. Click "预览" — verify all 375 terms load, search works
4. Navigate to "翻译" page — verify built-in checkbox is checked by default
5. Click "预览" on translate page — verify same preview works
6. Upload a DOCX file and translate with built-in enabled — verify success
7. Uncheck built-in, upload same file — verify translation completes (without glossary terms)
8. Try deleting the built-in glossary from glossary page — should not be possible (no delete button shown)

- [ ] **Step 3: Commit any remaining changes**

```bash
git add -A
git status  # review any unexpected files
git commit -m "chore: V0.3 built-in glossary feature complete"
```
