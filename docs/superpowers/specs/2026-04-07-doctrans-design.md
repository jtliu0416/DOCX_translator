# Doctrans - 文档翻译平台设计文档

## 概述

Doctrans 是一个 Web 文档翻译平台，支持 DOCX 文件上传、LLM 驱动翻译、术语表增强、翻译结果下载及历史记录管理。面向个人或小团队使用，无需用户登录。

**翻译模式：生成中英双语版本**，在原文基础上插入英文翻译，保持原文不变。

## 技术栈

| 层 | 技术 | 职责 |
|---|---|---|
| Web/API 层 | FastAPI (Python) | 路由、任务管理、LLM 调用、文件管理 |
| DOCX 处理层 | minimax-docx (.NET CLI, OpenXML SDK) | 解析文档、提取文本、插入双语内容、验证格式 |
| 前端 | Vue 3 + Vite + Element Plus | 上传、进度、下载、历史记录、术语表管理 |
| 存储 | SQLite + 本地磁盘 | 任务元数据 + 文件存储 |

## 整体架构

```
用户浏览器 (Vue 3)
    │
    ├── 上传 DOCX ──→ FastAPI 后端
    │                     │
    │                  Step 1: 自定义 CSX 脚本解析 DOCX
    │                  → 提取所有文本单元（段落/表格单元格）
    │                  → 每个单元标记类型：heading / paragraph / table_cell
    │                  → 检测已翻译内容（中英混合），标记为 skip
    │                  → 输出 structured_paragraphs.json
    │                     │
    │                  Step 2: Python 逐批调用 LLM API 翻译
    │                  → 仅翻译含中文且未标记 skip 的文本单元
    │                  → 注入术语表到 Prompt 提升准确性
    │                     │
    │                  Step 3: 自定义 CSX 脚本生成双语 DOCX
    │                  → 标题：同一段落内，中文后空格 + 英文翻译
    │                  → 正文：在中文段落后插入新段落（英文翻译）
    │                  → 表格单元格：单元格内换行 + 英文翻译
    │                  → 英文部分字体设为 Arial，其他样式继承原文
    │                     │
    │                  Step 4: .NET CLI validate 验证输出文件完整性
    │                     │
    ├── 轮询进度 ←── SQLite（任务状态）
    ├── 下载译文 ←── 本地文件系统
    └── 查看历史 ←── SQLite（元数据查询）
```

### Python 与 .NET 集成

FastAPI 通过 `subprocess` 调用 .NET CLI 工具。由于双语插入涉及段落新增、单元格换行等复杂操作，核心 DOCX 处理通过自定义 CSX 脚本实现（提取文本、插入双语内容），不使用简单的 replace-text 命令。

## 数据模型

### SQLite 表结构

```sql
-- 翻译任务表
CREATE TABLE translation_tasks (
    id TEXT PRIMARY KEY,              -- UUID
    token TEXT NOT NULL,              -- 临时用户标识 (UUID, 存入 cookie)
    original_filename TEXT NOT NULL,  -- 原始文件名
    original_path TEXT NOT NULL,      -- 原始文件存储路径
    result_path TEXT,                 -- 翻译结果文件路径
    glossary_id TEXT,                 -- 关联的术语表 ID（可选）
    source_lang TEXT DEFAULT 'zh',    -- 源语言
    target_lang TEXT DEFAULT 'en',    -- 目标语言
    status TEXT DEFAULT 'pending',    -- pending/extracting/translating/building/completed/failed
    progress INTEGER DEFAULT 0,       -- 翻译进度 0-100
    total_paragraphs INTEGER DEFAULT 0,
    translated_paragraphs INTEGER DEFAULT 0,
    error_message TEXT,               -- 失败原因
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    expires_at TIMESTAMP              -- 7天后过期
);

-- 术语表
CREATE TABLE glossaries (
    id TEXT PRIMARY KEY,              -- UUID
    token TEXT NOT NULL,              -- 关联用户 token
    name TEXT NOT NULL,               -- 术语表名称
    source_lang TEXT NOT NULL,        -- 源语言
    target_lang TEXT NOT NULL,        -- 目标语言
    file_path TEXT NOT NULL,          -- 存储路径
    term_count INTEGER DEFAULT 0,     -- 术语条数
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 术语条目
CREATE TABLE glossary_terms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    glossary_id TEXT NOT NULL,
    source_term TEXT NOT NULL,        -- 原文术语
    target_term TEXT NOT NULL,        -- 译文术语
    note TEXT,                        -- 备注（可选）
    FOREIGN KEY (glossary_id) REFERENCES glossaries(id)
);

-- 索引
CREATE INDEX idx_tasks_token ON translation_tasks(token);
CREATE INDEX idx_tasks_expires ON translation_tasks(expires_at);
CREATE INDEX idx_glossaries_token ON glossaries(token);
CREATE INDEX idx_terms_glossary ON glossary_terms(glossary_id);
```

### 文件存储结构

```
backend/
├── uploads/
│   └── {task_id}/
│       ├── original.docx        # 用户上传的原始文件
│       └── paragraphs.json      # 提取的文本段落
├── results/
│   └── {task_id}/
│       └── translated.docx      # 翻译后的文件
├── glossaries/
│   └── {glossary_id}/
│       └── original.xlsx        # 用户上传的原始术语表文件
```

### Token 机制

- 用户首次访问时，后端生成 UUID token，通过 `Set-Cookie` 写入浏览器
- 后续请求自动携带 cookie，后端通过 token 查询该用户的历史记录和术语表
- Token 有效期 30 天，过期后重新生成
- 无需登录，token 即用户身份

## API 设计

### 翻译任务

```
POST   /api/tasks                  上传文件，创建翻译任务
GET    /api/tasks                  查询当前 token 下的所有历史任务（分页）
GET    /api/tasks/{id}             查询单个任务详情（含进度，用于轮询）
GET    /api/tasks/{id}/download    下载翻译结果（仅 completed 状态）
DELETE /api/tasks/{id}             删除任务及文件
```

**POST /api/tasks**
- Request: `multipart/form-data` — `file` (DOCX), `source_lang`, `target_lang`, `glossary_id`（可选）
- Response: `{ "task_id": "uuid", "status": "pending" }`
- Limit: 文件最大 10MB

**GET /api/tasks**
- Query: `?page=1&page_size=20`
- Response: `{ "total": 15, "items": [...] }`
- 每个 item 包含：task_id, original_filename, source_lang, target_lang, status, progress, created_at, completed_at

**GET /api/tasks/{id}**
- Response: `{ "task_id", "status", "progress", "total_paragraphs", "translated_paragraphs" }`
- 前端每 2 秒轮询一次，completed/failed 时停止

**GET /api/tasks/{id}/download**
- Response: DOCX 文件流 (`Content-Disposition: attachment`)
- 仅 status=completed 时可下载

### 术语表

```
POST   /api/glossaries              上传术语表文件
GET    /api/glossaries              查询当前 token 下的术语表列表
GET    /api/glossaries/{id}         查看术语表详情（含条目预览）
DELETE /api/glossaries/{id}         删除术语表
```

**POST /api/glossaries**
- Request: `multipart/form-data` — `file` (CSV/XLSX/TXT), `name`, `source_lang`, `target_lang`
- 后端解析文件，将条目存入 `glossary_terms` 表

### 其他

```
GET    /api/languages              获取支持的语言列表
```

## 前端设计

### 技术选型

- Vue 3 + Composition API + `<script setup>`
- Element Plus（表格、上传、进度条、消息提示）
- Axios
- Vue Router

### 页面（共 4 个）

#### 1. 翻译页（首页） `/`

上传 DOCX 文件，选择源/目标语言，可选关联术语表，开始翻译。

交互流程：
1. 拖拽或点击上传 DOCX 文件
2. 选择源语言和目标语言（默认中→英）
3. 可选择关联的术语表
4. 点击"开始翻译"
5. 显示翻译进度条（轮询 GET /api/tasks/{id}）
6. 完成后显示下载按钮

#### 2. 历史记录页 `/history`

表格展示当前 token 下所有翻译任务，支持分页、下载、删除。

#### 3. 术语表管理页 `/glossary`

上传术语表文件（CSV/XLSX/TXT），查看已有术语表列表及条目预览，支持删除。

#### 4. 导航

顶部导航栏包含三个入口：翻译、历史记录、术语表。

### 边界状态处理

- 上传非 DOCX 文件 → 提示格式不支持
- 文件超过 10MB → 提示文件过大
- 翻译失败 → 显示错误原因 + 重试按钮
- 历史记录为空 → 引导去翻译页
- 下载时文件已过期 → 提示"文件已过期"

## 翻译核心逻辑

### 翻译模式：生成中英双语版本

翻译不是替换原文，而是在原文基础上插入英文翻译，生成中英双语对照文档。

### 双语插入规则

| 内容类型 | 插入方式 | 示例 |
|---------|---------|------|
| **标题（Heading）** | 同一段落内，中文后空格 + 英文翻译 | `研究概述 Research Overview` |
| **正文段落** | 在中文段落后插入新的英文段落 | 中文段落 → 新段落（英文翻译） |
| **表格单元格（含中文）** | 单元格内换行 + 英文翻译 | `含量\nContent` |
| **表格单元格（仅数字/英文）** | 不翻译，跳过 | `100%` 跳过 |
| **已翻译内容（中英混合）** | 检测到中英混合则跳过 | 已含英文的段落不重复翻译 |

### 已翻译内容检测

通过自定义 CSX 脚本检测每个文本单元是否已包含英文内容：

- 统计段落中中文字符占比和英文字符占比
- 若英文字符占比 > 30%，视为已翻译内容，标记 `skip`
- 纯中文段落：翻译
- 纯数字/英文（无中文字符）：跳过
- 中英混合（英文占比 > 30%）：视为已翻译，跳过

### 提取文本结构（CSX 脚本输出）

```json
{
  "units": [
    {
      "index": 0,
      "type": "heading",
      "level": 1,
      "text": "研究概述",
      "style_id": "Heading1",
      "contains_chinese": true,
      "already_translated": false,
      "skip": false
    },
    {
      "index": 1,
      "type": "paragraph",
      "text": "本报告主要分析了...",
      "style_id": "Normal",
      "contains_chinese": true,
      "already_translated": false,
      "skip": false
    },
    {
      "index": 2,
      "type": "table_cell",
      "table_index": 0,
      "row_index": 1,
      "col_index": 0,
      "text": "含量",
      "contains_chinese": true,
      "already_translated": false,
      "skip": false
    },
    {
      "index": 3,
      "type": "table_cell",
      "table_index": 0,
      "row_index": 1,
      "col_index": 1,
      "text": "100%",
      "contains_chinese": false,
      "skip": true
    }
  ]
}
```

### 格式继承规则

英文翻译部分的格式要求：
- **字体**：Arial（仅英文翻译部分）
- **其他所有样式**：继承对应中文原文的样式
  - 字号、加粗、斜体、下划线
  - 对齐方式、行距、段间距
  - 标题级别（Heading1/2/3...）
- 通过 CSX 脚本克隆原文段落的 `pPr`（段落属性）和 `rPr`（运行属性），仅将字体修改为 Arial

### LLM 分批翻译策略

- 仅翻译 `skip=false` 且 `contains_chinese=true` 的文本单元
- 按每 20 段一批分组
- 每批发送 JSON 数组给 LLM，要求返回翻译后的 JSON 数组
- Prompt 模板：

```
你是一个专业文档翻译专家。请将以下 JSON 数组中的每段文本从中文翻译为英文。

## 术语表（必须严格遵守）
以下是必须使用的术语翻译对照表，遇到相关词汇时必须使用指定译文：
- "术语原文" → "术语译文"
...

要求：
1. 保持原文的段落结构，一一对应
2. 遇到术语表中的词汇，必须使用指定译文
3. 专业术语需准确翻译
4. 只翻译中文内容，保留原文中的数字、公式、英文术语不变
5. 返回相同格式的 JSON 数组
6. 只返回翻译结果，不要添加解释

输入：
[{"index": 0, "text": "..."}]

输出格式：
[{"index": 0, "text": "..."}]
```

术语表条目按长度降序注入 Prompt（长词优先，避免子串误匹配）。

### 任务状态流转

```
pending → extracting → translating → building → completed
                ↘ failed (任何阶段都可能进入)
```

- `pending`: 任务刚创建
- `extracting`: CSX 脚本提取文本单元 + 检测已翻译内容
- `translating`: LLM 分批翻译中，progress 实时更新
- `building`: CSX 脚本生成双语 DOCX（插入英文翻译）
- `completed`: 翻译完成，可下载
- `failed`: 任何阶段失败，记录 error_message

### 错误处理与重试

| 错误类型 | 处理 |
|---------|------|
| 网络错误 / 429 限流 | 指数退避重试（最多 3 次，间隔 2s/4s/8s） |
| JSON 解析失败（LLM 输出格式错误） | 重试该批次，提示严格 JSON 格式 |
| 500 服务端错误 | 重试 1 次，仍失败则标记 failed |
| 重试耗尽 | 标记任务 failed，记录 error_message |

### .NET CLI 集成

```python
import subprocess, json

CLI = ["dotnet", "run", "--project", DOTNET_CLI_PATH, "--"]

# Step 1: 自定义 CSX 脚本提取文本单元（含类型、是否含中文、是否已翻译）
result = subprocess.run(
    CLI + ["run-script", "extract_paragraphs.csx",
           "--input", docx_path, "--output", paragraphs_json_path],
    capture_output=True, text=True
)

# Step 2: Python 读取 JSON，过滤 skip 的单元，分批调用 LLM 翻译
units = json.load(open(paragraphs_json_path))
to_translate = [u for u in units["units"] if not u["skip"]]
# ... LLM 翻译逻辑，生成 translations.json

# Step 3: 自定义 CSX 脚本生成双语 DOCX
# 接收 translations.json，按规则插入英文翻译
subprocess.run(
    CLI + ["run-script", "insert_translations.csx",
           "--input", docx_path,
           "--translations", translations_json_path,
           "--output", result_path],
    capture_output=True, text=True
)

# Step 4: 验证
subprocess.run(CLI + ["validate", "--input", result_path], ...)
```

**两个自定义 CSX 脚本：**

1. **`extract_paragraphs.csx`** — 解析 DOCX，输出结构化 JSON
   - 遍历所有段落和表格单元格
   - 标记类型（heading/paragraph/table_cell）
   - 检测中文字符、已翻译内容（英文占比 > 30%）
   - 提取样式信息（style_id、字体、字号等）

2. **`insert_translations.csx`** — 接收翻译结果，生成双语 DOCX
   - 标题：在同一段落追加英文 run（空格分隔），字体 Arial
   - 正文：在中文段落后插入新段落，克隆样式，字体 Arial
   - 表格单元格：插入换行（`<w:br/>`）+ 英文 run，字体 Arial
   - 保留所有非文本元素（图片、图表、页眉页脚等）

## 边界情况处理

| 场景 | 处理方式 |
|------|---------|
| 上传非 DOCX 文件 | 前端校验后缀名 + 后端校验 MIME type |
| 文件超过 10MB | 前端 + 后端双重校验 |
| 空文档（无文本段落） | 提取后发现 0 段，标记 failed |
| 文档含图片/表格 | 图片忽略，表格单元格含中文则翻译（换行追加英文），纯数字/英文单元格跳过 |
| 已翻译内容（中英混合） | 检测英文占比 > 30%，自动跳过不重复翻译 |
| LLM 输出段落数不匹配 | 重试该批次；仍失败则逐段翻译降级 |
| 并发翻译任务 | 同一 token 最多 3 个并行任务 |
| Token 过期/丢失 | 自动生成新 token |
| .NET CLI 调用失败 | 捕获 subprocess 异常，标记 failed |
| 下载时文件已被清理 | 返回 404 + 提示"文件已过期" |

## 定时清理

每天凌晨执行：
- 删除 expires_at < NOW() 的任务及其文件
- 删除无关联术语表的孤立文件
- 清理空目录

## 配置项

```python
MAX_FILE_SIZE = 10 * 1024 * 1024      # 10MB
MAX_PARALLEL_TASKS = 3                  # 每 token 并行任务上限
TRANSLATION_BATCH_SIZE = 20             # 每批翻译段落数
LLM_MAX_RETRIES = 3                     # LLM 调用重试次数
TASK_EXPIRE_DAYS = 7                    # 任务过期天数
TOKEN_EXPIRE_DAYS = 30                  # Token 过期天数
LLM_MODEL = "claude-sonnet-4-6"         # 默认模型
```

## 部署

- 开发环境：`uvicorn` + `vite dev` 分别启动
- 生产环境：前端 build 后由 FastAPI 托管静态文件，单进程部署
- 无需 Docker / Nginx

## 项目结构

```
Doctrans/
├── backend/
│   ├── app/
│   │   ├── main.py            # FastAPI 入口
│   │   ├── config.py          # 配置项
│   │   ├── database.py        # SQLite 连接
│   │   ├── api/
│   │   │   ├── tasks.py       # 翻译任务路由
│   │   │   ├── glossaries.py  # 术语表路由
│   │   │   └── languages.py   # 语言列表路由
│   │   ├── services/
│   │   │   ├── translator.py  # LLM 调用 + 分批逻辑
│   │   │   ├── docx_handler.py# .NET CLI 封装
│   │   │   ├── glossary.py    # 术语表解析
│   │   │   └── cleanup.py     # 定时清理
│   │   └── models/
│   │       ├── task.py        # 翻译任务模型
│   │       └── glossary.py    # 术语表模型
│   ├── uploads/               # 上传文件暂存
│   ├── results/               # 翻译结果文件
│   ├── glossaries/            # 术语表文件
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── views/
│   │   │   ├── Translate.vue  # 翻译页
│   │   │   ├── History.vue    # 历史记录页
│   │   │   └── Glossary.vue   # 术语表管理页
│   │   ├── components/        # 公共组件
│   │   ├── api/               # API 调用封装
│   │   ├── router/            # Vue Router
│   │   └── App.vue
│   └── package.json
├── docs/
│   └── superpowers/
│       └── specs/
│           └── 2026-04-07-doctrans-design.md  # 本文档
└── .gitignore
```

## 术语表支持的文件格式

| 格式 | 说明 |
|------|------|
| CSV | 两列：原文术语,译文术语 |
| XLSX | 两列：原文术语,译文术语，第一行为表头 |
| TXT | 每行一条，用 Tab 分隔：原文术语 `\t` 译文术语 |

所有格式均支持可选的第三列"备注"。
