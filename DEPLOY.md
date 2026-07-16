# ArtPM Agent 部署指南（轻量化单栈：Streamlit + Caddy + Redis）

本目录提供一套**轻量且功能完整**的容器化部署：单个 Docker 容器跑 Streamlit，
Caddy 作反向代理自动签发 / 续期 TLS 证书并默认开启基础认证，
Redis 默认拉起作为缓存后端接入口——**一条 `docker compose up` 命令把应用、反代、
证书、缓存全起来**。SQLite 全家桶 + 缓存全部落在 `DATA_ROOT`（默认 `/app/data`）
并由宿主卷持久化，不引入 Postgres / K8s。

## 交付文件

| 文件 | 作用 |
|---|---|
| `Dockerfile` | python:3.11-slim，依赖单一来源（pyproject.toml），headless 模式，轻量 `/healthz` 健康检查 |
| `docker-compose.yml` | 单栈编排：`artpm-agent` + `caddy` + `redis` 共享 `artpm-net` 网络；挂卷持久化数据/证书/缓存；带资源上限 |
| `Caddyfile` | 反向代理 + 自动 HTTPS + 全站 basicauth；站点域名由 `SITE_ADDRESS` 注入，认证哈希由 `BASIC_AUTH_HASH` 覆盖 |
| `.dockerignore` | 排除 data/logs/构建产物/测试，保障镜像精简且不泄露密钥 |

## 快速开始

```bash
# 1) 准备环境变量（API Key 等），参考 .env.example
cp .env.example .env
#   编辑 .env，至少填一个 LLM 提供商 Key

# 2)（可选）指定对外域名；不填则默认 localhost（本机 HTTPS 测试用）
#    公网部署请改成已解析到本机公网 IP 的域名
export SITE_ADDRESS=pmagent.example.com

# 3) 一条命令：构建 app + 起 Caddy + Redis + 自动申请/续期证书
docker compose up -d --build

# 4) 访问（basicauth 默认开启，见下方「身份验证」）
#    https://<你的域名>        （SITE_ADDRESS 设为真实域名时）
#    https://localhost          （默认，Caddy 本地 CA 证书，浏览器需信任）
docker compose logs -f            # 看全部服务日志
```

数据落在宿主 `./data` 与 `./logs`，**重启 / 重建镜像都不丢**知识库、对话历史与缓存；
Caddy 证书与配置落在具名卷 `caddy-data` / `caddy-config`，续期状态同样持久化；
Redis 数据落在 `redis-data` 卷（AOF 持久化）。

## 身份验证（basicauth，默认开启）

Caddy 默认对全站启用基础认证，避免服务裸奔到公网：

- **默认账号 / 密码**：`admin` / `ArtPM@2026#redis-default`
  （哈希内嵌在 `Caddyfile` 的 `{$BASIC_AUTH_HASH:...}` 中）
- ⚠️ **部署到公网前务必改密码**：
  ```bash
  # 在宿主机执行，按提示输入你的密码，得到 $2a$... 哈希
  docker run --rm caddy:2-alpine caddy hash-password
  # 在 docker-compose.yml 的 caddy.environment 取消 BASIC_AUTH_HASH 注释并填入该哈希：
  #   - BASIC_AUTH_HASH=$2a$...
  # 然后 docker compose up -d caddy 重新加载配置
  ```
- 本地测试用默认凭据即可；也可同样方式换成你自己的。

## Redis（默认开启，已接入为缓存层）

- 默认拉起 `redis:7-alpine`，仅挂载在 `artpm-net` 内部（不暴露宿主端口），
  agent 通过 `REDIS_URL=redis://redis:6379/0` 访问，数据落在 `redis-data` 卷（AOF 持久化）。
- **已真正接入**：`artpm_agent/core/redis_cache.py` 提供统一 Redis 封装，`TokenMonitor`
  与 `EpisodeStore` 现已把热路径查询（今日/各维度用量统计、预算聚合、近期 episode、
  失败率等）套上「读 Redis 缓存 → 未命中查 SQLite 并回填」的短 TTL 缓存，写入
  （`track()` / `record()` / `set_feedback()`）后通过前缀失效保持一致性。
- **设计原则**：Redis 是**加速层而非依赖**——SQLite 始终是 source of truth。
  当 `REDIS_URL` 未设、Redis 不可达或 `redis` 包缺失时，所有缓存 helper 静默降级为 no-op，
  应用完全回退到纯 SQLite，绝不因此崩溃（见 `tests/test_redis_cache.py` 锁死的契约）。
- 如暂不需要 Redis：删掉 compose 里的 `redis` 服务段（及 `volumes` 中的 `redis-data`），
  并把 `.env` 里的 `REDIS_URL` 留空即可，不影响任何功能。

## 资源与性能

- 容器默认上限 `2 CPU / 2G 内存`（见 compose `deploy.resources.limits`），按需调整。
- 未启用向量检索时（`EMBEDDING_PROVIDER=local_feature_hash` 走字面回退）可省 FAISS 重依赖，进一步瘦身。
- 语义检索需要 FAISS：确认真式检索时，compose 镜像已含相关依赖，数据根下的 `vector_store/` 持久化索引。

## 注意事项

- **不要在容器内用设置页「存储」标签页改数据根**：该操作会把 `data_root.json` 写进容器文件系统
  （未挂卷），重建容器即丢失。Docker 部署请统一通过 `DATA_ROOT` 环境变量（compose 已设 `/app/data`）
  或宿主 `./data` 挂卷来管理数据位置。
- `.env` 含密钥，已通过 `:ro` 只读挂载且被 `.dockerignore` 排除，不会烤进镜像。
- 健康检查：agent 探 `/healthz`（不初始化完整 Agent，开销极低）；redis 探 `redis-cli ping`。
- `artpm-agent` 默认**不**对外暴露 8501 端口（仅 Caddy 对内可达），对外唯一入口即 Caddy(443)。
  如确需局域网明文直连调试，可在 compose 中取消 `artpm-agent.ports` 注释。
