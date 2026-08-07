"""Bounded microphone recording backed by a temporary WAV file."""

from __future__ import annotations

import logging
import tempfile
import threading
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)
SAMPLE_RATE = 16000
CHANNELS = 1
DTYPE = "float32"
MAX_DURATION_SECONDS = 10 * 60
WARNING_SECONDS = 30


class RecordingError(RuntimeError):
    pass


class AudioRecorder:
    """Records PCM to disk, keeping at most one bounded dictation in memory."""

    def __init__(
        self, device_index=None, max_duration_seconds: int = MAX_DURATION_SECONDS, on_warning=None, on_limit=None
    ):
        self.device_index = device_index
        self.max_duration_seconds = max_duration_seconds
        self.on_warning = on_warning
        self.on_limit = on_limit
        self._stream: sd.InputStream | None = None
        self._wav: wave.Wave_write | None = None
        self._path: Path | None = None
        self._frames = 0
        self._warned = False
        self._lock = threading.RLock()

    @property
    def path(self) -> Path | None:
        return self._path

    def _callback(self, indata, frames, time_info, status):
        if status:
            logger.warning("sounddevice status: %s", status)
        with self._lock:
            if self._wav is None:
                return
            remaining = self.max_duration_seconds * SAMPLE_RATE - self._frames
            if remaining <= 0:
                return
            usable = min(frames, remaining)
            pcm = (np.clip(indata[:usable, 0], -1.0, 1.0) * 32767).astype(np.int16)
            self._wav.writeframes(pcm.tobytes())
            self._frames += usable
            seconds = self._frames / SAMPLE_RATE
            if not self._warned and seconds >= self.max_duration_seconds - WARNING_SECONDS:
                self._warned = True
                if self.on_warning:
                    self.on_warning("Recording limit will be reached in 30 seconds")
            if self._frames >= self.max_duration_seconds * SAMPLE_RATE and self._stream is not None:
                # The callback must not close the stream; the state owner stops it safely.
                logger.warning("Maximum recording duration reached")
                if self.on_limit:
                    callback, self.on_limit = self.on_limit, None
                    callback()

    def start(self):
        try:
            with self._lock:
                if self._stream is not None:
                    raise RecordingError("Recording is already active")
                file = tempfile.NamedTemporaryFile(prefix="whispertray-", suffix=".wav", delete=False)
                file.close()
                self._path = Path(file.name)
                self._frames, self._warned = 0, False
                self._wav = wave.open(str(self._path), "wb")
                self._wav.setnchannels(CHANNELS)
                self._wav.setsampwidth(2)
                self._wav.setframerate(SAMPLE_RATE)
                self._stream = sd.InputStream(
                    samplerate=SAMPLE_RATE,
                    channels=CHANNELS,
                    dtype=DTYPE,
                    device=self.device_index,
                    callback=self._callback,
                    blocksize=1024,
                )
                stream = self._stream
        except RecordingError:
            raise
        except Exception as exc:
            self.cleanup()
            raise RecordingError("Microphone is unavailable") from exc
        try:
            stream.start()
        except Exception as exc:
            self.cleanup()
            raise RecordingError("Microphone is unavailable") from exc

    def stop(self):
        self._close_resources()

    def _close_resources(self):
        # Never wait for PortAudio while holding the callback's write lock.
        with self._lock:
            stream = self._stream
            self._stream = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                logger.exception("Could not stop the audio stream cleanly")
        with self._lock:
            wav_file = self._wav
            self._wav = None
        if wav_file is not None:
            wav_file.close()

    def get_audio(self) -> np.ndarray:
        """Compatibility adapter for local Whisper. Cloud can use ``path`` directly."""
        if not self._path or not self._path.exists():
            return np.zeros(0, dtype=DTYPE)
        with wave.open(str(self._path), "rb") as file:
            frames = np.frombuffer(file.readframes(file.getnframes()), dtype=np.int16)
        return frames.astype(np.float32) / 32767.0

    def cleanup(self):
        self._close_resources()
        with self._lock:
            if self._path:
                try:
                    self._path.unlink(missing_ok=True)
                except OSError:
                    logger.warning("Could not remove temporary recording")
                self._path = None

    def shutdown(self):
        self.cleanup()
