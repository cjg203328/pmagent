# ArtPM Agent 快速启动

本指南覆盖本地 UI、UI + REST API 和 Docker Compose 三种启动方式。完整配置项以根目录
`.env.example` 为准，README 是项目总入口。

## 本地 UI + API

Python 版本要求为 3.10 或更高。

### Windows PowerShell

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[api]"
Copy-Item .env.example .env
python -m artpm_agent.tools.check_config
python start_with_checks.py
```

### Linux / macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[api]"
cp .env.example .env
python -m artpm_agent.tools.check_config
python start_with_checks.py
```

访问：

- UI：`http://127.0.0.1:8501`
- API：`http://127.0.0.1:8765`
- Swagger：`http://127.0.0.1:8765/docs`

无 LLM API Key 时仍可使用报价测算、任务分配、进度追踪、文档解析和本地文件分析。
配置 Provider 和对应 Key 后，才启用通用对话和模型增强能力。

## 仅启动离线 UI

基础安装不包含 FastAPI，只启动 Streamlit 时使用：

```bash
python -m pip install -e .
python -m streamlit run artpm_agent/app.py --server.address 127.0.0.1 --server.port 8501
```

Windows 可在安装依赖后运行 `start.bat`；Linux/macOS 可运行 `./start.sh`。统一启动器也
支持只做配置检查：

```bash
python start_with_checks.py --check-only
```

## 配置 LLM

编辑 `.env`，选择一个 Provider 并填写对应 Key：

```dotenv
LLM_PROVIDER=anthropic
LLM_MODEL=claude-3-5-sonnet-20241022
ANTHROPIC_API_KEY=
```

也支持 `openai`、`zhipu`、`deepseek` 和 `custom`。视觉模型使用
`LLM_VISION_PROVIDER`、`LLM_VISION_MODEL` 和对应 Key 单独配置。

模型工具调用默认关闭。只有接入宿主审批钩子后，才允许设置：

```dotenv
AGENT_MODEL_TOOL_CALLS_ENABLED=true
```

写入型工具始终需要审批，不能用该开关绕过安全边界。

## Docker Compose

生产 Compose 使用 PostgreSQL/RLS、Qdrant、Redis、Caddy 和可观测组件。对外入口是 Caddy
的 `80/443`；应用容器内的 UI `8501` 和 API `8765` 不直接暴露到宿主机。

在 `.env` 中设置 `BASIC_AUTH_USER`、`BASIC_AUTH_HASH`、`ARTPM_GATEWAY_SHARED_SECRET`、
`POSTGRES_PASSWORD`、`POSTGRES_ADMIN_PASSWORD` 和 `GRAFANA_ADMIN_PASSWORD`，再执行：

```bash
cp .env.example .env
docker run --rm caddy:2-alpine caddy hash-password
# 将输出填入 BASIC_AUTH_HASH，并设置 BASIC_AUTH_USER
docker compose config
docker compose up -d --build
docker compose ps
```

完整部署、证书、数据卷和权限说明见
[`docs/operations/DEPLOY.md`](../operations/DEPLOY.md)。

## 常用检查

```bash
python -m artpm_agent.tools.check_config
python -m compileall -q artpm_agent
```

开发测试命令见 [`docs/operations/QUALITY_GATES.md`](../operations/QUALITY_GATES.md)。
遇到服务、端口或依赖问题时，先查看
[`docs/operations/TROUBLESHOOTING.md`](../operations/TROUBLESHOOTING.md)。
