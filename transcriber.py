"""Explicit privacy-local and user-selected Groq transcription backends."""

from __future__ import annotations

import logging
import time
import wave
from email.utils import parsedate_to_datetime
from io import BytesIO
from pathlib import Path

import numpy as np

from core import BackendError, Profile
from credentials import CredentialStore

logger = logging.getLogger(__name__)

GROQ_BACKEND = "groq"
LOCAL_BACKEND = "local"
DEFAULT_GROQ_MODEL = "whisper-large-v3-turbo"
CLOUD_MAX_BYTES = 25_000_000
CLOUD_FORMATS = frozenset({".flac", ".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".ogg", ".wav", ".webm"})

_PUNCT_END = frozenset(".!?...")


def normalize_text(text: str) -> str:
    text = text.strip()
    if not text:
        return text
    text = text[0].upper() + text[1:]
    if text[-1] not in _PUNCT_END:
        text += "."
    return text


class GroqTranscriptionError(BackendError):
    pass


def validate_cloud_file(file_path) -> Path:
    path = Path(file_path)
    try:
        if not path.is_file():
            raise OSError("not a file")
        size = path.stat().st_size
    except OSError as exc:
        raise BackendError("file_missing", "The selected file is unavailable.") from exc
    if path.suffix.lower() not in CLOUD_FORMATS:
        raise BackendError("file_format", "This format requires local recognition.")
    if size > CLOUD_MAX_BYTES:
        raise BackendError("file_too_large", "Cloud uploads are limited to 25 MB. Choose local recognition.")
    if size == 0:
        raise BackendError("file_empty", "The selected file is empty.")
    return path


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
    def __init__(
        self,
        model_size: str = "small",
        config: dict | None = None,
        credentials: CredentialStore | None = None,
        on_backend_switch=None,
        on_progress=None,
        cancelled=None,
    ):
        self.model_size = model_size
        self.config = config or {}
        self._model = None
        self._groq_client = None
        self._groq_client_key = None
        self._force_cpu = False
        self.credentials = credentials or CredentialStore()
        self.on_backend_switch = on_backend_switch
        self.on_progress = on_progress
        self.cancelled = cancelled or (lambda: False)

    def _checkpoint(self):
        if self.cancelled():
            raise BackendError("cancelled", "Operation cancelled.")

    def _progress(self, stage, progress=None):
        self._checkpoint()
        if self.on_progress:
            self.on_progress(stage, progress)

    def prepare(self):
        self._ensure_model()
        self._progress("ready", 100)

    def _backend(self) -> str:
        # Privacy is a hard contract: it can never use a network backend.
        if self.config.get("profile", Profile.PRIVACY.value) == Profile.PRIVACY.value:
            return LOCAL_BACKEND
        return GROQ_BACKEND if self.config.get("profile") == Profile.SPEED.value else LOCAL_BACKEND

    def _groq_api_key(self) -> str:
        return self.credentials.get_groq_key()

    def _get_groq_client(self):
        api_key = self._groq_api_key()
        if self._groq_client is not None and self._groq_client_key == api_key:
            return self._groq_client

        if not api_key:
            raise GroqTranscriptionError("cloud_key_missing", "Groq API key is not configured")

        try:
            from groq import Groq
        except Exception as exc:
            raise GroqTranscriptionError("cloud_unavailable", "Groq support is not installed") from exc

        self._groq_client = Groq(api_key=api_key, timeout=60.0, max_retries=0)
        self._groq_client_key = api_key
        return self._groq_client

    def _transcribe_groq_bytes(self, audio_bytes: bytes, filename: str, language=None) -> str:
        client = self._get_groq_client()
        model = self.config.get("groq_model", DEFAULT_GROQ_MODEL)
        prompt = self.config.get("groq_prompt") or None
        from config_store import bounded_int

        max_retries = bounded_int(self.config.get("groq_max_retries"), 2, 0, 2)
        deadline = time.monotonic() + 180.0

        for attempt in range(max_retries + 1):
            self._checkpoint()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise GroqTranscriptionError("cloud_timeout", "Cloud transcription timed out.", retryable=True)
            try:
                self._progress("cloud_transcribing")
                start = time.monotonic()
                transcription = client.audio.transcriptions.create(
                    file=(filename, audio_bytes),
                    model=model,
                    response_format="verbose_json",
                    language=language or None,
                    prompt=prompt,
                    temperature=0.0,
                    timeout=min(60.0, remaining),
                )
                self._checkpoint()
                if time.monotonic() > deadline:
                    raise GroqTranscriptionError("cloud_timeout", "Cloud transcription timed out.", retryable=True)
                elapsed = time.monotonic() - start
                text = normalize_text(getattr(transcription, "text", "") or "")
                logger.info("Groq transcription completed in %.2fs", elapsed)
                return text
            except BackendError:
                raise
            except Exception as exc:
                status_code = getattr(exc, "status_code", None)
                name = exc.__class__.__name__
                is_retryable = status_code in (408, 409, 429) or (isinstance(status_code, int) and status_code >= 500) or name in ("APIConnectionError", "APITimeoutError")
                if is_retryable and attempt < max_retries:
                    delay = self._retry_delay(exc, attempt)
                    if time.monotonic() + delay >= deadline:
                        raise GroqTranscriptionError("cloud_timeout", "Cloud transcription timed out.", retryable=True) from exc
                    logger.warning("Groq retry %d/%d: status=%s", attempt + 1, max_retries, status_code)
                    self._progress("retry_wait")
                    until = time.monotonic() + delay
                    while time.monotonic() < until:
                        self._checkpoint()
                        time.sleep(min(0.1, max(0, until - time.monotonic())))
                    continue
                if status_code in (401, 403):
                    raise GroqTranscriptionError("cloud_auth", "Groq API key was rejected") from exc
                if status_code == 429:
                    raise GroqTranscriptionError(
                        "cloud_rate_limit", "Groq request limit reached", retryable=True
                    ) from exc
                if name == "APITimeoutError":
                    raise GroqTranscriptionError("cloud_timeout", "Cloud transcription timed out.", retryable=True) from exc
                if name == "APIConnectionError":
                    raise GroqTranscriptionError(
                        "network", "Network connection to Groq failed", retryable=True
                    ) from exc
                raise GroqTranscriptionError("cloud_failed", "Groq transcription failed") from exc

        raise GroqTranscriptionError("cloud_failed", "Groq transcription failed after all retries")

    @staticmethod
    def _retry_delay(exc, attempt):
        headers = getattr(getattr(exc, "response", None), "headers", {}) or {}
        value = headers.get("retry-after")
        if value is not None:
            try:
                return min(30.0, max(0.0, float(value)))
            except (ValueError, TypeError):
                try:
                    return min(30.0, max(0.0, parsedate_to_datetime(value).timestamp() - time.time()))
                except (ValueError, TypeError, OverflowError):
                    pass
        return min(2.0 ** attempt, 30.0)

    def _ensure_model(self):
        if self._model is not None:
            return
        self._checkpoint()
        logger.info("Loading local Whisper model '%s'...", self.model_size)
        try:
            from faster_whisper import WhisperModel
            from faster_whisper.utils import download_model

            try:
                model_path = download_model(self.model_size, local_files_only=True)
                if not (Path(model_path) / "model.bin").is_file():
                    raise FileNotFoundError("Incomplete model cache")
            except Exception:
                self._progress("downloading_model")
                model_path = download_model(self.model_size)
            self._progress("loading_model")

            if not self._force_cpu:
                try:
                    model = WhisperModel(
                        model_path,
                        device="cuda",
                        compute_type="float16",
                        download_root=None,
                    )
                    dummy = np.zeros(1600, dtype=np.float32)
                    list(model.transcribe(dummy, beam_size=1)[0])
                    self._model = model
                    logger.info("Local Whisper model '%s' loaded on CUDA float16", self.model_size)
                    return
                except Exception:
                    logger.info("CUDA is unavailable; using CPU")
                    self._force_cpu = True

            self._model = WhisperModel(
                model_path,
                device="cpu",
                compute_type="int8",
                download_root=None,
            )
            logger.info("Local Whisper model '%s' loaded on CPU int8", self.model_size)
        except BackendError:
            raise
        except Exception as exc:
            logger.error("Could not load local Whisper model (%s)", type(exc).__name__)
            raise BackendError("local_model_missing", "Local Whisper model is unavailable") from exc

    def reload(self, model_size: str):
        self.model_size = model_size
        self._model = None
        logger.info("Local Whisper model will be reloaded: %s", model_size)

    def _transcribe_local_audio(self, audio: np.ndarray | str | Path, language=None) -> str:
        self._ensure_model()
        self._progress("transcribing")
        # Greedy decoding regressed short Russian phrases on tiny in the QA corpus.
        beam = 2 if self.model_size in {"tiny", "base"} else 1
        segments, info = self._model.transcribe(
            str(audio) if isinstance(audio, Path) else audio,
            language=language,
            beam_size=beam,
            best_of=beam,
            condition_on_previous_text=False,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300},
        )
        parts = self._collect_segments(segments, info)
        logger.info("Local audio language: %s (%.2f)", info.language, info.language_probability)
        return normalize_text(" ".join(parts))

    def _transcribe_local_file(self, file_path, language=None) -> str:
        self._ensure_model()
        self._progress("transcribing")
        segments, info = self._model.transcribe(
            str(file_path),
            language=language,
            beam_size=5,
            best_of=5,
            condition_on_previous_text=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        parts = self._collect_segments(segments, info)
        logger.info("Local file language: %s (%.2f)", info.language, info.language_probability)
        return normalize_text(" ".join(parts))

    def _collect_segments(self, segments, info):
        parts = []
        for segment in segments:
            self._checkpoint()
            parts.append(segment.text.strip())
            duration = getattr(info, "duration", 0)
            if duration:
                self._progress("transcribing", min(99, int(segment.end / duration * 100)))
        self._checkpoint()
        return parts

    def _can_fallback_locally(self) -> bool:
        try:
            self._ensure_model()
            return True
        except BackendError as exc:
            if exc.code == "cancelled":
                raise
            return False

    def transcribe(self, audio: np.ndarray | str | Path, language=None) -> str:
        """Transcribe only through the selected profile; fallback is explicit and local."""
        self._checkpoint()
        if self._backend() == GROQ_BACKEND:
            cloud_bytes = self._read_cloud_file(audio) if isinstance(audio, (str, Path)) else _audio_to_wav_bytes(audio)
            try:
                return self._transcribe_groq_bytes(cloud_bytes, "recording.wav", language=language)
            except GroqTranscriptionError as exc:
                if exc.code == "cancelled" or not self.config.get("allow_local_fallback", False) or not self._can_fallback_locally():
                    raise
                logger.warning("Cloud backend failed; using configured local fallback (%s)", exc.code)
                if self.on_backend_switch:
                    self.on_backend_switch("Groq is unavailable; switching to the selected local model.")
        return self._transcribe_local_audio(audio, language=language)

    def _read_cloud_file(self, path):
        path = validate_cloud_file(path)
        self._progress("reading_file")
        with path.open("rb") as source:
            data = source.read(CLOUD_MAX_BYTES + 1)
        if len(data) > CLOUD_MAX_BYTES:
            raise BackendError("file_too_large", "Cloud uploads are limited to 25 MB.")
        self._checkpoint()
        return data

    def transcribe_file(self, file_path, language=None) -> str:
        self._checkpoint()
        if self._backend() == GROQ_BACKEND:
            data = self._read_cloud_file(file_path)
            try:
                path = Path(file_path)
                return self._transcribe_groq_bytes(data, path.name or "audio.wav", language=language)
            except GroqTranscriptionError as exc:
                if not self.config.get("allow_local_fallback", False) or not self._can_fallback_locally():
                    raise
                logger.warning("Cloud file backend failed; using configured local fallback (%s)", exc.code)
                if self.on_backend_switch:
                    self.on_backend_switch("Groq is unavailable; switching the file job to local Whisper.")
        return self._transcribe_local_file(file_path, language=language)
