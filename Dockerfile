# Dockerfile for ArtPM Agent
FROM python:3.10-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY artpm_agent/ ./artpm_agent/
COPY .env.example .env

# 创建数据目录
RUN mkdir -p data logs

# 暴露端口
EXPOSE 8501

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python artpm_agent/health_check.py || exit 1

# 启动应用
CMD ["streamlit", "run", "artpm_agent/app.py", "--server.address", "0.0.0.0"]
