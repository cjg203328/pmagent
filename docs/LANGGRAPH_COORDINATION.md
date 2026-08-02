# LangGraph task coordination

LangChain v1 is the model and tool adapter in ArtPM. LangGraph is the
orchestration runtime for work that spans multiple specialist agents or Skills.
The two layers are intentionally separate:

- LangGraph owns task dependencies, bounded parallel batches, retries,
  human-in-the-loop pauses, and graph state.
- `WorkflowEngine` remains the authority for installed workflow definitions,
  Skill capability allowlists, idempotency, risk policy, and persisted
  side-effect approvals.
- The knowledge and evolution pipeline remains in the existing SQLite/FAISS
  stores. A task runner can read that context, but the graph does not silently
  write ordinary chat into the knowledge base.

## Start a collaboration graph

`WorkflowCoordinator` exposes the integration point without changing ordinary
chat turns:

```python
from artpm_agent.workflows import TaskSpec

graph = coordinator.create_task_orchestrator()
state = graph.invoke(
    "prepare a project estimate",
    [
        TaskSpec(
            id="research",
            skill_id="progress_tracker",
            capability="projects.read",
            input_data={"project_id": "P-100"},
        ),
        {
            "id": "review",
            "agent": "finance-reviewer",
            "depends_on": ["research"],
            "input_data": {"source": "$results.research"},
        },
    ],
    conversation_id=conversation_id,
    thread_id="estimate-P-100",
)
```

Independent ready tasks run in the same bounded batch. A dependent task only
runs after all of its dependencies succeed. A task result is available through
`$results.<task_id>` references or through the runner context.

## Specialist agents

Use a runner when a task belongs to a specialist agent rather than a local
Skill. The runner receives a validated `TaskSpec` and a JSON-compatible context
containing resolved `inputs`, prior `results`, `attempt`, and approval state:

```python
def run_agent_task(task, context):
    return specialist_agents[task.agent].run(
        goal=context["goal"],
        inputs=context["inputs"],
        prior_results=context["results"],
    )

graph = coordinator.create_task_orchestrator(task_runner=run_agent_task)
```

A failing task is attempted `LANGGRAPH_MAX_RETRIES + 1` times, or exactly
`max_attempts` times when the task supplies that field. Non-JSON results are
rejected so a checkpoint cannot be corrupted by an opaque Python object, and a
runner returning `{"success": False, ...}` counts as a failure rather than a
result.

## Approval and recovery

`requires_approval=true` is what pauses the graph, and `side_effect=true` cannot
be set without it — `TaskSpec` rejects a side-effect task that does not require
approval, so a task cannot declare a side effect and skip the gate. The run
returns `status == "waiting_approval"` with the pending task IDs. Resume the
exact checkpoint with the same thread ID:

```python
if state["status"] == "waiting_approval":
    state = graph.resume("estimate-P-100", True)
```

The default in-process `MemorySaver` is suitable for tests and a single local
process. Production deployments must inject a durable LangGraph checkpointer;
the optional `SessionStore` audit adapter records graph start, batch, approval,
and finish events in the conversation database but is not itself a checkpoint.

The default Skill adapter only permits read-only allowlisted capabilities.
Side-effect Skills continue through `WorkflowEngine` so their existing server
approval and idempotency guarantees cannot be bypassed by a graph task.

## Configuration

```env
AGENT_ORCHESTRATION_FRAMEWORK=langgraph
LANGGRAPH_ENABLED=true
LANGGRAPH_MAX_PARALLELISM=4
LANGGRAPH_MAX_RETRIES=2
```

The graph factory is opt-in at the call site. This keeps existing linear
workflow routing stable while providing a ready LangGraph boundary for future
multi-agent collaboration.
