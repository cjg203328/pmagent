# MinerU multimodal conversion

ArtPM Agent integrates MinerU through a small adapter in
`artpm_agent/utils/mineru_adapter.py`. The adapter owns a stable
`artpm-mineru-v1` result contract and keeps the heavyweight MinerU runtime out
of the Streamlit process. It can call either a local `mineru` CLI or a
`mineru-api` HTTP sidecar. When neither is available, the existing PDF/Office
parsers and Unlimited-OCR path remain active.

## Install the optional backend

MinerU is intentionally not a core dependency. Install it in a deployment
environment that matches the selected backend, or run the official API in a
separate process/container. For a CPU-oriented local installation, follow the
official instructions and select the pipeline backend:

```powershell
python -m pip install -e ".[mineru]"
```

For a sidecar deployment, start the official service and point ArtPM at it:

```powershell
mineru-api --host 127.0.0.1 --port 8000
```

```env
MINERU_MODE=remote
MINERU_API_URL=http://127.0.0.1:8000
MINERU_BACKEND=pipeline
```

The current official package is version 3.4.4 and requires Python 3.10 to
3.13. On Windows, use a supported Python 3.10-3.12 environment for the
MinerU runtime; the ArtPM app itself does not need to change interpreters.

For a cloud-first ArtPM deployment, install only the lightweight official
client extra in the application environment and keep parsing in a remote
`mineru-api` service:

```powershell
python -m pip install -e ".[mineru-client]"
```

```env
MINERU_MODE=remote
MINERU_API_URL=https://mineru.example.com/api/v1
MINERU_API_KEY=your-service-token
MINERU_API_KEY_HEADER=Authorization
MINERU_API_KEY_PREFIX=Bearer
MINERU_API_PATH=/file_parse
MINERU_HEALTH_PATH=/health
```

The adapter rejects non-HTTPS cloud endpoints by default, preserves the
connection through one `requests.Session`, bounds response/archive sizes, and
polls the official asynchronous task contract when the service returns a task
ID. Set `MINERU_ALLOW_INSECURE_HTTP=true` only for a trusted local network or
loopback service.

## Result and fallback behavior

For each supported attachment, the adapter requests Markdown plus the stable
legacy `content_list.json` and optional `middle.json`. It also understands the
new `content_list_v2.json` format, but bounds and records it as optional because
the upstream documentation marks that schema as development/subject to change.
Absolute image paths and unsafe archive members are removed or rejected before
the result reaches the model or the knowledge store. A bounded summary is
stored under `extracted_data.mineru`; the Markdown is used for the current
turn and vector indexing. The current-turn context remains capped by the
existing 6,000/12,000-character attachment budgets, while approved MinerU
resources retain up to 128 KiB of searchable text and 384 KiB of structured
metadata per file so a multi-file proposal stays below the knowledge-store
payload limit.

The adapter is fail-open for availability but fail-closed for unsafe output:

- missing CLI/API, timeout, non-zero exit, invalid JSON, and incomplete output
  return control to the existing local parser;
- path traversal, symbolic links, oversized archives, and output overflows are
  rejected and never indexed;
- the cache key includes the input SHA-256, backend/options, integration schema,
  and detected MinerU version, so an upgraded parser creates a new result.

## Official attribution and license

This product uses MinerU when the optional backend is enabled. MinerU is
licensed under Apache License 2.0 with additional terms, including a prominent
online-service attribution requirement and commercial thresholds. Keep the
MinerU attribution visible in deployment documentation/UI and review the
upstream license before offering a hosted service.

Official references:

- [MinerU repository](https://github.com/opendatalab/MinerU)
- [MinerU license](https://github.com/opendatalab/MinerU/blob/master/LICENSE.md)
- [Quick usage and REST endpoints](https://opendatalab.github.io/MinerU/usage/quick_usage/)
- [CLI options](https://opendatalab.github.io/MinerU/usage/cli_tools/)
- [Output file formats](https://opendatalab.github.io/MinerU/reference/output_files/)
- [MinerU on PyPI](https://pypi.org/project/mineru/)
