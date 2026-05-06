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
        self._force_cpu = False

    def _ensure_model(self):
        """Загружает модель если ещё не загружена"""
        if self._model is not None:
            return
        logger.info(f"Загрузка модели Whisper '{self.model_size}'...")
        try:
            from faster_whisper import WhisperModel
            if not self._force_cpu:
                try:
                    model = WhisperModel(
                        self.model_size,
                        device='cuda',
                        compute_type='float16',
                        download_root=None,
                    )
                    # Warm-up: проверяем что cuBLAS реально доступен до первого вызова
                    _dummy = np.zeros(1600, dtype=np.float32)
                    list(model.transcribe(_dummy, beam_size=1)[0])
                    self._model = model
                    logger.info(f"Модель '{self.model_size}' загружена на GPU (CUDA float16)")
                    return
                except Exception as cuda_err:
                    logger.warning(f"CUDA недоступна ({cuda_err}), использую CPU")
                    self._force_cpu = True
            self._model = WhisperModel(
                self.model_size,
                device='cpu',
                compute_type='int8',
                download_root=None,
            )
            logger.info(f"Модель '{self.model_size}' загружена на CPU (int8)")
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
            language=language,
            beam_size=1,
            best_of=1,
            condition_on_previous_text=False,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=300),
        )
        parts = [seg.text.strip() for seg in segments]

        logger.info(
            f"Язык: {info.language} "
            f"(вероятность {info.language_probability:.2f})"
        )

        raw_text = ' '.join(parts)
        result = normalize_text(raw_text)
        logger.info(f"Транскрипт: {result!r}")
        return result

    def transcribe_file(self, file_path, language=None) -> str:
        """Транскрибирует аудиофайл по пути (faster-whisper читает через ffmpeg)."""
        self._ensure_model()
        path_str = str(file_path)
        params = dict(
            language=language,
            beam_size=5,
            best_of=5,
            condition_on_previous_text=True,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
        )
        segments, info = self._model.transcribe(path_str, **params)
        parts = [seg.text.strip() for seg in segments]
        logger.info(f"Язык файла: {info.language} ({info.language_probability:.2f})")
        return normalize_text(' '.join(parts))
