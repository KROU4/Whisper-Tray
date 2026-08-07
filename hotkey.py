"""Hotkey controller with a serial dictation state machine."""

from __future__ import annotations

import logging
import threading

from core import BackendError, DictationStateMachine, DictationStatus
from platform_integration import GlobalHotkey, PlatformIntegrationError, TextInserter, parse_hotkey

logger = logging.getLogger(__name__)


class HotkeyListener:
    def __init__(self, state):
        self.state = state
        self._recorder = None
        self._transcriber = None
        self._current_hotkey: str | None = None
        self._hotkey_registration: GlobalHotkey | None = None
        self._worker: threading.Thread | None = None
        self._shutdown = threading.Event()
        self.operation_lock = getattr(state, "operation_lock", None) or threading.RLock()
        state.operation_lock = self.operation_lock
        self.machine = getattr(state, "dictation_state", None) or DictationStateMachine()
        state.dictation_state = self.machine

    def _event(self, command: str, *args) -> None:
        queue = getattr(self.state, "tk_queue", None)
        if queue is not None:
            queue.put((command, *args))

    def _notify(self, title: str, message: str) -> None:
        tray = getattr(self.state, "tray_app", None)
        if tray:
            tray.notify(title, message)

    def _get_recorder(self):
        if self._recorder is None:
            from recorder import AudioRecorder

            self._recorder = AudioRecorder(
                self.state.config.get("device_index"),
                on_warning=lambda msg: self._notify("Recording", msg),
                on_limit=lambda: threading.Thread(target=self._stop_and_transcribe, daemon=True).start(),
            )
        return self._recorder

    def _get_transcriber(self):
        if self._transcriber is None:
            from transcriber import Transcriber

            self._transcriber = Transcriber(
                self.state.config.get("model", "small"),
                self.state.config,
                on_backend_switch=lambda message: self._event("backend_switch", message),
            )
        return self._transcriber

    def on_hotkey(self):
        if self._shutdown.is_set():
            return
        status = self.machine.status
        if status is DictationStatus.IDLE:
            self._start_recording()
        elif status is DictationStatus.RECORDING:
            self._stop_and_transcribe()
        elif status is DictationStatus.PROCESSING:
            self._notify("WhisperTray", "Already processing the previous dictation")
        else:
            self.machine.reset()
            self._start_recording()

    def _start_recording(self):
        with self.operation_lock:
            if self.state.is_file_transcribing.is_set():
                self._notify("WhisperTray", "A file transcription is already running")
                return
            if not self.machine.transition(
                {DictationStatus.IDLE, DictationStatus.INSERTED, DictationStatus.ERROR}, DictationStatus.RECORDING
            ):
                return
            try:
                self._get_recorder().start()
            except Exception:
                self.machine.transition({DictationStatus.RECORDING}, DictationStatus.ERROR, "microphone_unavailable")
                self._event("error", "Microphone is unavailable")
                self._notify("Microphone unavailable", "Choose another microphone or check its permissions")
                return
            self.state.is_recording.set()
        self._event("show_hud")
        if getattr(self.state, "tray_app", None):
            self.state.tray_app.set_recording(True)

    def _stop_and_transcribe(self):
        if not self.machine.transition({DictationStatus.RECORDING}, DictationStatus.PROCESSING):
            return
        self.state.is_recording.clear()
        self._event("processing")
        if getattr(self.state, "tray_app", None):
            self.state.tray_app.set_recording(False)
        recorder = self._get_recorder()
        try:
            recorder.stop()
            audio_path = recorder.path
            if audio_path is None:
                raise RuntimeError("no recording")
        except Exception:
            self._fail("recording_failed", "Could not finish the recording")
            return
        self._worker = threading.Thread(
            target=self._transcribe_and_paste, args=(audio_path,), daemon=False, name="WhisperTrayTranscribe"
        )
        self._worker.start()

    def _transcribe_and_paste(self, audio_path):
        try:
            text = self._get_transcriber().transcribe(audio_path, language=self.state.config.get("language"))
            if not text:
                self._fail("empty_audio", "No speech was detected")
                return
            callback = getattr(self.state, "on_transcript", None)
            if callable(callback):
                callback(text)
            paste_result = self._paste_text(text)
            if paste_result == "inserted":
                self.machine.transition({DictationStatus.PROCESSING}, DictationStatus.INSERTED)
                self._event("inserted")
            else:
                self.machine.transition({DictationStatus.PROCESSING}, DictationStatus.ERROR, "clipboard_fallback")
                if paste_result == "partial":
                    message = (
                        "Only part of the text was inserted. The complete text was copied; replace the partial text."
                    )
                elif paste_result == "clipboard":
                    message = "Automatic insertion failed. Press Ctrl+V to paste the copied text."
                else:
                    message = "Automatic insertion and clipboard fallback both failed."
                self._event("error", message)
                self._notify("WhisperTray", message)
        except BackendError as exc:
            self._fail(exc.code, str(exc))
        except Exception:
            logger.exception("Dictation failed")
            self._fail("transcription_failed", "Could not transcribe the recording")
        finally:
            self._get_recorder().cleanup()

    def _fail(self, code: str, message: str):
        self.machine.transition({DictationStatus.RECORDING, DictationStatus.PROCESSING}, DictationStatus.ERROR, code)
        self.state.is_recording.clear()
        self._event("error", message)
        self._notify("WhisperTray", message)

    @staticmethod
    def _copy_to_clipboard(text: str) -> bool:
        try:
            import pyperclip

            pyperclip.copy(text)
            return True
        except Exception:
            return False

    def _paste_text(self, text: str) -> str:
        """Return inserted, clipboard, or failed using the cross-platform input adapter."""
        return TextInserter().insert(text)

    def _register_hotkey(self, hotkey: str, mode: str) -> GlobalHotkey:
        # Parse before altering an existing shortcut, so a bad setting cannot
        # leave dictation without its previous global key.
        parse_hotkey(hotkey)
        registration = GlobalHotkey(
            hotkey,
            self._start_recording if mode == "hold" else self.on_hotkey,
            self._stop_and_transcribe if mode == "hold" else None,
        )
        registration.start()
        return registration

    def run(self):
        hotkey = self.state.config.get("hotkey", "win+alt")
        try:
            self._hotkey_registration = self._register_hotkey(hotkey, self.state.config.get("hotkey_mode", "toggle"))
            self._current_hotkey = hotkey
            while not self._shutdown.wait(0.25):
                pass
        except PlatformIntegrationError as exc:
            logger.warning("Could not register hotkey: %s", exc)
            self._event("error", str(exc))
            self._notify("Hotkey unavailable", str(exc))
        except Exception:
            logger.exception("Could not register hotkey")
            self._notify("Hotkey error", "Could not register the selected hotkey")

    def reload_hotkey(self, new_hotkey: str, mode: str | None = None):
        mode = mode or self.state.config.get("hotkey_mode", "toggle")
        old_hotkey = self._current_hotkey
        old_mode = self.state.config.get("hotkey_mode", "toggle")
        old_registration = self._hotkey_registration
        parse_hotkey(new_hotkey)
        if old_registration:
            old_registration.stop()
        try:
            self._hotkey_registration = self._register_hotkey(new_hotkey, mode)
            self._current_hotkey = new_hotkey
        except Exception:
            if old_hotkey:
                self._hotkey_registration = self._register_hotkey(old_hotkey, old_mode)
                self._current_hotkey = old_hotkey
            raise

    def shutdown(self, timeout: float = 5.0):
        self._shutdown.set()
        self.state.is_recording.clear()
        if self._recorder and self.machine.status is DictationStatus.RECORDING:
            self._recorder.stop()
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout)
        if self._recorder:
            self._recorder.shutdown()
        if self._hotkey_registration:
            self._hotkey_registration.stop()
