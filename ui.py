"""The single-threaded PySide6 presentation layer for WhisperTray.

Background workers may continue to put their legacy events into ``state.tk_queue``
during migration. UI actions use the minimal duck-typed controller API instead.
"""

from __future__ import annotations

import json
import logging
import math
import queue
import sys
import tempfile
import threading
import time
from copy import deepcopy
from enum import Enum
from pathlib import Path

from PySide6.QtCore import (
    QCoreApplication,
    QEasingCurve,
    QEvent,
    QLocale,
    QObject,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    QUrl,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QDesktopServices,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QIcon,
    QImage,
    QKeyEvent,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QProxyStyle,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QStyleFactory,
    QSystemTrayIcon,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)
APP_NAME = "WhisperTray"
# Design tokens: a warm graphite base with the brand's ember accent and a
# calm, distinct hue per dictation state. Contrast checked against WCAG AA.
TOKENS = {
    "bg": "#141519",
    "surface": "#1c1e23",
    "surface_2": "#24272d",
    "surface_3": "#2c3037",
    "line": "#2f3239",
    "control_line": "#6c717c",
    "text": "#f3eee6",
    "text_2": "#aca69c",
    "text_3": "#9a948b",
    "accent": "#ff7a5c",
    "accent_hover": "#ff9479",
    "on_accent": "#1f120e",
    "recording": "#ff6b57",
    "processing": "#8b93ff",
    "success": "#43d39e",
    "warning": "#f4b545",
}

STATE_ACCENTS = {
    "idle": TOKENS["text_3"],
    "preparing": TOKENS["processing"],
    "recording": TOKENS["recording"],
    "processing": TOKENS["processing"],
    "inserted": TOKENS["success"],
    "error": TOKENS["warning"],
}

_STYLE_TEMPLATE = """
QWidget {
    background: transparent;
    color: @text;
    font-size: 13px;
}
QMainWindow, QDialog, QMessageBox { background: @bg; }
QToolTip {
    background: @surface_3;
    color: @text;
    border: 1px solid @line;
    border-radius: 6px;
    padding: 5px 8px;
}
QLabel#statusLabel { color: @text; font-size: 19px; font-weight: 700; }
QLabel#detailLabel { color: @text_2; font-size: 12px; }
QLabel#appTitle { color: @text; font-size: 17px; font-weight: 700; }
QLabel#appCaption { color: @text_2; font-size: 12px; }
QLabel#sectionLabel { color: @text_2; font-size: 12px; font-weight: 600; }
QLabel#formLabel { color: @text_2; }
QLabel#sectionTitle {
    color: @text;
    font-size: 15px;
    font-weight: 700;
    padding: 10px 0 2px 0;
}
QLabel#hintLabel { color: @text_3; font-size: 12px; }
QLabel#stepLabel { color: @accent; font-size: 12px; font-weight: 700; }
QLabel#pageTitle { color: @text; font-size: 21px; font-weight: 700; }
QLabel#keycap {
    background: @surface_2;
    border: 1px dashed @control_line;
    border-radius: 12px;
    color: @text_2;
    font-size: 15px;
    font-weight: 600;
    padding: 18px;
}
QLabel#profileBadge {
    background: @surface_2;
    border: 1px solid @line;
    border-radius: 11px;
    color: @text_2;
    padding: 4px 10px;
    font-size: 11px;
    font-weight: 600;
}
QLabel a, QLabel#linkLabel { color: @accent; }
QFrame#statusCard, QFrame#panel {
    background: @surface;
    border: 1px solid @line;
    border-radius: 14px;
}
QFrame#statusCard[state="recording"] { border-color: #6b3a33; }
QFrame#statusCard[state="processing"], QFrame#statusCard[state="preparing"] { border-color: #3d4170; }
QFrame#statusCard[state="inserted"] { border-color: #26584a; }
QFrame#statusCard[state="error"] { border-color: #6a5326; }
QFrame#divider { background: @line; border: none; max-height: 1px; min-height: 1px; }
QPlainTextEdit, QLineEdit, QComboBox {
    background: @surface_2;
    border: 1px solid @control_line;
    border-radius: 8px;
    padding: 7px 9px;
    color: @text;
    selection-background-color: @accent;
    selection-color: @on_accent;
}
QPlainTextEdit#resultText {
    background: @surface;
    border: 1px solid @line;
    border-radius: 12px;
    padding: 10px 12px;
    font-size: 14px;
}
QLineEdit:focus, QComboBox:focus, QPlainTextEdit:focus { border: 1px solid @accent; }
QLineEdit:read-only { color: @text; }
QComboBox::drop-down { border: none; width: 26px; }
QComboBox QAbstractItemView {
    background: @surface_2;
    border: 1px solid @line;
    border-radius: 8px;
    color: @text;
    outline: none;
    padding: 4px;
    selection-background-color: @surface_3;
    selection-color: @text;
}
QPushButton {
    background: @surface_3;
    border: 1px solid #3b3f47;
    border-radius: 8px;
    padding: 7px 14px;
    color: @text;
    font-weight: 600;
}
QPushButton:hover { background: #343841; border-color: #4a4f59; }
QPushButton:pressed { background: #272a30; }
QPushButton:focus { border: 1px solid @accent; }
QPushButton:disabled { color: #767a82; background: #22252a; border-color: #2c2f35; }
QPushButton#primaryAction {
    background: @accent;
    border: 1px solid @accent;
    color: @on_accent;
    font-size: 14px;
    font-weight: 700;
    min-height: 30px;
    padding: 9px 18px;
    border-radius: 10px;
}
QPushButton#primaryAction:hover, QPushButton#dialogPrimary:hover {
    background: @accent_hover;
    border-color: @accent_hover;
}
QPushButton#dialogPrimary {
    background: @accent;
    border: 1px solid @accent;
    color: @on_accent;
    font-weight: 700;
    padding: 7px 18px;
}
QPushButton#dialogPrimary:focus { border: 2px solid @text; }
QPushButton#primaryAction:focus { border: 2px solid @text; }
QPushButton#primaryAction:disabled { background: #4a3530; border-color: #4a3530; color: #a8948e; }
QPushButton#primaryAction[recording="true"] {
    background: @surface_3;
    border: 1px solid @recording;
    color: @text;
}
QPushButton#secondaryAction, QPushButton#ghostAction {
    background: transparent;
    border: 1px solid transparent;
    color: @text_2;
}
QPushButton#secondaryAction:hover, QPushButton#ghostAction:hover {
    background: @surface_2;
    color: @text;
}
QPushButton#secondaryAction:focus, QPushButton#ghostAction:focus { border: 1px solid @accent; }
QPushButton#secondaryAction:disabled, QPushButton#ghostAction:disabled { color: #6d7178; background: transparent; }
QPushButton#disclosure {
    background: transparent;
    border: none;
    color: @text_2;
    font-weight: 600;
    padding: 6px 0;
    text-align: left;
}
QPushButton#disclosure:hover { color: @text; }
QPushButton#disclosure:focus { color: @accent; }
QCheckBox { spacing: 10px; color: @text; padding: 2px 0; }
QCheckBox:disabled { color: #767a82; }
QProgressBar {
    background: @surface_2;
    border: none;
    border-radius: 3px;
    color: @text_2;
    max-height: 6px;
    min-height: 6px;
    text-align: center;
}
QProgressBar::chunk { background: @accent; border-radius: 3px; }
QProgressBar#levelMeter::chunk { background: @recording; }
QProgressBar#taskProgress::chunk { background: @processing; }
QTabWidget::pane { border: none; border-top: 1px solid @line; top: -1px; }
QTabBar { qproperty-drawBase: 0; }
QTabBar::tab {
    background: transparent;
    color: @text_2;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 10px 14px;
    margin-right: 6px;
    font-weight: 600;
}
QTabBar::tab:hover { color: @text; }
QTabBar::tab:selected { color: @text; border-bottom: 2px solid @accent; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #3a3e46; border-radius: 3px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: #4a4f59; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QMenu {
    background: @surface;
    border: 1px solid @line;
    border-radius: 10px;
    padding: 6px;
    color: @text;
}
QMenu::item { padding: 7px 26px 7px 12px; border-radius: 6px; background: transparent; }
QMenu::item:selected { background: @surface_3; }
QMenu::item:disabled { color: #767a82; }
QMenu::separator { height: 1px; background: @line; margin: 5px 6px; }
"""


def _render_style(template: str) -> str:
    # Longest names first so "@surface_2" is not consumed by "@surface".
    for name in sorted(TOKENS, key=len, reverse=True):
        template = template.replace(f"@{name}", TOKENS[name])
    return template


APP_STYLE = _render_style(_STYLE_TEMPLATE)


def _chevron_file(color: str, name: str) -> Path | None:
    """Write a crisp combo-box chevron once; stylesheets can only load images from files."""
    folder = Path(tempfile.gettempdir()) / "whispertray-ui"
    path = folder / name
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            image = QImage(24, 24, QImage.Format_ARGB32_Premultiplied)
            image.fill(Qt.transparent)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setPen(QPen(QColor(color), 3.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            chevron = QPainterPath()
            chevron.moveTo(5, 9)
            chevron.lineTo(12, 16)
            chevron.lineTo(19, 9)
            painter.drawPath(chevron)
            painter.end()
            if not image.save(str(path), "PNG"):
                return None
    except OSError:
        logger.debug("Could not write the combo-box chevron", exc_info=True)
        return None
    return path


def window_style() -> str:
    """The app stylesheet plus rules that need generated image files."""
    enabled = _chevron_file(TOKENS["text_2"], "chevron-down.png")
    disabled = _chevron_file("#5c6068", "chevron-down-disabled.png")
    if enabled is None or disabled is None:
        return APP_STYLE
    return APP_STYLE + (
        "QComboBox::drop-down { subcontrol-origin: padding; subcontrol-position: center right; "
        "width: 28px; border: none; }"
        f"QComboBox::down-arrow {{ image: url(\"{enabled.as_posix()}\"); width: 12px; height: 12px; }}"
        f"QComboBox::down-arrow:disabled {{ image: url(\"{disabled.as_posix()}\"); }}"
    )


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
    PREPARING = "preparing"
    RECORDING = "recording"
    PROCESSING = "processing"
    INSERTED = "inserted"
    ERROR = "error"


STRINGS = {
    "ru": {
        "title": "WhisperTray",
        "idle": "Готов к диктовке",
        "preparing": "Готовлю распознавание…",
        "recording": "Идёт запись",
        "processing": "Распознаю речь…",
        "inserted": "Текст вставлен",
        "error": "Ошибка",
        "record": "Начать запись",
        "stop": "Остановить и распознать",
        "settings": "Настройки",
        "open_app": "Открыть WhisperTray",
        "idle_hint": "Нажмите {hotkey} в любой программе",
        "recognition": "Распознавание",
        "dictation": "Диктовка",
        "advanced": "Дополнительно",
        "privacy_hint": "Аудио обрабатывается только на этом компьютере.",
        "ai_cleanup": "Улучшать текст с помощью ИИ",
        "ai_cleanup_hint": (
            "Расставит знаки препинания, уберёт «э-э» и исправит ошибки распознавания. "
            "Работает в режиме «Скорость» через тот же ключ Groq."
        ),
        "ai_cleanup_needs_speed": "Доступно только в режиме «Скорость»: текст обрабатывается через Groq.",
        "hud_section": "Индикатор состояния",
        "bottom_center": "Снизу по центру",
        "check": "Проверить",
        "diagnostics": "Диагностика",
        "quit": "Выйти",
        "profile": "Профиль",
        "privacy": "Приватность (локально)",
        "speed": "Скорость (Groq)",
        "microphone": "Микрофон",
        "test_mic": "Проверить микрофон",
        "language": "Язык распознавания",
        "hotkey": "Горячая клавиша",
        "change_hotkey": "Изменить",
        "hotkey_capture_title": "Новое сочетание",
        "hotkey_capture_prompt": "Нажмите нужное сочетание клавиш",
        "hotkey_capture_hint": "Например, Ctrl + Space. Esc — отмена.",
        "hotkey_capture_saved": "Сохранено: {hotkey}",
        "hotkey_capture_failed": "Не удалось применить это сочетание. Попробуйте другое.",
        "hotkey_mode": "Режим клавиши",
        "toggle": "Нажать / нажать",
        "hold": "Удерживать",
        "model": "Локальная модель",
        "appearance": "Интерфейс",
        "general": "Основное",
        "data_system": "Данные и система",
        "autostart": "Запускать вместе с системой",
        "autostart_hint": "WhisperTray будет автоматически запускаться после входа в систему.",
        "start_in_tray": "Запускать сразу в трее",
        "start_in_tray_hint": "Главное окно не откроется, но диктовка и горячая клавиша будут готовы.",
        "startup": "Запуск приложения",
        "autostart_error": "Не удалось изменить автозапуск.",
        "diagnostics_info": "Безопасная техническая информация без ключей, аудио и текста диктовки.",
        "diagnostics_exported": "Диагностика экспортирована без секретов, аудио и текста диктовки.",
        "diagnostics_failed": "Не удалось экспортировать диагностику.",
        "show_diagnostics": "Показать техническую информацию",
        "hide_diagnostics": "Скрыть техническую информацию",
        "hud": "Показывать индикатор у края экрана",
        "contrast": "Высокий контраст",
        "motion": "Уменьшить анимацию",
        "position": "Расположение",
        "bottom_right": "Справа снизу",
        "bottom_left": "Слева снизу",
        "history": "Сохранять локальную историю",
        "history_section": "Локальная история",
        "retention": "Хранить историю",
        "days": "дней",
        "last_result": "Последний результат",
        "last_result_hint": "Здесь появится последняя расшифровка",
        "tagline": "Диктовка в любой программе",
        "onboarding": "Первый запуск",
        "welcome": "Выберите, как WhisperTray будет обрабатывать аудио.",
        "cloud_note": "Аудио отправляется в Groq для распознавания. Нужен ваш API-ключ.",
        "fallback": "Если Groq недоступен, распознавать локально",
        "test_key": "Проверить ключ",
        "groq_key": "Ключ Groq API",
        "keychain_saved": "Сохранён в системном хранилище ключей",
        "default_mic": "Системный микрофон",
        "prepare_model": "Скачать модель",
        "ui_language": "Язык интерфейса",
        "clear_history": "Очистить локальную историю",
        "export_diagnostics": "Экспортировать диагностику…",
        "save": "Сохранить",
        "cancel": "Отмена",
        "saved": "Настройки сохранены.",
        "already_processing": "Уже обрабатываю запись",
        "file": "Транскрибировать файл…",
        "copy": "Скопировать",
        "save_as": "Сохранить как…",
        "open_folder": "Открыть папку",
        "history_view": "Открыть историю",
        "history_search": "Поиск по истории",
        "history_empty": "История пока пуста.",
        "history_cleared": "Локальная история очищена.",
        "cancel_job": "Отменить",
        "retry": "Повторить",
        "retry_saved_audio": "Запись сохранена на пять минут — можно повторить.",
        "silent_warning": "Не слышу речь — проверьте микрофон.",
        "recording_details": "{elapsed} · уровень {level}%",
        "mic_recording": "Записываю тест 5 секунд…",
        "mic_ready": "Пробная запись готова. Нажмите «Прослушать».",
        "mic_play": "Прослушать",
        "mic_cancel": "Отменить тест",
        "model_submitted": "Подготовка модели запущена.",
        "model_ready": "Модель {model} готова.",
        "model_failed": "Не удалось подготовить модель.",
        "recognition_auto": "Автоматически",
        "file_local_offer": "Этот файл нельзя отправить в Groq. Обработать его локально?",
        "process_local": "Обработать локально",
        "close": "Закрыть",
        "file_result_saved": "Расшифровка сохранена: {path}",
        "save_failed": "Текст распознан, но файл сохранить не удалось: {error}",
        "controller_unavailable": "Контроллер диктовки недоступен.",
        "generic_error": "Не удалось выполнить операцию.",
        "closed": "WhisperTray работает в трее — горячая клавиша по-прежнему доступна.",
        "hotkey_empty": "Укажите горячую клавишу.",
        "hotkey_invalid": "Не удалось распознать это сочетание клавиш. Например: Ctrl+Space.",
        "groq_required": "Для профиля «Скорость» нужен ключ Groq API.",
        "mic_available": "Микрофон доступен и готов к записи.",
        "mic_failed": "Не удалось использовать микрофон: {error}",
        "key_enter": "Вставьте ключ Groq API, затем нажмите «Проверить ключ».",
        "key_testing": "Проверяю…",
        "key_valid": "Ключ Groq работает.",
        "key_failed": "Не удалось проверить ключ Groq. Проверьте ключ и подключение к интернету.",
        "settings_save_failed": "Не удалось безопасно сохранить настройки. Предыдущие настройки не изменены.",
        "config_recovered": (
            "Файл настроек был повреждён. Его резервная копия сохранена рядом, "
            "а WhisperTray запущен с безопасными настройками."
        ),
        "config_backup_failed": (
            "Файл настроек повреждён и не был перезаписан: резервную копию создать не удалось. "
            "WhisperTray временно использует безопасные настройки."
        ),
        "config_unavailable": (
            "Файл настроек недоступен и не был перезаписан. "
            "WhisperTray временно использует безопасные настройки."
        ),
    },
    "en": {
        "title": "WhisperTray",
        "idle": "Ready for dictation",
        "preparing": "Preparing transcription…",
        "recording": "Recording",
        "processing": "Transcribing…",
        "inserted": "Text inserted",
        "error": "Error",
        "record": "Start recording",
        "stop": "Stop and transcribe",
        "settings": "Settings",
        "open_app": "Open WhisperTray",
        "idle_hint": "Press {hotkey} in any app",
        "recognition": "Recognition",
        "dictation": "Dictation",
        "advanced": "Advanced",
        "privacy_hint": "Audio is processed on this computer only.",
        "ai_cleanup": "Polish text with AI",
        "ai_cleanup_hint": (
            "Adds punctuation, removes filler words and fixes recognition mistakes. "
            "Works in Speed mode with the same Groq key."
        ),
        "ai_cleanup_needs_speed": "Available in Speed mode only: the text is processed by Groq.",
        "hud_section": "Status overlay",
        "bottom_center": "Bottom center",
        "check": "Test",
        "diagnostics": "Diagnostics",
        "quit": "Quit",
        "profile": "Profile",
        "privacy": "Privacy (local)",
        "speed": "Speed (Groq)",
        "microphone": "Microphone",
        "test_mic": "Test microphone",
        "language": "Recognition language",
        "hotkey": "Hotkey",
        "change_hotkey": "Change",
        "hotkey_capture_title": "New shortcut",
        "hotkey_capture_prompt": "Press the shortcut you want to use",
        "hotkey_capture_hint": "For example, Ctrl + Space. Press Esc to cancel.",
        "hotkey_capture_saved": "Saved: {hotkey}",
        "hotkey_capture_failed": "This shortcut could not be applied. Try another one.",
        "hotkey_mode": "Hotkey mode",
        "toggle": "Press / press",
        "hold": "Hold to record",
        "model": "Local model",
        "appearance": "Appearance",
        "general": "General",
        "data_system": "Data & system",
        "autostart": "Launch with the system",
        "autostart_hint": "WhisperTray will start automatically after you sign in.",
        "start_in_tray": "Start directly in the tray",
        "start_in_tray_hint": "The main window stays closed while dictation and the hotkey remain ready.",
        "startup": "Application startup",
        "autostart_error": "Could not change launch-at-login settings.",
        "diagnostics_info": "Safe technical information without keys, audio, or dictated text.",
        "diagnostics_exported": "Diagnostics exported without secrets, audio, or dictated text.",
        "diagnostics_failed": "Diagnostics could not be exported.",
        "show_diagnostics": "Show technical information",
        "hide_diagnostics": "Hide technical information",
        "hud": "Show the overlay near the screen edge",
        "contrast": "High contrast",
        "motion": "Reduce motion",
        "position": "Position",
        "bottom_right": "Bottom right",
        "bottom_left": "Bottom left",
        "history": "Keep local history",
        "history_section": "Local history",
        "retention": "Keep history",
        "days": "days",
        "last_result": "Last result",
        "last_result_hint": "Your latest transcription will appear here",
        "tagline": "Dictation in any application",
        "onboarding": "First launch",
        "welcome": "Choose how WhisperTray processes audio.",
        "cloud_note": "Audio is sent to Groq for transcription. Your own API key is required.",
        "fallback": "Transcribe locally when Groq is unavailable",
        "test_key": "Test key",
        "groq_key": "Groq API key",
        "keychain_saved": "Saved in the system keychain",
        "default_mic": "System default",
        "prepare_model": "Download model",
        "ui_language": "Interface language",
        "clear_history": "Clear local history",
        "export_diagnostics": "Export diagnostics…",
        "save": "Save",
        "cancel": "Cancel",
        "saved": "Settings saved.",
        "already_processing": "Already processing a recording",
        "file": "Transcribe file…",
        "copy": "Copy",
        "save_as": "Save as…",
        "open_folder": "Open folder",
        "history_view": "View history",
        "history_search": "Search history",
        "history_empty": "History is empty.",
        "history_cleared": "Local history was cleared.",
        "cancel_job": "Cancel",
        "retry": "Retry",
        "retry_saved_audio": "The recording is kept for five minutes so you can retry.",
        "silent_warning": "No speech detected — check the microphone.",
        "recording_details": "{elapsed} · level {level}%",
        "mic_recording": "Recording a 5-second test…",
        "mic_ready": "The test recording is ready. Select Play.",
        "mic_play": "Play",
        "mic_cancel": "Cancel test",
        "model_submitted": "Model preparation started.",
        "model_ready": "Model {model} is ready.",
        "model_failed": "The model could not be prepared.",
        "recognition_auto": "Automatic",
        "file_local_offer": "This file cannot be sent to Groq. Process it locally?",
        "process_local": "Process locally",
        "close": "Close",
        "file_result_saved": "Transcript saved: {path}",
        "save_failed": "The text was transcribed, but the file could not be saved: {error}",
        "controller_unavailable": "The dictation controller is unavailable.",
        "generic_error": "The operation could not be completed.",
        "closed": "WhisperTray keeps running in the tray — the hotkey still works.",
        "hotkey_empty": "Enter a hotkey.",
        "hotkey_invalid": "This shortcut could not be recognized. Example: Ctrl+Space.",
        "groq_required": "The Speed profile requires a Groq API key.",
        "mic_available": "The microphone is available and ready.",
        "mic_failed": "The microphone could not be used: {error}",
        "key_enter": "Paste a Groq API key, then select Test key.",
        "key_testing": "Testing…",
        "key_valid": "The Groq key works.",
        "key_failed": "The Groq key could not be verified. Check the key and your internet connection.",
        "settings_save_failed": "Settings could not be saved safely. The previous settings were not changed.",
        "config_recovered": (
            "The settings file was damaged. A backup copy was saved beside it, "
            "and WhisperTray started with safe settings."
        ),
        "config_backup_failed": (
            "The settings file is damaged and was not overwritten because a backup could not be created. "
            "WhisperTray is temporarily using safe settings."
        ),
        "config_unavailable": (
            "The settings file is unavailable and was not overwritten. "
            "WhisperTray is temporarily using safe settings."
        ),
    },
}

ERROR_KEYS = {
    "microphone_unavailable": {
        "ru": "Микрофон недоступен. Выберите другое устройство или проверьте разрешения.",
        "en": "The microphone is unavailable. Choose another device or check permissions.",
    },
    "empty_audio": {"ru": "Речь не обнаружена.", "en": "No speech was detected."},
    "file_missing": {"ru": "Выбранный файл не найден.", "en": "The selected file was not found."},
    "file_empty": {"ru": "Выбранный файл пуст.", "en": "The selected file is empty."},
    "file_format": {
        "ru": "Groq не поддерживает формат этого файла.",
        "en": "Groq does not support this file format.",
    },
    "file_too_large": {
        "ru": "Файл превышает допустимый для Groq размер.",
        "en": "The file exceeds Groq's size limit.",
    },
    "cloud_auth": {
        "ru": "Ключ Groq отклонён. Проверьте ключ в настройках.",
        "en": "The Groq key was rejected. Check it in Settings.",
    },
    "cloud_timeout": {"ru": "Groq не ответил вовремя.", "en": "Groq did not respond in time."},
    "timeout": {"ru": "Время ожидания операции истекло.", "en": "The operation timed out."},
    "cloud_rate_limit": {
        "ru": "Достигнут лимит запросов Groq. Повторите позже.",
        "en": "The Groq request limit was reached. Try again later.",
    },
    "network": {"ru": "Нет соединения с Groq.", "en": "Could not connect to Groq."},
    "cloud_failed": {"ru": "Groq не смог распознать запись.", "en": "Groq could not transcribe the audio."},
    "local_model_missing": {
        "ru": "Локальная модель недоступна. Подготовьте её в настройках.",
        "en": "The local model is unavailable. Prepare it in Settings.",
    },
    "recording_failed": {"ru": "Не удалось завершить запись.", "en": "Could not finish the recording."},
    "worker_start_failed": {
        "ru": "Не удалось запустить распознавание.",
        "en": "Could not start transcription.",
    },
    "transcription_failed": {
        "ru": "Не удалось распознать запись.",
        "en": "Could not transcribe the audio.",
    },
    "save_failed": {
        "ru": "Текст распознан, но автоматически сохранить файл не удалось.",
        "en": "The text was transcribed, but the file could not be saved automatically.",
    },
    "cancelled": {"ru": "Операция отменена.", "en": "The operation was cancelled."},
    "clipboard_fallback": {
        "ru": "Автовставка не удалась. Полный текст скопирован в буфер обмена.",
        "en": "Automatic insertion failed. The complete text was copied to the clipboard.",
    },
}

# One-line, friendly variants for the floating HUD. The detailed message stays
# in the main window status card.
HUD_ERROR_KEYS = {
    "microphone_unavailable": {"ru": "Микрофон недоступен", "en": "Microphone unavailable"},
    "empty_audio": {"ru": "Речь не распознана", "en": "No speech detected"},
    "file_missing": {"ru": "Файл не найден", "en": "File not found"},
    "file_empty": {"ru": "Файл пуст", "en": "File is empty"},
    "file_format": {"ru": "Формат не подходит", "en": "Unsupported format"},
    "file_too_large": {"ru": "Файл слишком большой", "en": "File is too large"},
    "cloud_auth": {"ru": "Ключ Groq не принят", "en": "Groq key rejected"},
    "cloud_timeout": {"ru": "Groq не отвечает", "en": "Groq is not responding"},
    "timeout": {"ru": "Время ожидания вышло", "en": "Timed out"},
    "cloud_rate_limit": {"ru": "Лимит Groq, подождите", "en": "Groq limit, try later"},
    "network": {"ru": "Нет связи с Groq", "en": "No connection to Groq"},
    "cloud_failed": {"ru": "Groq не распознал речь", "en": "Groq couldn’t transcribe"},
    "local_model_missing": {"ru": "Модель не готова", "en": "Model not ready"},
    "recording_failed": {"ru": "Запись не сохранилась", "en": "Recording failed"},
    "worker_start_failed": {"ru": "Не удалось начать", "en": "Couldn’t start"},
    "transcription_failed": {"ru": "Речь не распознана", "en": "Couldn’t transcribe"},
    "save_failed": {"ru": "Файл не сохранён", "en": "File not saved"},
    "clipboard_fallback": {"ru": "Скопировано — вставьте вручную", "en": "Copied — paste manually"},
    "hotkey": {"ru": "Горячая клавиша недоступна", "en": "Hotkey unavailable"},
    "microphone": {"ru": "Микрофон недоступен", "en": "Microphone unavailable"},
    "generic": {"ru": "Что-то пошло не так", "en": "Something went wrong"},
}

# Short labels for the HUD pill; the main window uses the fuller STRINGS.
HUD_LABELS = {
    "ru": {"preparing": "Готовлю…", "recording": "Слушаю", "processing": "Распознаю…", "inserted": "Вставлено"},
    "en": {"preparing": "Preparing…", "recording": "Listening", "processing": "Transcribing…", "inserted": "Inserted"},
}

STAGE_LABELS = {
    "downloading_model": {"ru": "Загружаю локальную модель…", "en": "Downloading local model…"},
    "loading_model": {"ru": "Загружаю модель в память…", "en": "Loading model into memory…"},
    "transcribing": {"ru": "Распознаю локально…", "en": "Transcribing locally…"},
    "cloud_transcribing": {"ru": "Распознаю через Groq…", "en": "Transcribing with Groq…"},
    "retry_wait": {"ru": "Повторяю временно неудачный запрос…", "en": "Retrying a temporary failure…"},
    "reading_file": {"ru": "Читаю файл…", "en": "Reading file…"},
    "ready": {"ru": "Готово.", "en": "Ready."},
    "capturing": {"ru": "Идёт запись…", "en": "Recording…"},
    "validating": {"ru": "Проверяю файл…", "en": "Checking file…"},
    "loading": {"ru": "Подготавливаю модель…", "en": "Preparing model…"},
    "retrying": {"ru": "Повторяю распознавание…", "en": "Retrying transcription…"},
    "local_fallback": {"ru": "Переключаюсь на локальную модель…", "en": "Switching to the local model…"},
    "polishing": {"ru": "Привожу текст в порядок…", "en": "Polishing text…"},
    "limit_warning": {
        "ru": "Достигнут предел записи. Начинаю распознавание…",
        "en": "The recording limit was reached. Starting transcription…",
    },
}

ONBOARDING_STRINGS = {
    "en": {
        "heading": "Welcome to WhisperTray",
        "subtitle": "Three quick steps, then you can start dictating",
        "choose_profile": "Where should speech be recognized?",
        "choose_profile_hint": "This choice determines whether audio can leave your computer.",
        "privacy_card": "Audio never leaves this computer\nWorks offline after the model is downloaded",
        "speed_card": (
            "Fast cloud transcription through Groq\nAI adds punctuation and removes filler words\n"
            "Requires internet and your own API key"
        ),
        "continue": "Continue",
        "back": "Back",
        "profile_setup": "Prepare transcription",
        "local_explainer": "Audio stays on this device. The Small model is recommended for most computers.",
        "local_model_hint": "You can download the model now or on the first transcription.",
        "cloud_explainer": (
            "Groq receives audio only for transcription; WhisperTray keeps no copy. "
            "With AI polishing on, Groq also tidies up the recognized text."
        ),
        "cloud_key_hint": "Create a free key in Groq Console, paste it here and test it.",
        "get_groq_key": "Get a Groq API key",
        "audio_ready": "Choose how you will start dictation",
        "audio_hint": "Select a microphone and a shortcut. You can change both later.",
        "hotkey_hint": "Press once to start, press again to stop — or choose “Hold to record”.",
        "audio_controls": "Microphone and shortcut",
        "finish": "Finish setup",
        "step": "Step {current} of 3",
    },
    "ru": {
        "heading": "Добро пожаловать в WhisperTray",
        "subtitle": "Три коротких шага — и можно диктовать",
        "choose_profile": "Где распознавать вашу речь?",
        "choose_profile_hint": "От этого зависит, сможет ли аудио покидать ваш компьютер.",
        "privacy_card": "Аудио не покидает компьютер\nРаботает без интернета после загрузки модели",
        "speed_card": (
            "Быстрое распознавание через Groq\nИИ расставит знаки препинания и уберёт «э-э»\n"
            "Нужны интернет и ваш API-ключ"
        ),
        "continue": "Продолжить",
        "back": "Назад",
        "profile_setup": "Подготовим распознавание",
        "local_explainer": "Аудио останется на этом устройстве. Для большинства компьютеров рекомендуем модель Small.",
        "local_model_hint": "Модель можно скачать сейчас или при первой диктовке.",
        "cloud_explainer": (
            "Groq получает аудио только для распознавания, WhisperTray не хранит копию. "
            "С улучшением текста Groq также приведёт в порядок распознанный текст."
        ),
        "cloud_key_hint": "Создайте бесплатный ключ в Groq Console, вставьте его сюда и проверьте.",
        "get_groq_key": "Получить ключ Groq API",
        "audio_ready": "Выберите, как запускать диктовку",
        "audio_hint": "Укажите микрофон и сочетание клавиш. Позже их можно изменить.",
        "hotkey_hint": "Нажали — запись пошла, нажали ещё раз — остановилась. Или выберите «Удерживать».",
        "audio_controls": "Микрофон и горячая клавиша",
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


def should_show_main_window(config: dict, *, force_show: bool = False) -> bool:
    """Keep startup behavior explicit and independently testable."""
    return force_show or not bool(config.get("start_in_tray", False))


def available_dialog_size(widget: QWidget) -> QSize:
    screen = widget.screen() or QGuiApplication.primaryScreen()
    return screen.availableGeometry().size() if screen is not None else QSize(1280, 720)


def input_devices() -> list[tuple[str, int | None]]:
    try:
        import sounddevice as sd

        return [(str(d["name"]), i) for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0]
    except Exception as exc:
        logger.info("Input device enumeration unavailable: %s", exc)
        return []


def _tinted(color: str | QColor, alpha: int) -> QColor:
    tinted = QColor(color)
    tinted.setAlpha(alpha)
    return tinted


def paint_state_glyph(
    painter: QPainter,
    rect: QRectF,
    state: str,
    color: QColor,
    *,
    phase: float = 0.0,
    animated: bool = True,
) -> None:
    """Draw the per-state glyph (mic, record dot, spinner, check, alert) in ``rect``.

    Shared by the main window status card and the HUD so both speak one
    visual language. ``phase`` is a 0..1 loop position for animated states.
    """
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    center = rect.center()
    size = min(rect.width(), rect.height())
    stroke = max(1.6, size * 0.085)
    pen = QPen(color, stroke, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    if state == "recording":
        if animated:
            ring = size * (0.24 + 0.22 * phase)
            painter.setPen(QPen(_tinted(color, int(190 * (1.0 - phase))), stroke * 0.8))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(center, ring, ring)
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        dot = size * 0.2
        painter.drawEllipse(center, dot, dot)
    elif state in {"processing", "preparing"}:
        radius = size * 0.3
        arc_rect = QRectF(center.x() - radius, center.y() - radius, radius * 2, radius * 2)
        if animated:
            painter.setPen(QPen(_tinted(color, 60), stroke))
            painter.drawEllipse(arc_rect)
            painter.setPen(pen)
            start = int(-phase * 360 * 16)
            painter.drawArc(arc_rect, start + 90 * 16, -110 * 16)
        else:
            painter.setPen(Qt.NoPen)
            painter.setBrush(color)
            dot = size * 0.07
            for offset in (-1, 0, 1):
                painter.drawEllipse(QPointF(center.x() + offset * size * 0.2, center.y()), dot, dot)
    elif state == "inserted":
        painter.setPen(pen)
        path = QPainterPath()
        path.moveTo(center.x() - size * 0.2, center.y() + size * 0.01)
        path.lineTo(center.x() - size * 0.05, center.y() + size * 0.15)
        path.lineTo(center.x() + size * 0.22, center.y() - size * 0.14)
        painter.drawPath(path)
    elif state == "error":
        painter.setPen(pen)
        painter.drawLine(QPointF(center.x(), center.y() - size * 0.2), QPointF(center.x(), center.y() + size * 0.04))
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(QPointF(center.x(), center.y() + size * 0.18), stroke * 0.62, stroke * 0.62)
    else:
        # Idle: a small microphone — capsule, cradle and stem.
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        capsule = QRectF(center.x() - size * 0.09, center.y() - size * 0.26, size * 0.18, size * 0.32)
        painter.drawRoundedRect(capsule, size * 0.09, size * 0.09)
        painter.setPen(QPen(color, stroke * 0.8, Qt.SolidLine, Qt.RoundCap))
        painter.setBrush(Qt.NoBrush)
        cradle = QRectF(center.x() - size * 0.17, center.y() - size * 0.12, size * 0.34, size * 0.3)
        painter.drawArc(cradle, 200 * 16, 140 * 16)
        painter.drawLine(QPointF(center.x(), center.y() + size * 0.18), QPointF(center.x(), center.y() + size * 0.26))
    painter.restore()


class StatusGlyph(QWidget):
    """The state badge in the main window: a tinted disc with a state glyph.

    Recording pulses and processing spins unless "reduce motion" is enabled;
    each state also has a distinct static shape, so color is never the only cue.
    """

    def __init__(self, parent: QWidget | None = None, diameter: int = 48):
        super().__init__(parent)
        self.state = ViewState.IDLE.value
        self.phase = 0.0
        self.reduce_motion = False
        self.setFixedSize(diameter, diameter)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._advance)

    @property
    def recording(self) -> bool:
        return self.state == ViewState.RECORDING.value

    def set_status(self, status: ViewState, reduce_motion: bool) -> None:
        self.state = ViewState(status).value
        self.reduce_motion = reduce_motion
        animated = self.state in {"recording", "processing", "preparing"} and not reduce_motion
        if animated:
            if not self.timer.isActive():
                self.timer.start(33)
        else:
            self.timer.stop()
            self.phase = 0.0
        self.update()

    def set_recording(self, recording: bool, reduce_motion: bool) -> None:
        """Compatibility wrapper for callers that only know about recording."""
        self.set_status(ViewState.RECORDING if recording else ViewState.IDLE, reduce_motion)

    def _advance(self) -> None:
        step = 0.022 if self.state == "recording" else 0.03
        self.phase = (self.phase + step) % 1.0
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt callback name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        accent = QColor(STATE_ACCENTS.get(self.state, TOKENS["text_3"]))
        disc = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setPen(QPen(_tinted(accent, 70), 1))
        painter.setBrush(_tinted(accent, 34))
        painter.drawEllipse(disc)
        paint_state_glyph(painter, disc, self.state, accent, phase=self.phase, animated=not self.reduce_motion)
        painter.end()


# Kept for code that still imports the previous name.
RecordingPulse = StatusGlyph


class StatusHud(QWidget):
    """Floating, non-activating status pill near the screen edge.

    The window is translucent and the capsule is painted here with real
    antialiased corners and a soft shadow, so no square corners leak behind it.
    """

    SHADOW = 16
    HEIGHT = 46
    MIN_WIDTH = 150
    MAX_WIDTH = 420
    EDGE_MARGIN = 20
    AUTO_HIDE_MS = {ViewState.INSERTED: 1800, ViewState.ERROR: 3500}

    def __init__(self, config: dict, lang: str):
        super().__init__(
            None,
            Qt.Tool
            | Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.WindowDoesNotAcceptFocus
            | Qt.WindowTransparentForInput
            | Qt.NoDropShadowWindowHint,
        )
        self.config, self.lang = config, lang
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.NoFocus)
        self.status = ViewState.IDLE
        self.text = ""
        self.elapsed: float | None = None
        self.level = 0.0
        self._shown_level = 0.0
        self.phase = 0.0
        self.label_font = QFont(self.font())
        self.label_font.setPixelSize(14)
        self.label_font.setWeight(QFont.DemiBold)
        self.meta_font = QFont(self.font())
        self.meta_font.setPixelSize(13)
        self.meta_font.setWeight(QFont.Medium)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._advance)
        self.hide_timer = QTimer(self)
        self.hide_timer.setSingleShot(True)
        self.hide_timer.timeout.connect(self.hide)
        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(160)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)
        self.setFixedSize(self.MIN_WIDTH + self.SHADOW * 2, self.HEIGHT + self.SHADOW * 2)

    # -- settings -----------------------------------------------------------
    def _hud(self) -> dict:
        return self.config.get("hud", {}) or {}

    @property
    def reduce_motion(self) -> bool:
        return bool(self._hud().get("reduce_motion", False))

    @property
    def high_contrast(self) -> bool:
        return bool(self._hud().get("high_contrast", False))

    # -- public API -----------------------------------------------------------
    def show_status(self, status: ViewState, message: str | None = None) -> None:
        """Show ``status`` with an optional short ``message`` (one line)."""
        hud = self._hud()
        self.hide_timer.stop()
        if not hud.get("enabled", True) or status == ViewState.IDLE:
            self.timer.stop()
            self.hide()
            return
        if status != ViewState.RECORDING:
            self.elapsed = None
            self.level = self._shown_level = 0.0
        self.status = status
        labels = HUD_LABELS.get(self.lang, HUD_LABELS["en"])
        default = labels.get(status.value) or HUD_ERROR_KEYS["generic"][self.lang]
        self.text = " ".join((message or default).split())
        animated = status in {ViewState.RECORDING, ViewState.PROCESSING, ViewState.PREPARING}
        if animated and not self.reduce_motion:
            if not self.timer.isActive():
                self.timer.start(33)
        else:
            self.timer.stop()
            self.phase = 0.0
        self._relayout()
        delay = self.AUTO_HIDE_MS.get(status)
        if delay:
            self.hide_timer.start(delay)
        if not self.isVisible():
            if self.reduce_motion:
                self.setWindowOpacity(1.0)
            else:
                self.setWindowOpacity(0.0)
                self._fade.stop()
                self._fade.setStartValue(0.0)
                self._fade.setEndValue(1.0)
                self._fade.start()
            self.show()
        self.update()

    def update_recording(self, elapsed: float, level: float) -> None:
        """Feed live recording metrics (seconds, 0..1 level) from the controller."""
        if self.status != ViewState.RECORDING:
            return
        previous = self.elapsed
        self.elapsed = max(0.0, float(elapsed))
        self.level = max(0.0, min(1.0, float(level)))
        if self.reduce_motion or not self.timer.isActive():
            self._shown_level = self.level
        if previous is None:
            self._relayout()
        if self.isVisible():
            self.update()

    # -- geometry -------------------------------------------------------------
    def _trailing_width(self) -> int:
        if self.status != ViewState.RECORDING or self.elapsed is None:
            return 0
        metrics = QFontMetrics(self.meta_font)
        return metrics.horizontalAdvance("00:00") + 12 + self._bars_width()

    @staticmethod
    def _bars_width() -> int:
        return 4 * 3 + 3 * 3

    def _pill_width(self) -> int:
        metrics = QFontMetrics(self.label_font)
        trailing = self._trailing_width()
        content = 8 + 30 + 11 + metrics.horizontalAdvance(self.text) + (14 + trailing if trailing else 0) + 18
        return max(self.MIN_WIDTH, min(self.MAX_WIDTH, content))

    def _relayout(self) -> None:
        width = self._pill_width() + self.SHADOW * 2
        height = self.HEIGHT + self.SHADOW * 2
        if self.size() != QSize(width, height):
            self.setFixedSize(width, height)
        self._place()

    def _place(self) -> None:
        screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        position = self._hud().get("position", "active_monitor")
        pill_width = self.width() - self.SHADOW * 2
        if position == "bottom_left":
            pill_x = area.left() + self.EDGE_MARGIN
        elif position == "bottom_right":
            pill_x = area.right() - pill_width - self.EDGE_MARGIN
        else:  # active_monitor: centered on the screen the person is working on
            pill_x = area.left() + (area.width() - pill_width) // 2
        pill_y = area.bottom() - self.HEIGHT - self.EDGE_MARGIN - 8
        self.move(pill_x - self.SHADOW, pill_y - self.SHADOW)

    # -- animation ------------------------------------------------------------
    def _advance(self) -> None:
        step = 0.022 if self.status == ViewState.RECORDING else 0.03
        self.phase = (self.phase + step) % 1.0
        # Ease the level meter toward the latest sample instead of jumping.
        self._shown_level += (self.level - self._shown_level) * 0.35
        self.update()

    # -- painting -------------------------------------------------------------
    def _accent(self) -> QColor:
        if self.high_contrast:
            bright = {
                ViewState.RECORDING: "#ff8a7a",
                ViewState.PROCESSING: "#aab0ff",
                ViewState.PREPARING: "#aab0ff",
                ViewState.INSERTED: "#5cf0b4",
                ViewState.ERROR: "#ffd166",
            }
            return QColor(bright.get(self.status, "#ffffff"))
        return QColor(STATE_ACCENTS.get(self.status.value, TOKENS["text_3"]))

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt callback name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.TextAntialiasing)
        shadow = self.SHADOW
        pill = QRectF(shadow, shadow, self.width() - shadow * 2, self.HEIGHT)
        radius = pill.height() / 2
        accent = self._accent()
        contrast = self.high_contrast

        # Soft, offset shadow built from stacked translucent capsules.
        painter.setPen(Qt.NoPen)
        layers = 10
        for index in range(layers, 0, -1):
            grow = index * 1.25
            alpha = int(30 * (1 - index / (layers + 1)) ** 1.6) + 2
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawRoundedRect(pill.adjusted(-grow, -grow + 3, grow, grow + 3), radius + grow, radius + grow)

        # Glassy surface: a faint vertical gradient with a hairline border.
        if contrast:
            painter.setBrush(QColor("#000000"))
            painter.setPen(QPen(QColor("#ffffff"), 2))
            painter.drawRoundedRect(pill.adjusted(1, 1, -1, -1), radius - 1, radius - 1)
        else:
            gradient = QLinearGradient(pill.topLeft(), pill.bottomLeft())
            gradient.setColorAt(0.0, QColor(40, 42, 49, 246))
            gradient.setColorAt(1.0, QColor(24, 25, 30, 246))
            painter.setBrush(gradient)
            painter.setPen(QPen(_tinted(accent, 95), 1))
            painter.drawRoundedRect(pill.adjusted(0.5, 0.5, -0.5, -0.5), radius - 0.5, radius - 0.5)
            painter.setPen(QPen(QColor(255, 255, 255, 18), 1))
            painter.drawLine(
                QPointF(pill.left() + radius, pill.top() + 1.5), QPointF(pill.right() - radius, pill.top() + 1.5)
            )

        # Leading glyph disc.
        disc = QRectF(pill.left() + 8, pill.center().y() - 15, 30, 30)
        painter.setPen(Qt.NoPen)
        painter.setBrush(accent if contrast else _tinted(accent, 46))
        painter.drawEllipse(disc)
        glyph_color = QColor("#000000") if contrast else accent
        paint_state_glyph(
            painter, disc, self.status.value, glyph_color, phase=self.phase, animated=not self.reduce_motion
        )

        # Label (elided so it can never overflow the capsule).
        trailing = self._trailing_width()
        text_left = disc.right() + 11
        text_right = pill.right() - 18 - (trailing + 14 if trailing else 0)
        painter.setFont(self.label_font)
        painter.setPen(QColor("#ffffff") if contrast else QColor(TOKENS["text"]))
        metrics = QFontMetrics(self.label_font)
        text = metrics.elidedText(self.text, Qt.ElideRight, int(max(0.0, text_right - text_left)))
        painter.drawText(QRectF(text_left, pill.top(), text_right - text_left, pill.height()), Qt.AlignVCenter, text)

        # Recording: elapsed time and a live level meter.
        if trailing:
            minutes, seconds = divmod(int(self.elapsed or 0), 60)
            painter.setFont(self.meta_font)
            painter.setPen(QColor("#ffffff") if contrast else QColor(TOKENS["text_2"]))
            meta = QFontMetrics(self.meta_font)
            time_width = meta.horizontalAdvance("00:00")
            bars_left = pill.right() - 18 - self._bars_width()
            time_rect = QRectF(bars_left - 12 - time_width, pill.top(), time_width, pill.height())
            painter.drawText(time_rect, Qt.AlignVCenter | Qt.AlignRight, f"{minutes}:{seconds:02d}")
            painter.setPen(Qt.NoPen)
            painter.setBrush(accent)
            level = self._shown_level
            weights = (0.55, 1.0, 0.75, 0.9)
            for index, weight in enumerate(weights):
                wobble = 0.0
                if not self.reduce_motion and level > 0.02:
                    wobble = 0.18 * math.sin((self.phase * 2 + index * 0.27) * math.tau)
                height = 4 + 14 * max(0.0, min(1.0, level * weight * 1.6 + wobble * level))
                bar = QRectF(bars_left + index * 6, pill.center().y() - height / 2, 3, height)
                painter.drawRoundedRect(bar, 1.5, 1.5)
        painter.end()


class ProfileCard(QPushButton):
    """Accessible profile choice with a clear title, trade-offs and a radio mark."""

    def __init__(self, kind: str, title: str, subtitle: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.kind = kind
        self.setCheckable(True)
        self.setMinimumSize(240, 150)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(title)
        self.setAccessibleDescription(subtitle.replace("\n", " "))
        content = QVBoxLayout(self)
        content.setContentsMargins(22, 20, 44, 20)
        content.setSpacing(10)
        title_label = QLabel(title)
        title_label.setStyleSheet(f"color: {TOKENS['text']}; font-size: 18px; font-weight: 700;")
        title_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        content.addWidget(title_label)
        for line in subtitle.split("\n"):
            point = QLabel(line)
            point.setWordWrap(True)
            point.setStyleSheet(f"color: {TOKENS['text_2']}; font-size: 13px;")
            point.setAttribute(Qt.WA_TransparentForMouseEvents)
            content.addWidget(point)
        content.addStretch()
        self.setStyleSheet(
            f"QPushButton {{ background: {TOKENS['surface']}; border: 1px solid {TOKENS['line']}; "
            "border-radius: 16px; padding: 0; text-align: left; }"
            f"QPushButton:hover {{ background: {TOKENS['surface_2']}; border-color: #4a4f59; }}"
            f"QPushButton:checked {{ background: #2a201e; border: 2px solid {TOKENS['accent']}; }}"
            f"QPushButton:focus {{ border: 2px solid {TOKENS['accent_hover']}; }}"
        )

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt callback name
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        center = QPointF(self.width() - 26, 30)
        accent = QColor(TOKENS["accent"])
        if self.isChecked():
            painter.setPen(Qt.NoPen)
            painter.setBrush(accent)
            painter.drawEllipse(center, 10, 10)
            painter.setPen(QPen(QColor(TOKENS["on_accent"]), 2.2, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            path = QPainterPath()
            path.moveTo(center.x() - 4.5, center.y())
            path.lineTo(center.x() - 1.2, center.y() + 3.3)
            path.lineTo(center.x() + 4.8, center.y() - 3.6)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
        else:
            painter.setPen(QPen(QColor(TOKENS["control_line"]), 1.6))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(center, 9, 9)
        painter.end()


class WhisperStyle(QProxyStyle):
    """Fusion-based style that draws the few primitives stylesheets cannot.

    Stylesheets cannot draw a check mark without an image file, and the
    default combo arrow is tiny on dark backgrounds, so both are painted here.
    """

    def __init__(self):
        super().__init__(QStyleFactory.create("Fusion"))

    def pixelMetric(self, metric, option=None, widget=None):  # noqa: N802 - Qt override
        if metric in (QStyle.PM_IndicatorWidth, QStyle.PM_IndicatorHeight):
            return 18
        return super().pixelMetric(metric, option, widget)

    def drawPrimitive(self, element, option, painter, widget=None):  # noqa: N802 - Qt override
        if element == QStyle.PE_IndicatorCheckBox:
            self._draw_checkbox(option, painter)
            return
        if element == QStyle.PE_IndicatorArrowDown:
            self._draw_chevron(option, painter)
            return
        super().drawPrimitive(element, option, painter, widget)

    @staticmethod
    def _draw_checkbox(option, painter) -> None:
        enabled = bool(option.state & QStyle.State_Enabled)
        checked = bool(option.state & QStyle.State_On)
        hovered = bool(option.state & QStyle.State_MouseOver)
        focused = bool(option.state & QStyle.State_HasFocus)
        side = min(option.rect.width(), option.rect.height(), 18)
        box = QRectF(0, 0, side - 1, side - 1)
        box.moveCenter(QRectF(option.rect).center())
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        accent = QColor(TOKENS["accent"] if enabled else "#5a4a46")
        if checked:
            painter.setPen(QPen(accent, 1))
            painter.setBrush(accent)
        else:
            border = QColor(TOKENS["accent"] if (hovered or focused) and enabled else TOKENS["control_line"])
            if not enabled:
                border = QColor("#43464d")
            painter.setPen(QPen(border, 1.4))
            painter.setBrush(QColor(TOKENS["surface_2"]))
        painter.drawRoundedRect(box, 5, 5)
        if checked:
            pen = QPen(QColor(TOKENS["on_accent"] if enabled else "#2a2422"), 2.1, Qt.SolidLine, Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            path = QPainterPath()
            c = box.center()
            path.moveTo(c.x() - 4.2, c.y() + 0.2)
            path.lineTo(c.x() - 1.2, c.y() + 3.1)
            path.lineTo(c.x() + 4.4, c.y() - 3.2)
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)
        if focused and enabled:
            painter.setPen(QPen(_tinted(TOKENS["accent"], 110), 1.2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(box.adjusted(-2.5, -2.5, 2.5, 2.5), 7, 7)
        painter.restore()

    @staticmethod
    def _draw_chevron(option, painter) -> None:
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        enabled = bool(option.state & QStyle.State_Enabled)
        painter.setPen(QPen(QColor(TOKENS["text_2"] if enabled else "#5c6068"), 1.6, Qt.SolidLine, Qt.RoundCap))
        c = QRectF(option.rect).center()
        path = QPainterPath()
        path.moveTo(c.x() - 4, c.y() - 2)
        path.lineTo(c.x(), c.y() + 2)
        path.lineTo(c.x() + 4, c.y() - 2)
        painter.drawPath(path)
        painter.restore()


def install_app_style(qt_app: QApplication | None) -> None:
    """Apply the WhisperTray look once per QApplication (idempotent)."""
    if qt_app is None or isinstance(qt_app.style(), WhisperStyle):
        return
    qt_app.setStyle(WhisperStyle())
    palette = QPalette()
    roles = {
        QPalette.Window: TOKENS["bg"],
        QPalette.WindowText: TOKENS["text"],
        QPalette.Base: TOKENS["surface_2"],
        QPalette.AlternateBase: TOKENS["surface"],
        QPalette.Text: TOKENS["text"],
        QPalette.PlaceholderText: TOKENS["text_3"],
        QPalette.Button: TOKENS["surface_3"],
        QPalette.ButtonText: TOKENS["text"],
        QPalette.Highlight: TOKENS["accent"],
        QPalette.HighlightedText: TOKENS["on_accent"],
        QPalette.ToolTipBase: TOKENS["surface_3"],
        QPalette.ToolTipText: TOKENS["text"],
        QPalette.Link: TOKENS["accent"],
        QPalette.LinkVisited: TOKENS["accent_hover"],
    }
    for role, color in roles.items():
        palette.setColor(role, QColor(color))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor("#767a82"))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#767a82"))
    palette.setColor(QPalette.Disabled, QPalette.WindowText, QColor("#767a82"))
    qt_app.setPalette(palette)


def hotkey_display_name(value: str) -> str:
    """Format the serialized shortcut for people without changing its value."""
    labels = {
        "ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win", "cmd": "Cmd",
        "space": "Space", "enter": "Enter", "esc": "Esc", "tab": "Tab",
        "backspace": "Backspace", "delete": "Delete", "insert": "Insert",
        "home": "Home", "end": "End", "page_up": "Page Up", "page_down": "Page Down",
        "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    }
    return " + ".join(
        labels.get(part, part.upper() if part.startswith("f") and part[1:].isdigit() else part.upper())
        for part in value.split("+")
    )


def hotkey_from_key_event(event: QKeyEvent) -> str | None:
    """Translate a Qt key press into WhisperTray's portable config format."""
    key = event.key()
    if key in {Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift, Qt.Key_Meta}:
        return None

    parts: list[str] = []
    modifiers = event.modifiers()
    if modifiers & Qt.ControlModifier:
        parts.append("ctrl")
    if modifiers & Qt.AltModifier:
        parts.append("alt")
    if modifiers & Qt.ShiftModifier:
        parts.append("shift")
    if modifiers & Qt.MetaModifier:
        parts.append("cmd" if sys.platform == "darwin" else "win")

    named_keys = {
        Qt.Key_Space: "space", Qt.Key_Return: "enter", Qt.Key_Enter: "enter", Qt.Key_Tab: "tab",
        Qt.Key_Backspace: "backspace", Qt.Key_Delete: "delete", Qt.Key_Insert: "insert",
        Qt.Key_Home: "home", Qt.Key_End: "end", Qt.Key_PageUp: "page_up", Qt.Key_PageDown: "page_down",
        Qt.Key_Up: "up", Qt.Key_Down: "down", Qt.Key_Left: "left", Qt.Key_Right: "right",
    }
    final = named_keys.get(key)
    if final is None and Qt.Key_F1 <= key <= Qt.Key_F24:
        final = f"f{key - Qt.Key_F1 + 1}"
    if final is None and Qt.Key_A <= key <= Qt.Key_Z:
        final = chr(ord("a") + key - Qt.Key_A)
    if final is None and Qt.Key_0 <= key <= Qt.Key_9:
        final = chr(ord("0") + key - Qt.Key_0)
    if final is None:
        text = event.text().lower()
        if len(text) == 1 and text not in {"+", "\x00"} and text.isprintable():
            final = text
    if final is None:
        return None
    parts.append(final)
    return "+".join(parts)


class HotkeyCaptureDialog(QDialog):
    """Modal keyboard grabber used instead of asking people to type syntax."""

    def __init__(self, parent: QWidget, strings: dict[str, str]):
        super().__init__(parent)
        self.t = strings
        self.hotkey: str | None = None
        self.setWindowTitle(self.t["hotkey_capture_title"])
        self.setModal(True)
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 26)
        layout.setSpacing(14)
        prompt = QLabel(self.t["hotkey_capture_prompt"])
        prompt.setObjectName("statusLabel")
        prompt.setAlignment(Qt.AlignCenter)
        prompt.setWordWrap(True)
        layout.addWidget(prompt)
        keycap = QLabel("…")
        keycap.setObjectName("keycap")
        keycap.setAlignment(Qt.AlignCenter)
        keycap.setAccessibleName(self.t["hotkey_capture_prompt"])
        layout.addWidget(keycap)
        hint = QLabel(self.t["hotkey_capture_hint"])
        hint.setObjectName("hintLabel")
        hint.setAlignment(Qt.AlignCenter)
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.keycap = keycap
        QTimer.singleShot(0, self._begin_capture)

    def _begin_capture(self) -> None:
        self.activateWindow()
        self.setFocus(Qt.OtherFocusReason)
        self.grabKeyboard()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.isAutoRepeat():
            event.accept()
            return
        if event.key() == Qt.Key_Escape:
            self.reject()
            return
        value = hotkey_from_key_event(event)
        if value is None:
            self._preview_modifiers(event.modifiers())
            event.accept()
            return
        try:
            from platform_integration import normalize_hotkey, parse_hotkey

            value = normalize_hotkey(value)
            parse_hotkey(value)
        except Exception:
            event.accept()
            return
        self.hotkey = value
        self.accept()

    def _preview_modifiers(self, modifiers) -> None:
        """Echo held modifiers so people see the capture is listening."""
        names = []
        if modifiers & Qt.ControlModifier:
            names.append("ctrl")
        if modifiers & Qt.AltModifier:
            names.append("alt")
        if modifiers & Qt.ShiftModifier:
            names.append("shift")
        if modifiers & Qt.MetaModifier:
            names.append("cmd" if sys.platform == "darwin" else "win")
        self.keycap.setText(f"{hotkey_display_name('+'.join(names))} + …" if names else "…")

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt callback name
        self._preview_modifiers(event.modifiers())
        event.accept()

    def done(self, result: int) -> None:
        try:
            self.releaseKeyboard()
        except RuntimeError:
            pass
        super().done(result)


class MainWindowHotkeyFilter(QObject):
    """Keep a global shortcut from also activating a focused main-window control."""

    def __init__(self, app: "WhisperTrayUi"):
        super().__init__(app.window)
        self.app = app

    def eventFilter(self, watched, event) -> bool:
        if event.type() not in {QEvent.KeyPress, QEvent.KeyRelease} or not isinstance(watched, QWidget):
            return False
        if watched.window() is not self.app.window:
            return False
        pressed = hotkey_from_key_event(event)
        if not pressed:
            return False
        try:
            from platform_integration import normalize_hotkey

            configured = normalize_hotkey(str(self.app.state.config.get("hotkey", "win+alt")))
        except Exception:
            configured = str(self.app.state.config.get("hotkey", "win+alt")).lower()
        if pressed != configured:
            return False
        event.accept()
        return True


class WrappedStatusLabel(QLabel):
    """A wrapping label that reserves its platform-specific height-for-width."""

    def __init__(self):
        super().__init__()
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

    def reserve_height(self) -> None:
        width = self.contentsRect().width()
        required = self.heightForWidth(width) if width > 0 else -1
        if required > 0 and self.minimumHeight() != required:
            self.setMinimumHeight(required)
            self.updateGeometry()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.reserve_height()


def chevron_icon(expanded: bool, color: str = TOKENS["text_2"]) -> QIcon:
    """A small drawn chevron for disclosure buttons (no glyph fonts needed)."""
    ratio = 2
    pixmap = QPixmap(14 * ratio, 14 * ratio)
    pixmap.setDevicePixelRatio(ratio)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor(color), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    path = QPainterPath()
    if expanded:
        path.moveTo(3, 5)
        path.lineTo(7, 9)
        path.lineTo(11, 5)
    else:
        path.moveTo(5, 3)
        path.lineTo(9, 7)
        path.lineTo(5, 11)
    painter.drawPath(path)
    painter.end()
    return QIcon(pixmap)


MODEL_CHOICES = (
    ("tiny", "75 MB"),
    ("base", "142 MB"),
    ("small", "466 MB"),
    ("medium", "1.5 GB"),
    ("large", "2.9 GB"),
)

HUD_POSITIONS = ("active_monitor", "bottom_right", "bottom_left")


class SettingsDialog(QDialog):
    def __init__(self, app: "WhisperTrayUi", onboarding: bool = False):
        super().__init__(app.window)
        self.app, self.onboarding = app, onboarding
        self._dialog_generation = 0
        self._dialog_closed = False
        self.config = deepcopy(app.state.config)
        self.lang = ui_language(self.config)
        self.t = STRINGS[self.lang]
        self._mic_test_label = self.t["check"]
        self.setWindowTitle(self.t["onboarding"] if onboarding else self.t["settings"])
        layout = QVBoxLayout(self)
        if onboarding:
            self._build_onboarding(layout)
            return
        self.setMinimumSize(560, 420)
        self._build_settings(layout)
        self._fit_to_screen(640, 640)

    # -- shared building blocks ------------------------------------------------
    def _build_settings(self, layout: QVBoxLayout) -> None:
        layout.setContentsMargins(20, 14, 20, 16)
        layout.setSpacing(12)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tab_scrolls: list[QScrollArea] = []
        layout.addWidget(self.tabs, 1)
        self._build_general_tab()
        self._build_appearance_tab()
        self._build_data_tab()
        # The tallest page decides whether a small screen needs scrolling.
        self.settings_scroll = self.tab_scrolls[0]
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        save_button = buttons.button(QDialogButtonBox.Save)
        save_button.setText(self.t["save"])
        save_button.setObjectName("dialogPrimary")
        save_button.setDefault(True)
        cancel_button = buttons.button(QDialogButtonBox.Cancel)
        cancel_button.setText(self.t["cancel"])
        cancel_button.setObjectName("secondaryAction")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._align_form_labels()
        self.update_profile_controls()

    def _fit_to_screen(self, preferred_width: int, preferred_height: int) -> None:
        available = available_dialog_size(self)
        max_width = max(320, available.width() - 24)
        max_height = max(320, available.height() - 24)
        self.setMinimumSize(min(self.minimumWidth(), max_width), min(self.minimumHeight(), max_height))
        width = max(self.minimumWidth(), min(preferred_width, max(320, available.width() - 48)))
        height = max(self.minimumHeight(), min(preferred_height, max(320, available.height() - 48)))
        self.setMaximumSize(max_width, max_height)
        self.resize(width, height)

    def _scroll_page(self, title: str) -> QVBoxLayout:
        """Create a scrollable settings tab and return its content layout."""
        page = QWidget()
        page.setObjectName("settingsPage")
        content = QVBoxLayout(page)
        content.setContentsMargins(4, 10, 14, 10)
        content.setSpacing(10)
        scroll = QScrollArea()
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(page)
        self.tab_scrolls.append(scroll)
        # "&" would otherwise turn into a keyboard mnemonic ("Data & system").
        self.tabs.addTab(scroll, title.replace("&", "&&"))
        return content

    def _section_title(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    @staticmethod
    def _hint(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("hintLabel")
        label.setWordWrap(True)
        return label

    @staticmethod
    def _form() -> QFormLayout:
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.DontWrapRows)
        return form

    def _align_form_labels(self) -> None:
        """Center each form label on its field instead of pinning it to the top."""
        for form in self.findChildren(QFormLayout):
            for row in range(form.rowCount()):
                label_item = form.itemAt(row, QFormLayout.LabelRole)
                field_item = form.itemAt(row, QFormLayout.FieldRole)
                label = label_item.widget() if label_item else None
                field = field_item.widget() if field_item else None
                if isinstance(label, QLabel) and field is not None and label.text():
                    label.setObjectName("formLabel")
                    label.setMinimumHeight(field.sizeHint().height())
                    label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

    @staticmethod
    def _field_with_button(field: QWidget, button: QPushButton) -> QWidget:
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(field, 1)
        row.addWidget(button)
        return container

    @staticmethod
    def _button_row(*buttons: QPushButton) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 2, 0, 0)
        row.setSpacing(8)
        for button in buttons:
            row.addWidget(button)
        row.addStretch(1)
        return row

    def _option(self, checkbox: QCheckBox, hint: str) -> QWidget:
        container = QWidget()
        row = QVBoxLayout(container)
        row.setContentsMargins(0, 2, 0, 4)
        row.setSpacing(2)
        row.addWidget(checkbox)
        hint_label = self._hint(hint)
        # Align the hint with the checkbox text, not the box.
        hint_label.setContentsMargins(28, 0, 0, 0)
        row.addWidget(hint_label)
        container.hint_label = hint_label
        return container

    def _profile_combo(self) -> QComboBox:
        combo = QComboBox()
        combo.addItem(self.t["privacy"], "privacy")
        combo.addItem(self.t["speed"], "speed")
        combo.setCurrentIndex(0 if self.config.get("profile") == "privacy" else 1)
        return combo

    def _model_combo(self) -> QComboBox:
        combo = QComboBox()
        for name, size in MODEL_CHOICES:
            combo.addItem(f"{name.capitalize()} · ~{size}", name)
        combo.setCurrentIndex(max(0, combo.findData(self.config.get("model", "small"))))
        combo.setAccessibleName(self.t["model"])
        return combo

    def _mic_combo(self) -> QComboBox:
        combo = QComboBox()
        combo.addItem(self.t["default_mic"], None)
        for name, index in input_devices():
            combo.addItem(name, index)
        wanted = self.config.get("device_index")
        combo.setCurrentIndex(next((i for i in range(combo.count()) if combo.itemData(i) == wanted), 0))
        combo.setAccessibleName(self.t["microphone"])
        return combo

    def _hotkey_mode_combo(self) -> QComboBox:
        combo = QComboBox()
        combo.addItem(self.t["toggle"], "toggle")
        combo.addItem(self.t["hold"], "hold")
        combo.setCurrentIndex(1 if self.config.get("hotkey_mode") == "hold" else 0)
        combo.setAccessibleName(self.t["hotkey_mode"])
        return combo

    def _recognition_language_combo(self) -> QComboBox:
        combo = QComboBox()
        combo.addItems([self.t["recognition_auto"], "Русский (ru)", "English (en)"])
        combo.setCurrentIndex({None: 0, "ru": 1, "en": 2}.get(self.config.get("language"), 0))
        combo.setAccessibleName(self.t["language"])
        return combo

    def _groq_key_field(self) -> QWidget:
        self.groq_key = QLineEdit()
        self.groq_key.setEchoMode(QLineEdit.Password)
        self.groq_key.setPlaceholderText(self.t["keychain_saved"] if self._has_groq_key() else "gsk_…")
        self.groq_key.setAccessibleName(self.t["groq_key"])
        self.test_key_button = QPushButton(self.t["check"])
        self.test_key_button.setAccessibleName(self.t["test_key"])
        self.test_key_button.clicked.connect(self.test_groq_key)
        return self._field_with_button(self.groq_key, self.test_key_button)

    def _model_field(self) -> QWidget:
        self.model = self._model_combo()
        self.prepare_model_button = QPushButton(self.t["prepare_model"])
        self.prepare_model_button.clicked.connect(self.prepare_local_model)
        return self._field_with_button(self.model, self.prepare_model_button)

    def _mic_field(self) -> QWidget:
        self.mic = self._mic_combo()
        self.test_mic_button = QPushButton(self._mic_test_label)
        self.test_mic_button.setAccessibleName(self.t["test_mic"])
        self.test_mic_button.clicked.connect(self.test_microphone)
        return self._field_with_button(self.mic, self.test_mic_button)

    def _ai_cleanup_option(self) -> QWidget:
        self.ai_cleanup = QCheckBox(self.t["ai_cleanup"])
        self.ai_cleanup.setChecked(bool(self.config.get("ai_cleanup", True)))
        option = self._option(self.ai_cleanup, self.t["ai_cleanup_hint"])
        self.ai_cleanup_hint = option.hint_label
        return option

    def _update_ai_cleanup(self) -> None:
        speed = self.profile.currentData() == "speed"
        self.ai_cleanup.setEnabled(speed)
        self.ai_cleanup_hint.setText(self.t["ai_cleanup_hint"] if speed else self.t["ai_cleanup_needs_speed"])

    def _add_hotkey_control(self, form: QFormLayout) -> None:
        self.hotkey = QLineEdit(hotkey_display_name(self.config.get("hotkey", "win+alt")))
        self.hotkey.setReadOnly(True)
        self.hotkey.setAccessibleName(self.t["hotkey"])
        self.change_hotkey_button = QPushButton(self.t["change_hotkey"])
        self.change_hotkey_button.clicked.connect(self.capture_hotkey)
        form.addRow(self.t["hotkey"], self._field_with_button(self.hotkey, self.change_hotkey_button))
        self.hotkey_feedback = self._hint("")
        self.hotkey_feedback.hide()
        form.addRow("", self.hotkey_feedback)

    def capture_hotkey(self) -> None:
        listener = getattr(self.app.state, "hotkey_listener", None)
        suspended = False
        if listener and hasattr(listener, "suspend_hotkey"):
            try:
                suspended = listener.suspend_hotkey()
            except Exception:
                logger.exception("Could not suspend the global hotkey for capture")

        capture = HotkeyCaptureDialog(self, self.t)
        try:
            if capture.exec() != QDialog.Accepted or not capture.hotkey:
                return
            value = capture.hotkey
            if self.onboarding:
                self.config["hotkey"] = value
            else:
                updated = deepcopy(self.app.state.config)
                updated["hotkey"] = value
                self.app.save_config(updated)
                self.config["hotkey"] = value
            self.hotkey.setText(hotkey_display_name(value))
            self.hotkey_feedback.setText(
                self.t["hotkey_capture_saved"].format(hotkey=hotkey_display_name(value))
            )
            self.hotkey_feedback.show()
        except Exception:
            logger.exception("Could not apply captured hotkey")
            self.hotkey_feedback.setText(self.t["hotkey_capture_failed"])
            self.hotkey_feedback.show()
        finally:
            # reload_hotkey() may already have installed the new registration;
            # resume_hotkey() is deliberately idempotent and also covers the
            # unchanged-hotkey, cancel, onboarding and failed-save paths.
            if suspended and listener and hasattr(listener, "resume_hotkey"):
                try:
                    listener.resume_hotkey()
                except Exception:
                    logger.exception("Could not resume the global hotkey after capture")

    # -- settings tabs ---------------------------------------------------------
    def _build_general_tab(self) -> None:
        layout = self._scroll_page(self.t["general"])
        layout.addWidget(self._section_title(self.t["recognition"]))
        self.form = self._form()
        layout.addLayout(self.form)
        self.profile = self._profile_combo()
        self.profile.setAccessibleName(self.t["profile"])
        self.form.addRow(self.t["profile"], self.profile)
        self.groq_row = self._groq_key_field()
        self.form.addRow(self.t["groq_key"], self.groq_row)
        self.model_row = self._model_field()
        self.form.addRow(self.t["model"], self.model_row)
        self._add_model_job_controls(self.form)
        self.cloud_note = self._hint(self.t["cloud_note"])
        layout.addWidget(self.cloud_note)
        layout.addSpacing(2)
        layout.addWidget(self._ai_cleanup_option())

        layout.addWidget(self._section_title(self.t["dictation"]))
        audio_form = self._form()
        layout.addLayout(audio_form)
        audio_form.addRow(self.t["microphone"], self._mic_field())
        self.play_mic_button = QPushButton(self.t["mic_play"])
        self.play_mic_button.clicked.connect(self.play_microphone_test)
        self.play_mic_button.hide()
        audio_form.addRow("", self.play_mic_button)
        self._add_hotkey_control(audio_form)
        self.hotkey_mode = self._hotkey_mode_combo()
        audio_form.addRow(self.t["hotkey_mode"], self.hotkey_mode)

        self.advanced_toggle = QPushButton(self.t["advanced"])
        self.advanced_toggle.setObjectName("disclosure")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setIcon(chevron_icon(False))
        self.advanced_toggle.setCursor(Qt.PointingHandCursor)
        layout.addSpacing(4)
        layout.addWidget(self.advanced_toggle, alignment=Qt.AlignLeft)
        self.advanced_panel = QWidget()
        advanced = QVBoxLayout(self.advanced_panel)
        advanced.setContentsMargins(0, 0, 0, 0)
        advanced.setSpacing(8)
        advanced_form = self._form()
        self.rec_lang = self._recognition_language_combo()
        advanced_form.addRow(self.t["language"], self.rec_lang)
        advanced.addLayout(advanced_form)
        self.local_fallback = QCheckBox(self.t["fallback"])
        self.local_fallback.setChecked(self.config.get("allow_local_fallback", False))
        advanced.addWidget(self.local_fallback)
        self.advanced_panel.hide()
        layout.addWidget(self.advanced_panel)
        self.advanced_toggle.toggled.connect(self._toggle_advanced)
        layout.addStretch()
        self.profile.currentIndexChanged.connect(self.update_profile_controls)

    def _toggle_advanced(self, expanded: bool) -> None:
        self.advanced_panel.setVisible(expanded)
        self.advanced_toggle.setIcon(chevron_icon(expanded))

    def _build_appearance_tab(self) -> None:
        layout = self._scroll_page(self.t["appearance"])
        form = self._form()
        layout.addLayout(form)
        self.ui_lang = QComboBox()
        self.ui_lang.addItem("Русский", "ru")
        self.ui_lang.addItem("English", "en")
        self.ui_lang.setCurrentIndex(0 if self.lang == "ru" else 1)
        self.ui_lang.setAccessibleName(self.t["ui_language"])
        form.addRow(self.t["ui_language"], self.ui_lang)

        layout.addWidget(self._section_title(self.t["hud_section"]))
        hud = self.config.get("hud", {})
        self.hud_enabled = QCheckBox(self.t["hud"])
        self.hud_enabled.setChecked(hud.get("enabled", True))
        layout.addWidget(self.hud_enabled)
        hud_form = self._form()
        self.position = QComboBox()
        labels = {
            "active_monitor": self.t["bottom_center"],
            "bottom_right": self.t["bottom_right"],
            "bottom_left": self.t["bottom_left"],
        }
        for value in HUD_POSITIONS:
            self.position.addItem(labels[value], value)
        self.position.setCurrentIndex(max(0, self.position.findData(hud.get("position", "active_monitor"))))
        self.position.setAccessibleName(self.t["position"])
        hud_form.addRow(self.t["position"], self.position)
        layout.addLayout(hud_form)
        self.contrast = QCheckBox(self.t["contrast"])
        self.contrast.setChecked(hud.get("high_contrast", False))
        layout.addWidget(self.contrast)
        self.motion = QCheckBox(self.t["motion"])
        self.motion.setChecked(hud.get("reduce_motion", False))
        layout.addWidget(self.motion)
        self.hud_enabled.toggled.connect(self._update_hud_controls)
        self._update_hud_controls()
        layout.addStretch()

    def _update_hud_controls(self) -> None:
        enabled = self.hud_enabled.isChecked()
        self.position.setEnabled(enabled)
        self.contrast.setEnabled(enabled)

    def _build_data_tab(self) -> None:
        layout = self._scroll_page(self.t["data_system"])
        from autostart import is_enabled

        layout.addWidget(self._section_title(self.t["startup"]))
        self.autostart_initial = is_enabled()
        self.autostart_enabled = QCheckBox(self.t["autostart"])
        self.autostart_enabled.setChecked(self.autostart_initial)
        layout.addWidget(self._option(self.autostart_enabled, self.t["autostart_hint"]))
        self.start_in_tray = QCheckBox(self.t["start_in_tray"])
        self.start_in_tray.setChecked(self.config.get("start_in_tray", False))
        layout.addWidget(self._option(self.start_in_tray, self.t["start_in_tray_hint"]))

        layout.addWidget(self._section_title(self.t["history_section"]))
        history = self.config.get("history", {})
        self.history_enabled = QCheckBox(self.t["history"])
        self.history_enabled.setChecked(history.get("enabled", False))
        layout.addWidget(self.history_enabled)
        history_form = self._form()
        self.retention = QComboBox()
        for days in (7, 30, 90, 365):
            self.retention.addItem(f"{days} {self.t['days']}", days)
        self.retention.setCurrentIndex(max(0, self.retention.findData(history.get("retention_days", 30))))
        self.retention.setAccessibleName(self.t["retention"])
        history_form.addRow(self.t["retention"], self.retention)
        layout.addLayout(history_form)
        view_history = QPushButton(self.t["history_view"])
        view_history.clicked.connect(self.app.open_history)
        clear_history = QPushButton(self.t["clear_history"])
        clear_history.clicked.connect(self.clear_history)
        layout.addLayout(self._button_row(view_history, clear_history))

        layout.addWidget(self._section_title(self.t["diagnostics"]))
        layout.addWidget(self._hint(self.t["diagnostics_info"]))
        from diagnostics import collect_diagnostics

        self.diagnostics_field = QPlainTextEdit(
            json.dumps(collect_diagnostics(self.app.state.config), ensure_ascii=False, indent=2)
        )
        self.diagnostics_field.setReadOnly(True)
        self.diagnostics_field.setMaximumHeight(150)
        self.diagnostics_field.setAccessibleName(self.t["diagnostics"])
        self.diagnostics_field.hide()
        self.diagnostics_toggle = QPushButton(self.t["show_diagnostics"])
        self.diagnostics_toggle.clicked.connect(self.toggle_diagnostics)
        export = QPushButton(self.t["export_diagnostics"])
        export.clicked.connect(self.export_diagnostics)
        layout.addLayout(self._button_row(self.diagnostics_toggle, export))
        layout.addWidget(self.diagnostics_field)
        layout.addStretch()

    def toggle_diagnostics(self) -> None:
        visible = not self.diagnostics_field.isVisible()
        self.diagnostics_field.setVisible(visible)
        self.diagnostics_toggle.setText(self.t["hide_diagnostics"] if visible else self.t["show_diagnostics"])

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
        raw_hotkey = self.hotkey.text().strip()
        if not raw_hotkey:
            QMessageBox.warning(self, APP_NAME, self.t["hotkey_empty"])
            return
        try:
            from platform_integration import normalize_hotkey, parse_hotkey

            hotkey = normalize_hotkey(raw_hotkey)
            parse_hotkey(hotkey)
        except Exception:
            QMessageBox.warning(self, APP_NAME, self.t["hotkey_invalid"])
            return
        if self.profile.currentData() == "speed" and not self.groq_key.text().strip() and not self._has_groq_key():
            QMessageBox.warning(self, APP_NAME, self.t["groq_required"])
            return
        self.config.update(
            {
                "profile": self.profile.currentData(),
                "transcription_backend": "local" if self.profile.currentData() == "privacy" else "groq",
                "allow_local_fallback": self.local_fallback.isChecked(),
                "ai_cleanup": self.ai_cleanup.isChecked(),
                "device_index": self.mic.currentData(),
                "language": [None, "ru", "en"][self.rec_lang.currentIndex()],
                "hotkey": hotkey,
                "hotkey_mode": self.hotkey_mode.currentData(),
                "model": self.model.currentData(),
                "ui_language": self.ui_lang.currentData(),
                "onboarding_complete": True,
                "start_in_tray": getattr(self, "start_in_tray", None).isChecked()
                if hasattr(self, "start_in_tray")
                else self.config.get("start_in_tray", False),
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
        autostart_changed = False
        autostart_desired = getattr(self, "autostart_initial", False)
        if hasattr(self, "autostart_enabled"):
            autostart_desired = self.autostart_enabled.isChecked()
            autostart_changed = autostart_desired != self.autostart_initial
            if autostart_changed and not self._set_autostart(autostart_desired):
                QMessageBox.warning(self, APP_NAME, self.t["autostart_error"])
                return
        try:
            key = self.groq_key.text().strip()
            if key:
                from credentials import CredentialStore

                CredentialStore().set_groq_key(key)
            self.app.save_config(self.config)
        except Exception:
            if autostart_changed:
                self._set_autostart(self.autostart_initial)
            logger.exception("Saving settings failed")
            QMessageBox.critical(self, APP_NAME, self.t["settings_save_failed"])
            return
        if hasattr(self, "autostart_initial"):
            self.autostart_initial = autostart_desired
        self.accept()

    @staticmethod
    def _set_autostart(enabled: bool) -> bool:
        from autostart import disable, enable

        return enable() if enabled else disable()

    def update_profile_controls(self) -> None:
        cloud = self.profile.currentData() == "speed"
        self.form.setRowVisible(self.groq_row, cloud)
        self.form.setRowVisible(self.model_row, not cloud)
        self.cloud_note.setText(self.t["cloud_note"] if cloud else self.t["privacy_hint"])
        self.local_fallback.setVisible(cloud)
        self._update_ai_cleanup()

    # -- onboarding ------------------------------------------------------------
    def _build_onboarding(self, layout: QVBoxLayout) -> None:
        """Focused first-run flow; normal Settings stays comprehensive above."""
        self.setMinimumSize(560, 420)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(8)
        self.ot = ONBOARDING_STRINGS[self.lang]
        self.step_label = QLabel()
        self.step_label.setObjectName("stepLabel")
        layout.addWidget(self.step_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 3)
        self.progress.setTextVisible(False)
        self.progress.setAccessibleName(self.ot["step"].format(current=1))
        layout.addWidget(self.progress)
        layout.addSpacing(6)
        logo = QLabel()
        logo.setAlignment(Qt.AlignCenter)
        pixmap = QPixmap(str(app_logo_path()))
        if not pixmap.isNull():
            logo.setPixmap(pixmap.scaled(52, 52, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        layout.addWidget(logo)
        heading = QLabel(self.ot["heading"])
        heading.setObjectName("pageTitle")
        heading.setAlignment(Qt.AlignCenter)
        layout.addWidget(heading)
        subtitle = QLabel(self.ot["subtitle"])
        subtitle.setObjectName("detailLabel")
        subtitle.setAlignment(Qt.AlignCenter)
        layout.addWidget(subtitle)
        self.onboarding_brand_widgets = (logo, heading, subtitle)
        self.profile = self._profile_combo()
        self.pages = QStackedWidget()
        self.onboarding_scroll = QScrollArea()
        self.onboarding_scroll.setFrameShape(QFrame.NoFrame)
        self.onboarding_scroll.setWidgetResizable(True)
        self.onboarding_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.onboarding_scroll.setWidget(self.pages)
        layout.addWidget(self.onboarding_scroll, 1)
        self._onboarding_profile_page()
        self._onboarding_backend_page()
        self._onboarding_audio_page()
        self.pages.setMinimumHeight(max(self.pages.widget(index).sizeHint().height() for index in range(self.pages.count())))
        self.profile.currentIndexChanged.connect(self._update_ai_cleanup)
        self._update_ai_cleanup()
        self._align_form_labels()
        self.set_onboarding_step(0)
        self._fit_to_screen(720, 660)

    def _page(self, title: str, hint: str | None = None) -> QVBoxLayout:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 12, 4, 4)
        layout.setSpacing(12)
        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        heading.setAlignment(Qt.AlignCenter)
        heading.setWordWrap(True)
        layout.addWidget(heading)
        if hint:
            note = QLabel(hint)
            note.setObjectName("detailLabel")
            note.setAlignment(Qt.AlignCenter)
            note.setWordWrap(True)
            layout.addWidget(note)
        layout.addSpacing(6)
        self.pages.addWidget(page)
        return layout

    @staticmethod
    def _panel() -> tuple[QFrame, QVBoxLayout]:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        return panel, layout

    def _onboarding_profile_page(self) -> None:
        layout = self._page(self.ot["choose_profile"], self.ot["choose_profile_hint"])
        cards = QHBoxLayout()
        cards.setSpacing(14)
        self.privacy_card = ProfileCard("privacy", self.t["privacy"].split(" (")[0], self.ot["privacy_card"])
        self.speed_card = ProfileCard("speed", self.t["speed"].split(" (")[0], self.ot["speed_card"])
        self.privacy_card.clicked.connect(lambda: self._select_onboarding_profile("privacy"))
        self.speed_card.clicked.connect(lambda: self._select_onboarding_profile("speed"))
        cards.addWidget(self.privacy_card)
        cards.addWidget(self.speed_card)
        layout.addLayout(cards)
        layout.addSpacing(8)
        forward = QPushButton(self.ot["continue"])
        forward.setObjectName("primaryAction")
        forward.setMinimumWidth(240)
        forward.clicked.connect(lambda: self.set_onboarding_step(1))
        layout.addWidget(forward, alignment=Qt.AlignHCenter)
        layout.addStretch()
        self._select_onboarding_profile(self.profile.currentData())

    def _onboarding_backend_page(self) -> None:
        layout = self._page(self.ot["profile_setup"])
        self.backend_pages = QStackedWidget()

        local, local_layout = self._panel()
        local_layout.addWidget(self._section_title(self.t["privacy"]))
        local_note = QLabel(self.ot["local_explainer"])
        local_note.setWordWrap(True)
        local_layout.addWidget(local_note)
        local_form = self._form()
        local_form.addRow(self.t["model"], self._model_field())
        self._add_model_job_controls(local_form)
        local_layout.addLayout(local_form)
        local_layout.addWidget(self._hint(self.ot["local_model_hint"]))
        local_layout.addStretch()

        cloud, cloud_layout = self._panel()
        cloud_layout.addWidget(self._section_title(self.t["speed"]))
        cloud_note = QLabel(self.ot["cloud_explainer"])
        cloud_note.setWordWrap(True)
        cloud_layout.addWidget(cloud_note)
        key_hint = QLabel(
            f'{self.ot["cloud_key_hint"]} <a href="https://console.groq.com/keys" '
            f'style="color: {TOKENS["accent"]}; text-decoration: none;">{self.ot["get_groq_key"]}&nbsp;→</a>'
        )
        key_hint.setObjectName("hintLabel")
        key_hint.setWordWrap(True)
        key_hint.setOpenExternalLinks(True)
        key_hint.setTextInteractionFlags(Qt.TextBrowserInteraction)
        cloud_layout.addWidget(key_hint)
        cloud_form = self._form()
        cloud_form.addRow(self.t["groq_key"], self._groq_key_field())
        cloud_layout.addLayout(cloud_form)
        cloud_layout.addWidget(self._ai_cleanup_option())
        self.local_fallback = QCheckBox(self.t["fallback"])
        self.local_fallback.setChecked(self.config.get("allow_local_fallback", False))
        cloud_layout.addWidget(self.local_fallback)
        cloud_layout.addStretch()

        self.backend_pages.addWidget(local)
        self.backend_pages.addWidget(cloud)
        layout.addWidget(self.backend_pages)
        layout.addStretch()
        layout.addLayout(self._onboarding_navigation(lambda: self.set_onboarding_step(0), lambda: self.set_onboarding_step(2)))

    def _onboarding_audio_page(self) -> None:
        layout = self._page(self.ot["audio_ready"], self.ot["audio_hint"])
        panel, panel_layout = self._panel()
        form = self._form()
        form.addRow(self.t["microphone"], self._mic_field())
        self.play_mic_button = QPushButton(self.t["mic_play"])
        self.play_mic_button.clicked.connect(self.play_microphone_test)
        self.play_mic_button.hide()
        form.addRow("", self.play_mic_button)
        self._add_hotkey_control(form)
        self.hotkey_mode = self._hotkey_mode_combo()
        form.addRow(self.t["hotkey_mode"], self.hotkey_mode)
        panel_layout.addLayout(form)
        panel_layout.addWidget(self._hint(self.ot["hotkey_hint"]))
        layout.addWidget(panel)
        # Preserve advanced settings on first run; these controls remain available in Settings.
        self.rec_lang = self._recognition_language_combo()
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
        self.position.addItem("", hud.get("position", "active_monitor"))
        self.history_enabled = QCheckBox()
        self.history_enabled.setChecked(self.config.get("history", {}).get("enabled", False))
        self.retention = QComboBox()
        self.retention.addItem("", self.config.get("history", {}).get("retention_days", 30))
        layout.addStretch()
        layout.addLayout(self._onboarding_navigation(lambda: self.set_onboarding_step(1), self.save, self.ot["finish"]))

    def _onboarding_navigation(self, back, forward, label: str | None = None) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 8, 0, 0)
        back_button = QPushButton(self.ot["back"])
        back_button.setObjectName("secondaryAction")
        back_button.clicked.connect(back)
        forward_button = QPushButton(label or self.ot["continue"])
        forward_button.setObjectName("primaryAction")
        forward_button.setMinimumWidth(180)
        forward_button.clicked.connect(forward)
        row.addWidget(back_button)
        row.addStretch()
        row.addWidget(forward_button)
        return row

    def _select_onboarding_profile(self, profile: str) -> None:
        self.profile.setCurrentIndex(0 if profile == "privacy" else 1)
        self.privacy_card.setChecked(profile == "privacy")
        self.speed_card.setChecked(profile == "speed")

    def _add_model_job_controls(self, form: QFormLayout) -> None:
        self.model_status = self._hint("")
        self.model_status.hide()
        form.addRow("", self.model_status)
        self.model_progress = QProgressBar()
        self.model_progress.setRange(0, 0)
        self.model_progress.setTextVisible(False)
        self.model_progress.hide()
        self.cancel_model_button = QPushButton(self.t["cancel_job"])
        self.cancel_model_button.setObjectName("secondaryAction")
        self.cancel_model_button.clicked.connect(self.app.cancel_job)
        self.cancel_model_button.hide()
        progress_row = QWidget()
        row = QHBoxLayout(progress_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        row.addWidget(self.model_progress, 1)
        row.addWidget(self.cancel_model_button)
        form.addRow("", progress_row)
        self._model_poll_timer = QTimer(self)
        self._model_poll_timer.timeout.connect(self._poll_model_preparation)

    def _poll_model_preparation(self) -> None:
        active = self.app.current_job_kind == "model" and self.app.status in {
            ViewState.PREPARING,
            ViewState.PROCESSING,
        }
        if active:
            self.model_status.setText(self.app.status_label.text())
            self.model_status.show()
            self.model_progress.setRange(self.app.task_progress.minimum(), self.app.task_progress.maximum())
            if self.app.task_progress.maximum() > 0:
                self.model_progress.setValue(self.app.task_progress.value())
            self.model_progress.show()
            self.cancel_model_button.show()
            return
        self._model_poll_timer.stop()
        self.prepare_model_button.setEnabled(True)
        self.prepare_model_button.setText(self.t["prepare_model"])
        self.model_progress.hide()
        self.cancel_model_button.hide()
        if self.app.current_job_kind == "model":
            self.model_status.setText(self.app.status_label.text())
            self.model_status.show()

    def set_onboarding_step(self, step: int) -> None:
        for widget in self.onboarding_brand_widgets:
            widget.setVisible(step == 0)
        self.pages.setCurrentIndex(step)
        self.progress.setValue(step + 1)
        label = self.ot["step"].format(current=step + 1)
        self.step_label.setText(label)
        self.progress.setAccessibleName(label)
        if step == 1:
            self.backend_pages.setCurrentIndex(1 if self.profile.currentData() == "speed" else 0)

    def test_microphone(self) -> None:
        if getattr(self, "_mic_test_cancel", None) is not None:
            self._mic_test_cancel.set()
            try:
                import sounddevice as sd

                sd.stop()
            except Exception:
                logger.debug("Could not stop microphone test", exc_info=True)
            return
        self._mic_test_cancel = threading.Event()
        self._mic_test_result: queue.Queue = queue.Queue()
        generation = self._dialog_generation
        self.test_mic_button.setText(self.t["mic_cancel"])
        self.play_mic_button.hide()
        device = self.mic.currentData()

        def record_test():
            try:
                import sounddevice as sd

                sd.check_input_settings(device=device, channels=1, samplerate=16000, dtype="float32")
                recording = sd.rec(5 * 16000, samplerate=16000, channels=1, dtype="float32", device=device)
                sd.wait()
                if self._mic_test_cancel.is_set():
                    self._mic_test_result.put((False, None, None))
                else:
                    self._mic_test_result.put((True, recording.copy(), self.t["mic_ready"]))
            except Exception as exc:
                self._mic_test_result.put((False, None, self.t["mic_failed"].format(error=exc)))

        threading.Thread(target=record_test, daemon=True, name="MicrophoneTest").start()

        def poll():
            if self._dialog_closed or generation != self._dialog_generation:
                return
            try:
                ok, recording, message = self._mic_test_result.get_nowait()
            except queue.Empty:
                QTimer.singleShot(100, poll)
                return
            self._mic_test_cancel = None
            self.test_mic_button.setText(self._mic_test_label)
            if ok:
                self._mic_test_audio = recording
                self.play_mic_button.show()
                QMessageBox.information(self, APP_NAME, message)
            elif message:
                QMessageBox.warning(self, APP_NAME, message)

        QTimer.singleShot(100, poll)

    def play_microphone_test(self) -> None:
        recording = getattr(self, "_mic_test_audio", None)
        if recording is None:
            return

        def play():
            try:
                import sounddevice as sd

                sd.play(recording, samplerate=16000)
                sd.wait()
            except Exception:
                logger.exception("Could not play microphone test")

        threading.Thread(target=play, daemon=True, name="MicrophoneTestPlayback").start()

    def test_groq_key(self) -> None:
        key = self.groq_key.text().strip()
        if not key:
            try:
                from credentials import CredentialStore

                key = CredentialStore().get_groq_key() or ""
            except Exception:
                key = ""
            if not key:
                QMessageBox.warning(self, APP_NAME, self.t["key_enter"])
                return
        self.test_key_button.setEnabled(False)
        self.test_key_button.setText(self.t["key_testing"])
        self._key_result: queue.Queue = queue.Queue()
        self._key_test_generation = getattr(self, "_key_test_generation", 0) + 1
        generation = self._key_test_generation

        def check():
            try:
                from groq import Groq

                Groq(api_key=key, timeout=60.0, max_retries=0).models.list()
                self._key_result.put((True, self.t["key_valid"]))
            except Exception:
                self._key_result.put((False, self.t["key_failed"]))

        threading.Thread(target=check, daemon=True, name="GroqKeyTest").start()

        def poll():
            if generation != getattr(self, "_key_test_generation", 0) or not self.isVisible():
                return
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
        jobs = getattr(self.app.state, "jobs", None)
        if jobs is not None and hasattr(jobs, "prepare_model"):
            if jobs.prepare_model(self.model.currentData()):
                self.app.current_job_kind = "model"
                self.app.current_job_id = getattr(jobs, "current_job_id", None)
                self.prepare_model_button.setEnabled(False)
                self.prepare_model_button.setText(HUD_LABELS[self.lang]["preparing"])
                self.model_status.setText(self.t["model_submitted"])
                self.model_status.show()
                self.model_progress.setRange(0, 0)
                self.model_progress.show()
                self.cancel_model_button.show()
                self._model_poll_timer.start(100)
                self.app.set_state(ViewState.PREPARING, self.t["model_submitted"])
            else:
                QMessageBox.information(self, APP_NAME, self.t["already_processing"])
            return
        self.prepare_model_button.setEnabled(False)
        self.prepare_model_button.setText(HUD_LABELS[self.lang]["preparing"])
        result: queue.Queue = queue.Queue()
        generation = self._dialog_generation
        model_name = self.model.currentData()

        def prepare():
            try:
                from transcriber import Transcriber

                config = deepcopy(self.config)
                config["profile"] = "privacy"
                config["transcription_backend"] = "local"
                Transcriber(model_name, config)._ensure_model()
                result.put((True, self.t["model_ready"].format(model=model_name)))
            except Exception:
                result.put((False, self.t["model_failed"]))

        threading.Thread(target=prepare, daemon=True, name="LocalModelPrepare").start()

        def poll():
            if self._dialog_closed or generation != self._dialog_generation:
                return
            try:
                ok, message = result.get_nowait()
            except queue.Empty:
                QTimer.singleShot(200, poll)
                return
            self.prepare_model_button.setEnabled(True)
            self.prepare_model_button.setText(self.t["prepare_model"])
            (QMessageBox.information if ok else QMessageBox.warning)(self, APP_NAME, message)

        QTimer.singleShot(200, poll)

    def _stop_audio_preview(self) -> None:
        self._dialog_closed = True
        self._dialog_generation += 1
        self._key_test_generation = getattr(self, "_key_test_generation", 0) + 1
        cancel = getattr(self, "_mic_test_cancel", None)
        if cancel is not None:
            cancel.set()
            try:
                import sounddevice as sd

                sd.stop()
            except Exception:
                logger.debug("Could not stop audio preview while closing settings", exc_info=True)
        model_timer = getattr(self, "_model_poll_timer", None)
        if model_timer is not None:
            model_timer.stop()

    def done(self, result: int) -> None:
        self._stop_audio_preview()
        super().done(result)

    def closeEvent(self, event) -> None:
        self._stop_audio_preview()
        super().closeEvent(event)

    def clear_history(self) -> None:
        from history_store import HistoryStore

        result: queue.Queue = queue.Queue()
        generation = self._dialog_generation

        def clear():
            try:
                HistoryStore().clear()
                result.put(None)
            except Exception as exc:
                result.put(exc)

        self.app._start_history_task(clear, "HistoryClear")

        def poll():
            if self._dialog_closed or generation != self._dialog_generation:
                return
            try:
                error = result.get_nowait()
            except queue.Empty:
                QTimer.singleShot(50, poll)
                return
            if error is None:
                QMessageBox.information(self, APP_NAME, self.t["history_cleared"])
            else:
                QMessageBox.warning(self, APP_NAME, self.t["generic_error"])

        QTimer.singleShot(50, poll)

    def export_diagnostics(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, APP_NAME, "whispertray-diagnostics.json", "JSON (*.json)")
        if not path:
            return
        try:
            from diagnostics import export_diagnostics

            export_diagnostics(path, self.app.state.config)
            QMessageBox.information(self, APP_NAME, self.t["diagnostics_exported"])
        except Exception:
            logger.exception("Diagnostics export failed")
            QMessageBox.critical(self, APP_NAME, self.t["diagnostics_failed"])


class HistoryDialog(QDialog):
    """Read-only, searchable view over the optional local transcript history."""

    def __init__(self, parent: QWidget, strings: dict[str, str], task_runner=None):
        super().__init__(parent)
        self.t = strings
        self._task_runner = task_runner
        self.setWindowTitle(self.t["history_section"])
        self.setMinimumSize(620, 440)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)
        self.search = QLineEdit()
        self.search.setPlaceholderText(self.t["history_search"])
        self.search.setAccessibleName(self.t["history_search"])
        self.search.setClearButtonEnabled(True)
        layout.addWidget(self.search)
        self.results = QPlainTextEdit()
        self.results.setObjectName("resultText")
        self.results.setAccessibleName(self.t["history_section"])
        self.results.setReadOnly(True)
        self.results.setPlaceholderText(self.t["history_empty"])
        layout.addWidget(self.results, 1)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.button(QDialogButtonBox.Close).setText(self.t["close"])
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self._result_queue: queue.Queue = queue.Queue()
        self._request_id = 0
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.timeout.connect(self.refresh)
        self._poll_timer = QTimer(self)
        self._poll_timer.timeout.connect(self._poll_results)
        self._poll_timer.start(50)
        self.search.textChanged.connect(lambda: self._search_timer.start(180))
        self.refresh()

    def refresh(self) -> None:
        from history_store import HistoryStore

        self._request_id += 1
        request_id = self._request_id
        query = self.search.text()

        def load():
            try:
                self._result_queue.put((request_id, HistoryStore().entries(query), None))
            except Exception as exc:
                self._result_queue.put((request_id, [], exc))

        if self._task_runner is not None:
            self._task_runner(load, "HistoryLoad")
        else:
            threading.Thread(target=load, daemon=True, name="HistoryLoad").start()

    def _poll_results(self) -> None:
        try:
            while True:
                request_id, entries, error = self._result_queue.get_nowait()
                if request_id != self._request_id:
                    continue
                if error is not None:
                    self.results.setPlainText(self.t["generic_error"])
                    continue
                blocks = []
                for entry in entries:
                    stamp = entry["created_at"].replace("T", " ").replace("+00:00", " UTC")
                    blocks.append(f"{stamp}\n{entry['text']}")
                self.results.setPlainText("\n\n".join(blocks))
        except queue.Empty:
            pass

    def done(self, result: int) -> None:
        self._poll_timer.stop()
        super().done(result)


ERROR_TITLES = {"error", "ошибка", "hotkey error", "hotkey unavailable", "microphone unavailable"}
# Without a working hotkey a tray-only app looks dead, so these still reach the tray.
HOTKEY_TITLES = {"hotkey error", "hotkey unavailable"}
BUSY_MESSAGES = {
    "already processing the previous task",
    "already processing the previous dictation",
    "a file transcription is already running",
}
FILE_SAVED_TITLES = {"транскрибировано успешно", "transcribed successfully"}


def _repolish(widget: QWidget) -> None:
    """Re-apply stylesheet rules that depend on a dynamic property."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


class WhisperTrayUi:
    """Qt owner plus compatibility ``tray_app`` for existing workers."""

    def __init__(self, state):
        self.state = state
        self.lang = ui_language(state.config)
        self.t = STRINGS[self.lang]
        self.status = ViewState.IDLE
        self.current_job_id = None
        self.current_job_kind: str | None = None
        self.last_output_path: Path | None = None
        self._retry_available = False
        self._last_error_code: str | None = None
        self._hud_message: str | None = None
        self._recovery_notice_shown = False
        self._close_hint_shown = False
        self._history_threads: set[threading.Thread] = set()
        self._history_threads_lock = threading.Lock()
        qt_app = QApplication.instance()
        install_app_style(qt_app)
        self.window = QMainWindow()
        self.window.setWindowTitle(APP_NAME)
        self.style_sheet = window_style()
        self.window.setStyleSheet(self.style_sheet)
        self.window.setMinimumWidth(440)
        self.window.closeEvent = self.close_to_tray
        self._hotkey_collision_filter = MainWindowHotkeyFilter(self)
        if qt_app is not None:
            qt_app.installEventFilter(self._hotkey_collision_filter)
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
        self._history_timer = QTimer()
        self._history_timer.timeout.connect(self.prune_history_async)
        self._history_timer.start(15 * 60 * 1000)
        self.prune_history_async()

    def build_window(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(12)
        logo = QLabel()
        brand = QPixmap(str(app_icon_path()))
        if not brand.isNull():
            logo.setPixmap(brand.scaled(38, 38, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            header.addWidget(logo)
        brand_copy = QVBoxLayout()
        brand_copy.setSpacing(0)
        app_title = QLabel(APP_NAME)
        app_title.setObjectName("appTitle")
        app_caption = QLabel(self.t["tagline"])
        app_caption.setObjectName("appCaption")
        brand_copy.addWidget(app_title)
        brand_copy.addWidget(app_caption)
        header.addLayout(brand_copy)
        header.addStretch(1)
        self.profile_badge = QLabel()
        self.profile_badge.setObjectName("profileBadge")
        header.addWidget(self.profile_badge, alignment=Qt.AlignVCenter)
        layout.addLayout(header)

        status_card = QFrame()
        status_card.setObjectName("statusCard")
        status_card.setProperty("state", self.status.value)
        self.status_card = status_card
        status_layout = QHBoxLayout(status_card)
        status_layout.setContentsMargins(18, 16, 18, 16)
        status_layout.setSpacing(16)
        self.recording_pulse = StatusGlyph()
        status_layout.addWidget(self.recording_pulse, alignment=Qt.AlignTop)
        status_copy = QVBoxLayout()
        status_copy.setSpacing(6)
        self.status_label = WrappedStatusLabel()
        self.status_label.setObjectName("statusLabel")
        self.status_label.setAccessibleName(self.t["title"])
        status_copy.addWidget(self.status_label)
        self.detail_label = WrappedStatusLabel()
        self.detail_label.setObjectName("detailLabel")
        status_copy.addWidget(self.detail_label)
        self.task_progress = QProgressBar()
        self.task_progress.setObjectName("taskProgress")
        self.task_progress.setRange(0, 0)
        self.task_progress.setTextVisible(False)
        self.task_progress.hide()
        status_copy.addWidget(self.task_progress)
        self.audio_level = QProgressBar()
        self.audio_level.setObjectName("levelMeter")
        self.audio_level.setRange(0, 100)
        self.audio_level.setTextVisible(False)
        self.audio_level.setAccessibleName(self.t["microphone"])
        self.audio_level.hide()
        status_copy.addWidget(self.audio_level)
        job_actions = QHBoxLayout()
        job_actions.setContentsMargins(0, 2, 0, 0)
        job_actions.setSpacing(8)
        self.retry_button = QPushButton(self.t["retry"])
        self.retry_button.clicked.connect(self.retry_job)
        self.retry_button.hide()
        job_actions.addWidget(self.retry_button)
        self.cancel_job_button = QPushButton(self.t["cancel_job"])
        self.cancel_job_button.setObjectName("secondaryAction")
        self.cancel_job_button.clicked.connect(self.cancel_job)
        self.cancel_job_button.hide()
        job_actions.addWidget(self.cancel_job_button)
        job_actions.addStretch(1)
        status_copy.addLayout(job_actions)
        status_layout.addLayout(status_copy, 1)
        layout.addWidget(status_card)

        self.action_button = QPushButton()
        self.action_button.setObjectName("primaryAction")
        self.action_button.setMinimumHeight(44)
        self.action_button.setCursor(Qt.PointingHandCursor)
        self.action_button.clicked.connect(self.toggle_recording)
        layout.addWidget(self.action_button)

        layout.addSpacing(6)
        result_header = QHBoxLayout()
        result_header.setSpacing(4)
        result_label = QLabel(self.t["last_result"])
        result_label.setObjectName("sectionLabel")
        result_header.addWidget(result_label)
        result_header.addStretch(1)
        self.result_actions = QWidget()
        result_actions = QHBoxLayout(self.result_actions)
        result_actions.setContentsMargins(0, 0, 0, 0)
        result_actions.setSpacing(2)
        self.copy_button = QPushButton(self.t["copy"])
        self.copy_button.setObjectName("ghostAction")
        self.copy_button.clicked.connect(self.copy_result)
        self.copy_button.setEnabled(False)
        result_actions.addWidget(self.copy_button)
        self.save_button = QPushButton(self.t["save_as"])
        self.save_button.setObjectName("ghostAction")
        self.save_button.clicked.connect(self.save_result_as)
        self.save_button.setEnabled(False)
        result_actions.addWidget(self.save_button)
        self.open_folder_button = QPushButton(self.t["open_folder"])
        self.open_folder_button.setObjectName("ghostAction")
        self.open_folder_button.clicked.connect(self.open_result_folder)
        self.open_folder_button.hide()
        result_actions.addWidget(self.open_folder_button)
        self.result_actions.hide()
        result_header.addWidget(self.result_actions)
        layout.addLayout(result_header)
        self.last_result = QPlainTextEdit()
        self.last_result.setObjectName("resultText")
        self.last_result.setReadOnly(True)
        self.last_result.setPlaceholderText(self.t["last_result_hint"])
        self.last_result.setAccessibleName(self.t["last_result"])
        self.last_result.setMinimumHeight(96)
        self.last_result.setMaximumHeight(150)
        layout.addWidget(self.last_result)
        layout.addStretch(1)

        divider = QFrame()
        divider.setObjectName("divider")
        layout.addWidget(divider)
        secondary = QHBoxLayout()
        secondary.setSpacing(4)
        self.file_button = QPushButton(self.t["file"])
        self.file_button.setObjectName("secondaryAction")
        self.file_button.clicked.connect(self.transcribe_file)
        secondary.addWidget(self.file_button)
        self.history_button = QPushButton(self.t["history_view"])
        self.history_button.setObjectName("secondaryAction")
        self.history_button.clicked.connect(self.open_history)
        self.history_button.setVisible(self.state.config.get("history", {}).get("enabled", False))
        secondary.addWidget(self.history_button)
        secondary.addStretch(1)
        settings = QPushButton(self.t["settings"])
        settings.setObjectName("secondaryAction")
        settings.clicked.connect(self.open_settings)
        secondary.addWidget(settings)
        layout.addLayout(secondary)
        self.window.setCentralWidget(root)

    def build_tray(self) -> None:
        from PySide6.QtWidgets import QMenu

        menu = QMenu()
        # Translucent so the rounded stylesheet corners do not sit on a square backdrop.
        menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        menu.setAttribute(Qt.WA_TranslucentBackground)
        menu.setStyleSheet(self.style_sheet)
        show = QAction(self.t["open_app"], menu)
        show.triggered.connect(self.show_window)
        menu.addAction(show)
        menu.setDefaultAction(show)
        menu.addSeparator()
        self.tray_action = QAction(menu)
        self.tray_action.triggered.connect(self.toggle_recording)
        menu.addAction(self.tray_action)
        file_action = QAction(self.t["file"], menu)
        file_action.triggered.connect(self.transcribe_file)
        menu.addAction(file_action)
        settings = QAction(self.t["settings"], menu)
        settings.triggered.connect(self.open_settings)
        menu.addAction(settings)
        menu.addSeparator()
        quit_action = QAction(self.t["quit"], menu)
        quit_action.triggered.connect(self.quit)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.show_window() if reason == QSystemTrayIcon.Trigger else None)

    def icon(self) -> QIcon:
        """Brand icon, with a coral recording badge while the microphone is live."""
        size = 64
        brand = QPixmap(str(app_icon_path()))
        if not brand.isNull() and self.status != ViewState.RECORDING:
            return QIcon(brand)
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        if not brand.isNull():
            painter.drawPixmap(0, 0, brand.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(TOKENS["surface_3"]))
            painter.drawEllipse(3, 3, 58, 58)
        if self.status == ViewState.RECORDING:
            painter.setPen(QPen(QColor(TOKENS["bg"]), 4))
            painter.setBrush(QColor(TOKENS["recording"]))
            painter.drawEllipse(QPointF(46, 46), 14, 14)
        painter.end()
        return QIcon(pixmap)

    def _hud_text(self, status: ViewState) -> str | None:
        if status == ViewState.ERROR:
            return self._hud_message or HUD_ERROR_KEYS["generic"][self.lang]
        if status in {ViewState.PROCESSING, ViewState.PREPARING}:
            return self._hud_message
        return None

    def render_status(self, message: str | None = None, *, update_hud: bool = True) -> None:
        text = message or self.t[self.status.value]
        self.status_label.setText(text)
        reduce_motion = self.state.config.get("hud", {}).get("reduce_motion", False)
        self.recording_pulse.set_status(self.status, reduce_motion)
        if self.status_card.property("state") != self.status.value:
            self.status_card.setProperty("state", self.status.value)
            _repolish(self.status_card)
        profile = self.t["privacy"] if self.state.config.get("profile") == "privacy" else self.t["speed"]
        self.profile_badge.setText(profile.split(" (")[0])
        shortcut = hotkey_display_name(self.state.config.get("hotkey", "win+alt"))
        if self.status in {ViewState.IDLE, ViewState.INSERTED, ViewState.ERROR}:
            detail = self.t["idle_hint"].format(hotkey=shortcut)
        elif self.status == ViewState.RECORDING:
            detail = self.detail_label.text() if self.audio_level.isVisible() else ""
        else:
            detail = ""
        self.detail_label.setText(detail)
        self.detail_label.setVisible(bool(detail) or self.status == ViewState.RECORDING)
        busy = self.status in {ViewState.PREPARING, ViewState.PROCESSING}
        cancellable = self.status in {ViewState.PREPARING, ViewState.RECORDING, ViewState.PROCESSING} and getattr(
            self.state, "jobs", None
        ) is not None
        recording = self.status == ViewState.RECORDING
        self.action_button.setEnabled(not busy)
        self.action_button.setText(self.t["stop"] if recording else self.t["record"])
        if self.action_button.property("recording") != recording:
            self.action_button.setProperty("recording", recording)
            _repolish(self.action_button)
        self.cancel_job_button.setVisible(cancellable)
        self.retry_button.setVisible(self.status == ViewState.ERROR and self._retry_available)
        self.task_progress.setVisible(busy)
        self.audio_level.setVisible(recording)
        self._reserve_status_text_height()
        self.tray_action.setText(self.action_button.text())
        self.tray.setIcon(self.icon())
        self.tray.setToolTip(f"{APP_NAME} — {text}")
        if update_hud:
            self.hud.show_status(self.status, self._hud_text(self.status))
        elif not self.state.config.get("hud", {}).get("enabled", True):
            self.hud.hide()

    def _reserve_status_text_height(self) -> None:
        """Give wrapped status labels the height Qt reports for their actual width."""
        changed = False
        for label in (self.status_label, self.detail_label):
            previous = label.minimumHeight()
            label.reserve_height()
            if label.minimumHeight() != previous:
                changed = True
        if changed:
            self.status_card.updateGeometry()
            central = self.window.centralWidget()
            if central is not None and central.layout() is not None:
                central.layout().activate()

    def set_state(
        self,
        status: ViewState,
        message: str | None = None,
        hud_message: str | None = None,
        *,
        update_hud: bool = True,
    ) -> None:
        """Move the UI to ``status``.

        ``message`` is the detailed text for the main window; ``hud_message`` is
        the optional one-line text for the floating HUD.
        """
        if update_hud:
            self._hud_message = hud_message
        self.status = status
        self.render_status(message, update_hud=update_hud)
        if status is ViewState.INSERTED:
            QTimer.singleShot(2500, lambda: self.set_state(ViewState.IDLE) if self.status == status else None)

    # Compatibility API consumed by hotkey.py and file_transcriber.py.  Those
    # callers are worker threads, so they only enqueue operations for Qt.
    def set_recording(self, recording: bool) -> None:
        self.ui_events.put(("recording", recording))

    def notify(self, title: str, message: str) -> None:
        self.ui_events.put(("notification", title, message))

    def _show_notification(self, title: str, message: str) -> None:
        """Route a worker notification without spamming system balloons.

        Errors and routine info stay inside the app (status card, short HUD
        line). Only a finished file transcript earns a system notification.
        """
        kind = title.strip().lower()
        if message.strip().lower() in BUSY_MESSAGES:
            message = self.t["already_processing"]
        if kind in FILE_SAVED_TITLES:
            self.set_state(self.status, message, update_hud=False)
            self.tray.showMessage(title, message, QSystemTrayIcon.Information, 4000)
        elif kind in ERROR_TITLES:
            self._retry_available = False
            short_key = "hotkey" if kind in HOTKEY_TITLES else "microphone" if kind == "microphone unavailable" else None
            short = HUD_ERROR_KEYS[short_key][self.lang] if short_key else None
            self.set_state(ViewState.ERROR, message, hud_message=short)
            hud_enabled = self.state.config.get("hud", {}).get("enabled", True)
            if kind in HOTKEY_TITLES and (not self.window.isVisible() or not hud_enabled):
                self.tray.showMessage(APP_NAME, short or message, QSystemTrayIcon.Warning, 6000)
        else:
            self.set_state(self.status, message, update_hud=False)

    def drain_worker_events(self) -> None:
        try:
            while True:
                event = self.ui_events.get_nowait()
                if event[0] == "recording":
                    self.set_state(ViewState.RECORDING if event[1] else ViewState.PROCESSING)
                elif event[0] == "notification":
                    self._show_notification(event[1], event[2])
                elif event[0] == "transcript":
                    self._set_result(event[1])
        except queue.Empty:
            pass
        events = getattr(self.state, "tk_queue", None)
        if events is None:
            return
        try:
            while True:
                event = events.get_nowait()
                command = event[0] if event else ""
                if command == "job" and len(event) > 1 and isinstance(event[1], dict):
                    self._handle_job_event(event[1])
                elif command == "show_hud":
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
        self._refresh_recording_snapshot()

    def _handle_job_event(self, payload: dict) -> None:
        job_id = payload.get("job_id")
        status = payload.get("status", "")
        jobs = getattr(self.state, "jobs", None)
        controller_job_id = getattr(jobs, "current_job_id", None)
        if job_id is not None:
            if controller_job_id is not None and controller_job_id != job_id:
                return
            self.current_job_id = job_id
        self.current_job_kind = payload.get("kind") or self.current_job_kind

        stage = payload.get("stage")
        message = STAGE_LABELS.get(stage, {}).get(self.lang) if stage else None
        progress = payload.get("progress")
        if isinstance(progress, (int, float)):
            self.task_progress.setRange(0, 100)
            self.task_progress.setValue(max(0, min(100, int(progress))))
        elif status in {"preparing", "processing"}:
            self.task_progress.setRange(0, 0)

        if payload.get("text") is not None:
            self._set_result(str(payload["text"]), payload.get("output_path"))
        elif payload.get("output_path"):
            self.last_output_path = Path(payload["output_path"])
            self._update_result_actions()
        if (
            payload.get("kind") == "file"
            and payload.get("output_path")
            and payload.get("text") is not None
            and status in {"idle", "inserted"}
        ):
            saved = self.t["file_result_saved"].format(path=payload["output_path"])
            self.tray.showMessage(APP_NAME, saved, QSystemTrayIcon.Information, 4000)

        code = str(payload.get("code") or "")
        if status == "error" and code == "cancelled":
            status = "cancelled"
        if status == "preparing":
            self._retry_available = False
            self.set_state(ViewState.PREPARING, message, message)
        elif status == "recording":
            self._retry_available = False
            self.set_state(ViewState.RECORDING, message)
        elif status == "processing":
            self._retry_available = False
            self.set_state(ViewState.PROCESSING, message, message)
        elif status == "inserted":
            self._retry_available = False
            self.set_state(ViewState.INSERTED, message)
        elif status in {"idle", "cancelled"}:
            # Cancellation simply clears the HUD; the window keeps a quiet note.
            self._retry_available = False
            self.set_state(ViewState.IDLE, ERROR_KEYS["cancelled"][self.lang] if status == "cancelled" else message)
        elif status == "error":
            self._last_error_code = code
            self._retry_available = bool(payload.get("retry_available"))
            safe_message = ERROR_KEYS.get(code, {}).get(self.lang) or self.t["generic_error"]
            if self._retry_available:
                safe_message = f"{safe_message} {self.t['retry_saved_audio']}"
            short = (HUD_ERROR_KEYS.get(code) or HUD_ERROR_KEYS["generic"])[self.lang]
            self.set_state(ViewState.ERROR, safe_message, short)

    def _refresh_recording_snapshot(self) -> None:
        if self.status != ViewState.RECORDING:
            return
        jobs = getattr(self.state, "jobs", None)
        snapshot_fn = getattr(jobs, "recording_snapshot", None)
        if not callable(snapshot_fn):
            return
        try:
            snapshot = snapshot_fn() or {}
            elapsed = max(0.0, float(snapshot.get("elapsed", 0.0)))
            raw_level = max(0.0, min(1.0, float(snapshot.get("level", 0.0))))
            silent = max(0.0, float(snapshot.get("silent_seconds", 0.0)))
        except (TypeError, ValueError, RuntimeError):
            return
        level = int(raw_level * 100)
        minutes, seconds = divmod(int(elapsed), 60)
        self.audio_level.setValue(level)
        self.hud.update_recording(elapsed, raw_level)
        details = self.t["recording_details"].format(elapsed=f"{minutes:02d}:{seconds:02d}", level=level)
        if silent >= 5:
            details = f"{details} · {self.t['silent_warning']}"
        if self.detail_label.text() != details:
            self.detail_label.setText(details)
            self.detail_label.show()
            self._reserve_status_text_height()

    def _update_result_actions(self) -> None:
        has_text = bool(self.last_result.toPlainText())
        self.copy_button.setEnabled(has_text)
        self.save_button.setEnabled(has_text)
        self.copy_button.setVisible(has_text)
        self.save_button.setVisible(has_text)
        self.open_folder_button.setVisible(self.last_output_path is not None)
        self.result_actions.setVisible(has_text or self.last_output_path is not None)

    def _set_result(self, text: str, output_path: str | Path | None = None) -> None:
        self.last_result.setPlainText(text)
        if output_path:
            self.last_output_path = Path(output_path)
        self._update_result_actions()
        history = self.state.config.get("history", {})
        if history.get("enabled", False) and text.strip():
            retention = history.get("retention_days", 30)

            def append_history():
                try:
                    from history_store import HistoryStore

                    HistoryStore().append(text, retention)
                except Exception:
                    logger.exception("Could not append transcript history")

            self._start_history_task(append_history, "HistoryAppend")

    def toggle_recording(self) -> None:
        jobs = getattr(self.state, "jobs", None)
        if jobs is not None and hasattr(jobs, "toggle_recording"):
            if not jobs.toggle_recording():
                self.notify(APP_NAME, self.t["already_processing"])
            return
        listener = getattr(self.state, "hotkey_listener", None)
        if self.status == ViewState.PROCESSING:
            self.notify(APP_NAME, self.t["already_processing"])
            return
        if listener and hasattr(listener, "on_hotkey"):
            threading.Thread(target=listener.on_hotkey, daemon=True, name="UiDictationAction").start()
        else:
            self.notify(self.t["error"], self.t["controller_unavailable"])

    def transcribe_file(self) -> None:
        jobs = getattr(self.state, "jobs", None)
        worker = getattr(self.state, "file_transcriber", None)
        if jobs is None and worker is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self.window,
            self.t["file"],
            "",
            "Audio/video (*.mp3 *.wav *.m4a *.ogg *.flac *.aac *.wma *.opus *.mp4 *.mkv *.webm *.avi *.mov)",
        )
        if not path:
            return
        use_local = False
        if self.state.config.get("profile") == "speed":
            try:
                from transcriber import validate_cloud_file

                validate_cloud_file(path)
            except Exception as exc:
                code = getattr(exc, "code", "")
                if code not in {"file_format", "file_too_large"}:
                    self._retry_available = False
                    self.set_state(ViewState.ERROR, ERROR_KEYS.get(code, {}).get(self.lang) or self.t["generic_error"])
                    return
                prompt = QMessageBox(self.window)
                prompt.setWindowTitle(APP_NAME)
                prompt.setIcon(QMessageBox.Question)
                prompt.setText(f"{ERROR_KEYS[code][self.lang]}\n\n{self.t['file_local_offer']}")
                local_button = prompt.addButton(self.t["process_local"], QMessageBox.AcceptRole)
                prompt.addButton(self.t["cancel"], QMessageBox.RejectRole)
                prompt.exec()
                if prompt.clickedButton() is not local_button:
                    return
                use_local = True
        if jobs is not None and hasattr(jobs, "submit_file"):
            if not jobs.submit_file(path, use_local=use_local):
                self.notify(APP_NAME, self.t["already_processing"])
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

    def cancel_job(self) -> None:
        jobs = getattr(self.state, "jobs", None)
        if jobs is not None and hasattr(jobs, "cancel"):
            jobs.cancel()

    def retry_job(self) -> None:
        jobs = getattr(self.state, "jobs", None)
        if jobs is not None and hasattr(jobs, "retry") and jobs.retry():
            self._retry_available = False
            self.retry_button.hide()

    def copy_result(self) -> None:
        text = self.last_result.toPlainText()
        if text:
            QGuiApplication.clipboard().setText(text)

    def save_result_as(self) -> None:
        text = self.last_result.toPlainText()
        if not text:
            return
        suggested = str(self.last_output_path or Path("whispertray-transcript.txt"))
        path, _ = QFileDialog.getSaveFileName(self.window, self.t["save_as"], suggested, "Text (*.txt)")
        if not path:
            return
        try:
            Path(path).write_text(text, encoding="utf-8")
            self.last_output_path = Path(path)
            self._update_result_actions()
            self.tray.showMessage(
                APP_NAME, self.t["file_result_saved"].format(path=path), QSystemTrayIcon.Information, 4000
            )
        except OSError as exc:
            self.set_state(ViewState.ERROR, self.t["save_failed"].format(error=exc))

    def open_result_folder(self) -> None:
        if self.last_output_path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_output_path.parent.resolve())))

    def open_history(self) -> None:
        if not self.state.config.get("history", {}).get("enabled", False):
            return
        HistoryDialog(self.window, self.t, self._start_history_task).exec()

    def prune_history_async(self) -> None:
        history = self.state.config.get("history", {})
        if not history.get("enabled", False):
            return
        retention = history.get("retention_days", 30)

        def prune():
            try:
                from history_store import HistoryStore

                HistoryStore().prune(retention)
            except Exception:
                logger.exception("Could not prune transcript history")

        self._start_history_task(prune, "HistoryPrune")

    def _start_history_task(self, operation, name: str) -> None:
        def run():
            try:
                operation()
            finally:
                with self._history_threads_lock:
                    self._history_threads.discard(threading.current_thread())

        worker = threading.Thread(target=run, daemon=True, name=name)
        with self._history_threads_lock:
            self._history_threads.add(worker)
        worker.start()

    def open_settings(self) -> None:
        if self.is_busy():
            self.notify(APP_NAME, self.t["already_processing"])
            return
        SettingsDialog(self).exec()

    def open_onboarding(self) -> None:
        result = SettingsDialog(self, onboarding=True).exec()
        if result == QDialog.Accepted and self.state.config.get("onboarding_complete", False):
            self.start_hotkey_listener()
            self.show_window()
        else:
            self.quit()

    def start_hotkey_listener(self) -> None:
        listener = getattr(self.state, "hotkey_listener", None)
        running = getattr(self.state, "hotkey_thread", None)
        if listener is None or (running and running.is_alive()):
            return
        self.state.hotkey_thread = threading.Thread(target=listener.run, daemon=True, name="HotkeyThread")
        self.state.hotkey_thread.start()

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
        elif hasattr(self, "history_button"):
            self.history_button.setVisible(config.get("history", {}).get("enabled", False))
        self.prune_history_async()
        self.render_status(update_hud=False)

    def is_busy(self) -> bool:
        jobs = getattr(self.state, "jobs", None)
        if jobs is not None:
            busy = getattr(jobs, "busy", False)
            return bool(busy() if callable(busy) else busy)
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

    def show_recovery_notice(self) -> None:
        if self._recovery_notice_shown:
            return
        store = getattr(self.state, "config_store", None)
        notice = getattr(store, "recovery_notice", None) or getattr(self.state, "recovery_notice", None)
        message = self.t.get(str(notice), "")
        if not message:
            return
        self._recovery_notice_shown = True
        if self.window.isVisible():
            QMessageBox.warning(self.window, APP_NAME, message)
        else:
            self.tray.showMessage(APP_NAME, message, QSystemTrayIcon.Warning)

    def close_to_tray(self, event) -> None:
        event.ignore()
        self.window.hide()
        # Explain the tray once per run; repeating it on every close is noise.
        if not self._close_hint_shown:
            self._close_hint_shown = True
            self.tray.showMessage(APP_NAME, self.t["closed"], QSystemTrayIcon.Information, 4000)

    def quit(self) -> None:
        self.poller.stop()
        self._history_timer.stop()
        qt_app = QApplication.instance()
        if qt_app is not None:
            qt_app.removeEventFilter(self._hotkey_collision_filter)
        for dialog in self.window.findChildren(SettingsDialog):
            dialog.close()
        try:
            import sounddevice as sd

            sd.stop()
        except Exception:
            logger.debug("Could not stop audio preview during shutdown", exc_info=True)
        self.hud.close()
        self.tray.hide()
        jobs = getattr(self.state, "jobs", None)
        if jobs is not None and hasattr(jobs, "shutdown"):
            try:
                jobs.shutdown()
            except Exception:
                logger.exception("Job controller shutdown failed")
        listener = getattr(self.state, "hotkey_listener", None)
        if listener and hasattr(listener, "shutdown"):
            try:
                listener.shutdown()
            except Exception:
                logger.exception("Hotkey shutdown failed")
        worker = getattr(self.state, "file_transcriber", None)
        if worker and hasattr(worker, "shutdown"):
            worker.shutdown()
        deadline = time.monotonic() + 1.0
        with self._history_threads_lock:
            history_threads = list(self._history_threads)
        for history_thread in history_threads:
            history_thread.join(max(0.0, deadline - time.monotonic()))
        QCoreApplication.quit()


def run_qt(state, *, force_show: bool = False) -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(QIcon(str(app_icon_path())))
    app.setQuitOnLastWindowClosed(False)
    ui = WhisperTrayUi(state)
    state.tray_app = ui
    # JobController includes the transcript in its id-gated terminal event.
    # Legacy workers still use the callback bridge.
    if getattr(state, "jobs", None) is None:
        state.on_transcript = lambda text: ui.ui_events.put(("transcript", text))
    if not state.config.get("onboarding_complete", False):
        ui.show_window()
        QTimer.singleShot(0, ui.show_recovery_notice)
        QTimer.singleShot(0, ui.open_onboarding)
    else:
        ui.start_hotkey_listener()
        if should_show_main_window(state.config, force_show=force_show):
            ui.show_window()
        QTimer.singleShot(0, ui.show_recovery_notice)
    return app.exec()
