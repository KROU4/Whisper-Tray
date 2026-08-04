"""Transcription backends for WhisperTray.

Groq Cloud is the default backend. Local faster-whisper remains available and
is used automatically as a fallback when Groq is not configured or fails.
"""

from __future__ import annotations

import logging
import os
import time
import wave
from io import BytesIO
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

GROQ_BACKEND = "groq"
LOCAL_BACKEND = "local"
DEFAULT_GROQ_MODEL = "whisper-large-v3-turbo"

_PUNCT_END = frozenset(".!?...")


def normalize_text(text: str) -> str:
    text = text.strip()
    if not text:
        return text
    text = text[0].upper() + text[1:]
    if text[-1] not in _PUNCT_END:
        text += "."
    return text


class GroqTranscriptionError(Exception):
    pass


def _audio_to_wav_bytes(audio: np.ndarray, sample_rate: int = 16000) -> bytes:
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio[:, 0]

    audio = np.clip(audio, -1.0, 1.0)
    pcm = (audio * 32767).astype(np.int16)

    buf = BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())
    return buf.getvalue()


class Transcriber:
    def __init__(self, model_size: str = "small", config: dict | None = None):
        self.model_size = model_size
        self.config = config or {}
        self._model = None
        self._groq_client = None
        self._groq_client_key = None
        self._force_cpu = False

    def _backend(self) -> str:
        backend = self.config.get("transcription_backend", GROQ_BACKEND)
        if backend in {GROQ_BACKEND, LOCAL_BACKEND}:
            return backend
        return GROQ_BACKEND

    def _groq_api_key(self) -> str:
        return (self.config.get("groq_api_key") or os.environ.get("GROQ_API_KEY") or "").strip()

    def _get_groq_client(self):
        api_key = self._groq_api_key()
        if self._groq_client is not None and self._groq_client_key == api_key:
            return self._groq_client

        if not api_key:
            raise GroqTranscriptionError("GROQ_API_KEY is not configured")

        try:
            from groq import Groq
        except Exception as exc:
            raise GroqTranscriptionError("groq package is not installed") from exc

        self._groq_client = Groq(api_key=api_key)
        self._groq_client_key = api_key
        return self._groq_client

    def _transcribe_groq_bytes(self, audio_bytes: bytes, filename: str, language=None) -> str:
        client = self._get_groq_client()
        model = self.config.get("groq_model", DEFAULT_GROQ_MODEL)
        prompt = self.config.get("groq_prompt") or None
        max_retries = int(self.config.get("groq_max_retries", 4))
        delay = 1.0

        for attempt in range(max_retries + 1):
            try:
                start = time.monotonic()
                transcription = client.audio.transcriptions.create(
                    file=(filename, audio_bytes),
                    model=model,
                    response_format="verbose_json",
                    language=language or None,
                    prompt=prompt,
                    temperature=0.0,
                )
                elapsed = time.monotonic() - start
                text = normalize_text(getattr(transcription, "text", "") or "")
                logger.info(
                    "Groq transcribed %s (%.1f KB) in %.2fs",
                    filename,
                    len(audio_bytes) / 1024,
                    elapsed,
                )
                return text
            except Exception as exc:
                status_code = getattr(exc, "status_code", None)
                is_retryable = status_code == 429 or exc.__class__.__name__ == "APIConnectionError"
                if is_retryable and attempt < max_retries:
                    logger.warning(
                        "Groq transcription retry %d/%d after %.1fs: %s",
                        attempt + 1,
                        max_retries,
                        delay,
                        exc,
                    )
                    time.sleep(delay)
                    delay = min(delay * 2, 30.0)
                    continue
                raise GroqTranscriptionError(f"Groq transcription failed: {exc}") from exc

        raise GroqTranscriptionError("Groq transcription failed after all retries")

    def _ensure_model(self):
        if self._model is not None:
            return

        logger.info("Loading local Whisper model '%s'...", self.model_size)
        try:
            from faster_whisper import WhisperModel

            if not self._force_cpu:
                try:
                    model = WhisperModel(
                        self.model_size,
                        device="cuda",
                        compute_type="float16",
                        download_root=None,
                    )
                    dummy = np.zeros(1600, dtype=np.float32)
                    list(model.transcribe(dummy, beam_size=1)[0])
                    self._model = model
                    logger.info("Local Whisper model '%s' loaded on CUDA float16", self.model_size)
                    return
                except Exception as cuda_err:
                    logger.warning("CUDA is unavailable (%s), using CPU", cuda_err)
                    self._force_cpu = True

            self._model = WhisperModel(
                self.model_size,
                device="cpu",
                compute_type="int8",
                download_root=None,
            )
            logger.info("Local Whisper model '%s' loaded on CPU int8", self.model_size)
        except Exception:
            logger.exception("Could not load local Whisper model")
            raise

    def reload(self, model_size: str):
        self.model_size = model_size
        self._model = None
        logger.info("Local Whisper model will be reloaded: %s", model_size)

    def _transcribe_local_audio(self, audio: np.ndarray, language=None) -> str:
        self._ensure_model()
        segments, info = self._model.transcribe(
            audio,
            language=language,
            beam_size=1,
            best_of=1,
            condition_on_previous_text=False,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=300),
        )
        parts = [seg.text.strip() for seg in segments]
        logger.info("Local audio language: %s (%.2f)", info.language, info.language_probability)
        return normalize_text(" ".join(parts))

    def _transcribe_local_file(self, file_path, language=None) -> str:
        self._ensure_model()
        segments, info = self._model.transcribe(
            str(file_path),
            language=language,
            beam_size=5,
            best_of=5,
            condition_on_previous_text=True,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
        )
        parts = [seg.text.strip() for seg in segments]
        logger.info("Local file language: %s (%.2f)", info.language, info.language_probability)
        return normalize_text(" ".join(parts))

    def transcribe(self, audio: np.ndarray, language=None) -> str:
        if self._backend() == GROQ_BACKEND:
            try:
                audio_bytes = _audio_to_wav_bytes(audio)
                return self._transcribe_groq_bytes(audio_bytes, "recording.wav", language=language)
            except GroqTranscriptionError as exc:
                logger.warning("Groq backend failed, falling back to local Whisper: %s", exc)
        return self._transcribe_local_audio(audio, language=language)

    def transcribe_file(self, file_path, language=None) -> str:
        if self._backend() == GROQ_BACKEND:
            try:
                path = Path(file_path)
                return self._transcribe_groq_bytes(path.read_bytes(), path.name or "audio.wav", language=language)
            except GroqTranscriptionError as exc:
                logger.warning("Groq file backend failed, falling back to local Whisper: %s", exc)
        return self._transcribe_local_file(file_path, language=language)
