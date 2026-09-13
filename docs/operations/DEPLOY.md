# ArtPM Agent 部署指南（Compose 生产单栈）

本目录提供一套生产 Compose 部署：Streamlit UI 与 FastAPI 网关由 Caddy 统一反向代理，
并配套 PostgreSQL/RLS、Qdrant、Redis 和可观测组件。Caddy 自动签发/续期 TLS 证书并强制
基础认证；应用和数据库数据通过宿主卷及具名卷持久化。

## 交付文件

| 文件 | 作用 |
|---|---|
| `Dockerfile` | 多阶段构建：builder 编译 wheel → runtime 以非 root 用户 `appuser`(uid/gid 10001) 运行，运行镜像不含 build-essential；依赖单一来源（pyproject.toml），headless 模式，严格探测 Streamlit 原生 `/_stcore/health` |
| `docker-compose.yml` | 单栈编排：`artpm-agent` + `artpm-api` + `caddy` + `postgres` + `qdrant` + `redis` 及可观测组件共享 `artpm-net`；挂卷持久化数据/证书/缓存；认证和数据库凭据缺失即 fail-fast |
| `Caddyfile` | 反向代理 + 自动 HTTPS + 全站 basicauth；站点域名由 `SITE_ADDRESS` 注入，认证凭据由 `BASIC_AUTH_USER` / `BASIC_AUTH_HASH` 提供（仓库不内置任何默认账号/密码） |
| `.dockerignore` | 排除 data/logs/构建产物/测试，保障镜像精简且不泄露密钥 |

## 快速开始

```bash
# 1) 准备环境变量（API Key 等），参考 .env.example
cp .env.example .env
#   编辑 .env，至少填一个 LLM 提供商 Key

# 2)（可选）指定对外域名；不填则默认 localhost（本机 HTTPS 测试用）
#    公网部署请改成已解析到本机公网 IP 的域名
export SITE_ADDRESS=pmagent.example.com

# 3) 设置 Caddy 基础认证凭据（必填，缺失则 docker compose up 直接报错）
#    生成 bcrypt 哈希（按提示输入你的密码）：
docker run --rm caddy:2-alpine caddy hash-password
#    把用户名与哈希写进 .env：
#      BASIC_AUTH_USER=admin
#      BASIC_AUTH_HASH=$2a$...刚生成的哈希...

# 4) 一条命令：构建 app + 起 Caddy + Redis + 自动申请/续期证书
docker compose up -d --build

# 5) 访问（全站 basicauth 已开启，用上面设置的账号/密码登录）
#    https://<你的域名>        （SITE_ADDRESS 设为真实域名时）
#    https://localhost          （默认，Caddy 本地 CA 证书，浏览器需信任）
docker compose logs -f            # 看全部服务日志
```

数据落在宿主 `./data` 与 `./logs`，**重启 / 重建镜像都不丢**知识库、对话历史与缓存；
Caddy 证书与配置落在具名卷 `caddy-data` / `caddy-config`，续期状态同样持久化；
Redis 数据落在 `redis-data` 卷（AOF 持久化）。

## 身份验证（basicauth，强制开启，无默认凭据）

Caddy 对全站启用基础认证，避免服务裸奔到公网。**仓库与镜像内不内置任何默认账号/密码**：

- 凭据必须来自部署环境变量，并在 `docker-compose.yml` 中以 `${VAR:?...}` 声明为必填；
  未设置时 `docker compose up` 会在配置阶段直接报错，绝不会以弱默认启动。
- 设置步骤：
  ```bash
  # 1) 宿主机生成密码哈希（按提示输入你要用的密码）
  docker run --rm caddy:2-alpine caddy hash-password
  # 2) 把用户名与哈希写入 .env
  #      BASIC_AUTH_USER=admin
  #      BASIC_AUTH_HASH=$2a$...上一步得到的哈希...
  # 3) docker compose up -d 启动；访问时用该账号/密码登录
  ```
- 改密码只需重新生成哈希并更新 `.env` 的 `BASIC_AUTH_HASH`，再 `docker compose up -d caddy` 热重载。

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
- **非 root 运行权限**：运行镜像以固定 `appuser`(uid/gid 10001) 启动。宿主挂卷 `./data`、`../logs` 需对该 UID 可写，否则 Streamlit 启动会报权限错误。首次部署可：
  ```bash
  sudo chown -R 10001:10001 ./data ./logs
  ```
  或让目录对「其他用户」可写（`chmod -R a+rwX ./data ./logs`，仅可信环境）。
- 健康检查：agent 探 `/_stcore/health`，API 探 `/ready`（均不初始化完整 Agent）；redis 探 `redis-cli ping`。
- `artpm-agent` 默认**不**对外暴露 8501 端口（仅 Caddy 对内可达），对外唯一入口即 Caddy(443)。
  如确需局域网明文直连调试，可在 compose 中取消 `artpm-agent.ports` 注释。
