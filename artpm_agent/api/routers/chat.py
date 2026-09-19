"""Chat routes for synchronous and streaming conversations."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import AsyncIterable, Iterable, Mapping
from contextlib import suppress
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from artpm_agent.tenancy import TenantContext

from ..errors import GatewayError
from ..models import ChatRequest, ChatStreamRequest
from ..serializers import (
    coerce_chat_outcome as _coerce_chat_outcome,
    json_safe as _json_safe,
    sse_frame as _sse_frame,
    stream_event_payload as _stream_event_payload,
)
from ..services import (
    ChatCommand,
    ChatOutcome,
    GatewayServiceError,
    GatewayServices,
    IdentityError,
    RequestPrincipal,
    validate_identifier,
)

if TYPE_CHECKING:
    from collections.abc import (
        AsyncIterator,
        Callable,
    )


def create_chat_router(
    *,
    services: GatewayServices,
    principal_for_request: Callable[[Request], RequestPrincipal],
    require_workspace: Callable[[GatewayServices, RequestPrincipal, Request], None],
    require_conversation: Callable[
        [GatewayServices, RequestPrincipal, str | None], Mapping[str, Any]
    ],
    tenant_context: Callable[[Request], TenantContext],
    map_store_error: Callable[[Exception], GatewayError],
    request_id: Callable[[Request], str],
    normalize_json: Callable[[Any], Any],
) -> APIRouter:
    """Create chat router with injected dependencies."""
    router = APIRouter(prefix="/v1/chat", tags=["chat"])
    logger = logging.getLogger(__name__)

    @router.post("")
    async def chat(request: Request, payload: ChatRequest) -> JSONResponse:
        principal = principal_for_request(request)
        require_workspace(services, principal, request)
        if payload.conversation_id:
            conversation = require_conversation(
                services, principal, payload.conversation_id
            )
        else:
            title = payload.title or payload.message[:80]
            try:
                conversation = services.conversations.create_conversation(
                    title,
                    workspace_id=principal.workspace_id,
                )
            except Exception as error:  # noqa: BLE001 - normalize persistence errors
                raise map_store_error(error) from error
        turn_id = uuid4().hex
        attachments = tuple(
            item.model_dump(mode="json") for item in payload.attachments
        )
        user_message_persisted = False
        try:
            user_message = services.conversations.add_message(
                conversation["id"],
                "user",
                payload.message,
                turn_id=turn_id,
                workspace_id=principal.workspace_id,
                metadata={"attachments": list(attachments), "source": "api"},
            )
            user_message_persisted = True
            command = ChatCommand(
                principal=principal,
                conversation_id=conversation["id"],
                turn_id=turn_id,
                message=payload.message,
                attachments=attachments,
                tenant_context=tenant_context(request),
                before_message_id=(
                    int(user_message["id"])
                    if isinstance(user_message, Mapping) and user_message.get("id")
                    else None
                ),
            )
            outcome = await services.chat_async(command)
            if isinstance(outcome, Mapping):
                outcome = ChatOutcome(**dict(outcome))
            if not isinstance(outcome, ChatOutcome):
                raise GatewayServiceError("chat handler returned an invalid outcome")
            response_text = outcome.response or "模型服务未能完成请求，请稍后重试。"
            status = "complete" if outcome.success else "error"
            services.conversations.add_message(
                conversation["id"],
                "assistant",
                outcome.response or "请求未返回有效内容",
                status=status,
                turn_id=turn_id,
                workspace_id=principal.workspace_id,
                metadata={
                    "handled_by": outcome.handled_by,
                    "awaiting_approval": outcome.awaiting_approval,
                    "metadata": _json_safe(outcome.metadata),
                    "artifacts": _json_safe(outcome.artifacts),
                    # Provider details stay in server logs/telemetry. Never
                    # persist raw exception text in a user-visible transcript.
                    "error": "chat_failed" if outcome.error else None,
                },
            )
        except GatewayError:
            raise
        except Exception as error:  # noqa: BLE001 - never leak provider internals
            logger.exception("Chat handler failed [%s]", request_id(request))
            if user_message_persisted:
                try:
                    services.conversations.add_message(
                        conversation["id"],
                        "assistant",
                        "请求处理失败，请稍后重试。",
                        status="error",
                        turn_id=turn_id,
                        workspace_id=principal.workspace_id,
                        metadata={
                            "handled_by": "gateway",
                            "error": "chat_handler_failed",
                        },
                    )
                except Exception:  # noqa: BLE001 - preserve the original failure
                    logger.exception(
                        "Failed to persist chat error callback [%s]",
                        request_id(request),
                    )
            raise GatewayError(
                502,
                "chat_handler_failed",
                "chat service could not complete the request",
            ) from error
        metadata = _json_safe(outcome.metadata)
        permission_request_id = (
            metadata.get("permission_request_id")
            if isinstance(metadata, Mapping)
            else None
        )
        response_body = {
            "conversation_id": conversation["id"],
            "turn_id": turn_id,
            "response": response_text,
            "success": outcome.success,
            "awaiting_approval": outcome.awaiting_approval,
            "permission_request_id": permission_request_id,
            "handled_by": outcome.handled_by,
            "metadata": metadata,
            "artifacts": _json_safe(outcome.artifacts),
        }
        status_code = (
            202 if outcome.awaiting_approval else (200 if outcome.success else 502)
        )
        if not outcome.success and not outcome.awaiting_approval:
            response_body["error"] = {
                "code": "chat_failed",
                "message": "模型服务未能完成请求，请稍后重试。",
                "request_id": request_id(request),
            }
        return JSONResponse(
            status_code=status_code,
            content=_json_safe(response_body),
            headers={"x-request-id": request_id(request)},
        )

    @router.post("/stream")
    async def chat_stream(
        request: Request,
        payload: ChatStreamRequest,
        after_sequence: int | None = Query(default=None, ge=0),
    ) -> StreamingResponse:
        """Stream one tenant-scoped turn as resumable Server-Sent Events.

        The route accepts a provider-neutral ``chat_stream_handler`` when a
        host supplies one.  The local adapter bridges the existing async chat
        handler and its EventBus, so migrating providers does not require a
        second orchestration path.  Every frame carries ``run_id`` and
        ``turn_id`` in both the SSE id and JSON payload; clients can persist
        those values before reconnecting.  ``after_sequence`` is reserved for
        session-log replay adapters and is accepted for forward compatibility.
        """

        principal = principal_for_request(request)
        require_workspace(services, principal, request)
        if payload.conversation_id:
            conversation = require_conversation(
                services, principal, payload.conversation_id
            )
        else:
            title = payload.title or payload.message[:80]
            try:
                conversation = services.conversations.create_conversation(
                    title,
                    workspace_id=principal.workspace_id,
                )
            except Exception as error:  # noqa: BLE001 - normalize persistence errors
                raise map_store_error(error) from error

        try:
            turn_id = validate_identifier(
                payload.turn_id or uuid4().hex,
                "turn_id",
                max_length=256,
            )
            run_id = validate_identifier(
                payload.run_id or turn_id,
                "run_id",
                max_length=256,
            )
        except IdentityError as error:
            raise GatewayError(400, "invalid_stream_id", str(error)) from error

        attachments = tuple(
            item.model_dump(mode="json") for item in payload.attachments
        )
        try:
            user_message = services.conversations.add_message(
                conversation["id"],
                "user",
                payload.message,
                turn_id=turn_id,
                workspace_id=principal.workspace_id,
                metadata={"attachments": list(attachments), "source": "api-stream"},
            )
        except Exception as error:  # noqa: BLE001 - normalize persistence errors
            raise map_store_error(error) from error

        command = ChatCommand(
            principal=principal,
            conversation_id=conversation["id"],
            turn_id=turn_id,
            message=payload.message,
            attachments=attachments,
            tenant_context=tenant_context(request),
            before_message_id=(
                int(user_message["id"])
                if isinstance(user_message, Mapping) and user_message.get("id")
                else None
            ),
            run_id=run_id,
        )

        async def stream_frames() -> AsyncIterator[str]:
            event_index = 0
            started = False
            ended = False
            response_parts: list[str] = []
            outcome: ChatOutcome | None = None
            unsubscribe: Callable[[], Any] | None = None
            task: asyncio.Task[Any] | None = None

            async def emit(
                value: Any,
                *,
                final: bool = False,
            ) -> AsyncIterator[str]:
                nonlocal event_index, started, ended
                event_payload = _stream_event_payload(
                    value,
                    run_id=run_id,
                    turn_id=turn_id,
                )
                if event_payload is None:
                    return
                event_type = str(event_payload.get("type") or "message_update")
                if event_type == "turn_start":
                    if started:
                        return
                    started = True
                elif event_type == "turn_end":
                    if not final:
                        return
                    if ended:
                        return
                    ended = True
                if event_type in {"message_update", "message_delta"}:
                    delta = event_payload.get("delta")
                    if isinstance(delta, str) and delta:
                        response_parts.append(delta)
                    elif not response_parts:
                        content = event_payload.get("content")
                        if isinstance(content, str) and content:
                            response_parts.append(content)
                event_id = f"{run_id}:{turn_id}:{event_index}"
                event_index += 1
                yield _sse_frame(event_payload, event_id=event_id)

            # A synthetic first frame gives every adapter one stable lifecycle
            # marker.  A duplicate TURN_START from the adapter is suppressed.
            async for frame in emit(
                {
                    "type": "turn_start",
                    "run_id": run_id,
                    "turn_id": turn_id,
                    "metadata": {
                        "workspace_id": principal.workspace_id,
                        "conversation_id": conversation["id"],
                        "after_sequence": after_sequence,
                    },
                }
            ):
                yield frame

            try:
                stream_handler = services.chat_stream_handler
                if stream_handler is not None:
                    callable_target = getattr(
                        stream_handler, "__call__", stream_handler
                    )
                    if inspect.iscoroutinefunction(
                        stream_handler
                    ) or inspect.iscoroutinefunction(callable_target):
                        source = stream_handler(command)
                    else:
                        source = await asyncio.to_thread(stream_handler, command)
                    if inspect.isawaitable(source):
                        source = await source
                    direct_outcome = _coerce_chat_outcome(source)
                    if direct_outcome is not None:
                        outcome = direct_outcome
                    elif isinstance(source, Mapping):
                        async for frame in emit(source):
                            yield frame
                    elif isinstance(source, str):
                        async for frame in emit(source):
                            yield frame
                    elif isinstance(source, AsyncIterable) or hasattr(
                        source, "__aiter__"
                    ):
                        async for item in cast(AsyncIterable[Any], source):
                            direct_outcome = _coerce_chat_outcome(item)
                            if direct_outcome is not None:
                                outcome = direct_outcome
                                continue
                            async for frame in emit(item):
                                yield frame
                    elif isinstance(source, Iterable) and not isinstance(
                        source, (str, bytes)
                    ):
                        iterator = iter(source)

                        def next_item() -> tuple[bool, Any | None]:
                            try:
                                return True, next(iterator)
                            except StopIteration:
                                return False, None

                        while True:
                            has_item, item = await asyncio.to_thread(next_item)
                            if not has_item:
                                break
                            direct_outcome = _coerce_chat_outcome(item)
                            if direct_outcome is not None:
                                outcome = direct_outcome
                                continue
                            async for frame in emit(item):
                                yield frame
                    elif source is not None:
                        direct_outcome = _coerce_chat_outcome(source)
                        if direct_outcome is not None:
                            outcome = direct_outcome
                else:
                    # The default local runtime emits SESSION lifecycle events
                    # synchronously on its EventBus while chat_async runs in a
                    # worker thread. Forward those observations without
                    # blocking the gateway event loop.
                    event_queue: asyncio.Queue[Any] = asyncio.Queue()
                    event_loop = asyncio.get_running_loop()
                    stream_complete = object()
                    event_bus = services.event_bus

                    def observe(event: Any) -> None:
                        event_run = str(getattr(event, "run_id", "") or "")
                        event_turn = str(getattr(event, "turn_id", "") or "")
                        # Both identifiers are part of the event scope.  The
                        # previous ``and`` check admitted an event whenever
                        # either value happened to match, which can leak a
                        # different run that reused a turn ID (or vice versa).
                        if event_run != run_id or event_turn != turn_id:
                            return
                        # EventBus callbacks run in the provider thread. Hand
                        # events back to the loop without occupying an
                        # executor thread for every polling interval.

                        def enqueue() -> None:
                            event_queue.put_nowait(event)

                        try:
                            event_loop.call_soon_threadsafe(enqueue)
                        except RuntimeError:
                            # The client may disconnect while a provider
                            # thread is publishing its final event.
                            return

                    async def invoke_chat() -> Any:
                        try:
                            return await services.chat_async(command)
                        finally:
                            # EventBus publishes synchronously in the provider
                            # call. The loop callback order therefore preserves
                            # every event before this completion marker.
                            try:
                                event_loop.call_soon_threadsafe(
                                    event_queue.put_nowait, stream_complete
                                )
                            except RuntimeError:
                                # A disconnect may close the loop while the
                                # provider task is unwinding.
                                pass

                    if event_bus is not None and callable(
                        getattr(event_bus, "subscribe", None)
                    ):
                        unsubscribe = event_bus.subscribe(observe)
                    task = asyncio.create_task(invoke_chat())
                    while True:
                        event = await event_queue.get()
                        if event is stream_complete:
                            break
                        async for frame in emit(event):
                            yield frame
                    outcome_value = await task
                    outcome = _coerce_chat_outcome(outcome_value)
                    if outcome is None:
                        raise GatewayServiceError(
                            "chat handler returned an invalid outcome"
                        )
            except asyncio.CancelledError:
                if task is not None and not task.done():
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        # Client disconnects must not leak the provider worker;
                        # the original cancellation remains the public result.
                        pass
                raise
            except Exception:  # noqa: BLE001 - never leak provider internals
                logger.exception("Chat stream failed [%s]", request_id(request))
                outcome = ChatOutcome(
                    response="",
                    success=False,
                    error="chat_failed",
                    handled_by="gateway",
                )
                async for frame in emit(
                    {
                        "type": "error",
                        "run_id": run_id,
                        "turn_id": turn_id,
                        "is_error": True,
                        "error": "chat_failed",
                        "metadata": {"request_id": request_id(request)},
                    }
                ):
                    yield frame
            finally:
                if unsubscribe is not None:
                    try:
                        unsubscribe()
                    except Exception:  # pragma: no cover - optional bus cleanup
                        logger.debug(
                            "chat stream event unsubscribe failed", exc_info=True
                        )
                if task is not None and not task.done():
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task

            if outcome is None:
                outcome = ChatOutcome(response="".join(response_parts))
            response_text = outcome.response or "".join(response_parts)
            if response_text and not response_parts:
                # Legacy/default handlers return one final string rather than
                # token events. Expose it as one delta so every stream has a
                # consistent message channel while providers migrate.
                async for frame in emit(
                    {
                        "type": "message_update",
                        "run_id": run_id,
                        "turn_id": turn_id,
                        "delta": response_text,
                        "metadata": {"source": "final_response"},
                    }
                ):
                    yield frame
            status = "complete" if outcome.success else "error"
            try:
                services.conversations.add_message(
                    conversation["id"],
                    "assistant",
                    response_text
                    or ("请求处理失败，请稍后重试。" if not outcome.success else ""),
                    status=status,
                    turn_id=turn_id,
                    workspace_id=principal.workspace_id,
                    metadata={
                        "run_id": run_id,
                        "handled_by": outcome.handled_by,
                        "awaiting_approval": outcome.awaiting_approval,
                        "metadata": _json_safe(outcome.metadata),
                        "artifacts": _json_safe(outcome.artifacts),
                        "error": "chat_failed" if outcome.error else None,
                    },
                )
            except Exception:  # noqa: BLE001 - stream result remains inspectable
                logger.exception(
                    "Failed to persist streamed assistant message [%s]",
                    request_id(request),
                )

            async for frame in emit(
                {
                    "type": "snapshot",
                    "run_id": run_id,
                    "turn_id": turn_id,
                    "response": response_text,
                    "success": bool(outcome.success),
                    "awaiting_approval": bool(outcome.awaiting_approval),
                    "handled_by": outcome.handled_by,
                    "metadata": _json_safe(outcome.metadata),
                    "artifacts": _json_safe(outcome.artifacts),
                    "error": "chat_failed" if outcome.error else None,
                },
            ):
                yield frame
            async for frame in emit(
                {
                    "type": "turn_end",
                    "run_id": run_id,
                    "turn_id": turn_id,
                    "is_error": not outcome.success,
                    "error": "chat_failed" if not outcome.success else None,
                    "metadata": {
                        "success": bool(outcome.success),
                        "handled_by": outcome.handled_by,
                        "awaiting_approval": bool(outcome.awaiting_approval),
                    },
                },
                final=True,
            ):
                yield frame

        return StreamingResponse(
            stream_frames(),
            media_type="text/event-stream",
            headers={
                "cache-control": "no-cache, no-transform",
                "connection": "keep-alive",
                "x-accel-buffering": "no",
                "x-request-id": request_id(request),
            },
        )

    return router
