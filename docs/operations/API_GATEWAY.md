# ArtPM REST API 网关

网关使用 FastAPI 工厂创建，默认运行时不会在导入阶段启动 LLM：

```powershell
artpm-api
```

也可以直接运行 `python -m artpm_agent.api`。监听地址由
`ARTPM_API_HOST` / `ARTPM_API_PORT` 配置，默认是 `127.0.0.1:8765`。

生产部署建议由反向代理注入并覆盖以下请求头，客户端请求体不能设置这些字段：

| Header | 含义 |
| --- | --- |
| `X-Tenant-ID` | 已认证租户，缺省为 `local`（云端必须显式注入） |
| `X-Workspace-ID` | 全局唯一的规范工作区 ID |
| `X-Actor-ID` | 当前人类/服务主体 ID |
| `X-Actor-Role` | `user` 或 `admin` |
| `X-Actor-Kind` | `human` 或 `service`，审批接口只允许 `human` |
| `X-Gateway-Token` | 当启用共享密钥时的代理签名头 |

生产环境设置 `ARTPM_ENV=production` 后，网关会强制要求
`ARTPM_GATEWAY_SHARED_SECRET`（或 `create_app(..., gateway_secret=...)`）和
`X-Gateway-Token`。网关不会提供通配 CORS；需要跨域时应在已认证的反向代理层配置
明确的允许来源。

核心路由：

- `GET /health`、`GET /ready`
- `GET /v1/capabilities`
- `POST /v1/chat`
- 会话管理：`GET /v1/conversations`（`archived=true` 取归档桶）、
  `GET /v1/conversations/{id}/messages`、`PATCH /v1/conversations/{id}`
  （重命名 / 归档 / 恢复）、`DELETE /v1/conversations/{id}`（永久删除）
- 会话审批档位：`GET/PUT /v1/conversations/{id}/access-mode`
- 网关配置：`GET/PUT /v1/config`（**仅 admin**，密钥脱敏，写后热重载）
- `GET /v1/permissions`、`GET /v1/permissions/{id}`
- `POST /v1/permissions/{id}/approve`、`POST /v1/permissions/{id}/reject`
- `GET/POST /v1/workflows`
- `POST /v1/workflows/{id}/runs`
- `GET /v1/workflow-runs`、`GET /v1/workflow-runs/{id}`
- `POST /v1/workflow-runs/{id}/steps/{index}/approve|reject`

### 会话审批档位

每个会话持久化一档审批策略，由宿主而非模型强制。`controlled` 是失败即收紧的基线：
授权行缺失、过期或绑定不匹配时一律回落到它。

| 档位 | 语义 | 有效期 |
| --- | --- | --- |
| `read_only` | 仅放行纯查询；写入直接拒绝，不生成审批请求 | 7 天 |
| `controlled` | 每个写入逐项确认（默认档） | 不落库 |
| `full_access` | 低/中风险自动放行，高风险仍需确认 | 1 小时 |

授权行存放在 `data/conversations.db` 的 `conversation_access_grants` 表，权限库
schema 版本为 `4`。React 前端的 composer 模式选择器与 Streamlit 共用同一套语义，
Streamlit 仅暴露 `controlled` / `full_access` 两档。

### 网关配置接口

`GET /v1/config` 返回 `artpm_agent.api.config_admin.CONFIG_GROUPS` 白名单内的环境
变量，按八个分组组织。密钥字段的 `value` 恒为空，只返回 `configured` 与脱敏
`preview`，明文不经过该接口。

`PUT /v1/config` 接受 `{"values": {"KEY": "value"}}`，写入项目根目录 `.env`：仅白名单
键可写；整批先校验后落盘；保留注释、键顺序与行尾风格；写后热重载并释放缓存的模型
客户端。宿主不支持热重载时返回 `restart_required: true`。两个端点都要求
`X-Actor-Role: admin`，其他角色返回 `403`。

审批请求只接受 `expected_version`，服务端从持久化请求恢复原始 payload，执行前做
SHA-256 绑定、一次性 CAS claim 和脱敏；模型输出不能通过 API 自批。工作流创建时，
Skill/capability、`side_effect`、`approval` 和 `read_only` 均由服务端 allowlist 与
`DEFAULT_RISK_POLICY` 派生，客户端声明不会降低风险等级。

`workspace_id` 是底层 SQLite 的隔离键，必须由云端入口保证全局唯一并完成
`tenant -> workspace` membership 校验；网关不会根据任意请求头自动创建工作区。

应用工厂也接受注入的 `GatewayServices` 和身份 resolver，便于云端替换 Harness、
插件注册表、模型网关和持久化实现：

```python
from artpm_agent.api import create_app

app = create_app(services=my_services, identity_resolver=my_resolver)
```

## 本地前后端联调

本地启动会同时提供 Streamlit `8501` 和 FastAPI `8765`。前端侧边栏会以非阻塞方式探测
`GET /health`，并显示“已连接 / 部分可用 / 本地直连”状态；`GET /ready` 是给反向代理和容器编排使用的就绪别名。

浏览器跨域来源必须显式配置，例如：

```dotenv
ARTPM_CORS_ORIGINS=http://127.0.0.1:8501,http://localhost:8501
```

不允许使用 `*`。所有错误均返回 `error.code`、用户可读的 `error.message` 和 `request_id`，验证错误不会回显原始请求体或敏感值。
