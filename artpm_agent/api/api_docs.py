"""
API 文档生成器 - OpenAPI/Swagger 规范

自动生成 REST API 的完整文档
"""

from typing import Dict, Any

# OpenAPI 3.0 规范
OPENAPI_SPEC: Dict[str, Any] = {
    "openapi": "3.0.0",
    "info": {
        "title": "ArtPM Agent API",
        "description": "企业级多租户项目管理系统 REST API",
        "version": "1.0.0",
        "contact": {
            "name": "ArtPM Team",
            "email": "support@artpm.com"
        }
    },
    "servers": [
        {
            "url": "http://localhost:8765",
            "description": "开发服务器"
        },
        {
            "url": "https://api.artpm.com",
            "description": "生产服务器"
        }
    ],
    "components": {
        "securitySchemes": {
            "BearerAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT"
            },
            "TenantHeader": {
                "type": "apiKey",
                "in": "header",
                "name": "X-Tenant-ID"
            },
            "WorkspaceHeader": {
                "type": "apiKey",
                "in": "header",
                "name": "X-Workspace-ID",
                "description": "当前请求的工作区标识。"
            },
            "ActorHeader": {
                "type": "apiKey",
                "in": "header",
                "name": "X-Actor-ID",
                "description": "由可信网关注入的操作者标识。"
            },
            "GatewayToken": {
                "type": "apiKey",
                "in": "header",
                "name": "X-Gateway-Token",
                "description": "部署启用共享密钥时使用；本地回环开发可省略。"
            }
        },
        "schemas": {
            "ChatRequest": {
                "type": "object",
                "additionalProperties": False,
                "required": ["message"],
                "properties": {
                    "message": {"type": "string", "minLength": 1, "maxLength": 8000},
                    "conversation_id": {"type": "string", "maxLength": 256},
                    "title": {"type": "string", "maxLength": 80},
                    "attachments": {"type": "array", "maxItems": 16, "items": {"type": "object"}}
                }
            },
            "Tenant": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "format": "uuid"},
                    "name": {"type": "string"},
                    "subdomain": {"type": "string"},
                    "plan_tier": {"type": "string", "enum": ["free", "pro", "enterprise"]},
                    "status": {"type": "string"},
                    "created_at": {"type": "string", "format": "date-time"}
                }
            },
            "Workspace": {
                "type": "object",
                "required": ["id", "profile_id", "name", "tenant_id"],
                "properties": {
                    "id": {"type": "string", "maxLength": 128, "pattern": "^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"},
                    "profile_id": {"type": "string", "maxLength": 128},
                    "name": {"type": "string"},
                    "tenant_id": {"type": "string"},
                    "settings": {"type": "object", "additionalProperties": True},
                    "created_at": {"type": "string", "format": "date-time"}
                }
            },
            "WorkspaceCreateRequest": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "name"],
                "properties": {
                    "id": {"type": "string", "minLength": 1, "maxLength": 128},
                    "name": {"type": "string", "minLength": 1, "maxLength": 80},
                    "profile_id": {"type": "string", "default": "local-default", "maxLength": 128},
                    "settings": {"type": "object", "additionalProperties": True}
                }
            },
            "KnowledgeSearchRequest": {
                "type": "object",
                "additionalProperties": False,
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 4000},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                    "resource_types": {"type": "array", "maxItems": 20, "items": {"type": "string"}},
                    "source_types": {"type": "array", "maxItems": 20, "items": {"type": "string"}},
                    "include_rules": {"type": "boolean", "default": True},
                    "max_text_chars": {"type": "integer", "minimum": 100, "maximum": 20000, "default": 4000},
                    "confidence_floor": {"type": "number", "minimum": 0, "maximum": 1, "default": 0}
                }
            },
            "KnowledgeSearchHit": {
                "type": "object",
                "description": "Workspace-scoped retrieval result with stable citation metadata.",
                "properties": {
                    "id": {"type": "string"},
                    "tenant_id": {"type": "string"},
                    "workspace_id": {"type": "string"},
                    "title": {"type": "string"},
                    "text": {"type": "string"},
                    "score": {"type": "number"},
                    "match_type": {"type": "string"},
                    "citation": {"type": "object", "additionalProperties": True}
                }
            },
            "WorkspaceListResponse": {
                "type": "object",
                "required": ["tenant_id", "items"],
                "properties": {
                    "tenant_id": {"type": "string"},
                    "items": {"type": "array", "items": {"$ref": "#/components/schemas/Workspace"}}
                }
            },
            "KnowledgeSearchResponse": {
                "type": "object",
                "required": ["workspace_id", "query", "items"],
                "properties": {
                    "workspace_id": {"type": "string"},
                    "query": {"type": "string"},
                    "items": {"type": "array", "items": {"$ref": "#/components/schemas/KnowledgeSearchHit"}}
                }
            },
            "ChatStreamRequest": {
                "allOf": [
                    {"$ref": "#/components/schemas/ChatRequest"},
                    {
                        "type": "object",
                        "properties": {
                            "run_id": {"type": "string", "maxLength": 256},
                            "turn_id": {"type": "string", "maxLength": 256}
                        }
                    }
                ]
            },
            "EmbedExchangeRequest": {
                "type": "object",
                "additionalProperties": False,
                "required": ["origin", "publish_token"],
                "properties": {
                    "origin": {"type": "string", "format": "uri"},
                    "publish_token": {"type": "string", "writeOnly": True}
                }
            },
            "EmbedSessionRequest": {
                "type": "object",
                "additionalProperties": False,
                "required": ["origin", "exchange_token"],
                "properties": {
                    "origin": {"type": "string", "format": "uri"},
                    "exchange_token": {"type": "string", "writeOnly": True},
                    "conversation_id": {"type": "string", "maxLength": 256}
                }
            },
            "EmbedChatRequest": {
                "type": "object",
                "additionalProperties": False,
                "required": ["origin", "session_token", "message"],
                "properties": {
                    "origin": {"type": "string", "format": "uri"},
                    "session_token": {"type": "string", "writeOnly": True},
                    "message": {"type": "string", "minLength": 1, "maxLength": 8000}
                }
            },
            "Error": {
                "type": "object",
                "properties": {
                    "error": {"type": "string"},
                    "message": {"type": "string"}
                }
            }
        }
    },
    "paths": {
        "/": {
            "get": {
                "summary": "API service entry point",
                "tags": ["system"],
                "responses": {
                    "200": {
                        "description": "Service metadata and discovery links",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "service": {"type": "string"},
                                        "version": {"type": "string"},
                                        "status": {"type": "string"},
                                        "message": {"type": "string"},
                                        "links": {
                                            "type": "object",
                                            "additionalProperties": {"type": "string"}
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
        "/health": {
            "get": {
                "summary": "健康检查",
                "tags": ["系统"],
                "responses": {
                    "200": {
                        "description": "服务健康",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "status": {"type": "string"},
                                        "service": {"type": "string"},
                                        "version": {"type": "string"}
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
        "/api/v1/tenants": {
            "post": {
                "summary": "创建租户",
                "tags": ["租户管理"],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["name", "subdomain"],
                                "properties": {
                                    "name": {"type": "string"},
                                    "subdomain": {"type": "string"},
                                    "plan_tier": {"type": "string", "enum": ["free", "pro", "enterprise"]},
                                    "owner_email": {"type": "string", "format": "email"},
                                    "owner_name": {"type": "string"}
                                }
                            }
                        }
                    }
                },
                "responses": {
                    "201": {
                        "description": "租户创建成功",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Tenant"}
                            }
                        }
                    },
                    "400": {"description": "请求参数错误"},
                    "409": {"description": "子域名已存在"}
                }
            }
        },
        "/api/v1/tenants/{tenant_id}": {
            "get": {
                "summary": "获取租户信息",
                "tags": ["租户管理"],
                "security": [{"BearerAuth": [], "TenantHeader": []}],
                "parameters": [
                    {
                        "name": "tenant_id",
                        "in": "path",
                        "required": True,
                        "schema": {"type": "string", "format": "uuid"}
                    }
                ],
                "responses": {
                    "200": {
                        "description": "成功",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Tenant"}
                            }
                        }
                    },
                    "403": {"description": "访问被拒绝"},
                    "404": {"description": "租户不存在"}
                }
            }
        },
        "/v1/workspaces": {
            "post": {
                "summary": "创建工作区",
                "description": "仅 admin 操作者可创建当前租户拥有的工作区。",
                "tags": ["workspaces"],
                "security": [{"WorkspaceHeader": [], "ActorHeader": []}],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/WorkspaceCreateRequest"}
                        }
                    }
                },
                "responses": {
                    "201": {
                        "description": "工作空间创建成功",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/Workspace"}
                            }
                        }
                    },
                    "400": {"description": "请求参数或标识格式错误"},
                    "401": {"description": "可信身份缺失或无效"},
                    "403": {"description": "需要 admin 操作者"},
                    "409": {"description": "工作区已存在"},
                    "503": {"description": "工作区存储不可用"}
                }
            },
            "get": {
                "summary": "列出工作区",
                "tags": ["workspaces"],
                "security": [{"WorkspaceHeader": [], "ActorHeader": []}],
                "responses": {
                    "200": {
                        "description": "当前可信租户可见的工作区",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/WorkspaceListResponse"}
                            }
                        }
                    },
                    "401": {"description": "可信身份缺失或无效"},
                    "503": {"description": "工作区存储不可用"}
                }
            }
        },
        "/v1/search": {
            "post": {
                "summary": "检索当前工作区知识",
                "description": "检索严格限定在可信身份解析出的 tenant/workspace 范围内。",
                "tags": ["knowledge"],
                "security": [{"WorkspaceHeader": [], "ActorHeader": []}],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/KnowledgeSearchRequest"}
                        }
                    }
                },
                "responses": {
                    "200": {
                        "description": "检索完成",
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/KnowledgeSearchResponse"}
                            }
                        }
                    },
                    "400": {"description": "检索参数无效"},
                    "401": {"description": "可信身份缺失或无效"},
                    "404": {"description": "工作区不存在或不可见"},
                    "503": {"description": "知识检索不可用或执行失败"}
                }
            }
        },
        "/v1/chat/stream": {
            "post": {
                "summary": "以 SSE 流式执行一个回合",
                "description": "每帧带有 run_id、turn_id 和可恢复的 SSE id；结束前发送 snapshot。",
                "tags": ["chat"],
                "security": [{"WorkspaceHeader": [], "ActorHeader": []}],
                "requestBody": {
                    "required": True,
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ChatStreamRequest"}}}
                },
                "parameters": [
                    {"name": "after_sequence", "in": "query", "required": False, "schema": {"type": "integer", "minimum": 0}}
                ],
                "responses": {
                    "200": {"description": "text/event-stream 生命周期事件"},
                    "400": {"description": "流标识无效"},
                    "401": {"description": "可信身份缺失或无效"},
                    "404": {"description": "工作区或会话不存在"}
                }
            }
        },
        "/embed/{channel}/config": {
            "get": {
                "summary": "读取嵌入通道公开配置",
                "tags": ["embed"],
                "parameters": [
                    {"name": "channel", "in": "path", "required": True, "schema": {"type": "string"}},
                    {"name": "Origin", "in": "header", "required": True, "schema": {"type": "string", "format": "uri"}}
                ],
                "responses": {"200": {"description": "公开通道配置"}, "403": {"description": "来源不在 allowlist"}, "404": {"description": "通道不存在"}}
            }
        },
        "/embed/{channel}/exchange": {
            "post": {
                "summary": "交换嵌入发布令牌",
                "tags": ["embed"],
                "parameters": [{"name": "channel", "in": "path", "required": True, "schema": {"type": "string"}}],
                "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/EmbedExchangeRequest"}}}},
                "responses": {"200": {"description": "短期 exchange token"}, "401": {"description": "发布令牌无效"}, "403": {"description": "来源不允许"}, "429": {"description": "请求过于频繁"}}
            }
        },
        "/embed/{channel}/session": {
            "post": {
                "summary": "创建来源绑定的嵌入会话",
                "tags": ["embed"],
                "parameters": [{"name": "channel", "in": "path", "required": True, "schema": {"type": "string"}}],
                "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/EmbedSessionRequest"}}}},
                "responses": {"201": {"description": "短期 session token"}, "401": {"description": "exchange token 无效"}, "404": {"description": "工作区不存在"}}
            }
        },
        "/embed/{channel}/chat": {
            "post": {
                "summary": "执行嵌入会话聊天",
                "tags": ["embed"],
                "parameters": [{"name": "channel", "in": "path", "required": True, "schema": {"type": "string"}}],
                "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/EmbedChatRequest"}}}},
                "responses": {"200": {"description": "聊天响应"}, "401": {"description": "session token 无效"}, "403": {"description": "来源不允许"}, "429": {"description": "请求过于频繁"}}
            }
        }
    }
}


def get_openapi_spec() -> Dict[str, Any]:
    """获取 OpenAPI 规范"""
    return OPENAPI_SPEC


def generate_markdown_docs() -> str:
    """生成 Markdown 格式的 API 文档"""
    spec = OPENAPI_SPEC

    md = f"""# {spec['info']['title']}

{spec['info']['description']}

**版本**: {spec['info']['version']}

---

## 服务器

"""

    for server in spec['servers']:
        md += f"- **{server['description']}**: `{server['url']}`\n"

    md += "\n---\n\n## 认证\n\n"
    md += "### Bearer Token\n"
    md += "在请求头中添加: `Authorization: Bearer <token>`\n\n"
    md += "### 租户识别\n"
    md += "在请求头中添加: `X-Tenant-ID: <tenant-uuid>`\n\n"

    md += "---\n\n## API 端点\n\n"

    for path, methods in spec['paths'].items():
        for method, details in methods.items():
            md += f"### {details.get('summary', 'N/A')}\n\n"
            md += f"**`{method.upper()} {path}`**\n\n"

            if 'tags' in details:
                md += f"**标签**: {', '.join(details['tags'])}\n\n"

            if 'security' in details:
                md += "**需要认证**: 是\n\n"

            if 'parameters' in details:
                md += "**路径参数**:\n"
                for param in details['parameters']:
                    md += f"- `{param['name']}` ({param['schema']['type']}): "
                    md += "必需\n" if param.get('required') else "可选\n"
                md += "\n"

            if 'requestBody' in details:
                md += "**请求体**: 见下方示例\n\n"

            md += "**响应**:\n"
            for code, response in details['responses'].items():
                md += f"- `{code}`: {response.get('description', 'N/A')}\n"

            md += "\n---\n\n"

    return md


if __name__ == '__main__':
    # 生成文档
    docs = generate_markdown_docs()
    print(docs)

    # 保存到文件
    with open('API_REFERENCE.md', 'w', encoding='utf-8') as f:
        f.write(docs)

    print("\n✅ API 文档已生成: API_REFERENCE.md")
