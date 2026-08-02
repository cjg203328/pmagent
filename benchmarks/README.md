# Core performance benchmarks

This suite measures core offline paths. It never calls an LLM, an MCP server,
or the network. Workload setup and warmup are excluded from measurements.

## Run and report

Run the baseline and print median latency, P95 latency, throughput, and status:

```powershell
python -m benchmarks.core_performance
```

Write a machine-readable report and enforce the regression thresholds:

```powershell
python -m benchmarks.core_performance `
  --samples 30 `
  --warmups 5 `
  --output artifacts/core-performance.json `
  --enforce
```

Run only one or more workloads:

```powershell
python -m benchmarks.core_performance `
  --only memory_retrieval `
  --only task_graph
```

Run the explicit pytest performance gate. The directory is outside the normal
`testpaths`, so this does not add benchmark noise to the regular unit suite:

```powershell
python -m pytest benchmarks/test_core_performance.py -q -s --no-cov
```

## Thresholds

Thresholds are P95 limits per logical operation:

| Workload | P95 limit | Scope |
| --- | ---: | --- |
| `memory_retrieval` | 5 ms | Merge, confidence filter, deduplicate, and context budget |
| `routing_cache` | 0.10 ms | Cached deterministic intent lookup |
| `sqlite_roundtrip` | 100 ms | One committed insert followed by an indexed read |
| `task_graph` | 500 ms | Four local tasks with dependencies and bounded concurrency |

These are regression guards, not claims about production request latency. They
are intentionally above normal developer-machine results so scheduling and
filesystem jitter do not create false failures. Compare JSON reports from the
same operating system and runner class when tracking smaller changes.
