# ArtPM Agent — 轻量化单容器部署
# 设计要点：
#   • python:3.11-slim 基础镜像，依赖单一来源（pyproject.toml）
#   • 所有库/缓存落在 DATA_ROOT（默认 /app/data），由 compose 挂卷持久化
#   • Streamlit 以 headless 模式运行，适配容器
#   • 健康检查走 Streamlit /healthz，不启动完整 Agent，开销极低
#   • 不把 .env 烤进镜像，密钥由 compose 挂载

FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    DATA_ROOT=/app/data

WORKDIR /app

# 系统依赖：仅保留可能需编译的少量包；pandas/numpy/faiss/streamlit 均有预编译 wheel
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 先复制元数据（利用 Docker 层缓存，改代码不会每次重装依赖）
COPY pyproject.toml README.md requirements.txt ./
COPY agent.py app.py config.py health_check.py main.py ./
COPY artpm_agent/ ./artpm_agent/

# 以 pyproject 为单一来源安装核心依赖
RUN pip install --no-cache-dir .

# 保留 .env.example 作为配置模板参考（不生成 .env，.env 由 compose 挂载）
COPY .env.example /app/.env.example

# 运行期数据/日志目录（实际由卷挂载覆盖，这里仅作兜底）
RUN mkdir -p /app/data /app/logs

EXPOSE 8501

# 轻量健康检查：仅探测 Streamlit 健康端点，不初始化完整 Agent
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://localhost:8501/healthz', timeout=4); sys.exit(0)" || exit 1

CMD ["streamlit", "run", "artpm_agent/app.py", \
     "--server.address=0.0.0.0", "--server.port=8501"]
