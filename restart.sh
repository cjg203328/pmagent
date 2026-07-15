#!/bin/bash
# ArtPM Agent - 重启脚本

echo "🔄 重启 ArtPM Agent..."
echo ""

# 停止现有进程
echo "停止现有进程..."
taskkill /F /IM streamlit.exe 2>/dev/null || pkill -f "streamlit run" || echo "  没有找到运行中的进程"
sleep 2

# 检查环境
echo ""
echo "检查环境..."
if ! command -v python &> /dev/null; then
    echo "❌ 错误: 未找到Python"
    exit 1
fi
echo "  ✅ Python: $(python --version)"

# 检查项目安装
if ! python -c "import artpm_agent" 2>/dev/null; then
    echo "  ⚠️  项目未安装，正在安装..."
    pip install -e . --quiet
fi
echo "  ✅ 项目已安装（开发模式）"

# 检查配置
if [ ! -f .env ]; then
    echo "  ⚠️  创建 .env 配置..."
    cp .env.example .env
fi
echo "  ✅ 配置文件存在"

# 检查数据目录
mkdir -p data artpm_agent/logs
echo "  ✅ 数据目录已就绪"

# 启动应用
echo ""
echo "🚀 启动应用..."
echo "  访问地址: http://localhost:8501"
echo "  按 Ctrl+C 停止应用"
echo ""

cd "$(dirname "$0")"
python -m streamlit run artpm_agent/app.py \
    --server.address 127.0.0.1 \
    --server.port 8501 \
    --server.headless true
