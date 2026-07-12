"""
RAG系统基础 - 向量存储和混合检索
"""
import numpy as np
from typing import List, Dict, Optional, Tuple
from pathlib import Path
import json
import pickle
from datetime import datetime

try:
    import faiss
    FAISS_AVAILABLE = True
except ImportError:
    FAISS_AVAILABLE = False
    print("Warning: faiss-cpu not installed. Vector search will be unavailable.")


class VectorStore:
    """FAISS向量存储"""

    def __init__(self, dimension: int = 1536, index_path: str = "./data/vector_store"):
        if not FAISS_AVAILABLE:
            raise ImportError("faiss-cpu is required for VectorStore")

        self.dimension = dimension
        self.index_path = Path(index_path)
        self.index_path.mkdir(parents=True, exist_ok=True)

        # FAISS索引
        self.index = faiss.IndexFlatL2(dimension)

        # 元数据存储
        self.metadata = []
        self.texts = []

        # 加载已有索引
        self._load_index()

    def add(self, text: str, embedding: np.ndarray, metadata: Dict = None):
        """添加向量"""
        if embedding.shape[0] != self.dimension:
            raise ValueError(f"Embedding dimension mismatch: {embedding.shape[0]} != {self.dimension}")

        # 添加到FAISS
        self.index.add(embedding.reshape(1, -1).astype('float32'))

        # 保存文本和元数据
        self.texts.append(text)
        self.metadata.append(metadata or {})

    def add_batch(self, texts: List[str], embeddings: np.ndarray, metadata: List[Dict] = None):
        """批量添加"""
        if embeddings.shape[1] != self.dimension:
            raise ValueError(f"Embedding dimension mismatch")

        self.index.add(embeddings.astype('float32'))
        self.texts.extend(texts)
        self.metadata.extend(metadata or [{} for _ in texts])

    def search(self, query_embedding: np.ndarray, top_k: int = 5) -> List[Dict]:
        """向量检索"""
        if self.index.ntotal == 0:
            return []

        query = query_embedding.reshape(1, -1).astype('float32')
        distances, indices = self.index.search(query, min(top_k, self.index.ntotal))

        results = []
        for i, (dist, idx) in enumerate(zip(distances[0], indices[0])):
            if idx < len(self.texts):
                results.append({
                    "id": int(idx),
                    "text": self.texts[idx],
                    "metadata": self.metadata[idx],
                    "score": float(1 / (1 + dist)),  # 转换为相似度分数
                    "distance": float(dist)
                })

        return results

    def save_index(self):
        """保存索引"""
        # 保存FAISS索引
        faiss.write_index(self.index, str(self.index_path / "faiss.index"))

        # 保存元数据
        with open(self.index_path / "metadata.json", 'w', encoding='utf-8') as f:
            json.dump({
                "texts": self.texts,
                "metadata": self.metadata
            }, f, ensure_ascii=False, indent=2)

    def _load_index(self):
        """加载索引"""
        index_file = self.index_path / "faiss.index"
        metadata_file = self.index_path / "metadata.json"

        if index_file.exists() and metadata_file.exists():
            try:
                self.index = faiss.read_index(str(index_file))

                with open(metadata_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.texts = data["texts"]
                    self.metadata = data["metadata"]

                print(f"Loaded {len(self.texts)} vectors from index")
            except Exception as e:
                print(f"Error loading index: {e}")

    def clear(self):
        """清空索引"""
        self.index = faiss.IndexFlatL2(self.dimension)
        self.texts = []
        self.metadata = []


class KeywordIndex:
    """关键词倒排索引"""

    def __init__(self):
        self.inverted_index = {}  # {keyword: [doc_ids]}
        self.documents = {}  # {doc_id: doc_text}
        self.doc_metadata = {}  # {doc_id: metadata}
        self.next_doc_id = 0

    def add(self, text: str, metadata: Dict = None) -> int:
        """添加文档"""
        doc_id = self.next_doc_id
        self.next_doc_id += 1

        self.documents[doc_id] = text
        self.doc_metadata[doc_id] = metadata or {}

        # 提取关键词并建立索引
        keywords = self._extract_keywords(text)
        for keyword in keywords:
            if keyword not in self.inverted_index:
                self.inverted_index[keyword] = []
            self.inverted_index[keyword].append(doc_id)

        return doc_id

    def search(self, query: str, top_k: int = 5) -> List[Dict]:
        """关键词检索"""
        keywords = self._extract_keywords(query)

        # 计算文档得分
        doc_scores = {}
        for keyword in keywords:
            if keyword in self.inverted_index:
                for doc_id in self.inverted_index[keyword]:
                    doc_scores[doc_id] = doc_scores.get(doc_id, 0) + 1

        # 排序
        sorted_docs = sorted(doc_scores.items(), key=lambda x: x[1], reverse=True)

        results = []
        for doc_id, score in sorted_docs[:top_k]:
            results.append({
                "id": doc_id,
                "text": self.documents[doc_id],
                "metadata": self.doc_metadata[doc_id],
                "score": score / len(keywords) if keywords else 0
            })

        return results

    def _extract_keywords(self, text: str) -> List[str]:
        """提取关键词（简单分词）"""
        # 简单实现：按空格和常见标点分词
        import re
        # 移除标点，转小写
        text = re.sub(r'[^\w\s]', ' ', text.lower())
        keywords = text.split()
        # 过滤停用词
        stopwords = {'的', '了', '是', '在', '和', 'the', 'a', 'an', 'is', 'are'}
        keywords = [k for k in keywords if k not in stopwords and len(k) > 1]
        return keywords


class HybridRetriever:
    """混合检索器：向量检索 + 关键词检索"""

    def __init__(self, vector_store: VectorStore, keyword_index: KeywordIndex,
                 embed_fn=None):
        self.vector_store = vector_store
        self.keyword_index = keyword_index
        self.embed_fn = embed_fn

    def add_document(self, text: str, metadata: Dict = None):
        """添加文档到两个索引"""
        # 向量索引
        if self.embed_fn:
            embedding = self.embed_fn(text)
            self.vector_store.add(text, embedding, metadata)

        # 关键词索引
        self.keyword_index.add(text, metadata)

    def retrieve(self, query: str, top_k: int = 5, alpha: float = 0.5) -> List[Dict]:
        """
        混合检索
        alpha: 向量检索权重 (1-alpha为关键词检索权重)
        """
        results = {}

        # 1. 向量检索
        if self.embed_fn:
            query_embedding = self.embed_fn(query)
            vector_results = self.vector_store.search(query_embedding, top_k * 2)

            for rank, result in enumerate(vector_results, 1):
                doc_id = result["text"]  # 使用文本作为唯一标识
                score = alpha * (1 / rank)  # RRF score
                if doc_id in results:
                    results[doc_id]["score"] += score
                else:
                    results[doc_id] = {
                        **result,
                        "score": score
                    }

        # 2. 关键词检索
        keyword_results = self.keyword_index.search(query, top_k * 2)

        for rank, result in enumerate(keyword_results, 1):
            doc_id = result["text"]
            score = (1 - alpha) * (1 / rank)
            if doc_id in results:
                results[doc_id]["score"] += score
            else:
                results[doc_id] = {
                    **result,
                    "score": score
                }

        # 3. 排序并返回top_k
        sorted_results = sorted(results.values(), key=lambda x: x["score"], reverse=True)
        return sorted_results[:top_k]


class RAGPipeline:
    """RAG检索增强生成管道"""

    def __init__(self, retriever: HybridRetriever, llm_client=None):
        self.retriever = retriever
        self.llm_client = llm_client

    async def query(self, user_query: str, top_k: int = 3,
                   include_context: bool = True) -> Dict:
        """RAG查询"""
        # 1. 检索相关文档
        retrieved_docs = self.retriever.retrieve(user_query, top_k=top_k)

        if not include_context:
            return {
                "query": user_query,
                "retrieved_docs": retrieved_docs,
                "answer": None
            }

        # 2. 构建增强Prompt
        context = self._build_context(retrieved_docs)

        prompt = f"""基于以下知识库信息回答用户问题：

【知识库内容】
{context}

【用户问题】
{user_query}

【回答要求】
1. 基于知识库内容回答
2. 如果知识库没有相关信息，明确说明
3. 保持专业和准确
"""

        # 3. 生成回答
        if self.llm_client:
            messages = [
                {"role": "system", "content": "你是一个专业的知识助手，基于提供的知识库信息回答问题。"},
                {"role": "user", "content": prompt}
            ]

            response = await self.llm_client.chat(messages)
            answer = response.get("content", "生成回答失败")
        else:
            answer = "LLM客户端未配置"

        return {
            "query": user_query,
            "retrieved_docs": retrieved_docs,
            "answer": answer,
            "context": context
        }

    def _build_context(self, docs: List[Dict]) -> str:
        """构建上下文"""
        context_parts = []
        for i, doc in enumerate(docs, 1):
            source = doc.get("metadata", {}).get("source", "未知来源")
            text = doc["text"]
            score = doc.get("score", 0)

            context_parts.append(f"""
[文档{i}] 来源: {source} (相关度: {score:.2f})
{text}
---
""")
        return "\n".join(context_parts)

    def add_knowledge(self, text: str, source: str, category: str = None):
        """添加知识"""
        metadata = {
            "source": source,
            "category": category,
            "added_at": datetime.now().isoformat()
        }
        self.retriever.add_document(text, metadata)


# 简单的Embedding函数（用于演示）
def simple_embed(text: str) -> np.ndarray:
    """
    简单的embedding函数（仅用于演示）
    生产环境应使用OpenAI embedding或本地模型
    """
    # 这里返回随机向量作为演示
    # 实际应该使用: openai.Embedding.create() 或本地embedding模型
    np.random.seed(hash(text) % (2**32))
    return np.random.randn(1536).astype('float32')


# 使用示例
async def demo():
    """演示RAG系统"""
    # 1. 创建存储
    vector_store = VectorStore(dimension=1536)
    keyword_index = KeywordIndex()

    # 2. 创建检索器
    retriever = HybridRetriever(
        vector_store=vector_store,
        keyword_index=keyword_index,
        embed_fn=simple_embed
    )

    # 3. 添加知识
    documents = [
        {
            "text": "角色模型制作包括建模、贴图、绑定三个阶段，费用通常在5000-15000元",
            "source": "角色模型定价参考",
            "category": "pricing"
        },
        {
            "text": "场景资产制作周期通常为7-15天，具体取决于复杂度",
            "source": "制作周期说明",
            "category": "timeline"
        },
        {
            "text": "腾讯项目通常要求PBR材质，需要提供Roughness和Metallic贴图",
            "source": "腾讯项目规范",
            "category": "requirements"
        }
    ]

    for doc in documents:
        retriever.add_document(doc["text"], {
            "source": doc["source"],
            "category": doc["category"]
        })

    # 保存索引
    vector_store.save_index()

    # 4. 查询
    query = "角色模型制作需要多少钱？"
    results = retriever.retrieve(query, top_k=2)

    print(f"\n查询: {query}")
    print(f"\n检索结果:")
    for i, result in enumerate(results, 1):
        print(f"\n[{i}] 相关度: {result['score']:.3f}")
        print(f"来源: {result['metadata'].get('source', '未知')}")
        print(f"内容: {result['text']}")


if __name__ == "__main__":
    import asyncio
    if FAISS_AVAILABLE:
        asyncio.run(demo())
    else:
        print("请安装 faiss-cpu: pip install faiss-cpu")
