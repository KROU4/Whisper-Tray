"""
Системный трей: иконка, контекстное меню, уведомления.
Работает в main thread (требование pystray).
"""
import logging
from io import BytesIO
from PIL import Image, ImageDraw
import pystray
from pystray import MenuItem as item, Menu

logger = logging.getLogger(__name__)


def _create_icon_image() -> Image.Image:
    """
    Генерирует иконку трея 64×64:
    тёмный круг с красным микрофоном-точкой внутри.
    """
    img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Тёмный фон-круг
    draw.ellipse([2, 2, 62, 62], fill=(35, 35, 40, 240))

    # Красная точка — символ записи
    draw.ellipse([20, 20, 44, 44], fill=(220, 50, 50, 255))

    # Белая буква-тень для читаемости (маленький круг внутри)
    draw.ellipse([26, 26, 38, 38], fill=(255, 255, 255, 180))

    return img


class TrayApp:
    """Приложение системного трея на базе pystray"""

    def __init__(self, state):
        self.state = state
        self._icon: pystray.Icon | None = None

    # ------------------------------------------------------------------
    # Обработчики меню
    # ------------------------------------------------------------------

    def _on_settings(self, icon, menu_item):
        """Открывает окно настроек через tkinter-поток"""
        self.state.tk_queue.put(('show_settings',))

    def _on_autostart(self, icon, menu_item):
        """Переключает автозапуск"""
        import autostart
        if autostart.is_enabled():
            autostart.disable()
        else:
            autostart.enable()
        icon.update_menu()

    def _autostart_checked(self, menu_item) -> bool:
        """Возвращает текущее состояние автозапуска для отображения галочки"""
        import autostart
        return autostart.is_enabled()

    def _on_quit(self, icon, menu_item):
        logger.info("Завершение по команде меню")
        icon.stop()

    # ------------------------------------------------------------------
    # Публичный API
    # ------------------------------------------------------------------

    def notify(self, title: str, message: str):
        """Показывает всплывающее уведомление трея"""
        if self._icon is None:
            return
        try:
            self._icon.notify(message, title)
        except Exception as e:
            logger.warning(f"Уведомление не отображено: {e}")

    def run(self):
        """
        Запускает трей — блокирующий вызов.
        Должен выполняться в main thread.
        """
        menu = Menu(
            item('Настройки',   self._on_settings),
            item(
                'Автозапуск',
                self._on_autostart,
                checked=self._autostart_checked,
            ),
            Menu.SEPARATOR,
            item('Выход', self._on_quit),
        )

        self._icon = pystray.Icon(
            name='WhisperTray',
            icon=_create_icon_image(),
            title='WhisperTray — голосовой ввод\nWin+H для записи',
            menu=menu,
        )
        logger.info("Трей запущен. Используйте Win+H для записи.")
        self._icon.run()
