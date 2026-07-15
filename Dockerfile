# Dockerfile for ArtPM Agent
FROM python:3.10-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 先复制项目元数据和源码；requirements.txt 使用 -e .，安装时必须已有项目。
COPY pyproject.toml README.md requirements.txt ./
COPY agent.py app.py config.py health_check.py main.py ./
COPY artpm_agent/ ./artpm_agent/
RUN pip install --no-cache-dir .

COPY .env.example .env

# 创建数据目录
RUN mkdir -p data logs

# 暴露端口
EXPOSE 8501

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -m artpm_agent.health_check || exit 1

# 启动应用
CMD ["streamlit", "run", "artpm_agent/app.py", "--server.address", "0.0.0.0"]
