# ArtPM Agent REST API Gateway 完整文档

**版本**: v1  
**协议**: REST / JSON  
**认证**: Trusted Header + Shared Secret  
**最后更新**: 2026-07-22

---

## 📖 目录

1. [概述](#概述)
2. [认证与安全](#认证与安全)
3. [API 端点清单](#api-端点清单)
4. [请求/响应格式](#请求响应格式)
5. [错误处理](#错误处理)
6. [使用示例](#使用示例)
7. [部署指南](#部署指南)

---

## 概述

### 设计理念

ArtPM Agent REST API Gateway 是一个**租户隔离**的 HTTP 服务,提供以下核心能力:

- **Chat 对话**: 多轮对话接口
- **Capabilities 查询**: 查询可用技能列表
- **Permission 审批**: 用户确认写入操作
- **Workflow 编排**: 持久化工作流定义与执行

### 架构特点

1. **租户隔离**: 所有 API 都基于 `workspace_id` 隔离数据
2. **依赖注入**: 核心服务可替换 (适配云部署)
3. **CAS 一致性**: 审批/执行使用乐观锁
4. **Lazy 初始化**: Agent 实例按需加载 (health check 不触发)

### 技术栈

- **FastAPI 0.135.1**: Web 框架
- **Uvicorn 0.51.0**: ASGI 服务器
- **Pydantic 2.7.4**: 请求/响应验证
- **SQLite**: 持久化存储 (可替换 PostgreSQL)

---

## 认证与安全

### 认证流程

```
HTTP Request
  │
  ▼
┌────────────────────────────────────┐
│ CORS 中间件                         │
│ - 验证来源域名                      │
│ - 允许的方法: GET, POST, OPTIONS   │
└──────────┬─────────────────────────┘
           │
           ▼
┌────────────────────────────────────┐
│ 认证中间件                          │
│ - 检查 X-Gateway-Token            │
│ - 比对 ARTPM_GATEWAY_SHARED_SECRET│
└──────────┬─────────────────────────┘
           │
           ▼
┌────────────────────────────────────┐
│ 租户上下文提取                      │
│ - X-Tenant-ID                      │
│ - X-Workspace-ID                   │
│ - X-Actor-ID                       │
│ - X-Actor-Role (user/admin)       │
└──────────┬─────────────────────────┘
           │
           ▼
┌────────────────────────────────────┐
│ 路由处理                            │
│ - 业务逻辑执行                      │
│ - 数据访问控制                      │
└────────────────────────────────────┘
```

### 认证方式

#### 生产环境 (必需)
```bash
# 1. 设置环境变量
export ARTPM_ENV=production
export ARTPM_GATEWAY_SHARED_SECRET="<random-256-bit-secret>"

# 2. HTTP 请求必须包含 Header
curl -X POST https://api.example.com/v1/chat \
  -H "X-Gateway-Token: <shared-secret>" \
  -H "X-Workspace-ID: workspace-123" \
  -H "X-Actor-ID: user-456" \
  -H "Content-Type: application/json" \
  -d '{"message": "你好"}'
```

#### 开发环境 (可选)
```bash
# 本地开发时,仅从 localhost 接受请求
# 无需 shared secret (自动检测 client.host)

curl -X POST http://127.0.0.1:8765/v1/chat \
  -H "X-Workspace-ID: test-workspace" \
  -H "X-Actor-ID: test-user" \
  -H "Content-Type: application/json" \
  -d '{"message": "你好"}'
```

### 安全机制

1. **Gateway Token**: 防止未授权访问
2. **租户隔离**: 每个请求绑定 workspace_id
3. **角色权限**: user / admin 权限分离
4. **CORS 白名单**: 明确允许的来源域名
5. **输入验证**: Pydantic 自动验证请求体

---

## API 端点清单

### 系统端点

#### GET /health
**健康检查** (无需认证)

```bash
curl http://127.0.0.1:8765/health
```

**响应**:
```json
{
  "status": "ok",
  "service": "artpm-agent-api",
  "version": "v1",
  "checks": {
    "conversation_store": {"status": "ok"},
    "permission_store": {"status": "ok"},
    "workflow_store": {"status": "ok"}
  }
}
```

#### GET /ready
**就绪检查** (无需认证)

```bash
curl http://127.0.0.1:8765/ready
```

**响应**:
- `200 OK`: 所有存储就绪
- `503 Service Unavailable`: 存储降级

---

### 对话端点

#### POST /v1/chat
**发送对话消息**

**请求**:
```bash
curl -X POST http://127.0.0.1:8765/v1/chat \
  -H "X-Workspace-ID: workspace-123" \
  -H "X-Actor-ID: user-456" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "报价12万成本8万帮我算利润",
    "conversation_id": "conv-789",
    "attachments": []
  }'
```

**请求体 Schema**:
```json
{
  "message": "string (1-8000 chars, required)",
  "conversation_id": "string (optional, 如果省略则创建新对话)",
  "title": "string (optional, 对话标题)",
  "attachments": [
    {
      "name": "string (required)",
      "media_type": "string (optional)",
      "size_bytes": "integer (optional)"
    }
  ]
}
```

**响应 (成功)**:
```json
{
  "conversation_id": "conv-789",
  "turn_id": "turn-abc123",
  "response": "根据您的报价，利润计算如下：\n报价: ¥120,000\n成本: ¥80,000\n...",
  "success": true,
  "awaiting_approval": false,
  "handled_by": "smart_task_allocator",
  "metadata": {
    "skill_used": "profit_calculation",
    "execution_time_ms": 45
  },
  "artifacts": []
}
```

**响应 (需要审批)**:
```json
{
  "conversation_id": "conv-789",
  "turn_id": "turn-abc123",
  "response": "此操作需要您的确认:\n- 删除 5 个项目\n- 影响范围: 所有团队成员",
  "success": true,
  "awaiting_approval": true,
  "permission_request_id": "perm-xyz789",
  "handled_by": "delete_projects_skill",
  "metadata": {}
}
```

**错误响应**:
```json
{
  "error": {
    "code": "chat_failed",
    "message": "模型服务未能完成请求，请稍后重试。",
    "request_id": "req-123abc"
  }
}
```

---

### 能力查询端点

#### GET /v1/capabilities
**查询可用技能列表**

**请求**:
```bash
curl -X GET http://127.0.0.1:8765/v1/capabilities \
  -H "X-Workspace-ID: workspace-123" \
  -H "X-Actor-ID: user-456"
```

**响应**:
```json
{
  "workspace_id": "workspace-123",
  "items": [
    {
      "name": "profit_calculation",
      "description": "利润测算",
      "version": "1.0",
      "risk": "low",
      "read_only": true,
      "requires_approval": false,
      "source": "builtin"
    },
    {
      "name": "delete_projects",
      "description": "删除项目",
      "version": "1.0",
      "risk": "high",
      "read_only": false,
      "requires_approval": true,
      "source": "builtin"
    },
    {
      "name": "weather_query",
      "description": "天气查询",
      "version": "1.0",
      "risk": "low",
      "read_only": true,
      "requires_approval": false,
      "source": "plugin",
      "plugin_id": "com.example.weather",
      "plugin_version": "1.0.0"
    }
  ]
}
```

---

### 审批端点

#### GET /v1/permissions
**列出待审批请求**

**请求**:
```bash
curl -X GET "http://127.0.0.1:8765/v1/permissions?conversation_id=conv-789&limit=10" \
  -H "X-Workspace-ID: workspace-123" \
  -H "X-Actor-ID: user-456"
```

**响应**:
```json
{
  "items": [
    {
      "id": "perm-xyz789",
      "conversation_id": "conv-789",
      "turn_id": "turn-abc123",
      "action": "skill.delete_projects",
      "source": "skill",
      "risk": "high",
      "state": "pending",
      "created_at": "2026-07-22T10:30:00Z",
      "payload": {
        "skill_name": "delete_projects",
        "inputs": {"project_ids": [1, 2, 3]}
      }
    }
  ]
}
```

#### GET /v1/permissions/{request_id}
**查询单个审批请求**

**请求**:
```bash
curl -X GET http://127.0.0.1:8765/v1/permissions/perm-xyz789 \
  -H "X-Workspace-ID: workspace-123" \
  -H "X-Actor-ID: user-456"
```

#### POST /v1/permissions/{request_id}/approve
**批准审批请求**

**请求**:
```bash
curl -X POST http://127.0.0.1:8765/v1/permissions/perm-xyz789/approve \
  -H "X-Workspace-ID: workspace-123" \
  -H "X-Actor-ID: user-456" \
  -H "X-Actor-Role: admin" \
  -H "Content-Type: application/json" \
  -d '{
    "expected_version": 0,
    "acknowledged_risk": "high"
  }'
```

**响应**:
```json
{
  "item": {
    "id": "perm-xyz789",
    "state": "executed",
    "decision": "approved",
    "decided_by": "user-456",
    "decided_at": "2026-07-22T10:35:00Z",
    "execution_result": {
      "success": true,
      "deleted_count": 3
    }
  }
}
```

#### POST /v1/permissions/{request_id}/reject
**拒绝审批请求**

**请求**:
```bash
curl -X POST http://127.0.0.1:8765/v1/permissions/perm-xyz789/reject \
  -H "X-Workspace-ID: workspace-123" \
  -H "X-Actor-ID: user-456" \
  -H "Content-Type: application/json" \
  -d '{
    "expected_version": 0
  }'
```

---

### 工作流端点

#### GET /v1/workflows
**列出工作流定义**

```bash
curl -X GET "http://127.0.0.1:8765/v1/workflows?enabled_only=true" \
  -H "X-Workspace-ID: workspace-123" \
  -H "X-Actor-ID: user-456"
```

#### POST /v1/workflows
**创建工作流定义** (需要 admin 权限)

```bash
curl -X POST http://127.0.0.1:8765/v1/workflows \
  -H "X-Workspace-ID: workspace-123" \
  -H "X-Actor-ID: admin-user" \
  -H "X-Actor-Role: admin" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "my-workflow",
    "version": 1,
    "name": "自动化项目设置",
    "description": "自动创建项目并分配任务",
    "enabled": true,
    "priority": 0,
    "trigger": {
      "keywords": ["创建项目", "新项目"],
      "min_keyword_matches": 1
    },
    "steps": [
      {
        "id": "step-1",
        "skill_id": "create_project",
        "capability": "data.write",
        "input_map": {
          "project_name": "{{input.project_name}}"
        }
      }
    ]
  }'
```

#### POST /v1/workflows/{workflow_id}/runs
**执行工作流**

```bash
curl -X POST http://127.0.0.1:8765/v1/workflows/my-workflow/runs \
  -H "X-Workspace-ID: workspace-123" \
  -H "X-Actor-ID: user-456" \
  -H "Content-Type: application/json" \
  -d '{
    "conversation_id": "conv-789",
    "input_data": {
      "project_name": "新游戏美术项目"
    },
    "context_data": {},
    "auto_resume": true
  }'
```

---

## 请求/响应格式

### 通用响应格式

#### 成功响应
```json
{
  "data": { ... },
  "metadata": { ... }
}
```

#### 错误响应
```json
{
  "error": {
    "code": "error_code",
    "message": "Human-readable error message",
    "request_id": "req-123abc",
    "fields": [  // 仅验证错误时存在
      {
        "loc": ["body", "message"],
        "type": "value_error.missing",
        "message": "request field failed validation"
      }
    ]
  }
}
```

### HTTP 状态码

| 状态码 | 含义 | 使用场景 |
|--------|------|----------|
| 200 OK | 成功 | 查询成功 |
| 202 Accepted | 已接受 | 需要审批的操作 |
| 400 Bad Request | 请求错误 | 参数验证失败 |
| 401 Unauthorized | 未授权 | 缺少/无效认证信息 |
| 403 Forbidden | 禁止访问 | 权限不足 |
| 404 Not Found | 未找到 | 资源不存在 |
| 409 Conflict | 冲突 | 乐观锁版本冲突 |
| 422 Unprocessable Entity | 无法处理 | 语义验证失败 |
| 502 Bad Gateway | 网关错误 | 后端服务失败 |
| 503 Service Unavailable | 服务不可用 | 存储/依赖不可用 |

---

## 错误处理

### 错误码清单

| 错误码 | HTTP 状态 | 说明 |
|--------|-----------|------|
| `invalid_identity` | 401 | 认证信息缺失或无效 |
| `invalid_tenant_context` | 401 | 租户上下文无效 |
| `human_confirmation_required` | 403 | 操作需要人类确认 |
| `admin_required` | 403 | 需要 admin 权限 |
| `workspace_not_found` | 404 | 工作空间不存在 |
| `conversation_not_found` | 404 | 对话不存在 |
| `permission_not_found` | 404 | 审批请求不存在 |
| `workflow_not_found` | 404 | 工作流不存在 |
| `state_conflict` | 409 | 版本冲突 (乐观锁) |
| `validation_error` | 422 | 请求验证失败 |
| `chat_handler_failed` | 502 | 对话处理失败 |
| `permission_execution_failed` | 502 | 审批执行失败 |
| `storage_unavailable` | 503 | 存储不可用 |
| `internal_error` | 500 | 内部错误 |

### 错误处理最佳实践

```python
import requests

def call_artpm_api(endpoint, payload):
    try:
        response = requests.post(
            f"http://127.0.0.1:8765{endpoint}",
            json=payload,
            headers={
                "X-Workspace-ID": "workspace-123",
                "X-Actor-ID": "user-456"
            },
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 401:
            print("认证失败,请检查凭据")
        elif e.response.status_code == 403:
            print("权限不足")
        elif e.response.status_code == 404:
            print("资源不存在")
        elif e.response.status_code == 409:
            print("版本冲突,请重试")
        elif e.response.status_code >= 500:
            print("服务器错误,请稍后重试")
        else:
            error_data = e.response.json()
            print(f"API 错误: {error_data['error']['message']}")
    
    except requests.exceptions.Timeout:
        print("请求超时")
    
    except requests.exceptions.ConnectionError:
        print("连接失败")
```

---

## 使用示例

### Python 示例

```python
import requests

class ArtPMAPIClient:
    def __init__(self, base_url, workspace_id, actor_id, gateway_token=None):
        self.base_url = base_url
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.gateway_token = gateway_token
    
    def _headers(self, **extras):
        headers = {
            "X-Workspace-ID": self.workspace_id,
            "X-Actor-ID": self.actor_id,
            "Content-Type": "application/json"
        }
        if self.gateway_token:
            headers["X-Gateway-Token"] = self.gateway_token
        headers.update(extras)
        return headers
    
    def chat(self, message, conversation_id=None):
        """发送对话消息"""
        payload = {"message": message}
        if conversation_id:
            payload["conversation_id"] = conversation_id
        
        response = requests.post(
            f"{self.base_url}/v1/chat",
            json=payload,
            headers=self._headers(),
            timeout=30
        )
        response.raise_for_status()
        return response.json()
    
    def get_capabilities(self):
        """查询可用能力"""
        response = requests.get(
            f"{self.base_url}/v1/capabilities",
            headers=self._headers()
        )
        response.raise_for_status()
        return response.json()["items"]
    
    def list_pending_approvals(self, conversation_id=None):
        """列出待审批请求"""
        params = {}
        if conversation_id:
            params["conversation_id"] = conversation_id
        
        response = requests.get(
            f"{self.base_url}/v1/permissions",
            params=params,
            headers=self._headers()
        )
        response.raise_for_status()
        return response.json()["items"]
    
    def approve_permission(self, request_id, expected_version, risk_level=None):
        """批准审批请求"""
        payload = {"expected_version": expected_version}
        if risk_level:
            payload["acknowledged_risk"] = risk_level
        
        response = requests.post(
            f"{self.base_url}/v1/permissions/{request_id}/approve",
            json=payload,
            headers=self._headers(**{"X-Actor-Role": "admin"})
        )
        response.raise_for_status()
        return response.json()["item"]


# 使用示例
client = ArtPMAPIClient(
    base_url="http://127.0.0.1:8765",
    workspace_id="workspace-123",
    actor_id="user-456"
)

# 发送对话
result = client.chat("报价12万成本8万帮我算利润")
print(result["response"])

# 查询能力
capabilities = client.get_capabilities()
for cap in capabilities:
    print(f"- {cap['name']}: {cap['description']}")

# 查询待审批
approvals = client.list_pending_approvals()
for approval in approvals:
    print(f"待审批: {approval['action']} (风险: {approval['risk']})")
```

### JavaScript 示例

```javascript
class ArtPMAPIClient {
  constructor(baseURL, workspaceId, actorId, gatewayToken = null) {
    this.baseURL = baseURL;
    this.workspaceId = workspaceId;
    this.actorId = actorId;
    this.gatewayToken = gatewayToken;
  }

  _headers(extras = {}) {
    const headers = {
      'X-Workspace-ID': this.workspaceId,
      'X-Actor-ID': this.actorId,
      'Content-Type': 'application/json',
      ...extras
    };
    if (this.gatewayToken) {
      headers['X-Gateway-Token'] = this.gatewayToken;
    }
    return headers;
  }

  async chat(message, conversationId = null) {
    const payload = { message };
    if (conversationId) {
      payload.conversation_id = conversationId;
    }

    const response = await fetch(`${this.baseURL}/v1/chat`, {
      method: 'POST',
      headers: this._headers(),
      body: JSON.stringify(payload)
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${await response.text()}`);
    }

    return response.json();
  }

  async getCapabilities() {
    const response = await fetch(`${this.baseURL}/v1/capabilities`, {
      headers: this._headers()
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const data = await response.json();
    return data.items;
  }

  async listPendingApprovals(conversationId = null) {
    const params = new URLSearchParams();
    if (conversationId) {
      params.append('conversation_id', conversationId);
    }

    const response = await fetch(
      `${this.baseURL}/v1/permissions?${params}`,
      { headers: this._headers() }
    );

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const data = await response.json();
    return data.items;
  }
}

// 使用示例
const client = new ArtPMAPIClient(
  'http://127.0.0.1:8765',
  'workspace-123',
  'user-456'
);

// 发送对话
client.chat('报价12万成本8万帮我算利润')
  .then(result => console.log(result.response))
  .catch(error => console.error('Error:', error));
```

---

## 部署指南

### 本地开发

```bash
# 1. 安装依赖
pip install -e .

# 2. 启动 API Gateway
python -m artpm_agent.api

# 3. 访问
curl http://127.0.0.1:8765/health
```

### Docker 部署

```dockerfile
# Dockerfile
FROM python:3.10-slim

WORKDIR /app
COPY . /app

RUN pip install -e .

EXPOSE 8765

CMD ["python", "-m", "artpm_agent.api"]
```

```bash
# 构建镜像
docker build -t artpm-agent-api .

# 运行容器
docker run -d \
  -p 8765:8765 \
  -e ARTPM_ENV=production \
  -e ARTPM_GATEWAY_SHARED_SECRET=<secret> \
  -v ./data:/app/data \
  artpm-agent-api
```

### 生产部署 (Nginx + Uvicorn)

**nginx.conf**:
```nginx
upstream artpm_api {
    server 127.0.0.1:8765;
    server 127.0.0.1:8766;  # 多实例
}

server {
    listen 443 ssl http2;
    server_name api.example.com;

    ssl_certificate /path/to/cert.pem;
    ssl_certificate_key /path/to/key.pem;

    location / {
        proxy_pass http://artpm_api;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # 注入认证 Header
        proxy_set_header X-Gateway-Token "<shared-secret>";
        
        # 超时设置
        proxy_connect_timeout 30s;
        proxy_send_timeout 30s;
        proxy_read_timeout 30s;
    }
}
```

**Systemd Service**:
```ini
# /etc/systemd/system/artpm-api.service
[Unit]
Description=ArtPM Agent API Gateway
After=network.target

[Service]
Type=simple
User=artpm
WorkingDirectory=/opt/artpm-agent
Environment="ARTPM_ENV=production"
Environment="ARTPM_GATEWAY_SHARED_SECRET=<secret>"
ExecStart=/opt/artpm-agent/venv/bin/python -m artpm_agent.api
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
# 启动服务
sudo systemctl daemon-reload
sudo systemctl enable artpm-api
sudo systemctl start artpm-api
```

---

## 附录

### Postman Collection

Postman Collection 不随仓库发布；请以 `/docs` 暴露的 OpenAPI schema 为准导入。

### OpenAPI Specification

访问 `/docs` 查看交互式 API 文档 (Swagger UI)  
访问 `/redoc` 查看 ReDoc 文档

### 更新日志

- **2026-07-22**: 补充完整端点示例和错误处理
- **2026-07-22**: 初始版本,包含认证和基础架构

---

**版本**: v1.0  
**作者**: ArtPM Agent Team  
**许可**: MIT License
