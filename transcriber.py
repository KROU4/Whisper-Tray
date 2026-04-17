"""
Обёртка над faster-whisper для транскрипции аудио.
Ленивая загрузка модели при первом вызове.
"""
import logging
import numpy as np

logger = logging.getLogger(__name__)

# Знаки препинания, которые считаются "концом предложения"
_PUNCT_END = frozenset('.!?…')


def normalize_text(text: str) -> str:
    """
    Нормализует транскрипт:
    - strip пробелов
    - первая буква заглавная
    - добавляет точку если нет знака препинания в конце
    """
    text = text.strip()
    if not text:
        return text
    text = text[0].upper() + text[1:]
    if text[-1] not in _PUNCT_END:
        text += '.'
    return text


class Transcriber:
    def __init__(self, model_size: str = 'small'):
        self.model_size = model_size
        self._model = None  # Загружается при первом вызове

    def _ensure_model(self):
        """Загружает модель если ещё не загружена"""
        if self._model is not None:
            return
        logger.info(f"Загрузка модели Whisper '{self.model_size}'...")
        try:
            from faster_whisper import WhisperModel
            self._model = WhisperModel(
                self.model_size,
                device='cpu',
                compute_type='int8',      # Сжатие для ускорения на CPU
                download_root=None,       # Кеш в ~/.cache/huggingface/
            )
            logger.info(f"Модель '{self.model_size}' загружена")
        except Exception as e:
            logger.error(f"Не удалось загрузить модель: {e}")
            raise

    def reload(self, model_size: str):
        """Сбрасывает модель для перезагрузки с новым размером"""
        self.model_size = model_size
        self._model = None
        logger.info(f"Модель будет перезагружена: {model_size}")

    def transcribe(self, audio: np.ndarray, language=None) -> str:
        """
        Транскрибирует numpy-массив float32 (16 кГц, моно).
        Возвращает нормализованную строку.
        """
        self._ensure_model()

        segments, info = self._model.transcribe(
            audio,
            language=language,          # None = автодетект
            beam_size=5,
            vad_filter=True,            # Фильтрация тишины через VAD
            vad_parameters=dict(min_silence_duration_ms=300),
        )

        logger.info(
            f"Язык: {info.language} "
            f"(вероятность {info.language_probability:.2f})"
        )

        # Собираем текст из сегментов (faster-whisper возвращает генератор)
        parts = []
        for seg in segments:
            parts.append(seg.text.strip())

        raw_text = ' '.join(parts)
        result = normalize_text(raw_text)
        logger.info(f"Транскрипт: {result!r}")
        return result
