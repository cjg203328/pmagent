# ArtPM Agent Project Memory

## Glossary

暂无项目术语漂移记录。

## Lessons Learned

### Known Issue: api.anthropic.li 模型目录可用但推理端无响应

**发现日期**: 2026-09-11
**问题类型**: 第三方依赖风险
**严重度**: 严重

#### 现象

TLS 校验正常，鉴权 `/v1/models` 返回 200 且包含 `qwen3.8-27b-uncensored`，但 `/chat/completions` 在 45 秒和 120 秒超时窗口内均未返回响应头。

#### 根因

已排除本机 TLS 信任链、模型标识缺失和基础鉴权问题。故障边界位于外部网关的推理路径；其内部排队、模型实例或账号侧状态无法从本地进一步确认。

#### 规避方案

先用模型目录确认基础连通性，再用单次、有限时的最小流式请求验证推理。达到 120 秒上限后停止重试，不关闭 TLS 校验，也不继续扩大客户端超时；等待上游恢复或切换到已确认可推理的兼容网关。

#### 相关文件

- `.env`
- `artpm_agent/utils/langchain_client.py`
- `artpm_agent/utils/llm_client.py`

#### 状态

未修复，外部阻塞。
