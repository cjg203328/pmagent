"""Workflow context-validation contract.

GitHub rejects an entire workflow when a context is used where it is not
allowed, and reports that only as a run with **zero jobs** and no message. That
failure mode cost real debugging time: a `secrets` reference in a step-level
`if:` produced a run that failed in under a second with no jobs to inspect.

These tests pin the rule that caused it, plus the validator's own accuracy.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.validate_workflows import (
    ALLOWED_CONTEXTS,
    _contexts_used,
    validate_workflow,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"


def test_secrets_is_not_allowed_in_a_step_level_if():
    """The rule that broke CI. Asserted explicitly so it cannot be relaxed."""
    assert "secrets" not in ALLOWED_CONTEXTS["jobs.<job_id>.steps.if"]
    assert "secrets" in ALLOWED_CONTEXTS["jobs.<job_id>.steps.env"]


def test_bare_if_expression_is_scanned_without_the_dollar_wrapper():
    """`if:` may omit `${{ }}`, so a wrapper-only scan misses it entirely."""
    bare = "github.event_name == 'push' && secrets.DOCKER_USERNAME != ''"

    assert _contexts_used(bare, bare=True) == {"github", "secrets"}
    # Without the flag this is the blind spot that let the bug through.
    assert _contexts_used(bare, bare=False) == set()


def test_wrapped_expression_is_scanned_normally():
    value = "${{ secrets.DOCKER_PASSWORD }}"

    assert _contexts_used(value) == {"secrets"}


def test_index_syntax_is_recognised():
    assert _contexts_used("${{ github['sha'] }}") == {"github"}
    assert _contexts_used("${{ secrets[format('X')] }}") == {"secrets"}


def test_plain_text_is_not_mistaken_for_a_context():
    # "env" appears as a word but never as a context reference.
    assert _contexts_used("set the env var here") == set()


def test_validator_rejects_secrets_in_a_step_if(tmp_path):
    workflow = {
        "name": "t",
        "on": "push",
        "jobs": {
            "j": {
                "runs-on": "ubuntu-latest",
                "steps": [
                    {"name": "s", "if": "secrets.A != ''", "run": "echo hi"},
                ],
            }
        },
    }
    path = tmp_path / "bad.yml"
    path.write_text(yaml.safe_dump(workflow), encoding="utf-8")

    problems = validate_workflow(path)

    assert len(problems) == 1
    assert "secrets" in problems[0]
    assert "steps.if" in problems[0]


def test_validator_accepts_secrets_mapped_into_step_env(tmp_path):
    """The supported pattern: map secrets via `env`, branch in the shell."""
    workflow = {
        "name": "t",
        "on": "push",
        "jobs": {
            "j": {
                "runs-on": "ubuntu-latest",
                "steps": [
                    {
                        "name": "s",
                        "if": "github.event_name == 'push'",
                        "env": {
                            "USERNAME": "${{ secrets.DOCKER_USERNAME }}",
                            "PASSWORD": "${{ secrets.DOCKER_PASSWORD }}",
                        },
                        "run": "test -n \"$USERNAME\" || exit 0",
                    },
                ],
            }
        },
    }
    path = tmp_path / "good.yml"
    path.write_text(yaml.safe_dump(workflow), encoding="utf-8")

    assert validate_workflow(path) == []


def test_validator_accepts_a_job_level_if_using_needs(tmp_path):
    workflow = {
        "name": "t",
        "on": "push",
        "jobs": {
            "j": {
                "runs-on": "ubuntu-latest",
                "if": "needs.a.result == 'success'",
                "steps": [{"name": "s", "run": "echo hi"}],
            }
        },
    }
    path = tmp_path / "jobif.yml"
    path.write_text(yaml.safe_dump(workflow), encoding="utf-8")

    assert validate_workflow(path) == []


def test_every_committed_workflow_is_valid():
    """Guard the real files, not just synthetic ones."""
    files = sorted(WORKFLOW_DIR.glob("*.y*ml"))
    assert files, "no workflow files found to validate"

    problems = [p for path in files for p in validate_workflow(path)]
    assert problems == [], "\n".join(problems)


def test_the_publish_step_no_longer_gates_on_secrets():
    """Regression guard on the actual file that broke."""
    data = yaml.safe_load((WORKFLOW_DIR / "ci.yml").read_text(encoding="utf-8"))
    build_steps = data["jobs"]["build"]["steps"]
    publish = [s for s in build_steps if s.get("name") == "Push to registry"]

    assert publish, "the publish step disappeared"
    step = publish[0]
    assert "secrets" not in str(step.get("if", ""))
    # Credentials must still reach the script, just via env rather than `if`.
    assert "DOCKER_USERNAME" in (step.get("env") or {})
