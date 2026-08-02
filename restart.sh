#!/bin/bash
# ArtPM Agent 项目重启脚本

echo "🚀 重启 ArtPM Agent 项目"
echo "================================"
echo ""

# 1. 停止所有运行中的进程
echo "📝 步骤 1: 停止运行中的服务..."
pkill -f "streamlit run" 2>/dev/null
pkill -f "python.*app.py" 2>/dev/null
pkill -f "artpm_agent.api" 2>/dev/null
sleep 2
echo "✅ 已停止所有服务"
echo ""

# 2. 检查 Python 环境
echo "📝 步骤 2: 检查 Python 环境..."
python --version
echo "✅ Python 环境正常"
echo ""

# 3. 安装/更新依赖
echo "📝 步骤 3: 更新依赖..."
pip install -e . -q
echo "✅ 依赖已更新"
echo ""

# 4. 清理临时文件
echo "📝 步骤 4: 清理临时文件..."
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null
find . -type f -name "*.pyc" -delete 2>/dev/null
echo "✅ 临时文件已清理"
echo ""

echo "================================"
echo "🎯 项目已准备就绪!"
echo ""
echo "📝 启动选项:"
echo "1. UI + API 一起启动: python start_with_checks.py"
echo "2. 仅 REST API: python -m artpm_agent.api"
echo "3. 运行测试: pytest tests/ -v"
echo "================================"
