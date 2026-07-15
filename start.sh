#!/bin/bash
# ArtPM Agent 快速启动脚本

echo "🚀 启动 ArtPM Agent..."
echo ""

# 检查Python环境
if ! command -v python &> /dev/null; then
    echo "❌ 错误: 未找到Python"
    exit 1
fi

echo "✅ Python: $(python --version)"

# 检查依赖
if ! python -c "import streamlit" 2>/dev/null; then
    echo "⚠️  安装依赖..."
    pip install -r artpm_agent/requirements.txt
fi

# 检查.env文件
if [ ! -f .env ]; then
    echo "⚠️  创建 .env 配置文件..."
    cp .env.example .env
    echo "✅ 已创建 .env (离线模式)"
fi

# 检查数据目录
mkdir -p data logs

echo ""
echo "📊 配置信息:"
echo "  - 工作模式: 离线优先"
echo "  - 数据目录: ./data"
echo "  - 日志目录: ./artpm_agent/logs"
echo ""

# 启动应用
echo "🌐 启动 Streamlit 应用..."
echo "访问地址: http://localhost:8501"
echo ""
echo "提示: 按 Ctrl+C 停止应用"
echo ""

cd "$(dirname "$0")"

# 设置项目根目录到 Python 搜索路径（确保多页面导入不报错）
export PYTHONPATH="$(pwd):${PYTHONPATH}"

python -m streamlit run artpm_agent/app.py \
    --server.address 127.0.0.1 \
    --server.port 8501 \
    --server.headless true
