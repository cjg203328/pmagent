"""LiveKit TTS adapter for the operating system's local speech engine."""

from __future__ import annotations

import asyncio

from livekit.agents import tts, utils
from livekit.agents.types import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS

from .service import VoiceAudioService


class LocalSystemTTS(tts.TTS):
    """Non-streaming pyttsx3 adapter used only as the final audio fallback."""

    def __init__(self, *, sample_rate: int = 24000) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(
                streaming=False,
                aligned_transcript=False,
            ),
            sample_rate=sample_rate,
            num_channels=1,
        )

    @property
    def model(self) -> str:
        return "system"

    @property
    def provider(self) -> str:
        return "local"

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> "LocalChunkedStream":
        return LocalChunkedStream(tts=self, input_text=text, conn_options=conn_options)


class LocalChunkedStream(tts.ChunkedStream):
    def __init__(
        self,
        *,
        tts: LocalSystemTTS,
        input_text: str,
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        if not self._input_text.strip():
            return
        audio = await asyncio.to_thread(VoiceAudioService._local_tts, self._input_text)
        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=self._tts.sample_rate,
            num_channels=1,
            mime_type="audio/wav",
            stream=False,
        )
        output_emitter.push(audio.data)
        output_emitter.flush()


__all__ = ["LocalSystemTTS"]
