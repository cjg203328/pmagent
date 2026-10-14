"""Validate workflow expressions against GitHub Actions' context rules.

GitHub reports an invalid workflow as a run with **zero jobs** and no useful
message, so a mistake here looks like "CI failed" with nothing to debug. The
specific trap this guards against: `secrets` is not an allowed context for
step-level `if`, so referencing it there silently invalidates the entire
workflow rather than failing one step.

Allowed contexts are taken from the official availability table:
https://docs.github.com/en/actions/reference/workflows-and-actions/contexts

Usage:
    python scripts/validate_workflows.py
"""

from __future__ import annotations

from pathlib import Path
import re
import sys

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

#: Contexts permitted per workflow key, per the availability table. Only the
#: keys this repository uses are listed; extend as needed.
ALLOWED_CONTEXTS: dict[str, frozenset[str]] = {
    "on.workflow_call.inputs.<inputs_id>.default": frozenset({"github", "inputs", "vars"}),
    "env": frozenset({"github", "secrets", "inputs", "vars"}),
    "jobs.<job_id>.if": frozenset({"github", "needs", "vars", "inputs"}),
    "jobs.<job_id>.env": frozenset({"github", "needs", "strategy", "matrix", "vars", "secrets", "inputs"}),
    "jobs.<job_id>.runs-on": frozenset({"github", "needs", "strategy", "matrix", "vars", "inputs"}),
    "jobs.<job_id>.steps.if": frozenset(
        {"github", "needs", "strategy", "matrix", "job", "runner", "env", "vars", "steps", "inputs"}
    ),
    "jobs.<job_id>.steps.env": frozenset(
        {"github", "needs", "strategy", "matrix", "job", "runner", "env", "vars", "steps", "secrets", "inputs"}
    ),
    "jobs.<job_id>.steps.name": frozenset(
        {"github", "needs", "strategy", "matrix", "job", "runner", "env", "vars", "steps", "secrets", "inputs"}
    ),
    "jobs.<job_id>.steps.run": frozenset(
        {"github", "needs", "strategy", "matrix", "job", "runner", "env", "vars", "steps", "secrets", "inputs"}
    ),
    "jobs.<job_id>.steps.with": frozenset(
        {"github", "needs", "strategy", "matrix", "job", "runner", "env", "vars", "steps", "secrets", "inputs"}
    ),
    "jobs.<job_id>.steps.continue-on-error": frozenset(
        {"github", "needs", "strategy", "matrix", "job", "runner", "env", "vars", "steps", "secrets", "inputs"}
    ),
}

#: Every context name that can appear as `name.` or `name[` in an expression.
KNOWN_CONTEXTS = (
    "github", "env", "vars", "job", "jobs", "steps", "runner", "secrets",
    "strategy", "matrix", "needs", "inputs",
)

_EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)
_CONTEXT_REF = re.compile(r"\b(" + "|".join(KNOWN_CONTEXTS) + r")\s*(?:\.|\[)")


#: Keys whose value is an expression even without a `${{ }}` wrapper. GitHub
#: allows `if:` and `continue-on-error:` to be written bare, which is the common
#: style — and the reason a `${{ }}`-only scan misses exactly these keys.
_BARE_EXPRESSION_KEYS = frozenset({"if", "continue-on-error"})


def _contexts_used(value: object, *, bare: bool = False) -> set[str]:
    """Return every context name referenced in a string.

    Args:
        value: The YAML scalar to scan.
        bare: Treat the whole string as one expression, for keys that do not
            require a `${{ }}` wrapper.
    """
    if not isinstance(value, str):
        return set()
    used: set[str] = set()
    if bare and "${{" not in value:
        return set(_CONTEXT_REF.findall(value))
    for match in _EXPRESSION.finditer(value):
        used.update(_CONTEXT_REF.findall(match.group(1)))
    return used


def _check(key: str, value: object, where: str) -> list[str]:
    allowed = ALLOWED_CONTEXTS.get(key)
    if allowed is None:
        return []
    bare = key.rsplit(".", 1)[-1] in _BARE_EXPRESSION_KEYS
    offending = sorted(_contexts_used(value, bare=bare) - allowed)
    if not offending:
        return []
    return [
        f"{where}: `{key}` uses disallowed context(s) {offending}; "
        f"allowed here: {sorted(allowed)}"
    ]


def validate_workflow(path: Path) -> list[str]:
    problems: list[str] = []
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return [f"{path.name}: top level is not a mapping"]

    problems += _check("env", data.get("env"), path.name)

    jobs = data.get("jobs") or {}
    if not isinstance(jobs, dict):
        return problems + [f"{path.name}: `jobs` is not a mapping"]

    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            continue
        problems += _check("jobs.<job_id>.if", job.get("if"), f"{path.name}:{job_id}")
        problems += _check("jobs.<job_id>.env", job.get("env"), f"{path.name}:{job_id}")
        problems += _check("jobs.<job_id>.runs-on", job.get("runs-on"), f"{path.name}:{job_id}")

        for index, step in enumerate(job.get("steps") or [], start=1):
            if not isinstance(step, dict):
                continue
            label = f"{path.name}:{job_id}:step{index}({step.get('name', '?')})"
            for key in (
                "jobs.<job_id>.steps.if",
                "jobs.<job_id>.steps.env",
                "jobs.<job_id>.steps.name",
                "jobs.<job_id>.steps.run",
                "jobs.<job_id>.steps.with",
                "jobs.<job_id>.steps.continue-on-error",
            ):
                problems += _check(key, step.get(key.split(".")[-1]), label)
    return problems


def main() -> int:
    if not WORKFLOW_DIR.is_dir():
        print(f"No workflow directory at {WORKFLOW_DIR}", file=sys.stderr)
        return 1

    problems: list[str] = []
    files = sorted(WORKFLOW_DIR.glob("*.y*ml"))
    for path in files:
        problems += validate_workflow(path)

    if problems:
        for problem in problems:
            print(f"INVALID {problem}", file=sys.stderr)
        print(
            f"\n{len(problems)} workflow context violation(s). GitHub rejects the "
            "whole workflow for these, reporting only a run with zero jobs.",
            file=sys.stderr,
        )
        return 1

    print(f"Workflow context validation passed ({len(files)} file(s) checked).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
