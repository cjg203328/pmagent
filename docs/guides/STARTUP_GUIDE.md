# ArtPM Agent 启动指南

## 本地完整栈

本地 UI + API 需要安装 API extra：

```powershell
python -m pip install -e ".[api]"
Copy-Item .env.example .env
python -m artpm_agent.tools.check_config
python start_with_checks.py
```

Linux/macOS 使用 `cp .env.example .env` 替代 `Copy-Item`。启动后访问：

- Streamlit UI：`http://127.0.0.1:8501`
- API 健康检查：`http://127.0.0.1:8765/health`
- API 文档：`http://127.0.0.1:8765/docs`

## 仅离线 UI

不需要 API Key，也不需要 FastAPI：

```bash
python -m pip install -e .
python -m streamlit run artpm_agent/app.py --server.address 127.0.0.1 --server.port 8501
```

Windows 可使用 `start.bat`，Linux/macOS 可使用 `./start.sh`。配置检查模式：

```bash
python start_with_checks.py --check-only
```

## Docker Compose

生产 Compose 使用 PostgreSQL/RLS、Qdrant、Redis、Caddy 和可观测组件；对外入口为 Caddy
的 `80/443`。请先按 [`../operations/DEPLOY.md`](../operations/DEPLOY.md) 设置认证、数据库、
网关和 Grafana 凭据，再执行：

```bash
docker compose config
docker compose up -d --build
docker compose ps
```

## 停止与排查

- 本地前台进程按 `Ctrl+C` 停止，启动器只回收本次创建的 API 进程。
- 端口被占用时先确认监听进程属于本项目，不要强制结束其他项目服务。
- 先运行 `python -m artpm_agent.tools.check_config`，再查看
  [`../operations/TROUBLESHOOTING.md`](../operations/TROUBLESHOOTING.md)。
- 质量命令见 [`../operations/QUALITY_GATES.md`](../operations/QUALITY_GATES.md)。
