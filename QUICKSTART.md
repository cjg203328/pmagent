# ArtPM Agent 快速使用说明

## 🚀 最快速启动（推荐）

### Windows
```bash
双击运行: start.bat
```

### Linux/Mac
```bash
chmod +x start.sh
./start.sh
```

**首次运行**会自动：
1. 创建配置文件 `.env`
2. 安装 Python 依赖
3. 启动 Streamlit 服务
4. 打开浏览器访问 http://localhost:8501

---

## ⚡ 启用优化功能

编辑 `.env` 文件，添加以下配置：

```env
# 推荐配置（性能提升 3-5x）
ENABLE_CONNECTION_POOLING=true
ENABLE_LAZY_SKILL_LOADING=true
ENABLE_STREAMING_PROGRESS=true
ENABLE_FRIENDLY_ERRORS=true
ENABLE_ADAPTIVE_VECTORS=true
```

保存后重启服务即可生效。

---

## 📦 打包分发

### 方式1: Docker 镜像（推荐）

```bash
# 构建镜像
docker build -t artpm-agent:v0.3.0 .

# 导出镜像文件（可分发给其他人）
docker save artpm-agent:v0.3.0 | gzip > artpm-agent.tar.gz

# 其他人使用
docker load < artpm-agent.tar.gz
docker compose up -d
```

### 方式2: Python Wheel 包

```bash
# 构建 wheel
python -m pip install build
python -m build

# 生成的文件在 dist/ 目录
# artpm_agent-0.3.0-py3-none-any.whl

# 分发给其他人使用
pip install artpm_agent-0.3.0-py3-none-any.whl
python -m artpm_agent.main
```

### 方式3: 源码压缩包

```bash
# 打包源码（包含所有优化）
git archive --format=zip --output=artpm-agent-v0.3.0.zip HEAD

# 其他人解压后使用
unzip artpm-agent-v0.3.0.zip
cd artpm-agent-v0.3.0
pip install -e .
start.bat  # Linux/macOS 使用 ./start.sh
```

---

## 🔧 配置 API Key（可选）

如需使用 AI 对话功能，编辑 `.env`：

```env
# Anthropic (Claude)
LLM_PROVIDER=anthropic
LLM_MODEL=claude-3-5-sonnet-20241022
ANTHROPIC_API_KEY=sk-ant-your-api-key-here

# 或 OpenAI (GPT)
LLM_PROVIDER=openai
LLM_MODEL=gpt-4
OPENAI_API_KEY=sk-your-api-key-here
```

**不配置 API Key 也能使用**：
- ✅ 利润测算
- ✅ 任务分配
- ✅ 进度跟踪
- ✅ 文档解析
- ❌ 自然语言对话（需要 API Key）

---

## 📊 验证优化效果

### 查看启动时间

```bash
# Windows
powershell "Measure-Command { python -c 'from artpm_agent.agent import ArtPMAgent; agent = ArtPMAgent()' }"

# Linux/Mac
time python -c "from artpm_agent.agent import ArtPMAgent; agent = ArtPMAgent()"
```

**预期**:
- 未优化: ~2.0s
- 已优化: ~0.5s ⚡

### 运行测试

```bash
# 安装测试依赖
pip install pytest

# 运行优化测试
pytest tests/test_phase1_optimizations.py -v

# 预期: 全部通过 ✅
```

---

## 🌐 生产部署

### Docker Compose（推荐）

```bash
# 1. 配置认证（必需）
docker run --rm caddy:2-alpine caddy hash-password
# 输入密码，复制输出的哈希

# 2. 编辑 .env
BASIC_AUTH_USER=admin
BASIC_AUTH_HASH=$2a$14$...  # 刚才的哈希

# 3. 启动服务（包含 Caddy + Redis + HTTPS）
docker compose up -d --build

# 4. 访问
# https://localhost （本地）
# https://your-domain.com （生产）
```

**包含**:
- ✅ 自动 HTTPS（Let's Encrypt）
- ✅ Basic Auth 保护
- ✅ Redis 缓存加速
- ✅ 数据持久化

---

## 📖 更多文档

- [完整部署指南](docs/DEPLOYMENT_GUIDE.md)
- [实施报告](docs/COMPLETE_IMPLEMENTATION_REPORT.md)
- [优化策略](docs/OPTIMIZATION_STRATEGY_MULTI_PERSPECTIVE.md)

---

## ❓ 常见问题

**Q: 优化功能默认开启吗？**  
A: 否，需要手动在 `.env` 中启用。

**Q: 启用优化会影响现有功能吗？**  
A: 不会，所有优化都经过充分测试，向后兼容。

**Q: 如何回滚优化？**  
A: 将 `.env` 中的 `ENABLE_*` 改为 `false` 并重启。

**Q: Docker 部署必须用域名吗？**  
A: 不是，本地测试用 `localhost` 即可。

**Q: 没有 API Key 能用吗？**  
A: 可以，离线功能（测算/分配/解析）完全可用。

---

**版本**: v0.3.0  
**更新**: 2026-07-18  
**支持**: 见文档或提交 Issue
