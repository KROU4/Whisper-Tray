"""
Глобальный перехват хоткея (по умолчанию win+alt).
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
        self._lock = threading.Lock()       # Защита от двойного нажатия хоткея
        self._is_transcribing = False       # Флаг активной транскрипции

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
                model_size=self.state.config.get('model', 'small'),
                config=self.state.config,
            )
        return self._transcriber

    # ------------------------------------------------------------------
    # Обработчик хоткея
    # ------------------------------------------------------------------

    def on_hotkey(self):
        """Переключает между началом и концом записи (атомарно под локом)"""
        with self._lock:
            if self._is_transcribing:
                logger.warning("Транскрипция уже идёт — хоткей проигнорирован")
                return
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
        if self.state.tray_app:
            self.state.tray_app.set_recording(True)

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
        self._is_transcribing = True
        if self.state.tray_app:
            self.state.tray_app.set_recording(False)
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
        finally:
            self._is_transcribing = False

        if not text:
            logger.info("Транскрипт пустой — ничего не вставляем")
            self.state.tk_queue.put(('hide_hud',))
            return

        self._paste_text(text)

    def _paste_text(self, text: str):
        """
        Вставляет текст через SendInput (KEYEVENTF_UNICODE) — буфер обмена не трогается.
        Работает в большинстве Windows-приложений с текстовыми полями.
        """
        import ctypes

        logger.info(f"Вставка: {text!r}")

        PUL = ctypes.POINTER(ctypes.c_ulong)

        class KeyBdInput(ctypes.Structure):
            _fields_ = [
                ('wVk',         ctypes.c_ushort),
                ('wScan',       ctypes.c_ushort),
                ('dwFlags',     ctypes.c_ulong),
                ('time',        ctypes.c_ulong),
                ('dwExtraInfo', PUL),
            ]

        class _InputUnion(ctypes.Union):
            # Pad to 32 bytes — size of MOUSEINPUT (largest INPUT union member on 64-bit)
            _fields_ = [('ki', KeyBdInput), ('_pad', ctypes.c_byte * 32)]

        class Input(ctypes.Structure):
            _fields_ = [('type', ctypes.c_ulong), ('ii', _InputUnion)]

        KEYEVENTF_UNICODE = 0x0004
        KEYEVENTF_KEYUP   = 0x0002
        INPUT_KEYBOARD    = 1

        extra = ctypes.c_ulong(0)
        ptr   = ctypes.pointer(extra)

        events = []
        for ch in text:
            code = ord(ch)
            if code > 0xFFFF:
                # Surrogate pair для символов за пределами BMP
                code -= 0x10000
                high = 0xD800 + (code >> 10)
                low  = 0xDC00 + (code & 0x3FF)
                for scan in (high, low):
                    dn = Input(INPUT_KEYBOARD, _InputUnion(ki=KeyBdInput(0, scan, KEYEVENTF_UNICODE, 0, ptr)))
                    up = Input(INPUT_KEYBOARD, _InputUnion(ki=KeyBdInput(0, scan, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, ptr)))
                    events += [dn, up]
            else:
                dn = Input(INPUT_KEYBOARD, _InputUnion(ki=KeyBdInput(0, code, KEYEVENTF_UNICODE, 0, ptr)))
                up = Input(INPUT_KEYBOARD, _InputUnion(ki=KeyBdInput(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP, 0, ptr)))
                events += [dn, up]

        time.sleep(0.15)  # Ждём возврата фокуса к целевому окну

        try:
            n = len(events)
            arr = (Input * n)(*events)
            sent = ctypes.windll.user32.SendInput(n, arr, ctypes.sizeof(Input))
            logger.info(f"SendInput отправил {sent} событий")
        except Exception as e:
            logger.error(f"Ошибка SendInput: {e}")
        finally:
            self.state.tk_queue.put(('hide_hud',))

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

        hotkey = self.state.config.get('hotkey', 'win+alt')
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
