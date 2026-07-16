import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = PROJECT_ROOT / "artpm_agent"

from artpm_agent.memory.workspace_knowledge_store import WorkspaceKnowledgeStore
from artpm_agent.memory import KnowledgeProposalConflictError
from artpm_agent.memory.conversation_store import ConversationStore


def test_resource_source_metadata_and_versions_are_persistent(tmp_path):
    store = WorkspaceKnowledgeStore(tmp_path / "knowledge.db")

    first = store.ingest_resource(
        title="腾讯角色报价",
        searchable_text="第一版报价 10 万元",
        resource_type="spreadsheet",
        source_type="upload",
        source_uri="uploads/quote.xlsx",
        source_id="sha256:quote",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        structured_data={"total_amount": 100000},
        metadata={"client": "腾讯"},
        version_metadata={"sheet": "报价"},
        created_by="user-1",
    )

    assert first["version_created"] is True
    assert first["current_version"] == 1
    assert first["source"] == {
        "type": "upload",
        "uri": "uploads/quote.xlsx",
        "id": "sha256:quote",
    }
    assert first["metadata"] == {"client": "腾讯"}
    assert first["version"]["structured_data"] == {"total_amount": 100000}

    duplicate = store.ingest_resource(
        title="腾讯角色报价",
        searchable_text="第一版报价 10 万元",
        resource_type="spreadsheet",
        source_type="upload",
        source_uri="uploads/quote.xlsx",
        source_id="sha256:quote",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        structured_data={"total_amount": 100000},
        metadata={"project": "角色制作"},
    )
    assert duplicate["id"] == first["id"]
    assert duplicate["version_created"] is False
    assert duplicate["current_version"] == 1
    assert duplicate["metadata"] == {
        "client": "腾讯",
        "project": "角色制作",
    }

    second = store.ingest_resource(
        title="腾讯角色报价（修订）",
        searchable_text="第二版报价 12 万元",
        resource_type="spreadsheet",
        source_type="upload",
        source_uri="uploads/quote-v2.xlsx",
        source_id="sha256:quote",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        structured_data={"total_amount": 120000},
        change_note="客户增加两个角色",
    )

    assert second["id"] == first["id"]
    assert second["current_version"] == 2
    first_version = store.get_resource(first["id"], version=1)["version"]
    assert first_version["searchable_text"] == "第一版报价 10 万元"
    assert first_version["source"] == {
        "uri": "uploads/quote.xlsx",
        "id": "sha256:quote",
    }
    assert second["version"]["source"]["uri"] == "uploads/quote-v2.xlsx"
    assert [item["version"] for item in store.list_versions(first["id"])] == [2, 1]

    reopened = WorkspaceKnowledgeStore(tmp_path / "knowledge.db")
    assert reopened.get_resource(first["id"])["current_version"] == 2


def test_search_uses_current_version_and_isolates_workspaces(tmp_path):
    store = WorkspaceKnowledgeStore(tmp_path / "knowledge.db")
    first = store.ingest_resource(
        title="项目规则",
        searchable_text="旧预算规则：原画 3 万",
        workspace_id="studio-a",
        resource_type="note",
        source_type="manual",
        source_id="budget-rule",
    )
    store.ingest_resource(
        title="项目规则",
        searchable_text="新预算规则：原画 5 万",
        workspace_id="studio-a",
        resource_type="note",
        source_type="manual",
        source_id="budget-rule",
    )
    store.ingest_resource(
        title="另一个工作区",
        searchable_text="新预算规则：原画 9 万",
        workspace_id="studio-b",
        resource_type="note",
        source_type="integration",
        source_id="budget-rule",
    )

    semantic_results = store.search("旧预算规则", workspace_id="studio-a")
    assert [result["id"] for result in semantic_results] == [first["id"]]
    assert semantic_results[0]["current_version"] == 2
    assert semantic_results[0]["retrieval_mode"] == "vector"
    assert "旧预算规则" not in semantic_results[0]["version"]["searchable_text"]
    results = store.search(
        "新预算规则",
        workspace_id="studio-a",
        resource_types=["note"],
        source_types=["manual"],
    )
    assert [result["id"] for result in results] == [first["id"]]
    assert results[0]["current_version"] == 2
    assert "5 万" in results[0]["text"]
    assert "9 万" not in results[0]["text"]


def test_image_ocr_and_table_payloads_are_generic_searchable_resources(tmp_path):
    store = WorkspaceKnowledgeStore(tmp_path / "knowledge.db")
    image = store.ingest_resource(
        title="客户反馈截图",
        searchable_text="主角面部需要更年轻，调整发型轮廓",
        resource_type="image",
        source_type="upload",
        source_id="image-feedback-1",
        mime_type="image/png",
        structured_data={
            "ocr": {
                "confidence": 0.93,
                "boxes": [[[0, 0], [20, 0], [20, 10], [0, 10]]],
            }
        },
    )
    table = store.ingest_resource(
        title="资产清单",
        resource_type="table",
        source_type="upload",
        source_id="asset-table-1",
        mime_type="text/csv",
        structured_data={"rows": [{"资产": "主角贴图", "数量": 4}]},
    )

    assert store.search("发型轮廓")[0]["id"] == image["id"]
    assert store.search("主角贴图")[0]["id"] == table["id"]
    assert store.get_resource(image["id"])["version"]["structured_data"]["ocr"][
        "confidence"
    ] == 0.93


def test_agent_rule_is_not_active_or_searchable_until_user_confirms(tmp_path):
    store = WorkspaceKnowledgeStore(tmp_path / "knowledge.db")
    proposed = store.propose_rule(
        "报价净利润率低于 25% 时标记为高风险",
        workspace_id="studio-a",
        scope="quote_review",
        proposed_by="agent",
        source_conversation_id="conversation-1",
        source_message_id="message-8",
        metadata={"reason": "用户在会话中表达了偏好"},
    )

    assert proposed["status"] == "proposed"
    assert store.get_active_rules(workspace_id="studio-a") == []
    assert store.search("净利润率", workspace_id="studio-a") == []
    with pytest.raises(ValueError, match="confirmation_token"):
        store.confirm_rule(
            proposed["id"],
            confirmed_by="user-1",
            confirmation_token="",
        )
    with pytest.raises(ValueError, match="confirmer_type"):
        store.confirm_rule(
            proposed["id"],
            confirmed_by="agent",
            confirmation_token="not-user-approved",
            confirmer_type="agent",
        )

    accepted = store.confirm_rule(
        proposed["id"],
        confirmed_by="user-1",
        confirmation_token="approval-action-123",
    )

    assert accepted["status"] == "accepted"
    assert accepted["decision_by"] == "user-1"
    assert accepted["confirmation_recorded"] is True
    assert "confirmation_hash" not in accepted
    active = store.get_active_rules(workspace_id="studio-a", scope="quote_review")
    assert [rule["id"] for rule in active] == [proposed["id"]]
    rule_result = store.search("净利润率", workspace_id="studio-a")[0]
    assert rule_result["record_type"] == "rule"
    assert rule_result["status"] == "accepted"


def test_rejected_and_revoked_rules_are_not_retrieved(tmp_path):
    store = WorkspaceKnowledgeStore(tmp_path / "knowledge.db")
    rejected = store.propose_rule("所有提醒都自动发送", proposed_by="agent")
    store.reject_rule(rejected["id"], rejected_by="user-1")
    assert store.search("自动发送") == []

    accepted = store.propose_rule("周报默认使用中文", proposed_by="agent")
    store.confirm_rule(
        accepted["id"],
        confirmed_by="user-1",
        confirmation_token="approval-1",
    )
    assert store.search("周报默认")[0]["record_type"] == "rule"
    revoked = store.revoke_rule(
        accepted["id"],
        revoked_by="user-1",
        confirmation_token="revoke-1",
    )
    assert revoked["status"] == "revoked"
    assert store.search("周报默认") == []


def test_archive_hides_resource_from_search_but_preserves_versions(tmp_path):
    store = WorkspaceKnowledgeStore(tmp_path / "knowledge.db")
    resource = store.ingest_resource(
        title="旧交付规范",
        searchable_text="贴图必须使用 TGA 格式",
        resource_type="document",
        source_type="manual",
    )

    assert store.archive_resource(resource["id"]) is True
    assert store.search("TGA 格式") == []
    assert store.get_resource(resource["id"])["status"] == "archived"
    assert store.list_resources() == []
    assert store.list_resources(include_archived=True)[0]["id"] == resource["id"]


def test_concurrent_source_updates_create_a_serial_version_chain(tmp_path):
    path = tmp_path / "knowledge.db"
    store = WorkspaceKnowledgeStore(path)
    initial = store.ingest_resource(
        title="并发规范",
        searchable_text="版本 0",
        resource_type="note",
        source_type="integration",
        source_id="shared-source",
    )

    def update(index):
        worker_store = WorkspaceKnowledgeStore(path)
        return worker_store.ingest_resource(
            title="并发规范",
            searchable_text=f"版本 {index}",
            resource_type="note",
            source_type="integration",
            source_id="shared-source",
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(update, range(1, 9)))

    current = store.get_resource(initial["id"])
    assert current["current_version"] == 9
    assert len(store.list_versions(initial["id"])) == 9
    assert all(result["id"] == initial["id"] for result in results)


def test_schema_is_idempotent_and_wal_is_enabled(tmp_path):
    path = tmp_path / "knowledge.db"
    WorkspaceKnowledgeStore(path)
    WorkspaceKnowledgeStore(path)  # 第二次打开不应重复迁移

    connection = sqlite3.connect(path)
    journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    migrations = connection.execute(
        "SELECT version FROM knowledge_schema_migrations ORDER BY version"
    ).fetchall()
    connection.close()

    assert journal_mode.lower() == "wal"
    # 迁移应覆盖到当前 SCHEMA_VERSION，且幂等（无重复版本行）
    assert migrations[-1] == (WorkspaceKnowledgeStore.SCHEMA_VERSION,)
    assert len(migrations) == len({row[0] for row in migrations})


def test_ingestion_proposal_is_inert_until_confirmed_and_audited(tmp_path):
    store = WorkspaceKnowledgeStore(tmp_path / "knowledge.db")
    proposal = store.propose_ingestion(
        "conversation-1",
        "turn-1",
        [{
            "title": "角色报价单",
            "searchable_text": "主角模型报价 10 万元",
            "resource_type": "spreadsheet",
            "source_uri": "attachments/quote.xlsx",
            "source_id": "sha256:quote-1",
            "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "structured_data": {"total_amount": 100000},
            "metadata": {"client": "腾讯"},
        }],
        "ingestion-turn-1",
    )

    assert proposal["status"] == "pending"
    assert proposal["state_version"] == 1
    assert proposal["resources"][0]["base_version"] == 0
    assert store.search("主角模型") == []
    assert store.list_resources() == []
    with pytest.raises(ValueError, match="confirmation_token"):
        store.confirm_ingestion(
            proposal["id"],
            actor="user-1",
            confirmation_token="",
        )
    with pytest.raises(KnowledgeProposalConflictError, match="state changed"):
        store.confirm_ingestion(
            proposal["id"],
            actor="user-1",
            confirmation_token="approval-token-1",
            expected_state_version=2,
        )
    assert store.list_resources() == []

    confirmed = store.confirm_ingestion(
        proposal["id"],
        actor="user-1",
        confirmation_token="approval-token-1",
        expected_state_version=1,
    )

    assert confirmed["status"] == "confirmed"
    assert confirmed["state_version"] == 2
    assert confirmed["confirmation_recorded"] is True
    assert "confirmation_hash" not in confirmed
    assert confirmed["decision_created"] is True
    assert len(confirmed["ingested_resources"]) == 1
    result = store.search("主角模型")[0]
    assert result["resource_type"] == "spreadsheet"
    assert result["source"]["uri"] == "attachments/quote.xlsx"
    assert result["version"]["structured_data"] == {"total_amount": 100000}
    assert [event["event_type"] for event in store.list_ingestion_events()] == [
        "ingestion.proposed",
        "ingestion.confirmed",
    ]
    assert store.list_ingestion_events()[1]["actor"] == "user-1"


def test_ingestion_proposal_and_confirmation_are_idempotent(tmp_path):
    store = WorkspaceKnowledgeStore(tmp_path / "knowledge.db")
    resources = [{
        "title": "制作规范",
        "searchable_text": "角色贴图统一使用 TGA",
        "resource_type": "document",
        "source_id": "spec-1",
    }]
    first = store.propose_ingestion(
        "conversation-1",
        "turn-1",
        resources,
        "same-ingestion",
    )
    repeated = store.propose_ingestion(
        "conversation-1",
        "turn-1",
        resources,
        "same-ingestion",
    )
    assert repeated["id"] == first["id"]
    with pytest.raises(ValueError, match="another ingestion proposal"):
        store.propose_ingestion(
            "conversation-1",
            "turn-1",
            [{**resources[0], "searchable_text": "改成 PNG"}],
            "same-ingestion",
        )

    applied = store.confirm_ingestion(
        first["id"],
        actor="user-1",
        confirmation_token="approve-1",
    )
    replay = store.confirm_ingestion(
        first["id"],
        actor="user-1",
        confirmation_token="approve-1",
    )

    assert applied["ingested_resources"] == replay["ingested_resources"]
    assert replay["decision_created"] is False
    resource_id = applied["ingested_resources"][0]["id"]
    assert len(store.list_versions(resource_id)) == 1
    assert [event["event_type"] for event in store.list_ingestion_events()] == [
        "ingestion.proposed",
        "ingestion.confirmed",
    ]


def test_rejected_ingestion_never_creates_knowledge(tmp_path):
    store = WorkspaceKnowledgeStore(tmp_path / "knowledge.db")
    proposal = store.propose_ingestion(
        "conversation-1",
        "turn-1",
        [{"title": "不采纳", "searchable_text": "不要写入的资料"}],
        "reject-ingestion",
    )

    rejected = store.reject_ingestion(
        proposal["id"],
        actor="user-1",
        expected_state_version=1,
    )

    assert rejected["status"] == "rejected"
    assert store.reject_ingestion(proposal["id"], actor="user-1")[
        "status"
    ] == "rejected"
    assert store.list_resources() == []
    with pytest.raises(KnowledgeProposalConflictError):
        store.confirm_ingestion(
            proposal["id"],
            actor="user-1",
            confirmation_token="too-late",
        )


def test_ingestion_proposal_enforces_conversation_workspace_scope(tmp_path):
    path = tmp_path / "shared.db"
    conversations = ConversationStore(path)
    default_conversation = conversations.create_conversation("默认工作区")
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute(
            """
            INSERT INTO workspaces(
                id, profile_id, name, settings_json, created_at, updated_at
            ) VALUES ('studio-b', 'studio-b', 'Studio B', '{}', ?, ?)
            """,
            ("2026-07-12T00:00:00+00:00", "2026-07-12T00:00:00+00:00"),
        )
    other_conversation = conversations.create_conversation(
        "Studio B",
        workspace_id="studio-b",
    )
    store = WorkspaceKnowledgeStore(path)

    with pytest.raises(KeyError, match="does not belong"):
        store.propose_ingestion(
            default_conversation["id"],
            "turn-1",
            [{"title": "跨区资料", "searchable_text": "不能进入 B"}],
            "cross-workspace",
            workspace_id="studio-b",
        )

    proposal = store.propose_ingestion(
        other_conversation["id"],
        "turn-2",
        [{"title": "B 资料", "searchable_text": "仅属于 Studio B"}],
        "studio-b-ingestion",
        workspace_id="studio-b",
    )
    assert store.get_ingestion_proposal(proposal["id"]) is None
    with pytest.raises(KeyError, match="for workspace"):
        store.confirm_ingestion(
            proposal["id"],
            actor="user-1",
            confirmation_token="wrong-workspace",
        )
    confirmed = store.confirm_ingestion(
        proposal["id"],
        actor="user-b",
        confirmation_token="studio-b-approved",
        workspace_id="studio-b",
    )
    assert confirmed["workspace_id"] == "studio-b"
    assert store.search("Studio B") == []
    assert store.search("Studio B", workspace_id="studio-b")[0][
        "workspace_id"
    ] == "studio-b"


def test_ingestion_confirmation_detects_stale_resource_version(tmp_path):
    store = WorkspaceKnowledgeStore(tmp_path / "knowledge.db")
    resource = store.ingest_resource(
        title="报价规范",
        searchable_text="版本一",
        resource_type="document",
        source_type="conversation",
        source_id="quote-spec",
    )
    proposal = store.propose_ingestion(
        "conversation-1",
        "turn-1",
        [{
            "title": "报价规范",
            "searchable_text": "提案中的版本二",
            "resource_type": "document",
            "source_id": "quote-spec",
        }],
        "stale-ingestion",
    )
    assert proposal["resources"][0]["base_version"] == 1
    assert proposal["resources"][0]["expected_resource_id"] == resource["id"]
    store.ingest_resource(
        title="报价规范",
        searchable_text="其他来源先写入的版本二",
        resource_type="document",
        source_type="conversation",
        source_id="quote-spec",
    )

    with pytest.raises(KnowledgeProposalConflictError, match="source changed"):
        store.confirm_ingestion(
            proposal["id"],
            actor="user-1",
            confirmation_token="approve-stale",
        )

    assert store.get_ingestion_proposal(proposal["id"])["status"] == "conflict"
    assert store.get_resource(resource["id"])["version"]["searchable_text"] == (
        "其他来源先写入的版本二"
    )
    assert len(store.list_versions(resource["id"])) == 2
    assert store.list_ingestion_events()[-1]["event_type"] == "ingestion.conflict"


def test_concurrent_ingestion_confirmation_creates_resources_once(tmp_path):
    path = tmp_path / "knowledge.db"
    store = WorkspaceKnowledgeStore(path)
    proposal = store.propose_ingestion(
        "conversation-1",
        "turn-1",
        [{
            "title": "并发入库",
            "searchable_text": "只应该创建一个版本",
            "source_id": "concurrent-source",
        }],
        "concurrent-ingestion",
    )

    def confirm(_):
        worker = WorkspaceKnowledgeStore(path)
        return worker.confirm_ingestion(
            proposal["id"],
            actor="user-1",
            confirmation_token="concurrent-approved",
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(confirm, range(8)))

    assert {result["status"] for result in results} == {"confirmed"}
    resource_ids = {
        result["ingested_resources"][0]["id"] for result in results
    }
    assert len(resource_ids) == 1
    resource_id = resource_ids.pop()
    assert len(store.list_versions(resource_id)) == 1
    assert [event["event_type"] for event in store.list_ingestion_events()] == [
        "ingestion.proposed",
        "ingestion.confirmed",
    ]


def test_ingestion_proposal_enforces_payload_and_resource_limits(
    tmp_path,
    monkeypatch,
):
    store = WorkspaceKnowledgeStore(tmp_path / "knowledge.db")
    with pytest.raises(ValueError, match="between 1 and"):
        store.propose_ingestion("c", "t", [], "empty")
    with pytest.raises(ValueError, match="between 1 and"):
        store.propose_ingestion(
            "c",
            "t",
            [
                {"title": f"资料 {index}", "searchable_text": "内容"}
                for index in range(store.MAX_INGESTION_RESOURCES + 1)
            ],
            "too-many",
        )
    with pytest.raises(ValueError, match="unsupported fields"):
        store.propose_ingestion(
            "c",
            "t",
            [{"title": "资料", "searchable_text": "内容", "execute": True}],
            "unknown-field",
        )

    monkeypatch.setattr(store, "MAX_INGESTION_PAYLOAD_BYTES", 100)
    with pytest.raises(ValueError, match="payload exceeds"):
        store.propose_ingestion(
            "c",
            "t",
            [{"title": "大资料", "searchable_text": "很长的内容" * 100}],
            "too-large",
        )
    assert store.list_ingestion_proposals() == []


def test_search_keeps_old_matching_rule_beyond_limit(tmp_path, monkeypatch):
    """回归测试：>1000 条已采纳规则时，较旧但命中查询词的规则不能被 LIMIT 1000 静默截断。

    对应修复：search() 内规则查询改为先按查询词做 SQL LIKE 预过滤，
    再套 LIMIT 1000，避免旧但相关的规则落在截断窗口之外而漏检。
    若回退到「ORDER BY updated_at DESC LIMIT 1000」且无预过滤，本测试会失败。
    """
    store = WorkspaceKnowledgeStore(
        tmp_path / "knowledge.db",
        enable_vector_search=False,
    )

    # 可控时钟：保证“命中规则”拥有严格更早的 updated_at，
    # 使其在无预过滤时必然排在 LIMIT 1000 之外（最旧的一条）。
    clock = {"t": datetime(2024, 1, 1, tzinfo=timezone.utc)}

    def fake_now():
        now = clock["t"]
        clock["t"] = now + timedelta(seconds=1)
        return now.isoformat(timespec="microseconds")

    monkeypatch.setattr(
        WorkspaceKnowledgeStore, "_utc_now", staticmethod(fake_now)
    )

    query_term = "量子纠缠校准"
    old_rule = store.propose_rule(
        f"开展{query_term}实验前必须先预热设备",
        workspace_id="ws-regression",
    )
    store.confirm_rule(
        old_rule["id"],
        confirmed_by="user-1",
        confirmation_token="approve-old",
    )

    # 灌入 1001 条不命中查询词、但 updated_at 更新的已采纳规则，
    # 把旧规则挤到 LIMIT 窗口之外。
    for index in range(1001):
        filler = store.propose_rule(
            f"无关的填充规则 {index}：项目排期与进度管理",
            workspace_id="ws-regression",
        )
        store.confirm_rule(
            filler["id"],
            confirmed_by="user-1",
            confirmation_token=f"approve-filler-{index}",
        )

    results = store.search(query_term, workspace_id="ws-regression")
    rule_results = [r for r in results if r.get("record_type") == "rule"]
    assert any(
        r["id"] == old_rule["id"] for r in rule_results
    ), "较旧但命中查询词的已采纳规则被 LIMIT 1000 静默截断，未出现在搜索结果中"


def test_search_keeps_old_matching_resource_beyond_limit(tmp_path, monkeypatch):
    """回归测试：>2000 条活跃资源时，较旧但命中某 resource_type 的资源不能被 LIMIT 2000 静默截断。

    对应修复：search() 内资源查询改为先按 resource_type/source_type 做 SQL 过滤，
    再套 LIMIT 2000，避免旧但相关的资源落在截断窗口之外而漏检。
    若回退到「ORDER BY updated_at DESC LIMIT 2000」且无类型过滤，本测试会失败。
    """
    store = WorkspaceKnowledgeStore(
        tmp_path / "knowledge.db",
        enable_vector_search=False,
    )

    # 可控时钟：保证“命中资源”拥有严格更早的 updated_at，
    # 使其在无类型预过滤时必然排在 LIMIT 2000 之外（最旧的一条）。
    clock = {"t": datetime(2024, 1, 1, tzinfo=timezone.utc)}

    def fake_now():
        now = clock["t"]
        clock["t"] = now + timedelta(seconds=1)
        return now.isoformat(timespec="microseconds")

    monkeypatch.setattr(
        WorkspaceKnowledgeStore, "_utc_now", staticmethod(fake_now)
    )

    query_term = "量子纠缠预热"
    # 1 条较旧、但命中查询词、类型为 doc 的资源
    old_resource = store.ingest_resource(
        title=f"设备规范：{query_term}流程",
        searchable_text=f"开展实验前必须先完成{query_term}",
        workspace_id="ws-regression",
        resource_type="doc",
        source_type="manual",
    )

    # 灌入 2001 条不命中查询词、类型不同、且 updated_at 更新的填充资源，
    # 把旧资源挤到 LIMIT 2000 窗口之外（活跃资源总数 2002 条，>2000）。
    for index in range(2001):
        store.ingest_resource(
            title=f"填充资源 {index}",
            searchable_text=f"项目排期与进度管理 {index}",
            workspace_id="ws-regression",
            resource_type="filler",
            source_type="manual",
        )

    results = store.search(
        query_term,
        workspace_id="ws-regression",
        resource_types=["doc"],
    )
    resource_results = [r for r in results if r.get("record_type") == "resource"]
    assert any(
        r["id"] == old_resource["id"] for r in resource_results
    ), "较旧但命中 resource_type 的活跃资源被 LIMIT 2000 静默截断，未出现在搜索结果中"
