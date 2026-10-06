# 2026-10-06

- 按用户授权开展冗余清理审计，确认 `ArtPMAgent`、`RequestOrchestrator`、MCP 多后端和根目录兼容入口仍被运行时、CLI 或测试引用，暂不删除。
- 抽取 `artpm_agent/utils/ocr_config_values.py`，让 `ocr_runtime.py` 与 `unlimited_ocr.py` 共享布尔值、整数和浮点范围解析，移除 6 个重复私有实现。
- 验证：OCR/MinerU 定向测试 26 passed；相关 Ruff 与 compileall 通过。
- 快速回归：1424 passed，4 项既有/环境相关失败（日志进程角色文件名 3 项，模型 failover 1 项）。

## React 前端交付与会话管理

- 修复欢迎页标题在宽屏被强制换行的问题：标题容器 `max-w-2xl` 容不下 44px 字号下的
  756px 整句，导致强调短语从词中劈开。改为 `max-w-3xl`、去掉 `text-balance`、两个短语
  各自 `[text-wrap:nowrap]`，字号下限 28px→22px，并在 ≤359px 降到 20px。实测 1512/1440/
  1280/1180 单行，1100/1024/900/768 在逗号处两行，480/414/375/360/320 两行，全部无溢出。
- 修掉空会话首屏被顶出视口：无消息时 `scrollTop = 0` 而不是 `scrollHeight`。
- 侧边栏补齐会话管理：行内菜单支持归档/删除，标题栏归档图标切换「最近会话 ↔ 已归档」，
  归档桶内可恢复，删除走二次确认。后端补 `GET /v1/conversations?archived=true` 使归档可逆。
- 新增会话审批档位：`read_only` / `controlled` / `full_access` 三档，与 Codex 的确认态对齐。
  前端 `access-mode-picker.tsx` 提供下拉选择；后端 `access_mode.py` 新增 `deny` 决策、
  `permission_store` schema v4 放宽 `CHECK` 约束、`services.py` 把持久化档位注入执行上下文。
  关键修复：`/v1/chat` 此前从未读取该档位，切换只是"存了个状态"。
- 新增网关配置页：后端 `api/config_admin.py` 提供白名单 `.env` 读写（密钥脱敏、整批校验、
  保留注释与行尾、写后热重载），前端 `pages/settings.tsx` 渲染八个分组 55 个字段。
- 修掉两个既有缺陷：`.env.example` 的 `ARTPM_LOG_FILE=artpm.log` 废掉了按进程角色隔离日志
  的设计；`gateway.py` 让环境变量覆盖了调用方显式传入的 `failover_max_attempts`。
- 验证：后端全量 1504 passed / 21 skipped（`.env` 在位），CI 等效 1501 passed；前端
  typecheck、test、build 全绿；浏览器端到端确认归档/恢复/删除全链路与配置保存热重载。
- 清理冗余缓存与截图：释放约 966 MB（`.cache/verify`、`.cache/tmp`、`__pycache__`、`build` 等），
  `artifacts/screenshots/` 的 11 个设计参考图保留。
