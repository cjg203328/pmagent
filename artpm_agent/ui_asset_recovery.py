"""Small client-side guard for stale Streamlit frontend chunks.

Streamlit serves hashed JavaScript chunks as immutable assets. A browser tab
that survives a server/package restart can keep an old ``index.*.js`` and ask
for a chunk that no longer exists. Streamlit's fallback route returns the app
HTML for that missing path, which surfaces as raw markup and a dynamic-import
error. This guard reloads once when it detects that split-brain state.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any, Optional


_ENTRYPOINT_RE = re.compile(r"static/js/(index\.[A-Za-z0-9_-]+\.js)")
_CRITICAL_COMPONENT_PREFIXES = (
    "ChatInput.",
    "Slider.",
    "DataFrame.",
)


def _default_static_root() -> Optional[Path]:
    try:
        import streamlit

        return Path(streamlit.__file__).resolve().parent / "static"
    except Exception:  # pragma: no cover - only broken installations
        return None


def find_frontend_entrypoint(static_index: Optional[Path] = None) -> str:
    """Return the installed Streamlit index chunk name, or an empty string."""
    if static_index is None:
        static_root = _default_static_root()
        if static_root is None:
            return ""
        static_index = static_root / "index.html"
    try:
        html = static_index.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ""
    match = _ENTRYPOINT_RE.search(html)
    return match.group(1) if match else ""


def inspect_frontend_bundle(static_root: Optional[Path] = None) -> dict[str, Any]:
    """Verify the installed Streamlit manifest and every declared JS asset."""
    root = Path(static_root) if static_root is not None else _default_static_root()
    if root is None:
        return {"status": "error", "message": "Streamlit static root not found"}

    entrypoint = find_frontend_entrypoint(root / "index.html")
    try:
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return {"status": "error", "message": f"Invalid Streamlit manifest: {error}"}
    if not isinstance(manifest, dict):
        return {"status": "error", "message": "Invalid Streamlit manifest object"}

    javascript_assets = sorted(
        {
            item["file"]
            for item in manifest.values()
            if isinstance(item, dict)
            and isinstance(item.get("file"), str)
            and item["file"].endswith(".js")
        }
    )
    critical_components = [
        asset
        for asset in javascript_assets
        if Path(asset).name.startswith(_CRITICAL_COMPONENT_PREFIXES)
    ]
    missing_components = [
        prefix
        for prefix in _CRITICAL_COMPONENT_PREFIXES
        if not any(Path(asset).name.startswith(prefix) for asset in javascript_assets)
    ]
    missing = [asset for asset in javascript_assets if not (root / asset).is_file()]
    if not entrypoint or missing_components or missing:
        return {
            "status": "error",
            "entrypoint": entrypoint,
            "javascript_assets": len(javascript_assets),
            "missing": missing,
            "missing_components": missing_components,
            "message": "Streamlit frontend bundle is incomplete",
        }
    return {
        "status": "ok",
        "entrypoint": entrypoint,
        "javascript_assets": len(javascript_assets),
        "critical_assets": [f"static/js/{entrypoint}", *critical_components],
        "missing": [],
    }


def build_recovery_script(expected_entrypoint: str) -> str:
    """Build an idempotent script that recovers from stale frontend chunks."""
    expected = json.dumps(str(expected_entrypoint or ""))
    return f"""<script>
(function () {{
  const expected = {expected};
  const parentWindow = window.parent;
  const guardKey = "__artpmFrontendRecoveryGuard";
  const guard = parentWindow[guardKey] || {{}};
  const showNotice = function (message, persistent) {{
    try {{
      const doc = parentWindow.document;
      const id = "artpm-frontend-recovery-notice";
      let notice = doc.getElementById(id);
      if (!notice) {{
        notice = doc.createElement("div");
        notice.id = id;
        notice.setAttribute("role", "alert");
        notice.setAttribute("aria-live", "assertive");
        notice.style.cssText = [
          "position:fixed", "right:20px", "top:16px", "z-index:2147483647",
          "max-width:360px", "padding:12px 14px", "border:1px solid #f1c4c0",
          "border-left:3px solid #dc4a3d", "border-radius:8px",
          "background:#fff8f7", "color:#202124", "font:14px/1.5 -apple-system,BlinkMacSystemFont,Segoe UI,sans-serif",
          "box-shadow:0 6px 20px rgba(32,33,36,.10)"
        ].join(";");
        doc.body.appendChild(notice);
      }}
      notice.textContent = message;
      if (persistent) {{
        const refresh = doc.createElement("button");
        refresh.type = "button";
        refresh.textContent = "刷新页面";
        refresh.style.cssText = "display:block;margin-top:8px;border:0;background:transparent;color:#1d4ed8;font-weight:600;cursor:pointer;padding:0";
        refresh.onclick = function () {{ parentWindow.location.reload(); }};
        notice.appendChild(refresh);
      }}
    }} catch (_) {{
      // A restricted component frame must not block recovery.
    }}
  }};
  const recover = function (reason) {{
    const now = Date.now();
    try {{
      const key = "artpm.frontend.recovery";
      const previous = JSON.parse(parentWindow.sessionStorage.getItem(key) || "null");
      if (previous && previous.expected === expected && now - previous.timestamp < 30000) {{
        showNotice("页面资源加载失败，请点击刷新页面后重试。", true);
        return;
      }}
      parentWindow.sessionStorage.setItem(
        key,
        JSON.stringify({{ expected: expected, timestamp: now, reason: String(reason || "") }})
      );
    }} catch (_) {{
      // A restricted component frame must still attempt the reload.
    }}
    showNotice("页面资源已更新，正在重新连接…", false);
    const target = new URL(parentWindow.location.href);
    target.searchParams.set("_artpm_frontend_recovery", String(now));
    parentWindow.location.replace(target.toString());
  }};
  guard.expected = expected;
  guard.recover = recover;

  if (!guard.installed) {{
    guard.installed = true;
    const isChunkFailure = function (value) {{
      return /Failed to fetch dynamically imported module|Importing a module script failed|Loading chunk|ChunkLoadError/i.test(String(value || ""));
    }};
    parentWindow.addEventListener("error", function (event) {{
      const value = String(event && event.message || "") + " " + String(event && event.filename || "");
      const source = event && event.target && event.target.src;
      if (isChunkFailure(value) || (source && /\\/static\\/js\\//.test(source))) {{
        guard.recover(value || source);
      }}
    }}, true);
    parentWindow.addEventListener("unhandledrejection", function (event) {{
      const reason = event && event.reason;
      const value = reason && reason.message || reason;
      if (isChunkFailure(value)) {{
        guard.recover(value);
      }}
    }});
  }}
  parentWindow[guardKey] = guard;
  try {{
    parentWindow.document.documentElement.dataset.artpmFrontendGuard = expected || "installed";
  }} catch (_) {{
    // Keep recovery functional even when a legacy iframe restricts DOM access.
  }}

  // Proactively reload before a later component asks for an old split chunk.
  try {{
    const loaded = Array.from(parentWindow.document.querySelectorAll("script[type=module][src]"))
      .map(function (node) {{ return new URL(node.src, parentWindow.location.href).pathname.split("/").pop(); }})
      .find(function (name) {{ return name && name.indexOf("index.") === 0; }});
    if (expected && loaded && loaded !== expected) {{
      guard.recover("frontend-entrypoint-mismatch");
    }} else if (expected && loaded === expected) {{
      const stableUrl = new URL(parentWindow.location.href);
      if (stableUrl.searchParams.has("_artpm_frontend_recovery")) {{
        stableUrl.searchParams.delete("_artpm_frontend_recovery");
        parentWindow.history.replaceState(null, "", stableUrl.toString());
      }}
    }}
  }} catch (_) {{
    // The error listeners above remain useful if DOM access is restricted.
  }}
}})();
</script>"""


def install_frontend_recovery_guard() -> None:
    """Install the hidden recovery script on the current app run."""
    script = build_recovery_script(find_frontend_entrypoint())
    try:
        import streamlit as st

        st.html(
            script,
            width="content",
            unsafe_allow_javascript=True,
        )
        return
    except (AttributeError, TypeError):
        # Compatibility path for older supported Streamlit versions whose
        # st.html API cannot execute JavaScript.
        try:
            import streamlit.components.v1 as components

            components.html(script, height=0, width=0)
        except Exception:  # noqa: BLE001 - recovery must never break the app
            return
    except Exception:  # noqa: BLE001 - recovery must never break the app
        return
