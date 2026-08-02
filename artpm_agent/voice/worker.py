"""LiveKit worker that gives PMAgent a real-time voice shell."""

from __future__ import annotations

import logging
import json
from typing import Any, AsyncIterable

from .bridge import PMAgentVoiceBridge
from .config import VoiceSettings
from .contracts import VoiceConfigurationError
from .session import VoiceSessionContext

logger = logging.getLogger("artpm.voice")


def _latest_user_text(chat_ctx: Any) -> str:
    for item in reversed(list(getattr(chat_ctx, "items", ()) or ())):
        role = getattr(item, "role", "")
        role = getattr(role, "value", role)
        if str(role).casefold() != "user":
            continue
        text = getattr(item, "text_content", "")
        if callable(text):
            text = text()
        if str(text or "").strip():
            return str(text).strip()
    return ""


def _build_stt(settings: VoiceSettings) -> Any:
    from livekit.plugins import cartesia

    if not settings.cartesia_api_key:
        raise VoiceConfigurationError("CARTESIA_API_KEY is required for voice STT")
    return cartesia.STT(
        model=settings.stt_model or "ink-whisper",
        language=settings.stt_language or "zh",
        sample_rate=16000,
        api_key=settings.cartesia_api_key,
    )


def _build_tts(settings: VoiceSettings) -> Any:
    from livekit.agents import tts

    providers: list[Any] = []
    for provider in settings.tts_fallbacks:
        if provider == "text":
            continue
        if provider == "minimax" and settings.minimax_api_key:
            from livekit.plugins import minimax

            kwargs: dict[str, Any] = {"api_key": settings.minimax_api_key}
            if settings.tts_provider == "minimax" and settings.tts_model:
                kwargs["model"] = settings.tts_model
            if settings.tts_provider == "minimax" and settings.tts_voice_id:
                kwargs["voice"] = settings.tts_voice_id
            providers.append(minimax.TTS(**kwargs))
        elif provider == "cartesia" and settings.cartesia_api_key:
            from livekit.plugins import cartesia

            kwargs = {
                "api_key": settings.cartesia_api_key,
                "language": settings.stt_language or "zh",
            }
            if settings.tts_provider == "cartesia" and settings.tts_model:
                kwargs["model"] = settings.tts_model
            if settings.tts_provider == "cartesia" and settings.tts_voice_id:
                kwargs["voice"] = settings.tts_voice_id
            providers.append(cartesia.TTS(**kwargs))
        elif provider == "local" and settings.local_tts_available:
            from .local_tts import LocalSystemTTS

            providers.append(LocalSystemTTS())
    if not providers:
        raise VoiceConfigurationError("no speech output provider is configured")
    if len(providers) == 1:
        return providers[0]
    return tts.FallbackAdapter(providers, max_retry_per_tts=1)


def build_server(settings: VoiceSettings | None = None) -> Any:
    """Build an isolated worker server; no Agent or store is duplicated."""

    try:
        from livekit import agents
        from livekit.agents import (
            Agent,
            AgentServer,
            AgentSession,
            ModelSettings,
            TurnHandlingOptions,
            llm,
            room_io,
        )
        from livekit.plugins import silero
    except ImportError as error:  # pragma: no cover - optional install boundary
        raise VoiceConfigurationError(
            'install voice dependencies with `pip install -e ".[voice]"`'
        ) from error

    settings = settings or VoiceSettings.from_env()
    server = AgentServer()

    class ArtPMVoiceAgent(Agent):
        def __init__(self, bridge: PMAgentVoiceBridge) -> None:
            super().__init__(
                instructions=(
                    "You are the voice transport for ArtPM Agent. "
                    "All reasoning, memory, tools and permissions are handled by PMAgent."
                ),
                llm=None,
            )
            self.bridge = bridge

        async def llm_node(
            self,
            chat_ctx: llm.ChatContext,
            tools: list[llm.FunctionTool],
            model_settings: ModelSettings,
        ) -> AsyncIterable[str]:
            del tools, model_settings
            text = _latest_user_text(chat_ctx)
            if not text:
                yield "我没有听清，请再说一次。"
                return
            try:
                async for chunk in self.bridge.stream_reply(text):
                    yield chunk
            except Exception as error:  # noqa: BLE001 - keep text channel alive
                logger.warning("voice turn failed: %s", type(error).__name__)
                yield "语音服务暂时不可用，请在对话框中继续输入。"

    @server.rtc_session(agent_name=settings.agent_name)
    async def entrypoint(ctx: agents.JobContext) -> None:
        context = VoiceSessionContext.from_json(ctx.job.metadata)

        def publish_event(event: Any) -> Any:
            return ctx.room.local_participant.publish_data(
                json.dumps(event, ensure_ascii=True, separators=(",", ":")),
                reliable=True,
                topic="artpm.voice",
            )

        bridge = PMAgentVoiceBridge(settings, context, event_sink=publish_event)
        session = AgentSession(
            stt=_build_stt(settings),
            vad=silero.VAD.load(
                min_speech_duration=0.08,
                min_silence_duration=0.55,
                prefix_padding_duration=0.35,
                force_cpu=True,
            ),
            tts=_build_tts(settings),
            turn_handling=TurnHandlingOptions(
                turn_detection="vad",
                interruption={
                    "enabled": True,
                    "resume_false_interruption": True,
                    "false_interruption_timeout": 1.0,
                    "min_duration": 0.35,
                },
                preemptive_generation={"enabled": False},
            ),
            tts_text_transforms=["filter_markdown", "filter_emoji"],
            aec_warmup_duration=2.0,
        )
        ctx.log_context_fields = {
            "voice_session": context.session_id,
            "workspace": context.workspace_id,
            "conversation": context.conversation_id,
        }
        await session.start(
            agent=ArtPMVoiceAgent(bridge),
            room=ctx.room,
            room_options=room_io.RoomOptions(
                audio_input=room_io.AudioInputOptions(),
            ),
        )

    return server


def main() -> None:
    from livekit.agents import cli

    settings = VoiceSettings.from_env()
    status = settings.public_status()
    if not settings.enabled:
        raise SystemExit(
            "ARTPM_VOICE_ENABLED is false; the text application remains available"
        )
    if not settings.livekit_configured:
        raise SystemExit(
            "LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET are required"
        )
    if not settings.stt_configured:
        raise SystemExit("CARTESIA_API_KEY is required for Chinese speech recognition")
    if not settings.speech_output_configured:
        raise SystemExit("configure MiniMax, Cartesia, or the local TTS fallback")
    logger.info("starting voice worker: %s", status)
    cli.run_app(build_server(settings))


if __name__ == "__main__":  # pragma: no cover - CLI entrypoint
    main()


__all__ = ["build_server", "main"]
