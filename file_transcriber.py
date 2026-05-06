"""Транскрипция аудиофайлов с диска. Сохраняет результат в Output/."""
import logging
import threading
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

AUDIO_FILETYPES = [
    ('Аудио файлы', '*.mp3 *.wav *.m4a *.ogg *.flac *.aac *.wma *.opus'),
    ('Видео файлы', '*.mp4 *.mkv *.webm *.avi *.mov'),
    ('Все файлы',   '*.*'),
]


class FileTranscriptionWorker:
    def __init__(self, state):
        self.state = state
        self._transcriber = None

    def _get_transcriber(self):
        if self._transcriber is None:
            from transcriber import Transcriber
            model_size = self.state.config.get('file_model', 'large')
            self._transcriber = Transcriber(model_size=model_size)
        return self._transcriber

    def reload(self, new_model_size: str):
        """Перезагружает модель с новым размером (вызывается из настроек)."""
        if self._transcriber is not None:
            self._transcriber.reload(new_model_size)

    def open_file_dialog(self, root_tk):
        """Вызывается из TkLoop. Открывает диалог и запускает фоновый поток."""
        if self.state.is_recording.is_set():
            if self.state.tray_app:
                self.state.tray_app.notify('Занято', 'Дождитесь окончания записи.')
            return
        if self.state.is_file_transcribing.is_set():
            if self.state.tray_app:
                self.state.tray_app.notify('Занято', 'Транскрипция файла уже выполняется.')
            return

        from tkinter import filedialog
        file_path = filedialog.askopenfilename(
            title='Выберите аудиофайл для транскрипции',
            filetypes=AUDIO_FILETYPES,
            parent=root_tk,
        )
        if not file_path:
            return

        self.state.tk_queue.put(('processing',))
        threading.Thread(
            target=self._transcribe_in_background,
            args=(file_path,),
            daemon=True,
            name='FileTranscribeThread',
        ).start()

    def _transcribe_in_background(self, file_path: str):
        self.state.is_file_transcribing.set()
        try:
            text = self._get_transcriber().transcribe_file(
                file_path,
                language=self.state.config.get('language'),
            )
        except Exception as e:
            logger.error(f"Ошибка транскрипции файла: {e}")
            self.state.tk_queue.put(('hide_hud',))
            if self.state.tray_app:
                self.state.tray_app.notify('Ошибка', f'Не удалось транскрибировать: {e}')
            return
        finally:
            self.state.is_file_transcribing.clear()

        if not text:
            logger.info("Файловая транскрипция вернула пустой текст")
            self.state.tk_queue.put(('hide_hud',))
            return

        out = self._save_to_output(file_path, text)
        self.state.tk_queue.put(('hide_hud',))
        if self.state.tray_app and out:
            self.state.tray_app.notify('Транскрибировано успешно', f'Файл: {out.name}')

    def _save_to_output(self, source_path: str, text: str) -> Path | None:
        try:
            out_dir = Path(__file__).parent / 'Output'
            out_dir.mkdir(exist_ok=True)
            stem = Path(source_path).stem
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            out_file = out_dir / f"{stem}_{ts}.txt"
            out_file.write_text(text, encoding='utf-8')
            logger.info(f"Транскрипт сохранён: {out_file}")
            return out_file
        except Exception as e:
            logger.error(f"Ошибка сохранения транскрипта: {e}")
            return None
