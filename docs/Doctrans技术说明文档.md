# Doctrans 技术说明文档

## 1. 项目概述

Doctrans 是一个基于 Web 的 DOCX 文档双语翻译平台，支持将中文文档翻译为英文并生成中英双语对照文档。系统保留原文内容和格式，将英文译文按规则插入文档中。

### 核心特性

- 上传 DOCX 文件，自动提取文本并翻译为英文
- 生成中英双语对照文档：标题行内追加、正文插入新段落、表格单元格内换行追加
- 支持术语表（Glossary）确保专业术语翻译一致性
- 支持多种 LLM 提供商（Anthropic Claude、DeepSeek、GLM、Qwen、豆包等）
- Cookie-Token 身份识别，无需注册登录
- 翻译任务后台异步执行，前端实时轮询进度

---

## 2. 技术架构

```
┌─────────────────────────────────────────────────┐
│  Frontend (Vue 3 + Element Plus + Vite 5)       │
│  浏览器端单页应用                                  │
└──────────────────────┬──────────────────────────┘
                       │ HTTP (REST API)
┌──────────────────────▼──────────────────────────┐
│  Backend (FastAPI + Uvicorn)                     │
│  Python 3.10+ 异步 Web 服务                       │
│  ┌─────────────┐  ┌──────────────┐              │
│  │ API Routes  │  │ Middleware   │              │
│  │ Tasks/Gloss │  │ Token/CORS   │              │
│  └──────┬──────┘  └──────────────┘              │
│  ┌──────▼──────────────────────────┐            │
│  │ Services                        │            │
│  │  - translator.py (LLM 翻译)     │            │
│  │  - docx_handler.py (DOCX 处理)  │            │
│  │  - glossary.py (术语表解析)      │            │
│  └──────┬──────────────────────────┘            │
│  ┌──────▼──────┐                                 │
│  │ SQLite DB   │  aiosqlite 异步访问              │
│  └─────────────┘                                 │
└──────────────────────┬──────────────────────────┘
                       │ subprocess (asyncio.to_thread)
┌──────────────────────▼──────────────────────────┐
│  Doctrans.DocxProc (.NET 8 + OpenXML SDK 3.2)   │
│  独立命令行工具，负责 DOCX 文件解析与生成            │
└─────────────────────────────────────────────────┘
```

### 技术栈

| 层级 | 技术 | 版本 |
|------|------|------|
| 前端框架 | Vue 3 + Vite | 3.5 / 5.4 |
| UI 组件库 | Element Plus | 2.13 |
| 路由 | Vue Router | 4.6 |
| HTTP 客户端 | Axios | 1.15 |
| 后端框架 | FastAPI + Uvicorn | 0.133 / 0.41 |
| 数据库 | SQLite (aiosqlite) | - |
| DOCX 处理 | .NET 8 + DocumentFormat.OpenXml | 3.2.0 |
| LLM SDK | anthropic / openai | 0.83 / 1.0+ |

---

## 3. 项目结构

```
Doctrans/
├── backend/
│   ├── .env                          # LLM 配置文件
│   ├── requirements.txt              # Python 依赖
│   ├── doctrans.db                   # SQLite 数据库（运行时生成）
│   ├── app/
│   │   ├── main.py                   # FastAPI 入口，中间件注册
│   │   ├── config.py                 # 配置加载（从 .env）
│   │   ├── database.py               # 数据库 Schema 与连接管理
│   │   ├── api/
│   │   │   ├── tasks.py              # 翻译任务 CRUD + 后台执行
│   │   │   ├── glossaries.py         # 术语表 CRUD
│   │   │   ├── languages.py          # 语言列表
│   │   │   └── settings.py           # LLM 配置（只读）
│   │   ├── models/                   # Pydantic 数据模型
│   │   └── services/
│   │       ├── translator.py         # LLM 翻译引擎
│   │       ├── docx_handler.py       # .NET CLI 调用封装
│   │       ├── glossary.py           # 术语表文件解析
│   │       └── cleanup.py            # 过期任务清理
│   ├── scripts/
│   │   └── Doctrans.DocxProc/       # .NET DOCX 处理项目
│   │       ├── Program.cs            # extract / insert 命令
│   │       └── Doctrans.DocxProc.csproj
│   ├── uploads/                      # 上传文件存储（按任务 ID 分目录）
│   └── results/                      # 翻译结果存储
└── frontend/
    ├── package.json
    ├── vite.config.js                # 开发代理 /api → localhost:8000
    ├── dist/                         # 生产构建输出
    └── src/
        ├── App.vue                   # 导航栏 + 路由出口
        ├── router/index.js           # 路由定义
        ├── api/index.js              # Axios API 封装
        └── views/
            ├── Translate.vue         # 上传翻译页
            ├── History.vue           # 历史记录页
            ├── Glossary.vue          # 术语表管理页
            └── Settings.vue          # 配置展示页（只读）
```

---

## 4. 配置说明

### 4.1 后端配置（.env 文件）

配置文件位于 `backend/.env`，修改后需重启后端生效。

```ini
# Provider 类型：anthropic | openai_compatible
LLM_PROVIDER=anthropic

# API 配置
LLM_API_URL=https://api.anthropic.com
LLM_API_KEY=sk-xxx
LLM_MODEL=claude-sonnet-4-6
```

**已支持的提供商配置示例：**

| 提供商 | Provider | API URL | 模型示例 |
|--------|----------|---------|---------|
| Anthropic Claude | `anthropic` | `https://api.anthropic.com` | `claude-sonnet-4-6` |
| DeepSeek | `openai_compatible` | `https://api.deepseek.com` | `deepseek-chat` |
| 智谱 GLM | `openai_compatible` | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` |
| 通义千问 | `openai_compatible` | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| 豆包（字节） | `openai_compatible` | `https://ark.cn-beijing.volces.com/api/v3` | `doubao-seed-2-0-lite-260215` |

### 4.2 应用参数（config.py）

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `MAX_FILE_SIZE` | 10 MB | 上传文件大小限制 |
| `MAX_PARALLEL_TASKS` | 3 | 每个 Token 最大并行任务数 |
| `TRANSLATION_BATCH_SIZE` | 20 | 每批翻译段落数 |
| `LLM_MAX_RETRIES` | 3 | LLM 调用最大重试次数 |
| `TASK_EXPIRE_DAYS` | 7 | 任务过期天数 |
| `TOKEN_EXPIRE_DAYS` | 30 | Cookie 有效天数 |
| 并发信号量 | 3 | 翻译批次最大并发数 |

---

## 5. API 接口

### 5.1 翻译任务

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/tasks` | 上传 DOCX 创建翻译任务 |
| GET | `/api/tasks` | 分页查询任务列表 |
| GET | `/api/tasks/{task_id}` | 查询单个任务状态（轮询用） |
| GET | `/api/tasks/{task_id}/download` | 下载翻译完成的 DOCX |
| DELETE | `/api/tasks/{task_id}` | 删除任务及文件 |

**创建任务参数（multipart/form-data）：**

- `file`: DOCX 文件（必填）
- `source_lang`: 源语言，默认 `zh`
- `target_lang`: 目标语言，默认 `en`
- `glossary_id`: 术语表 ID（可选）

### 5.2 术语表

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/glossaries` | 上传术语表文件 |
| GET | `/api/glossaries` | 列出所有术语表 |
| GET | `/api/glossaries/{id}` | 术语表详情（含前 50 条术语） |
| DELETE | `/api/glossaries/{id}` | 删除术语表 |

### 5.3 其他

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/languages` | 支持的语言列表 |
| GET | `/api/settings/llm` | 当前 LLM 配置（只读） |

---

## 6. 翻译流水线

### 6.1 任务状态机

```
pending → extracting → translating → building → completed
                                         ↘ failed（任何阶段均可转入）
```

### 6.2 处理流程

1. **文件上传**：用户上传 DOCX → 保存到 `uploads/{task_id}/original.docx` → 创建数据库记录
2. **文本提取**：调用 .NET CLI 执行 `extract` 命令，输出 `paragraphs.json`
3. **LLM 翻译**：
   - 按每 20 段切分为批次
   - 使用 `asyncio.Semaphore(3)` 控制最大 3 个批次并发
   - 注入术语表到翻译提示词
   - 实时更新翻译进度到数据库
   - 遇到 429 限流时指数退避重试
4. **双语文档生成**：调用 .NET CLI 执行 `insert` 命令，输出双语 DOCX
5. **完成**：更新状态，用户可下载

### 6.3 LLM 翻译提示词

```
你是一个专业文档翻译专家。请将以下 JSON 数组中的每段文本从中文翻译为英文。

## 术语表（必须严格遵守）
- "源术语" → "目标术语"
...

要求：
1. 保持原文的段落结构，一一对应
2. 遇到术语表中的词汇，必须使用指定译文
3. 专业术语需准确翻译
4. 只翻译中文内容，保留原文中的数字、公式、英文术语不变
5. 返回相同格式的 JSON 数组
6. 只返回翻译结果，不要添加解释
```

---

## 7. DOCX 处理（.NET 组件）

### 7.1 命令行接口

```
# 提取文本单元
dotnet Doctrans.DocxProc.dll extract --input <docx> --output <json>

# 插入翻译生成双语文档
dotnet Doctrans.DocxProc.dll insert --input <docx> --translations <json> --output <docx> [--paragraphs <json>]
```

### 7.2 文本提取规则

- 扫描文档样式定义（styles.xml），通过 `OutlineLevel` 属性（0-8）识别所有标题样式
- 遍历正文段落和表格单元格
- 对每个文本单元检测：
  - 是否包含中文字符（Unicode 范围判断）
  - 英文占比（字母数 / 总字符数）
- **跳过条件**：不含中文，或英文占比 > 30%（视为已翻译）
- 输出 JSON 包含：index、type（heading/paragraph/table_cell）、level、text、style_id、skip 等

### 7.3 双语插入规则

| 内容类型 | 插入方式 | 格式 |
|----------|----------|------|
| 标题 | 同一段落内追加 | `中文标题 English Title`（空格分隔） |
| 正文段落 | 源段落之后插入新段落 | 新段落继承源段落样式，字体为 Arial |
| 表格单元格 | 单元格内换行追加 | `中文内容<br/>English content` |
| 已翻译内容 | 跳过 | 不做任何修改 |
| 纯数字/英文 | 跳过 | 不做任何修改 |

英文译文的字体统一设为 Arial（Ascii、HighAnsi、EastAsia、ComplexScript），其余格式属性（字号、加粗、颜色等）继承原文。

### 7.4 标题检测策略

采用三层优先级检测：

1. **样式映射表**（主要）：扫描 styles.xml 中定义了 `OutlineLevel 0-8` 的段落样式
2. **行内 OutlineLevel**：段落自身的 `pPr.OutlineLevel` 覆盖
3. **名称模式匹配**（兜底）：匹配 `Heading*`、`标题*`、`1-9` 等常见命名

---

## 8. 身份认证

系统采用无登录设计，通过 Cookie 中的 `token` 字段标识用户：

- 首次访问时，中间件自动生成 UUID 作为 token，写入 Cookie
- Cookie 有效期 30 天，`httpOnly=false`
- 所有 API 请求通过 `request.state.token` 获取当前用户标识
- 数据库中所有数据按 token 隔离

---

## 9. 数据库设计

### translation_tasks（翻译任务表）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | TEXT PK | 任务 UUID |
| token | TEXT | 用户标识 |
| original_filename | TEXT | 原始文件名 |
| original_path | TEXT | 上传文件路径 |
| result_path | TEXT | 结果文件路径 |
| glossary_id | TEXT | 关联术语表 ID |
| source_lang | TEXT | 源语言（默认 zh） |
| target_lang | TEXT | 目标语言（默认 en） |
| status | TEXT | 任务状态 |
| progress | INTEGER | 进度百分比（0-100） |
| total_paragraphs | INTEGER | 总段落数 |
| translated_paragraphs | INTEGER | 已翻译段落数 |
| error_message | TEXT | 错误信息 |
| created_at | TIMESTAMP | 创建时间 |
| completed_at | TIMESTAMP | 完成时间 |
| expires_at | TIMESTAMP | 过期时间 |

### glossaries（术语表）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | TEXT PK | 术语表 UUID |
| token | TEXT | 用户标识 |
| name | TEXT | 术语表名称 |
| source_lang | TEXT | 源语言 |
| target_lang | TEXT | 目标语言 |
| file_path | TEXT | 上传文件路径 |
| term_count | INTEGER | 术语条数 |
| created_at | TIMESTAMP | 创建时间 |

### glossary_terms（术语条目）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增 ID |
| glossary_id | TEXT FK | 所属术语表 |
| source_term | TEXT | 源术语 |
| target_term | TEXT | 目标术语 |
| note | TEXT | 备注 |

---

## 10. 部署与运行

### 10.1 环境要求

- Python 3.10+
- Node.js 18+（仅前端构建时需要）
- .NET 8 SDK（首次运行自动编译 .NET 项目）

### 10.2 后端启动

```bash
cd backend
pip install -r requirements.txt

# 编辑 .env 配置 LLM API
# 启动服务
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 10.3 前端构建

```bash
cd frontend
npm install
npm run build    # 输出到 dist/
```

构建产物放入 `backend/frontend/dist/` 目录后，FastAPI 会自动托管前端静态文件，实现单端口部署。

### 10.4 开发模式

```bash
# 终端 1：后端
cd backend && python -m uvicorn app.main:app --reload --port 8000

# 终端 2：前端（Vite 开发服务器，自动代理 /api 到后端）
cd frontend && npm run dev
```

---

## 11. 术语表格式支持

| 格式 | 解析方式 | 列结构 |
|------|----------|--------|
| CSV | Python csv 模块 | 第一行为表头，列：源术语、目标术语、备注（可选） |
| XLSX | openpyxl | 同上，跳过第一行表头 |
| TXT | Tab 分隔 | 同上，跳过第一行表头 |

术语表在翻译时按源术语长度降序注入提示词，避免短术语误替换长术语中的子串。
