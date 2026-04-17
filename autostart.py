"""
Управление автозапуском через реестр Windows.
HKEY_CURRENT_USER\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
"""
import sys
import logging
import winreg
from pathlib import Path

logger = logging.getLogger(__name__)

REG_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
APP_NAME = 'WhisperTray'


def _get_run_value() -> str:
    """Строка запуска для реестра: путь к интерпретатору + путь к main.py"""
    exe = sys.executable
    main_script = str(Path(__file__).parent / 'main.py')
    # Если упакован PyInstaller'ом — только путь к exe
    if getattr(sys, 'frozen', False):
        return f'"{exe}"'
    return f'"{exe}" "{main_script}"'


def enable():
    """Добавляет запись автозапуска в реестр"""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            REG_KEY,
            0,
            winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _get_run_value())
        logger.info("Автозапуск включён")
    except Exception as e:
        logger.error(f"Ошибка включения автозапуска: {e}")


def disable():
    """Удаляет запись автозапуска из реестра"""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            REG_KEY,
            0,
            winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, APP_NAME)
        logger.info("Автозапуск отключён")
    except FileNotFoundError:
        pass  # Записи нет — нормально
    except Exception as e:
        logger.error(f"Ошибка отключения автозапуска: {e}")


def is_enabled() -> bool:
    """Возвращает True, если приложение зарегистрировано в автозапуске"""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            REG_KEY,
            0,
            winreg.KEY_READ
        ) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except FileNotFoundError:
        return False
    except Exception as e:
        logger.warning(f"Ошибка проверки автозапуска: {e}")
        return False
