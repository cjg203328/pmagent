"""
智能对话集成 - LLM + RAG + 工具调用
"""
import asyncio
from typing import Dict, List, Optional
import json
from datetime import datetime


class SmartChatAgent:
    """智能对话Agent - 集成LLM、RAG、Tools"""

    def __init__(self, llm_client, rag_pipeline, tool_registry, skill_registry, db_manager):
        self.llm = llm_client
        self.rag = rag_pipeline
        self.tools = tool_registry
        self.skills = skill_registry
        self.db = db_manager

        # 系统Prompt
        self.system_prompt = """
# 身份定位
你是 ArtPM Copilot，一个专门为游戏美术项目经理设计的AI助手。

## 核心能力
1. 📄 文档处理：解析报价单、合同，提取关键信息
2. 💰 利润计算：精确计算项目利润率和成本分解
3. 👥 任务分配：智能匹配团队成员和任务
4. 📊 进度追踪：监控项目进度，预警风险
5. 📚 知识查询：回答行业知识、历史项目数据

## 工作原则
- 专业：使用游戏美术行业术语
- 精确：数字计算必须准确
- 主动：发现问题主动提醒
- 高效：优先使用工具而不是对话

## 可用工具
{available_tools}

## 响应格式
- 简洁明了，重点突出
- 使用表格、列表呈现数据
- 包含具体建议和行动项
"""

    async def chat(self, user_message: str, context: Dict = None) -> Dict:
        """
        智能对话

        Args:
            user_message: 用户消息
            context: 上下文（会话历史、当前状态等）

        Returns:
            回复结果
        """
        context = context or {}

        # 1. 意图识别
        intent = await self._detect_intent(user_message)

        # 2. 根据意图选择处理方式
        if intent["type"] == "tool_call":
            # 需要调用工具
            response = await self._handle_tool_call(user_message, intent, context)

        elif intent["type"] == "knowledge_query":
            # 知识查询（RAG）
            response = await self._handle_knowledge_query(user_message, context)

        elif intent["type"] == "data_query":
            # 数据库查询
            response = await self._handle_data_query(user_message, intent, context)

        elif intent["type"] == "complex_task":
            # 复杂任务（使用Skill）
            response = await self._handle_complex_task(user_message, intent, context)

        else:
            # 普通对话
            response = await self._handle_chat(user_message, context)

        return response

    async def _detect_intent(self, message: str) -> Dict:
        """意图识别"""
        # 使用LLM识别意图
        intent_prompt = f"""
分析用户意图，从以下类型中选择：

1. tool_call - 需要调用单个工具（如：计算利润、解析文档）
2. knowledge_query - 查询行业知识（如：角色模型制作流程）
3. data_query - 查询数据库数据（如：查看项目列表、统计信息）
4. complex_task - 复杂任务，需要多步骤（如：创建新项目并分配任务）
5. chat - 普通对话

用户消息: {message}

返回JSON格式:
{{
    "type": "类型",
    "confidence": 0.95,
    "tools": ["工具名称"],
    "params": {{"参数": "值"}},
    "reasoning": "推理过程"
}}
"""

        try:
            messages = [{"role": "user", "content": intent_prompt}]
            result = await self.llm.chat(messages, temperature=0.1)

            # 解析JSON
            content = result.get("content", "{}")
            # 提取JSON部分
            import re
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                intent = json.loads(json_match.group())
            else:
                intent = {"type": "chat", "confidence": 0.5}

            return intent

        except Exception as e:
            print(f"意图识别失败: {e}")
            return {"type": "chat", "confidence": 0.5}

    async def _handle_tool_call(self, message: str, intent: Dict, context: Dict) -> Dict:
        """处理工具调用"""
        tool_name = intent.get("tools", [])[0] if intent.get("tools") else None
        params = intent.get("params", )

        if not tool_name:
            return await self._handle_chat(message, context)

        # 调用工具
        tool_result = await self.tools.execute(tool_name, **params)

        # 生成自然语言回复
        summary_prompt = f"""
用户问题: {message}

工具调用: {tool_name}
参数: {json.dumps(params, ensure_ascii=False)}
结果: {json.dumps(tool_result, ensure_ascii=False, indent=2)}

请用自然语言总结结果，重点突出：
1. 关键数据和指标
2. 存在的问题或风险
3. 具体的建议

回复格式：简洁、专业、有结构
"""

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": summary_prompt}
        ]

        result = await self.llm.chat(messages)

        return {
            "type": "tool_result",
            "content": result.get("content"),
            "tool_used": tool_name,
            "tool_result": tool_result
        }

    async def _handle_knowledge_query(self, message: str, context: Dict) -> Dict:
        """处理知识查询（RAG）"""
        # 使用RAG检索相关知识
        rag_result = await self.rag.query(message, top_k=3)

        return {
            "type": "knowledge",
            "content": rag_result.get("answer"),
            "sources": rag_result.get("retrieved_docs", [])
        }

    async def _handle_data_query(self, message: str, intent: Dict, context: Dict) -> Dict:
        """处理数据查询"""
        # 识别查询类型
        query_type = None
        if any(kw in message for kw in ["项目", "project"]):
            query_type = "projects"
        elif any(kw in message for kw in ["任务", "task"]):
            query_type = "tasks"
        elif any(kw in message for kw in ["统计", "数据", "报表"]):
            query_type = "stats"

        # 查询数据
        if query_type == "projects":
            projects = self.db.list_projects(limit=10)
            data = [p.to_dict() for p in projects]
        elif query_type == "stats":
            data = self.db.get_project_stats()
        else:
            data = {}

        # 生成回复
        summary_prompt = f"""
用户问题: {message}

查询结果: {json.dumps(data, ensure_ascii=False, indent=2)}

请用表格和列表形式呈现数据，并给出简要分析。
"""

        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": summary_prompt}
        ]

        result = await self.llm.chat(messages)

        return {
            "type": "data_query",
            "content": result.get("content"),
            "data": data
        }

    async def _handle_complex_task(self, message: str, intent: Dict, context: Dict) -> Dict:
        """处理复杂任务（使用Skill）"""
        # TODO: 实现Skill调用逻辑
        return await self._handle_chat(message, context)

    async def _handle_chat(self, message: str, context: Dict) -> Dict:
        """处理普通对话"""
        # 构建消息历史
        messages = [
            {"role": "system", "content": self.system_prompt.format(
                available_tools=self._format_tools()
            )}
        ]

        # 添加历史消息
        if "history" in context:
            messages.extend(context["history"][-6:])  # 最近3轮

        # 添加当前消息
        messages.append({"role": "user", "content": message})

        # 调用LLM
        result = await self.llm.chat(messages)

        return {
            "type": "chat",
            "content": result.get("content")
        }

    def _format_tools(self) -> str:
        """格式化工具列表"""
        tools = self.tools.list_tools()
        formatted = []

        for tool in tools:
            formatted.append(f"- {tool['name']}: {tool['description']}")

        return "\n".join(formatted)


class ConversationManager:
    """对话管理器 - 管理会话历史和上下文"""

    def __init__(self):
        self.sessions = {}  # {session_id: conversation_data}

    def create_session(self, session_id: str) -> Dict:
        """创建会话"""
        self.sessions[session_id] = {
            "id": session_id,
            "created_at": datetime.now(),
            "messages": [],
            "context": {}
        }
        return self.sessions[session_id]

    def add_message(self, session_id: str, role: str, content: str, metadata: Dict = None):
        """添加消息"""
        if session_id not in self.sessions:
            self.create_session(session_id)

        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "metadata": metadata or {}
        }

        self.sessions[session_id]["messages"].append(message)

    def get_history(self, session_id: str, limit: int = 10) -> List[Dict]:
        """获取历史消息"""
        if session_id not in self.sessions:
            return []

        messages = self.sessions[session_id]["messages"]
        return messages[-limit:]

    def get_context(self, session_id: str) -> Dict:
        """获取上下文"""
        if session_id not in self.sessions:
            return {}

        return {
            "history": [
                {"role": m["role"], "content": m["content"]}
                for m in self.get_history(session_id, limit=6)
            ],
            "session_info": {
                "id": session_id,
                "message_count": len(self.sessions[session_id]["messages"])
            }
        }

    def clear_session(self, session_id: str):
        """清空会话"""
        if session_id in self.sessions:
            del self.sessions[session_id]


# 使用示例
async def demo():
    """演示智能对话"""
    from core.llm_client import UniversalLLMClient
    from core.tools import init_default_tools
    from core.skills import init_default_skills
    from database.models import DatabaseManager

    # 初始化
    llm = UniversalLLMClient(
        provider="anthropic",
        model="claude-3-5-sonnet-20241022",
        api_key="your-api-key"
    )

    tools = init_default_tools()
    skills = init_default_skills(tools)
    db = DatabaseManager()

    # 创建Agent
    agent = SmartChatAgent(
        llm_client=llm,
        rag_pipeline=None,  # RAG可选
        tool_registry=tools,
        skill_registry=skills,
        db_manager=db
    )

    # 对话管理
    conv_manager = ConversationManager()
    session_id = "test_session"

    # 示例对话
    test_messages = [
        "你好，介绍一下你自己",
        "帮我计算一个项目的利润：报价30万，成本20万",
        "查看当前有哪些项目",
    ]

    for msg in test_messages:
        print(f"\n用户: {msg}")

        # 获取上下文
        context = conv_manager.get_context(session_id)

        # 调用Agent
        response = await agent.chat(msg, context)

        # 保存消息
        conv_manager.add_message(session_id, "user", msg)
        conv_manager.add_message(session_id, "assistant", response["content"])

        print(f"AI: {response['content']}")
        print(f"类型: {response['type']}")


if __name__ == "__main__":
    asyncio.run(demo())
