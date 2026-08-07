# Doctrans 人工生产发布手册

本手册适用于尚未接入 CI/CD 的单机 Docker Compose 环境。正式服务器只部署经过验证并打 Git 标签的版本，不接收开发目录覆盖、未提交文件或服务器现场修改。

## 1. 发布约束

- 发布标签统一使用小写语义版本，例如 `v0.4.0`。
- 发布提交必须通过后端测试和前端生产构建。
- 正式服务器的 Git 工作区必须干净，且 `HEAD` 必须等于待发布标签。
- `backend/.env` 只保存在正式服务器，不提交到 Git。
- 发布前暂停新任务，并等待正在执行的翻译任务结束。
- 每次发布自动备份 `data/`、`backend/.env` 和旧容器镜像。

## 2. 开发环境发布准备

在 Windows 开发机执行完整验证：

先停止本项目的 Vite 开发服务器，避免 Windows 锁定 `node_modules` 中的可执行文件，然后运行：

```powershell
./scripts/verify-release.ps1 `
  -WebUiOrigins 'http://innovatex.intbio.com:8189,http://10.56.0.25:8189'
git diff --check
git status --short
```

检查浏览器控制台和服务日志不得包含 JWT、API Key 或文档内容。验证通过后提交代码，经评审合并到发布分支并创建标签：

```bash
git tag -a v0.4.0 -m "Release v0.4.0"
git push origin v0.4.0
```

标签一旦用于生产不得移动或覆盖。后续修复必须使用新版本，例如 `v0.4.1`。

## 3. 正式服务器首次配置

创建 Compose 构建参数文件。建议放在仓库外：

```bash
mkdir -p /home/yananliu/.config/doctrans
cp deploy/compose.env.example /home/yananliu/.config/doctrans/compose.env
chmod 600 /home/yananliu/.config/doctrans/compose.env
vi /home/yananliu/.config/doctrans/compose.env
```

`VITE_WEBUI_ORIGINS` 是逗号分隔的正式 WebUI origin 列表。每项只包含协议、主机和可选端口，不包含路径；它是前端构建变量，不能用 `backend/.env` 代替。

确认 `backend/.env` 至少包含：

```ini
JWT_SECRET=<与正式WebUI一致且不少于32字符>
JWT_ISSUER=non-gmp-lims
JWT_AUDIENCE=web-ui
JWT_LEEWAY_SECONDS=60
CORS_ALLOWED_ORIGINS=http://innovatex.intbio.com:8189,http://10.56.0.25:8189
```

`JWT_LEEWAY_SECONDS` 只用于容忍签发端与验签端的短暂时钟偏差，允许范围为 `0-300` 秒；生产服务器仍必须启用 NTP。随后配置正式 LLM 地址、密钥、模型和并发参数，并执行：

```bash
chmod 600 backend/.env
```

## 4. 执行发布

先检查工作区。如果存在服务器手工修改，停止发布并将修改回收到开发流程，不能直接覆盖：

```bash
cd /home/yananliu/DOCX_translator
git status --short
git fetch --tags origin
git checkout --detach v0.4.0
git status --short
```

确认任务已排空后执行：

```bash
CONFIRM_PRODUCTION=YES bash scripts/deploy-production.sh v0.4.0
```

脚本按以下顺序运行：

1. 校验生产确认、依赖命令、配置文件、Git 标签和干净工作区。
2. 在旧容器继续服务时构建 `doctrans:v0.4.0` 镜像。
3. 保存旧镜像为带时间戳的回滚镜像。
4. 停止旧容器，并一致性备份 `data/` 和 `backend/.env`。
5. 启动新容器并等待 `/health` 变为 healthy。
6. 健康检查失败时，自动恢复发布前数据并启动旧镜像。

默认文件位置：

- 备份：`/home/yananliu/backups/doctrans/`
- 最近一次发布状态：`/home/yananliu/.local/state/doctrans/last-deploy.env`
- Compose 参数：`/home/yananliu/.config/doctrans/compose.env`

可通过 `BACKUP_ROOT`、`DEPLOY_STATE_DIR` 和 `COMPOSE_ENV_FILE` 覆盖这些路径。

## 5. 上线验证

```bash
docker compose \
  --project-name doctrans \
  --env-file /home/yananliu/.config/doctrans/compose.env \
  --file docker-compose.prod.yml \
  ps

docker logs --tail=200 doctrans
curl -fsS http://127.0.0.1:8000/health
```

还必须通过正式 WebUI 完成登录、打开翻译页、上传、翻译和下载冒烟测试，并确认用户间任务与术语表隔离正常。

## 6. 回滚

只回滚应用镜像，保留当前数据：

```bash
CONFIRM_PRODUCTION=YES bash scripts/rollback-production.sh
```

当新版数据库变更与旧版不兼容时，恢复发布前数据：

```bash
CONFIRM_PRODUCTION=YES RESTORE_DATA=YES bash scripts/rollback-production.sh
```

恢复数据前，脚本会把当前 `data/` 移到本次备份目录，不会直接删除。

## 7. 发布记录

每次发布至少记录以下内容：版本标签、commit SHA、操作人、发布时间、备份路径、验证结果、异常及回滚结果。发布完成后保留最近若干版本镜像和备份，并按既定保留周期清理。
