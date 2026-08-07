"""Serialized file transcription worker used by the Qt file picker."""

import logging
import threading
from datetime import datetime
from pathlib import Path

from config_store import app_data_dir

logger = logging.getLogger(__name__)

class FileTranscriptionWorker:
    def __init__(self, state):
        self.state = state
        self._transcriber = None
        self._lock = threading.Lock()
        self.operation_lock = getattr(state, "operation_lock", None) or threading.RLock()
        state.operation_lock = self.operation_lock
        self._thread: threading.Thread | None = None

    def _get_transcriber(self):
        if self._transcriber is None:
            from transcriber import Transcriber

            model_size = self.state.config.get("file_model", "large")
            self._transcriber = Transcriber(
                model_size=model_size,
                config=self.state.config,
                on_backend_switch=lambda message: self.state.tk_queue.put(("backend_switch", message)),
            )
        return self._transcriber

    def reload(self, new_model_size: str):
        """Перезагружает модель с новым размером (вызывается из настроек)."""
        if self._transcriber is not None:
            self._transcriber.reload(new_model_size)

    def start(self, file_path: str) -> bool:
        """Reserve the single file job before starting its worker thread."""
        with self.operation_lock, self._lock:
            machine = getattr(self.state, "dictation_state", None)
            status = getattr(getattr(machine, "status", None), "value", getattr(machine, "status", None))
            if (
                status in {"recording", "processing"}
                or self.state.is_recording.is_set()
                or self.state.is_file_transcribing.is_set()
            ):
                return False
            self.state.is_file_transcribing.set()
        self.state.tk_queue.put(("processing",))
        self._thread = threading.Thread(
            target=self._transcribe_in_background,
            args=(file_path,),
            daemon=False,
            name="FileTranscribeThread",
        )
        try:
            self._thread.start()
        except Exception:
            self.state.is_file_transcribing.clear()
            raise
        return True

    def _transcribe_in_background(self, file_path: str):
        try:
            self._run_transcription(file_path)
        finally:
            self.state.is_file_transcribing.clear()

    def _run_transcription(self, file_path: str):
        try:
            text = self._get_transcriber().transcribe_file(
                file_path,
                language=self.state.config.get("language"),
            )
        except Exception as e:
            logger.error(f"Ошибка транскрипции файла: {e}")
            self.state.tk_queue.put(("error", "Не удалось транскрибировать файл."))
            if self.state.tray_app:
                self.state.tray_app.notify("Ошибка", f"Не удалось транскрибировать: {e}")
            return
        if not text:
            logger.info("Файловая транскрипция вернула пустой текст")
            self.state.tk_queue.put(("error", "В выбранном файле не обнаружена речь."))
            if self.state.tray_app:
                self.state.tray_app.notify("WhisperTray", "В выбранном файле не обнаружена речь.")
            return

        out = self._save_to_output(file_path, text)
        self.state.tk_queue.put(("idle",))
        if self.state.tray_app:
            if out:
                self.state.tray_app.notify("Транскрибировано успешно", f"Файл: {out}")
            else:
                self.state.tray_app.notify("Ошибка", "Не удалось сохранить результат транскрипции.")

    def _save_to_output(self, source_path: str, text: str) -> Path | None:
        try:
            out_dir = app_data_dir() / "Output"
            out_dir.mkdir(exist_ok=True)
            stem = Path(source_path).stem
            ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            out_file = out_dir / f"{stem}_{ts}.txt"
            out_file.write_text(text, encoding="utf-8")
            logger.info(f"Транскрипт сохранён: {out_file}")
            return out_file
        except Exception as e:
            logger.error(f"Ошибка сохранения транскрипта: {e}")
            return None

    def shutdown(self, timeout: float = 5.0) -> None:
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout)
