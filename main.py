"""
WhisperTray — голосовой ввод через локальный Whisper.
Точка входа: python main.py

Архитектура потоков:
  Main thread  →  pystray (системный трей)
  Thread 2     →  tkinter event loop (HUD + Settings)
  Thread 3     →  keyboard listener (глобальный хоткей)
  Thread 4     →  sounddevice InputStream (аудиозапись)
  Thread 5     →  faster-whisper транскрипция (временный)
"""
import sys
import json
import logging
import queue
import threading
from pathlib import Path

# ------------------------------------------------------------------
# Пути
# ------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / 'config.json'
LOG_FILE = BASE_DIR / 'whisper_tray.log'

# ------------------------------------------------------------------
# Логирование
# ------------------------------------------------------------------
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# Подавляем шумные DEBUG-сообщения сторонних библиотек
logging.getLogger('PIL').setLevel(logging.WARNING)
logging.getLogger('faster_whisper').setLevel(logging.WARNING)

# ------------------------------------------------------------------
# Конфигурация
# ------------------------------------------------------------------
DEFAULT_CONFIG: dict = {
    'model':        'small',   # Размер модели Whisper
    'device_index': None,      # Индекс микрофона (None = системный по умолчанию)
    'hotkey':       'win+alt', # Глобальный хоткей
    'language':     None,      # Язык (None = автодетект)
}


def load_config() -> dict:
    """Загружает config.json; создаёт файл с дефолтами если не существует"""
    if not CONFIG_FILE.exists():
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
        logger.info("Создан config.json с настройками по умолчанию")
        return DEFAULT_CONFIG.copy()

    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            cfg: dict = json.load(f)
        # Добавляем недостающие ключи из дефолтов
        for key, val in DEFAULT_CONFIG.items():
            cfg.setdefault(key, val)
        return cfg
    except Exception as e:
        logger.error(f"Ошибка чтения config.json: {e}. Используем дефолты.")
        return DEFAULT_CONFIG.copy()


# ------------------------------------------------------------------
# Общее состояние приложения
# ------------------------------------------------------------------
class AppState:
    """
    Контейнер разделяемого состояния между потоками.
    Все поля thread-safe (Event, Queue) или read-only после инициализации.
    """
    def __init__(self):
        self.config: dict        = load_config()
        self.is_recording        = threading.Event()   # Установлен = идёт запись
        self.tk_queue: queue.Queue = queue.Queue()     # Команды в tkinter-поток
        self.tray_app            = None                # TrayApp (позже)
        self.hotkey_listener     = None                # HotkeyListener (позже)


# ------------------------------------------------------------------
# Точка входа
# ------------------------------------------------------------------
def main():
    logger.info("=" * 60)
    logger.info("Запуск WhisperTray")
    logger.info("=" * 60)

    state = AppState()
    logger.info(f"Конфигурация: {state.config}")

    # ---- Поток 2: tkinter (HUD + Settings) ----
    from hud import TkLoop
    tk_thread = threading.Thread(
        target=TkLoop,
        args=(state,),
        daemon=True,
        name="TkThread",
    )
    tk_thread.start()

    # ---- Поток 3: hotkey listener ----
    from hotkey import HotkeyListener
    hotkey_listener = HotkeyListener(state)
    state.hotkey_listener = hotkey_listener

    hotkey_thread = threading.Thread(
        target=hotkey_listener.run,
        daemon=True,
        name="HotkeyThread",
    )
    hotkey_thread.start()

    # ---- Main thread: системный трей (блокирующий) ----
    from tray import TrayApp
    tray = TrayApp(state)
    state.tray_app = tray

    logger.info(f"Хоткей: {state.config['hotkey']}")
    logger.info("Приложение готово. Нажмите Win+H для начала записи.")

    try:
        tray.run()  # Блокирует до команды "Выход"
    except KeyboardInterrupt:
        logger.info("Прервано пользователем (Ctrl+C)")
    finally:
        logger.info("WhisperTray завершён")


if __name__ == '__main__':
    main()
