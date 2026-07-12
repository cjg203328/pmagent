"""
统一LLM客户端 - 支持多种AI模型
支持: OpenAI, Anthropic, DeepSeek 等
"""
import os
from typing import Dict, List, Optional, AsyncIterator
from datetime import datetime
import json
import asyncio
from abc import ABC, abstractmethod

try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False


class TokenCounter:
    """Token计数器"""

    @staticmethod
    def count_tokens(text: str, model: str = "gpt-4") -> int:
        """估算token数量"""
        # 简单估算: 1 token ≈ 4 characters for English, ≈ 1.5 characters for Chinese
        chinese_chars = sum(1 for c in text if '一' <= c <= '鿿')
        english_chars = len(text) - chinese_chars

        estimated_tokens = int(chinese_chars / 1.5 + english_chars / 4)
        return estimated_tokens

    @staticmethod
    def count_messages_tokens(messages: List[Dict], model: str = "gpt-4") -> int:
        """计算消息列表的token数"""
        total = 0
        for msg in messages:
            total += TokenCounter.count_tokens(str(msg.get("content", "")), model)
            total += 4  # 每条消息的固定开销
        return total


class BaseProvider(ABC):
    """LLM Provider基类"""

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model
        self.token_counter = TokenCounter()

    @abstractmethod
    async def chat(self, messages: List[Dict], **kwargs) -> Dict:
        """对话接口"""
        pass

    @abstractmethod
    async def chat_stream(self, messages: List[Dict], **kwargs) -> AsyncIterator[str]:
        """流式对话接口"""
        pass

    def count_tokens(self, text: str) -> int:
        """计算token数"""
        return self.token_counter.count_tokens(text, self.model)


class OpenAIProvider(BaseProvider):
    """OpenAI Provider"""

    def __init__(self, api_key: str, model: str = "gpt-4o", base_url: str = None):
        super().__init__(api_key, model)
        if not OPENAI_AVAILABLE:
            raise ImportError("openai package not installed")

        self.client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=base_url
        )

    async def chat(self, messages: List[Dict], **kwargs) -> Dict:
        """对话"""
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                **kwargs
            )

            return {
                "content": response.choices[0].message.content,
                "model": response.model,
                "usage": {
                    "input_tokens": response.usage.prompt_tokens,
                    "output_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens
                },
                "finish_reason": response.choices[0].finish_reason
            }
        except Exception as e:
            return {
                "error": str(e),
                "content": None
            }

    async def chat_stream(self, messages: List[Dict], **kwargs) -> AsyncIterator[str]:
        """流式对话"""
        try:
            stream = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=True,
                **kwargs
            )

            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            yield f"Error: {str(e)}"


class AnthropicProvider(BaseProvider):
    """Anthropic Provider"""

    def __init__(self, api_key: str, model: str = "claude-3-5-sonnet-20241022"):
        super().__init__(api_key, model)
        if not ANTHROPIC_AVAILABLE:
            raise ImportError("anthropic package not installed")

        self.client = anthropic.AsyncAnthropic(api_key=api_key)

    async def chat(self, messages: List[Dict], **kwargs) -> Dict:
        """对话"""
        try:
            # Anthropic API需要分离system message
            system_msg = None
            conversation_msgs = []

            for msg in messages:
                if msg["role"] == "system":
                    system_msg = msg["content"]
                else:
                    conversation_msgs.append(msg)

            response = await self.client.messages.create(
                model=self.model,
                max_tokens=kwargs.get("max_tokens", 4096),
                system=system_msg,
                messages=conversation_msgs
            )

            return {
                "content": response.content[0].text,
                "model": response.model,
                "usage": {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                    "total_tokens": response.usage.input_tokens + response.usage.output_tokens
                },
                "finish_reason": response.stop_reason
            }
        except Exception as e:
            return {
                "error": str(e),
                "content": None
            }

    async def chat_stream(self, messages: List[Dict], **kwargs) -> AsyncIterator[str]:
        """流式对话"""
        try:
            system_msg = None
            conversation_msgs = []

            for msg in messages:
                if msg["role"] == "system":
                    system_msg = msg["content"]
                else:
                    conversation_msgs.append(msg)

            async with self.client.messages.stream(
                model=self.model,
                max_tokens=kwargs.get("max_tokens", 4096),
                system=system_msg,
                messages=conversation_msgs
            ) as stream:
                async for text in stream.text_stream:
                    yield text
        except Exception as e:
            yield f"Error: {str(e)}"


class DeepSeekProvider(BaseProvider):
    """DeepSeek Provider (兼容OpenAI API)"""

    def __init__(self, api_key: str, model: str = "deepseek-chat"):
        super().__init__(api_key, model)
        if not OPENAI_AVAILABLE:
            raise ImportError("openai package not installed")

        self.client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com"
        )

    async def chat(self, messages: List[Dict], **kwargs) -> Dict:
        """对话"""
        provider = OpenAIProvider(self.api_key, self.model, "https://api.deepseek.com")
        return await provider.chat(messages, **kwargs)

    async def chat_stream(self, messages: List[Dict], **kwargs) -> AsyncIterator[str]:
        """流式对话"""
        provider = OpenAIProvider(self.api_key, self.model, "https://api.deepseek.com")
        async for chunk in provider.chat_stream(messages, **kwargs):
            yield chunk


class UniversalLLMClient:
    """统一的LLM客户端"""

    PROVIDERS = {
        "openai": OpenAIProvider,
        "anthropic": AnthropicProvider,
        "deepseek": DeepSeekProvider
    }

    def __init__(self, provider: str, model: str, api_key: str, **kwargs):
        if provider not in self.PROVIDERS:
            raise ValueError(f"Unknown provider: {provider}")

        self.provider_name = provider
        self.provider = self.PROVIDERS[provider](api_key, model, **kwargs)
        self.model = model
        self.usage_tracker = UsageTracker()

    async def chat(self, messages: List[Dict], **kwargs) -> Dict:
        """统一对话接口"""
        # 记录输入token
        input_tokens = self.provider.token_counter.count_messages_tokens(messages, self.model)

        # 调用Provider
        start_time = datetime.now()
        result = await self.provider.chat(messages, **kwargs)
        latency = (datetime.now() - start_time).total_seconds()

        # 记录使用情况
        if "error" not in result:
            self.usage_tracker.track(
                provider=self.provider_name,
                model=self.model,
                input_tokens=result["usage"]["input_tokens"],
                output_tokens=result["usage"]["output_tokens"],
                latency=latency
            )

        return result

    async def chat_stream(self, messages: List[Dict], **kwargs) -> AsyncIterator[str]:
        """统一流式接口"""
        async for chunk in self.provider.chat_stream(messages, **kwargs):
            yield chunk

    def count_tokens(self, text: str) -> int:
        """计算token数"""
        return self.provider.count_tokens(text)

    def get_usage_stats(self) -> Dict:
        """获取使用统计"""
        return self.usage_tracker.get_stats()


class UsageTracker:
    """使用情况跟踪器"""

    def __init__(self):
        self.records = []

    def track(self, provider: str, model: str, input_tokens: int,
              output_tokens: int, latency: float):
        """记录使用"""
        record = {
            "timestamp": datetime.now().isoformat(),
            "provider": provider,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "latency": latency,
            "cost": self.calculate_cost(provider, model, input_tokens, output_tokens)
        }
        self.records.append(record)

    def calculate_cost(self, provider: str, model: str,
                      input_tokens: int, output_tokens: int) -> float:
        """计算成本"""
        # 价格表 (USD per 1M tokens)
        PRICING = {
            "gpt-4o": {"input": 2.50, "output": 10.00},
            "gpt-4-turbo": {"input": 10.00, "output": 30.00},
            "claude-3-5-sonnet-20241022": {"input": 3.00, "output": 15.00},
            "claude-3-opus": {"input": 15.00, "output": 75.00},
            "deepseek-chat": {"input": 0.14, "output": 0.28}
        }

        price = PRICING.get(model, {"input": 0, "output": 0})
        cost = (input_tokens / 1_000_000 * price["input"] +
                output_tokens / 1_000_000 * price["output"])
        return cost

    def get_stats(self) -> Dict:
        """获取统计信息"""
        if not self.records:
            return {
                "total_requests": 0,
                "total_tokens": 0,
                "total_cost": 0,
                "avg_latency": 0
            }

        return {
            "total_requests": len(self.records),
            "total_tokens": sum(r["total_tokens"] for r in self.records),
            "total_cost": sum(r["cost"] for r in self.records),
            "avg_latency": sum(r["latency"] for r in self.records) / len(self.records),
            "by_model": self._group_by_model(),
            "recent_records": self.records[-10:]
        }

    def _group_by_model(self) -> Dict:
        """按模型分组统计"""
        grouped = {}
        for record in self.records:
            model = record["model"]
            if model not in grouped:
                grouped[model] = {
                    "requests": 0,
                    "tokens": 0,
                    "cost": 0
                }
            grouped[model]["requests"] += 1
            grouped[model]["tokens"] += record["total_tokens"]
            grouped[model]["cost"] += record["cost"]
        return grouped


# 使用示例
async def demo():
    """演示用法"""
    # 1. OpenAI
    client_openai = UniversalLLMClient(
        provider="openai",
        model="gpt-4o",
        api_key=os.getenv("OPENAI_API_KEY")
    )

    # 2. Anthropic
    client_claude = UniversalLLMClient(
        provider="anthropic",
        model="claude-3-5-sonnet-20241022",
        api_key=os.getenv("ANTHROPIC_API_KEY")
    )

    # 3. DeepSeek
    client_deepseek = UniversalLLMClient(
        provider="deepseek",
        model="deepseek-chat",
        api_key=os.getenv("DEEPSEEK_API_KEY")
    )

    # 对话
    messages = [
        {"role": "user", "content": "你好，介绍一下自己"}
    ]

    result = await client_claude.chat(messages)
    print(result["content"])

    # 获取统计
    stats = client_claude.get_usage_stats()
    print(f"使用统计: {stats}")


if __name__ == "__main__":
    asyncio.run(demo())
