# 记忆与检索系统优化计划

计划日期：2026-10-14
范围：对话压缩、向量嵌入接口、文档勘误、FAISS 分区、离线语义匹配
交付形态：本文是**执行计划 + 状态跟踪**二合一文档，每项完成后更新状态列。

## 背景

本文的五项优化来自对记忆与检索子系统的代码复核。复核结论与原始建议存在一处出入
（③），已在各项中如实标注，避免按错误前提改代码。

溯源：本文第 1 节的功能与边界描述与
[`SYSTEM_ANALYSIS.md`](../architecture/SYSTEM_ANALYSIS.md) 第 6 章保持一致。

## 总体优先级

| # | 项目 | 原始优先级 | 复核结论 | 风险 | 状态 |
| --- | --- | --- | --- | --- | --- |
| ① | 对话压缩断网截断兜底 | 高 | 属实 | 低 | ✅ 已完成 |
| ② | 向量嵌入接口接通或删除 | 中 | 属实（且更严重） | 低 | ✅ 已完成 |
| ③ | 文档过时文件名勘误 | 低 | 描述有出入 | 无 | ✅ 已完成 |
| ④ | FAISS 按租户/工作区分区 | 架构升级 | 属实 | **中高** | ✅ 已完成（默认关闭） |
| ⑤ | 离线语义模型 | 体验升级 | 属实 | 中 | 🔶 已完成接入点，模型可选 |

优先级排序原则：**先消除"言行不一致"，再优化性能，最后升级能力**。①②属于
代码行为与自身文档矛盾，是可信度问题；③是纯文档；④⑤是能力升级。

---

## ① 对话压缩断网截断兜底（高）

### 问题

`artpm_agent/memory/conversation_compressor.py:76` 的类文档承诺：

> 降级友好：LLM 失败时退化为截断策略

但 `compress()` 的异常分支（`:228-230`）只设置 `result.error` 后返回，
`was_compressed` 保持 `False`。由于 `build_compressed_context()` 仅在
`was_compressed` 为真时才输出摘要（`:242`），结果是：**LLM 不可用时，该轮压缩
既不产生摘要，也不产生任何兜底上下文**——长对话在断网场景下等于完全没有压缩。

### 影响

- 离线部署（无 API Key）时长对话上下文持续膨胀，直到触发 Token 上限
- 注释与实际行为矛盾，维护者会误以为已有兜底

### 方案

在异常分支补充「用户原话截断」兜底，与类文档保持一致：

1. 从消息列表中**倒序**挑选最近的用户消息（用户原话比助手回复更能表达意图）
2. 保留最近若干条，拼接为摘要文本
3. 关键点取这些原话的首句，作为可检索内容
4. 标记 `was_compressed = True`，使 `build_compressed_context()` 正常注入
5. 用 `degraded=True` 新字段与「LLM 正常压缩」区分，便于观测

### 验收

- 新增测试：LLM 抛异常时 `was_compressed` 为真、摘要非空、含用户原话
- 新增测试：LLM 返回空串时同样走兜底
- 新增测试：完全无用户消息时不产生空摘要、仍返回错误
- 现有压缩测试全部保持通过

---

## ② 向量嵌入接口接通或删除（中）

### 问题

`artpm_agent/memory/memory_manager.py:566-583` 的 `_get_embedding()` 文档写明：

> To use real embeddings (OpenAI / local model), set llm_client and the client
> will be used instead.

但函数体只有 `return self.embedding_provider.embed(text)`，`llm_client`
（在 `:50` 由构造函数保存）从未被读取。更值得注意的是
`request_orchestrator.py:161-166` **确实把 `self.llm_client` 传了进来**，
于是这个参数看起来是活跃的，实际是死代码。

### 影响

- 误导性文档：维护者以为配置了 LLM 就会用真实向量，实际不会
- 无声失败：无法通过配置观察到"嵌入走的还是哈希"这一事实

### 方案

选择**接通**而非删除，因为架构上已经有正确的扩展点：

- 新增 `RemoteEmbeddingProvider`，实现既有 `EmbeddingProvider` 协议
  （`embeddings.py:11-18`），带**独立 fingerprint**，避免与本地哈希索引混用
- 在 `create_embedding_provider()` 中支持 `embedding_provider=remote`，
  但**默认仍是 `local_feature_hash`**，即离线优先不变
- 当显式选择 remote 但缺少可用客户端时，**明确降级回本地并记录原因**，
  而不是静默失败
- 修正 `_get_embedding()` 文档，让它描述真实行为

关键约束：真实向量与哈希向量的**维度和语义都不同**，混进同一个索引会导致
检索结果无意义。因此 `VectorStore` 的 manifest 已经校验 `embedding_fingerprint`
（`faiss_vector_store.py:172`），切换 provider 会触发重建而非静默复用。

### 验收

- 新增测试：默认仍为本地哈希 provider
- 新增测试：显式 remote 但无客户端 → 降级本地且 state 中可见原因
- 新增测试：切换 fingerprint 后索引触发重建而非返回脏结果
- 文档字符串与实现一致

---

## ③ 文档过时文件名勘误（低）

### 问题与出入说明

原始建议称"文档里提到的 `vector_search.py` 早就拆分成 4 个文件"。复核结果：
**全仓库没有任何文档把 `vector_search.py` 当作模块引用**。

- `docs/analysis/deep-analysis-artpm-2026.md:341` 提到的 `test_vector_search`
  是**测试文件名**，`tests/test_vector_search.py` 真实存在，该引用正确
- `docs/dev/TECHNICAL_DEBT_TASKS.md:558,575` 与
  `docs/roadmaps/improvement-roadmap-24w.md:137` 提到
  `artpm_agent/memory/vector_store_base.py`——该文件**不存在**
- `docs/roadmaps/optimization-roadmap.md:965,1331` 提到
  `vector_store_qdrant.py`——实际文件名是 `qdrant_vector_store.py`

因此本次按**实际存在的过时引用**修正，不动本来正确的引用。

### 当前真实文件清单

```text
artpm_agent/memory/
├── vector_store.py            # 后端选择 facade：Qdrant 优先，FAISS 兜底
├── faiss_vector_store.py      # 本地 FAISS 持久化索引
├── qdrant_vector_store.py     # 远程 Qdrant 后端
└── adaptive_vector_store.py   # 按规模切换 Flat/IVF/HNSW
```

### 方案

- 修正在路线图与技术债务文档中出现的 `vector_store_base.py`、
  `vector_store_qdrant.py` 引用
- 在 `docs/architecture/PROJECT_STRUCTURE.md` 的 `memory/` 段补充实际文件清单，
  让"查文件名"有单一可信来源
- 对历史归档文档（`docs/archive/`）**不做修改**——归档内容本就不作为当前契约

### 验收

- 非归档文档中不再出现指向不存在文件的路径
- 补充的文件清单与 `ls artpm_agent/memory/*.py` 一致

---

## ④ FAISS 按租户/工作区分区（架构升级）

### 问题

`artpm_agent/memory/faiss_vector_store.py:349-352`：

```python
# Metadata filters are evaluated after FAISS ranking. Search the whole
# small local index so workspace isolation cannot discard valid hits.
search_k = self.count if filters else min(top_k, self.count)
```

当 `filters` 含 `tenant_id` / `workspace_id`（这是检索的默认路径，
见 `workspace_knowledge_store.py:2678-2681`）时，`search_k` 直接等于全库条目数，
即**每次都做全索引扫描，再后置过滤**。

代码注释里"small local index"的前提在多租户部署下不再成立：N 个租户共享一个
索引，每个租户的每次查询都要扫过其他所有租户的向量。

### 影响

- 检索延迟随**全库规模**增长，而非随**本租户规模**增长
- 多租户场景下，租户数量增加会线性拖慢所有租户

### 方案

新增 `ShardedVectorStore`，按 `<tenant>/<workspace>` 落到独立子目录：

```text
data/vector_store/
├── scopes/
│   ├── local/local-default/{index.faiss,metadata.json,manifest.json}
│   └── acme/art-01/{index.faiss,metadata.json,manifest.json}
└── ...（旧的单索引文件保持可读）
```

设计要点：

1. **作用域从 filters 解析**：`search()` 读取 `filters` 中的 tid/wid，
   直接路由到对应分片，使扫描范围从 O(全库) 降到 O(本租户)
2. **不变量保持**：`list_entries()` 聚合全部分片，`sync()` / `replace()`
   按条目自身 metadata 路由，因此既有的全量对账逻辑（`_sync_vector_index`）
   无需改动
3. **向后兼容**：默认**关闭**（`VECTOR_SHARD_BY_SCOPE=0`）。
   开启前需重建索引，旧布局不会被静默误读
4. **可回退**：关闭开关即回到单索引，不需要数据迁移

### 为什么默认关闭

开启会改变磁盘布局。对已部署实例，这意味着要么重建索引，要么在切换时出现
"旧数据在新布局下不可见"。这属于需要运维决策的变更，不适合在一次代码修复里
静默生效。因此做成显式开关，并在文档中给出开启步骤与代价。

### 验收

- 新增测试：分片模式下不同 tenant/workspace 的数据互相不可见
- 新增测试：`list_entries()` 聚合结果与单索引模式一致
- 新增测试：`sync()` / `replace()` 正确路由且不留孤儿分片
- 新增测试：开关关闭时行为与现状**完全一致**（回归保护）
- 新增测试：作用域标识含路径分隔符等特殊字符时目录名安全

---

## ⑤ 离线语义模型（体验升级）

### 问题

`artpm_agent/memory/embeddings.py:21-53` 的 `DeterministicEmbeddingProvider`
用字符 3/4/5-gram + MD5 特征哈希生成 1536 维向量。它**完全不具备同义理解能力**：

```text
"成本" → n-gram: 成本(短文本回退整串)
"开销" → n-gram: 开销
两者无共享 n-gram → 余弦相似度 = 0
```

同一份代码也是意图识别的第二层（`routing/service.py` 的 embedding tier），
因此"把成本说成开销就认不出"是系统级的。

### 影响

- 离线语义检索召回率受限于字面重合
- 意图识别的 embedding tier 对近义词、换词表达无效，被迫更多落到澄清或 LLM 层

### 方案

采用**可选 provider + 优雅降级**，而不是替换默认实现：

| 层次 | Provider | 依赖 | 默认 |
| --- | --- | --- | --- |
| 默认 | `DeterministicEmbeddingProvider` | 无 | ✅ 启用 |
| 可选 | ONNX 本地语义模型（如 BGE-small-zh，约 100MB） | `onnxruntime` + 模型文件 | 显式启用 |
| 可选 | 远程 embedding API | 网络 + Key | 显式启用 |

实现策略：

1. 定义 `OnnxEmbeddingProvider`，实现既有 `EmbeddingProvider` 协议，
   带独立 fingerprint（如 `onnx-bge-small-zh-v1.5:512`）
2. 模型文件**不由本仓库分发**，通过环境变量指向本地路径；缺失时报清晰错误
3. provider 选择在 `create_embedding_provider()` 内完成，配置驱动
4. 切换 provider 会改变 fingerprint，触发索引重建——这是**期望行为**，
   因为不同模型的向量空间不可混用
5. 默认保持本地哈希，确保无网络、无模型文件时零配置可用

### 环境现状

本机已具备 `onnxruntime 1.30.0`（经 `pip list` 核实），因此接入路径可行性已确认；
但**模型权重文件不在仓库内**，需要部署方自行提供。因此本项交付的是
**可插拔接入点 + 降级逻辑**，而非打包一个模型。

### 真实模型验证记录

为确认不是"结构上看起来可行"，用 `onnx` 生成了一个真实 ONNX 计算图
（Gather 嵌入表 + ReduceMean 池化，dim=16），并用 `onnxruntime` 实际加载执行：

```text
provider:        OnnxEmbeddingProvider
fingerprint:     onnx-sentence-encoder-v1:tiny_encoder:16
fallback_reason: None
dim:             16
norm ~1:         1.0
deterministic:   True
batch len:       3
vectors finite:  True
```

验证结论：真实 onnxruntime 会话可以正常创建、执行推理、返回归一化向量。
该验证脚本为一次性工具，未留在仓库中。

验证过程中发现并修复了一个真实缺陷：onnxruntime 的异常
（`InvalidProtobuf`、`EngineError`、`RuntimeException`）**直接继承 `Exception`，
没有共同基类**，因此损坏的模型文件无法按类型捕获，会让整轮对话崩溃而非降级。
已在 `_open_onnx_session()` 的加载边界处把异常转换为 `RuntimeError`，
使调用方能基于一个可预期的异常降级。

### 启用方式

```bash
# 1. 准备模型与分词器
pip install onnxruntime
# 下载 ONNX 版 BGE-small-zh（约 100MB）到本地，例如 ./models/bge-small-zh.onnx

# 2. 指向模型并选择 provider
export ARTPM_EMBEDDING_PROVIDER=onnx
export ARTPM_EMBEDDING_MODEL_PATH=./models/bge-small-zh.onnx
export ARTPM_EMBEDDING_DIMENSION=512
```

注意：切换 provider 会改变 fingerprint，**既有索引会触发重建**（这是期望行为，
不同模型的向量空间不可混用）。

### 验收

- ✅ 未配置模型路径时明确报错并给出配置指引
- ✅ 配置了不存在的路径时报错可读
- ✅ provider 选择逻辑对未知值拒绝
- ✅ 默认路径下行为与现状完全一致
- ✅ 真实 ONNX 模型端到端执行验证通过
- ✅ 损坏模型文件降级而非崩溃

---

## 依赖关系与执行顺序

```text
① 断网兜底  ──┐
② 嵌入接口  ──┼─→ 无依赖，可独立完成
③ 文档勘误  ──┘

④ FAISS 分区 ──→ 依赖 ② 的 fingerprint 机制（切换 provider 需正确重建）

⑤ 离线模型   ──→ 依赖 ② 的 provider 抽象；与 ④ 共享"fingerprint 变更触发重建"路径
```

因此执行顺序为 **① → ② → ③ → ④ → ⑤**，其中 ④⑤ 共用 ② 建立的 provider
与 fingerprint 契约。

## 全局验收标准

1. 全部现有测试保持通过（基线：`1438 passed, 89 deselected`）
2. 新增测试覆盖每一项的行为变更与回归保护
3. `ruff check` 与 mypy 增量门禁通过
4. 默认配置下**行为不变**：离线可用、无网络依赖、无需模型文件
5. 文档描述与代码行为一致——这是本次优化的核心目标之一

## 最终验证结果

全部五项已完成。实测结果：

| 门禁 | 结果 |
| --- | --- |
| 后端离线回归 | `1495 passed, 89 deselected`（原 1438，新增 57 项） |
| Ruff | `All checks passed!` |
| mypy 增量门禁 | 通过，278 条基线（较原先**减少 4 条**） |
| 核心覆盖率门禁 | 92%（门槛 90%） |
| 新增测试 | 57 项，分布在 4 个新测试文件 |

新增测试文件：

| 文件 | 项数 | 覆盖内容 |
| --- | --- | --- |
| `tests/test_conversation_compressor_fallback.py` | 8 | 断网截断兜底、降级标记、注入生效 |
| `tests/test_embedding_providers.py` | 16 | provider 选择、降级可见性、指纹不可混用 |
| `tests/test_onnx_embedding_provider.py` | 13 | 归一化、池化、维度校验、损坏模型降级 |
| `tests/test_sharded_vector_store.py` | 20 | 租户隔离、分片路由、布局兼容、路径安全 |

覆盖率门禁同时扩展到了新模块（`sharded_vector_store.py`、`embeddings.py`），
避免新代码落在门禁保护之外。

## 顺带修复的问题

实施过程中发现并修复了三个**计划外的真实缺陷**：

| # | 问题 | 影响 | 修复 |
| --- | --- | --- | --- |
| 1 | `ui_state.py` 的 `create_embedding_provider = None` 无 `type: ignore` | mypy 会回显完整函数签名，任何签名变更都在该行产生"新错误"，即使这行没改——本次就误报了 1 条 | 加显式 `type: ignore[assignment]` |
| 2 | `vector_store.py` 用 `**dict` 解包构造后端 | 字典解包绕过了关键字参数类型检查，两个本地后端因此可以静默漂移 | 改为具名属性 + 显式关键字传参 |
| 3 | onnxruntime 异常无共同基类 | `InvalidProtobuf`/`EngineError`/`RuntimeException` 都直接继承 `Exception`，损坏的模型文件无法按类型捕获，会让整轮对话崩溃而非降级 | 在 `_open_onnx_session()` 加载边界转换为 `RuntimeError` |

## 不在本次范围

- 替换默认嵌入模型为神经网络模型（属部署决策，需要模型分发方案）
- Qdrant 侧的同等分区（Qdrant 原生支持 payload 过滤与分片，无需应用层改造）
- 历史归档文档的批量勘误（`docs/archive/` 不作为当前契约）
