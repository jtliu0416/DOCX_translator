# Doctrans

基于 AI 大模型的 DOCX 文档智能翻译平台，自动生成中英双语对照文档，完美保留原文格式。

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10+-blue" />
  <img src="https://img.shields.io/badge/Vue-3.5-brightgreen" />
  <img src="https://img.shields.io/badge/.NET-8.0-purple" />
  <img src="https://img.shields.io/badge/License-MIT-yellow" />
</p>

---

## 功能特性

- **一键翻译** — 上传 DOCX 文件，自动提取中文内容，AI 翻译为英文，生成中英双语对照文档
- **格式完美保留** — 基于 OpenXML SDK 直接操作文档底层结构，标题、正文、表格格式零丢失
- **多文件批量处理** — 支持同时上传多个文件，后台自动排队，逐个翻译
- **内置生物制药术语表** — 预置 375 条生物制药专业术语（中英对照），翻译时默认启用，可预览和搜索
- **自定义术语表** — 上传自定义术语对照表，与内置术语表合并使用（自定义优先）
- **多模型灵活切换** — 支持 Anthropic Claude、DeepSeek、GLM、Qwen、豆包等主流大模型
- **实时进度推送** — WebSocket 实时显示翻译进度
- **失败自动重试** — 翻译失败一键重试，无需重新上传
- **双语插入规则** — 标题行内追加、正文新段落插入、表格单元格换行追加

> 📷 *【项目截图：翻译主界面】*

## 双语插入效果

| 内容类型 | 原文 | 翻译后 |
|----------|------|--------|
| 标题 | `产品技术要求` | `产品技术要求 Product Technical Requirements` |
| 正文段落 | `本产品适用于...` | 原段落下方插入英文段落 |
| 表格单元格 | `规格：10mL` | 单元格内换行追加 `Specification: 10mL` |

> 📷 *【翻译前后文档对比截图】*

## 技术架构

```
┌─────────────────────────────────────┐
│  Frontend   Vue 3 + Element Plus    │
├─────────────────────────────────────┤
│  Backend    FastAPI + SQLite        │
├─────────────────────────────────────┤
│  DOCX处理   .NET 8 + OpenXML SDK   │
├─────────────────────────────────────┤
│  翻译引擎   Claude / DeepSeek / GLM │
└─────────────────────────────────────┘
```

| 组件 | 技术栈 | 说明 |
|------|--------|------|
| 前端 | Vue 3 + Vite 5 + Element Plus | 响应式单页应用 |
| 后端 | FastAPI + Uvicorn + aiosqlite | 异步 Python Web 服务 |
| DOCX 处理 | .NET 8 + DocumentFormat.OpenXml 3.2 | 精准操作 Word 文档底层 XML |
| 翻译引擎 | Anthropic SDK / OpenAI SDK | 支持所有 OpenAI 兼容接口 |
| 实时通信 | WebSocket | 翻译进度实时推送 |

## 快速开始

### 环境要求

- Python 3.10+
- Node.js 18+
- .NET 8 SDK

### 1. 配置 LLM

编辑 `backend/.env`，填入你的 AI 模型 API 配置：

```ini
# 选择提供商类型：anthropic 或 openai_compatible
LLM_PROVIDER=openai_compatible

# API 配置
LLM_API_URL=https://api.deepseek.com
LLM_API_KEY=sk-your-api-key
LLM_MODEL=deepseek-chat
```

**已验证支持的模型：**

| 提供商 | API 地址 | 模型 |
|--------|----------|------|
| Anthropic Claude | `https://api.anthropic.com` | `claude-sonnet-4-6` |
| DeepSeek | `https://api.deepseek.com` | `deepseek-chat` |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | `glm-4-flash` |
| 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | `qwen-plus` |
| 豆包 | `https://ark.cn-beijing.volces.com/api/v3` | `doubao-seed-2-0-lite-260215` |

### 2. 启动后端

```bash
cd backend
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 3. 构建前端（可选）

开发模式：
```bash
cd frontend
npm install
npm run dev
```

生产构建（输出到 `dist/`，后端自动托管）：
```bash
cd frontend
npm install
npm run build
```

### 4. 访问

浏览器打开 `http://localhost:8000`

## 使用方式

1. **上传文档** — 拖拽一个或多个 `.docx` 文件到上传区域
2. **选择术语表** — 内置生物制药术语表默认启用，也可上传自定义术语表
3. **开始翻译** — 点击按钮，实时查看翻译进度
4. **下载结果** — 翻译完成后下载中英双语对照文档

> 📷 *【操作流程截图】*

## 项目结构

```
Doctrans/
├── backend/
│   ├── .env                          # LLM 配置（需自行填写）
│   ├── requirements.txt
│   └── app/
│       ├── main.py                   # FastAPI 入口
│       ├── config.py                 # 配置加载
│       ├── database.py               # SQLite 数据库
│       ├── api/
│       │   ├── tasks.py              # 翻译任务 API
│       │   ├── glossaries.py         # 术语表 API
│       │   ├── ws.py                 # WebSocket 实时推送
│       │   └── settings.py           # 配置查询
│       └── services/
│           ├── translator.py         # LLM 翻译引擎
│           ├── docx_handler.py       # DOCX 处理封装
│           ├── queue.py              # 并发翻译队列
│           ├── glossary.py           # 术语表解析
│           ├── builtin_glossary.py   # 内置术语表初始化
│           └── builtin_terms_data.py # 内置术语数据（375条）
│           └── Doctrans.DocxProc/    # .NET DOCX 处理工具
├── frontend/
│   ├── package.json
│   └── src/
│       ├── views/
│       │   ├── Translate.vue         # 翻译页
│       │   ├── History.vue           # 历史记录
│       │   ├── Glossary.vue          # 术语表管理
│       │   └── Settings.vue          # 配置展示
│       └── api/index.js              # API 封装
└── docs/                             # 文档
```

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/tasks` | 上传文件创建翻译任务 |
| GET | `/api/tasks` | 查询任务列表 |
| GET | `/api/tasks/{id}` | 查询任务状态 |
| GET | `/api/tasks/{id}/download` | 下载翻译结果 |
| POST | `/api/tasks/{id}/retry` | 重试失败任务 |
| DELETE | `/api/tasks/{id}` | 删除任务 |
| POST | `/api/glossaries` | 上传术语表 |
| GET | `/api/glossaries` | 查询术语表列表（含内置术语表） |
| GET | `/api/glossaries/{id}` | 查询术语表详情 |
| DELETE | `/api/glossaries/{id}` | 删除术语表（内置不可删） |
| WS | `/ws/tasks/{id}` | 实时进度推送 |

## 常见问题

**Q: 翻译很慢怎么办？**

A: 翻译速度取决于所选 AI 模型的响应速度。推荐使用 DeepSeek 或 GLM 等国内模型，延迟更低。可在 `backend/.env` 中切换。

**Q: 翻译后格式乱了？**

A: Doctrans 使用 OpenXML SDK 直接操作文档 XML 结构，不做格式转换。如果遇到问题，请提交 Issue 并附上示例文档。

**Q: 支持英文翻中文吗？**

A: 目前仅支持中文翻英文，其他方向将在后续版本支持。

**Q: 数据安全吗？**

A: 所有数据存储在本地 SQLite 数据库中，文件保存在本地服务器。翻译通过 API 发送给大模型服务商，请根据公司安全策略选择合规的模型提供商。

## 更新日志

- **V0.3** (2026-04-28) — 内置 375 条生物制药专业术语表、翻译页内置术语开关、术语表页分类展示（内置/用户）、搜索预览、自定义术语表与内置合并使用（冲突时自定义优先）、插入英文段落自动编号隔离
- **V0.2** (2026-04-24) — 多文件上传、WebSocket 实时进度、失败重试、下载文件名优化
- **V0.1** (2026-04-23) — 首版发布，核心翻译功能

## 许可证

MIT License
