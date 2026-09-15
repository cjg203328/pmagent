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

仓库自带的 Compose/Caddy 示例使用一组由部署环境注入的固定身份头：
`ARTPM_GATEWAY_TENANT_ID`、`ARTPM_GATEWAY_WORKSPACE_ID`、
`ARTPM_GATEWAY_ACTOR_ID`（以及可选的 role/kind）。Caddy 会先删除客户端同名头，
再注入这些值；多用户部署必须替换为能从认证会话派生身份的可信网关。

核心路由：

- `GET /health`
- `GET /v1/capabilities`
- `POST /v1/chat`
- `POST /v1/chat/stream`（SSE：turn_start、事件增量、snapshot、turn_end）
- `GET/POST /v1/workspaces`
- `POST /v1/search`
- `GET /v1/permissions`、`GET /v1/permissions/{id}`
- `POST /v1/permissions/{id}/approve`、`POST /v1/permissions/{id}/reject`
- `GET/POST /v1/workflows`
- `POST /v1/workflows/{id}/runs`
- `GET /v1/workflow-runs`、`GET /v1/workflow-runs/{id}`
- `POST /v1/workflow-runs/{id}/steps/{index}/approve|reject`

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

## 网站嵌入（可选）

嵌入能力默认关闭。启用时至少配置：

```dotenv
ARTPM_EMBED_ENABLED=1
ARTPM_EMBED_SECRET=<server-only-signing-secret>
ARTPM_EMBED_PUBLISH_TOKEN=<server-only-publish-token>
ARTPM_EMBED_WORKSPACE_ID=local-default
ARTPM_EMBED_ALLOWED_ORIGINS=https://your-site.example
```

端点为 `/embed/{channel}/config`、`exchange`、`session`、`chat`。发布令牌只
能由嵌入站点服务端提交到 `exchange`；浏览器会话令牌绑定 channel、tenant、
workspace 和精确 `Origin`，并受短期过期、限流和 `frame-ancestors` 约束。前端
`postMessage` 仍需校验 `event.origin` 和 `event.source`。

不允许使用 `*`。所有错误均返回 `error.code`、用户可读的 `error.message` 和 `request_id`，验证错误不会回显原始请求体或敏感值。
