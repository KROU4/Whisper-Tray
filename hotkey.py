"""
Глобальный перехват хоткея (по умолчанию Win+H).
Управляет циклом запись → транскрипция → вставка.
"""
import logging
import threading
import time
import numpy as np

logger = logging.getLogger(__name__)


class HotkeyListener:
    """
    Регистрирует глобальный хоткей через библиотеку keyboard.
    Запускается в отдельном daemon-потоке методом run().
    """

    def __init__(self, state):
        self.state = state
        self._recorder = None
        self._transcriber = None
        self._current_hotkey: str | None = None

    # ------------------------------------------------------------------
    # Ленивая инициализация тяжёлых компонентов
    # ------------------------------------------------------------------

    def _get_recorder(self):
        if self._recorder is None:
            from recorder import AudioRecorder
            self._recorder = AudioRecorder(
                device_index=self.state.config.get('device_index')
            )
        return self._recorder

    def _get_transcriber(self):
        if self._transcriber is None:
            from transcriber import Transcriber
            self._transcriber = Transcriber(
                model_size=self.state.config.get('model', 'small')
            )
        return self._transcriber

    # ------------------------------------------------------------------
    # Обработчик хоткея
    # ------------------------------------------------------------------

    def on_hotkey(self):
        """Переключает между началом и концом записи"""
        if not self.state.is_recording.is_set():
            self._start_recording()
        else:
            self._stop_and_transcribe()

    # ------------------------------------------------------------------
    # Запись
    # ------------------------------------------------------------------

    def _start_recording(self):
        recorder = self._get_recorder()
        logger.info("Начало записи")
        try:
            recorder.start()
        except Exception as e:
            logger.error(f"Не удалось открыть микрофон: {e}")
            if self.state.tray_app:
                self.state.tray_app.notify('Ошибка', f'Микрофон недоступен: {e}')
            return

        self.state.is_recording.set()
        self.state.tk_queue.put(('show_hud',))

    def _stop_and_transcribe(self):
        self.state.is_recording.clear()
        self.state.tk_queue.put(('processing',))

        recorder = self._get_recorder()
        try:
            recorder.stop()
            audio = recorder.get_audio()
        except Exception as e:
            logger.error(f"Ошибка при остановке записи: {e}")
            self.state.tk_queue.put(('hide_hud',))
            return

        # Транскрипция в отдельном потоке, чтобы не блокировать хоткей
        t = threading.Thread(
            target=self._transcribe_and_paste,
            args=(audio,),
            daemon=True,
            name="TranscribeThread",
        )
        t.start()

    # ------------------------------------------------------------------
    # Транскрипция и вставка
    # ------------------------------------------------------------------

    def _transcribe_and_paste(self, audio: np.ndarray):
        try:
            transcriber = self._get_transcriber()
            text = transcriber.transcribe(
                audio,
                language=self.state.config.get('language'),
            )
        except Exception as e:
            logger.error(f"Ошибка транскрипции: {e}")
            self.state.tk_queue.put(('hide_hud',))
            if self.state.tray_app:
                self.state.tray_app.notify(
                    'Ошибка', f'Не удалось транскрибировать: {e}'
                )
            return

        if not text:
            logger.info("Транскрипт пустой — ничего не вставляем")
            self.state.tk_queue.put(('hide_hud',))
            return

        self._paste_text(text)

    def _paste_text(self, text: str):
        """
        Вставляет текст через буфер обмена:
        1. Сохраняет оригинальный буфер
        2. Копирует транскрипт
        3. Нажимает Ctrl+V
        4. Восстанавливает буфер через 500ms
        """
        import pyperclip
        import keyboard as kb

        logger.info(f"Вставка: {text!r}")

        # Сохраняем текущий буфер обмена
        try:
            original = pyperclip.paste()
        except Exception:
            original = ''

        try:
            pyperclip.copy(text)
            time.sleep(0.15)    # Пауза чтобы фокус вернулся к целевому окну
            kb.send('ctrl+v')
            logger.info("Ctrl+V отправлен")
        except Exception as e:
            logger.error(f"Ошибка вставки текста: {e}")
        finally:
            self.state.tk_queue.put(('hide_hud',))

        # Восстанавливаем оригинальный буфер через 500ms в фоне
        def restore_clipboard():
            time.sleep(0.5)
            try:
                pyperclip.copy(original)
            except Exception:
                pass

        threading.Thread(target=restore_clipboard, daemon=True).start()

    # ------------------------------------------------------------------
    # Запуск и перезагрузка
    # ------------------------------------------------------------------

    def run(self):
        """
        Блокирующий цикл: регистрирует хоткей и ждёт завершения.
        Вызывается в daemon-потоке.

        Примечание: библиотека keyboard требует прав администратора
        для перехвата клавиши Win на некоторых конфигурациях Windows.
        """
        import keyboard as kb

        hotkey = self.state.config.get('hotkey', 'win+h')
        logger.info(f"Регистрация хоткея: '{hotkey}'")

        try:
            kb.add_hotkey(hotkey, self.on_hotkey, suppress=False)
            self._current_hotkey = hotkey
        except Exception as e:
            logger.error(f"Не удалось зарегистрировать хоткей '{hotkey}': {e}")
            if self.state.tray_app:
                self.state.tray_app.notify(
                    'Ошибка', f'Хоткей не зарегистрирован: {e}'
                )
            return

        # kb.wait() блокирует поток до завершения процесса
        kb.wait()

    def reload_hotkey(self, new_hotkey: str):
        """Перерегистрирует хоткей (вызывается из настроек)"""
        import keyboard as kb
        try:
            if self._current_hotkey:
                kb.remove_hotkey(self._current_hotkey)
        except Exception as e:
            logger.warning(f"Не удалось снять старый хоткей: {e}")

        try:
            kb.add_hotkey(new_hotkey, self.on_hotkey, suppress=False)
            self._current_hotkey = new_hotkey
            logger.info(f"Хоткей изменён на '{new_hotkey}'")
        except Exception as e:
            logger.error(f"Ошибка смены хоткея: {e}")
