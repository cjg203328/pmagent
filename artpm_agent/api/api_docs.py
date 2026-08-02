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
            }
        },
        "schemas": {
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
                "properties": {
                    "id": {"type": "string", "format": "uuid"},
                    "name": {"type": "string"},
                    "slug": {"type": "string"},
                    "description": {"type": "string"},
                    "created_at": {"type": "string", "format": "date-time"}
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
        "/api/v1/workspaces": {
            "post": {
                "summary": "创建工作空间",
                "tags": ["工作空间管理"],
                "security": [{"BearerAuth": [], "TenantHeader": []}],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["name", "slug"],
                                "properties": {
                                    "name": {"type": "string"},
                                    "slug": {"type": "string"},
                                    "description": {"type": "string"}
                                }
                            }
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
                    "400": {"description": "请求参数错误"},
                    "429": {"description": "超出配额限制"}
                }
            },
            "get": {
                "summary": "列出工作空间",
                "tags": ["工作空间管理"],
                "security": [{"BearerAuth": [], "TenantHeader": []}],
                "responses": {
                    "200": {
                        "description": "成功",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "array",
                                    "items": {"$ref": "#/components/schemas/Workspace"}
                                }
                            }
                        }
                    }
                }
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
