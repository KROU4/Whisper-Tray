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
import time
from copy import deepcopy
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEvent, QLocale, QObject, QSize, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QColor,
    QCursor,
    QDesktopServices,
    QGuiApplication,
    QIcon,
    QKeyEvent,
    QPainter,
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
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QSystemTrayIcon,
    QTabWidget,
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
    font-size: 18px;
    font-weight: 700;
}
QLabel#detailLabel { color: #b9b3aa; font-size: 12px; }
QLabel#appTitle { color: #fff8eb; font-size: 22px; font-weight: 750; }
QLabel#appCaption { color: #aaa49a; font-size: 12px; }
QLabel#sectionLabel { color: #d8d2c8; font-size: 12px; font-weight: 650; }
QLabel#profileBadge {
    background: #2a2e35;
    border: 1px solid #41464f;
    border-radius: 11px;
    color: #ded8ce;
    padding: 4px 9px;
    font-size: 11px;
}
QFrame#statusCard, QFrame#resultCard {
    background: #202329;
    border: 1px solid #343840;
    border-radius: 12px;
}
QFrame#statusCard QLabel { background: transparent; }
QLabel#hintLabel { color: #aaa49a; font-size: 12px; }
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
    min-height: 26px;
}
QPushButton#primaryAction:hover { background: #ff8b70; }
QPushButton#secondaryAction { background: transparent; }
QCheckBox { spacing: 8px; }
QGroupBox {
    background: #202329;
    border: 1px solid #343840;
    border-radius: 10px;
    margin-top: 12px;
    padding: 14px 12px 12px 12px;
    font-weight: 700;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    color: #fff8eb;
}
QProgressBar {
    background: #25282e;
    border: none;
    border-radius: 4px;
    color: #c8c2b8;
    height: 8px;
    text-align: center;
}
QProgressBar::chunk { background: #ff765d; border-radius: 4px; }
QLabel#sectionTitle {
    color: #fff8eb;
    font-size: 16px;
    font-weight: 700;
    padding: 4px 0 6px 0;
}
QTabWidget::pane {
    border: 1px solid #343840;
    border-radius: 9px;
    background: #202329;
    top: -1px;
}
QTabBar::tab {
    background: #25282e;
    color: #bdb7ad;
    border: 1px solid #343840;
    padding: 9px 16px;
    min-width: 110px;
}
QTabBar::tab:selected {
    background: #202329;
    color: #fff8eb;
    border-bottom-color: #202329;
}
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
    PREPARING = "preparing"
    RECORDING = "recording"
    PROCESSING = "processing"
    INSERTED = "inserted"
    ERROR = "error"


STRINGS = {
    "ru": {
        "title": "WhisperTray",
        "idle": "Готов к диктовке",
        "preparing": "Подготавливаю распознавание…",
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
        "hud": "Показывать индикатор",
        "contrast": "Высокий контраст",
        "motion": "Уменьшить анимацию",
        "position": "Позиция HUD",
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
        "copy": "Скопировать",
        "save_as": "Сохранить как…",
        "open_folder": "Открыть папку",
        "history_view": "Открыть историю",
        "history_search": "Поиск по истории",
        "history_empty": "История пока пуста.",
        "history_cleared": "Локальная история очищена.",
        "cancel_job": "Отменить",
        "retry": "Повторить",
        "retry_saved_audio": "Запись временно сохранена. Повтор доступен в течение пяти минут.",
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
        "closed": "Приложение продолжает работать в системном трее.",
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
        "hud": "Show status overlay",
        "contrast": "High contrast",
        "motion": "Reduce motion",
        "position": "HUD position",
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
        "copy": "Copy",
        "save_as": "Save as…",
        "open_folder": "Open folder",
        "history_view": "View history",
        "history_search": "Search history",
        "history_empty": "History is empty.",
        "history_cleared": "Local history was cleared.",
        "cancel_job": "Cancel",
        "retry": "Retry",
        "retry_saved_audio": "The recording is temporarily saved. Retry is available for five minutes.",
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
        "closed": "WhisperTray is still running in the system tray.",
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
        "speed_card": "Fast cloud transcription through Groq\nRequires internet and your personal API key",
        "continue": "Continue",
        "back": "Back",
        "profile_setup": "Prepare transcription",
        "local_explainer": "Audio stays on this device. The Small model is recommended for most computers.",
        "local_model_hint": "You can download the model now or on the first transcription.",
        "cloud_explainer": "Groq receives audio only for transcription. WhisperTray does not keep a copy.",
        "cloud_key_hint": "Create a free key in Groq Console, paste it here, then test the connection.",
        "get_groq_key": "Get a Groq API key",
        "audio_ready": "Choose how you will start dictation",
        "audio_hint": "Select a microphone and a shortcut. You can change both later.",
        "hotkey_hint": "Example: Ctrl+Space. Toggle mode starts and stops recording with two presses.",
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
        "speed_card": "Быстрое распознавание через Groq\nНужны интернет и ваш личный API-ключ",
        "continue": "Продолжить",
        "back": "Назад",
        "profile_setup": "Подготовим распознавание",
        "local_explainer": "Аудио останется на этом устройстве. Для большинства компьютеров рекомендуем модель Small.",
        "local_model_hint": "Модель можно скачать сейчас или при первой диктовке.",
        "cloud_explainer": "Groq получает аудио только для распознавания. WhisperTray не сохраняет его копию.",
        "cloud_key_hint": "Создайте бесплатный ключ в Groq Console, вставьте его сюда и проверьте подключение.",
        "get_groq_key": "Получить ключ Groq API",
        "audio_ready": "Выберите, как запускать диктовку",
        "audio_hint": "Укажите микрофон и сочетание клавиш. Позже их можно изменить.",
        "hotkey_hint": "Например: Ctrl+Space. Режим «нажать / нажать» запускает и останавливает запись двумя нажатиями.",
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
            radius = 16 + (self.phase % 10) / 4
            pulse = QColor("#ff765d")
            pulse.setAlpha(170 - (self.phase % 10) * 12)
            painter.setPen(QPen(pulse, 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(center, radius, radius)
        else:
            painter.setPen(QPen(QColor("#3d424b"), 2))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(center, 17, 17)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#ff765d") if self.recording else QColor("#59606b"))
        painter.drawEllipse(center, 10, 10)


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
    """Accessible profile choice with a clear title and explicit trade-off."""

    def __init__(self, kind: str, title: str, subtitle: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.kind = kind
        self.setCheckable(True)
        self.setMinimumSize(280, 170)
        self.setCursor(Qt.PointingHandCursor)
        self.setAccessibleName(title)
        self.setAccessibleDescription(subtitle.replace("\n", " "))
        content = QVBoxLayout(self)
        content.setContentsMargins(22, 20, 22, 20)
        content.setSpacing(10)
        title_label = QLabel(title)
        title_label.setStyleSheet("background: transparent; color: #fff8eb; font-size: 20px; font-weight: 700;")
        title_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        subtitle_label = QLabel(subtitle)
        subtitle_label.setWordWrap(True)
        subtitle_label.setStyleSheet("background: transparent; color: #c8c2b8; font-size: 13px;")
        subtitle_label.setAttribute(Qt.WA_TransparentForMouseEvents)
        content.addWidget(title_label)
        content.addWidget(subtitle_label)
        content.addStretch()
        self.setStyleSheet(
            "QPushButton { background: #202329; border: 2px solid #41454d; border-radius: 18px; "
            "padding: 0; }"
            "QPushButton:hover { background: #25282e; border-color: #ff9a84; }"
            "QPushButton:checked { background: #2a2424; border: 3px solid #ff765d; }"
            "QPushButton:focus { border: 3px solid #ffb09e; }"
        )


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
        self.setMinimumWidth(380)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(10)
        prompt = QLabel(self.t["hotkey_capture_prompt"])
        prompt.setObjectName("statusLabel")
        prompt.setAlignment(Qt.AlignCenter)
        layout.addWidget(prompt)
        hint = QLabel(self.t["hotkey_capture_hint"])
        hint.setObjectName("hintLabel")
        hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(hint)
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


class SettingsDialog(QDialog):
    def __init__(self, app: "WhisperTrayUi", onboarding: bool = False):
        super().__init__(app.window)
        self.app, self.onboarding = app, onboarding
        self._dialog_generation = 0
        self._dialog_closed = False
        self.config = deepcopy(app.state.config)
        self.lang = ui_language(self.config)
        self.t = STRINGS[self.lang]
        self.setWindowTitle(self.t["onboarding"] if onboarding else self.t["settings"])
        layout = QVBoxLayout(self)
        if onboarding:
            self._build_onboarding(layout)
            return
        self.setMinimumSize(560, 420)
        self._build_settings(layout)
        self._fit_to_screen(660, 620)

    def _build_settings(self, layout: QVBoxLayout) -> None:
        self.tabs = QTabWidget()
        self.settings_scroll = QScrollArea()
        self.settings_scroll.setFrameShape(QFrame.NoFrame)
        self.settings_scroll.setWidgetResizable(True)
        self.settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.settings_scroll.setWidget(self.tabs)
        layout.addWidget(self.settings_scroll, 1)
        self._build_general_tab()
        self._build_appearance_tab()
        self._build_data_tab()
        self.tabs.setMinimumHeight(self.tabs.sizeHint().height())
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText(self.t["save"])
        buttons.button(QDialogButtonBox.Cancel).setText(self.t["cancel"])
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
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

    def _option(self, checkbox: QCheckBox, hint: str) -> QWidget:
        container = QWidget()
        row = QVBoxLayout(container)
        row.setContentsMargins(0, 2, 0, 8)
        row.setSpacing(3)
        row.addWidget(checkbox)
        row.addWidget(self._hint(hint))
        return container

    def _add_hotkey_control(self, form: QFormLayout) -> None:
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.hotkey = QLineEdit(hotkey_display_name(self.config.get("hotkey", "win+alt")))
        self.hotkey.setReadOnly(True)
        self.hotkey.setAccessibleName(self.t["hotkey"])
        self.change_hotkey_button = QPushButton(self.t["change_hotkey"])
        self.change_hotkey_button.setObjectName("secondaryAction")
        self.change_hotkey_button.clicked.connect(self.capture_hotkey)
        row.addWidget(self.hotkey, 1)
        row.addWidget(self.change_hotkey_button)
        form.addRow(self.t["hotkey"], container)
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

    def _build_general_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        layout.addWidget(self._section_title(self.t["profile"]))
        self.form = QFormLayout()
        layout.addLayout(self.form)
        self.profile = QComboBox()
        self.profile.addItem(self.t["privacy"], "privacy")
        self.profile.addItem(self.t["speed"], "speed")
        self.profile.setCurrentIndex(0 if self.config.get("profile") == "privacy" else 1)
        self.form.addRow(self.t["profile"], self.profile)
        self.cloud_note = QLabel(self.t["cloud_note"])
        self.cloud_note.setWordWrap(True)
        layout.addWidget(self.cloud_note)
        self.local_fallback = QCheckBox(self.t["fallback"])
        self.local_fallback.setChecked(self.config.get("allow_local_fallback", False))
        layout.addWidget(self.local_fallback)
        self.groq_key = QLineEdit()
        self.groq_key.setEchoMode(QLineEdit.Password)
        self.groq_key.setPlaceholderText(self.t["keychain_saved"] if self._has_groq_key() else "gsk_…")
        self.form.addRow(self.t["groq_key"], self.groq_key)
        self.test_key_button = QPushButton(self.t["test_key"])
        self.test_key_button.clicked.connect(self.test_groq_key)
        self.form.addRow("", self.test_key_button)
        self.profile.currentIndexChanged.connect(self.update_profile_controls)
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
        self.form.addRow(self.t["model"], self.model)
        self.prepare_model_button = QPushButton(self.t["prepare_model"])
        self.prepare_model_button.clicked.connect(self.prepare_local_model)
        self.form.addRow("", self.prepare_model_button)
        self._add_model_job_controls(self.form)

        layout.addWidget(self._section_title(self.t["microphone"]))
        audio_form = QFormLayout()
        layout.addLayout(audio_form)
        self.mic = QComboBox()
        self.mic.addItem(self.t["default_mic"], None)
        for name, index in input_devices():
            self.mic.addItem(name, index)
        wanted = self.config.get("device_index")
        self.mic.setCurrentIndex(next((i for i in range(self.mic.count()) if self.mic.itemData(i) == wanted), 0))
        audio_form.addRow(self.t["microphone"], self.mic)
        self.test_mic_button = QPushButton(self.t["test_mic"])
        self.test_mic_button.clicked.connect(self.test_microphone)
        audio_form.addRow("", self.test_mic_button)
        self.play_mic_button = QPushButton(self.t["mic_play"])
        self.play_mic_button.clicked.connect(self.play_microphone_test)
        self.play_mic_button.hide()
        audio_form.addRow("", self.play_mic_button)
        self.rec_lang = QComboBox()
        self.rec_lang.addItems([self.t["recognition_auto"], "Русский (ru)", "English (en)"])
        self.rec_lang.setCurrentIndex({None: 0, "ru": 1, "en": 2}.get(self.config.get("language"), 0))
        audio_form.addRow(self.t["language"], self.rec_lang)
        self._add_hotkey_control(audio_form)
        self.hotkey_mode = QComboBox()
        self.hotkey_mode.addItem(self.t["toggle"], "toggle")
        self.hotkey_mode.addItem(self.t["hold"], "hold")
        self.hotkey_mode.setCurrentIndex(1 if self.config.get("hotkey_mode") == "hold" else 0)
        audio_form.addRow(self.t["hotkey_mode"], self.hotkey_mode)
        layout.addStretch()
        self.tabs.addTab(page, self.t["general"])

    def _build_appearance_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        layout.addWidget(self._section_title(self.t["appearance"]))
        form = QFormLayout()
        layout.addLayout(form)
        self.ui_lang = QComboBox()
        self.ui_lang.addItem("Русский", "ru")
        self.ui_lang.addItem("English", "en")
        self.ui_lang.setCurrentIndex(0 if self.lang == "ru" else 1)
        form.addRow(self.t["ui_language"], self.ui_lang)
        hud = self.config.get("hud", {})
        self.hud_enabled = QCheckBox(self.t["hud"])
        self.hud_enabled.setChecked(hud.get("enabled", True))
        form.addRow("", self.hud_enabled)
        self.contrast = QCheckBox(self.t["contrast"])
        self.contrast.setChecked(hud.get("high_contrast", False))
        form.addRow("", self.contrast)
        self.motion = QCheckBox(self.t["motion"])
        self.motion.setChecked(hud.get("reduce_motion", False))
        form.addRow("", self.motion)
        self.position = QComboBox()
        self.position.addItem(self.t["bottom_right"], "bottom_right")
        self.position.addItem(self.t["bottom_left"], "bottom_left")
        self.position.setCurrentIndex(1 if hud.get("position") == "bottom_left" else 0)
        form.addRow(self.t["position"], self.position)
        layout.addStretch()
        self.tabs.addTab(page, self.t["appearance"])

    def _build_data_tab(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        from autostart import is_enabled

        startup = QGroupBox(self.t["startup"])
        startup_layout = QVBoxLayout(startup)
        self.autostart_initial = is_enabled()
        self.autostart_enabled = QCheckBox(self.t["autostart"])
        self.autostart_enabled.setChecked(self.autostart_initial)
        startup_layout.addWidget(self._option(self.autostart_enabled, self.t["autostart_hint"]))
        self.start_in_tray = QCheckBox(self.t["start_in_tray"])
        self.start_in_tray.setChecked(self.config.get("start_in_tray", False))
        startup_layout.addWidget(self._option(self.start_in_tray, self.t["start_in_tray_hint"]))
        layout.addWidget(startup)

        history_group = QGroupBox(self.t["history_section"])
        history_layout = QFormLayout(history_group)
        history = self.config.get("history", {})
        self.history_enabled = QCheckBox(self.t["history"])
        self.history_enabled.setChecked(history.get("enabled", False))
        history_layout.addRow("", self.history_enabled)
        self.retention = QComboBox()
        for days in (7, 30, 90, 365):
            self.retention.addItem(f"{days} {self.t['days']}", days)
        self.retention.setCurrentIndex(max(0, self.retention.findData(history.get("retention_days", 30))))
        history_layout.addRow(self.t["retention"], self.retention)
        clear_history = QPushButton(self.t["clear_history"])
        clear_history.clicked.connect(self.clear_history)
        history_layout.addRow("", clear_history)
        view_history = QPushButton(self.t["history_view"])
        view_history.clicked.connect(self.app.open_history)
        history_layout.addRow("", view_history)
        layout.addWidget(history_group)

        diagnostics_group = QGroupBox(self.t["diagnostics"])
        diagnostics_layout = QVBoxLayout(diagnostics_group)
        note = QLabel(self.t["diagnostics_info"])
        note.setObjectName("detailLabel")
        note.setWordWrap(True)
        diagnostics_layout.addWidget(note)
        from diagnostics import collect_diagnostics

        self.diagnostics_field = QPlainTextEdit(
            json.dumps(collect_diagnostics(self.app.state.config), ensure_ascii=False, indent=2)
        )
        self.diagnostics_field.setReadOnly(True)
        self.diagnostics_field.setMaximumHeight(130)
        self.diagnostics_field.hide()
        diagnostics_layout.addWidget(self.diagnostics_field)
        self.diagnostics_toggle = QPushButton(self.t["show_diagnostics"])
        self.diagnostics_toggle.clicked.connect(self.toggle_diagnostics)
        diagnostics_layout.addWidget(self.diagnostics_toggle)
        export = QPushButton(self.t["export_diagnostics"])
        export.clicked.connect(self.export_diagnostics)
        diagnostics_layout.addWidget(export)
        layout.addWidget(diagnostics_group)
        layout.addStretch()
        self.tabs.addTab(page, self.t["data_system"])

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
        self.setMinimumSize(560, 420)
        layout.setContentsMargins(28, 22, 28, 24)
        layout.setSpacing(9)
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
        self.onboarding_brand_widgets = (logo, heading, subtitle)
        self.progress = QProgressBar()
        self.progress.setRange(0, 3)
        self.progress.setTextVisible(True)
        self.progress.setFixedHeight(24)
        layout.addWidget(self.progress)
        self.profile = QComboBox()
        self.profile.addItem(self.t["privacy"], "privacy")
        self.profile.addItem(self.t["speed"], "speed")
        self.profile.setCurrentIndex(0 if self.config.get("profile") == "privacy" else 1)
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
        self.set_onboarding_step(0)
        self._fit_to_screen(720, 650)

    def _onboarding_profile_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(18)
        layout.setAlignment(Qt.AlignTop)
        prompt = QLabel(self.ot["choose_profile"])
        prompt.setAlignment(Qt.AlignCenter)
        prompt.setStyleSheet("font-size: 20px; font-weight: 700; padding-top: 8px;")
        layout.addWidget(prompt)
        hint = self._hint(self.ot["choose_profile_hint"])
        hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(hint)
        cards = QHBoxLayout()
        cards.setSpacing(16)
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
        layout.setContentsMargins(28, 10, 28, 12)
        layout.setSpacing(14)
        title = QLabel(self.ot["profile_setup"])
        title.setObjectName("statusLabel")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        self.backend_pages = QStackedWidget()
        local = QGroupBox(self.t["privacy"])
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
        self._add_model_job_controls(local_layout)
        local_hint = self._hint(self.ot["local_model_hint"])
        local_layout.addRow("", local_hint)
        cloud = QGroupBox(self.t["speed"])
        cloud_layout = QFormLayout(cloud)
        cloud_note = QLabel(self.ot["cloud_explainer"])
        cloud_note.setWordWrap(True)
        cloud_layout.addRow(cloud_note)
        cloud_hint = self._hint(self.ot["cloud_key_hint"])
        cloud_layout.addRow(cloud_hint)
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
        layout.setContentsMargins(36, 10, 36, 12)
        layout.setSpacing(12)
        title = QLabel(self.ot["audio_ready"])
        title.setObjectName("statusLabel")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)
        hint = QLabel(self.ot["audio_hint"])
        hint.setObjectName("detailLabel")
        hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(hint)
        controls = QGroupBox(self.ot["audio_controls"])
        form = QFormLayout(controls)
        self.mic = QComboBox()
        self.mic.addItem(self.t["default_mic"], None)
        for name, index in input_devices():
            self.mic.addItem(name, index)
        self.mic.setCurrentIndex(next((i for i in range(self.mic.count()) if self.mic.itemData(i) == self.config.get("device_index")), 0))
        form.addRow(self.t["microphone"], self.mic)
        self.test_mic_button = QPushButton(self.t["test_mic"])
        self.test_mic_button.clicked.connect(self.test_microphone)
        form.addRow("", self.test_mic_button)
        self.play_mic_button = QPushButton(self.t["mic_play"])
        self.play_mic_button.clicked.connect(self.play_microphone_test)
        self.play_mic_button.hide()
        form.addRow("", self.play_mic_button)
        self._add_hotkey_control(form)
        self.hotkey_mode = QComboBox()
        self.hotkey_mode.addItem(self.t["toggle"], "toggle")
        self.hotkey_mode.addItem(self.t["hold"], "hold")
        self.hotkey_mode.setCurrentIndex(1 if self.config.get("hotkey_mode") == "hold" else 0)
        form.addRow(self.t["hotkey_mode"], self.hotkey_mode)
        hotkey_hint = self._hint(self.ot["hotkey_hint"])
        form.addRow("", hotkey_hint)
        layout.addWidget(controls)
        # Preserve advanced settings on first run; these controls remain available in Settings.
        self.rec_lang = QComboBox()
        self.rec_lang.addItems([self.t["recognition_auto"], "Русский (ru)", "English (en)"])
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

    def _add_model_job_controls(self, form: QFormLayout) -> None:
        self.model_status = self._hint("")
        self.model_status.hide()
        form.addRow("", self.model_status)
        self.model_progress = QProgressBar()
        self.model_progress.setRange(0, 0)
        self.model_progress.setTextVisible(False)
        self.model_progress.hide()
        form.addRow("", self.model_progress)
        self.cancel_model_button = QPushButton(self.t["cancel_job"])
        self.cancel_model_button.clicked.connect(self.app.cancel_job)
        self.cancel_model_button.hide()
        form.addRow("", self.cancel_model_button)
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
                self.model_progress.setTextVisible(True)
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
        self.progress.setFormat(self.ot["step"].format(current=step + 1))
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
            self.test_mic_button.setText(self.t["test_mic"])
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
                self.prepare_model_button.setText(self.t["preparing"])
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
        self.prepare_model_button.setText(self.t["preparing"])
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
        self.search = QLineEdit()
        self.search.setPlaceholderText(self.t["history_search"])
        self.search.setAccessibleName(self.t["history_search"])
        layout.addWidget(self.search)
        self.results = QPlainTextEdit()
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
        self._recovery_notice_shown = False
        self._history_threads: set[threading.Thread] = set()
        self._history_threads_lock = threading.Lock()
        self.window = QMainWindow()
        self.window.setWindowTitle(APP_NAME)
        self.window.setStyleSheet(APP_STYLE)
        self.window.setMinimumWidth(440)
        self.window.closeEvent = self.close_to_tray
        self._hotkey_collision_filter = MainWindowHotkeyFilter(self)
        qt_app = QApplication.instance()
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
        layout.setContentsMargins(24, 24, 24, 22)
        layout.setSpacing(16)

        header = QHBoxLayout()
        header.setSpacing(12)
        logo = QLabel()
        brand = QPixmap(str(app_icon_path()))
        if not brand.isNull():
            logo.setPixmap(brand.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            header.addWidget(logo)
        brand_copy = QVBoxLayout()
        brand_copy.setSpacing(1)
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
        header.addWidget(self.profile_badge, alignment=Qt.AlignTop)
        layout.addLayout(header)

        status_card = QFrame()
        status_card.setObjectName("statusCard")
        self.status_card = status_card
        status_layout = QHBoxLayout(status_card)
        status_layout.setContentsMargins(16, 14, 18, 14)
        status_layout.setSpacing(14)
        self.recording_pulse = RecordingPulse()
        status_layout.addWidget(self.recording_pulse)
        status_copy = QVBoxLayout()
        status_copy.setSpacing(3)
        self.status_label = WrappedStatusLabel()
        self.status_label.setObjectName("statusLabel")
        status_copy.addWidget(self.status_label)
        self.detail_label = WrappedStatusLabel()
        self.detail_label.setObjectName("detailLabel")
        status_copy.addWidget(self.detail_label)
        self.task_progress = QProgressBar()
        self.task_progress.setRange(0, 0)
        self.task_progress.setTextVisible(False)
        self.task_progress.hide()
        status_copy.addWidget(self.task_progress)
        self.audio_level = QProgressBar()
        self.audio_level.setRange(0, 100)
        self.audio_level.setTextVisible(False)
        self.audio_level.setAccessibleName(self.t["microphone"])
        self.audio_level.hide()
        status_copy.addWidget(self.audio_level)
        status_layout.addLayout(status_copy, 1)
        layout.addWidget(status_card)

        result_label = QLabel(self.t["last_result"])
        result_label.setObjectName("sectionLabel")
        layout.addWidget(result_label)
        result_card = QFrame()
        result_card.setObjectName("resultCard")
        result_layout = QVBoxLayout(result_card)
        result_layout.setContentsMargins(1, 1, 1, 1)
        self.last_result = QPlainTextEdit()
        self.last_result.setReadOnly(True)
        self.last_result.setPlaceholderText(self.t["last_result_hint"])
        self.last_result.setFrameShape(QFrame.NoFrame)
        self.last_result.setMinimumHeight(104)
        self.last_result.setMaximumHeight(124)
        result_layout.addWidget(self.last_result)
        result_actions = QHBoxLayout()
        result_actions.setContentsMargins(8, 0, 8, 8)
        self.copy_button = QPushButton(self.t["copy"])
        self.copy_button.clicked.connect(self.copy_result)
        self.copy_button.setEnabled(False)
        result_actions.addWidget(self.copy_button)
        self.save_button = QPushButton(self.t["save_as"])
        self.save_button.clicked.connect(self.save_result_as)
        self.save_button.setEnabled(False)
        result_actions.addWidget(self.save_button)
        self.open_folder_button = QPushButton(self.t["open_folder"])
        self.open_folder_button.clicked.connect(self.open_result_folder)
        self.open_folder_button.hide()
        result_actions.addWidget(self.open_folder_button)
        result_actions.addStretch(1)
        result_layout.addLayout(result_actions)
        layout.addWidget(result_card)

        self.action_button = QPushButton()
        self.action_button.setObjectName("primaryAction")
        self.action_button.clicked.connect(self.toggle_recording)
        layout.addWidget(self.action_button)

        job_actions = QHBoxLayout()
        self.cancel_job_button = QPushButton(self.t["cancel_job"])
        self.cancel_job_button.clicked.connect(self.cancel_job)
        self.cancel_job_button.hide()
        job_actions.addWidget(self.cancel_job_button)
        self.retry_button = QPushButton(self.t["retry"])
        self.retry_button.clicked.connect(self.retry_job)
        self.retry_button.hide()
        job_actions.addWidget(self.retry_button)
        job_actions.addStretch(1)
        layout.addLayout(job_actions)

        secondary = QHBoxLayout()
        secondary.setSpacing(10)
        self.file_button = QPushButton(self.t["file"])
        self.file_button.setObjectName("secondaryAction")
        self.file_button.clicked.connect(self.transcribe_file)
        secondary.addWidget(self.file_button)
        self.history_button = QPushButton(self.t["history_view"])
        self.history_button.setObjectName("secondaryAction")
        self.history_button.clicked.connect(self.open_history)
        self.history_button.setVisible(self.state.config.get("history", {}).get("enabled", False))
        secondary.addWidget(self.history_button)
        settings = QPushButton(self.t["settings"])
        settings.setObjectName("secondaryAction")
        settings.clicked.connect(self.open_settings)
        secondary.addWidget(settings)
        layout.addLayout(secondary)
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
        self.profile_badge.setText(profile.split(" (")[0])
        shortcut = hotkey_display_name(self.state.config.get("hotkey", "win+alt"))
        self.detail_label.setText(f"{self.t['hotkey']}: {shortcut}")
        self._reserve_status_text_height()
        busy = self.status in {ViewState.PREPARING, ViewState.PROCESSING}
        cancellable = self.status in {ViewState.PREPARING, ViewState.RECORDING, ViewState.PROCESSING} and getattr(
            self.state, "jobs", None
        ) is not None
        self.action_button.setEnabled(not busy)
        self.action_button.setText(self.t["stop"] if self.status == ViewState.RECORDING else self.t["record"])
        self.cancel_job_button.setVisible(cancellable)
        self.retry_button.setVisible(self.status == ViewState.ERROR and self._retry_available)
        self.task_progress.setVisible(busy)
        self.audio_level.setVisible(self.status == ViewState.RECORDING)
        self.tray_action.setText(self.action_button.text())
        self.tray.setIcon(self.icon())
        self.tray.setToolTip(f"{APP_NAME} — {text}")
        self.hud.show_status(self.status, message)

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

    def set_state(self, status: ViewState, message: str | None = None) -> None:
        self.status = status
        self.render_status(message)
        if status is ViewState.INSERTED:
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
            self.task_progress.setTextVisible(True)
        elif status in {"preparing", "processing"}:
            self.task_progress.setRange(0, 0)
            self.task_progress.setTextVisible(False)

        if payload.get("text") is not None:
            self._set_result(str(payload["text"]), payload.get("output_path"))
        elif payload.get("output_path"):
            self.last_output_path = Path(payload["output_path"])
            self.open_folder_button.show()

        if status == "preparing":
            self._retry_available = False
            self.set_state(ViewState.PREPARING, message)
        elif status == "recording":
            self._retry_available = False
            self.set_state(ViewState.RECORDING, message)
        elif status == "processing":
            self._retry_available = False
            self.set_state(ViewState.PROCESSING, message)
        elif status == "inserted":
            self._retry_available = False
            self.set_state(ViewState.INSERTED, message)
        elif status in {"idle", "cancelled"}:
            self._retry_available = False
            self.set_state(ViewState.IDLE, ERROR_KEYS["cancelled"][self.lang] if status == "cancelled" else message)
        elif status == "error":
            code = str(payload.get("code") or "")
            self._last_error_code = code
            self._retry_available = bool(payload.get("retry_available"))
            safe_message = ERROR_KEYS.get(code, {}).get(self.lang) or self.t["generic_error"]
            if self._retry_available:
                safe_message = f"{safe_message} {self.t['retry_saved_audio']}"
            self.set_state(ViewState.ERROR, safe_message)

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
            level = max(0, min(100, int(float(snapshot.get("level", 0.0)) * 100)))
            silent = max(0.0, float(snapshot.get("silent_seconds", 0.0)))
        except (TypeError, ValueError, RuntimeError):
            return
        minutes, seconds = divmod(int(elapsed), 60)
        self.audio_level.setValue(level)
        details = self.t["recording_details"].format(elapsed=f"{minutes:02d}:{seconds:02d}", level=level)
        if silent >= 5:
            details = f"{details} · {self.t['silent_warning']}"
        self.detail_label.setText(details)
        self._reserve_status_text_height()

    def _set_result(self, text: str, output_path: str | Path | None = None) -> None:
        self.last_result.setPlainText(text)
        self.copy_button.setEnabled(bool(text))
        self.save_button.setEnabled(bool(text))
        if output_path:
            self.last_output_path = Path(output_path)
        self.open_folder_button.setVisible(self.last_output_path is not None)
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
            self.open_folder_button.show()
            self.tray.showMessage(APP_NAME, self.t["file_result_saved"].format(path=path))
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
        self.render_status()

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
        self.tray.showMessage(APP_NAME, self.t["closed"])

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
