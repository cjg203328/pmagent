# Architecture Diagram

The legacy detailed diagram is preserved at
[`docs/archive/architecture/ARCHITECTURE_DIAGRAM.md`](../archive/architecture/ARCHITECTURE_DIAGRAM.md).
The current request path is documented in
[`CURRENT.md`](CURRENT.md) and [`EXECUTION_MAP.md`](EXECUTION_MAP.md):

```text
API / Streamlit / CLI
  -> LocalHarnessRuntime
  -> TurnContext
  -> run_turn()
  -> TurnResult and lifecycle events
```
