# 常见问题快速解决指南

## 🚨 问题 1：模型切换到错误的 Provider

### 症状
- **设置页显示**：`openai/gpt-4o-mini`
- **对话页显示**：`deepseek-v4-flash` 或其他不同 provider 的模型
- **报错信息**：「服务繁忙」或「未收到有效回答」

### 原因
`LLM_AVAILABLE_MODELS` 包含了跨 provider 的模型，导致 failover 时切换到没有有效 API key 的 provider。

### 快速诊断
```bash
# 运行配置检查工具
python -m artpm_agent.tools.check_config

# 如果报告「跨 provider 的模型」警告，继续下面的修复步骤
```

### 修复步骤

**步骤 1：清理 `.env` 中的候选模型列表**

```env
# 只保留与主模型相同 provider 的候选
LLM_AVAILABLE_MODELS="[\"gpt-4o\",\"gpt-4o-mini\",\"gpt-3.5-turbo\"]"
```

**步骤 2：移除无效的占位符 API keys**
```env
# 注释掉或删除这些行
# ANTHROPIC_API_KEY=sk-ant-your-anthropic-key-here
# DEEPSEEK_API_KEY=sk-your-deepseek-key-here
```

**步骤 3：重启应用**
```bash
streamlit run app.py
```

**详细说明**：参见 [FIXED_MODEL_FALLBACK_ISSUE.md](../reports/FIXED_MODEL_FALLBACK_ISSUE.md)

---

## 问题 2：模型服务繁忙 / 无法访问

### 症状
```
模型 deepseek-v4-flash 当前服务繁忙，
非常抱歉，请稍后回访。
大批访问已被批处理，正在重新生成，大幅降低已过扩的工作负载处理时间
```

### 原因
- DeepSeek 服务繁忙或不稳定
- API 端点响应超时
- 配额限制

### 解决方案（已实施）

#### ✅ 方案 1: 切换到 OpenAI（已配置）

当前配置（.env）：
```env
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o-mini
OPENAI_API_BASE=https://api.zhongteai.cn/v1
```

**优点**：
- 更稳定
- 响应更快
- 功能完整

#### 方案 2: 切换其他模型

编辑 `.env`，选择以下配置之一：

**选项 A - GPT-4o（高质量）**：
```env
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
```

**选项 B - GPT-3.5（快速）**：
```env
LLM_PROVIDER=openai
LLM_MODEL=gpt-3.5-turbo
```

**选项 C - Claude（高质量）**：
```env
LLM_PROVIDER=anthropic
LLM_MODEL=claude-3-5-sonnet-20241022
ANTHROPIC_API_KEY=sk-ant-your-real-key
```

**选项 D - 回到 DeepSeek（当服务恢复）**：
```env
LLM_PROVIDER=custom
LLM_MODEL=deepseek-v4-flash
```

### 应用配置更改

```bash
# 方法 1: 重启服务（推荐）
kill $(pgrep -f streamlit)
./start.sh

# 方法 2: 手动重启
# 找到进程 ID
ps aux | grep streamlit

# 停止服务
kill <PID>

# 启动服务
streamlit run artpm_agent/app.py
```

---

## 问题：API Key 无效

### 症状
```
Authentication failed
Invalid API key
401 Unauthorized
```

### 解决方案

1. **检查 API Key**：
```bash
grep API_KEY .env
```

2. **更新 API Key**：
编辑 `.env`：
```env
# OpenAI
OPENAI_API_KEY=sk-your-real-key-here

# Anthropic
ANTHROPIC_API_KEY=sk-ant-your-real-key-here
```

3. **测试 API Key**：
```bash
# 测试 OpenAI
curl -H "Authorization: Bearer $OPENAI_API_KEY" \
  https://api.zhongteai.cn/v1/models

# 测试 Anthropic
curl -H "x-api-key: $ANTHROPIC_API_KEY" \
  https://api.anthropic.com/v1/messages
```

---

## 问题：服务无法启动

### 症状
```
Address already in use
Port 8501 is already allocated
```

### 解决方案

```bash
# 1. 查找占用端口的进程
netstat -ano | findstr :8501

# 2. 停止进程
taskkill /PID <PID> /F

# 3. 或使用不同端口
streamlit run artpm_agent/app.py --server.port 8502
```

---

## 问题：导入错误

### 症状
```
ModuleNotFoundError: No module named 'streamlit'
ImportError: cannot import name 'xxx'
```

### 解决方案

```bash
# 重新安装依赖
pip install -e . --force-reinstall

# 或清理缓存重装
pip cache purge
pip install -e .
```

---

## 问题：数据库锁定

### 症状
```
sqlite3.OperationalError: database is locked
```

### 解决方案

**方法 1: 禁用连接池（临时）**：
```env
ENABLE_CONNECTION_POOLING=false
```

**方法 2: 迁移到本地磁盘**：
```env
DB_PATH=./local_data/artpm.db
```

**方法 3: 检查其他进程**：
```bash
# 查找占用数据库的进程
lsof data/artpm.db

# 停止占用进程
kill <PID>
```

---

## 问题：向量检索变慢

### 症状
- 检索耗时 > 1s
- 内存占用高

### 解决方案

**方法 1: 强制使用 Flat 索引**：
```env
VECTOR_INDEX_TYPE=flat
```

**方法 2: 重建向量索引**：
```bash
# 删除旧索引
rm -rf data/vector_store/

# 重启服务，自动重建
./start.sh
```

---

## 快速诊断脚本

```bash
# 运行诊断
python -c "
print('=== ArtPM Agent 诊断 ===')
print()

# 1. 检查服务状态
import subprocess
result = subprocess.run(['curl', '-s', 'http://localhost:8501'], 
                       capture_output=True, timeout=5)
print(f'服务状态: {'运行中' if result.returncode == 0 else '未启动'}')

# 2. 检查配置
import os
from dotenv import load_dotenv
load_dotenv()
print(f'LLM Provider: {os.getenv(\"LLM_PROVIDER\")}')
print(f'LLM Model: {os.getenv(\"LLM_MODEL\")}')
print(f'API Base: {os.getenv(\"OPENAI_API_BASE\")}')

# 3. 检查优化功能
from artpm_agent.bugfixes import get_optimization_registry
registry = get_optimization_registry()
enabled = sum(1 for v in registry.get_status().values() if v)
print(f'优化功能: {enabled}/9 启用')

# 4. 检查数据库
import sqlite3
try:
    conn = sqlite3.connect('data/artpm.db')
    print('数据库: 正常')
    conn.close()
except Exception as e:
    print(f'数据库: 错误 - {e}')

print()
print('=== 诊断完成 ===')
"
```

---

## 获取帮助

### 查看日志
```bash
# 实时日志
tail -f streamlit.log

# 最近 50 行
tail -50 streamlit.log

# 搜索错误
grep -i error streamlit.log
```

### 查看系统状态
```bash
# 进程状态
ps aux | grep streamlit

# 端口占用
netstat -ano | findstr :8501

# 磁盘空间
df -h

# 内存使用
free -h
```

### 重置到初始状态

```bash
# 1. 停止服务
kill $(pgrep -f streamlit)

# 2. 备份数据
cp -r data data.backup

# 3. 清理缓存
rm -rf .cache .pytest_cache __pycache__

# 4. 重置配置
cp .env.example .env

# 5. 重新启动
./start.sh
```

---

## 联系支持

如果问题仍未解决：

1. 收集诊断信息：
```bash
# 生成诊断报告
python -c "
import sys, os, platform
from dotenv import load_dotenv
load_dotenv()

print('System Info:')
print(f'  OS: {platform.system()} {platform.release()}')
print(f'  Python: {sys.version}')
print(f'  Working Dir: {os.getcwd()}')
print()
print('Config:')
print(f'  LLM Provider: {os.getenv(\"LLM_PROVIDER\")}')
print(f'  LLM Model: {os.getenv(\"LLM_MODEL\")}')
print()
" > diagnostic.txt

# 附加日志
tail -100 streamlit.log >> diagnostic.txt
```

2. 提交 Issue 时附上 `diagnostic.txt`

---

**最后更新**: 2026-07-18  
**版本**: v0.3.0
