# Unlimited-OCR sidecar integration

ArtPM treats [baidu/Unlimited-OCR](https://github.com/baidu/Unlimited-OCR) as a
hidden, read-only Agent skill. The application does not load the OCR model in the
Streamlit process. The first image or scanned PDF triggers a bounded health probe
and, when present, the deployment-owned runtime package. An unavailable runtime
falls back to the configured multimodal chat model.

## Runtime boundary

The upstream project tests its Transformers path with Python 3.12.3 and CUDA
12.9. Its published SGLang command uses a pinned custom wheel, the `fa3`
attention backend, and `--enable-custom-logit-processor`. That is not equivalent
to this project's current native Windows/Python 3.13 runtime. Run the official
service in a compatible Linux/WSL/GPU environment, on another trusted machine,
or behind a compatible internal proxy. This is deployment infrastructure, not an
end-user setting. A release bundle provides `runtime.json`, its isolated Python
environment, native libraries, and model weights below
`artpm_agent/runtime/unlimited_ocr/`. ArtPM disables network model downloads and
never invokes `pip` from a chat request.

The current workstation has an RTX 4070 Ti SUPER (Ada, 16 GB). Do not assume the
upstream Hopper-oriented `fa3` command will start unchanged on this GPU. Follow
the upstream SGLang or vLLM recipe for the actual inference host and validate it
there before connecting it to the ArtPM runtime.

## Deployment configuration

These environment values are managed by the service operator. They are not
shown or changed in the ArtPM settings page:

| Key | Default | Purpose |
| --- | --- | --- |
| `UNLIMITED_OCR_ENABLED` | `true` | Allows automatic OCR skill discovery |
| `UNLIMITED_OCR_BASE_URL` | `http://127.0.0.1:10000` | Trusted sidecar/proxy root |
| `UNLIMITED_OCR_MODEL` | `Unlimited-OCR` | Served model ID |
| `UNLIMITED_OCR_TIMEOUT` | `120` | Per-request timeout in seconds |
| `UNLIMITED_OCR_MAX_OUTPUT` | `32768` | Maximum returned text length |
| `UNLIMITED_OCR_ALLOW_REMOTE` | `false` | Allows public image URLs after SSRF checks |
| `UNLIMITED_OCR_API_KEY` | empty | Optional bearer token |

Skill dispatch uses a fast cached `GET /health` probe and only calls OCR when the
sidecar is ready. Startup uses an atomic cross-process lock, and a failed process
started by ArtPM is restarted on demand within a bounded retry budget. ArtPM never
kills an unowned process that happens to use the configured port. Full diagnostics
can additionally inspect `GET /v1/models`.
Recognition uses `POST /v1/chat/completions`, inline data URLs,
`images_config`, `skip_special_tokens=false`, and the upstream n-gram parameters.
Single images use `gundam` with window 128; multi-page requests use `base` with
window 1024.

The upstream SGLang custom logit processor is a serialized Python object loaded
with `dill` by the inference server. Never expose a server launched with
`--enable-custom-logit-processor` directly to an untrusted network. Prefer a
sidecar or proxy that fixes this processor server-side and only accepts the
bounded OCR request schema from ArtPM. ArtPM does not accept arbitrary processor
payloads from chat messages or the settings UI.

Direct SGLang parity additionally requires the operator-generated
`DeepseekOCRNoRepeatNGramLogitProcessor.to_str()` value in the trusted
`UNLIMITED_OCR_CUSTOM_LOGIT_PROCESSOR` environment variable. ArtPM validates the
JSON/hex envelope and size but never deserializes it. When this value is empty,
the connector remains usable but does not apply the upstream 35-gram repetition
processor; a trusted proxy that injects the fixed processor is the preferred
deployment.

## Behavior

- Text/table/document extraction uses OCR text as attachment evidence.
- Users attach files and ask their question normally; no OCR provider selection
  or connection setup appears in the conversation UI.
- Color, composition, style, and reference-image questions still use the visual
  chat model; OCR is supplementary context.
- Scanned PDF pages are detected page by page; native text is preserved and only
  blank pages are rendered at 300 DPI for OCR. At most 16 blank pages are sent in
  one request, and truncation is recorded in metadata.
- If OCR is unavailable, the bounded blank-page renders are passed to the
  multimodal model for the same conversation; temporary renders are removed
  immediately after generation.
- OCR failure never blocks normal application startup. Images fall back to the
  visual chat path.
- Knowledge ingestion rejects images with no searchable OCR text instead of
  storing only width/height metadata.
- The active `DocumentClassifierParser` and the legacy `ocr_image` tool both
  recognize the configured sidecar. PaddleOCR remains only as an explicit
  compatibility fallback when no Unlimited-OCR service is configured.

Upstream references: [README](https://github.com/baidu/Unlimited-OCR/blob/main/README.md),
[`infer.py`](https://github.com/baidu/Unlimited-OCR/blob/main/infer.py), and the
[model card](https://huggingface.co/baidu/Unlimited-OCR).
