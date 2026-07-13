# 离线降级行为说明（Offline Fallback）

本文说明 ArtPM Agent 在**无 LLM API 密钥**时的行为，便于部署与排障。
无密钥并不等于不可用——技能路由、数据库、向量检索、文件解析、MCP 本地工具
均可正常工作。

## 1. 启动阶段

- `agent.py` 初始化时若无有效 API 密钥，记录日志：
  `进入离线模式 - Skill路由正常工作,对话需要API密钥`。
- 离线不影响数据库、向量检索、文件解析、MCP 本地工具。

## 2. 技能路由（离线可用）

意图路由三层（关键词 → embedding → LLM）中：

- **关键词层** 与 **embedding 层** 完全离线（embedding 使用
  `DeterministicEmbeddingProvider` 的离线 hash 向量，无 API 依赖）。
- **LLM 分类层** 仅在 `llm.intent_classification_enabled=true` 且有密钥时启用；
  否则跳过，回退到关键词 + embedding。

因此输入如「报价X万成本Y万帮我算利润」「分配XX任务」「检查项目进度」
「催一下进度」「解析报价单」等会正确路由到对应技能并正常执行。

## 3. 对话（需 API 密钥）

当 `llm_client is None` 时，`chat()` 不会调用模型，而是：

- 若附带可解析的文档附件，返回解析结果（`document_classifier_parser`）。
- 否则返回「LLM 未配置」提示，列出当前可用离线功能，并指引配置 `.env` 的
  `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`。

## 4. 模型故障转移（有密钥时）

- 主模型请求失败时，按 `_fallback_model_ids` / `_vision_fallback_model_ids`
  尝试备用模型。
- 若所有模型失败但文档 OCR 已产出本地结果，返回该 OCR 文本作为**透明降级**，
  避免丢失用户答案。

## 5. 外部依赖

| 依赖 | 离线可用？ | 说明 |
|---|---|---|
| 数据库 | ✅ | SQLite，完全离线 |
| 向量检索 | ✅ | FAISS 本地索引，离线 |
| MCP 本地工具 | ✅ | `EnhancedMCPClient`，离线 |
| MCP 远程 Skills Forge | ❌ | HTTP 服务，需网络 |
| LLM 对话/分类 | ❌ | 需 API 密钥 |

## 6. 健康检查

`health_check.py` 将「无 LLM 密钥」判定为 **`degraded`**（系统可用，离线模式）
而非 `unhealthy`。
