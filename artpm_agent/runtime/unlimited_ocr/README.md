# Managed Unlimited-OCR runtime

This directory is a deployment boundary, not a user configuration surface.

The application looks for `runtime.json` here only after an image or scanned PDF
needs OCR. A release/deployment package must provide that manifest together with
the exact Python runtime, launcher, model weights, and any native libraries named
by `required_paths`. The application does not run `pip`, download weights, or
accept runtime commands from chat or the settings page.

Use `runtime.example.json` as the manifest schema. Windows and Linux deployment
packages should generate platform-specific commands. The process must expose:

- `GET /health`
- `GET /v1/models`
- `POST /v1/chat/completions`

Startup is guarded by an atomic cross-process lock. A failed managed process is
restarted on demand within the configured retry limit. If the package is absent
or unhealthy, image/PDF analysis falls back to the configured multimodal model.
