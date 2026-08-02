#!/bin/bash
# 测试覆盖率报告生成脚本
# 使用方法: ./scripts/coverage_report.sh

set -e

echo "🧪 Running test coverage analysis..."
echo ""

# 运行测试并生成覆盖率报告
# 覆盖率范围与 fail-under 门槛统一由 pytest.ini 提供，此处仅追加 HTML 报告。
pytest --cov-report=html -q --tb=short

echo ""
echo "✅ Coverage report generated successfully!"
echo ""
echo "📊 View detailed report:"
echo "   HTML: htmlcov/index.html"
echo "   JSON: coverage.json"
echo "   XML:  coverage.xml"
echo ""
echo "📈 Coverage threshold: ≥ 70%"
echo ""

# 自动打开HTML报告 (可选)
if [ "$1" == "--open" ]; then
    if command -v xdg-open &> /dev/null; then
        xdg-open htmlcov/index.html
    elif command -v open &> /dev/null; then
        open htmlcov/index.html
    elif command -v start &> /dev/null; then
        start htmlcov/index.html
    else
        echo "💡 Tip: Manually open htmlcov/index.html in your browser"
    fi
fi
