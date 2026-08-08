"""The single-threaded PySide6 presentation layer for WhisperTray.

Background workers may continue to put their legacy events into ``state.tk_queue``
during migration. UI actions use the minimal duck-typed controller API instead.
"""

from __future__ import annotations

import json
import logging
import queue
import sys
import threading
from copy import deepcopy
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QLocale, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QCursor, QGuiApplication, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)
APP_NAME = "WhisperTray"
APP_STYLE = """
QMainWindow, QDialog, QWidget {
    background: #1b1d21;
    color: #f5f1e8;
    font-size: 13px;
}
QLabel#statusLabel {
    color: #fff8eb;
    font-size: 20px;
    font-weight: 700;
}
QLabel#detailLabel { color: #c8c2b8; }
QPlainTextEdit, QLineEdit, QComboBox {
    background: #25282e;
    border: 1px solid #3a3e46;
    border-radius: 7px;
    padding: 7px;
    color: #fff8eb;
    selection-background-color: #ff765d;
}
QPushButton {
    background: #30343b;
    border: 1px solid #484d56;
    border-radius: 7px;
    padding: 8px 12px;
    color: #fff8eb;
}
QPushButton:hover { background: #3a3f47; border-color: #ff8b70; }
QPushButton:focus { border: 2px solid #ff8b70; }
QPushButton:disabled { color: #777b82; background: #26292e; }
QPushButton#primaryAction {
    background: #ff765d;
    border-color: #ff8b70;
    color: #241b18;
    font-weight: 700;
}
QPushButton#primaryAction:hover { background: #ff8b70; }
QCheckBox { spacing: 8px; }
"""


def app_icon_path() -> Path:
    """Return the bundled brand icon without depending on the working directory."""
    root = Path(__file__).resolve().parent
    source_tree = root / "assets" / "whispertray-icon.png"
    return source_tree if source_tree.exists() else root / "whispertray-icon.png"


def app_logo_path() -> Path:
    """Return the wider onboarding brand mark when it is packaged."""
    root = Path(__file__).resolve().parent
    source_tree = root / "assets" / "whispertray-logo.png"
    return source_tree if source_tree.exists() else app_icon_path()


class ViewState(str, Enum):
    IDLE = "idle"
    RECORDING = "recording"
    PROCESSING = "processing"
    INSERTED = "inserted"
    ERROR = "error"


STRINGS = {
    "ru": {
        "title": "WhisperTray",
        "idle": "Готов к диктовке",
        "recording": "Идёт запись",
        "processing": "Распознаю речь…",
        "inserted": "Текст вставлен",
        "error": "Ошибка",
        "record": "Начать запись",
        "stop": "Остановить и распознать",
        "settings": "Настройки",
        "diagnostics": "Диагностика",
        "quit": "Выйти",
        "profile": "Профиль",
        "privacy": "Приватность (локально)",
        "speed": "Скорость (Groq)",
        "microphone": "Микрофон",
        "test_mic": "Проверить микрофон",
        "language": "Язык распознавания",
        "hotkey": "Горячая клавиша",
        "hotkey_mode": "Режим клавиши",
        "toggle": "Нажать / нажать",
        "hold": "Удерживать",
        "model": "Локальная модель",
        "appearance": "Интерфейс",
        "hud": "Показывать индикатор",
        "contrast": "Высокий контраст",
        "motion": "Уменьшить анимацию",
        "position": "Позиция HUD",
        "bottom_right": "Справа снизу",
        "bottom_left": "Слева снизу",
        "history": "Сохранять локальную историю",
        "retention": "Хранить историю",
        "days": "дней",
        "last_result": "Последний результат",
        "onboarding": "Первый запуск",
        "welcome": "Выберите, как WhisperTray будет обрабатывать аудио.",
        "cloud_note": "В режиме «Скорость» аудио отправляется в Groq. Нужен ваш API-ключ.",
        "fallback": "При ошибке Groq явно переключаться на локальную модель",
        "test_key": "Проверить ключ",
        "groq_key": "Ключ Groq API",
        "keychain_saved": "Сохранён в системном хранилище ключей",
        "default_mic": "Системный микрофон",
        "prepare_model": "Скачать / подготовить модель",
        "ui_language": "Язык интерфейса",
        "clear_history": "Очистить локальную историю",
        "export_diagnostics": "Экспортировать диагностику…",
        "save": "Сохранить",
        "cancel": "Отмена",
        "saved": "Настройки сохранены.",
        "already_processing": "Уже обрабатываю запись",
        "file": "Транскрибировать файл…",
        "closed": "Приложение продолжает работать в системном трее.",
    },
    "en": {
        "title": "WhisperTray",
        "idle": "Ready for dictation",
        "recording": "Recording",
        "processing": "Transcribing…",
        "inserted": "Text inserted",
        "error": "Error",
        "record": "Start recording",
        "stop": "Stop and transcribe",
        "settings": "Settings",
        "diagnostics": "Diagnostics",
        "quit": "Quit",
        "profile": "Profile",
        "privacy": "Privacy (local)",
        "speed": "Speed (Groq)",
        "microphone": "Microphone",
        "test_mic": "Test microphone",
        "language": "Recognition language",
        "hotkey": "Hotkey",
        "hotkey_mode": "Hotkey mode",
        "toggle": "Press / press",
        "hold": "Hold to record",
        "model": "Local model",
        "appearance": "Appearance",
        "hud": "Show status overlay",
        "contrast": "High contrast",
        "motion": "Reduce motion",
        "position": "HUD position",
        "bottom_right": "Bottom right",
        "bottom_left": "Bottom left",
        "history": "Keep local history",
        "retention": "Keep history",
        "days": "days",
        "last_result": "Last result",
        "onboarding": "First launch",
        "welcome": "Choose how WhisperTray processes audio.",
        "cloud_note": "Speed mode sends audio to Groq. Your own API key is required.",
        "fallback": "Explicitly switch to the local model when Groq fails",
        "test_key": "Test key",
        "groq_key": "Groq API key",
        "keychain_saved": "Saved in the system keychain",
        "default_mic": "System default",
        "prepare_model": "Prepare / download local model",
        "ui_language": "Interface language",
        "clear_history": "Clear local history",
        "export_diagnostics": "Export diagnostics…",
        "save": "Save",
        "cancel": "Cancel",
        "saved": "Settings saved.",
        "already_processing": "Already processing a recording",
        "file": "Transcribe file…",
        "closed": "WhisperTray is still running in the system tray.",
    },
}

ONBOARDING_STRINGS = {
    "en": {
        "heading": "Welcome to WhisperTray",
        "subtitle": "Set up dictation in a couple of minutes",
        "choose_profile": "Choose your audio processing profile",
        "privacy_card": "Only on this computer",
        "speed_card": "Processed through Groq",
        "continue": "Continue",
        "back": "Back",
        "profile_setup": "Set up your profile",
        "local_explainer": "Audio stays on this device. Choose a local Whisper model when you are ready.",
        "cloud_explainer": "Audio is sent to Groq for transcription. It is never kept by WhisperTray.",
        "get_groq_key": "Get a Groq API key",
        "audio_ready": "Check your microphone and shortcut",
        "audio_hint": "You can change these later in Settings.",
        "finish": "Finish setup",
        "step": "Step {current} of 3",
    },
    "ru": {
        "heading": "Добро пожаловать в WhisperTray",
        "subtitle": "Настроим диктовку за пару минут",
        "choose_profile": "Выберите профиль обработки аудио",
        "privacy_card": "Только на этом компьютере",
        "speed_card": "Обработка через Groq",
        "continue": "Продолжить",
        "back": "Назад",
        "profile_setup": "Настройте профиль",
        "local_explainer": "Аудио остаётся на этом устройстве. Выберите локальную модель Whisper, когда будете готовы.",
        "cloud_explainer": "Аудио отправляется в Groq для распознавания. WhisperTray никогда его не хранит.",
        "get_groq_key": "Получить ключ Groq API",
        "audio_ready": "Проверьте микрофон и горячую клавишу",
        "audio_hint": "Позже это можно изменить в настройках.",
        "finish": "Завершить настройку",
        "step": "Шаг {current} из 3",
    },
}


def ui_language(config: dict) -> str:
    configured = config.get("ui_language", "auto")
    if configured in STRINGS:
        return configured
    # On first run use the Windows/UI locale, then allow an explicit override.
    return "ru" if QLocale.system().name().lower().startswith("ru") else "en"


def input_devices() -> list[tuple[str, int | None]]:
    try:
        import sounddevice as sd

        return [(str(d["name"]), i) for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]
    except Exception as exc:
        logger.info("Input device enumeration unavailable: %s", exc)
        return []


class RecordingPulse(QWidget):
    """A small native-painted recorder indicator, with a motion-safe static state."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.recording = False
        self.phase = 0
        self.setFixedSize(46, 46)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._advance)

    def set_recording(self, recording: bool, reduce_motion: bool) -> None:
        self.recording = recording
        if recording and not reduce_motion:
            self.timer.start(70)
        else:
            self.timer.stop()
            self.phase = 0
        self.update()

    def _advance(self) -> None:
        self.phase = (self.phase + 1) % 20
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt callback name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        center = self.rect().center()
        if self.recording:
            radius = 17 + (self.phase % 10) / 5
            painter.setPen(QPen(QColor("#ff765d"), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(center, radius, radius)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#ff765d") if self.recording else QColor("#59606b"))
        painter.drawEllipse(center, 12, 12)


class StatusHud(QWidget):
    def __init__(self, config: dict, lang: str):
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.config, self.lang = config, lang
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedSize(280, 58)
        self.pulse = RecordingPulse(self)
        self.pulse.move(8, 6)
        self.label = QLabel(self)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setGeometry(50, 0, 222, 58)

    def show_status(self, status: ViewState, message: str | None = None) -> None:
        hud = self.config.get("hud", {})
        if not hud.get("enabled", True) or status == ViewState.IDLE:
            self.hide()
            return
        colors = {
            ViewState.RECORDING: "#c62828",
            ViewState.PROCESSING: "#455a64",
            ViewState.INSERTED: "#2e7d32",
            ViewState.ERROR: "#b71c1c",
        }
        bg = "#000" if hud.get("high_contrast") else colors.get(status, "#263238")
        fg = "#ffff00" if hud.get("high_contrast") else "white"
        self.setStyleSheet(f"background:{bg}; border-radius:12px; color:{fg};")
        self.label.setText(message or STRINGS[self.lang][status.value])
        self.pulse.set_recording(status == ViewState.RECORDING, hud.get("reduce_motion", False))
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        margin = 18
        x = area.left() + margin if hud.get("position") == "bottom_left" else area.right() - self.width() - margin
        self.move(x, area.bottom() - self.height() - margin)
        self.show()


class ProfileCard(QPushButton):
    """Large selectable profile control drawn with Qt rather than image/CSS art."""

    def __init__(self, kind: str, title: str, subtitle: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.kind, self.title, self.subtitle = kind, title, subtitle
        self.setCheckable(True)
        self.setMinimumSize(260, 290)
        self.setMaximumHeight(310)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(title)
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt callback name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(3, 3, -3, -3)
        accent = QColor("#ff765d")
        painter.setPen(QPen(accent if self.isChecked() else QColor("#41454d"), 3 if self.isChecked() else 2))
        painter.setBrush(QColor("#202329"))
        painter.drawRoundedRect(rect, 24, 24)
        icon_rect = rect.adjusted(0, 34, 0, 0)
        icon_center = icon_rect.center()
        painter.setPen(QPen(QColor("#4b4f56"), 1))
        painter.setBrush(QColor("#292c32"))
        painter.drawEllipse(icon_center, 52, 52)
        painter.setPen(QPen(accent, 5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        if self.kind == "privacy":
            path = QPainterPath()
            path.moveTo(icon_center.x(), icon_center.y() - 34)
            path.lineTo(icon_center.x() - 27, icon_center.y() - 20)
            path.lineTo(icon_center.x() - 23, icon_center.y() + 17)
            path.lineTo(icon_center.x(), icon_center.y() + 33)
            path.lineTo(icon_center.x() + 23, icon_center.y() + 17)
            path.lineTo(icon_center.x() + 27, icon_center.y() - 20)
            path.closeSubpath()
            painter.drawPath(path)
            painter.setBrush(accent)
            painter.drawRoundedRect(icon_center.x() - 10, icon_center.y() - 1, 20, 17, 4, 4)
            painter.drawArc(icon_center.x() - 9, icon_center.y() - 15, 18, 20, 0, 180 * 16)
        else:
            bolt = QPainterPath()
            bolt.moveTo(icon_center.x() + 6, icon_center.y() - 36)
            bolt.lineTo(icon_center.x() - 25, icon_center.y() + 2)
            bolt.lineTo(icon_center.x() - 3, icon_center.y() + 2)
            bolt.lineTo(icon_center.x() - 10, icon_center.y() + 36)
            bolt.lineTo(icon_center.x() + 27, icon_center.y() - 9)
            bolt.lineTo(icon_center.x() + 5, icon_center.y() - 9)
            bolt.closeSubpath()
            painter.setBrush(accent)
            painter.drawPath(bolt)
        painter.setPen(QColor("#fff1df"))
        font = painter.font()
        font.setPointSize(20)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(rect.adjusted(16, 165, -16, -58), Qt.AlignCenter, self.title)
        painter.setPen(QColor("#b9b3ab"))
        font.setPointSize(11)
        font.setBold(False)
        painter.setFont(font)
        painter.drawText(rect.adjusted(18, 212, -18, -20), Qt.AlignCenter | Qt.TextWordWrap, self.subtitle)
        if self.isChecked():
            painter.setPen(Qt.NoPen)
            painter.setBrush(accent)
            painter.drawEllipse(rect.right() - 43, rect.top() + 14, 24, 24)
            painter.setPen(QPen(QColor("#202329"), 3, Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(rect.right() - 38, rect.top() + 26, rect.right() - 33, rect.top() + 31)
            painter.drawLine(rect.right() - 33, rect.top() + 31, rect.right() - 24, rect.top() + 21)


class SettingsDialog(QDialog):
    def __init__(self, app: "WhisperTrayUi", onboarding: bool = False):
        super().__init__(app.window)
        self.app, self.onboarding = app, onboarding
        self.config = deepcopy(app.state.config)
        self.lang = ui_language(self.config)
        self.t = STRINGS[self.lang]
        self.setWindowTitle(self.t["onboarding"] if onboarding else self.t["settings"])
        layout = QVBoxLayout(self)
        if onboarding:
            self._build_onboarding(layout)
            return
        form = QFormLayout()
        self.form = form
        layout.addLayout(form)
        self.profile = QComboBox()
        self.profile.addItem(self.t["privacy"], "privacy")
        self.profile.addItem(self.t["speed"], "speed")
        self.profile.setCurrentIndex(0 if self.config.get("profile") == "privacy" else 1)
        form.addRow(self.t["profile"], self.profile)
        self.cloud_note = QLabel(self.t["cloud_note"])
        self.cloud_note.setWordWrap(True)
        layout.addWidget(self.cloud_note)
        self.local_fallback = QCheckBox(self.t["fallback"])
        self.local_fallback.setChecked(self.config.get("allow_local_fallback", False))
        layout.addWidget(self.local_fallback)
        self.groq_key = QLineEdit()
        self.groq_key.setEchoMode(QLineEdit.Password)
        self.groq_key.setPlaceholderText(self.t["keychain_saved"] if self._has_groq_key() else "gsk_…")
        form.addRow(self.t["groq_key"], self.groq_key)
        self.test_key_button = QPushButton(self.t["test_key"])
        self.test_key_button.clicked.connect(self.test_groq_key)
        form.addRow("", self.test_key_button)
        self.profile.currentIndexChanged.connect(self.update_profile_controls)
        self.update_profile_controls()
        self.mic = QComboBox()
        self.mic.addItem(self.t["default_mic"], None)
        for name, index in input_devices():
            self.mic.addItem(name, index)
        wanted = self.config.get("device_index")
        self.mic.setCurrentIndex(next((i for i in range(self.mic.count()) if self.mic.itemData(i) == wanted), 0))
        form.addRow(self.t["microphone"], self.mic)
        test_mic = QPushButton(self.t["test_mic"])
        test_mic.clicked.connect(self.test_microphone)
        form.addRow("", test_mic)
        self.rec_lang = QComboBox()
        self.rec_lang.addItems(["Auto", "Русский (ru)", "English (en)"])
        self.rec_lang.setCurrentIndex({None: 0, "ru": 1, "en": 2}.get(self.config.get("language"), 0))
        form.addRow(self.t["language"], self.rec_lang)
        self.hotkey = QLineEdit(self.config.get("hotkey", "win+alt"))
        form.addRow(self.t["hotkey"], self.hotkey)
        self.hotkey_mode = QComboBox()
        self.hotkey_mode.addItem(self.t["toggle"], "toggle")
        self.hotkey_mode.addItem(self.t["hold"], "hold")
        self.hotkey_mode.setCurrentIndex(1 if self.config.get("hotkey_mode") == "hold" else 0)
        form.addRow(self.t["hotkey_mode"], self.hotkey_mode)
        self.model = QComboBox()
        for name, size in (
            ("tiny", "75 MB"),
            ("base", "142 MB"),
            ("small", "466 MB"),
            ("medium", "1.5 GB"),
            ("large", "2.9 GB"),
        ):
            self.model.addItem(f"{name} (~{size})", name)
        self.model.setCurrentIndex(max(0, self.model.findData(self.config.get("model", "small"))))
        form.addRow(self.t["model"], self.model)
        self.prepare_model_button = QPushButton(self.t["prepare_model"])
        self.prepare_model_button.clicked.connect(self.prepare_local_model)
        form.addRow("", self.prepare_model_button)
        self.ui_lang = QComboBox()
        self.ui_lang.addItem("Русский", "ru")
        self.ui_lang.addItem("English", "en")
        self.ui_lang.setCurrentIndex(0 if self.lang == "ru" else 1)
        form.addRow(self.t["ui_language"], self.ui_lang)
        layout.addWidget(QLabel(self.t["appearance"]))
        hud = self.config.get("hud", {})
        self.hud_enabled = QCheckBox(self.t["hud"])
        self.hud_enabled.setChecked(hud.get("enabled", True))
        layout.addWidget(self.hud_enabled)
        self.contrast = QCheckBox(self.t["contrast"])
        self.contrast.setChecked(hud.get("high_contrast", False))
        layout.addWidget(self.contrast)
        self.motion = QCheckBox(self.t["motion"])
        self.motion.setChecked(hud.get("reduce_motion", False))
        layout.addWidget(self.motion)
        self.position = QComboBox()
        self.position.addItem(self.t["bottom_right"], "bottom_right")
        self.position.addItem(self.t["bottom_left"], "bottom_left")
        self.position.setCurrentIndex(1 if hud.get("position") == "bottom_left" else 0)
        form.addRow(self.t["position"], self.position)
        history = self.config.get("history", {})
        self.history_enabled = QCheckBox(self.t["history"])
        self.history_enabled.setChecked(history.get("enabled", False))
        layout.addWidget(self.history_enabled)
        self.retention = QComboBox()
        for days in (7, 30, 90, 365):
            self.retention.addItem(f"{days} {self.t['days']}", days)
        self.retention.setCurrentIndex(max(0, self.retention.findData(history.get("retention_days", 30))))
        form.addRow(self.t["retention"], self.retention)
        clear_history = QPushButton(self.t["clear_history"])
        clear_history.clicked.connect(self.clear_history)
        form.addRow("", clear_history)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText(self.t["save"])
        buttons.button(QDialogButtonBox.Cancel).setText(self.t["cancel"])
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.update_profile_controls()

    @staticmethod
    def _has_groq_key() -> bool:
        try:
            from credentials import CredentialStore

            return bool(CredentialStore().get_groq_key())
        except Exception:
            return False

    def save(self) -> None:
        if self.app.is_busy():
            QMessageBox.warning(self, APP_NAME, self.t["already_processing"])
            return
        hotkey = self.hotkey.text().strip()
        if not hotkey:
            QMessageBox.warning(self, APP_NAME, "Hotkey cannot be empty.")
            return
        try:
            from platform_integration import parse_hotkey

            parse_hotkey(hotkey)
        except Exception:
            QMessageBox.warning(self, APP_NAME, "The selected hotkey is not valid.")
            return
        if self.profile.currentData() == "speed" and not self.groq_key.text().strip() and not self._has_groq_key():
            QMessageBox.warning(self, APP_NAME, "Groq API key is required for the Speed profile.")
            return
        self.config.update(
            {
                "profile": self.profile.currentData(),
                "transcription_backend": "local" if self.profile.currentData() == "privacy" else "groq",
                "allow_local_fallback": self.local_fallback.isChecked(),
                "device_index": self.mic.currentData(),
                "language": [None, "ru", "en"][self.rec_lang.currentIndex()],
                "hotkey": hotkey,
                "hotkey_mode": self.hotkey_mode.currentData(),
                "model": self.model.currentData(),
                "ui_language": self.ui_lang.currentData(),
                "onboarding_complete": True,
                "hud": {
                    "enabled": self.hud_enabled.isChecked(),
                    "position": self.position.currentData(),
                    "high_contrast": self.contrast.isChecked(),
                    "reduce_motion": self.motion.isChecked(),
                },
                "history": {
                    "enabled": self.history_enabled.isChecked(),
                    "retention_days": self.retention.currentData(),
                },
            }
        )
        try:
            key = self.groq_key.text().strip()
            if key:
                from credentials import CredentialStore

                CredentialStore().set_groq_key(key)
            self.app.save_config(self.config)
        except Exception as exc:
            logger.exception("Saving settings failed")
            QMessageBox.critical(self, APP_NAME, str(exc))
            return
        self.accept()

    def update_profile_controls(self) -> None:
        cloud = self.profile.currentData() == "speed"
        self.cloud_note.setVisible(cloud)
        self.local_fallback.setVisible(cloud)
        for widget in (self.groq_key, self.test_key_button):
            widget.setVisible(cloud)
        local_widgets = (widget for widget in (getattr(self, "model", None), getattr(self, "prepare_model_button", None)) if widget)
        for widget in local_widgets:
            widget.setVisible(not cloud)
        controls = [(self.groq_key, cloud), (self.test_key_button, cloud)]
        if hasattr(self, "model"):
            controls.extend([(self.model, not cloud), (self.prepare_model_button, not cloud)])
        for widget, visible in controls:
            label = self.form.labelForField(widget)
            if label:
                label.setVisible(visible)

    def _build_onboarding(self, layout: QVBoxLayout) -> None:
        """Focused first-run flow; normal Settings stays comprehensive below."""
        self.setMinimumSize(700, 700)
        self.ot = ONBOARDING_STRINGS[self.lang]
        logo = QLabel()
        logo.setAlignment(Qt.AlignCenter)
        pixmap = QPixmap(str(app_logo_path()))
        if not pixmap.isNull():
            logo.setPixmap(pixmap.scaled(76, 76, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        layout.addWidget(logo)
        heading = QLabel(self.ot["heading"])
        heading.setObjectName("statusLabel")
        heading.setAlignment(Qt.AlignCenter)
        layout.addWidget(heading)
        subtitle = QLabel(self.ot["subtitle"])
        subtitle.setObjectName("detailLabel")
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(subtitle)
        self.progress = QLabel()
        self.progress.setAlignment(Qt.AlignCenter)
        self.progress.setStyleSheet("color: #ff765d; font-size: 15px; padding: 8px;")
        layout.addWidget(self.progress)
        self.profile = QComboBox()
        self.profile.addItem(self.t["privacy"], "privacy")
        self.profile.addItem(self.t["speed"], "speed")
        self.profile.setCurrentIndex(0 if self.config.get("profile") == "privacy" else 1)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages)
        self._onboarding_profile_page()
        self._onboarding_backend_page()
        self._onboarding_audio_page()
        self.set_onboarding_step(0)

    def _onboarding_profile_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(18)
        layout.setAlignment(Qt.AlignTop)
        prompt = QLabel(self.ot["choose_profile"])
        prompt.setAlignment(Qt.AlignCenter)
        prompt.setStyleSheet("font-size: 18px; font-weight: 700; padding: 10px;")
        layout.addWidget(prompt)
        cards = QHBoxLayout()
        self.privacy_card = ProfileCard("privacy", self.t["privacy"].split(" (")[0], self.ot["privacy_card"])
        self.speed_card = ProfileCard("speed", self.t["speed"].split(" (")[0], self.ot["speed_card"])
        self.privacy_card.clicked.connect(lambda: self._select_onboarding_profile("privacy"))
        self.speed_card.clicked.connect(lambda: self._select_onboarding_profile("speed"))
        cards.addWidget(self.privacy_card)
        cards.addWidget(self.speed_card)
        layout.addLayout(cards)
        forward = QPushButton(self.ot["continue"])
        forward.setObjectName("primaryAction")
        forward.setMinimumWidth(260)
        forward.clicked.connect(lambda: self.set_onboarding_step(1))
        layout.addWidget(forward, alignment=Qt.AlignHCenter)
        self.pages.addWidget(page)
        self._select_onboarding_profile(self.profile.currentData())

    def _onboarding_backend_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel(self.ot["profile_setup"])
        title.setObjectName("statusLabel")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        self.backend_pages = QStackedWidget()
        local = QWidget()
        local_layout = QFormLayout(local)
        local_note = QLabel(self.ot["local_explainer"])
        local_note.setWordWrap(True)
        local_layout.addRow(local_note)
        self.model = QComboBox()
        for name, size in (("tiny", "75 MB"), ("base", "142 MB"), ("small", "466 MB"), ("medium", "1.5 GB"), ("large", "2.9 GB")):
            self.model.addItem(f"{name} (~{size})", name)
        self.model.setCurrentIndex(max(0, self.model.findData(self.config.get("model", "small"))))
        local_layout.addRow(self.t["model"], self.model)
        self.prepare_model_button = QPushButton(self.t["prepare_model"])
        self.prepare_model_button.clicked.connect(self.prepare_local_model)
        local_layout.addRow("", self.prepare_model_button)
        cloud = QWidget()
        cloud_layout = QFormLayout(cloud)
        cloud_note = QLabel(self.ot["cloud_explainer"])
        cloud_note.setWordWrap(True)
        cloud_layout.addRow(cloud_note)
        link = QLabel(f'<a href="https://console.groq.com/keys">{self.ot["get_groq_key"]}</a>')
        link.setOpenExternalLinks(True)
        cloud_layout.addRow(link)
        self.groq_key = QLineEdit()
        self.groq_key.setEchoMode(QLineEdit.Password)
        self.groq_key.setPlaceholderText(self.t["keychain_saved"] if self._has_groq_key() else "gsk_…")
        cloud_layout.addRow(self.t["groq_key"], self.groq_key)
        self.test_key_button = QPushButton(self.t["test_key"])
        self.test_key_button.clicked.connect(self.test_groq_key)
        cloud_layout.addRow("", self.test_key_button)
        self.local_fallback = QCheckBox(self.t["fallback"])
        self.local_fallback.setChecked(self.config.get("allow_local_fallback", False))
        cloud_layout.addRow("", self.local_fallback)
        self.backend_pages.addWidget(local)
        self.backend_pages.addWidget(cloud)
        layout.addWidget(self.backend_pages)
        layout.addLayout(self._onboarding_navigation(lambda: self.set_onboarding_step(0), lambda: self.set_onboarding_step(2)))
        self.pages.addWidget(page)

    def _onboarding_audio_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        title = QLabel(self.ot["audio_ready"])
        title.setObjectName("statusLabel")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        hint = QLabel(self.ot["audio_hint"])
        hint.setObjectName("detailLabel")
        hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(hint)
        form = QFormLayout()
        self.mic = QComboBox()
        self.mic.addItem(self.t["default_mic"], None)
        for name, index in input_devices():
            self.mic.addItem(name, index)
        self.mic.setCurrentIndex(next((i for i in range(self.mic.count()) if self.mic.itemData(i) == self.config.get("device_index")), 0))
        form.addRow(self.t["microphone"], self.mic)
        test_mic = QPushButton(self.t["test_mic"])
        test_mic.clicked.connect(self.test_microphone)
        form.addRow("", test_mic)
        self.hotkey = QLineEdit(self.config.get("hotkey", "win+alt"))
        form.addRow(self.t["hotkey"], self.hotkey)
        self.hotkey_mode = QComboBox()
        self.hotkey_mode.addItem(self.t["toggle"], "toggle")
        self.hotkey_mode.addItem(self.t["hold"], "hold")
        self.hotkey_mode.setCurrentIndex(1 if self.config.get("hotkey_mode") == "hold" else 0)
        form.addRow(self.t["hotkey_mode"], self.hotkey_mode)
        layout.addLayout(form)
        # Preserve advanced settings on first run; these controls remain available in Settings.
        self.rec_lang = QComboBox()
        self.rec_lang.addItems(["Auto", "Русский (ru)", "English (en)"])
        self.rec_lang.setCurrentIndex({None: 0, "ru": 1, "en": 2}.get(self.config.get("language"), 0))
        self.ui_lang = QComboBox()
        self.ui_lang.addItem("Русский", "ru")
        self.ui_lang.addItem("English", "en")
        self.ui_lang.setCurrentIndex(0 if self.lang == "ru" else 1)
        hud = self.config.get("hud", {})
        self.hud_enabled = QCheckBox()
        self.hud_enabled.setChecked(hud.get("enabled", True))
        self.contrast = QCheckBox()
        self.contrast.setChecked(hud.get("high_contrast", False))
        self.motion = QCheckBox()
        self.motion.setChecked(hud.get("reduce_motion", False))
        self.position = QComboBox()
        self.position.addItem("", "bottom_right")
        self.history_enabled = QCheckBox()
        self.history_enabled.setChecked(self.config.get("history", {}).get("enabled", False))
        self.retention = QComboBox()
        self.retention.addItem("", self.config.get("history", {}).get("retention_days", 30))
        layout.addLayout(self._onboarding_navigation(lambda: self.set_onboarding_step(1), self.save, self.ot["finish"]))
        self.pages.addWidget(page)

    def _onboarding_navigation(self, back, forward, label: str | None = None) -> QHBoxLayout:
        row = QHBoxLayout()
        back_button = QPushButton(self.ot["back"])
        back_button.clicked.connect(back)
        forward_button = QPushButton(label or self.ot["continue"])
        forward_button.setObjectName("primaryAction")
        forward_button.clicked.connect(forward)
        row.addWidget(back_button)
        row.addStretch()
        row.addWidget(forward_button)
        return row

    def _select_onboarding_profile(self, profile: str) -> None:
        self.profile.setCurrentIndex(0 if profile == "privacy" else 1)
        self.privacy_card.setChecked(profile == "privacy")
        self.speed_card.setChecked(profile == "speed")

    def set_onboarding_step(self, step: int) -> None:
        self.pages.setCurrentIndex(step)
        self.progress.setText(self.ot["step"].format(current=step + 1))
        if step == 1:
            self.backend_pages.setCurrentIndex(1 if self.profile.currentData() == "speed" else 0)

    def test_microphone(self) -> None:
        try:
            import sounddevice as sd

            sd.check_input_settings(device=self.mic.currentData(), channels=1, samplerate=16000, dtype="float32")
            QMessageBox.information(self, APP_NAME, "Microphone is available.")
        except Exception as exc:
            QMessageBox.warning(self, APP_NAME, f"Microphone test failed: {exc}")

    def test_groq_key(self) -> None:
        key = self.groq_key.text().strip()
        if not key:
            QMessageBox.warning(self, APP_NAME, "Enter a Groq API key to test it.")
            return
        self.test_key_button.setEnabled(False)
        self.test_key_button.setText("Testing…")
        self._key_result: queue.Queue = queue.Queue()

        def check():
            try:
                from groq import Groq

                Groq(api_key=key).models.list()
                self._key_result.put((True, "Groq API key is valid."))
            except Exception as exc:
                self._key_result.put((False, f"Groq key test failed: {exc}"))

        threading.Thread(target=check, daemon=True, name="GroqKeyTest").start()

        def poll():
            try:
                ok, message = self._key_result.get_nowait()
            except queue.Empty:
                QTimer.singleShot(100, poll)
                return
            self.test_key_button.setEnabled(True)
            self.test_key_button.setText(self.t["test_key"])
            (QMessageBox.information if ok else QMessageBox.warning)(self, APP_NAME, message)

        QTimer.singleShot(100, poll)

    def prepare_local_model(self) -> None:
        self.prepare_model_button.setEnabled(False)
        self.prepare_model_button.setText("Preparing model…")
        result: queue.Queue = queue.Queue()
        model_name = self.model.currentData()

        def prepare():
            try:
                from transcriber import Transcriber

                config = deepcopy(self.config)
                config["profile"] = "privacy"
                config["transcription_backend"] = "local"
                Transcriber(model_name, config)._ensure_model()
                result.put((True, f"Model {model_name} is ready."))
            except Exception as exc:
                result.put((False, f"Could not prepare model: {exc}"))

        threading.Thread(target=prepare, daemon=True, name="LocalModelPrepare").start()

        def poll():
            try:
                ok, message = result.get_nowait()
            except queue.Empty:
                QTimer.singleShot(200, poll)
                return
            self.prepare_model_button.setEnabled(True)
            self.prepare_model_button.setText(self.t["prepare_model"])
            (QMessageBox.information if ok else QMessageBox.warning)(self, APP_NAME, message)

        QTimer.singleShot(200, poll)

    def clear_history(self) -> None:
        from history_store import HistoryStore

        HistoryStore().clear()
        QMessageBox.information(self, APP_NAME, "Local transcript history was cleared.")


class DiagnosticsDialog(QDialog):
    def __init__(self, app: "WhisperTrayUi"):
        super().__init__(app.window)
        self.setWindowTitle(STRINGS[app.lang]["diagnostics"])
        self.resize(520, 340)
        info = {
            "version": "1.0",
            "profile": app.state.config.get("profile", "legacy"),
            "backend": app.state.config.get("transcription_backend"),
            "hotkey": app.state.config.get("hotkey"),
            "microphone": app.state.config.get("device_index"),
            "python": sys.version.split()[0],
            "platform": sys.platform,
        }
        field = QPlainTextEdit(json.dumps(info, ensure_ascii=False, indent=2))
        field.setReadOnly(True)
        layout = QVBoxLayout(self)
        layout.addWidget(field)
        row = QHBoxLayout()
        export = QPushButton(STRINGS[app.lang]["export_diagnostics"])
        export.clicked.connect(lambda: self.export(app))
        close = QPushButton("OK")
        close.clicked.connect(self.accept)
        row.addWidget(export)
        row.addWidget(close)
        layout.addLayout(row)

    def export(self, app: "WhisperTrayUi") -> None:
        path, _ = QFileDialog.getSaveFileName(self, APP_NAME, "whispertray-diagnostics.json", "JSON (*.json)")
        if not path:
            return
        try:
            from diagnostics import export_diagnostics

            export_diagnostics(path, app.state.config)
            QMessageBox.information(self, APP_NAME, "Diagnostics exported without secrets, audio, or transcript text.")
        except Exception as exc:
            logger.exception("Diagnostics export failed")
            QMessageBox.critical(self, APP_NAME, str(exc))


class WhisperTrayUi:
    """Qt owner plus compatibility ``tray_app`` for existing workers."""

    def __init__(self, state):
        self.state = state
        self.lang = ui_language(state.config)
        self.t = STRINGS[self.lang]
        self.status = ViewState.IDLE
        self.window = QMainWindow()
        self.window.setWindowTitle(APP_NAME)
        self.window.setStyleSheet(APP_STYLE)
        self.window.setMinimumWidth(380)
        self.window.closeEvent = self.close_to_tray
        self.build_window()
        self.hud = StatusHud(state.config, self.lang)
        self.tray = QSystemTrayIcon(self.icon(), self.window)
        self.build_tray()
        self.tray.show()
        self.render_status()
        self.ui_events: queue.Queue = queue.Queue()
        self.poller = QTimer()
        self.poller.timeout.connect(self.drain_worker_events)
        self.poller.start(50)

    def build_window(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(20, 20, 20, 20)
        logo = QLabel()
        logo.setAlignment(Qt.AlignCenter)
        brand = QPixmap(str(app_icon_path()))
        if not brand.isNull():
            logo.setPixmap(brand.scaled(58, 58, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            layout.addWidget(logo)
        self.recording_pulse = RecordingPulse()
        layout.addWidget(self.recording_pulse, alignment=Qt.AlignHCenter)
        self.status_label = QLabel()
        self.status_label.setObjectName("statusLabel")
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)
        self.detail_label = QLabel()
        self.detail_label.setObjectName("detailLabel")
        self.detail_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.detail_label)
        layout.addWidget(QLabel(self.t["last_result"]))
        self.last_result = QPlainTextEdit()
        self.last_result.setReadOnly(True)
        self.last_result.setMaximumHeight(110)
        layout.addWidget(self.last_result)
        self.action_button = QPushButton()
        self.action_button.setObjectName("primaryAction")
        self.action_button.clicked.connect(self.toggle_recording)
        layout.addWidget(self.action_button)
        row = QHBoxLayout()
        settings = QPushButton(self.t["settings"])
        settings.clicked.connect(self.open_settings)
        diagnostics = QPushButton(self.t["diagnostics"])
        diagnostics.clicked.connect(self.open_diagnostics)
        row.addWidget(settings)
        row.addWidget(diagnostics)
        layout.addLayout(row)
        self.window.setCentralWidget(root)

    def build_tray(self) -> None:
        from PySide6.QtWidgets import QMenu

        menu = QMenu()
        show = QAction(APP_NAME, menu)
        show.triggered.connect(self.show_window)
        menu.addAction(show)
        self.tray_action = QAction(menu)
        self.tray_action.triggered.connect(self.toggle_recording)
        menu.addAction(self.tray_action)
        file_action = QAction(self.t["file"], menu)
        file_action.triggered.connect(self.transcribe_file)
        menu.addAction(file_action)
        settings = QAction(self.t["settings"], menu)
        settings.triggered.connect(self.open_settings)
        menu.addAction(settings)
        diag = QAction(self.t["diagnostics"], menu)
        diag.triggered.connect(self.open_diagnostics)
        menu.addAction(diag)
        menu.addSeparator()
        quit_action = QAction(self.t["quit"], menu)
        quit_action.triggered.connect(self.quit)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_window() if reason == QSystemTrayIcon.Trigger else None)

    def icon(self) -> QIcon:
        brand = QIcon(str(app_icon_path()))
        if not brand.isNull() and self.status != ViewState.RECORDING:
            return brand
        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#d32f2f" if self.status == ViewState.RECORDING else "#263238"))
        painter.drawEllipse(3, 3, 58, 58)
        painter.setBrush(QColor("white"))
        painter.drawEllipse(24, 24, 16, 16)
        painter.end()
        return QIcon(pixmap)

    def render_status(self, message: str | None = None) -> None:
        text = message or self.t[self.status.value]
        self.status_label.setText(text)
        self.recording_pulse.set_recording(
            self.status == ViewState.RECORDING,
            self.state.config.get("hud", {}).get("reduce_motion", False),
        )
        profile = self.t["privacy"] if self.state.config.get("profile") == "privacy" else self.t["speed"]
        self.detail_label.setText(f"{self.t['profile']}: {profile} · {self.state.config.get('hotkey', 'win+alt')}")
        busy = self.status == ViewState.PROCESSING
        self.action_button.setEnabled(not busy)
        self.action_button.setText(self.t["stop"] if self.status == ViewState.RECORDING else self.t["record"])
        self.tray_action.setText(self.action_button.text())
        self.tray.setIcon(self.icon())
        self.tray.setToolTip(f"{APP_NAME} — {text}")
        self.hud.show_status(self.status, message)

    def set_state(self, status: ViewState, message: str | None = None) -> None:
        self.status = status
        self.render_status(message)
        if status in {ViewState.INSERTED, ViewState.ERROR}:
            QTimer.singleShot(2500, lambda: self.set_state(ViewState.IDLE) if self.status == status else None)

    # Compatibility API consumed by hotkey.py and file_transcriber.py.  Those
    # callers are worker threads, so they only enqueue operations for Qt.
    def set_recording(self, recording: bool) -> None:
        self.ui_events.put(("recording", recording))

    def notify(self, title: str, message: str) -> None:
        self.ui_events.put(("notification", title, message))

    def drain_worker_events(self) -> None:
        try:
            while True:
                event = self.ui_events.get_nowait()
                if event[0] == "recording":
                    self.set_state(ViewState.RECORDING if event[1] else ViewState.PROCESSING)
                elif event[0] == "notification":
                    status = ViewState.ERROR if event[1].lower() in {"error", "ошибка"} else self.status
                    self.set_state(status, event[2])
                    self.tray.showMessage(
                        event[1],
                        event[2],
                        QSystemTrayIcon.Warning if status == ViewState.ERROR else QSystemTrayIcon.Information,
                    )
                elif event[0] == "transcript":
                    self.last_result.setPlainText(event[1])
                    history = self.state.config.get("history", {})
                    if history.get("enabled", False):
                        from history_store import HistoryStore

                        HistoryStore().append(event[1], history.get("retention_days", 30))
        except queue.Empty:
            pass
        events = getattr(self.state, "tk_queue", None)
        if events is None:
            return
        try:
            while True:
                event = events.get_nowait()
                command = event[0] if event else ""
                if command == "show_hud":
                    self.set_state(ViewState.RECORDING)
                elif command == "processing":
                    self.set_state(ViewState.PROCESSING)
                elif command == "hide_hud":
                    self.set_state(ViewState.IDLE)
                elif command == "inserted":
                    self.set_state(ViewState.INSERTED)
                elif command == "error":
                    self.set_state(ViewState.ERROR, event[1] if len(event) > 1 else None)
                elif command == "idle":
                    self.set_state(ViewState.IDLE)
                elif command == "backend_switch":
                    self.set_state(ViewState.PROCESSING, event[1] if len(event) > 1 else None)
                elif command == "show_settings":
                    self.open_settings()
                elif command == "open_file_dialog":
                    self.transcribe_file()
        except queue.Empty:
            pass

    def toggle_recording(self) -> None:
        listener = getattr(self.state, "hotkey_listener", None)
        if self.status == ViewState.PROCESSING:
            self.notify(APP_NAME, self.t["already_processing"])
            return
        if listener and hasattr(listener, "on_hotkey"):
            threading.Thread(target=listener.on_hotkey, daemon=True, name="UiDictationAction").start()
        else:
            self.notify(self.t["error"], "Dictation controller is unavailable.")

    def transcribe_file(self) -> None:
        worker = getattr(self.state, "file_transcriber", None)
        if worker is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self.window,
            self.t["file"],
            "",
            "Audio/video (*.mp3 *.wav *.m4a *.ogg *.flac *.aac *.wma *.opus *.mp4 *.mkv *.webm *.avi *.mov)",
        )
        if not path:
            return
        if self.state.is_recording.is_set() or self.state.is_file_transcribing.is_set():
            self.notify(APP_NAME, self.t["already_processing"])
            return
        self.set_state(ViewState.PROCESSING)
        if hasattr(worker, "start"):
            if not worker.start(path):
                self.set_state(ViewState.IDLE)
                self.notify(APP_NAME, self.t["already_processing"])
        else:
            threading.Thread(
                target=worker._transcribe_in_background, args=(path,), daemon=True, name="FileTranscribeThread"
            ).start()

    def open_settings(self) -> None:
        if self.is_busy():
            self.notify(APP_NAME, self.t["already_processing"])
            return
        SettingsDialog(self).exec()

    def open_onboarding(self) -> None:
        SettingsDialog(self, onboarding=True).exec()

    def open_diagnostics(self) -> None:
        DiagnosticsDialog(self).exec()

    def save_config(self, config: dict) -> None:
        operation_lock = getattr(self.state, "operation_lock", None) or threading.RLock()
        with operation_lock:
            self._save_config_locked(config)

    def _save_config_locked(self, config: dict) -> None:
        if self.is_busy():
            raise RuntimeError(self.t["already_processing"])
        old = deepcopy(self.state.config)
        listener = getattr(self.state, "hotkey_listener", None)
        hotkey_changed = (old.get("hotkey"), old.get("hotkey_mode")) != (
            config.get("hotkey"),
            config.get("hotkey_mode"),
        )
        if listener and hotkey_changed and hasattr(listener, "reload_hotkey"):
            listener.reload_hotkey(config["hotkey"], config.get("hotkey_mode", "toggle"))
        saver = getattr(self.state, "save_config", None)
        try:
            if callable(saver):
                saver(config)
                config = self.state.config
            elif hasattr(self.state, "config_store"):
                self.state.config_store.save(config)
            else:
                raise RuntimeError("No atomic configuration store is available.")
        except Exception:
            if listener and hotkey_changed and hasattr(listener, "reload_hotkey"):
                listener.reload_hotkey(old["hotkey"], old.get("hotkey_mode", "toggle"))
            raise
        self.state.config = config
        if listener:
            if getattr(listener, "_recorder", None) is not None and old.get("device_index") != config.get(
                "device_index"
            ):
                listener._recorder.shutdown()
                listener._recorder = None
            if getattr(listener, "_transcriber", None) is not None:
                listener._transcriber.config = config
                if old.get("model") != config.get("model"):
                    listener._transcriber.reload(config.get("model", "small"))
        file_worker = getattr(self.state, "file_transcriber", None)
        if file_worker and getattr(file_worker, "_transcriber", None) is not None:
            file_worker._transcriber.config = config
        language_changed = old.get("ui_language") != config.get("ui_language")
        self.lang = ui_language(config)
        self.t = STRINGS[self.lang]
        self.hud.config = config
        self.hud.lang = self.lang
        if language_changed:
            self.build_window()
            self.build_tray()
        self.render_status()

    def is_busy(self) -> bool:
        machine = getattr(self.state, "dictation_state", None)
        status = getattr(machine, "status", None)
        status_value = getattr(status, "value", status)
        return (
            status_value in {"recording", "processing"}
            or self.state.is_recording.is_set()
            or self.state.is_file_transcribing.is_set()
        )

    def show_window(self) -> None:
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def close_to_tray(self, event) -> None:
        event.ignore()
        self.window.hide()
        self.tray.showMessage(APP_NAME, self.t["closed"])

    def quit(self) -> None:
        self.poller.stop()
        self.hud.close()
        self.tray.hide()
        listener = getattr(self.state, "hotkey_listener", None)
        if listener and hasattr(listener, "shutdown"):
            try:
                listener.shutdown()
            except Exception:
                logger.exception("Hotkey shutdown failed")
        worker = getattr(self.state, "file_transcriber", None)
        if worker and hasattr(worker, "shutdown"):
            worker.shutdown()
        QCoreApplication.quit()


def run_qt(state) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(QIcon(str(app_icon_path())))
    app.setQuitOnLastWindowClosed(False)
    ui = WhisperTrayUi(state)
    state.tray_app = ui
    state.on_transcript = lambda text: ui.ui_events.put(("transcript", text))
    listener = getattr(state, "hotkey_listener", None)
    if listener:
        state.hotkey_thread = threading.Thread(target=listener.run, daemon=True, name="HotkeyThread")
        state.hotkey_thread.start()
    if not state.config.get("onboarding_complete", False):
        QTimer.singleShot(0, ui.open_onboarding)
    else:
        ui.show_window()
    return app.exec()
