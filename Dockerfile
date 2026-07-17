# ArtPM Agent — 轻量化多阶段部署
# 设计要点：
#   • 阶段 1（builder）：带编译工具链，仅用于把项目打包成 wheel
#   • 阶段 2（runtime）：python:3.11-slim + 非 root 用户 appuser(uid/gid 10001)
#     安装 wheel 及其预编译依赖，不保留 build-essential，镜像更小更安全
#   • 所有库/缓存落在 DATA_ROOT（默认 /app/data），由 compose 挂卷持久化
#   • Streamlit 以 headless 模式运行，适配容器
#   • 健康检查走 Streamlit /healthz，不启动完整 Agent，开销极低
#   • 不把 .env 烤进镜像，密钥由 compose 挂载

# ───────────────────────── 阶段 1：构建 wheel ─────────────────────────
FROM python:3.11-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# 编译工具链只存在于 builder 阶段
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

# 复制打包所需元数据与源码（利用层缓存：改业务代码不会每次重装依赖）
COPY pyproject.toml README.md ./
COPY agent.py app.py config.py health_check.py main.py ./
COPY artpm_agent ./artpm_agent

# 构建项目自身的 wheel（依赖在 runtime 阶段以预编译 wheel 形式安装）
RUN python -m pip wheel . --no-deps --wheel-dir /wheels

# ───────────────────────── 阶段 2：运行镜像 ─────────────────────────
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    DATA_ROOT=/app/data

WORKDIR /app

# 固定 UID/GID 的非 root 用户，降低容器逃逸风险
RUN groupadd --system --gid 10001 appuser \
    && useradd --system --uid 10001 --gid appuser --home /app --shell /usr/sbin/nologin appuser

# 安装项目 wheel（pip 同时拉取其依赖的预编译 wheel；runtime 无 build-essential）
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir /wheels/*.whl \
    && rm -rf /wheels

# 运行期数据/日志目录（实际由卷挂载覆盖，这里仅作兜底并修正属主）
RUN mkdir -p /app/data /app/logs \
    && chown -R appuser:appuser /app

COPY .env.example /app/.env.example

EXPOSE 8501

# 轻量健康检查：仅探测 Streamlit 健康端点，不初始化完整 Agent
HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://localhost:8501/healthz', timeout=4); sys.exit(0)" || exit 1

USER appuser

CMD ["streamlit", "run", "artpm_agent/app.py", \
     "--server.address=0.0.0.0", "--server.port=8501"]
