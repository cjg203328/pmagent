"""Desktop recording and speech output service.

This module uses the same Cartesia/MiniMax plugins as the LiveKit worker, but
exposes a small synchronous facade for Streamlit. Provider imports and network
calls happen only after voice is enabled and configured.
"""

from __future__ import annotations

from array import array
import asyncio
from collections.abc import Callable, Mapping
from io import BytesIO
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any
import wave

from .config import VoiceSettings, get_voice_settings
from .contracts import (
    SpeechAudio,
    Transcript,
    VoiceProviderError,
    VoiceUnavailableError,
)

logger = logging.getLogger(__name__)

_MARKDOWN_TOKEN = re.compile(r"[`*_#>|~]+")
_SPACE = re.compile(r"[ \t]+")


def speakable_text(value: str, *, max_chars: int = 6000) -> str:
    """Remove formatting that speech engines pronounce poorly."""

    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = _MARKDOWN_TOKEN.sub("", text)
    text = "\n".join(_SPACE.sub(" ", line).strip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:max_chars].strip()


def _run(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise VoiceProviderError("voice provider cannot run inside an active event loop")


def _wav_pcm(value: bytes, *, target_rate: int = 16000) -> tuple[bytes, int, int]:
    try:
        with wave.open(BytesIO(value), "rb") as source:
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
            sample_rate = source.getframerate()
            frame_count = source.getnframes()
            pcm = source.readframes(frame_count)
    except (EOFError, wave.Error) as error:
        raise VoiceProviderError("recording is not a valid WAV file") from error
    if sample_width != 2 or channels not in {1, 2}:
        raise VoiceProviderError("recording must use 16-bit mono or stereo PCM")
    if not 8000 <= sample_rate <= 48000:
        raise VoiceProviderError("recording sample rate is unsupported")

    samples = array("h")
    samples.frombytes(pcm)
    if channels == 2:
        samples = array(
            "h",
            (
                int((int(samples[index]) + int(samples[index + 1])) / 2)
                for index in range(0, len(samples) - 1, 2)
            ),
        )
        channels = 1
    if sample_rate != target_rate and samples:
        # Linear resampling is sufficient for the 16 kHz speech-recognition
        # input and avoids a second audio-processing dependency.
        output_count = max(1, round(len(samples) * target_rate / sample_rate))
        if output_count == 1:
            resampled = array("h", [samples[0]])
        else:
            scale = (len(samples) - 1) / (output_count - 1)
            result = array("h")
            for index in range(output_count):
                position = index * scale
                left = int(position)
                right = min(left + 1, len(samples) - 1)
                fraction = position - left
                result.append(
                    int(samples[left] + (samples[right] - samples[left]) * fraction)
                )
            resampled = result
        samples = resampled
        sample_rate = target_rate
    return samples.tobytes(), sample_rate, channels


def _pcm_wav(data: bytes, *, sample_rate: int, channels: int) -> bytes:
    output = BytesIO()
    with wave.open(output, "wb") as destination:
        destination.setnchannels(channels)
        destination.setsampwidth(2)
        destination.setframerate(sample_rate)
        destination.writeframes(data)
    return output.getvalue()


def _validated_wav(value: bytes) -> tuple[int, int]:
    """Return sample metadata only when a local engine produced real audio."""

    try:
        with wave.open(BytesIO(value), "rb") as source:
            sample_rate = source.getframerate()
            frame_count = source.getnframes()
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
    except (EOFError, wave.Error) as error:
        raise VoiceProviderError(
            "local speech engine returned invalid audio"
        ) from error
    if (
        sample_width != 2
        or channels not in {1, 2}
        or sample_rate <= 0
        or frame_count < max(1, sample_rate // 20)
    ):
        raise VoiceProviderError("local speech engine returned empty audio")
    return sample_rate, channels


def _run_windows_speech(text: str, path: Path, *, timeout: float = 20.0) -> None:
    executable = shutil.which("powershell") or shutil.which("pwsh")
    if not executable:
        raise VoiceUnavailableError("Windows speech runtime is unavailable")
    environment = os.environ.copy()
    environment["ARTPM_TTS_TEXT"] = text
    environment["ARTPM_TTS_PATH"] = str(path)
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$speaker = [System.Speech.Synthesis.SpeechSynthesizer]::new(); "
        "try { $speaker.SetOutputToWaveFile($env:ARTPM_TTS_PATH); "
        "$speaker.Speak($env:ARTPM_TTS_TEXT) } finally { $speaker.Dispose() }"
    )
    try:
        result = subprocess.run(
            [
                executable,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                script,
            ],
            capture_output=True,
            check=False,
            env=environment,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as error:
        raise VoiceProviderError("local speech engine timed out") from error
    if result.returncode != 0:
        raise VoiceProviderError("local speech engine could not synthesize audio")


def _run_pyttsx3(text: str, path: Path, *, timeout: float = 20.0) -> None:
    try:
        result = subprocess.run(
            [sys.executable, "-m", "artpm_agent.voice.local_tts_runner", str(path)],
            input=text.encode("utf-8"),
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise VoiceProviderError("local speech engine timed out") from error
    if result.returncode != 0:
        raise VoiceProviderError("local speech engine could not synthesize audio")


class VoiceAudioService:
    """Transcribe final recordings and synthesize replies with fallback."""

    def __init__(
        self,
        settings: VoiceSettings | None = None,
        *,
        stt_factory: Callable[[VoiceSettings], Any] | None = None,
        tts_factories: Mapping[str, Callable[[VoiceSettings], Any]] | None = None,
        local_synthesizer: Callable[[str], SpeechAudio] | None = None,
    ) -> None:
        self.settings = settings or get_voice_settings()
        self._stt_factory = stt_factory or self._cartesia_stt
        self._tts_factories = dict(
            tts_factories
            or {
                "minimax": self._minimax_tts,
                "cartesia": self._cartesia_tts,
            }
        )
        self._local_synthesizer = local_synthesizer or self._local_tts

    def status(self) -> dict[str, Any]:
        return self.settings.public_status()

    def transcribe_wav(self, value: bytes) -> Transcript:
        if not self.settings.enabled:
            raise VoiceUnavailableError("voice channel is not enabled")
        if not self.settings.stt_configured:
            raise VoiceUnavailableError("Cartesia speech recognition is not configured")
        if not isinstance(value, (bytes, bytearray)) or not value:
            raise VoiceProviderError("recording is empty")
        if len(value) > self.settings.max_recording_bytes:
            raise VoiceProviderError("recording exceeds the configured size limit")
        pcm, sample_rate, channels = _wav_pcm(bytes(value), target_rate=16000)
        if len(pcm) < sample_rate // 5 * 2:
            raise VoiceProviderError("recording is too short to transcribe")
        try:
            engine = self._stt_factory(self.settings)
            text = _run(
                self._transcribe_stream(
                    engine,
                    pcm,
                    sample_rate=sample_rate,
                    channels=channels,
                )
            )
        except VoiceUnavailableError:
            raise
        except Exception as error:  # noqa: BLE001 - normalize provider details
            logger.warning("voice STT request failed: %s", type(error).__name__)
            raise VoiceProviderError(
                "speech recognition is temporarily unavailable"
            ) from error
        text = str(text or "").strip()
        if not text:
            raise VoiceProviderError("no speech was recognized")
        return Transcript(
            text=text,
            provider=self.settings.stt_provider,
            language=self.settings.stt_language,
        )

    async def _transcribe_stream(
        self,
        engine: Any,
        pcm: bytes,
        *,
        sample_rate: int,
        channels: int,
    ) -> str:
        try:
            from livekit import rtc
            from livekit.agents import stt
        except ImportError as error:
            raise VoiceUnavailableError(
                "voice dependencies are not installed"
            ) from error

        final_parts: list[str] = []
        stream = engine.stream(language=self.settings.stt_language)
        try:
            async with stream:
                samples_per_chunk = max(1, int(sample_rate * 0.16))
                bytes_per_chunk = samples_per_chunk * channels * 2
                for offset in range(0, len(pcm), bytes_per_chunk):
                    chunk = pcm[offset : offset + bytes_per_chunk]
                    sample_count = len(chunk) // (channels * 2)
                    if sample_count <= 0:
                        continue
                    frame = rtc.AudioFrame(
                        data=chunk,
                        sample_rate=sample_rate,
                        num_channels=channels,
                        samples_per_channel=sample_count,
                    )
                    stream.push_frame(frame)
                stream.end_input()
                async for event in stream:
                    if event.type != stt.SpeechEventType.FINAL_TRANSCRIPT:
                        continue
                    alternatives = list(event.alternatives or [])
                    if alternatives and alternatives[0].text.strip():
                        final_parts.append(alternatives[0].text.strip())
        finally:
            close = getattr(engine, "aclose", None)
            if callable(close):
                await close()
        return " ".join(final_parts).strip()

    def synthesize(self, value: str) -> SpeechAudio:
        text = speakable_text(value)
        if not text:
            raise VoiceProviderError("reply does not contain speakable text")
        if not self.settings.enabled:
            raise VoiceUnavailableError("voice channel is not enabled")

        failures: list[str] = []
        for provider in self.settings.tts_fallbacks:
            if provider == "text":
                break
            if not self.settings.tts_provider_ready(provider):
                failures.append(provider)
                continue
            try:
                if provider == "local":
                    return self._local_synthesizer(text)
                factory = self._tts_factories.get(provider)
                if factory is None:
                    failures.append(provider)
                    continue
                engine = factory(self.settings)
                return _run(self._synthesize_cloud(engine, text, provider))
            except Exception as error:  # noqa: BLE001 - continue configured fallback
                failures.append(provider)
                logger.warning(
                    "voice TTS provider %s failed: %s",
                    provider,
                    type(error).__name__,
                )
        raise VoiceUnavailableError(
            "speech output is unavailable; the text reply remains available"
        )

    async def _synthesize_cloud(
        self, engine: Any, text: str, provider: str
    ) -> SpeechAudio:
        chunks: list[bytes] = []
        sample_rate: int | None = None
        channels: int | None = None
        try:
            async with engine.synthesize(text) as stream:
                async for event in stream:
                    frame = event.frame
                    if sample_rate is None:
                        sample_rate = int(frame.sample_rate)
                        channels = int(frame.num_channels)
                    chunks.append(bytes(frame.data))
        finally:
            close = getattr(engine, "aclose", None)
            if callable(close):
                await close()
        if not chunks or sample_rate is None or channels is None:
            raise VoiceProviderError("speech provider returned no audio")
        return SpeechAudio(
            data=_pcm_wav(b"".join(chunks), sample_rate=sample_rate, channels=channels),
            mime_type="audio/wav",
            provider=provider,
            sample_rate=sample_rate,
        )

    @staticmethod
    def _cartesia_stt(settings: VoiceSettings) -> Any:
        try:
            from livekit.plugins import cartesia
        except ImportError as error:
            raise VoiceUnavailableError(
                "Cartesia voice plugin is not installed"
            ) from error
        return cartesia.STT(
            model=settings.stt_model or "ink-whisper",
            language=settings.stt_language or "zh",
            sample_rate=16000,
            api_key=settings.cartesia_api_key,
        )

    @staticmethod
    def _minimax_tts(settings: VoiceSettings) -> Any:
        try:
            from livekit.plugins import minimax
        except ImportError as error:
            raise VoiceUnavailableError(
                "MiniMax voice plugin is not installed"
            ) from error
        kwargs: dict[str, Any] = {"api_key": settings.minimax_api_key}
        if settings.tts_provider == "minimax" and settings.tts_model:
            kwargs["model"] = settings.tts_model
        if settings.tts_provider == "minimax" and settings.tts_voice_id:
            kwargs["voice"] = settings.tts_voice_id
        return minimax.TTS(**kwargs)

    @staticmethod
    def _cartesia_tts(settings: VoiceSettings) -> Any:
        try:
            from livekit.plugins import cartesia
        except ImportError as error:
            raise VoiceUnavailableError(
                "Cartesia voice plugin is not installed"
            ) from error
        kwargs: dict[str, Any] = {
            "api_key": settings.cartesia_api_key,
            "language": settings.stt_language or "zh",
        }
        if settings.tts_provider == "cartesia" and settings.tts_model:
            kwargs["model"] = settings.tts_model
        if settings.tts_provider == "cartesia" and settings.tts_voice_id:
            kwargs["voice"] = settings.tts_voice_id
        return cartesia.TTS(**kwargs)

    @staticmethod
    def _local_tts(text: str) -> SpeechAudio:
        handle, raw_path = tempfile.mkstemp(prefix="artpm-voice-", suffix=".wav")
        os.close(handle)
        path = Path(raw_path)
        path.unlink(missing_ok=True)
        try:
            if os.name == "nt":
                try:
                    _run_windows_speech(text, path)
                except VoiceUnavailableError:
                    _run_pyttsx3(text, path)
            else:
                _run_pyttsx3(text, path)
            data = path.read_bytes()
        finally:
            path.unlink(missing_ok=True)
        sample_rate, _channels = _validated_wav(data)
        return SpeechAudio(
            data=data,
            mime_type="audio/wav",
            provider="local",
            sample_rate=sample_rate,
        )


__all__ = ["VoiceAudioService", "speakable_text"]
