"""
Запись аудио с микрофона в оперативную память.
Использует sounddevice.InputStream, накапливает чанки numpy-массивов.
"""
import logging
import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000   # Whisper ожидает 16 кГц
CHANNELS = 1          # Моно
DTYPE = 'float32'     # Формат для faster-whisper


class AudioRecorder:
    def __init__(self, device_index=None):
        self.device_index = device_index
        self.chunks: list = []
        self._stream: sd.InputStream | None = None

    def _callback(self, indata, frames, time_info, status):
        """Вызывается sounddevice при получении новых аудиоданных"""
        if status:
            logger.warning(f"sounddevice статус: {status}")
        self.chunks.append(indata.copy())

    def start(self):
        """Начинает запись. Поднимает исключение если микрофон недоступен."""
        self.chunks = []
        try:
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=DTYPE,
                device=self.device_index,
                callback=self._callback,
                blocksize=1024,
            )
            self._stream.start()
            logger.info(f"Запись начата (устройство: {self.device_index})")
        except Exception as e:
            logger.error(f"Ошибка запуска записи: {e}")
            self._stream = None
            raise

    def stop(self):
        """Останавливает запись и закрывает поток"""
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                logger.error(f"Ошибка остановки потока: {e}")
            finally:
                self._stream = None
        logger.info(f"Запись остановлена, чанков: {len(self.chunks)}")

    def get_audio(self) -> np.ndarray:
        """Склеивает все чанки в единый 1D массив float32"""
        if not self.chunks:
            # Возвращаем секунду тишины чтобы не падать в Whisper
            return np.zeros(SAMPLE_RATE, dtype=DTYPE)
        audio = np.concatenate(self.chunks, axis=0)
        # Убираем канальное измерение: (N, 1) → (N,)
        if audio.ndim > 1:
            audio = audio[:, 0]
        return audio
