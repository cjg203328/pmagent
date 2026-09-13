# ArtPM Agent 优化策略 - 多视角分析

**版本**: v1.0  
**日期**: 2026-07-18  
**分析基线**: v0.2.0

---

## 目录

1. [算法工程师视角](#一算法工程师视角)
2. [开发工程师视角](#二开发工程师视角)
3. [测试工程师视角](#三测试工程师视角)
4. [产品经理视角](#四产品经理视角)
5. [优先级矩阵](#五优先级矩阵)

---

## 一、算法工程师视角

### 1.1 向量检索优化

**现状问题**：
- FAISS 使用 cosine similarity 的平坦索引（FlatL2），对大规模数据 O(n) 线性扫描
- `embedding_provider=local_feature_hash` 为本地特征哈希，语义表达能力有限
- 向量维度固定 1536，未针对实际数据分布优化

**优化方案**：

```python
# artpm_agent/memory/vector_store.py 优化点

# 1. 引入 IVF (Inverted File) 索引
def _create_index_with_ivf(self, dimension: int, nlist: int = 100):
    """Use IVF index for large-scale retrieval (> 10k vectors)"""
    import faiss
    quantizer = faiss.IndexFlatL2(dimension)
    index = faiss.IndexIVFFlat(quantizer, dimension, nlist)
    return index

# 2. 动态索引切换策略
def _select_index_type(self, count: int, dimension: int):
    if count < 1000:
        return faiss.IndexFlatL2(dimension)  # 小规模保持精确
    elif count < 100000:
        return self._create_index_with_ivf(dimension, nlist=int(count**0.5))
    else:
        # 超大规模使用 HNSW
        return faiss.IndexHNSWFlat(dimension, 32)

# 3. 量化压缩（牺牲少量精度换内存）
def _create_pq_index(self, dimension: int, m: int = 8):
    """Product Quantization for memory efficiency"""
    index = faiss.IndexPQ(dimension, m, 8)
    return index
```

**预期收益**：
- 10k+ 向量时检索速度提升 10-50x
- 内存占用降低 4-8x（PQ 模式）
- 召回率保持 95%+ （IVF + PQ 组合）

### 1.2 意图路由算法优化

**现状分析**（[routing/service.py](../../artpm_agent/routing/service.py)）：
- 三层路由：关键词匹配 → 向量相似度 → LLM 分类
- 关键词匹配用简单加权求和，未考虑词序、上下文
- 向量相似度阈值硬编码（0.7/0.75/0.8），未自适应

**优化方案**：

```python
# artpm_agent/routing/service.py 增强

class IntentRouter:
    def __init__(self, ...):
        # 新增：混合打分机制
        self._hybrid_scorer = HybridIntentScorer()
        # 新增：动态阈值调整器
        self._threshold_adjuster = AdaptiveThresholdAdjuster()
    
    def detect(self, user_input: str) -> Optional[str]:
        # 1. 多路并行打分
        kw_score, kw_intent = self._detect_via_keywords(user_input)
        emb_score, emb_intent = self._detect_via_embedding(user_input)
        llm_score, llm_intent = self._detect_via_llm(user_input)
        
        # 2. 加权融合 + 置信度估计
        final_intent, confidence = self._hybrid_scorer.fuse(
            [(kw_intent, kw_score, 0.3),
             (emb_intent, emb_score, 0.5),
             (llm_intent, llm_score, 0.2)]
        )
        
        # 3. 动态阈值（基于历史准确率）
        threshold = self._threshold_adjuster.get_threshold(final_intent)
        
        return final_intent if confidence >= threshold else None

class HybridIntentScorer:
    """融合多路信号，输出归一化置信度"""
    def fuse(self, signals: List[Tuple[str, float, float]]) -> Tuple[str, float]:
        # 投票机制 + Borda count
        votes = {}
        for intent, score, weight in signals:
            if intent:
                votes[intent] = votes.get(intent, 0) + score * weight
        
        if not votes:
            return None, 0.0
        
        winner = max(votes.items(), key=lambda x: x[1])
        # 归一化到 [0, 1]
        confidence = winner[1] / sum(votes.values())
        return winner[0], confidence

class AdaptiveThresholdAdjuster:
    """根据历史准确率动态调整阈值"""
    def __init__(self):
        self.history = {}  # intent -> (correct, total)
    
    def get_threshold(self, intent: str) -> float:
        if intent not in self.history:
            return 0.75  # 默认
        
        correct, total = self.history[intent]
        accuracy = correct / total if total > 0 else 0.5
        
        # 准确率高 → 降低阈值（更激进）
        # 准确率低 → 提高阈值（更保守）
        return 0.9 - accuracy * 0.3  # [0.6, 0.9] 区间
    
    def update(self, intent: str, was_correct: bool):
        """每次路由后通过反馈更新"""
        if intent not in self.history:
            self.history[intent] = [0, 0]
        self.history[intent][1] += 1
        if was_correct:
            self.history[intent][0] += 1
```

**预期收益**：
- 意图识别准确率从 ~80% 提升到 90%+
- 降低误路由导致的用户体验下降
- 自适应阈值减少人工调参

### 1.3 模型故障转移策略优化

**现状**（[providers/gateway.py](../../artpm_agent/providers/gateway.py)）：
- 固定 60s 熔断器冷却时间
- 无区分性重试（网络错误、限流、模型错误一视同仁）
- 缺少预测性故障检测

**优化方案**：

```python
# artpm_agent/providers/gateway.py 增强

class ModelGateway:
    def __init__(self, ...):
        self._health_tracker = ModelHealthTracker()
        self._retry_policy = SmartRetryPolicy()
    
    def chat_with_failover(self, prompt, system_prompt, history, **kwargs):
        attempts = self._model_attempts(require_vision=bool(kwargs.get("image_paths")))
        
        for attempt_idx, (model_id, client) in enumerate(attempts):
            # 预测性检查（基于近期健康度）
            health_score = self._health_tracker.get_health_score(model_id)
            if health_score < 0.3 and attempt_idx > 0:
                logger.info(f"跳过不健康模型 {model_id}（健康度 {health_score:.2f}）")
                continue
            
            try:
                start = time.time()
                response = client.chat(prompt, system_prompt, history, **kwargs)
                latency = time.time() - start
                
                # 成功：更新健康状态
                self._health_tracker.record_success(model_id, latency)
                return response
            
            except Exception as e:
                error_type = self._classify_error(e)
                latency = time.time() - start
                
                # 失败：记录并决定是否重试
                self._health_tracker.record_failure(model_id, error_type, latency)
                
                if not self._retry_policy.should_retry(error_type, attempt_idx):
                    raise
                
                cooldown = self._retry_policy.get_cooldown(error_type, model_id)
                self._mark_model_unavailable(model_id, cooldown)
        
        raise RuntimeError("所有模型均不可用")
    
    def _classify_error(self, error: Exception) -> str:
        """细粒度错误分类"""
        err_str = str(error).lower()
        if "rate limit" in err_str or "429" in err_str:
            return "rate_limit"
        elif "timeout" in err_str or "timed out" in err_str:
            return "timeout"
        elif "connection" in err_str or "network" in err_str:
            return "network"
        elif "auth" in err_str or "401" in err_str or "403" in err_str:
            return "auth"
        elif "500" in err_str or "502" in err_str or "503" in err_str:
            return "server"
        else:
            return "unknown"

class ModelHealthTracker:
    """基于滑动窗口的健康度追踪"""
    def __init__(self, window_size: int = 100):
        self.window_size = window_size
        self.records = {}  # model_id -> deque of (success: bool, latency: float, ts: float)
    
    def get_health_score(self, model_id: str) -> float:
        """综合评分：成功率 0.6 + 延迟归一化 0.3 + 时间衰减 0.1"""
        if model_id not in self.records or len(self.records[model_id]) == 0:
            return 1.0  # 未知模型默认健康
        
        recent = list(self.records[model_id])[-self.window_size:]
        
        # 成功率
        success_rate = sum(1 for r in recent if r[0]) / len(recent)
        
        # 延迟（越低越好，归一化到 [0, 1]）
        latencies = [r[1] for r in recent if r[0]]
        if latencies:
            avg_latency = sum(latencies) / len(latencies)
            # 假设 2s 以下为优秀，10s 以上为差
            latency_score = max(0, 1 - (avg_latency - 2) / 8)
        else:
            latency_score = 0.0
        
        # 时间衰减（最近的记录权重更高）
        now = time.time()
        recency_weights = [math.exp(-(now - r[2]) / 300) for r in recent]  # 5min 半衰期
        weighted_success = sum(w for w, r in zip(recency_weights, recent) if r[0])
        recency_score = weighted_success / sum(recency_weights) if recency_weights else 0.5
        
        return success_rate * 0.6 + latency_score * 0.3 + recency_score * 0.1

class SmartRetryPolicy:
    """差异化重试策略"""
    def should_retry(self, error_type: str, attempt_idx: int) -> bool:
        # 认证错误不重试
        if error_type == "auth":
            return False
        # 其他错误根据尝试次数决定
        max_retries = {"rate_limit": 2, "timeout": 3, "network": 3, "server": 2, "unknown": 1}
        return attempt_idx < max_retries.get(error_type, 1)
    
    def get_cooldown(self, error_type: str, model_id: str) -> float:
        """差异化冷却时间"""
        base_cooldown = {
            "rate_limit": 120,  # 限流：等 2 分钟
            "timeout": 30,      # 超时：等 30 秒
            "network": 10,      # 网络：等 10 秒
            "server": 60,       # 服务器：等 1 分钟
            "unknown": 60,
        }
        return base_cooldown.get(error_type, 60)
```

**预期收益**：
- 降低不必要的重试开销（认证错误快速失败）
- 提升故障转移成功率（跳过不健康节点）
- 更细粒度的可观测性（错误类型分布）

### 1.4 Embedding 模型升级

**现状**：
- `local_feature_hash` 缺乏语义理解
- 跨语言、同义词、长尾 query 召回率低

**推荐方案**：

| 模型 | 维度 | 优势 | 适用场景 |
|------|------|------|---------|
| `paraphrase-multilingual-MiniLM-L12-v2` | 384 | 轻量、中英文 | 离线部署 |
| `bge-small-zh-v1.5` | 512 | 中文优化 | 国内场景 |
| `text-embedding-3-small` (OpenAI) | 1536 | 高质量 | 云端模式 |

```python
# artpm_agent/memory/embeddings.py 增强

class HybridEmbeddingProvider:
    """混合模式：本地 fallback + 云端加速"""
    def __init__(self, config):
        self.local_model = SentenceTransformer("paraphrase-multilingual-MiniLM-L12-v2")
        self.cloud_client = None
        if config.get("openai_api_key"):
            self.cloud_client = OpenAIEmbeddingClient(config)
    
    def embed(self, text: str) -> List[float]:
        # 优先云端（高质量）
        if self.cloud_client:
            try:
                return self.cloud_client.embed(text)
            except Exception as e:
                logger.warning(f"云端 embedding 失败，降级到本地: {e}")
        
        # 降级本地
        return self.local_model.encode(text).tolist()
```

**实施建议**：
1. 先在小规模数据集上 A/B 测试召回率
2. 评估延迟增加是否可接受（本地模型 ~10ms，云端 ~100ms）
3. 提供配置开关让用户选择模式

---

## 二、开发工程师视角

### 2.1 架构解耦与模块化

**现状问题**：
- `agent.py` 承载过多职责（1300+ 行）
- Harness 系统虽然引入，但与 `agent.chat()` 并存造成双路径
- 配置管理分散在多处（`config.py`, `.env`, `pyproject.toml`）

**优化方案**：

