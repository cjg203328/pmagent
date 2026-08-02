"""Contract tests for the provider-neutral harness runtime boundary."""

from __future__ import annotations

from typing import Any, Mapping

from artpm_agent.harness import (
    BaseHarnessRuntime,
    LegacyAgentRuntimeAdapter,
    RuntimeCapabilities,
    TurnContext,
    run_turn,
)
from artpm_agent.security import PermissionStore
from artpm_agent.tenancy import TenantContext


class ExternalSkillRuntime(BaseHarnessRuntime):
    capabilities = RuntimeCapabilities(
        turn_processing=True,
        skill_routing=True,
        model_chat=True,
        direct_response=False,
    )
    model_available = True

    def __init__(self, *, intent: str | None = "external_lookup") -> None:
        self.intent = intent
        self.calls: list[tuple[str, Any]] = []

    def detect_intent(self, _user_input: str) -> str | None:
        return self.intent

    def skill_names(self) -> set[str]:
        return {"external_lookup"}

    def skill_metadata(self, _skill_name: str) -> Mapping[str, Any]:
        return {"read_only": True, "risk": "low"}

    def build_skill_input(self, user_input: str, _context: Mapping[str, Any]) -> str:
        return user_input

    def extract_skill_inputs(
        self,
        user_input: str,
        _intent: str,
        _context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return {"query": user_input}

    def execute_skill(self, intent: str, inputs: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append((intent, dict(inputs)))
        return {"success": True, "answer": "external result"}

    def format_skill_result(self, _skill_name: str, result: Mapping[str, Any]) -> str:
        return str(result["answer"])

    def build_system_prompt(self, _profile: Any, _knowledge: str) -> str:
        return "system"

    def chat_with_failover(
        self,
        _prompt: str,
        _system_prompt: str,
        _history: list[Mapping[str, Any]],
        *,
        image_paths: list[str],
    ) -> str:
        del image_paths
        return "model result"


def test_run_turn_accepts_external_runtime_without_agent() -> None:
    runtime = ExternalSkillRuntime()
    context = TurnContext(
        turn_id="runtime-1",
        conversation_id="conversation-1",
        user_input="lookup this",
        runtime=runtime,
    )

    result = run_turn(context)

    assert context.agent is None
    assert context.runtime is runtime
    assert result.success is True
    assert result.handled_by == "skill:external_lookup"
    assert runtime.calls == [("external_lookup", {"query": "lookup this"})]


def test_runtime_object_is_accepted_through_legacy_agent_keyword() -> None:
    runtime = ExternalSkillRuntime()
    context = TurnContext(
        turn_id="runtime-legacy-keyword",
        conversation_id="conversation-1",
        user_input="lookup this",
        agent=runtime,
    )

    result = run_turn(context)

    assert context.runtime is runtime
    assert result.handled_by == "skill:external_lookup"


class MissingMetadataWriteRuntime(ExternalSkillRuntime):
    def __init__(self) -> None:
        super().__init__(intent="unknown_writer")

    def skill_names(self) -> set[str]:
        return {"unknown_writer"}

    def skill_metadata(self, _skill_name: str) -> Mapping[str, Any]:
        return {}

    def extract_skill_inputs(
        self,
        _user_input: str,
        _intent: str,
        _context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return {"action": "delete", "target": "record-1"}


def test_missing_skill_metadata_fails_closed_into_admin_approval(tmp_path) -> None:
    runtime = MissingMetadataWriteRuntime()
    permission_store = PermissionStore(tmp_path / "permissions.db")
    context = TurnContext(
        turn_id="missing-metadata",
        conversation_id="conversation-1",
        user_input="delete it",
        runtime=runtime,
        extra={
            "permission_store": permission_store,
            "workspace_id": "workspace-1",
        },
    )

    result = run_turn(context)

    assert result.awaiting_approval is True
    assert result.handled_by == "permission_gate"
    assert runtime.calls == []
    request = permission_store.list_pending(workspace_id="workspace-1")[0]
    assert request.risk == "untrusted"
    assert request.required_role == "admin"


class TenantBoundRuntime(ExternalSkillRuntime):
    def __init__(self, inputs: Mapping[str, Any], *, write: bool = False) -> None:
        super().__init__(intent="external_write" if write else "external_lookup")
        self.inputs = dict(inputs)
        self.write = write

    def skill_names(self) -> set[str]:
        return {self.intent or "external_lookup"}

    def skill_metadata(self, _skill_name: str) -> Mapping[str, Any]:
        return {"read_only": not self.write, "requires_approval": self.write}

    def extract_skill_inputs(
        self,
        _user_input: str,
        _intent: str,
        _context: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        return dict(self.inputs)


def _tenant_context() -> TenantContext:
    return TenantContext(
        tenant_id="tenant-a",
        workspace_id="workspace-a",
        principal_id="principal-a",
    )


def test_tenant_scope_is_bound_before_read_only_skill_execution() -> None:
    runtime = TenantBoundRuntime({"query": "value"})
    context = TurnContext(
        turn_id="tenant-1",
        conversation_id="conversation-1",
        user_input="lookup",
        runtime=runtime,
        extra={"tenant_context": _tenant_context()},
    )

    result = run_turn(context)

    assert result.success is True
    assert runtime.calls == [
        (
            "external_lookup",
            {
                "query": "value",
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
            },
        )
    ]


def test_tenant_scope_conflict_is_terminal_before_permission_or_execution(tmp_path) -> None:
    runtime = TenantBoundRuntime({"workspace_id": "workspace-other"}, write=True)
    store = PermissionStore(tmp_path / "permissions.db")
    context = TurnContext(
        turn_id="tenant-2",
        conversation_id="conversation-1",
        user_input="write",
        runtime=runtime,
        extra={
            "tenant_context": _tenant_context(),
            "permission_store": store,
            "workspace_id": "workspace-a",
        },
    )

    result = run_turn(context)

    assert result.success is False
    assert result.handled_by == "tenant_scope_gate"
    assert runtime.calls == []
    assert store.list_pending() == []


def test_forged_tenant_mapping_is_rejected_by_harness() -> None:
    runtime = TenantBoundRuntime({"query": "value"})
    context = TurnContext(
        turn_id="tenant-forged",
        conversation_id="conversation-1",
        user_input="lookup",
        runtime=runtime,
        extra={
            "tenant_context": {
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
            }
        },
    )

    result = run_turn(context)

    assert result.success is False
    assert result.handled_by == "tenant_scope_gate"
    assert runtime.calls == []


def test_permission_payload_contains_trusted_tenant_scope(tmp_path) -> None:
    runtime = TenantBoundRuntime({"value": 1}, write=True)
    store = PermissionStore(tmp_path / "permissions.db")
    context = TurnContext(
        turn_id="tenant-3",
        conversation_id="conversation-1",
        user_input="write",
        runtime=runtime,
        extra={
            "tenant_context": _tenant_context(),
            "permission_store": store,
            "workspace_id": "workspace-a",
        },
    )

    result = run_turn(context)

    assert result.awaiting_approval is True
    pending = store.list_pending(
        workspace_id="workspace-a",
        conversation_id="conversation-1",
    )
    assert len(pending) == 1
    assert pending[0].payload["inputs"]["tenant_id"] == "tenant-a"
    assert pending[0].payload["inputs"]["workspace_id"] == "workspace-a"
    assert runtime.calls == []


def test_full_access_executes_trusted_write_with_host_approval_fields(tmp_path) -> None:
    runtime = TenantBoundRuntime({"value": 1}, write=True)
    store = PermissionStore(tmp_path / "permissions.db")
    context = TurnContext(
        turn_id="tenant-full-access",
        conversation_id="conversation-1",
        user_input="write",
        runtime=runtime,
        extra={
            "tenant_context": _tenant_context(),
            "permission_store": store,
            "permission_mode": "full_access",
            "workspace_id": "workspace-a",
        },
    )

    result = run_turn(context)

    assert result.success is True
    assert result.awaiting_approval is False
    assert store.list_pending(workspace_id="workspace-a") == []
    executed = runtime.calls[0][1]
    assert executed["approved"] is True
    assert executed["confirmation_token"].startswith("full-access:")
    assert executed["tenant_id"] == "tenant-a"
    assert executed["workspace_id"] == "workspace-a"


def test_full_access_keeps_delete_action_behind_confirmation(tmp_path) -> None:
    runtime = TenantBoundRuntime({"action": "delete", "value": 1}, write=True)
    store = PermissionStore(tmp_path / "permissions.db")
    context = TurnContext(
        turn_id="tenant-full-access-delete",
        conversation_id="conversation-1",
        user_input="delete",
        runtime=runtime,
        extra={
            "tenant_context": _tenant_context(),
            "permission_store": store,
            "permission_mode": "full_access",
            "workspace_id": "workspace-a",
        },
    )

    result = run_turn(context)

    assert result.awaiting_approval is True
    assert runtime.calls == []
    pending = store.list_pending(workspace_id="workspace-a")
    assert len(pending) == 1
    assert pending[0].risk == "high"


def test_legacy_adapter_executes_request_scoped_router_proxy() -> None:
    class BoundRouter:
        def __init__(self, base: "SharedRouter", tenant: TenantContext) -> None:
            self.base = base
            self.tenant = tenant

        @property
        def skills(self) -> dict[str, object]:
            return self.base.skills

        def list_skills(self) -> list[dict[str, Any]]:
            return [{"name": "external_lookup", "read_only": True}]

        def execute_skill(self, name: str, inputs: dict[str, Any]) -> dict[str, Any]:
            self.base.scoped_calls.append((self.tenant, name, dict(inputs)))
            return {"success": True, "answer": "scoped"}

    class SharedRouter:
        def __init__(self) -> None:
            self.skills = {"external_lookup": object()}
            self.bound_contexts: list[TenantContext] = []
            self.scoped_calls: list[tuple[TenantContext, str, dict[str, Any]]] = []

        def for_tenant(self, tenant: TenantContext) -> BoundRouter:
            self.bound_contexts.append(tenant)
            return BoundRouter(self, tenant)

        def execute_skill(self, _name: str, _inputs: dict[str, Any]) -> dict[str, Any]:
            raise AssertionError("shared router execution must not be used")

    class Legacy:
        def __init__(self) -> None:
            self.router = SharedRouter()
            self.llm_client = object()

        def _detect_intent(self, _prompt: str) -> str:
            return "external_lookup"

        def _skill_input_with_history(self, prompt: str, _context: dict) -> str:
            return prompt

        def _extract_inputs(self, _prompt: str, _intent: str, _context: dict) -> dict:
            return {"value": 1}

        def _format_skill_result(self, _name: str, result: dict) -> str:
            return str(result["answer"])

        def _build_system_prompt(self, _profile: Any, _knowledge: str) -> str:
            return "system"

        def _needs_visual_semantics(self, _prompt: str) -> bool:
            return False

        def _vision_attachment_paths(self, _files: list, _visual: bool):
            from contextlib import nullcontext

            return nullcontext([])

        def _chat_with_model_failover(self, *_args: Any, **_kwargs: Any) -> str:
            return "model"

    legacy = Legacy()
    tenant = _tenant_context()
    context = TurnContext(
        turn_id="tenant-proxy",
        conversation_id="conversation-1",
        user_input="lookup",
        agent=legacy,
        extra={"tenant_context": tenant},
    )

    result = run_turn(context)

    assert result.response == "scoped"
    assert legacy.router.bound_contexts == [tenant]
    assert legacy.router.scoped_calls == [
        (
            tenant,
            "external_lookup",
            {
                "value": 1,
                "tenant_id": "tenant-a",
                "workspace_id": "workspace-a",
            },
        )
    ]


def test_unsupported_runtime_fails_closed() -> None:
    runtime = BaseHarnessRuntime()
    context = TurnContext(
        turn_id="runtime-2",
        conversation_id="conversation-1",
        user_input="do something",
        runtime=runtime,
    )

    result = run_turn(context)

    assert result.success is False
    assert result.handled_by == "harness_runtime_error"


def test_malformed_runtime_returns_error_instead_of_raising() -> None:
    context = TurnContext(
        turn_id="runtime-invalid",
        conversation_id="conversation-1",
        user_input="do something",
        runtime=object(),
    )

    result = run_turn(context)

    assert result.success is False
    assert result.handled_by == "harness_runtime_error"
    assert "capabilities" in str(result.error)


def test_legacy_adapter_preserves_thin_chat_compatibility() -> None:
    class Legacy:
        def chat(self, user_input: str, context: Mapping[str, Any] | None = None) -> str:
            assert context is not None and context.get("source") == "test"
            return f"echo:{user_input}"

    adapter = LegacyAgentRuntimeAdapter(Legacy())
    context = TurnContext(
        turn_id="runtime-3",
        conversation_id="conversation-1",
        user_input="hello",
        runtime=adapter,
        extra={"source": "test"},
    )

    result = run_turn(context)

    assert result.success is True
    assert result.response == "echo:hello"
    assert result.handled_by == "thin_agent_chat"


def test_document_handler_uses_runtime_public_method() -> None:
    class Store:
        def __init__(self) -> None:
            self.resources = None

        def propose_ingestion(self, *_args: Any) -> None:
            self.resources = _args

    class DocumentRuntime(BaseHarnessRuntime):
        capabilities = RuntimeCapabilities(document_parsing=True)

        def process_document(self, _path: str, _hint: str = "") -> Mapping[str, Any]:
            return {"success": True, "raw_text": "searchable text"}

    store = Store()
    context = TurnContext(
        turn_id="runtime-4",
        conversation_id="conversation-1",
        user_input="把这个文件加入知识库",
        attachments=[{"name": "notes.txt", "extension": "txt"}],
        runtime=DocumentRuntime(),
        extra={"file_paths": ["notes.txt"]},
    )

    result = run_turn(
        context,
        knowledge_store=store,
        request_conversation_id="conversation-1",
    )

    assert result.success is True
    assert result.awaiting_approval is True
    assert store.resources is not None
