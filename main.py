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
import os
import json
import logging
import queue
import threading
from pathlib import Path

# Добавляем CUDA bin в PATH чтобы ctranslate2 нашёл cublas64_12.dll и cudnn
for _cuda_ver in ('12.0', '12.1', '12.2', '12.3', '12.4', '12.5', '12.6'):
    _cuda_bin = Path(r'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA') / f'v{_cuda_ver}' / 'bin'
    if _cuda_bin.exists():
        os.environ['PATH'] = str(_cuda_bin) + os.pathsep + os.environ.get('PATH', '')
        break

# ------------------------------------------------------------------
# Пути
# ------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / 'config.json'
LOG_FILE = BASE_DIR / 'whisper_tray.log'

# ------------------------------------------------------------------
# Логирование
# ------------------------------------------------------------------
_handlers = [logging.FileHandler(LOG_FILE, encoding='utf-8')]
if sys.stdout is not None:
    _handlers.append(logging.StreamHandler(sys.stdout))
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=_handlers,
)
logger = logging.getLogger(__name__)

# Подавляем шумные DEBUG-сообщения сторонних библиотек
logging.getLogger('PIL').setLevel(logging.WARNING)
logging.getLogger('faster_whisper').setLevel(logging.WARNING)
logging.getLogger('groq').setLevel(logging.WARNING)
logging.getLogger('groq._base_client').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)
logging.getLogger('httpx').setLevel(logging.WARNING)

# ------------------------------------------------------------------
# Конфигурация
# ------------------------------------------------------------------
DEFAULT_CONFIG: dict = {
    'transcription_backend': 'groq',
    'groq_model':   'whisper-large-v3-turbo',
    'groq_api_key': '',
    'groq_prompt':  '',
    'groq_max_retries': 4,
    'model':        'small',   # Размер модели Whisper для real-time записи
    'file_model':   'large',   # Размер модели Whisper для транскрипции файлов
    'device_index': None,      # Индекс микрофона (None = системный по умолчанию)
    'hotkey':       'win+alt', # Глобальный хоткей
    'language':     None,      # Язык (None = автодетект)
}


def sanitize_config(cfg: dict) -> dict:
    safe = cfg.copy()
    if safe.get('groq_api_key'):
        safe['groq_api_key'] = '***'
    return safe


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
        self.config: dict          = load_config()
        self.is_recording          = threading.Event()   # Установлен = идёт запись
        self.is_file_transcribing  = threading.Event()   # Установлен = транскрипция файла
        self.tk_queue: queue.Queue = queue.Queue()       # Команды в tkinter-поток
        self.tray_app              = None                # TrayApp (позже)
        self.hotkey_listener       = None                # HotkeyListener (позже)
        self.file_transcriber      = None                # FileTranscriptionWorker (позже)


# ------------------------------------------------------------------
# Точка входа
# ------------------------------------------------------------------
def main():
    logger.info("=" * 60)
    logger.info("Запуск WhisperTray")
    logger.info("=" * 60)

    state = AppState()
    logger.info(f"Конфигурация: {sanitize_config(state.config)}")

    try:
        import ctranslate2
        gpu_count = ctranslate2.get_cuda_device_count()
        device_info = f"GPU (CUDA, устройств: {gpu_count})" if gpu_count > 0 else "CPU"
    except Exception:
        device_info = "CPU"
    logger.info(f"Транскрипция будет выполняться на: {device_info}")

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

    # ---- Файловый транскрибатор (создаём до трея) ----
    from file_transcriber import FileTranscriptionWorker
    state.file_transcriber = FileTranscriptionWorker(state)

    # ---- Main thread: системный трей (блокирующий) ----
    from tray import TrayApp
    tray = TrayApp(state)
    state.tray_app = tray

    logger.info(f"Хоткей: {state.config['hotkey']}")
    logger.info("Приложение готово. Нажмите win+alt для начала записи.")

    try:
        tray.run()  # Блокирует до команды "Выход"
    except KeyboardInterrupt:
        logger.info("Прервано пользователем (Ctrl+C)")
    finally:
        logger.info("WhisperTray завершён")


if __name__ == '__main__':
    main()
