# V0.3 内置术语表设计文档

## 概述

将整理好的 375 条生物制药专业术语（翻译指定对照表）内置到 Doctrans 程序中。用户可在翻译时选择启用内置术语表，也可以同时叠加自己上传的术语表。用户上传术语表的原有功能保持不变。

## 需求摘要

1. 内置术语表在术语表页面（`/glossary`）可预览，支持全量展示和关键词搜索
2. 翻译页面（`/`）提供复选框让用户选择是否启用内置术语表，默认勾选
3. 内置术语表和用户术语表可同时使用，合并后注入 LLM prompt
4. 冲突术语（同一中文词有不同英文翻译）以用户术语为准
5. 用户上传术语表的原有功能完全保留

## 数据存储方案

### 方案选择：数据库种子（方案 A）

在现有 `glossaries` + `glossary_terms` 表中存入内置术语表，新增 `is_builtin` 字段区分内置和用户术语表。

选择理由：复用现有 `get_glossary_terms()`、`build_prompt()` 全链路查询逻辑，改动最小，不需要维护两套代码路径。

### 数据库 Schema 变更

`glossaries` 表新增列：

```sql
ALTER TABLE glossaries ADD COLUMN is_builtin INTEGER DEFAULT 0;
```

- `is_builtin=1`：内置术语表，`token` 为 NULL（所有用户共享）
- `is_builtin=0`：用户上传术语表，`token` 为用户 cookie UUID

`translation_tasks` 表新增列：

```sql
ALTER TABLE translation_tasks ADD COLUMN use_builtin_glossary INTEGER DEFAULT 0;
```

- 原有 `glossary_id` 字段保留，继续存储用户术语表 ID
- 新字段记录本次翻译是否启用了内置术语表

### 种子数据

新增文件 `backend/app/services/builtin_glossary.py`：
- 包含一个 Python list，直接硬编码 375 条术语数据
- 每条术语格式：`{"source_term": "中文", "target_term": "英文", "note": "备注或None"}`
- 提供函数 `seed_builtin_glossary(db)` 在应用启动时调用

在 `database.py` 的 `init_db()` 中新增逻辑：
- 查询 `glossaries WHERE is_builtin=1` 的记录数
- 若为 0，调用种子函数插入内置术语表（glossary 行 + 375 条 glossary_terms 行）
- 内置术语表的 ID 使用固定值（如 `"builtin-biopharma-zh-en"`），便于查询

## API 变更

### 修改现有端点

**`GET /api/glossaries`** — `glossaries.py:list_glossaries`
- 修改查询逻辑：返回当前用户的术语表 **加上** 所有 `is_builtin=1` 的术语表
- 返回结构新增 `is_builtin: bool` 字段

**`GET /api/glossaries/{id}`** — `glossaries.py:get_glossary`
- 对内置术语表：不限制 50 条，返回全量术语（375 条一次返回）
- 前端拿到全量数据后本地搜索过滤，无需后端 search 参数

**`DELETE /api/glossaries/{id}`** — `glossaries.py:delete_glossary`
- 新增检查：若 `is_builtin=1`，返回 HTTP 403，拒绝删除

**`POST /api/tasks`** — `tasks.py:create_task`
- 新增表单字段 `use_builtin_glossary`（可选，默认 "false"）
- 存入 `translation_tasks.use_builtin_glossary` 列

### 新增端点

无需新增独立端点。内置术语表通过修改后的 `GET /api/glossaries` 列表接口和 `GET /api/glossaries/{id}` 详情接口即可覆盖所有场景（预览、统计信息）。前端通过 `is_builtin` 字段区分展示。

## 翻译管线变更

### `translator.py`

**`translate_all(units, glossary_id, task_id, use_builtin=False)`**
- 新增 `use_builtin` 参数
- 若 `use_builtin=True`，查询内置术语表获取内置术语列表
- 若 `glossary_id` 有值，查询用户术语表获取用户术语列表
- 合并两个列表，去重逻辑：以 `(source_term)` 为 key，用户术语覆盖内置术语
- 将合并后的术语列表传入 `build_prompt()`

**`build_prompt(batch, glossary_terms)`** — 无变更
- 现有逻辑已能处理任意长度的术语列表，无需修改

### `tasks.py:run_translation()`
- 从 `translation_tasks` 行中同时读取 `glossary_id` 和 `use_builtin_glossary`
- 将 `use_builtin` 传入 `translate_all()`

## 前端变更

### Glossary.vue — 术语表页面

页面分为两个区域：

1. **内置术语表区域**（蓝色主题）
   - 展示内置术语表卡片：名称、术语条数、语言对
   - "预览"按钮：点击打开预览弹窗
   - 不可删除、不可编辑

2. **我的术语表区域**（绿色主题，保留原有功能）
   - 保留原有的上传、预览、删除功能
   - 上传对话框不变

**预览弹窗**（内置术语表专用）：
- 弹窗标题显示术语表名称和总条数
- 顶部搜索框：实时过滤中文/英文关键词
- 表格展示：序号 | 中文 | 英文 | 备注
- 全量加载 375 条，前端搜索过滤（无需每次请求后端）
- 虚拟滚动或懒加载（如果性能需要）

### Translate.vue — 翻译页面

术语表选择区域改为两部分：

1. **内置术语表复选框**
   - `<el-checkbox>` 默认勾选
   - 标签文字："生物制药专业术语对照表 (375 条)"
   - 旁边"预览"按钮（复用 Glossary.vue 的预览弹窗组件）
   - 绑定到 `useBuiltinGlossary` ref

2. **用户术语表下拉框**
   - 保留原有 `<el-select>` 下拉选择
   - 只显示用户自己上传的术语表（过滤掉内置的）
   - 绑定到 `glossaryId` ref

**提交逻辑**：
- FormData 中新增 `use_builtin_glossary` 字段（"true" / "false"）
- `glossary_id` 字段保持不变

### api/index.js

无需新增 API 函数。复用现有 `listGlossaries()` 和 `getGlossary(id)` 接口：
- `listGlossaries()` 返回结果中新增 `is_builtin` 字段，前端按此字段分区展示
- `getGlossary(builtin_id)` 返回内置术语表全量术语，前端本地搜索

## 不在范围内

- 术语表编辑功能（内置和用户都不支持编辑）
- 多语言方向支持（内置术语表仅 zh→en）
- 术语表版本管理
- 后台管理界面
- 术语相关性过滤（所有术语全量注入 prompt，不做批次级别过滤）

## 涉及文件清单

| 文件 | 变更类型 |
|------|----------|
| `backend/app/database.py` | 修改：schema migration + 种子调用 |
| `backend/app/services/builtin_glossary.py` | 新增：375 条术语数据 + seed 函数 |
| `backend/app/api/glossaries.py` | 修改：list/get/delete 适配内置术语表 |
| `backend/app/api/tasks.py` | 修改：create_task 和 run_translation 支持 use_builtin |
| `backend/app/services/translator.py` | 修改：合并术语逻辑 |
| `frontend/src/views/Glossary.vue` | 修改：分区展示 + 内置预览弹窗 |
| `frontend/src/views/Translate.vue` | 修改：复选框 + 下拉框组合 |
| `frontend/src/api/index.js` | 无变更（复用现有 listGlossaries / getGlossary） |
