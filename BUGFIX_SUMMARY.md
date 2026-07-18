# 模型 Fallback 问题修复总结

## 修复内容

修复了一个导致模型错误切换到没有有效 API key 的 provider 的配置问题。

## 问题表现

用户在设置页配置了 `openai/gpt-4o-mini`，但对话时系统显示正在使用 `deepseek-v4-flash`，并报错"服务繁忙"或"未收到有效回答"。

## 根本原因

1. `.env` 中 `LLM_AVAILABLE_MODELS` 包含跨 provider 的模型列表
2. 当主模型请求失败（网络波动/超时）时，`ModelGateway` 会自动 failover 到候选模型
3. 由于候选模型来自不同 provider，且对应的 API key 不存在或是占位符，导致切换后立即失败

## 修复措施

### 1. 清理 .env 配置

**修改前**：
```env
LLM_AVAILABLE_MODELS="[\"deepseek-v4-flash\",\"deepseek-v4-pro\",\"gemma-4\",\"glm-5.2\",\"kimi-k2.6\",\"qwen3.5\"]"
DEEPSEEK_API_KEY=sk-your-deepseek-key-here
```

**修改后**：
```env
# 只保留与主模型相同 provider 的候选
LLM_AVAILABLE_MODELS="[\"gpt-4o\",\"gpt-4o-mini\",\"gpt-3.5-turbo\"]"

# 移除无效的占位符 API keys
# DEEPSEEK_API_KEY=sk-your-deepseek-key-here
```

### 2. 新增配置检查工具

创建了 `artpm_agent/tools/check_config.py`，可以自动检测：
- 占位符 API keys
- 跨 provider 的候选模型配置
- 缺失的必需 API keys
- 数据路径访问权限

**使用方式**：
```bash
python -m artpm_agent.tools.check_config
```

### 3. 新增启动时配置检查

创建了安全启动脚本：
- `start_with_checks.bat`（Windows）
- `start_with_checks.py`（跨平台）

在启动应用前自动运行配置检查，拦截配置错误。

### 4. 更新文档

- 新增 [`docs/FIXED_MODEL_FALLBACK_ISSUE.md`](./docs/FIXED_MODEL_FALLBACK_ISSUE.md)：详细的问题分析和修复记录
- 更新 [`docs/TROUBLESHOOTING.md`](./docs/TROUBLESHOOTING.md)：添加快速排查步骤
- 更新 [`README.md`](./README.md)：推荐使用带配置检查的启动方式

## 预防机制

1. **配置检查工具**：启动前或遇到问题时运行，及早发现配置问题
2. **文档说明**：在 TROUBLESHOOTING.md 中记录常见配置错误
3. **启动脚本**：推荐使用 `start_with_checks` 系列脚本，自动拦截配置错误

## 验证步骤

1. 运行配置检查：
   ```bash
   python -m artpm_agent.tools.check_config
   ```
   应显示"✅ 配置检查通过"

2. 使用新的启动脚本：
   ```bash
   start_with_checks.bat  # Windows
   # 或
   python start_with_checks.py  # 跨平台
   ```

3. 在对话页发起对话：
   - 观察对话页顶部的模型指示器（应始终显示 gpt-4o-mini）
   - 如果主模型暂时不可用，应切换到同 provider 的候选（gpt-4o 或 gpt-3.5-turbo）

4. 查看应用日志：
   - 不应出现 "切换到候选模型: deepseek-v4-flash" 这样的跨 provider 切换

## 相关文件

**修改的文件**：
- `.env`：清理跨 provider 的候选模型和占位符 API keys

**新增的文件**：
- `artpm_agent/tools/check_config.py`：配置检查工具
- `start_with_checks.py`：跨平台启动脚本（带配置检查）
- `start_with_checks.bat`：Windows 启动脚本（带配置检查）
- `docs/FIXED_MODEL_FALLBACK_ISSUE.md`：详细修复记录

**更新的文件**：
- `docs/TROUBLESHOOTING.md`：添加快速排查步骤
- `README.md`：推荐新的启动方式

## 后续改进建议

### 短期（代码层面）

1. **ModelGateway 增强**：在 `fallback_model_ids()` 中增加 provider 边界检查，拒绝返回没有有效 API key 的跨 provider 候选

2. **设置页校验**：在保存配置时，自动过滤掉不属于当前 provider 的候选模型

3. **启动时警告**：在应用启动时运行配置检查，并在日志中打印警告（非阻塞）

### 长期（架构层面）

1. **Provider 注册表**：建立 provider → API key → 候选模型的映射关系，使 failover 逻辑更清晰

2. **健康检查机制**：定期 ping 候选模型的端点，提前发现不可用的候选

3. **用户友好的错误提示**：当 failover 失败时，提示用户"所有候选模型均不可用，请检查 API key 或网络连接"

---

**修复日期**: 2026-07-18  
**修复人**: Kiro (Claude Code Agent)  
**影响版本**: 所有包含 ModelGateway failover 逻辑的版本
