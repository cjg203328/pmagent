# 模型 Fallback 错误切换问题修复记录

## 问题表现

**症状**：
- 设置页配置了 `openai/gpt-4o-mini`，但对话时显示正在使用 `deepseek-v4-flash`
- 聊天时报错："服务繁忙" 或 "未收到有效回答"
- 即使 OpenAI API key 有效，也无法正常对话

**截图问题**：
```
设置页显示: provider=openai, model=gpt-4o-mini
对话页显示: 正在使用 deepseek-v4-flash
错误信息: 模型服务未返回有效回答
```

## 根本原因

`ModelGateway` 的 failover 机制存在配置错误：

1. `.env` 中 `LLM_AVAILABLE_MODELS` 列表包含了跨 provider 的模型：
   ```env
   LLM_AVAILABLE_MODELS="[\"deepseek-v4-flash\",\"deepseek-v4-pro\",\"gemma-4\",\"glm-5.2\",\"kimi-k2.6\",\"qwen3.5\"]"
   ```

2. 当主模型请求失败（网络波动、超时等）时，`ModelGateway.fallback_model_ids()` 会从 available_models 中选择候选：

   ```python
   # artpm_agent/providers/gateway.py:196-223
   def fallback_model_ids(self, primary_model: str) -> List[str]:
       provider = str(self._llm_config.get("provider", "") or "").strip().lower()
       if provider not in self.MODEL_FAILOVER_PROVIDERS:
           return []
       # 从 available_models 中选择候选（优先相同 family）
       unique_models.sort(
           key=lambda model_id: (
               self._model_family(model_id) != primary_family,
               model_id.casefold(),
           )
       )
       return [model_id for model_id in unique_models if self.is_model_available(model_id)]
   ```

3. 因为 DeepSeek 相关的 API key 只是占位符（`sk-your-deepseek-key-here`），所以切换到 `deepseek-v4-flash` 后立即失败。

## 问题链路

```
用户发起对话
    ↓
OpenAI API 调用（gpt-4o-mini）
    ↓
失败（网络波动 / 超时 / 其他临时错误）
    ↓
ModelGateway.chat_with_failover() 触发 failover
    ↓
从 LLM_AVAILABLE_MODELS 选择候选（deepseek-v4-flash）
    ↓
尝试调用 DeepSeek API（DEEPSEEK_API_KEY 无效）
    ↓
返回错误："服务繁忙" / "未收到有效回答"
```

## 修复方案

### 方案 1：清理跨 provider 的 available_models（已应用）

**修改 `.env` 文件**：
```env
# 只保留 OpenAI 系列模型作为 fallback 候选
LLM_AVAILABLE_MODELS="[\"gpt-4o\",\"gpt-4o-mini\",\"gpt-3.5-turbo\"]"

# 移除无效的占位符 API keys
# ANTHROPIC_API_KEY=sk-ant-your-anthropic-key-here
# DEEPSEEK_API_KEY=sk-your-deepseek-key-here
```

**效果**：
- Failover 只在同 provider 内切换（gpt-4o-mini → gpt-4o → gpt-3.5-turbo）
- 避免切换到没有有效 API key 的 provider

### 方案 2：增强 ModelGateway 的 provider 边界检查

**长期优化**（代码层面）：

```python
# artpm_agent/providers/gateway.py

def fallback_model_ids(self, primary_model: str) -> List[str]:
    provider = str(self._llm_config.get("provider", "") or "").strip().lower()
    if provider not in self.MODEL_FAILOVER_PROVIDERS:
        return []
    
    # 🔧 新增：只返回当前 provider 下有 API key 的模型
    def has_valid_api_key(model_id: str) -> bool:
        model_provider = self._infer_provider(model_id)
        if model_provider != provider:
            return False  # 跨 provider，拒绝
        
        # 检查对应的 API key 是否存在且不是占位符
        key_name = f"{provider.upper()}_API_KEY"
        key_value = os.getenv(key_name, "")
        return key_value and not key_value.startswith("sk-your-")
    
    unique_models = [...]  # 原有逻辑
    return [
        model_id for model_id in unique_models
        if self.is_model_available(model_id) and has_valid_api_key(model_id)
    ]
```

### 方案 3：设置页自动过滤候选模型

**在设置页保存时**：
```python
# artpm_agent/views/settings.py

def _save_settings(config):
    # 过滤掉不属于当前 provider 的模型
    provider = config["provider"]
    available_models = config.get("available_models", [])
    
    valid_models = [
        m for m in available_models
        if infer_provider(m) == provider
    ]
    
    config["available_models"] = valid_models
    # ... 其余保存逻辑
```

## 验证步骤

1. **重启应用**：
   ```bash
   streamlit run app.py
   ```

2. **检查设置页**：
   - Provider: openai
   - Model: gpt-4o-mini
   - Available Models: 只应显示 OpenAI 系列模型

3. **发起对话**：
   - 观察对话页顶部模型指示器（应始终显示 gpt-4o-mini）
   - 如果主模型暂时不可用，应切换到 gpt-4o 或 gpt-3.5-turbo（而非跨 provider）

4. **查看日志**：
   ```bash
   # 应该看到
   INFO: 使用模型: gpt-4o-mini
   
   # 如果 failover，应该看到
   WARNING: 模型 gpt-4o-mini 暂时不可用，尝试候选模型
   INFO: 切换到候选模型: gpt-4o
   
   # 不应该看到
   INFO: 切换到候选模型: deepseek-v4-flash  # ❌ 这是错误的
   ```

## 预防措施

1. **设置页校验**：
   - 保存配置时，自动过滤掉不属于当前 provider 的候选模型
   - 提示用户：「候选模型列表仅保留与主模型相同 provider 的选项」

2. **启动时检查**：
   - 检测到跨 provider 的 available_models 时，打印警告日志
   - 建议用户运行 `python -m artpm_agent.tools.check_config` 做配置体检

3. **文档说明**：
   - 在 `docs/guides/QUICKSTART.md` 中明确说明：
     > **候选模型列表应只包含主 provider 的模型，避免跨 provider failover 时的认证失败。**

## 相关代码位置

- **Failover 逻辑**：[artpm_agent/providers/gateway.py:196-223](../artpm_agent/providers/gateway.py#L196-L223)
- **配置加载**：[artpm_agent/config.py:98-106](../artpm_agent/config.py#L98-L106)
- **设置页保存**：[artpm_agent/views/settings.py:329-347](../artpm_agent/views/settings.py#L329-L347)

## 附录：快速诊断命令

```bash
# 检查 .env 中的 provider 和 available_models
grep -E "^LLM_PROVIDER|^LLM_MODEL|^LLM_AVAILABLE_MODELS" .env

# 检查是否有无效的占位符 API keys
grep -E "sk-your-.*-key-here" .env

# 查看最近的模型调用日志
grep -E "使用模型|切换.*模型|模型.*不可用" logs/artpm_agent.log | tail -20
```

---

**修复日期**: 2026-07-18  
**修复人**: Kiro (Claude Code Agent)  
**影响版本**: 所有包含 ModelGateway failover 逻辑的版本
