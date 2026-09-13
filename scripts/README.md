# Scripts

仓库维护脚本统一放在本目录，并从项目根目录执行。脚本不应把密钥、真实用户数据或
临时输出写入源码目录。

| 分类 | 入口 | 用途 |
| --- | --- | --- |
| 测试 | `test_fast.ps1` | 快速离线回归 |
| 测试 | `test_all.ps1` | 完整离线回归，包含慢速 UI |
| 测试 | `test_integration.ps1` | 显式外部集成测试 |
| 测试 | `test_benchmark.ps1` | 性能基准测试 |
| 覆盖率 | `coverage_core.py` / `coverage_core.ps1` | 核心边界 90% 覆盖率门禁 |
| 覆盖率 | `coverage_report.sh` | 生成 `artifacts/quality/` 报告 |
| 数据 | `backup_data.py` / `backup_data.sh` | 备份 `data/` 数据库 |
| 数据库 | `migrate_postgres.py` | Compose PostgreSQL 迁移任务 |
| 启动 | `prepare_streamlit_port.ps1` | 识别并处理本项目占用的 UI 端口 |
| 检查 | `verify_optimization.py` | 启动、文档和优化契约检查 |
| 检查 | `verify_new_features.py` | 历史功能验证脚本，按需运行 |
| 看板 | `telemetry_dashboard_app.py` | 独立 Streamlit 遥测看板入口 |

推荐先运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/test_fast.ps1
ruff check artpm_agent tests
python -m compileall -q artpm_agent
```

运行脚本产生的 coverage、类型检查、基准、截图和 HTML 报告归入 `artifacts/`，不要
重新写回仓库根目录。
