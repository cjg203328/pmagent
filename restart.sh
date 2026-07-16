#!/bin/bash
# ArtPM Agent - 完整重启脚本

echo "╔════════════════════════════════════════════════════════╗"
echo "║                                                        ║"
echo "║         🔄 ArtPM Agent 重启脚本                       ║"
echo "║                                                        ║"
echo "╚════════════════════════════════════════════════════════╝"
echo ""

# 进入项目目录
cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"
echo "📂 项目目录: $PROJECT_DIR"
echo ""

# 1. 停止现有进程
echo "🛑 停止现有进程..."
if command -v taskkill &> /dev/null; then
    # Windows
    taskkill /F /IM streamlit.exe 2>/dev/null && echo "  ✅ 已停止 Streamlit 进程" || echo "  ℹ️  没有运行中的进程"
else
    # Linux/Mac
    pkill -f "streamlit run" && echo "  ✅ 已停止 Streamlit 进程" || echo "  ℹ️  没有运行中的进程"
fi
sleep 2
echo ""

# 2. 检查 Python 环境
echo "🐍 检查 Python 环境..."
if ! command -v python &> /dev/null; then
    echo "  ❌ 错误: 未找到 Python"
    exit 1
fi
PYTHON_VERSION=$(python --version 2>&1)
echo "  ✅ $PYTHON_VERSION"
echo ""

# 3. 检查项目安装
echo "📦 检查项目安装..."
if python -c "import artpm_agent" 2>/dev/null; then
    echo "  ✅ 项目已安装（开发模式）"
else
    echo "  ⚠️  项目未安装，正在安装..."
    pip install -e . --quiet
    echo "  ✅ 安装完成"
fi
echo ""

# 4. 检查配置文件
echo "⚙️  检查配置文件..."
if [ ! -f .env ]; then
    echo "  ⚠️  .env 不存在，从示例创建..."
    cp .env.example .env
    echo "  ✅ 已创建 .env 配置文件"
    echo "  ℹ️  请编辑 .env 文件设置 API Key"
else
    echo "  ✅ 配置文件存在"
fi
echo ""

# 5. 检查数据目录
echo "💾 检查数据目录..."
mkdir -p data artpm_agent/logs
if [ -f data/artpm.db ]; then
    DB_SIZE=$(du -h data/artpm.db | cut -f1)
    echo "  ✅ 业务数据库: $DB_SIZE"
else
    echo "  ℹ️  业务数据库将在首次运行时创建"
fi

if [ -f data/conversations.db ]; then
    CONV_SIZE=$(du -h data/conversations.db | cut -f1)
    echo "  ✅ 对话数据库: $CONV_SIZE"
else
    echo "  ℹ️  对话数据库将在首次运行时创建"
fi
echo ""

# 6. 清理缓存（可选）
echo "🧹 清理 Python 缓存..."
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
find . -name "*.pyc" -delete 2>/dev/null
echo "  ✅ 缓存已清理"
echo ""

# 7. 启动应用
echo "🚀 启动应用..."
echo "  访问地址: http://localhost:8501"
echo "  按 Ctrl+C 停止应用"
echo ""
echo "════════════════════════════════════════════════════════"
echo ""

python -m streamlit run artpm_agent/app.py \
    --server.address 127.0.0.1 \
    --server.port 8501 \
    --server.headless true
