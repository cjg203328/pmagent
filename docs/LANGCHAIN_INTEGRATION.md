# LangChain integration

ArtPM uses LangChain v1 as the model integration layer. The application still
owns the higher-risk boundaries:

- `ModelGateway` selects models, records usage, handles cache and failover.
- `AgentLoop` validates tool schemas, enforces budgets, and executes approved tools.
- The harness injects workspace knowledge, feedback, episodes, and evolution strategies.
- SQLite and FAISS remain the durable knowledge and session stores.

This split is intentional. Replacing the whole runtime with `create_agent` would
discard the existing approval, persistence, and learning contracts.

## Configuration

```env
LLM_FRAMEWORK=langchain
LLM_PROVIDER=custom
LLM_MODEL=deepseek-v4-pro
OPENAI_API_KEY=your-key
OPENAI_API_BASE=https://api.example/v1
```

Set `LLM_FRAMEWORK=native` to use the legacy OpenAI/Anthropic SDK adapters. The
settings page exposes the same choice under 模型框架 as “LangChain v1” and
“原生 SDK”, defaulting to LangChain. An unrecognized `LLM_FRAMEWORK` value is
logged and falls back to the native adapter rather than failing startup.

LangChain providers are selected by the existing `LLM_PROVIDER` value:

- `openai` and `custom` use `langchain-openai`.
- `zhipu` uses the OpenAI-compatible LangChain adapter and its configured base URL.
- `anthropic` uses `langchain-anthropic`.

LangChain is lazy-loaded. Offline mode still works when API keys are empty, and
the native adapter remains available for deployments that explicitly select it.

## Dependency audit

The dependency boundary is intentionally small and each direct package has a
runtime owner:

| Distribution | ArtPM usage |
| --- | --- |
| `langchain` | v1 release-family compatibility boundary |
| `langchain-core` | structured tool-call message types |
| `langchain-openai` | OpenAI-compatible chat models |
| `langchain-anthropic` | Anthropic chat models |
| `langgraph` | dependency-aware collaborative task graphs |

ArtPM does not directly enable LangSmith tracing, LangChain memory, retrievers,
or vector stores. Those concerns remain in the existing telemetry, SQLite, and
FAISS layers, so credentials and duplicate persistence are not introduced by a
framework default.

Run the offline audit after installation and in CI:

```bash
python -m artpm_agent.tools.audit_langchain
python -m artpm_agent.tools.audit_langchain --json
```

It verifies direct dependency declarations, the exact public symbols used by
the adapters, and the complete installed dependency graph through `pip check`.
Checking for newer releases remains a deliberate upgrade step because the
audit must also work in offline deployments.

For multi-agent task collaboration, see
[`LANGGRAPH_COORDINATION.md`](LANGGRAPH_COORDINATION.md). LangGraph coordinates
dependencies, bounded parallel execution, retries, and approval pauses while
the existing WorkflowEngine keeps capability and side-effect controls.
