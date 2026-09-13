"""Workspace Wiki authoring surface backed by the shared RAG knowledge store."""

from __future__ import annotations

from collections.abc import Callable
import logging
from typing import Optional

import streamlit as st

from artpm_agent.memory import WikiPageConflictError
from artpm_agent.tenancy import TenantContext
from artpm_agent.ui_state import get_wiki_store

logger = logging.getLogger(__name__)

_NEW_PAGE = "__new_wiki_page__"
_PAGE_CHOICE_KEY = "wiki_page_choice"
_PENDING_PAGE_CHOICE_KEY = "wiki_pending_page_choice"
_FLASH_KEY = "wiki_flash"


def _workspace_id() -> str:
    context = st.session_state.get("tenant_context")
    if isinstance(context, TenantContext):
        return context.workspace_id
    return "local-default"


def _page_label(page: dict) -> str:
    return (
        f"{page.get('title') or '未命名页面'} · {_status_label(page)} · "
        f"v{page.get('version')}"
    )


def _status_label(page: dict) -> str:
    status = {
        "draft": "草稿",
        "published": "已发布",
        "archived": "已归档",
    }.get(str(page.get("status") or ""), "未知")
    return status


def _sync_label(page: dict) -> str:
    status = str((page.get("sync") or {}).get("status") or "")
    return {
        "not_indexed": "未索引",
        "pending": "待同步",
        "synced": "已同步",
        "error": "同步失败",
    }.get(status, "未知")


def _finish_mutation(
    *,
    message: str,
    on_changed: Optional[Callable[[], None]],
    page_id: Optional[str] = None,
) -> None:
    if on_changed is not None:
        try:
            on_changed()
        except Exception:  # noqa: BLE001 - cache invalidation is optional
            logger.debug("Wiki cache invalidation skipped", exc_info=True)
    if page_id is not None:
        st.session_state[_PENDING_PAGE_CHOICE_KEY] = page_id
    st.session_state[_FLASH_KEY] = message
    st.rerun()


def _mutation_message(page: dict, action: str) -> str:
    if (page.get("sync") or {}).get("status") == "error":
        return f"{action}，索引将在稍后重试"
    return action


def render_wiki_workspace(
    *,
    on_changed: Optional[Callable[[], None]] = None,
) -> None:
    """Render creation, publishing, revision, and retry controls for Wiki pages."""
    store = get_wiki_store()
    if store is None:
        st.error("Wiki 知识库未就绪，请查看服务日志。")
        return

    flash = st.session_state.pop(_FLASH_KEY, None)
    if flash:
        try:
            st.toast(str(flash))
        except (AttributeError, TypeError):
            st.success(str(flash))

    workspace_id = _workspace_id()
    try:
        all_pages = store.list_pages(
            workspace_id=workspace_id,
            include_archived=True,
            limit=200,
        )
    except Exception:
        logger.exception("Unable to read workspace Wiki pages")
        st.error("暂时无法读取 Wiki 页面。")
        return

    pages = [page for page in all_pages if page["status"] != "archived"]

    published_count = sum(page["status"] == "published" for page in pages)
    unsynced_count = sum(
        page["status"] in {"published", "archived"}
        and (page.get("sync") or {}).get("status") != "synced"
        for page in all_pages
    )
    metric_columns = st.columns(3)
    with metric_columns[0]:
        st.metric("Wiki 页面", len(pages))
    with metric_columns[1]:
        st.metric("已发布", published_count)
    with metric_columns[2]:
        st.metric("待同步", unsynced_count)

    retry_column, _ = st.columns((1, 5))
    with retry_column:
        retry_pending = st.button(
            "重试同步",
            key="wiki_reconcile",
            icon=":material/sync:",
            disabled=unsynced_count == 0,
        )
    if retry_pending:
        try:
            result = store.reconcile(workspace_id=workspace_id)
            _finish_mutation(
                message=f"已处理 {result['attempted']} 个待同步页面",
                on_changed=on_changed,
            )
        except Exception:
            logger.exception("Unable to reconcile Wiki RAG projections")
            st.error("同步失败，请稍后重试。")

    page_by_id = {page["id"]: page for page in pages}
    choices = [_NEW_PAGE, *page_by_id]
    pending_choice = st.session_state.pop(_PENDING_PAGE_CHOICE_KEY, None)
    if pending_choice in choices:
        st.session_state[_PAGE_CHOICE_KEY] = pending_choice
    elif st.session_state.get(_PAGE_CHOICE_KEY) not in choices:
        st.session_state[_PAGE_CHOICE_KEY] = _NEW_PAGE

    page_id = st.selectbox(
        "页面",
        choices,
        key=_PAGE_CHOICE_KEY,
        format_func=lambda choice: (
            "新建页面" if choice == _NEW_PAGE else _page_label(page_by_id[choice])
        ),
    )
    page = None
    if page_id != _NEW_PAGE:
        try:
            page = store.get_page(page_id, workspace_id=workspace_id)
        except Exception:
            logger.exception("Unable to read selected Wiki page")
            st.error("暂时无法读取这个 Wiki 页面。")
            return
        if page is None:
            st.warning("页面已不存在，请重新选择。")
            return
    editor_id = page["id"] if page is not None else "new"
    title = st.text_input(
        "标题",
        value=page["title"] if page is not None else "",
        key=f"wiki_title_{editor_id}",
        max_chars=240,
    ).strip()
    slug = st.text_input(
        "路径",
        value=page["slug"] if page is not None else "",
        key=f"wiki_slug_{editor_id}",
        max_chars=160,
    ).strip()
    markdown = st.text_area(
        "内容",
        value=page["markdown"] if page is not None else "",
        key=f"wiki_markdown_{editor_id}",
        height=320,
        max_chars=2_000_000,
    )

    if page is not None:
        st.caption(
            f"状态：{_status_label(page)} · "
            f"RAG：{_sync_label(page)}"
        )
        if (page.get("sync") or {}).get("status") == "error":
            st.warning("索引同步失败，可使用“重试同步”。")

    action_columns = st.columns(4)
    with action_columns[0]:
        save_clicked = st.button(
            "保存",
            key=f"wiki_save_{editor_id}",
            icon=":material/save:",
            width="stretch",
        )
    with action_columns[1]:
        publish_clicked = st.button(
            "发布",
            key=f"wiki_publish_{editor_id}",
            icon=":material/publish:",
            type="primary",
            width="stretch",
        )
    with action_columns[2]:
        sync_clicked = st.button(
            "同步",
            key=f"wiki_sync_{editor_id}",
            icon=":material/sync:",
            disabled=(
                page is None
                or page["status"] != "published"
                or (page.get("sync") or {}).get("status") == "synced"
            ),
            width="stretch",
        )
    with action_columns[3]:
        archive_clicked = st.button(
            "归档",
            key=f"wiki_archive_{editor_id}",
            icon=":material/archive:",
            disabled=page is None,
            width="stretch",
        )

    try:
        result = None
        if save_clicked:
            if page is None:
                result = store.create_page(
                    title=title,
                    markdown=markdown,
                    slug=slug or None,
                    workspace_id=workspace_id,
                    actor="local-user",
                    change_note="Saved Wiki draft",
                )
            else:
                result = store.update_page(
                    page["id"],
                    title=title,
                    markdown=markdown,
                    slug=slug,
                    workspace_id=workspace_id,
                    actor="local-user",
                    change_note="Saved Wiki page",
                    expected_version=page["version"],
                )
            _finish_mutation(
                message=_mutation_message(result, "已保存"),
                on_changed=on_changed,
                page_id=result["id"],
            )
        if publish_clicked:
            if page is None:
                result = store.create_page(
                    title=title,
                    markdown=markdown,
                    slug=slug or None,
                    workspace_id=workspace_id,
                    actor="local-user",
                    publish=True,
                    change_note="Published Wiki page",
                )
            else:
                result = store.update_page(
                    page["id"],
                    title=title,
                    markdown=markdown,
                    slug=slug,
                    workspace_id=workspace_id,
                    actor="local-user",
                    expected_version=page["version"],
                    publish=True,
                    change_note="Published Wiki page",
                )
            _finish_mutation(
                message=_mutation_message(result, "已发布"),
                on_changed=on_changed,
                page_id=result["id"],
            )
        if sync_clicked and page is not None:
            result = store.sync_page(page["id"], workspace_id=workspace_id)
            _finish_mutation(
                message=_mutation_message(result, "已同步"),
                on_changed=on_changed,
                page_id=result["id"],
            )
        if archive_clicked and page is not None:
            result = store.archive_page(
                page["id"],
                workspace_id=workspace_id,
                actor="local-user",
                expected_version=page["version"],
            )
            _finish_mutation(
                message=_mutation_message(result, "已归档"),
                on_changed=on_changed,
                page_id=_NEW_PAGE,
            )
    except WikiPageConflictError:
        st.warning("页面已被更新，请重新加载后再保存。")
    except (KeyError, ValueError) as error:
        st.warning(str(error))
    except Exception:
        logger.exception("Unable to mutate Wiki page")
        st.error("操作失败，请稍后重试。")

    if page is not None:
        try:
            versions = store.list_versions(page["id"], workspace_id=workspace_id)
        except Exception:
            logger.exception("Unable to read Wiki page versions")
            versions = []
        if versions:
            with st.expander("版本记录"):
                selected_version = st.selectbox(
                    "版本",
                    [item["version"] for item in versions],
                    key=f"wiki_restore_version_{page['id']}",
                    format_func=lambda value: f"v{value}",
                )
                restore_clicked = st.button(
                    "恢复此版本",
                    key=f"wiki_restore_{page['id']}",
                    icon=":material/history:",
                )
                if restore_clicked:
                    try:
                        restored = store.restore_version(
                            page["id"],
                            selected_version,
                            workspace_id=workspace_id,
                            actor="local-user",
                            expected_version=page["version"],
                        )
                        _finish_mutation(
                            message=_mutation_message(restored, "已恢复版本"),
                            on_changed=on_changed,
                            page_id=restored["id"],
                        )
                    except WikiPageConflictError:
                        st.warning("页面已被更新，请重新加载后再恢复。")
                    except Exception:
                        logger.exception("Unable to restore Wiki page version")
                        st.error("恢复失败，请稍后重试。")
        with st.expander("预览"):
            st.markdown(markdown)
