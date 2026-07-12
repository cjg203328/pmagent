# ArtPM Agent 源码目录

项目的权威启动、配置、架构和测试说明位于上级目录 [README.md](../README.md)。

主要入口：

- `app.py`：Streamlit Web 应用。
- `main.py`：命令行交互入口。
- `agent.py`：意图识别与 Skill 调度。
- `skills/`：当前业务实现。

`app_old.py`、`app_backup.py`、`app_simple.py` 仅保留为历史参考，不是受支持的启动入口。

`core/chat_agent.py`、`core/llm_client.py`、`core/rag_system.py`、`core/tools.py`、`core/skills.py`、`core/mcp_skills.py` 属于早期实验架构，当前应用不会导入；当前实现以 `agent.py`、`skills/`、`memory/` 为准。
