#!/bin/bash
# 测试覆盖率报告生成脚本
# 使用方法: ./scripts/coverage_report.sh

set -e

QUALITY_DIR="artifacts/quality"
mkdir -p "$QUALITY_DIR"
export COVERAGE_FILE="$QUALITY_DIR/.coverage"

echo "🧪 Running test coverage analysis..."
echo ""

# 运行测试并生成覆盖率报告
# This is a report command, not the CI baseline gate. CI enforces the current
# whole-project baseline explicitly (`--cov-fail-under=20`) in its own job.
pytest --cov=artpm_agent \
  --cov-report="html:$QUALITY_DIR/htmlcov" \
  --cov-report="json:$QUALITY_DIR/coverage.json" \
  --cov-report="xml:$QUALITY_DIR/coverage.xml" \
  -q --tb=short

echo ""
echo "✅ Coverage report generated successfully!"
echo ""
echo "📊 View detailed report:"
echo "   HTML: $QUALITY_DIR/htmlcov/index.html"
echo "   JSON: $QUALITY_DIR/coverage.json"
echo "   XML:  $QUALITY_DIR/coverage.xml"
echo ""
echo "📈 Core coverage gate: scripts/coverage_core.py (≥ 90%)"
echo ""

# 自动打开HTML报告 (可选)
if [ "$1" == "--open" ]; then
    if command -v xdg-open &> /dev/null; then
        xdg-open "$QUALITY_DIR/htmlcov/index.html"
    elif command -v open &> /dev/null; then
        open "$QUALITY_DIR/htmlcov/index.html"
    elif command -v start &> /dev/null; then
        start "$QUALITY_DIR/htmlcov/index.html"
    else
        echo "💡 Tip: Manually open $QUALITY_DIR/htmlcov/index.html in your browser"
    fi
fi
